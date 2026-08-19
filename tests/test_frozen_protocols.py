"""The protocols _FrozenProxy blocks or deliberately leaves alone, and the ones it invokes.

The forwarded tables are swept against the value itself in test_views.py, for
every view at once.
"""

from collections.abc import AsyncIterator, Generator
from typing import Any

import pytest

from nodrill import FrozenContextError, provider, use


class Guard:
    def __init__(self) -> None:
        self.entered = False

    def __enter__(self) -> str:
        self.entered = True
        return "sync"

    def __exit__(self, *_exc: object) -> None:
        return

    async def __aenter__(self) -> str:
        return "async"

    async def __aexit__(self, *_exc: object) -> None:
        return


class Stream:
    def __aiter__(self) -> AsyncIterator[int]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[int]:
        yield 1


class Waiter:
    def __await__(self) -> Generator[Any, Any, None]:
        return
        yield


class TestInvokedProtocols:
    def test_context_manager(self) -> None:
        with provider(Guard(), frozen=True):
            with use(Guard) as entered:
                assert entered == "sync"

    async def test_async_context_manager(self) -> None:
        with provider(Guard(), frozen=True):
            async with use(Guard) as entered:
                assert entered == "async"

    async def test_async_iteration(self) -> None:
        with provider(Stream(), frozen=True):
            assert [n async for n in use(Stream)] == [1]

    async def test_await(self) -> None:
        with provider(Waiter(), frozen=True):
            awaited: Any = use(Waiter)
            assert await awaited is None


class Table:
    def __init__(self) -> None:
        self.rows = {"a": 1}

    def __getitem__(self, key: str) -> int:
        return self.rows[key]

    def __setitem__(self, key: str, value: int) -> None:
        self.rows[key] = value

    def __delitem__(self, key: str) -> None:
        del self.rows[key]


class TestBlockedProtocols:
    def test_item_assignment_raises(self) -> None:
        target = Table()
        with provider(target, frozen=True):
            with pytest.raises(FrozenContextError, match="cannot set item 'b'"):
                use(Table)["b"] = 2
            assert target.rows == {"a": 1}

    def test_item_deletion_raises(self) -> None:
        target = Table()
        with provider(target, frozen=True):
            with pytest.raises(FrozenContextError, match="cannot delete item 'a'"):
                del use(Table)["a"]
            assert target.rows == {"a": 1}

    def test_item_reads_still_work(self) -> None:
        with provider(Table(), frozen=True):
            assert use(Table)["a"] == 1

    def test_in_place_operator_leaves_the_target_alone(self) -> None:
        # No __iadd__ on the proxy, so `+=` falls back to __add__ and rebinds the name.
        target = [1, 2]
        with provider(target, frozen=True):
            items: Any = use(list)
            items += [3]
            assert items == [1, 2, 3]
        assert target == [1, 2]


class Answer:
    """A target whose == answers with something other than a bool."""

    def __eq__(self, other: object) -> Any:
        return f"compared to {other!r}"

    def __ne__(self, other: object) -> Any:
        return f"differs from {other!r}"

    __hash__ = None  # type: ignore[assignment]


class TestEquality:
    def test_comparison_result_passes_through_uncoerced(self) -> None:
        with provider(Answer(), frozen=True):
            assert (use(Answer) == 1) == "compared to 1"
            assert (use(Answer) != 1) == "differs from 1"


class TestShallowFreeze:
    def test_freeze_does_not_reach_into_attributes(self) -> None:
        # Documented as a guard rail against accidental writes, not a boundary.
        target = Table()
        with provider(target, frozen=True):
            use(Table).rows["b"] = 2
        assert target.rows == {"a": 1, "b": 2}
