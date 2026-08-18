import threading
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from nodrill import Executor, NoProviderError, provider, use, wrap


@dataclass
class Config:
    tag: str = "?"


def read_tag() -> str:
    tag: str = use(Config).tag
    return tag


async def _async_read_tag() -> str:
    return read_tag()


async def _async_gen() -> AsyncIterator[str]:
    yield read_tag()


# What the in_thread fixture in conftest hands over, as a type.
ThreadRunner = Callable[[Callable[[], Any]], Any]


@pytest.fixture
def pool() -> Iterator[Executor]:
    with Executor(max_workers=2) as executor:
        yield executor


class TestPlainThreads:
    def test_plain_thread_does_not_see_context(self, in_thread: ThreadRunner) -> None:
        """The documented behavior: threading.Thread starts with an empty context."""
        with provider(Config(tag="main")):
            assert isinstance(in_thread(read_tag), NoProviderError)

    def test_wrapped_target_sees_context(self, in_thread: ThreadRunner) -> None:
        with provider(Config(tag="main")):
            assert in_thread(wrap(read_tag)) == "main"

    def test_wrap_snapshots_at_wrap_time(self) -> None:
        with provider(Config(tag="at-wrap")):
            bound = wrap(read_tag)
        with provider(Config(tag="at-call")):
            assert bound() == "at-wrap"

    def test_wrapped_callable_safe_to_call_concurrently(self) -> None:
        with provider(Config(tag="shared")):
            bound = wrap(read_tag)
        start = threading.Barrier(8)
        results: list[Any] = []
        lock = threading.Lock()

        def hammer() -> None:
            start.wait()
            for _ in range(50):
                value = bound()
                with lock:
                    results.append(value)

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 400
        assert set(results) == {"shared"}

    @pytest.mark.parametrize("target", [_async_read_tag, _async_gen], ids=["coroutine", "asyncgen"])
    def test_wrap_rejects_async_functions(self, target: Any) -> None:
        """A coroutine body resumes in the caller's context, dropping the snapshot."""
        with pytest.raises(TypeError, match="does not support async functions"):
            wrap(target)

    def test_wrap_writes_do_not_leak_to_wrapper_caller(self) -> None:
        def writer() -> None:
            with provider(Config(tag="inside")):
                assert read_tag() == "inside"

        with provider(Config(tag="outer")):
            wrap(writer)()
            assert read_tag() == "outer"


class TestExecutor:
    def test_submit_propagates_submit_time_context(self, pool: Executor) -> None:
        with provider(Config(tag="pool")):
            assert pool.submit(read_tag).result() == "pool"

    def test_map_propagates(self, pool: Executor) -> None:
        def job(i: int) -> str:
            return f"{use(Config).tag}:{i}"

        with provider(Config(tag="m")):
            assert list(pool.map(job, range(3))) == ["m:0", "m:1", "m:2"]

    def test_each_submit_sees_its_own_snapshot(self, pool: Executor) -> None:
        with provider(Config(tag="first")):
            first = pool.submit(read_tag)
        with provider(Config(tag="second")):
            second = pool.submit(read_tag)
        assert (first.result(), second.result()) == ("first", "second")

    def test_worker_writes_do_not_leak_between_tasks(self) -> None:
        def writes() -> str:
            with provider(Config(tag="task-local")):
                return read_tag()

        with provider(Config(tag="base")), Executor(max_workers=1) as pool:
            assert pool.submit(writes).result() == "task-local"
            assert pool.submit(read_tag).result() == "base"  # same worker thread, clean slate


class TestThreadSafetySmoke:
    def test_many_threads_own_providers_no_crosstalk(self) -> None:
        """Concurrent providers in many threads never mix."""
        thread_count, laps = 16, 30
        start = threading.Barrier(thread_count)
        failures: list[str] = []
        lock = threading.Lock()

        def worker(worker_id: int) -> None:
            start.wait()
            for lap in range(laps):
                expected = f"w{worker_id}-l{lap}"
                with provider("slot", owner=expected), provider(Config(tag=expected)):
                    seen_ns = use("slot").owner
                    seen_cfg = use(Config).tag
                    if (seen_ns, seen_cfg) != (expected, expected):
                        with lock:
                            failures.append(f"{expected} saw {seen_ns}/{seen_cfg}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not failures
