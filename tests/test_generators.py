import asyncio
import threading
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from contextlib import aclosing
from contextvars import Context
from typing import Any

import pytest

from nodrill import (
    NoProviderError,
    OrphanedProviderWarning,
    debug,
    explain,
    inject,
    provider,
    use,
)


def _sync_gen() -> Iterator[int]:
    yield 1


async def _async_gen() -> AsyncIterator[int]:
    yield 1


class TestGeneratorPolicy:
    """Injection resolves at call time, but a generator body runs later."""

    @pytest.mark.parametrize("gen", [_sync_gen, _async_gen], ids=["sync", "async"])
    def test_rejected_at_decoration(self, gen: Any) -> None:
        with pytest.raises(TypeError) as exc_info:
            inject(gen)
        message = str(exc_info.value)
        assert "does not support generator functions" in message
        assert "Call use() inside the generator body" in message

    def test_plain_use_inside_generator_works(self) -> None:
        def gen() -> Iterator[str]:
            while True:
                yield use("app").mode

        stream = gen()
        with provider("app", mode="first"):
            assert next(stream) == "first"
        with provider("app", mode="second"):
            assert next(stream) == "second"


async def _held(mode: str) -> AsyncGenerator[int, None]:
    async with provider("app", mode=mode):
        for i in range(3):
            yield i


class TestAsyncGeneratorFinalization:
    """An abandoned async generator is finalized elsewhere, so its block cannot unwind."""

    async def test_abandoned_generator_warns_and_leaks(self) -> None:
        stream = _held("x")
        assert await anext(stream) == 0
        # asyncio finalizes an abandoned generator this way, in a task with its own context.
        with pytest.warns(OrphanedProviderWarning, match="did not open in"):
            await asyncio.create_task(stream.aclose())
        assert use("app").mode == "x"

    async def test_aclosing_pairs_the_block(self) -> None:
        async with aclosing(_held("x")) as stream:
            async for i in stream:
                if i == 1:
                    break
        with pytest.raises(NoProviderError):
            use("app")


class TestOrphanedBookkeeping:
    """A filter making the warning an error must not cost the unwind its bookkeeping."""

    def test_a_live_exception_is_not_displaced(self) -> None:
        entered = provider("svc", n=1)
        Context().run(entered.__enter__)
        failure = ValueError("the real failure")
        # No raise, since the warning must never become the exception the caller sees.
        entered.__exit__(ValueError, failure, None)

    def test_the_ledger_still_records_the_exit(self) -> None:
        with debug():
            entered = provider("svc", n=1)
            Context().run(entered.__enter__)
            with pytest.raises(OrphanedProviderWarning):
                entered.__exit__(None, None, None)
            assert "provider block open" not in explain()

    def test_a_thread_that_did_not_open_the_block_cannot_close_it(self) -> None:
        entered = provider("svc", n=1)
        thread = threading.Thread(target=entered.__enter__)
        thread.start()
        thread.join()
        with pytest.warns(OrphanedProviderWarning, match="did not open in"):
            entered.__exit__(None, None, None)


def _tenant(slug: str) -> Iterator[str]:
    with provider("tenant", slug=slug):
        yield str(use("tenant").slug)
        yield str(use("tenant").slug)


def _interleave() -> None:
    """Open two blocks and close them in the order they opened, which is the wrong one."""
    first, second = _tenant("acme"), _tenant("globex")
    list(zip(first, second, strict=False))
    list(first)
    list(second)


class TestOutOfOrderExit:
    """A block closing before one that opened later must not bring its snapshot back."""

    def test_nothing_survives_both_blocks(self) -> None:
        _interleave()
        with pytest.raises(NoProviderError):
            use("tenant")

    def test_an_enclosing_block_is_left_alone(self) -> None:
        with provider("tenant", slug="outer"):
            _interleave()
            assert use("tenant").slug == "outer"

    def test_read_counting_survives_the_rebuild(self) -> None:
        with debug(unused=True), provider("tenant", slug="outer"):
            _interleave()
            assert use("tenant").slug == "outer"
