import asyncio
from dataclasses import dataclass

import pytest

from nodrill import NoProviderError, provider, use


@dataclass
class Tenant:
    name: str = "?"


class TestTaskPropagation:
    async def test_create_task_inside_provider_sees_context(self) -> None:
        async def child() -> str:
            name: str = use(Tenant).name
            return name

        with provider(Tenant(name="acme")):
            task = asyncio.create_task(child())
        # The provider block exited before the task ran: the task keeps the
        # snapshot taken at create_task time (native contextvars semantics).
        assert await task == "acme"

    async def test_gather_inside_provider(self) -> None:
        async def child(i: int) -> str:
            await asyncio.sleep(0)
            return f"{use('req').rid}:{i}"

        with provider("req", rid="r-9"):
            results = await asyncio.gather(child(1), child(2))
        assert list(results) == ["r-9:1", "r-9:2"]

    async def test_task_created_outside_provider_sees_nothing(self) -> None:
        async def child() -> None:
            use(Tenant)

        task = asyncio.create_task(child())
        with pytest.raises(NoProviderError):
            await task


class TestTaskIsolation:
    async def test_two_concurrent_tasks_with_different_providers(self) -> None:
        """Sibling tasks entering their own providers never leak into each other."""
        checkpoints: list[str] = []

        async def worker(name: str) -> str:
            with provider(Tenant(name=name)):
                await asyncio.sleep(0.01)  # interleave with the sibling
                checkpoints.append(f"{name}={use(Tenant).name}")
                await asyncio.sleep(0.01)
                seen: str = use(Tenant).name
                return seen

        left, right = await asyncio.gather(worker("left"), worker("right"))
        assert (left, right) == ("left", "right")
        assert sorted(checkpoints) == ["left=left", "right=right"]

    async def test_provider_entered_in_task_invisible_to_parent(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def child() -> None:
            with provider(Tenant(name="child-only")):
                entered.set()
                await release.wait()

        task = asyncio.create_task(child())
        await entered.wait()
        with pytest.raises(NoProviderError):
            use(Tenant)  # the child's provider must not leak up
        release.set()
        await task

    async def test_shadowing_across_await_boundaries(self) -> None:
        async def read() -> str:
            await asyncio.sleep(0)
            rid: str = use("req").rid
            return rid

        with provider("req", rid="outer"):
            assert await read() == "outer"
            with provider("req", rid="inner"):
                assert await read() == "inner"
            assert await read() == "outer"
