"""The borrowed identity of a ref() key, where one is accepted, and how resolution fails."""

import gc
import importlib
import threading
from dataclasses import dataclass
from typing import Annotated, Any

import pytest

from nodrill import (
    KeyResolutionError,
    NoProviderError,
    active,
    from_ctx,
    inject,
    injected,
    isolate,
    lazy,
    provider,
    ref,
    resolve_refs,
    set_default,
    use,
)
from nodrill._refs import _SWEEP_EVERY, _created, _imported

HERE = __name__


def distinct(text: str) -> str:
    """Return a string equal to text and not the same object, as a built name is."""
    return text.upper().lower()


# Two module attributes holding one name, which two refs then have to agree on.
NAME = distinct("request_scope")
OTHER_NAME = distinct("request_scope")


@dataclass
class Config:
    dsn: str = "sqlite://"


def config_ref() -> Any:
    """Return a fresh ref to Config in this module, by the canonical spelling."""
    return ref(f"{HERE}:Config")


class TestBorrowedIdentity:
    def test_ref_finds_the_entry_stored_under_the_class(self) -> None:
        with provider(Config(dsn="pg://")):
            assert use(config_ref()).dsn == "pg://"

    def test_class_finds_the_entry_stored_under_a_ref(self) -> None:
        with provider(Config(dsn="pg://"), key=config_ref()):
            assert use(Config).dsn == "pg://"

    def test_registry_never_holds_a_ref(self) -> None:
        with provider(Config(), key=config_ref()):
            assert list(active()) == [Config]

    def test_two_refs_to_one_target_are_one_key(self) -> None:
        first, second = config_ref(), config_ref()
        assert first == second
        assert hash(first) == hash(second)
        assert len({first: 1, second: 2}) == 1

    def test_ref_and_class_compare_equal_both_ways(self) -> None:
        key = config_ref()
        assert key == Config
        assert Config == key

    def test_ref_to_another_target_is_not_equal(self) -> None:
        assert config_ref() != ref(f"{HERE}:TestBorrowedIdentity")

    def test_unrelated_object_is_not_equal(self) -> None:
        assert config_ref() != "Config"

    def test_a_named_key_compares_by_value(self) -> None:
        # The provider is opened under a name equal to the module's, not the same object.
        key = distinct("request_scope")
        assert key is not NAME
        with provider(Config(dsn="named"), key=key):
            assert use(ref(f"{HERE}:NAME")).dsn == "named"

    def test_two_refs_to_one_named_key_are_one_key(self) -> None:
        first, second = ref(f"{HERE}:NAME"), ref(f"{HERE}:OTHER_NAME")
        assert first == second
        assert len({first: 1, second: 2}) == 1

    def test_both_spellings_name_the_same_target(self) -> None:
        assert ref(f"{HERE}.Config") == ref(f"{HERE}:Config")

    def test_dotted_path_walks_past_the_module(self) -> None:
        with provider(Config()):
            assert use(ref(f"{HERE}.Config")).dsn == "sqlite://"

    def test_dotted_path_prefers_the_longest_importable_prefix(self) -> None:
        assert ref("collections.abc.Mapping") == ref("collections.abc:Mapping")

    def test_dotted_path_walks_attributes_of_an_attribute(self) -> None:
        assert ref(f"{HERE}.Config.dsn") == ref(f"{HERE}:Config.dsn")

    def test_repr_is_the_call_that_made_it(self) -> None:
        assert repr(config_ref()) == f"ref({HERE + ':Config'!r})"


class TestAcceptedWhereverAClassIs:
    def test_provider_key(self) -> None:
        with provider(Config(dsn="keyed"), key=config_ref()):
            assert use(config_ref()).dsn == "keyed"

    def test_set_default_resolves_on_the_spot(self) -> None:
        assert set_default(config_ref(), lambda: Config(dsn="fallback")) is Config
        assert use(config_ref()).dsn == "fallback"

    def test_set_default_removal_takes_a_ref_too(self) -> None:
        set_default(config_ref(), Config)
        set_default(config_ref(), None)
        with pytest.raises(NoProviderError):
            use(Config)

    def test_lazy_key(self) -> None:
        with provider(lazy(config_ref(), lambda: Config(dsn="built"))):
            assert list(active()) == [Config]
            assert use(config_ref()).dsn == "built"

    def test_from_ctx_marker_through_a_compiled_wrapper(self) -> None:
        @inject
        def handler(cfg: Annotated[Config, from_ctx(config_ref())] = injected) -> str:
            return cfg.dsn

        assert "@inject" in handler.__code__.co_filename
        with provider(Config(dsn="injected")):
            assert handler() == "injected"

    def test_from_ctx_marker_on_a_named_key_pulls_the_parameter_name(self) -> None:
        @inject
        def handler(dsn: Annotated[str, from_ctx(ref(f"{HERE}:NAME"))] = injected) -> str:
            return dsn

        with provider(NAME, dsn="named"):
            assert handler() == "named"

    def test_from_ctx_marker_pulls_an_attribute(self) -> None:
        @inject
        def handler(dsn: Annotated[str, from_ctx(config_ref(), attr="dsn")] = injected) -> str:
            return dsn

        with provider(Config(dsn="attr")):
            assert handler() == "attr"

    def test_inject_from_mode(self) -> None:
        @inject(from_=config_ref())
        def handler(dsn: str = "none") -> str:
            return dsn

        with provider(Config(dsn="by-name")):
            assert handler() == "by-name"

    def test_explicit_argument_still_wins(self) -> None:
        @inject
        def handler(cfg: Annotated[Config, from_ctx(config_ref())] = injected) -> str:
            return cfg.dsn

        assert handler(Config(dsn="explicit")) == "explicit"


class TestResolutionFailures:
    def test_module_does_not_import(self) -> None:
        with pytest.raises(KeyResolutionError) as exc_info:
            use(ref("nodrill_nonexistent.module:Thing"))
        assert "cannot import 'nodrill_nonexistent.module'" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, ImportError)

    def test_module_has_no_such_attribute(self) -> None:
        with pytest.raises(KeyResolutionError) as exc_info:
            use(ref(f"{HERE}:Missing"))
        assert f"{HERE!r} has no attribute 'Missing'" in str(exc_info.value)

    def test_attribute_of_an_attribute_names_its_owner(self) -> None:
        with pytest.raises(KeyResolutionError) as exc_info:
            use(ref(f"{HERE}:Config.missing"))
        assert f"{HERE}.Config' has no attribute 'missing'" in str(exc_info.value)

    def test_module_still_initialising(self) -> None:
        module = importlib.import_module("tests.cycle.at_import")
        message = module.FAILURE
        assert "'tests.cycle.at_import' is still executing its own import" in message
        assert "move it inside a function" in message

    def test_a_nested_name_missing_during_an_import_is_not_the_cycle(self) -> None:
        module = importlib.import_module("tests.cycle.at_import")
        message = module.NESTED_FAILURE
        assert "'tests.cycle.at_import.Scope' has no attribute 'missing'" in message
        assert "still executing" not in message

    def test_error_is_a_lookup_error(self) -> None:
        with pytest.raises(LookupError):
            use(ref("nodrill_nonexistent:Thing"))

    def test_path_carried_on_the_exception(self) -> None:
        with pytest.raises(KeyResolutionError) as exc_info:
            use(ref("nodrill_nonexistent:Thing"))
        assert exc_info.value.path == "nodrill_nonexistent:Thing"

    def test_failure_is_not_cached(self) -> None:
        key = ref(f"{HERE}:LateArrival")
        with pytest.raises(KeyResolutionError):
            use(key)
        globals()["LateArrival"] = Config
        try:
            assert key == Config
        finally:
            del globals()["LateArrival"]

    def test_a_missing_prefix_falls_back_to_a_shorter_one(self) -> None:
        # 'json.nonexistent' is no module, so 'json' is the module and the rest is a walk.
        with pytest.raises(KeyResolutionError, match="'json' has no attribute 'nonexistent'"):
            use(ref("json.nonexistent.Thing"))

    def test_a_failing_module_in_the_path_is_reported_rather_than_shortened(self) -> None:
        with pytest.raises(KeyResolutionError) as exc_info:
            use(ref("tests.cycle.direct_context.RequestScope"))
        assert "cannot import 'tests.cycle.direct_context'" in str(exc_info.value)

    def test_a_missing_package_is_reported_rather_than_shortened(self) -> None:
        with pytest.raises(KeyResolutionError) as exc_info:
            use(ref("nodrill_nonexistent.sub.Thing"))
        assert "cannot import 'nodrill_nonexistent.sub'" in str(exc_info.value)

    def test_default_does_not_cover_a_broken_path(self) -> None:
        with pytest.raises(KeyResolutionError):
            use(ref("nodrill_nonexistent:Thing"), default=None)

    @pytest.mark.parametrize(
        "path",
        ["", "Config", "tests.", ".Config", "a..b", "tests.cycle:", ":Config", "not an identifier"],
    )
    def test_malformed_path_is_refused_at_the_call(self, path: str) -> None:
        with pytest.raises(ValueError, match="is not an import path"):
            ref(path)

    def test_a_ref_is_not_a_provider_target(self) -> None:
        with pytest.raises(TypeError, match="takes an instance, not a key"):
            provider(config_ref())

    def test_path_must_be_a_string(self) -> None:
        with pytest.raises(TypeError, match="expects an import path as a string"):
            ref(Config)  # type: ignore[arg-type]

    def test_a_path_naming_a_module_is_only_refused_at_the_lookup(self) -> None:
        # 'package.module' cannot be told from 'module.Name', so the path is accepted
        # and the module it names is what fails, as any other non-key target does.
        key = ref("json.decoder")
        with pytest.raises(TypeError, match="use\\(\\) expects a string name or a class"):
            use(key)

    def test_target_that_is_no_kind_of_key(self) -> None:
        with pytest.raises(TypeError, match="use\\(\\) expects a string name or a class"):
            use(ref(f"{HERE}.config_ref"))

    def test_no_provider_message_names_the_ref(self) -> None:
        with pytest.raises(NoProviderError) as exc_info:
            use(config_ref())
        message = str(exc_info.value)
        assert f"ref({HERE + ':Config'!r})" in message
        assert "key=ref(" in message
        assert "set_default(ref(" in message


class TestResolveRefs:
    def test_forces_every_ref_and_is_idempotent(self) -> None:
        key = config_ref()
        resolve_refs()
        with provider(Config(dsn="forced")):
            assert use(key).dsn == "forced"
        resolve_refs()

    def test_raises_on_the_first_broken_path(self) -> None:
        broken = ref("nodrill_nonexistent:Thing")
        assert broken is not None
        with pytest.raises(KeyResolutionError, match="nodrill_nonexistent"):
            resolve_refs()

    def test_a_dropped_ref_is_forgotten(self) -> None:
        broken = ref("nodrill_nonexistent:Thing")
        del broken
        gc.collect()
        resolve_refs()

    def test_dead_entries_do_not_accumulate(self) -> None:
        live = [config_ref(), config_ref()]
        for _ in range(_SWEEP_EVERY * 3):
            config_ref()
        gc.collect()
        assert len(_created) < len(live) + _SWEEP_EVERY

    def test_refs_made_inside_isolate_do_not_leak_out(self) -> None:
        with isolate():
            broken = ref("nodrill_nonexistent:Thing")
            assert broken is not None
        resolve_refs()

    def test_a_ref_a_module_body_made_survives_the_block_that_imported_it(self) -> None:
        with isolate():
            models = importlib.import_module("tests.cycle.models")
        assert any(holder() is models.RequestScope for holder in _imported)


class TestConcurrency:
    def test_two_threads_resolve_to_one_target(self) -> None:
        key = config_ref()
        start = threading.Event()
        resolved: list[Any] = []

        def resolve() -> None:
            start.wait()
            resolved.append(key == Config)

        threads = [threading.Thread(target=resolve) for _ in range(2)]
        for thread in threads:
            thread.start()
        start.set()
        for thread in threads:
            thread.join()
        assert resolved == [True, True]


class TestImportCycle:
    def test_the_plain_import_really_is_a_cycle(self) -> None:
        with pytest.raises(ImportError, match="partially initialized module"):
            importlib.import_module("tests.cycle.direct_context")

    def test_the_ref_spelling_imports_and_reads(self) -> None:
        context = importlib.import_module("tests.cycle.context")
        with provider(context.RequestScope(user_id=42)):
            assert context.describe() == "user 42"

    def test_the_downstream_module_imports_on_its_own(self) -> None:
        models = importlib.import_module("tests.cycle.models")
        context = importlib.import_module("tests.cycle.context")
        with provider(context.RequestScope(user_id=7)):
            assert models.owner_of() == 7

    def test_two_paths_to_one_class_are_one_key(self) -> None:
        context = importlib.import_module("tests.cycle.context")
        direct = ref("tests.cycle.context:RequestScope")
        aliased = ref("tests.cycle.alias:RequestScope")
        assert direct == aliased
        assert len({direct: 1, aliased: 2}) == 1
        with provider(context.RequestScope(user_id=3)):
            assert use(aliased).user_id == 3

    def test_a_resolved_ref_does_not_follow_a_reload(self) -> None:
        module = importlib.import_module("tests.cycle.reloadable")
        key = ref("tests.cycle.reloadable:Scope")
        resolved = module.Scope
        assert key == resolved
        importlib.reload(module)
        assert module.Scope is not resolved
        assert key == resolved
        with provider(module.Scope()), pytest.raises(NoProviderError):
            use(key)

    def test_the_subscript_spelling_injects_the_late_bound_key(self) -> None:
        models = importlib.import_module("tests.cycle.models")
        context = importlib.import_module("tests.cycle.context")
        with provider(context.RequestScope(user_id=9)):
            assert models.greet() == "hello 9"
