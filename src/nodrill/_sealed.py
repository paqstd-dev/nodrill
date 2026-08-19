"""The view stored and yielded for values provided with sealed=True.

A sealed provider hands out a proxy that stops working when its block exits,
so a value captured by a closure, a callback or a background task says so at
the moment it is touched rather than failing later somewhere else.

The liveness check sits in front of the delegation in every generated method,
which is why the protocol tables in _views are the coverage guarantee here as
they are for the other two views.  Freezing decides what a consumer may do and
sealing decides when, so the two compose by nesting rather than by growing
flags on one class, and the seal is the outer one.

The generators are written again rather than shared with the other views,
because each one needs the operation's own name to report and a check in front
of the delegation, so what would be shared is the one line that differs.

An in-place operator has a generator of its own, since operator.iadd hands
back the target and the caller's name would be rebound to the value the seal
was covering.  The lazy cell has the same generator for the same reason, or a
seal over one would be rebound to the resolved value instead.

__class__, __repr__ and __dir__ are the three members that answer after
expiry, none of which hands back the target, since a debugging session that
cannot ask what an object is has lost more than the bug is worth.
"""

from __future__ import annotations

import operator
from collections.abc import Callable
from typing import Any, SupportsIndex, cast

from ._debug import _Site, _user_site
from ._errors import ExpiredScopeError, _at_site, _describe_key, _Key
from ._views import _FORWARDED, _INPLACE, _INVOKED, _ITEM_WRITES, _REFLECTED, _View

_UNCOPYABLE = "sealed context views cannot be pickled or copied"

# Generated rather than written out, since these three are off the hot path and report
# themselves, unlike the attribute members below which report the name they were given.
_COMPARED: dict[str, Callable[..., Any]] = {
    "__eq__": operator.eq,
    "__ne__": operator.ne,
    "__hash__": hash,
}


class _Scope:
    """One entry of a sealed provider, live until that block exits.

    Holds the sites rather than the value, so what the error names outlives
    the block and what the block provided does not.
    """

    __slots__ = ("exited", "key", "opened")

    def __init__(self, key: _Key, opened: _Site) -> None:
        self.key = key
        self.opened = opened
        self.exited: _Site | None = None


def _expired(scope: _Scope, operation: str) -> ExpiredScopeError:
    """Build the error for an operation that arrived after the block exited.

    The use site is read here, at the raise, since this frame is the one the
    escaped reference was touched from.
    """
    # Cast rather than a branch, since every caller has already found this set.
    exited = cast("_Site", scope.exited)
    return ExpiredScopeError(
        scope.key, operation, opened=scope.opened, exited=exited, used=_user_site()[0]
    )


def _sealed_views(value: Any, public: Any, scope: _Scope) -> tuple[Any, Any]:
    """Return what the block and the registry get for one entry of a sealed provider.

    One view where those are the same object, so a sealed provider keeps
    use(key) is the yielded value true as an unsealed one does.
    """
    sealed = _SealedProxy(value, scope)
    return sealed, sealed if public is value else _SealedProxy(public, scope)


class _SealedProxy(_View):
    """View over an object provided with sealed=True.

    Every operation checks the scope and then delegates, so the value behaves
    exactly as itself until the block exits and raises everywhere afterwards.
    Sealing is shallow, an attribute handed out separately stays live, and
    the proxy holds its target for as long as anything holds the proxy, so
    this reports an escape rather than preventing one.
    """

    __slots__ = ("_nodrill_scope", "_nodrill_target")

    # Declared rather than assigned, since no checker follows a slot through object.__setattr__.
    _nodrill_scope: _Scope
    _nodrill_target: Any

    def __init__(self, target: Any, scope: _Scope) -> None:
        # Through object, because __setattr__ below belongs to the target rather than to self.
        object.__setattr__(self, "_nodrill_target", target)
        object.__setattr__(self, "_nodrill_scope", scope)

    @property  # type: ignore[misc]  # the spoof is the point
    def __class__(self) -> type[Any]:  # pyright: ignore[reportIncompatibleMethodOverride]
        # The target's own answer, so the spoof composes over a frozen view or a lazy cell.
        answer: type[Any] = self._nodrill_target.__class__
        return answer

    # Written out rather than generated, since a generator taking the name through *args
    # costs half as much again on the operation a sealed value is read through most.
    def __getattr__(self, name: str) -> Any:
        scope = self._nodrill_scope
        if scope.exited is not None:
            raise _expired(scope, name)
        return getattr(self._nodrill_target, name)

    def __setattr__(self, name: str, value: Any) -> None:
        scope = self._nodrill_scope
        if scope.exited is not None:
            raise _expired(scope, name)
        setattr(self._nodrill_target, name, value)

    def __delattr__(self, name: str) -> None:
        scope = self._nodrill_scope
        if scope.exited is not None:
            raise _expired(scope, name)
        delattr(self._nodrill_target, name)

    def __dir__(self) -> list[str]:
        # Unchecked, since dir() is what a debugger calls and it hands back names.
        return dir(self._nodrill_target)

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        raise TypeError(_UNCOPYABLE)

    # On the class, since copy looks these up on the instance and __getattr__ would
    # hand back the target's own hook.
    def __copy__(self) -> Any:
        raise TypeError(_UNCOPYABLE)

    def __deepcopy__(self, memo: dict[int, Any]) -> Any:
        raise TypeError(_UNCOPYABLE)

    def __repr__(self) -> str:
        # State rather than a raise, since a repr that blows up makes the session worse.
        scope = self._nodrill_scope
        exited = scope.exited
        if exited is None:
            return f"<sealed {self._nodrill_target!r}>"
        return (
            f"<expired {_describe_key(scope.key)}, opened at {_at_site(scope.opened)}, "
            f"exited at {_at_site(exited)}>"
        )


def _make_forward(name: str, op: Callable[..., Any]) -> Callable[..., Any]:
    def method(self: _SealedProxy, *args: Any, **kwargs: Any) -> Any:
        scope = self._nodrill_scope
        if scope.exited is not None:
            raise _expired(scope, name)
        return op(self._nodrill_target, *args, **kwargs)

    return method


def _make_inplace(name: str, op: Callable[[Any, Any], Any]) -> Callable[..., Any]:
    def method(self: _SealedProxy, other: Any) -> Any:
        scope = self._nodrill_scope
        if scope.exited is not None:
            raise _expired(scope, name)
        target = self._nodrill_target
        result = op(target, other)
        # This view again where the target mutated itself, so `x += y` cannot unseal x.
        return self if result is target else result

    return method


def _make_reflected(name: str, op: Callable[[Any, Any], Any]) -> Callable[..., Any]:
    def method(self: _SealedProxy, other: Any) -> Any:
        scope = self._nodrill_scope
        if scope.exited is not None:
            raise _expired(scope, name)
        return op(other, self._nodrill_target)

    return method


def _make_invoked(name: str) -> Callable[..., Any]:
    def method(self: _SealedProxy, *args: Any, **kwargs: Any) -> Any:
        scope = self._nodrill_scope
        if scope.exited is not None:
            raise _expired(scope, name)
        return getattr(self._nodrill_target, name)(*args, **kwargs)

    return method


# The item writes as well, since sealing restricts when rather than what.
for _name, _op in {**_FORWARDED, **_ITEM_WRITES, **_COMPARED}.items():
    setattr(_SealedProxy, _name, _make_forward(_name, _op))
for _name, _op in _INPLACE.items():
    setattr(_SealedProxy, _name, _make_inplace(_name, _op))
for _name, _op in _REFLECTED.items():
    setattr(_SealedProxy, _name, _make_reflected(_name, _op))
for _name in _INVOKED:
    setattr(_SealedProxy, _name, _make_invoked(_name))


__all__ = ["_Scope", "_SealedProxy", "_sealed_views"]
