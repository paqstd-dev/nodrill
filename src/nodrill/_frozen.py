"""The read-only view stored for values provided with frozen=True.

Special methods are looked up on the type, never through __getattr__, so every
protocol the proxy forwards has to exist on the class.  The tables below are
that list, and the methods are generated from them below the class body.
"""

from __future__ import annotations

import operator
from collections.abc import Callable
from typing import Any, SupportsIndex

from ._errors import FrozenContextError


def _call(target: Any, *args: Any, **kwargs: Any) -> Any:
    return target(*args, **kwargs)


# Each dunder mapped to the operation it performs on the target.
_FORWARDED: dict[str, Callable[..., Any]] = {
    "__str__": str,
    "__bytes__": bytes,
    "__format__": format,
    "__bool__": bool,
    "__int__": int,
    "__float__": float,
    "__complex__": complex,
    "__index__": operator.index,
    "__round__": round,
    "__abs__": abs,
    "__neg__": operator.neg,
    "__pos__": operator.pos,
    "__invert__": operator.invert,
    "__len__": len,
    "__iter__": iter,
    "__reversed__": reversed,
    "__next__": next,
    "__contains__": operator.contains,
    "__getitem__": operator.getitem,
    "__call__": _call,
    # == and != are written out in the class body so a target's own answer
    # passes through instead of being coerced to bool.
    "__lt__": operator.lt,
    "__le__": operator.le,
    "__gt__": operator.gt,
    "__ge__": operator.ge,
    "__add__": operator.add,
    "__sub__": operator.sub,
    "__mul__": operator.mul,
    "__matmul__": operator.matmul,
    "__truediv__": operator.truediv,
    "__floordiv__": operator.floordiv,
    "__mod__": operator.mod,
    "__divmod__": divmod,
    "__pow__": pow,
    "__lshift__": operator.lshift,
    "__rshift__": operator.rshift,
    "__and__": operator.and_,
    "__or__": operator.or_,
    "__xor__": operator.xor,
}

# Reflected arithmetic: __radd__(self, other) means other + target, so the
# same operation runs with the arguments the other way round.
_REFLECTED: dict[str, Callable[[Any, Any], Any]] = {
    "__radd__": operator.add,
    "__rsub__": operator.sub,
    "__rmul__": operator.mul,
    "__rmatmul__": operator.matmul,
    "__rtruediv__": operator.truediv,
    "__rfloordiv__": operator.floordiv,
    "__rmod__": operator.mod,
    "__rdivmod__": divmod,
    "__rpow__": pow,
    "__rlshift__": operator.lshift,
    "__rrshift__": operator.rshift,
    "__rand__": operator.and_,
    "__ror__": operator.or_,
    "__rxor__": operator.xor,
}

# Protocols whose dunder is simply called on the target.  The async ones hand
# back the target's own awaitable or iterator for the caller to drive.
_INVOKED = (
    "__enter__",
    "__exit__",
    "__aenter__",
    "__aexit__",
    "__await__",
    "__aiter__",
    "__anext__",
)

# Mutating item protocols; attribute writes are refused in the class body.
# In-place operators are deliberately absent from every table: with no
# __iadd__ on the proxy, `frozen += x` falls back to __add__ and rebinds the
# caller's name, which already leaves the target alone.
_BLOCKED = {
    "__setitem__": "cannot set item {0!r}",
    "__delitem__": "cannot delete item {0!r}",
}


class _FrozenProxy:
    """Read-only view over an object provided with frozen=True.

    Reads and the tabled protocols delegate to the target, writes raise, and
    __class__ is spoofed so isinstance holds.  Freezing is shallow: a mutable
    object reached through the target can still be mutated, so this is a guard
    rail against accidental writes, not a security boundary.
    """

    __slots__ = ("_nodrill_target",)

    def __init__(self, target: Any) -> None:
        object.__setattr__(self, "_nodrill_target", target)

    @property  # type: ignore[misc]  # intentionally read-only
    def __class__(self) -> type[Any]:  # pyright: ignore[reportIncompatibleMethodOverride]
        return type(self._nodrill_target)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._nodrill_target, name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise FrozenContextError(
            f"cannot set {name!r}: this context was provided with frozen=True, so "
            "consumers get a read-only view. Mutate the object yielded by "
            "the provider block instead."
        )

    def __delattr__(self, name: str) -> None:
        raise FrozenContextError(
            f"cannot delete {name!r}: this context was provided with frozen=True."
        )

    def __eq__(self, other: object) -> Any:
        return self._nodrill_target == other

    def __ne__(self, other: object) -> Any:
        return self._nodrill_target != other

    def __hash__(self) -> int:
        return hash(self._nodrill_target)

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        raise TypeError("frozen context views cannot be pickled or copied")

    def __dir__(self) -> list[str]:
        return dir(self._nodrill_target)

    def __repr__(self) -> str:
        return f"<frozen {self._nodrill_target!r}>"


def _make_forward(op: Callable[..., Any]) -> Callable[..., Any]:
    def method(self: _FrozenProxy, *args: Any, **kwargs: Any) -> Any:
        return op(self._nodrill_target, *args, **kwargs)

    return method


def _make_reflected(op: Callable[[Any, Any], Any]) -> Callable[..., Any]:
    def method(self: _FrozenProxy, other: Any) -> Any:
        return op(other, self._nodrill_target)

    return method


def _make_invoked(name: str) -> Callable[..., Any]:
    def method(self: _FrozenProxy, *args: Any, **kwargs: Any) -> Any:
        return getattr(self._nodrill_target, name)(*args, **kwargs)

    return method


def _make_blocked(template: str) -> Callable[..., Any]:
    def method(_self: _FrozenProxy, *args: Any) -> Any:
        raise FrozenContextError(
            f"{template.format(*args)}: this context was provided with frozen=True. "
            "Mutate the object yielded by the provider block instead."
        )

    return method


for _name, _op in _FORWARDED.items():
    setattr(_FrozenProxy, _name, _make_forward(_op))
for _name, _op in _REFLECTED.items():
    setattr(_FrozenProxy, _name, _make_reflected(_op))
for _name in _INVOKED:
    setattr(_FrozenProxy, _name, _make_invoked(_name))
for _name, _template in _BLOCKED.items():
    setattr(_FrozenProxy, _name, _make_blocked(_template))


__all__ = ["_FrozenProxy"]
