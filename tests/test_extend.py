import asyncio
import gc
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from nodrill import (
    FrozenContextError,
    Namespace,
    active,
    inject,
    lazy,
    provider,
    use,
    wrap,
)


@dataclass
class Config:
    url: str = "sqlite://"


class TestAccumulation:
    def test_three_layers_accumulate(self) -> None:
        with (
            provider("audit", request_id="r-1"),
            provider("audit", extend=True, actor_id=7),
            provider("audit", extend=True, reason="cleanup"),
        ):
            audit = use("audit")
            assert (audit.request_id, audit.actor_id, audit.reason) == ("r-1", 7, "cleanup")

    def test_each_exit_restores_exactly_one_layer(self) -> None:
        with provider("audit", request_id="r-1"):
            with provider("audit", extend=True, actor_id=7):
                with provider("audit", extend=True, reason="cleanup"):
                    assert set(vars(use("audit"))) == {"request_id", "actor_id", "reason"}
                assert set(vars(use("audit"))) == {"request_id", "actor_id"}
            assert set(vars(use("audit"))) == {"request_id"}

    def test_inner_value_wins_for_a_repeated_name(self) -> None:
        with provider("audit", actor_id=1), provider("audit", extend=True, actor_id=2):
            assert use("audit").actor_id == 2

    def test_extend_without_an_enclosing_provider_is_a_plain_provider(self) -> None:
        """A layer need not know whether it is the first, which is why the feature exists."""
        with provider("audit", extend=True, request_id="r-1"):
            assert vars(use("audit")) == {"request_id": "r-1"}

    def test_the_merge_is_one_level_deep(self) -> None:
        """A value that is itself a mapping is replaced, not merged into."""
        with (
            provider("audit", tags={"a": 1}),
            provider("audit", extend=True, tags={"b": 2}),
        ):
            assert use("audit").tags == {"b": 2}

    def test_extend_over_a_different_name_does_not_merge(self) -> None:
        with provider("request", rid="r-1"), provider("audit", extend=True, actor_id=7):
            assert vars(use("audit")) == {"actor_id": 7}
            assert vars(use("request")) == {"rid": "r-1"}

    def test_one_registry_entry_per_name(self) -> None:
        with provider("audit", request_id="r-1"), provider("audit", extend=True, actor_id=7):
            assert set(active()) == {"audit"}

    def test_yielded_namespace_is_the_one_use_returns(self) -> None:
        with provider("audit", request_id="r-1"), provider("audit", extend=True) as audit:
            assert use("audit") is audit

    def test_name_keyword_spelling(self) -> None:
        with provider("audit", request_id="r-1"), provider(name="audit", extend=True, actor_id=7):
            assert vars(use("audit")) == {"request_id": "r-1", "actor_id": 7}

    def test_values_named_like_the_parameters_are_still_data(self) -> None:
        with provider("doc", name="report.pdf"), provider("doc", extend=True, pages=3):
            assert (use("doc").name, use("doc").pages) == ("report.pdf", 3)

    async def test_async_with_spelling(self) -> None:
        with provider("audit", request_id="r-1"):
            async with provider("audit", extend=True, actor_id=7):
                assert vars(use("audit")) == {"request_id": "r-1", "actor_id": 7}
            assert vars(use("audit")) == {"request_id": "r-1"}


class TestSnapshot:
    def test_outer_write_after_inner_enter_is_not_seen_inside(self) -> None:
        with provider("audit", request_id="r-1") as outer:
            with provider("audit", extend=True, actor_id=7):
                outer.request_id = "r-2"
                assert use("audit").request_id == "r-1"
            assert use("audit").request_id == "r-2"

    def test_inner_write_is_not_seen_outside(self) -> None:
        with provider("audit", request_id="r-1") as outer:
            with provider("audit", extend=True, actor_id=7):
                use("audit").request_id = "rewritten"
            assert outer.request_id == "r-1"
            assert not hasattr(use("audit"), "actor_id")

    def test_the_layers_are_distinct_objects(self) -> None:
        with provider("audit", request_id="r-1") as outer:
            with provider("audit", extend=True) as inner:
                assert inner is not outer
                assert vars(inner) is not vars(outer)


class TestReuse:
    def test_a_reused_provider_merges_at_enter(self) -> None:
        """The same object layers over what is open now, so the merge happens at enter."""
        layer = provider("audit", extend=True, actor_id=7)
        with provider("audit", request_id="r-1"), layer:
            assert vars(use("audit")) == {"request_id": "r-1", "actor_id": 7}
        with provider("audit", request_id="r-2", path="/documents"), layer:
            assert vars(use("audit")) == {"request_id": "r-2", "path": "/documents", "actor_id": 7}

    def test_reentering_an_active_extending_provider_raises(self) -> None:
        layer = provider("audit", extend=True)
        with layer, pytest.raises(RuntimeError, match="already active"):
            layer.__enter__()

    def test_exit_without_enter_is_a_noop(self) -> None:
        provider("audit", extend=True).__exit__(None, None, None)

    def test_the_provider_does_not_retain_the_namespace_after_exit(self) -> None:
        """The namespace belongs to the scope, so a long-lived provider must not pin it."""
        layer = provider("audit", extend=True, actor_id=7)
        with layer as audit:
            assert layer in gc.get_referrers(audit)
        assert layer not in gc.get_referrers(audit)


class TestIsolation:
    async def test_sibling_task_sees_the_middle_layer_only(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        seen: dict[str, Any] = {}

        async def sibling() -> None:
            entered.set()
            await release.wait()
            seen.update(vars(use("audit")))

        with provider("audit", request_id="r-1"), provider("audit", extend=True, actor_id=7):
            task = asyncio.create_task(sibling())
            await entered.wait()
            with provider("audit", extend=True, reason="cleanup"):
                release.set()
                await task

        assert seen == {"request_id": "r-1", "actor_id": 7}

    def test_sibling_thread_sees_the_middle_layer_only(self) -> None:
        started = threading.Event()
        release = threading.Event()
        box: list[dict[str, Any]] = []

        def audit() -> dict[str, Any]:
            return dict(vars(use("audit")))

        def sibling(read: Callable[[], dict[str, Any]]) -> None:
            started.set()
            release.wait()
            box.append(read())

        with provider("audit", request_id="r-1"), provider("audit", extend=True, actor_id=7):
            thread = threading.Thread(target=sibling, args=(wrap(audit),))
            thread.start()
            started.wait()
            with provider("audit", extend=True, reason="cleanup"):
                release.set()
                thread.join()

        assert box == [{"request_id": "r-1", "actor_id": 7}]

    def test_a_thread_that_copied_the_context_extends_its_own_copy(self) -> None:
        """extend=True across a context copy is ordinary, the copy being the outer layer."""

        def worker() -> dict[str, Any]:
            with provider("audit", extend=True, actor_id=7):
                return dict(vars(use("audit")))

        with provider("audit", request_id="r-1") as outer:
            bound = wrap(worker)
            box: list[dict[str, Any]] = []
            thread = threading.Thread(target=lambda: box.append(bound()))
            thread.start()
            thread.join()
            assert box == [{"request_id": "r-1", "actor_id": 7}]
            assert not hasattr(outer, "actor_id")

    def test_restoration_after_an_exception_inside_the_block(self) -> None:
        with provider("audit", request_id="r-1"):
            with pytest.raises(RuntimeError, match="boom"):
                with provider("audit", extend=True, actor_id=7):
                    raise RuntimeError("boom")
            assert vars(use("audit")) == {"request_id": "r-1"}


class TestLabel:
    def test_the_merged_namespace_keeps_the_provider_name(self) -> None:
        with provider("audit", request_id="r-1"), provider("audit", extend=True, actor_id=7):
            with pytest.raises(AttributeError, match=r"namespace 'audit' has no attribute 'oops'"):
                _ = use("audit").oops

    def test_the_error_lists_the_accumulated_attributes(self) -> None:
        with provider("audit", request_id="r-1"), provider("audit", extend=True, actor_id=7):
            with pytest.raises(AttributeError, match=r"\(available: actor_id, request_id\)"):
                _ = use("audit").oops

    def test_repr_names_the_provider(self) -> None:
        with provider("audit", request_id="r-1"), provider("audit", extend=True, actor_id=7) as ns:
            assert repr(ns) == "Namespace('audit', actor_id=7, request_id='r-1')"


class TestFrozen:
    def test_extending_a_frozen_layer_reads_it_through_the_proxy(self) -> None:
        with provider("audit", request_id="r-1", frozen=True):
            with provider("audit", extend=True, actor_id=7):
                assert vars(use("audit")) == {"request_id": "r-1", "actor_id": 7}

    def test_freezing_is_not_inherited(self) -> None:
        with provider("audit", request_id="r-1", frozen=True):
            with provider("audit", extend=True, actor_id=7):
                use("audit").reason = "cleanup"
                assert use("audit").reason == "cleanup"

    def test_the_inner_layer_freezes_when_it_asks_to(self) -> None:
        with provider("audit", request_id="r-1"):
            with provider("audit", extend=True, actor_id=7, frozen=True) as audit:
                audit.reason = "the owner can still write"
                with pytest.raises(FrozenContextError):
                    use("audit").reason = "..."
                assert use("audit").reason == "the owner can still write"

    def test_a_frozen_layer_extends_a_frozen_layer(self) -> None:
        with provider("audit", request_id="r-1", frozen=True):
            with provider("audit", extend=True, actor_id=7, frozen=True):
                assert vars(use("audit")) == {"request_id": "r-1", "actor_id": 7}
                with pytest.raises(FrozenContextError):
                    use("audit").reason = "..."

    def test_the_outer_frozen_namespace_is_not_written_to(self) -> None:
        with provider("audit", request_id="r-1", frozen=True) as outer:
            with provider("audit", extend=True, actor_id=7):
                pass
            assert not hasattr(outer, "actor_id")


class TestErrors:
    def test_extend_on_an_instance_target(self) -> None:
        with pytest.raises(TypeError, match=r"string-named providers"):
            provider(Config(), extend=True)  # type: ignore[call-overload]

    def test_the_instance_message_points_at_dataclasses_replace(self) -> None:
        with pytest.raises(TypeError, match=r"dataclasses\.replace\(instance, \.\.\.\)"):
            provider(Config(), extend=True)  # type: ignore[call-overload]

    def test_extend_on_a_lazy_target(self) -> None:
        with pytest.raises(TypeError, match=r"string-named providers"):
            provider(lazy(Config, Config), extend=True)  # type: ignore[call-overload]

    @pytest.mark.parametrize(
        "match",
        [r"can only extend a Namespace", r"currently provides a Config", r"Drop\s+extend=True"],
        ids=["names-the-target-type", "names-the-value-type", "names-the-fix"],
    )
    def test_extend_over_a_non_namespace_value(self, match: str) -> None:
        """The enclosing value is only known on entry, so this is where it is refused."""
        with provider(Config(), key="audit"), pytest.raises(TypeError, match=match):
            provider("audit", extend=True, actor_id=7).__enter__()

    def test_extend_over_a_frozen_non_namespace_names_the_real_type(self) -> None:
        """The proxy spoofs __class__, so the message says Config and not the proxy."""
        with (
            provider(Config(), key="audit", frozen=True),
            pytest.raises(TypeError, match=r"currently provides a Config"),
        ):
            provider("audit", extend=True, actor_id=7).__enter__()

    def test_the_refused_layer_leaves_the_outer_value_in_place(self) -> None:
        cfg = Config()
        with provider(cfg, key="audit"):
            with pytest.raises(TypeError, match="can only extend"):
                provider("audit", extend=True, actor_id=7).__enter__()
            assert active()["audit"] is cfg

    def test_extend_beside_key_on_a_string_target(self) -> None:
        """key= is refused first, since a string-named provider already has its key."""
        with pytest.raises(TypeError, match=r"provider\(key=\.\.\.\) applies to instance"):
            provider("audit", key="other", extend=True)

    def test_extend_beside_key_on_an_instance_target(self) -> None:
        with pytest.raises(TypeError, match=r"string-named providers"):
            provider(Config(), key="audit", extend=True)  # type: ignore[call-overload]


class TestInjection:
    def test_inject_from_an_accumulated_namespace(self) -> None:
        @inject(from_="audit")
        def record(request_id: str = "", actor_id: int = 0, reason: str = "") -> str:
            return f"{request_id}/{actor_id}/{reason}"

        with (
            provider("audit", request_id="r-1"),
            provider("audit", extend=True, actor_id=7),
            provider("audit", extend=True, reason="cleanup"),
        ):
            assert record() == "r-1/7/cleanup"

    def test_a_parameter_the_layers_have_not_filled_keeps_its_default(self) -> None:
        @inject(from_="audit")
        def record(request_id: str = "", reason: str = "none") -> str:
            return f"{request_id}/{reason}"

        with provider("audit", request_id="r-1"), provider("audit", extend=True, actor_id=7):
            assert record() == "r-1/none"


class TestNamespaceIdentity:
    def test_the_merged_value_is_a_namespace(self) -> None:
        with provider("audit", request_id="r-1"), provider("audit", extend=True) as audit:
            assert isinstance(audit, Namespace)

    def test_equality_is_by_attributes(self) -> None:
        with provider("audit", request_id="r-1"), provider("audit", extend=True, actor_id=7):
            assert use("audit") == Namespace(request_id="r-1", actor_id=7)
