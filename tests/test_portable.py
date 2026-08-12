import json
import os
import pickle
import queue
import sys
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

import nodrill
from nodrill import (
    EnvelopeVersionError,
    NoProviderError,
    UnusedProviderWarning,
    active,
    adopt,
    debug,
    export,
    provider,
    ref,
    set_codec,
    use,
)


@dataclass
class Session:
    dsn: str


@dataclass
class Actor:
    id: int
    name: str


def tag_actors(values: dict[str, Any]) -> dict[str, Any]:
    """Render an Actor as a tagged dict, which is the dump half of a codec."""
    return {
        name: {"__type__": "Actor", **asdict(value)} if isinstance(value, Actor) else value
        for name, value in values.items()
    }


def untag_actors(values: dict[str, Any]) -> dict[str, Any]:
    """Rebuild an Actor from its tag, which is the load half."""
    return {
        name: Actor(id=value["id"], name=value["name"])
        if isinstance(value, dict) and value.get("__type__") == "Actor"
        else value
        for name, value in values.items()
    }


def not_a_mapping(_values: dict[str, Any]) -> Any:
    """Return what a namespace is not, which is what a codec is not allowed to hand back."""
    return "not a mapping"


def boom(_values: dict[str, Any]) -> Any:
    """Fail the way a real codec fails, on a value it cannot encode."""
    raise ZeroDivisionError


class Level(IntEnum):
    HIGH = 2


class Tenant(str, Enum):
    ACME = "acme"


def adopted_request_id(payload: dict[str, Any]) -> str:
    """Adopt a payload and read one attribute, which is the worker half of a round trip."""
    with adopt(payload):
        return str(use("trace").request_id)


@contextmanager
def _importable_by_a_child() -> Iterator[None]:
    """Put the repository root on sys.path, which is what spawn hands to a child process."""
    root = str(Path(__file__).resolve().parent.parent)
    sys.path.insert(0, root)
    try:
        yield
    finally:
        sys.path.remove(root)


def envelope(**values: Any) -> dict[str, Any]:
    """Return a one-namespace envelope, for tests that do not go through export()."""
    return {"v": 1, "ctx": {"trace": values}}


class TestRoundTrip:
    def test_a_payload_crosses_a_real_process(self) -> None:
        """The boundary this exists for, a worker in another interpreter."""
        with provider("trace", request_id="r-1"):
            payload = export("trace")
        with _importable_by_a_child(), ProcessPoolExecutor(max_workers=1) as pool:
            assert pool.submit(adopted_request_id, payload).result() == "r-1"
            # Proof rather than trust, since a pool that ran in this process would also pass.
            assert pool.submit(os.getpid).result() != os.getpid()

    def test_a_payload_crosses_as_a_json_string(self) -> None:
        """What a broker carries is a string, so that is the round trip that has to hold."""
        broker: queue.Queue[str] = queue.Queue()
        with provider("trace", request_id="r-2", depth=3, ratio=0.5, live=True, parent=None):
            broker.put(json.dumps(export("trace")))
        with adopt(json.loads(broker.get())):
            carried = use("trace")
            assert (carried.request_id, carried.depth, carried.ratio) == ("r-2", 3, 0.5)
            assert (carried.live, carried.parent) == (True, None)

    def test_nested_containers_survive(self) -> None:
        """Lists and dicts of scalars travel, to any depth."""
        with provider("trace", tags=["a", 1], meta={"region": {"eu": [True, None]}}):
            payload = json.loads(json.dumps(export("trace")))
        with adopt(payload):
            assert use("trace").tags == ["a", 1]
            assert use("trace").meta == {"region": {"eu": [True, None]}}

    def test_several_namespaces_travel_together(self) -> None:
        """One envelope, one adopt, every namespace it was given."""
        with provider("trace", request_id="r-3"), provider("audit", actor_id=7):
            payload = export("trace", "audit")
        with adopt(payload):
            assert (use("trace").request_id, use("audit").actor_id) == ("r-3", 7)

    def test_the_worker_reads_what_the_parent_read(self) -> None:
        """The worker half alone, since a failure in the child arrives as a pickled traceback."""
        with provider("trace", request_id="r-4"):
            payload = export("trace")
        assert adopted_request_id(payload) == "r-4"


class TestWhatTravels:
    def test_only_what_export_names_travels(self) -> None:
        """A provider nobody named is absent, which is the whole safety story."""
        with provider("trace", request_id="r-5"), provider("db", dsn="postgres://"):
            payload = export("trace")
        assert payload["ctx"] == {"trace": {"request_id": "r-5"}}

    def test_export_without_names_is_an_empty_envelope(self) -> None:
        """Nothing travels by default, and adopting that is a no-op rather than an error."""
        payload = export()
        assert payload == {"v": 1, "ctx": {}}
        with adopt(payload):
            assert dict(active()) == {}

    def test_export_reads_the_nearest_provider(self) -> None:
        """The registry holds one value per key, so a shadowed layer is not reachable."""
        with provider("trace", request_id="outer"), provider("trace", request_id="inner"):
            assert export("trace")["ctx"]["trace"] == {"request_id": "inner"}

    def test_a_missing_provider_raises_the_usual_error(self) -> None:
        """export() reads through use(), so a name nobody opened fails the way it always does."""
        with pytest.raises(NoProviderError, match="no active provider for 'trace'"):
            export("trace")

    def test_a_frozen_provider_exports(self) -> None:
        """frozen=True is about who may write, and says nothing about what may travel."""
        with provider("trace", frozen=True, request_id="r-6"):
            assert export("trace")["ctx"]["trace"] == {"request_id": "r-6"}

    def test_the_envelope_is_a_copy(self) -> None:
        """A container written after export() cannot reach a payload already handed on."""
        with provider("trace", tags=["a"]) as ctx:
            payload = export("trace")
            ctx.tags.append("b")
        assert payload["ctx"]["trace"]["tags"] == ["a"]

    def test_exporting_counts_as_reading(self) -> None:
        """A provider that exists only to be carried is not a dead one."""
        with debug(unused=True), provider("trace", request_id="r-7"):
            export("trace")


class TestRefusedNames:
    def test_a_class_key_does_not_travel(self) -> None:
        """A class cannot be reopened on the other side without importing it."""
        with pytest.raises(TypeError, match=r"got Session\. A class key cannot be reopened"):
            export(Session)  # type: ignore[arg-type]

    def test_a_ref_does_not_travel_either(self) -> None:
        """A ref is a late-bound name for a class key, and it says so rather than its own type."""
        with pytest.raises(TypeError, match=r"got ref\('myapp.context:Scope'\)\. A class key"):
            export(ref("myapp.context:Scope"))

    def test_a_name_has_to_be_a_string(self) -> None:
        """Anything else is neither a namespace name nor a class."""
        with pytest.raises(TypeError, match="string provider names, got int"):
            export(7)  # type: ignore[arg-type]

    def test_an_instance_under_a_string_key_does_not_travel(self) -> None:
        """provider(instance, key='db') puts a live object under a name, and it stays here."""
        with provider(Session(dsn="postgres://"), key="db"):
            with pytest.raises(TypeError, match="'db' provides an instance of Session"):
                export("db")


class TestRefusedValues:
    @pytest.mark.parametrize(
        ("value", "named"),
        [
            (Session(dsn="postgres://"), "Session"),
            ((1, 2), "tuple"),
            (b"raw", "bytes"),
            ({"a"}, "set"),
            (Decimal("1.5"), "Decimal"),
            (uuid4(), "UUID"),
            (Level.HIGH, "Level"),
            (Tenant.ACME, "Tenant"),
        ],
    )
    def test_a_value_that_would_not_come_back_as_itself_is_refused(
        self, value: Any, named: str
    ) -> None:
        """Exact types only, so a subclass is refused rather than flattened to its base."""
        with provider("trace", conn=value):
            with pytest.raises(TypeError, match=f"conn is of type {named}, which does not"):
                export("trace")

    def test_a_frozen_value_reports_its_own_type(self) -> None:
        """The refusal names what the consumer sees, not the proxy the registry holds."""
        with provider(Session(dsn="postgres://"), frozen=True):
            with provider("trace", conn=use(Session)):
                with pytest.raises(TypeError, match="conn is of type Session"):
                    export("trace")

    def test_the_path_names_a_list_element(self) -> None:
        """A miss inside a container names the element, not the attribute holding it."""
        with provider("trace", tags=["a", uuid4()]):
            with pytest.raises(TypeError, match=r"export\('trace'\): tags\[1\] is of type UUID"):
                export("trace")

    def test_the_path_names_a_dict_item(self) -> None:
        """The same, one level of dict down."""
        with provider("trace", meta={"region": {"eu": object()}}):
            with pytest.raises(TypeError, match=r"meta\['region'\]\['eu'\] is of type object"):
                export("trace")

    @pytest.mark.parametrize("value", [float("inf"), float("nan")])
    def test_a_float_json_cannot_represent_is_refused(self, value: float) -> None:
        """json.dumps writes inf and nan, and no other language's parser reads them."""
        with provider("trace", ratio=value):
            with pytest.raises(ValueError, match=r"ratio is .*, which JSON cannot represent"):
                export("trace")

    def test_a_dict_keyed_by_anything_but_a_string_is_refused(self) -> None:
        """A JSON object is keyed by strings, and json.dumps would silently stringify."""
        with provider("trace", meta={1: "one"}):
            with pytest.raises(TypeError, match="meta is keyed by int"):
                export("trace")

    def test_a_container_holding_itself_is_refused(self) -> None:
        """A cycle raises where it is found rather than recursing to the interpreter limit."""
        tags: list[Any] = ["a"]
        tags.append(tags)
        with provider("trace", tags=tags):
            with pytest.raises(ValueError, match=r"tags\[1\] contains itself"):
                export("trace")

    def test_one_container_twice_is_not_a_cycle(self) -> None:
        """Sharing is not recursion, and only the path back to a container counts."""
        shared = ["a"]
        with provider("trace", first=shared, second=shared):
            assert export("trace")["ctx"]["trace"] == {"first": ["a"], "second": ["a"]}

    def test_bools_travel(self) -> None:
        """Booleans travel, since JSON has them, though bool is an int subclass."""
        with provider("trace", live=True):
            assert export("trace")["ctx"]["trace"] == {"live": True}


class TestAdoptedPayloads:
    def test_adopt_shadows_a_provider_of_the_same_name(self) -> None:
        """The same rule provider() has, since that is what adopt() opens."""
        with provider("trace", request_id="outer", region="eu"):
            with adopt(envelope(request_id="inner")):
                assert use("trace").request_id == "inner"
                assert not hasattr(use("trace"), "region")
            assert use("trace").request_id == "outer"

    def test_adopt_unwinds_on_an_exception(self) -> None:
        """A failing block restores the outer scope, as every provider block does."""
        with pytest.raises(RuntimeError, match="boom"):
            with adopt(envelope(request_id="r-8")):
                raise RuntimeError("boom")
        assert dict(active()) == {}

    def test_an_adopted_namespace_names_itself(self) -> None:
        """An attribute error after an adopt reads like one after a provider block."""
        with adopt(envelope(request_id="r-9")):
            with pytest.raises(AttributeError, match="namespace 'trace' has no attribute 'nope'"):
                _ = use("trace").nope

    def test_an_attribute_named_like_a_provider_parameter_stays_data(self) -> None:
        """A payload attribute called frozen or key is a value, not a flag."""
        with adopt(envelope(frozen=True, key="k", extend=True, annotate=False)):
            carried = use("trace")
            assert (carried.frozen, carried.key) == (True, "k")
            assert (carried.extend, carried.annotate) == (True, False)

    def test_an_adopted_provider_is_an_ordinary_one(self) -> None:
        """It joins the debug ledger, since adopt() opens providers rather than the registry."""
        with pytest.warns(UnusedProviderWarning, match="'trace' at") as records, debug(unused=True):
            with adopt(envelope(request_id="r-11")):
                pass
        # The block that opened it is the caller's, not the contextlib frame adopt() runs in.
        assert records[0].filename == __file__

    def test_a_bad_namespace_opens_nothing(self) -> None:
        """The whole payload is checked before the first provider opens."""
        payload = {"v": 1, "ctx": {"trace": {"request_id": "r-10"}, "audit": {"actor": object()}}}
        with pytest.raises(TypeError, match=r"adopt\('audit'\): actor is of type object"):
            with adopt(payload):
                pass
        assert dict(active()) == {}

    def test_an_infinity_a_broker_delivered_is_refused(self) -> None:
        """json.loads reads the Infinity literal, so a payload really can arrive carrying one."""
        payload = json.loads('{"v": 1, "ctx": {"trace": {"ratio": Infinity}}}')
        with pytest.raises(ValueError, match=r"adopt\('trace'\): ratio is inf"):
            with adopt(payload):
                pass

    def test_an_adopted_payload_is_checked_the_way_an_exported_one_is(self) -> None:
        """A payload from a broker is input, and the same rule reads it."""
        with pytest.raises(TypeError, match=r"adopt\('trace'\): tags\[0\] is of type tuple"):
            with adopt(envelope(tags=[(1, 2)])):
                pass


class TestMalformedEnvelopes:
    def test_an_unknown_version_names_both(self) -> None:
        """A producer a release ahead is the case this field exists for."""
        with pytest.raises(EnvelopeVersionError, match=r"reads envelope version 1, .* version 2"):
            with adopt({"v": 2, "ctx": {}}):
                pass

    def test_the_version_error_carries_both(self) -> None:
        """A consumer that logs and runs on needs the numbers, not the sentence."""
        error = EnvelopeVersionError(2, 1)
        assert (error.version, error.supported) == (2, 1)
        assert isinstance(error, ValueError)

    def test_the_version_error_survives_a_pickle(self) -> None:
        """It is raised at a process boundary, so it travels back over one."""
        restored = pickle.loads(pickle.dumps(EnvelopeVersionError(2, 1)))
        assert (restored.version, restored.supported) == (2, 1)
        assert str(restored) == str(EnvelopeVersionError(2, 1))

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            ("not a payload", "got str"),
            ({}, "carries its version under 'v'"),
            ({"v": "1", "ctx": {}}, "carries its version under 'v'"),
            ({"v": True, "ctx": {}}, "carries its version under 'v'"),
            ({"v": 1}, "carries its namespaces under 'ctx'"),
            ({"v": 1, "ctx": []}, "carries its namespaces under 'ctx'"),
            ({"v": 1, "ctx": {7: {}}}, "'ctx' is keyed by int"),
            ({"v": 1, "ctx": {"trace": "r-1"}}, "'ctx' holds str under 'trace'"),
            ({"v": 1, "ctx": {"trace": {7: "r-1"}}}, "an attribute name has to be a string, got 7"),
        ],
    )
    def test_a_payload_that_is_not_an_envelope_is_refused(self, payload: Any, message: str) -> None:
        """Malformed input fails at the boundary, naming what was expected."""
        with pytest.raises(TypeError, match=message):
            with adopt(payload):
                pass


class TestCodec:
    @pytest.fixture(autouse=True)
    def _cleared(self) -> Iterator[None]:
        """Clear the codec after each test, since isolate() deliberately does not."""
        try:
            yield
        finally:
            set_codec()

    def test_an_object_travels_through_a_codec(self) -> None:
        """The case the hook exists for, a value JSON has nowhere to put."""
        set_codec(dump=tag_actors, load=untag_actors)
        with provider("trace", request_id="r-1", actor=Actor(id=7, name="acme")):
            payload = json.loads(json.dumps(export("trace")))
        assert payload["ctx"]["trace"]["actor"] == {"__type__": "Actor", "id": 7, "name": "acme"}
        with adopt(payload):
            assert use("trace").actor == Actor(id=7, name="acme")

    def test_a_codec_result_is_checked_like_any_other_value(self) -> None:
        """The envelope stays JSON, so a codec handing back an object of its own fails here."""
        set_codec(dump=lambda values: {**values, "extra": uuid4()})
        with provider("trace", request_id="r-2"):
            with pytest.raises(TypeError, match="extra is of type UUID"):
                export("trace")

    def test_only_the_half_a_service_needs_has_to_be_registered(self) -> None:
        """A producer registers dump alone, and its consumer runs somewhere else entirely."""
        set_codec(dump=tag_actors)
        with provider("trace", actor=Actor(id=7, name="acme")):
            payload = export("trace")
        with adopt(payload):
            assert use("trace").actor == {"__type__": "Actor", "id": 7, "name": "acme"}

    def test_a_call_states_the_whole_codec(self) -> None:
        """set_codec() with no arguments clears both halves rather than leaving one behind."""
        set_codec(dump=tag_actors, load=untag_actors)
        set_codec()
        with provider("trace", actor=Actor(id=7, name="acme")):
            with pytest.raises(TypeError, match="actor is of type Actor"):
                export("trace")

    def test_a_payload_is_checked_before_the_codec_sees_it(self) -> None:
        """Load runs on input, so a malformed payload is refused without reaching user code."""
        seen: list[dict[str, Any]] = []

        def watch(values: dict[str, Any]) -> dict[str, Any]:
            seen.append(values)
            return values

        set_codec(load=watch)
        with pytest.raises(TypeError, match=r"adopt\('trace'\): tags\[0\] is of type tuple"):
            with adopt(envelope(tags=[(1, 2)])):
                pass
        assert seen == []

    def test_a_codec_failure_arrives_as_itself(self) -> None:
        """Nothing wraps the codec's exception, since its own message is the useful one."""
        set_codec(dump=boom)
        with provider("trace", request_id="r-3"):
            with pytest.raises(ZeroDivisionError):
                export("trace")

    def test_a_dump_returning_something_else_is_refused(self) -> None:
        """A namespace is a mapping of names to values, and the way out is checked for it."""
        set_codec(dump=not_a_mapping)
        with provider("trace", request_id="r-4"):
            with pytest.raises(TypeError, match="the codec returned str rather than a mapping"):
                export("trace")

    def test_a_load_returning_something_else_is_refused(self) -> None:
        """The same on the way in, where the codec is reading somebody else's payload."""
        set_codec(load=not_a_mapping)
        with pytest.raises(TypeError, match="the codec returned str rather than a mapping"):
            with adopt(envelope(request_id="r-4")):
                pass

    def test_a_loaded_name_has_to_be_a_string(self) -> None:
        """The namespace it builds is keyed by attribute names, whatever the codec thinks."""

        def numbered(_values: dict[str, Any]) -> Any:
            return {7: "seven"}

        set_codec(load=numbered)
        with pytest.raises(TypeError, match="the codec returned 7 as a name"):
            with adopt(envelope(request_id="r-5")):
                pass

    def test_a_codec_half_has_to_be_callable(self) -> None:
        """Registering something uncallable fails at startup rather than at the first boundary."""
        with pytest.raises(TypeError, match=r"set_codec\(dump=\.\.\.\) takes a callable"):
            set_codec(dump=7)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match=r"set_codec\(load=\.\.\.\) takes a callable"):
            set_codec(load=7)  # type: ignore[arg-type]

    def test_isolate_does_not_clear_it(self) -> None:
        """It is startup configuration, like the annotation switch, so a block does not own it."""
        set_codec(dump=tag_actors)
        with nodrill.isolate():
            with provider("trace", actor=Actor(id=7, name="acme")):
                assert export("trace")["ctx"]["trace"]["actor"]["__type__"] == "Actor"


def test_the_boundary_names_are_public() -> None:
    """The boundary API is importable from the top level, like everything else."""
    assert {"export", "adopt", "set_codec", "EnvelopeVersionError"} <= set(nodrill.__all__)
