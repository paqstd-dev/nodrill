from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest

from nodrill import inject, provider, use


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
