"""The read-only view stored for values provided with frozen=True.

Reads and the tabled protocols delegate to the target while writes raise, so
the block keeps a writable handle and its callees do not.  The protocols come
from the tables in _views, and _BLOCKED below is the one table this view owns,
being the writes it refuses where another view forwards them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, SupportsIndex

from ._errors import FrozenContextError
from ._views import _FORWARDED, _INVOKED, _REFLECTED, _View

# Absent in-place operators make `+=` fall back to __add__, which leaves the target alone.
_BLOCKED = {
    "__setitem__": "cannot set item {0!r}",
    "__delitem__": "cannot delete item {0!r}",
}

_UNCOPYABLE = "frozen context views cannot be pickled or copied"


class _FrozenProxy(_View):
    """Read-only view over an object provided with frozen=True.

    Reads and the tabled protocols delegate to the target, writes raise, and
    __class__ is spoofed so isinstance holds.  Freezing is shallow, since a
    mutable object reached through the target can still be mutated.  This is
    a guard rail against accidental writes, not a security boundary.
    """

    __slots__ = ("_nodrill_target",)

    def __init__(self, target: Any) -> None:
        object.__setattr__(self, "_nodrill_target", target)

    @property  # type: ignore[misc]  # intentionally read-only
    def __class__(self) -> type[Any]:  # pyright: ignore[reportIncompatibleMethodOverride]
        # The target's own answer, so a view over another view reports what is under both.
        answer: type[Any] = self._nodrill_target.__class__
        return answer

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
        raise TypeError(_UNCOPYABLE)

    # On the class, since copy looks these up on the instance and __getattr__ would
    # hand back the target's own hook.
    def __copy__(self) -> Any:
        raise TypeError(_UNCOPYABLE)

    def __deepcopy__(self, memo: dict[int, Any]) -> Any:
        raise TypeError(_UNCOPYABLE)

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
