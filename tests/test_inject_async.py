import asyncio
import inspect
from dataclasses import dataclass
from typing import Annotated

from nodrill import FromCtx, inject, injected, provider


@dataclass
class Config:
    tag: str = "cfg"


@inject
async def fetch(cfg: FromCtx[Config] = injected, retry: int = 0) -> str:
    await asyncio.sleep(0)
    return f"{cfg.tag}:{retry}"


class TestAsyncInjection:
    def test_wrapper_is_a_coroutine_function(self) -> None:
        """The wrapper must be async — never a sync function returning a coroutine."""
        assert inspect.iscoroutinefunction(fetch)

    async def test_injects_from_provider(self) -> None:
        with provider(Config(tag="live")):
            assert await fetch() == "live:0"

    async def test_explicit_arguments_win(self) -> None:
        with provider(Config(tag="live")):
            assert await fetch(Config(tag="manual"), retry=2) == "manual:2"

    async def test_all_explicit_outside_provider(self) -> None:
        assert await fetch(Config(tag="solo"), 1) == "solo:1"

    async def test_resolution_uses_context_at_await_point(self) -> None:
        with provider(Config(tag="outer")):
            coro_born_outer = fetch()
            with provider(Config(tag="inner")):
                # bind_partial + use() run when the coroutine is awaited.
                assert await coro_born_outer == "inner:0"

    async def test_async_method(self) -> None:
        class Repo:
            @inject
            async def load(self, name: Annotated[str, FromCtx("req")] = injected) -> str:
                return f"load:{name}"

        with provider("req", name="doc-1"):
            assert await Repo().load() == "load:doc-1"

    async def test_async_from_mode(self) -> None:
        @inject(from_="req")
        async def handler(user: str, lang: str = "en") -> str:
            return f"{user}/{lang}"

        with provider("req", user="alice"):
            assert await handler() == "alice/en"  # type: ignore[call-arg]
