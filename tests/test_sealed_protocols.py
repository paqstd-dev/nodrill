"""The protocols _SealedProxy forwards while its block is open, and refuses after."""

import math
from collections.abc import AsyncIterator, Generator, Iterator
from typing import Any

import pytest

from nodrill import ExpiredScopeError, provider, use
from nodrill._sealed import _SealedProxy
from nodrill._views import _FORWARDED, _INPLACE, _INVOKED, _ITEM_WRITES, _REFLECTED

# The three that answer after expiry, and the three that refuse whatever the state.
_UNCHECKED = frozenset(
    {"__class__", "__repr__", "__dir__", "__reduce_ex__", "__copy__", "__deepcopy__"}
)

# Written in the class body rather than generated, with the arguments each of them takes.
_HANDWRITTEN = (
    ("__getattr__", ("value",)),
    ("__setattr__", ("value", 1)),
    ("__delattr__", ("value",)),
    ("__eq__", (1,)),
    ("__ne__", (1,)),
    ("__hash__", ()),
)

_GENERATED = (*_FORWARDED, *_ITEM_WRITES, *_INPLACE, *_REFLECTED, *_INVOKED)


class Everything:
    """A target answering enough protocols for the live half of the table."""

    def __init__(self, value: int = 7) -> None:
        self.value = value
        self.rows = {"a": 1}

    def __str__(self) -> str:
        return f"number {self.value}"

    def __format__(self, spec: str) -> str:
        return format(self.value, spec)

    def __bytes__(self) -> bytes:
        return bytes([self.value])

    def __bool__(self) -> bool:
        return bool(self.value)

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

    def __floor__(self) -> str:
        return "floored"

    def __ceil__(self) -> str:
        return "ceiled"

    def __trunc__(self) -> str:
        return "truncated"

    def __neg__(self) -> int:
        return -self.value

    def __pos__(self) -> int:
        return +self.value

    def __invert__(self) -> int:
        return ~self.value

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Iterator[str]:
        return iter(self.rows)

    def __reversed__(self) -> Iterator[str]:
        return reversed(list(self.rows))

    def __contains__(self, item: str) -> bool:
        return item in self.rows

    def __getitem__(self, key: str) -> int:
        return self.rows[key]

    def __call__(self, arg: int, *, twice: bool = False) -> int:
        return self.value * arg * (2 if twice else 1)

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

    def __rsub__(self, other: int) -> int:
        return other - self.value

    def __sub__(self, other: int) -> int:
        return self.value - other

    def __mul__(self, other: int) -> int:
        return self.value * other

    def __matmul__(self, other: str) -> str:
        return f"matmul {other}"

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


class Matrix:
    """A target for the one in-place operator an int cannot answer."""

    def __init__(self) -> None:
        self.applied = ""

    # Any rather than Self, which needs the 3.11 typing this package does not require yet.
    def __imatmul__(self, other: str) -> Any:
        self.applied = other
        return self


class Guard:
    def __enter__(self) -> str:
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


class Counter:
    def __init__(self) -> None:
        self.seen = 0

    def __next__(self) -> int:
        self.seen += 1
        return self.seen


class TestLiveProtocols:
    def test_text_and_number_conversions(self) -> None:
        with provider(Everything(), sealed=True):
            p = use(Everything)
            assert str(p) == "number 7"
            assert f"{p:03d}" == "007"
            assert bytes(p) == b"\x07"
            assert bool(p) is True
            assert int(p) == 7
            assert float(p) == 7.0
            assert complex(p) == complex(7)
            assert [0, 1, 2, 3, 4, 5, 6, 7, 8][p] == 7  # __index__
            assert round(p) == 7
            assert abs(p) == 7
            assert (math.floor(p), math.ceil(p), math.trunc(p)) == (
                "floored",
                "ceiled",
                "truncated",
            )
            assert -p == -7
            assert +p == 7
            assert ~p == ~7

    def test_containers_and_ordering(self) -> None:
        with provider(Everything(), sealed=True):
            p = use(Everything)
            assert len(p) == 1
            assert list(p) == ["a"]
            assert list(reversed(p)) == ["a"]
            assert "a" in p
            assert p["a"] == 1
            assert p < 8
            assert p <= 7
            assert p > 6
            assert p >= 7

    def test_arithmetic_and_calling(self) -> None:
        with provider(Everything(), sealed=True):
            p = use(Everything)
            assert p + 1 == 8
            assert 1 + p == 8
            # Not commutative, so the operands cannot be the wrong way round unnoticed.
            assert 5 - p == -2
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
            assert p(2) == 14
            assert p(2, twice=True) == 28

    def test_item_and_in_place_mutation_goes_through(self) -> None:
        # A list, since the in-place operators are what frozen omits and sealed forwards.
        target = [1, 2]
        with provider(target, key="items", sealed=True):
            items: Any = use("items")
            items[0] = 9
            del items[1]
            items += [3]
        assert target == [9, 3]

    def test_a_mutating_in_place_operator_hands_the_view_back(self) -> None:
        target = [1]
        with provider(target, key="items", sealed=True) as items:
            items += [2]
            assert use("items") == [1, 2]
        # The name still holds the view, so the seal survived the rebinding.
        with pytest.raises(ExpiredScopeError):
            items.append(3)
        assert target == [1, 2]

    def test_an_immutable_target_rebinds_to_the_result(self) -> None:
        with provider(7, key="count", sealed=True) as count:
            total: Any = count
            total += 1
        # A new object, which was never the provided value and so is not sealed.
        assert total == 8

    def test_a_matmul_that_mutates_hands_the_view_back(self) -> None:
        with provider(Matrix(), sealed=True) as matrix:
            product: Any = matrix
            product @= "x"
            assert use(Matrix).applied == "x"
            assert product is matrix

    def test_attribute_writes_and_deletes_go_through(self) -> None:
        target = Everything()
        with provider(target, sealed=True):
            p: Any = use(Everything)
            p.value = 9
            p.note = "temporary"
            del p.note
        assert target.value == 9
        assert not hasattr(target, "note")

    def test_next_delegates(self) -> None:
        with provider(Counter(), sealed=True):
            assert next(use(Counter)) == 1

    def test_equality_hash_and_dir(self) -> None:
        target = Everything()
        with provider(target, sealed=True):
            p = use(Everything)
            assert p == target
            assert (p != target) is False
            assert hash(p) == hash(target)
            assert "value" in dir(p)

    def test_context_manager(self) -> None:
        with provider(Guard(), sealed=True):
            with use(Guard) as entered:
                assert entered == "sync"

    async def test_async_context_manager(self) -> None:
        with provider(Guard(), sealed=True):
            async with use(Guard) as entered:
                assert entered == "async"

    async def test_async_iteration(self) -> None:
        with provider(Stream(), sealed=True):
            assert [n async for n in use(Stream)] == [1]

    async def test_await(self) -> None:
        with provider(Waiter(), sealed=True):
            awaited: Any = use(Waiter)
            assert await awaited is None


class TestExpiredProtocols:
    @pytest.fixture
    def expired(self) -> Everything:
        """Hand over a view whose block has already exited."""
        with provider(Everything(), sealed=True) as sealed:
            pass
        return sealed

    @pytest.mark.parametrize("name", _GENERATED)
    def test_every_generated_protocol_checks_the_scope(
        self, expired: Everything, name: str
    ) -> None:
        # Called with no arguments, since the check runs before the delegation, so
        # a missing one shows up as something other than ExpiredScopeError.
        args = (1,) if name in _REFLECTED or name in _INPLACE else ()
        method = getattr(type(expired), name)
        with pytest.raises(ExpiredScopeError, match=rf"Everything\.{name} was used after"):
            method(expired, *args)

    @pytest.mark.parametrize(
        ("name", "args"), _HANDWRITTEN, ids=[name for name, _args in _HANDWRITTEN]
    )
    def test_every_handwritten_member_checks_the_scope(
        self, expired: Everything, name: str, args: tuple[Any, ...]
    ) -> None:
        # An attribute member reports the attribute, and the rest report themselves.
        reported = "value" if name.endswith("attr__") else name
        method = getattr(type(expired), name)
        with pytest.raises(ExpiredScopeError, match=rf"Everything\.{reported} was used after"):
            method(expired, *args)

    def test_the_table_is_the_coverage_and_nothing_slipped_past_it(self) -> None:
        generated = {
            name
            for name, member in vars(_SealedProxy).items()
            if name.startswith("__") and callable(member) and name not in _UNCHECKED
        }
        handwritten = {name for name, _args in _HANDWRITTEN}
        assert generated == {*_GENERATED, *handwritten, "__init__"}

    def test_dir_answers_after_expiry_the_way_repr_does(self, expired: Everything) -> None:
        assert "value" in dir(expired)

    def test_attribute_reads_writes_and_deletes_all_raise(self, expired: Everything) -> None:
        with pytest.raises(ExpiredScopeError, match=r"Everything\.value was used after"):
            _ = expired.value
        with pytest.raises(ExpiredScopeError, match=r"Everything\.value was used after"):
            expired.value = 1
        with pytest.raises(ExpiredScopeError, match=r"Everything\.value was used after"):
            del expired.value

    def test_the_operators_raise_through_their_own_syntax(self, expired: Everything) -> None:
        proxy: Any = expired
        with pytest.raises(ExpiredScopeError):
            len(proxy)
        with pytest.raises(ExpiredScopeError):
            _ = proxy["a"]
        with pytest.raises(ExpiredScopeError):
            proxy["a"] = 1
        with pytest.raises(ExpiredScopeError):
            _ = proxy + 1
        with pytest.raises(ExpiredScopeError):
            _ = 1 + proxy
        with pytest.raises(ExpiredScopeError):
            proxy += 1
        with pytest.raises(ExpiredScopeError):
            str(proxy)

    def test_a_context_manager_cannot_be_entered_afterwards(self) -> None:
        with provider(Guard(), sealed=True) as guard:
            pass
        with pytest.raises(ExpiredScopeError, match=r"Guard\.__enter__ was used after"):
            with guard:
                pass
