"""The protocols _FrozenProxy forwards, blocks, or deliberately leaves alone."""

from collections.abc import AsyncIterator, Generator
from typing import Any

import pytest

from nodrill import FrozenContextError, provider, use


class Number:
    """A target that answers most of the numeric and ordering protocols."""

    def __init__(self, value: int = 7) -> None:
        self.value = value

    def __str__(self) -> str:
        return f"number {self.value}"

    def __format__(self, spec: str) -> str:
        return format(self.value, spec)

    def __bytes__(self) -> bytes:
        return bytes([self.value])

    def __int__(self) -> int:
        return self.value

    def __float__(self) -> float:
        return float(self.value)

    def __complex__(self) -> complex:
        return complex(self.value)

    def __index__(self) -> int:
        return self.value

    def __round__(self, ndigits: int = 0) -> int:
        return round(self.value, ndigits)

    def __abs__(self) -> int:
        return abs(self.value)

    def __neg__(self) -> int:
        return -self.value

    def __pos__(self) -> int:
        return +self.value

    def __invert__(self) -> int:
        return ~self.value

    def __lt__(self, other: int) -> bool:
        return self.value < other

    def __le__(self, other: int) -> bool:
        return self.value <= other

    def __gt__(self, other: int) -> bool:
        return self.value > other

    def __ge__(self, other: int) -> bool:
        return self.value >= other

    def __add__(self, other: int) -> int:
        return self.value + other

    def __radd__(self, other: int) -> int:
        return other + self.value

    def __sub__(self, other: int) -> int:
        return self.value - other

    def __mul__(self, other: int) -> int:
        return self.value * other

    def __truediv__(self, other: int) -> float:
        return self.value / other

    def __floordiv__(self, other: int) -> int:
        return self.value // other

    def __mod__(self, other: int) -> int:
        return self.value % other

    def __divmod__(self, other: int) -> tuple[int, int]:
        return divmod(self.value, other)

    def __pow__(self, other: int) -> int:
        return int(self.value**other)

    def __lshift__(self, other: int) -> int:
        return self.value << other

    def __rshift__(self, other: int) -> int:
        return self.value >> other

    def __and__(self, other: int) -> int:
        return self.value & other

    def __or__(self, other: int) -> int:
        return self.value | other

    def __xor__(self, other: int) -> int:
        return self.value ^ other

    def __matmul__(self, other: str) -> str:
        return f"matmul {other}"

    def __call__(self, arg: int, *, twice: bool = False) -> int:
        return self.value * arg * (2 if twice else 1)


class TestForwardedProtocols:
    def test_text_and_number_conversions(self) -> None:
        with provider(Number(7), frozen=True):
            p = use(Number)
            assert str(p) == "number 7"
            assert f"{p:03d}" == "007"
            assert bytes(p) == b"\x07"
            assert int(p) == 7
            assert float(p) == 7.0
            assert complex(p) == complex(7)
            assert [0, 1, 2, 3, 4, 5, 6, 7, 8][p] == 7  # __index__
            assert round(p) == 7
            assert abs(p) == 7
            assert -p == -7
            assert +p == 7
            assert ~p == ~7

    def test_ordering(self) -> None:
        with provider(Number(7), frozen=True):
            p = use(Number)
            assert p < 8
            assert p <= 7
            assert p > 6
            assert p >= 7

    def test_arithmetic(self) -> None:
        with provider(Number(7), frozen=True):
            p = use(Number)
            assert p + 1 == 8
            assert p - 1 == 6
            assert p * 2 == 14
            assert p / 2 == 3.5
            assert p // 2 == 3
            assert p % 2 == 1
            assert divmod(p, 2) == (3, 1)
            assert p**2 == 49
            assert p << 1 == 14
            assert p >> 1 == 3
            assert p & 1 == 1
            assert p | 8 == 15
            assert p ^ 1 == 6
            assert p @ "x" == "matmul x"

    def test_reflected_arithmetic(self) -> None:
        with provider(Number(7), frozen=True):
            assert 1 + use(Number) == 8

    def test_call(self) -> None:
        with provider(Number(7), frozen=True):
            p = use(Number)
            assert p(2) == 14
            assert p(2, twice=True) == 28

    def test_in_place_operator_leaves_the_target_alone(self) -> None:
        # No __iadd__ on the proxy, so `+=` falls back to __add__ and rebinds the name.
        target = Number(7)
        with provider(target, frozen=True):
            p: Any = use(Number)
            p += 1
            assert p == 8
            assert target.value == 7


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
