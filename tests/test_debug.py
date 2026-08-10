import asyncio
import gc
import inspect
import os
import pickle
import subprocess
import sys
import threading
import weakref
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

import pytest

import nodrill
from nodrill import FromCtx, NoProviderError, debug, explain, inject, injected, provider, use, wrap


@dataclass
class Session:
    tag: str = "?"


@dataclass
class Other:
    tag: str = "?"


# What the miss says with debug mode off, and what every diagnosis is appended to.
PLAIN = (
    "use(Session): no active provider for Session. No providers are active. "
    "Hint: did you forget `with provider(Session(...))`? "
    "A fallback can be registered with `set_default(Session, ...)`."
)


def line_above() -> int:
    """Return the line number of the statement just above the call."""
    frame = inspect.currentframe()
    assert frame is not None
    assert frame.f_back is not None
    return frame.f_back.f_lineno - 1


def read_session() -> NoProviderError:
    """Read Session where nothing provides it, and hand the failure back."""
    try:
        use(Session)
    except NoProviderError as exc:
        return exc
    pytest.fail("expected the lookup to miss")


def in_thread(target: Callable[[], Any], name: str) -> Any:
    """Run target on a fresh thread with a known name and return what it produced."""
    box: list[Any] = []

    def runner() -> None:
        box.append(target())

    thread = threading.Thread(target=runner, name=name)
    thread.start()
    thread.join()
    [result] = box
    return result


def layer_over_a_namespace() -> int:
    """Lay a layer over a namespace without reading either, and return the layer's line."""
    with provider("app", db=1):
        with provider("app", extend=True, tag="x"):
            return line_above()


@inject
def needs_session(session: FromCtx[Session] = injected) -> Session:
    return session


@inject
def needs_other(other: FromCtx[Other] = injected) -> Other:
    return other


class TestDebugOff:
    def test_message_is_unchanged(self) -> None:
        """With debug mode off the miss says exactly what it said before it existed."""
        assert str(read_session()) == PLAIN

    def test_no_diagnosis_is_attached(self) -> None:
        """The diagnosis attribute is None unless debug mode was on for the lookup."""
        assert read_session().diagnosis is None

    def test_a_key_nothing_ever_provided_gets_no_diagnosis(self) -> None:
        """Debug mode invents nothing for a key no provider in the process ever opened."""
        with debug():
            error = read_session()
        assert error.diagnosis is None
        assert str(error) == PLAIN


class TestOpenElsewhere:
    def test_another_thread_names_the_site_and_the_fix(self) -> None:
        """A miss on a thread that did not inherit the context names the open block."""
        with debug(), provider(Session()):
            opened = line_above()
            error = in_thread(read_session, name="worker")
        assert str(error) == (
            f"{PLAIN}\n"
            f"\n"
            f"Session is open right now at {__file__}:{opened}, on thread 'MainThread'.\n"
            f"This frame is on thread 'worker', which did not inherit that context.\n"
            f"Fix: submit through nodrill.Executor instead of ThreadPoolExecutor, or bind the "
            f"callable with nodrill.wrap() inside the provider block."
        )

    def test_a_snapshot_taken_before_the_block_names_the_context(self) -> None:
        """A callable bound before the block runs on this thread and still cannot see it."""
        bound = wrap(read_session)
        with debug(), provider(Session()):
            opened = line_above()
            error = bound()
        assert str(error) == (
            f"{PLAIN}\n"
            f"\n"
            f"Session is open right now at {__file__}:{opened}, on thread 'MainThread'.\n"
            f"This frame is on that thread and still cannot see it, so it is running under a "
            f"different context: a contextvars.Context.run(), a nodrill.wrap() snapshot taken "
            f"before the block, or a generator resumed outside it.\n"
            f"Fix: enter the provider inside the frame that reads it, or bind the callable with "
            f"nodrill.wrap() inside the block."
        )

    def test_the_innermost_open_block_is_the_one_reported(self) -> None:
        """With the same key open twice, the diagnosis names the block entered last."""
        with debug(), provider(Session(tag="outer")):
            with provider(Session(tag="inner")):
                opened = line_above()
                error = in_thread(read_session, name="worker")
        assert f"{__file__}:{opened}" in str(error)

    def test_a_bare_pool_worker_is_diagnosed(self) -> None:
        """The case the feature exists for: submitting to a pool that is not nodrill's."""
        with debug(), provider(Session()), ThreadPoolExecutor(max_workers=1) as pool:
            error = pool.submit(read_session).result()
        assert "which did not inherit that context" in str(error)
        assert "submit through nodrill.Executor" in str(error)


class TestAlreadyClosed:
    def test_a_callback_after_the_block_names_the_open_site(self) -> None:
        """A miss after the block closed says so, rather than reporting an absent provider."""
        with debug():
            with provider(Session()):
                opened = line_above()
            error = read_session()
        assert str(error) == (
            f"{PLAIN}\n"
            f"\n"
            f"Session was open at {__file__}:{opened}, on thread 'MainThread'.\n"
            f"This frame is running after that block closed.\n"
            f"Fix: do the work inside the block, or bind the callback with nodrill.wrap() inside "
            f"it, which carries the scope to wherever it runs."
        )

    def test_an_exit_somewhere_else_is_named_too(self) -> None:
        """A block closed away from where it was opened reports both sites."""
        with debug():
            stack = ExitStack()
            stack.enter_context(provider(Session()))
            opened = line_above()
            stack.close()
            closed = line_above()
            error = read_session()
        assert (
            f"Session was open at {__file__}:{opened} and exited at {__file__}:{closed}, "
            f"on thread 'MainThread'." in str(error)
        )

    def test_a_reused_provider_reports_its_last_run(self) -> None:
        """A provider entered twice in sequence leaves one record, from the second run."""
        reusable = provider(Session())
        with debug():
            with reusable:
                pass
            with reusable:
                opened = line_above()
            error = read_session()
        assert f"Session was open at {__file__}:{opened}," in str(error)


class TestTasks:
    async def test_a_task_created_before_the_block_names_both_tasks(self) -> None:
        """An asyncio task that predates the block never snapshotted it, and is told so."""
        ready = asyncio.Event()
        failures: list[NoProviderError] = []
        current = asyncio.current_task()
        assert current is not None

        async def child() -> None:
            await ready.wait()
            failures.append(read_session())

        with debug():
            task = asyncio.create_task(child(), name="child")
            with provider(Session()):
                opened = line_above()
                ready.set()
                await task
        [error] = failures
        assert str(error) == (
            f"{PLAIN}\n"
            f"\n"
            f"Session is open right now at {__file__}:{opened}, on thread 'MainThread', "
            f"task {current.get_name()!r}.\n"
            f"This frame is running in task 'child', which was created outside that block, so it "
            f"never snapshotted it.\n"
            f"Fix: create the task inside the provider block, or await the work there."
        )

    async def test_the_task_is_named_in_the_report(self) -> None:
        """explain() names the task a block was opened in, not only the thread."""
        current = asyncio.current_task()
        assert current is not None
        with debug(), provider(Session()):
            report = explain()
        assert f"on thread 'MainThread', task {current.get_name()!r}" in report


class TestLedgerLifetime:
    def test_a_block_is_forgotten_on_a_normal_exit(self) -> None:
        """Nothing is left in the ledger once the block ends."""
        with debug():
            with provider(Session()):
                pass
            assert explain() == "nodrill debug: no provider block is open."

    def test_a_block_is_forgotten_when_it_raises(self) -> None:
        """An exception unwinding the block still clears its ledger entry."""
        with debug():
            with pytest.raises(ValueError, match="boom"), provider(Session()):
                raise ValueError("boom")
            assert explain() == "nodrill debug: no provider block is open."

    def test_a_reused_provider_leaves_nothing_behind(self) -> None:
        """Entering the same provider object twice in sequence balances both times."""
        reusable = provider(Session())
        with debug():
            with reusable:
                pass
            with reusable:
                pass
            assert explain() == "nodrill debug: no provider block is open."

    def test_a_block_opened_before_debug_mode_is_ignored(self) -> None:
        """A block whose entry was never recorded exits without disturbing the ledger."""
        stack = ExitStack()
        stack.enter_context(provider(Session()))
        with debug():
            stack.close()
            assert explain() == "nodrill debug: no provider block is open."

    def test_turning_debug_off_clears_what_is_still_open(self) -> None:
        """A block still open when recording stops is not reported once it restarts."""
        stack = ExitStack()
        with debug():
            stack.enter_context(provider(Session()))
        with debug():
            assert explain() == "nodrill debug: no provider block is open."
            stack.close()
            assert read_session().diagnosis is None

    def test_debug_nests_and_the_inner_block_does_not_turn_it_off(self) -> None:
        """Recording is reference counted, so the outer block keeps it on."""
        with debug(), provider(Session()):
            with debug():
                pass
            assert "Session opened at" in explain()
        assert explain().startswith("nodrill debug mode is off")


class TestNoRetention:
    def test_the_provided_value_is_not_held(self) -> None:
        """The ledger records the key and the site, never the value."""
        session = Session()
        gone = weakref.ref(session)
        with debug():
            with provider(session):
                pass
            del session
            gc.collect()
            assert gone() is None

    def test_the_provider_is_not_held(self) -> None:
        """Entries are keyed by identity, so the provider object is not referenced either."""
        opened = provider(Session())
        before = sys.getrefcount(opened)
        with debug():
            with opened:
                pass
            assert sys.getrefcount(opened) == before


class TestReport:
    def test_the_report_says_debug_mode_is_off(self) -> None:
        """explain() outside debug mode says why it has nothing to report."""
        assert explain() == (
            "nodrill debug mode is off, so no provider block is recorded.\n"
            "Turn it on with `with nodrill.debug():` or with NODRILL_DEBUG=1 in the environment."
        )

    def test_the_report_lists_open_blocks_innermost_first(self) -> None:
        """Blocks are listed in the order that puts the one you are inside at the top."""
        with debug(), provider("app"):
            outer = line_above()
            with provider(Session()):
                inner = line_above()
                report = explain()
        assert report == (
            f"nodrill debug: 2 provider blocks open, innermost first.\n"
            f"  Session opened at {__file__}:{inner}, on thread 'MainThread'\n"
            f"  'app' opened at {__file__}:{outer}, on thread 'MainThread'"
        )

    def test_one_open_block_is_counted_in_the_singular(self) -> None:
        """The count reads as a sentence for one block as well as for several."""
        with debug(), provider(Session()):
            assert explain().startswith("nodrill debug: 1 provider block open,")


class TestUnusedProviders:
    def test_a_provider_nothing_read_warns_at_the_with_statement(self) -> None:
        """unused=True reports a dead provider, pointing at the block that opened it."""
        with pytest.warns(UserWarning, match="never read") as records, debug(unused=True):
            with provider(Session()):
                opened = line_above()
        [record] = records
        assert str(record.message) == (
            f"nodrill: the provider for Session at {__file__}:{opened} was never read, "
            f"since no use(Session) ran inside the block."
        )
        assert record.filename == __file__
        assert record.lineno == opened

    def test_a_read_provider_is_silent(self) -> None:
        """A provider something read is not warned about; warnings are errors here."""
        with debug(unused=True), provider(Session()):
            use(Session)

    def test_a_read_through_inject_counts(self) -> None:
        """A compiled @inject wrapper reads the registry its own way, and that counts."""
        with debug(unused=True), provider(Session()):
            needs_session()

    def test_a_miss_through_inject_is_not_a_read(self) -> None:
        """Looking for a key that is not there leaves the provider that is there unread."""
        with pytest.warns(UserWarning, match="never read"), debug(unused=True):
            with provider(Session()), pytest.raises(NoProviderError):
                needs_other()

    def test_an_extending_layer_counts_as_a_read_of_what_it_extends(self) -> None:
        """Laying a layer over a namespace reads it, so only the layer itself is dead."""
        with debug(unused=True), pytest.warns(UserWarning, match="never read") as records:
            layered = layer_over_a_namespace()
        [record] = records
        assert record.lineno == layered

    def test_a_block_that_raised_is_not_warned_about(self) -> None:
        """A body that blew up never had the chance to read, so it is not blamed for it."""
        with debug(unused=True):
            with pytest.raises(ValueError, match="boom"), provider(Session()):
                raise ValueError("boom")

    def test_counting_stays_off_without_the_flag(self) -> None:
        """Plain debug() records sites and nothing else, so an unread provider is silent."""
        with debug(), provider(Session()):
            pass

    def test_counting_is_reference_counted_too(self) -> None:
        """Leaving the inner block turns counting off again while recording continues."""
        with debug():
            with debug(unused=True), provider(Session()):
                use(Session)
            with provider(Session()):
                pass


class TestEnvironment:
    def test_the_environment_variable_is_read_at_import(self) -> None:
        """NODRILL_DEBUG turns recording on for a process that was never edited."""
        program = (
            "import nodrill\n"
            "with nodrill.provider('app'):\n"
            "    pass\n"
            "try:\n"
            "    nodrill.use('app')\n"
            "except nodrill.NoProviderError as exc:\n"
            "    print(exc.diagnosis)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", program],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "NODRILL_DEBUG": "1"},
        )
        assert "was open at" in result.stdout
        assert "This frame is running after that block closed." in result.stdout

    def test_an_unset_variable_leaves_debug_mode_off(self) -> None:
        """The variable is off unless it is set to something, and 0 counts as off."""
        program = "import nodrill\nprint(nodrill.explain())\n"
        result = subprocess.run(
            [sys.executable, "-c", program],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "NODRILL_DEBUG": "0"},
        )
        assert result.stdout.startswith("nodrill debug mode is off")


class TestErrorObject:
    def test_the_diagnosis_survives_a_pickle_round_trip(self) -> None:
        """A worker process delivers a failure by pickling it, diagnosis included."""
        with debug():
            with provider(Session()):
                pass
            error = read_session()
        restored = pickle.loads(pickle.dumps(error))
        assert restored.diagnosis == error.diagnosis
        assert str(restored) == str(error)

    def test_the_diagnosis_is_also_an_attribute(self) -> None:
        """The rendered message is the product, but the cause is readable on its own."""
        with debug(), provider(Session()):
            error = in_thread(read_session, name="worker")
        assert error.diagnosis is not None
        assert str(error).endswith(error.diagnosis)
        assert error.key is Session


class TestSurface:
    def test_debug_and_explain_are_exported(self) -> None:
        """Both names are part of the public surface."""
        assert "debug" in nodrill.__all__
        assert "explain" in nodrill.__all__
