"""The lazy() carrier, the build it becomes, and the views over that build.

The registry holds a view rather than the value, so use() stays one dict read
and a scope nothing reads never builds anything.  The first operation that
needs the value runs the factory under a lock and caches the result, or the
exception, for the life of the scope.

The build is a _Resolution and the proxies are _LazyCells over it, because a
frozen provider needs two views of one result, the writable handle the block
keeps and the read-only one its callees get.

Special methods are looked up on the type, never through __getattr__, so every
protocol a view resolves through has to exist on the class.  The tables in
_frozen describe the same protocols and are reused verbatim.  The three
generators are written again rather than shared, because a shared one would
have to reach its target through a passed-in accessor, and that is a call
added to every operation on both proxies.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from contextvars import Context, copy_context
from typing import Any, SupportsIndex, TypeVar, cast

from ._errors import _describe_key
from ._frozen import _FrozenProxy
from ._refs import _key_target
from ._views import _FORWARDED, _INPLACE, _INVOKED, _ITEM_WRITES, _REFLECTED, _unwrapped

T = TypeVar("T")

# Distinct from None, which a factory may legitimately return.
_PENDING = object()


class _Lazy:
    """The inert key and factory that lazy() returns, which only provider() accepts.

    Kept apart from the views, which spoof __class__ and so cannot be
    recognised by an isinstance check.
    """

    __slots__ = ("factory", "key")

    def __init__(self, key: type[Any], factory: Callable[[], Any]) -> None:
        self.key = key
        self.factory = factory

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(
            f"cannot read {name!r} from what lazy() returned: it is a target for provider(), "
            f"not the value. Open it with provider(lazy(...)) and read it with use()"
        )

    def __repr__(self) -> str:
        return f"lazy({_describe_key(self.key)}, {self.factory!r})"


def _is_lazy(value: Any) -> bool:
    """Report whether value is what lazy() returned.

    Called rather than inlined, since against an annotated parameter a
    narrowing type checker reads such a guard as dead code.
    """
    return type(value) is _Lazy


def _self_reference_error(key: type[Any]) -> RuntimeError:
    """Build the error for a factory that depends on the key it is building."""
    name = _describe_key(key)
    return RuntimeError(
        f"lazy provider for {name}: the factory depends on use({name}), which is the value "
        f"it is building. A lazy factory cannot read or return its own key."
    )


class _Resolution:
    """The once-only build behind the views one lazy provider hands out.

    The factory runs under a snapshot of the scope that opened it, so use()
    inside a factory reads the scope that declared the value.
    """

    __slots__ = ("context", "error", "factory", "key", "lock", "owner", "value")

    def __init__(self, key: type[Any], factory: Callable[[], Any]) -> None:
        self.key = key
        self.factory = factory
        self.lock: AbstractContextManager[bool] = threading.RLock()
        self.owner: int | None = None
        self.value: Any = _PENDING
        self.error: BaseException | None = None
        # Replaced with the post-publish snapshot in _LazyProvider.__enter__.
        self.context: Context = copy_context()

    def resolve(self) -> Any:
        """Return the built value, running the factory on the first call."""
        value = self.value
        if value is _PENDING:
            return self.fill()
        return value

    def fill(self) -> Any:
        """Take the lock and finish what the fast path could not."""
        with self.lock:
            if self.error is None and self.value is _PENDING:
                self.build()
            error = self.error
            value = self.value
        if error is not None:
            raise error
        return value

    def build(self) -> None:
        """Run the factory once, caching the value or the exception it raised.

        Once-only is the lock's job.  The owner check covers what a reentrant
        lock lets through, a factory reading the key it is building.
        """
        thread = threading.get_ident()
        if self.owner == thread:
            raise _self_reference_error(self.key)
        self.owner = thread
        # Entered at most once, which is what lets one Context stand in for wrap()'s replay.
        try:
            built = self.context.run(self.factory)
        except Exception as exc:
            # Uncached, since a cancelled task says nothing about the factory.
            self.error = exc
            raise
        finally:
            self.owner = None
        if _own_view(built, self):
            # Otherwise the view becomes its own value and every later read recurses.
            error = _self_reference_error(self.key)
            self.error = error
            raise error
        self.value = built


class _LazyCell:
    """One view of a lazy provider's value, as the block or the registry sees it.

    Resolves on the first operation that needs the value and delegates
    everything afterwards, except repr(), which reports state instead so that
    printing active() has no side effect.  Freezing belongs to the view, which
    is how one build backs both a writable handle and a read-only one.
    """

    __slots__ = ("_nodrill_frozen", "_nodrill_state", "_nodrill_view")

    # Declared rather than assigned, since no checker follows a slot through object.__setattr__.
    _nodrill_frozen: bool
    _nodrill_state: _Resolution
    _nodrill_view: Any

    def __init__(self, state: _Resolution, *, frozen: bool) -> None:
        # Through object, because __setattr__ below belongs to the target rather than to self.
        object.__setattr__(self, "_nodrill_state", state)
        object.__setattr__(self, "_nodrill_frozen", frozen)
        object.__setattr__(self, "_nodrill_view", _PENDING)

    def _nodrill_resolve(self) -> Any:
        """Return what this view shows, building the value on the first call."""
        view = self._nodrill_view
        if view is _PENDING:
            return self._nodrill_fill()
        return view

    def _nodrill_fill(self) -> Any:
        """Take the shared result and keep this view's wrapping of it."""
        value = self._nodrill_state.resolve()
        view = _FrozenProxy(value) if self._nodrill_frozen else value
        object.__setattr__(self, "_nodrill_view", view)
        return view

    @property  # type: ignore[misc]  # the spoof is the point
    def __class__(self) -> type[Any]:  # pyright: ignore[reportIncompatibleMethodOverride]
        # The key is the promise the registry made, and the only answer available unresolved.
        state = self._nodrill_state
        if state.value is _PENDING:
            return state.key
        resolved: type[Any] = state.value.__class__
        return resolved

    def __getattr__(self, name: str) -> Any:
        return getattr(self._nodrill_resolve(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._nodrill_resolve(), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(self._nodrill_resolve(), name)

    def __eq__(self, other: object) -> Any:
        return self._nodrill_resolve() == other

    def __ne__(self, other: object) -> Any:
        return self._nodrill_resolve() != other

    def __hash__(self) -> int:
        return hash(self._nodrill_resolve())

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        return self._nodrill_resolve().__reduce_ex__(protocol)

    def __dir__(self) -> list[str]:
        return dir(self._nodrill_resolve())

    def __repr__(self) -> str:
        # Off the shared build, so both views of one provider read the same.
        state = self._nodrill_state
        name = _describe_key(state.key)
        if state.value is not _PENDING:
            return f"<lazy {name}, {state.value!r}>"
        if state.error is not None:
            return f"<lazy {name}, failed with {type(state.error).__name__}>"
        return f"<lazy {name}, unresolved>"


def _own_view(built: Any, state: _Resolution) -> bool:
    """Report whether the factory handed back the very value it is building.

    Through any view as well, since a sealed provider publishes one over the
    cell and the identity test would otherwise miss it and recurse.
    """
    built = _unwrapped(built)
    return type(built) is _LazyCell and built._nodrill_state is state  # noqa: SLF001


def _make_forward(op: Callable[..., Any]) -> Callable[..., Any]:
    def method(self: _LazyCell, *args: Any, **kwargs: Any) -> Any:
        return op(self._nodrill_resolve(), *args, **kwargs)

    return method


def _make_reflected(op: Callable[[Any, Any], Any]) -> Callable[..., Any]:
    def method(self: _LazyCell, other: Any) -> Any:
        return op(other, self._nodrill_resolve())

    return method


def _make_invoked(name: str) -> Callable[..., Any]:
    def method(self: _LazyCell, *args: Any, **kwargs: Any) -> Any:
        return getattr(self._nodrill_resolve(), name)(*args, **kwargs)

    return method


for _name, _op in {**_FORWARDED, **_ITEM_WRITES, **_INPLACE}.items():
    setattr(_LazyCell, _name, _make_forward(_op))
for _name, _op in _REFLECTED.items():
    setattr(_LazyCell, _name, _make_reflected(_op))
for _name in _INVOKED:
    setattr(_LazyCell, _name, _make_invoked(_name))


def lazy(key: type[Any], factory: Callable[[], T], /) -> T:
    """Defer building a provided value until something below reads it.

    Pass the result to provider().  The factory runs on the first read inside
    the scope, under the context that scope was entered with, and its result
    is cached until the scope exits.  The key is given explicitly, since
    there is no value to derive it from yet.  A ref() resolves here, so the
    carrier holds the class the provider will register.
    """
    key = _key_target(key)
    if isinstance(key, str):
        raise TypeError(
            "lazy() takes a class as its key. A string-named provider fills its Namespace "
            "from the keyword values passed at the call, so there is nothing to defer"
        )
    if not isinstance(key, type):
        raise TypeError(f"lazy() expects a class as its key, got {type(key).__name__}: {key!r}")
    if callable(factory):
        # The view answers isinstance and every read as the value, so that is how it types.
        return cast("T", _Lazy(key, factory))
    raise TypeError(f"lazy() factory must be callable, got {type(factory).__name__}")


__all__ = ["lazy"]
