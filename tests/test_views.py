"""Every entry in the _views tables, through each view, against the value's own answer."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any

import pytest

from nodrill import lazy, provider, use
from nodrill._frozen import _FrozenProxy
from nodrill._lazy import _LazyCell
from nodrill._sealed import _SealedProxy
from nodrill._views import _FORWARDED, _INPLACE, _INVOKED, _ITEM_WRITES, _REFLECTED

Make = Callable[[], Any]
Row = tuple[str, Make, tuple[Any, ...]]
ViewOf = Callable[[Any], AbstractContextManager[Any]]


class Widget:
    """The protocols no builtin answers, and the three an int answers indistinguishably."""

    def __bytes__(self) -> bytes:
        return b"widget"

    def __complex__(self) -> complex:
        return complex(1, 2)

    # Distinctive answers, since an int would floor to itself whichever operation ran.
    def __floor__(self) -> str:
        return "floored"

    def __ceil__(self) -> str:
        return "ceiled"

    def __trunc__(self) -> str:
        return "truncated"

    def __call__(self, arg: int) -> str:
        return f"called {arg}"

    def __matmul__(self, other: str) -> str:
        return f"matmul {other}"

    def __rmatmul__(self, other: str) -> str:
        return f"rmatmul {other}"

    # A fresh value rather than self, so the row can compare against __matmul__.
    def __imatmul__(self, other: str) -> str:
        return f"matmul {other}"


def _seven() -> int:
    return 7


def _pair() -> list[int]:
    return [1, 2]


def _rows(names: tuple[str, ...], make: Make, args: tuple[Any, ...]) -> tuple[Row, ...]:
    """Pair each name with the target that answers it and the arguments it takes."""
    return tuple((name, make, args) for name in names)


_ON_SEVEN_ALONE = (
    "__str__",
    "__bool__",
    "__int__",
    "__float__",
    "__index__",
    "__round__",
    "__abs__",
    "__neg__",
    "__pos__",
    "__invert__",
)

# Base 7 against an operand of 2, so a table remapped to a neighbouring operator is caught.
_ON_SEVEN_WITH_AN_OPERAND = (
    "__lt__",
    "__le__",
    "__gt__",
    "__ge__",
    "__add__",
    "__sub__",
    "__mul__",
    "__truediv__",
    "__floordiv__",
    "__mod__",
    "__divmod__",
    "__pow__",
    "__lshift__",
    "__rshift__",
    "__and__",
    "__or__",
    "__xor__",
    "__radd__",
    "__rsub__",
    "__rmul__",
    "__rtruediv__",
    "__rfloordiv__",
    "__rmod__",
    "__rdivmod__",
    "__rpow__",
    "__rlshift__",
    "__rrshift__",
    "__rand__",
    "__ror__",
    "__rxor__",
)

_OVER_A_LIST = ("__len__", "__iter__", "__reversed__")

_READS: tuple[Row, ...] = (
    *_rows(_ON_SEVEN_ALONE, _seven, ()),
    *_rows(_ON_SEVEN_WITH_AN_OPERAND, _seven, (2,)),
    *_rows(_OVER_A_LIST, _pair, ()),
    *_rows(("__bytes__", "__complex__", "__floor__", "__ceil__", "__trunc__"), Widget, ()),
    ("__format__", _seven, ("03d",)),
    ("__contains__", _pair, (1,)),
    ("__getitem__", _pair, (0,)),
    ("__next__", lambda: iter([1]), ()),
    ("__call__", Widget, (2,)),
    ("__matmul__", Widget, ("x",)),
    ("__rmatmul__", Widget, ("x",)),
)

_WRITES: tuple[tuple[str, tuple[Any, ...]], ...] = (
    ("__setitem__", (0, 9)),
    ("__delitem__", (0,)),
)


@contextmanager
def _frozen_view(value: Any) -> Iterator[Any]:
    with provider(value, frozen=True):
        yield use(type(value))


@contextmanager
def _lazy_view(value: Any) -> Iterator[Any]:
    with provider(lazy(type(value), lambda: value)):
        yield use(type(value))


@contextmanager
def _sealed_view(value: Any) -> Iterator[Any]:
    with provider(value, sealed=True):
        yield use(type(value))


_VIEWS: tuple[ViewOf, ...] = (_frozen_view, _lazy_view, _sealed_view)
_VIEW_IDS = ("frozen", "lazy", "sealed")

# Frozen refuses the writes the other two forward, which its own tests cover.
_FORWARDING_VIEWS: tuple[ViewOf, ...] = (_lazy_view, _sealed_view)
_FORWARDING_IDS = ("lazy", "sealed")


def _settled(result: Any) -> Any:
    """Return something comparable, since two iterators over the same items never are."""
    return list(result) if isinstance(result, Iterator) else result


def _through(value: Any, name: str, args: tuple[Any, ...]) -> Any:
    """Call one protocol the way Python does, on the type rather than the instance."""
    return getattr(type(value), name)(value, *args)


class TestTheTablesAreCovered:
    def test_every_read_has_a_row(self) -> None:
        assert {name for name, _make, _args in _READS} == {*_FORWARDED, *_REFLECTED}

    def test_every_item_write_has_a_row(self) -> None:
        assert {name for name, _args in _WRITES} == set(_ITEM_WRITES)

    @pytest.mark.parametrize(
        ("view", "expected"),
        [
            pytest.param(_FrozenProxy, {*_FORWARDED, *_REFLECTED, *_INVOKED}, id="frozen"),
            pytest.param(
                _LazyCell,
                {*_FORWARDED, *_REFLECTED, *_INVOKED, *_ITEM_WRITES, *_INPLACE},
                id="lazy",
            ),
            pytest.param(
                _SealedProxy,
                {*_FORWARDED, *_REFLECTED, *_INVOKED, *_ITEM_WRITES, *_INPLACE},
                id="sealed",
            ),
        ],
    )
    def test_every_entry_reached_the_class(self, view: type[Any], expected: set[str]) -> None:
        # On the class, since a special method is looked up there and never through __getattr__.
        assert expected <= set(vars(view))


@pytest.mark.parametrize("view_of", _VIEWS, ids=_VIEW_IDS)
@pytest.mark.parametrize(
    ("name", "make", "args"), _READS, ids=[name for name, _make, _args in _READS]
)
class TestReadsMatchTheValue:
    def test_the_view_answers_what_the_value_answers(
        self, view_of: ViewOf, name: str, make: Make, args: tuple[Any, ...]
    ) -> None:
        expected = _settled(_through(make(), name, args))
        with view_of(make()) as view:
            assert _settled(_through(view, name, args)) == expected


@pytest.mark.parametrize("view_of", _FORWARDING_VIEWS, ids=_FORWARDING_IDS)
class TestWritesMatchTheValue:
    @pytest.mark.parametrize(("name", "args"), _WRITES, ids=[name for name, _args in _WRITES])
    def test_an_item_write_lands_where_it_would_on_the_value(
        self, view_of: ViewOf, name: str, args: tuple[Any, ...]
    ) -> None:
        expected = _pair()
        getattr(type(expected), name)(expected, *args)
        target = _pair()
        with view_of(target) as view:
            getattr(type(view), name)(view, *args)
        assert target == expected

    @pytest.mark.parametrize("name", _INPLACE)
    def test_an_in_place_operator_applies_its_own_operation(
        self, view_of: ViewOf, name: str
    ) -> None:
        # The operator without the i, which is what the in-place form falls back to here.
        plain = name.replace("__i", "__", 1)
        value, operand = (Widget(), "x") if name == "__imatmul__" else (7, 2)
        expected = _through(value, plain, (operand,))
        with view_of(value) as view:
            assert _through(view, name, (operand,)) == expected
