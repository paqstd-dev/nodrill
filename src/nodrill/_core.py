"""Provider registry and lookup.

A single ContextVar holds the registry mapping.  Providers swap in an
updated copy on enter and reset through the token on exit, so parents and
sibling tasks never observe another scope's keys.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from types import MappingProxyType, TracebackType
from typing import TYPE_CHECKING, Any, Generic, TypeVar, overload

from ._ambient import _ambient
from ._errors import NoProviderError
from ._frozen import _FrozenProxy

T = TypeVar("T")
D = TypeVar("D")

# Shared as a default and never mutated, since every write path copies first.
_EMPTY_REGISTRY: dict[str | type[Any], Any] = {}
_registry: ContextVar[dict[str | type[Any], Any]] = ContextVar(
    "nodrill_registry", default=_EMPTY_REGISTRY
)

# Configuration rather than per-context state, so deliberately not a ContextVar.
_defaults: dict[type[Any], Callable[[], Any]] = {}

_MISSING = object()

# A tuple rather than `str | type`, which would allocate a UnionType on every evaluation.
_KEY_TYPES = (str, type)


class Namespace:
    """Mutable attribute bag yielded by string-named providers."""

    # A mangled slot keeps the provider name out of __dict__, where a value could collide.
    __slots__ = ("__dict__", "__label")

    def __init__(self, /, **values: Any) -> None:
        self.__label: str | None = None
        self.__dict__.update(values)

    @classmethod
    def _named(cls, label: str, values: dict[str, Any]) -> Namespace:
        """Build a namespace that knows which provider created it."""
        namespace = cls(**values)
        namespace.__label = label
        return namespace

    if TYPE_CHECKING:
        # Defining it for real would route every write through Python instead of object's slot.
        def __setattr__(self, name: str, value: Any) -> None: ...

    def __getattr__(self, name: str) -> Any:
        label = self.__label
        where = f"namespace {label!r}" if label else "context namespace"
        available = ", ".join(sorted(self.__dict__)) or "none"
        raise AttributeError(f"{where} has no attribute {name!r} (available: {available})")

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Namespace):
            return self.__dict__ == other.__dict__
        return NotImplemented

    # Value equality on a mutable bag means no hash, as for types.SimpleNamespace.
    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        inner = ", ".join(f"{k}={v!r}" for k, v in sorted(self.__dict__.items()))
        label = self.__label
        if label:
            return f"Namespace({label!r}, {inner})" if inner else f"Namespace({label!r})"
        return f"Namespace({inner})"


class _Provider(Generic[T]):
    """Context manager returned by provider().

    Reusable sequentially, not re-entrant while active.
    """

    __slots__ = ("_key", "_public", "_token", "_value")

    def __init__(self, key: str | type[Any], value: T, public: Any) -> None:
        self._key = key
        self._value = value
        self._public = public
        self._token: Token[dict[str | type[Any], Any]] | None = None

    def __enter__(self) -> T:
        if self._token is not None:
            raise RuntimeError(
                "this provider is already active. Create a separate provider() "
                "for nested or concurrent with blocks"
            )
        updated = dict(_registry.get())
        updated[self._key] = self._public
        self._token = _registry.set(updated)
        return self._value

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        token, self._token = self._token, None
        if token is not None:
            _registry.reset(token)

    # Nothing awaits, but the protocol lets async code spell `async with provider(...)`.
    async def __aenter__(self) -> T:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.__exit__(exc_type, exc, tb)


@overload
def provider(name: str, /, *, frozen: bool = ..., **values: Any) -> _Provider[Namespace]: ...
@overload
def provider(instance: T, /, *, key: str | type[Any] = ..., frozen: bool = ...) -> _Provider[T]: ...
@overload
def provider(*, name: str, frozen: bool = ..., **values: Any) -> _Provider[Namespace]: ...
def provider(
    *args: Any, key: str | type[Any] | None = None, frozen: bool = False, **values: Any
) -> _Provider[Any]:
    """Make a value available to the whole call subtree through use().

    provider("app") yields a fresh Namespace registered under the name,
    prefilled from keyword arguments.  provider(instance) registers the
    instance under its class for a typed use(type(instance)) lookup, or under
    key= if one is given.  Same-key providers shadow outer ones, and the
    outer value is restored on exit even if the block raises.  With frozen=True,
    consumers get a read-only view while the yielded object stays writable.
    """
    if len(args) > 1:
        raise TypeError(f"provider() takes a single target, got {len(args)} positional arguments")
    if args:
        target: Any = args[0]
    else:
        # With a positional target, a name= keyword is prefill data rather than the key.
        try:
            target = values.pop("name")
        except KeyError:
            raise TypeError(
                "provider() needs a target: provider('name'), provider(name='name'), "
                "or provider(instance)"
            ) from None
        if not isinstance(target, str):
            raise TypeError(
                "provider(name=...) expects a string name. Pass instances positionally, as "
                "provider(instance)"
            )
    if isinstance(target, str):
        if key is not None:
            raise TypeError(
                "provider(key=...) applies to instance providers. A string-named "
                "provider is already registered under its name"
            )
        registered: str | type[Any] = target
        value: Any = Namespace._named(target, values)  # noqa: SLF001
    else:
        if values:
            raise TypeError(
                "keyword values are only supported for string-named providers: "
                "provider('app', db=...)"
            )
        registered = _instance_key(target, key)
        value = target
    public = _FrozenProxy(value) if frozen else value
    return _Provider(registered, value, public)


def _instance_key(target: Any, key: str | type[Any] | None) -> str | type[Any]:
    """Return the registry key for an instance provider."""
    if isinstance(target, type):
        raise TypeError(
            f"provider() takes an instance, not a class. "
            f"Did you mean provider({target.__name__}(...))?"
        )
    if key is None:
        return type(target)
    if isinstance(key, _KEY_TYPES):
        return key
    raise TypeError(f"provider(key=...) expects a string name or a class, got {type(key).__name__}")


@overload
def use(key: type[T]) -> T: ...
@overload
def use(key: str) -> Namespace: ...
@overload
def use(key: type[T], *, default: D) -> T | D: ...
@overload
def use(key: str, *, default: D) -> Namespace | D: ...
def use(key: Any, *, default: Any = _MISSING) -> Any:
    """Return the value provided for key by the nearest enclosing provider.

    String keys return the provider's Namespace, class keys the provided
    instance typed as that class.  A miss tries a set_default() factory,
    then the default argument, then raises NoProviderError.
    """
    registry = _registry.get()
    try:
        return registry[key]
    except KeyError:
        pass
    except TypeError:
        # An unhashable key lands here and gets the same message below as any other wrong kind.
        pass
    if not isinstance(key, _KEY_TYPES):
        raise TypeError(
            f"use() expects a string name or a class, got {type(key).__name__}: {key!r}"
        )
    if isinstance(key, type):
        factory = _defaults.get(key)
        if factory is not None:
            return factory()
    if default is not _MISSING:
        return default
    raise NoProviderError(key, registry.keys())


def active() -> Mapping[str | type[Any], Any]:
    """Return a read-only view of the providers active right now.

    Keyed exactly as use() looks values up, for debugging and test
    assertions.  The view is a snapshot and does not track later scopes.
    """
    return MappingProxyType(_registry.get())


def set_default(cls: type[T], factory: Callable[[], T] | None) -> type[T]:
    """Register a fallback factory for use(cls) outside any provider.

    The factory runs on every miss (a fresh instance each time, never a
    cached singleton).  None removes the registration.
    """
    if not isinstance(cls, type):
        raise TypeError(f"set_default() registers classes, got {type(cls).__name__}: {cls!r}")
    if factory is None:
        _defaults.pop(cls, None)
    elif callable(factory):
        _defaults[cls] = factory
    else:
        raise TypeError(f"factory must be callable or None, got {type(factory).__name__}")
    return cls


@contextmanager
def isolate() -> Iterator[None]:
    """Run a block against fresh context state, restoring the outer state on exit.

    Providers and ambient attributes start empty.  Any set_default()
    registration made inside is rolled back.  Meant for test fixtures.
    """
    registry_token = _registry.set({})
    ambient_token = _ambient.set({})
    saved_defaults = dict(_defaults)
    try:
        yield
    finally:
        _defaults.clear()
        _defaults.update(saved_defaults)
        _ambient.reset(ambient_token)
        _registry.reset(registry_token)


__all__ = [
    "Namespace",
    "active",
    "isolate",
    "provider",
    "set_default",
    "use",
]
