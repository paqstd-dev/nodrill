"""What sealed=True does while its block is open, and what it refuses afterwards."""

import asyncio
import copy
import pickle
import threading
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import pytest

from nodrill import (
    Executor,
    ExpiredScopeError,
    FromCtx,
    FrozenContextError,
    NoProviderError,
    active,
    debug,
    explain,
    export,
    inject,
    injected,
    lazy,
    provider,
    use,
    wrap,
)
from nodrill._core import _Provider, _Sealing

# True from 3.11, which is where PEP 678 notes exist at all.
NOTES = hasattr(BaseException, "add_note")


@dataclass
class Session:
    dsn: str = "pg://"
    open: bool = True

    def query(self) -> str:
        return f"rows from {self.dsn}"


# What the in_thread fixture in conftest hands over, as a type.
ThreadRunner = Callable[[Callable[[], Any]], Any]


def read(value: Any, name: str = "dsn") -> Any:
    """Read one attribute, which is what a reference that escaped its block does."""
    return getattr(value, name)


class TestSealedLifetime:
    def test_the_value_behaves_as_itself_inside_the_block(self) -> None:
        with provider(Session(), sealed=True) as session:
            assert session.query() == "rows from pg://"
            assert use(Session).dsn == "pg://"
            session.dsn = "pg://other"
            assert use(Session).dsn == "pg://other"

    def test_use_hands_back_the_object_the_block_yielded(self) -> None:
        with provider(Session(), sealed=True) as session:
            assert use(Session) is session

    def test_a_closure_that_runs_later_raises_where_it_runs(self) -> None:
        later: list[Any] = []
        with provider(Session(), sealed=True) as session:

            def query_it() -> str:
                text: str = session.query()
                return text

            later.append(query_it)
        with pytest.raises(ExpiredScopeError, match=r"Session\.query was used after"):
            later[0]()

    def test_the_message_names_where_the_block_opened_and_where_it_was_used(self) -> None:
        with provider(Session(), sealed=True) as session:
            pass
        with pytest.raises(ExpiredScopeError) as caught:
            session.query()
        error = caught.value
        assert error.key is Session
        assert error.operation == "query"
        assert error.opened[0] == error.exited[0] == error.used[0] == __file__
        assert error.opened[1] < error.used[1]
        assert f"opened at {error.opened[0]}:{error.opened[1]}" in str(error)
        assert f"used here at {error.used[0]}:{error.used[1]}" in str(error)
        # A with exits on its own line, so repeating it would be a site the reader already has.
        assert error.exited == error.opened
        assert "and exited at" not in str(error)

    def test_the_sites_are_plain_tuples(self) -> None:
        with provider(Session(), sealed=True) as session:
            pass
        with pytest.raises(ExpiredScopeError) as caught:
            read(session)
        for site in (caught.value.opened, caught.value.exited, caught.value.used):
            assert type(site) is tuple

    def test_the_exit_site_appears_when_something_else_closed_the_block(self) -> None:
        with ExitStack() as stack:
            session = stack.enter_context(provider(Session(), sealed=True))
        with pytest.raises(ExpiredScopeError) as caught:
            read(session)
        error = caught.value
        assert error.exited != error.opened
        assert f"and exited at {error.exited[0]}:{error.exited[1]}" in str(error)

    def test_the_registry_side_expires_too(self) -> None:
        escaped: list[Any] = []
        with provider(Session(), sealed=True):
            escaped.append(use(Session))
        with pytest.raises(ExpiredScopeError):
            read(escaped[0])

    def test_an_exception_leaving_the_block_still_expires_it(self) -> None:
        with pytest.raises(ZeroDivisionError):
            with provider(Session(), sealed=True) as session:
                raise ZeroDivisionError
        with pytest.raises(ExpiredScopeError):
            read(session)

    def test_it_is_a_runtime_error(self) -> None:
        with provider(Session(), sealed=True) as session:
            pass
        with pytest.raises(RuntimeError):
            read(session)

    def test_the_error_survives_a_pickle_round_trip(self) -> None:
        with provider(Session(), sealed=True) as session:
            pass
        with pytest.raises(ExpiredScopeError) as caught:
            read(session)
        restored = pickle.loads(pickle.dumps(caught.value))
        assert str(restored) == str(caught.value)
        assert restored.operation == "dsn"

    def test_an_unsealed_provider_is_unchanged(self) -> None:
        with provider(Session(), sealed=False) as session:
            pass
        assert session.query() == "rows from pg://"


class TestProviderKinds:
    def test_every_provider_kind_has_a_sealed_twin(self) -> None:
        # A kind added without one would take sealed=True and quietly never expire.
        def kinds(cls: type[Any]) -> set[type[Any]]:
            found = set()
            for sub in cls.__subclasses__():
                found |= {sub} | kinds(sub)
            return found

        every = kinds(_Provider)
        sealed = {kind for kind in every if issubclass(kind, _Sealing)}
        # A base that only shares an implementation is never opened, so it needs no twin.
        shared = {base for kind in every - sealed for base in kind.__bases__} - {_Provider}
        assert {kind.__bases__[1] for kind in sealed} == (every - sealed - shared) | {_Provider}


class TestGuardsAndCopies:
    def test_a_defaulting_getattr_does_not_swallow_the_expiry(self) -> None:
        with provider(Session(), sealed=True) as session:
            pass
        # Not an AttributeError, since getattr(x, name, default) would answer the default
        # and hand the caller the silent wrong value sealing exists to report.
        assert not issubclass(ExpiredScopeError, AttributeError)
        with pytest.raises(ExpiredScopeError):
            getattr(session, "dsn", "fallback")
        with pytest.raises(ExpiredScopeError):
            hasattr(session, "dsn")

    def test_an_injected_by_name_read_reports_the_expiry(self) -> None:
        @inject(from_="app")
        def handler(dsn: str = injected) -> str:
            return dsn

        with provider("app", dsn="pg://", sealed=True):
            assert handler() == "pg://"
            later = wrap(handler)
        # The wrapper reads the attribute with a default, which must not answer for a dead scope.
        with pytest.raises(ExpiredScopeError):
            later()

    def test_copying_is_refused_even_when_the_target_defines_the_hook(self) -> None:
        class Cloneable:
            def __deepcopy__(self, memo: dict[int, Any]) -> str:
                return "copied out of the block"

        with provider(Cloneable(), sealed=True) as cloneable:
            with pytest.raises(TypeError, match="sealed context views cannot be"):
                copy.deepcopy(cloneable)
        with pytest.raises(TypeError, match="sealed context views cannot be"):
            copy.deepcopy(cloneable)


class TestReEntry:
    def test_a_second_entry_does_not_revive_the_first(self) -> None:
        block = provider(Session(), sealed=True)
        with block as first:
            pass
        with block as second:
            assert second.dsn == "pg://"
            with pytest.raises(ExpiredScopeError):
                read(first)
        with pytest.raises(ExpiredScopeError):
            read(second)

    def test_the_two_entries_report_their_own_sites(self) -> None:
        block = provider(Session(), sealed=True)
        with block as first:
            pass
        with block as second:
            pass
        with pytest.raises(ExpiredScopeError) as one:
            read(first)
        with pytest.raises(ExpiredScopeError) as two:
            read(second)
        assert one.value.opened[1] < two.value.opened[1]

    def test_exit_without_enter_is_noop(self) -> None:
        block = provider(Session(), sealed=True)
        block.__exit__(None, None, None)
        with block as session:
            assert session.dsn == "pg://"

    def test_a_nested_second_provider_expires_on_its_own_exit(self) -> None:
        with provider(Session(dsn="outer"), sealed=True) as outer:
            with provider(Session(dsn="inner"), sealed=True) as inner:
                assert use(Session).dsn == "inner"
            with pytest.raises(ExpiredScopeError):
                read(inner)
            assert outer.dsn == "outer"
            assert use(Session).dsn == "outer"


class TestEscapesAcrossBoundaries:
    def test_a_thread_touching_an_expired_value_raises_there(self, in_thread: ThreadRunner) -> None:
        with provider(Session(), sealed=True) as session:
            pass

        def query_it() -> str:
            text: str = session.query()
            return text

        result = in_thread(query_it)
        assert isinstance(result, ExpiredScopeError)
        assert result.used[0] == __file__

    def test_a_wrapped_callback_carries_the_scope_and_not_the_lifetime(self) -> None:
        def read_it() -> str:
            text: str = use(Session).query()
            return text

        with provider(Session(), sealed=True):
            bound = wrap(read_it)
        # wrap() carries the registry entry, and the entry is the proxy that just expired.
        with pytest.raises(ExpiredScopeError):
            bound()

    def test_a_pool_task_submitted_inside_the_block_and_run_after_it(self) -> None:
        release = threading.Event()

        def read_it() -> str:
            release.wait(timeout=5)
            text: str = use(Session).query()
            return text

        with Executor(max_workers=1) as pool:
            with provider(Session(), sealed=True):
                future = pool.submit(read_it)
            release.set()
            with pytest.raises(ExpiredScopeError):
                future.result(timeout=5)

    async def test_a_task_created_inside_the_block_that_outlives_it(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def child() -> str:
            started.set()
            await release.wait()
            text: str = use(Session).query()
            return text

        with provider(Session(), sealed=True):
            task = asyncio.create_task(child())
            await started.wait()
        release.set()
        with pytest.raises(ExpiredScopeError):
            await task

    async def test_async_with_expires_the_same_way(self) -> None:
        async with provider(Session(), sealed=True) as session:
            assert session.query() == "rows from pg://"
        with pytest.raises(ExpiredScopeError):
            read(session)

    def test_re_providing_the_yielded_value_registers_its_own_key(
        self, in_thread: ThreadRunner
    ) -> None:
        with provider(Session(), sealed=True) as session:

            def query_it() -> str:
                # The pattern the concurrency page prescribes, on a thread that starts empty.
                with provider(session):
                    text: str = use(Session).query()
                    return text

            assert in_thread(query_it) == "rows from pg://"

    def test_a_re_provided_value_expires_with_the_block_it_came_from(self) -> None:
        with provider(Session(), sealed=True) as session:
            pass
        with provider(session):
            with pytest.raises(ExpiredScopeError):
                read(use(Session))

    async def test_a_task_that_finishes_inside_the_block_is_unaffected(self) -> None:
        async def child() -> str:
            text: str = use(Session).query()
            return text

        with provider(Session(), sealed=True):
            assert await asyncio.create_task(child()) == "rows from pg://"


class TestComposition:
    def test_frozen_and_sealed_give_the_block_a_writable_handle(self) -> None:
        with provider(Session(), frozen=True, sealed=True) as session:
            session.dsn = "written by the owner"
            assert use(Session).dsn == "written by the owner"
            with pytest.raises(FrozenContextError):
                use(Session).dsn = "written by a consumer"

    def test_expiry_is_reported_before_the_freeze(self) -> None:
        escaped: list[Any] = []
        with provider(Session(), frozen=True, sealed=True) as session:
            escaped.append(use(Session))
        with pytest.raises(ExpiredScopeError):
            escaped[0].dsn = "x"
        with pytest.raises(ExpiredScopeError):
            session.dsn = "x"

    def test_frozen_and_sealed_are_two_proxies_over_one_object(self) -> None:
        target = Session()
        with provider(target, frozen=True, sealed=True) as session:
            assert use(Session) is not session
            assert use(Session) == target
            assert session == target

    def test_isinstance_holds_through_a_frozen_view(self) -> None:
        with provider(Session(), frozen=True, sealed=True):
            assert isinstance(use(Session), Session)
            assert use(Session).__class__ is Session

    def test_isinstance_holds_through_a_lazy_cell(self) -> None:
        with provider(lazy(Session, Session), sealed=True) as cell:
            assert isinstance(cell, Session)
            assert cell.dsn == "pg://"
            assert isinstance(cell, Session)

    def test_a_frozen_and_sealed_namespace_can_be_extended_and_exported(self) -> None:
        with provider("trace", request_id="r-1", frozen=True, sealed=True):
            assert export("trace") == {"v": 1, "ctx": {"trace": {"request_id": "r-1"}}}
            with provider("trace", extend=True, actor_id=7) as layer:
                assert (layer.request_id, layer.actor_id) == ("r-1", 7)

    def test_a_self_referential_factory_still_names_itself(self) -> None:
        # Through the seal the identity check would otherwise miss the cell and recurse.
        with pytest.raises(RuntimeError, match="cannot read or return its own key"):
            with provider(lazy(Session, lambda: use(Session)), sealed=True):
                read(use(Session))

    def test_a_lazy_value_expires_without_ever_building(self) -> None:
        built: list[int] = []

        def factory() -> Session:
            built.append(1)
            return Session()

        with provider(lazy(Session, factory), sealed=True) as cell:
            pass
        with pytest.raises(ExpiredScopeError):
            read(cell)
        assert built == []

    def test_a_lazy_value_resolved_inside_the_block_still_expires(self) -> None:
        with provider(lazy(Session, Session), sealed=True) as cell:
            assert cell.dsn == "pg://"
        with pytest.raises(ExpiredScopeError):
            read(cell)

    def test_an_in_place_operator_on_a_lazy_value_keeps_the_seal(self) -> None:
        shared = [1]
        with provider(lazy(list, lambda: shared), sealed=True) as items:
            items += [2]
            assert use(list) == [1, 2]
        # The cell hands itself back, so the operator cannot rebind the name past the seal.
        with pytest.raises(ExpiredScopeError):
            items.append(3)
        assert shared == [1, 2]

    def test_re_providing_a_sealed_lazy_value_registers_the_key_class(self) -> None:
        with provider(lazy(Session, Session), sealed=True) as cell:
            with provider(cell):
                assert list(active()) == [Session]
                assert use(Session).query() == "rows from pg://"
        with pytest.raises(ExpiredScopeError):
            read(cell)

    def test_lazy_and_frozen_and_sealed_together(self) -> None:
        with provider(lazy(Session, Session), frozen=True, sealed=True) as cell:
            cell.dsn = "the owner can still write"
            assert use(Session).dsn == "the owner can still write"
            with pytest.raises(FrozenContextError):
                use(Session).dsn = "x"
        with pytest.raises(ExpiredScopeError):
            read(cell)

    def test_an_extending_layer_seals_only_itself(self) -> None:
        with provider("audit", request_id="r-1"):
            with provider("audit", extend=True, actor_id=7, sealed=True) as layer:
                assert (layer.request_id, layer.actor_id) == ("r-1", 7)
            with pytest.raises(ExpiredScopeError, match=r"'audit'\.actor_id"):
                read(layer, "actor_id")
            assert use("audit").request_id == "r-1"

    def test_a_sealed_layer_can_itself_be_extended(self) -> None:
        with provider("audit", request_id="r-1", sealed=True):
            with provider("audit", extend=True, actor_id=7) as inner:
                assert (inner.request_id, inner.actor_id) == ("r-1", 7)

    def test_a_string_namespace_stays_writable_while_live(self) -> None:
        with provider("app", db="engine", sealed=True) as ctx:
            ctx.user_id = 42
            assert use("app").user_id == 42
        with pytest.raises(ExpiredScopeError, match=r"'app'\.user_id"):
            read(ctx, "user_id")

    def test_a_default_still_answers_after_the_block(self) -> None:
        with provider(Session(), sealed=True):
            pass
        assert use(Session, default=None) is None
        with pytest.raises(NoProviderError):
            use(Session)


class TestWithTheRestOfTheLibrary:
    def test_inject_fills_the_parameter_with_the_sealed_view(self) -> None:
        @inject
        def handler(session: FromCtx[Session] = injected) -> str:
            text: str = session.query()
            return text

        with provider(Session(), sealed=True):
            assert handler() == "rows from pg://"

    def test_export_reads_a_sealed_namespace_while_its_block_is_open(self) -> None:
        with provider("trace", request_id="r-1", sealed=True):
            assert export("trace") == {"v": 1, "ctx": {"trace": {"request_id": "r-1"}}}

    def test_an_exception_note_renders_the_value_and_not_the_expiry(self) -> None:
        with pytest.raises(ValueError, match="boom") as caught:
            with provider("audit", actor_id=7, sealed=True, annotate=True):
                raise ValueError("boom")
        # The note is built after the seal expires, off the raw value the block provided.
        notes = getattr(caught.value, "__notes__", [])
        if NOTES:
            assert notes == ["nodrill scope: Namespace('audit', actor_id=7)"]
        else:
            assert not notes

    def test_an_injected_read_names_the_caller_and_not_the_generated_wrapper(self) -> None:
        @inject(from_="app")
        def handler(dsn: str = injected) -> str:
            return dsn

        with provider("app", dsn="pg://", sealed=True):
            # The attribute is read inside the compiled wrapper, which is not user code.
            later = wrap(handler)
        with pytest.raises(ExpiredScopeError) as caught:
            later()
        assert caught.value.used[0] == __file__
        assert caught.value.operation == "dsn"

    def test_a_user_module_named_like_contextlib_is_not_taken_for_one(self) -> None:
        module = ModuleType("contextlib_ext")
        source = compile("def touch(value):\n    return value.dsn", "ext.py", "exec")
        exec(source, module.__dict__)  # noqa: S102
        with provider(Session(), sealed=True) as session:
            pass
        with pytest.raises(ExpiredScopeError) as caught:
            module.touch(session)
        assert caught.value.used[0] == "ext.py"

    def test_debug_mode_and_sealing_read_the_same_sites(self) -> None:
        with debug():
            with provider(Session(), sealed=True) as session:
                assert f"Session opened at {__file__}" in explain()
            with pytest.raises(ExpiredScopeError) as caught:
                read(session)
        assert caught.value.opened[0] == __file__


class TestWhatDoesNotCheck:
    def test_isinstance_holds_before_and_after(self) -> None:
        with provider(Session(), sealed=True) as session:
            assert isinstance(session, Session)
        assert isinstance(session, Session)

    def test_repr_describes_the_expiry_instead_of_raising(self) -> None:
        with provider(Session(), sealed=True) as session:
            assert repr(session) == f"<sealed {Session()!r}>"
        text = repr(session)
        assert text.startswith("<expired Session, opened at ")
        assert " exited at " in text

    def test_active_prints_a_sealed_value_without_resolving_it(self) -> None:
        with provider(lazy(Session, Session), sealed=True):
            assert "unresolved" in repr(active())

    def test_pickling_and_copying_are_refused(self) -> None:
        with provider(Session(), sealed=True) as session:
            with pytest.raises(TypeError, match="sealed context views cannot be pickled"):
                pickle.dumps(session)
            with pytest.raises(TypeError, match="sealed context views cannot be pickled"):
                copy.copy(session)
