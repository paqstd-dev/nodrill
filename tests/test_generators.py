import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from contextlib import aclosing
from typing import Any

import pytest

from nodrill import NoProviderError, OrphanedProviderWarning, inject, provider, use


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
        with pytest.warns(OrphanedProviderWarning, match="different context"):
            await asyncio.create_task(stream.aclose())
        assert use("app").mode == "x"

    async def test_aclosing_pairs_the_block(self) -> None:
        async with aclosing(_held("x")) as stream:
            async for i in stream:
                if i == 1:
                    break
        with pytest.raises(NoProviderError):
            use("app")
