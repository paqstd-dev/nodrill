"""Provider registry and lookup.

A single ContextVar holds the registry mapping.  Providers swap in an
updated copy on enter and reset through the token on exit, so parents and
sibling tasks never observe another scope's keys.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token, copy_context
from types import MappingProxyType, TracebackType
from typing import TYPE_CHECKING, Any, ClassVar, Generic, TypeVar, overload

from ._ambient import _ambient
from ._debug import _diagnose, _record_enter, _record_exit, _recount, _user_site
from ._debug import _state as _debug_state
from ._declare import _expected_at, _fired, _note_fallback, _pending
from ._declare import _restore as _restore_declared
from ._declare import _snapshot as _snapshot_declared
from ._errors import _KEY_TYPES, NoProviderError, OrphanedProviderWarning, _describe_key
from ._frozen import _FrozenProxy
from ._lazy import _is_lazy, _Lazy, _LazyCell, _Resolution
from ._refs import _is_ref, _key_target, _restore, _snapshot
from ._report import _annotate
from ._sealed import _Scope, _sealed_views

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


class _Open:
    """The key the open-block chain rides under, oldest first, for a block closing out of order.

    The chain lives in the mapping itself rather than in a second ContextVar,
    so it copies with the registry and entering a block stays one ContextVar
    operation.  A class, so the registry keeps holding nothing but str and
    type keys, and private, so no lookup can name it.  The two places that
    iterate the registry filter it out.
    """


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
    __hash__: ClassVar[None] = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        inner = ", ".join(f"{k}={v!r}" for k, v in sorted(self.__dict__.items()))
        label = self.__label
        if label:
            return f"Namespace({label!r}, {inner})" if inner else f"Namespace({label!r})"
        return f"Namespace({inner})"


def _repaired(
    chain: tuple[_Provider[Any], ...],
    leaving: _Provider[Any],
    current: dict[str | type[Any], Any],
) -> dict[str | type[Any], Any]:
    """Return the current mapping with the block closing out of order taken out.

    Resetting through the token would reinstate a snapshot taken before the
    others opened, which is how a closed block's value survives a sibling.
    The current mapping is right for every key but the leaving block's, so
    only that one is restored, to the innermost still-open block providing it.
    """
    repaired = dict(current)
    key = leaving._key  # noqa: SLF001
    # Innermost first, so the first block still open under the key is the one that owns it now.
    for open_block in reversed(chain):
        entered = open_block._entered  # noqa: SLF001
        if open_block is not leaving and entered is not None and open_block._key == key:  # noqa: SLF001
            repaired[key] = entered[key]
            break
    else:
        repaired.pop(key, None)
    repaired[_Open] = tuple(block for block in chain if block is not leaving)
    return _recount(repaired, current)


class _Provider(Generic[T]):
    """Context manager returned by provider().

    Reusable sequentially, not re-entrant while active.
    """

    __slots__ = (
        "_annotate",
        "_block",
        "_entered",
        "_key",
        "_public",
        "_scope",
        "_token",
        "_value",
    )

    # A class attribute rather than a field, so an ordinary provider stores nothing for it.
    _sealed = False

    # Declared rather than assigned, since only a sealed provider ever has one to store.
    _scope: _Scope

    def __init__(
        self,
        key: str | type[Any],
        value: T,
        public: Any,
        # Positional, since passing this by keyword costs more per block than the feature does.
        annotate: bool | None,  # noqa: FBT001
    ) -> None:
        self._key = key
        self._value = value
        self._public = public
        self._annotate = annotate
        self._token: Token[dict[str | type[Any], Any]] | None = None
        self._block: int | None = None
        # The exact mapping installed, which is how exit tells an ordinary unwind from a repair.
        self._entered: dict[str | type[Any], Any] | None = None

    def __enter__(self) -> T:
        if self._token is not None:
            raise RuntimeError(
                "this provider is already active. Create a separate provider() "
                "for nested or concurrent with blocks"
            )
        value, public = self._value, self._public
        if self._sealed:
            # A fresh scope per entry, which is what stops a re-entry reviving the last one.
            self._scope = scope = _Scope(self._key, _user_site()[0])
            value, public = _sealed_views(value, public, scope)
        enclosing = _registry.get()
        updated = dict(enclosing)
        updated[self._key] = public
        if _debug_state.recording:
            self._block, updated = _record_enter(self._key, enclosing, updated)
        # After the ledger, so the chain lands on the mapping actually installed.
        updated[_Open] = (*enclosing.get(_Open, ()), self)
        self._entered = updated
        self._token = _registry.set(updated)
        return value

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        token, self._token = self._token, None
        if token is not None:
            ours = self._unwind(token)
            # Gated on what enter recorded, not on a switch a thread can flip.
            block, self._block = self._block, None
            if block is not None:
                _record_exit(block, failed=exc_type is not None)
            # After the reset, since restoring the scope must not depend on a user's repr.
            if exc is not None:
                _annotate(exc, self._key, self._noted(), annotate=self._annotate)
            # Last, since a filter making the warning an error would skip the bookkeeping above.
            if not ours:
                self._warn_orphaned(displacing=exc is not None)

    def _unwind(self, token: Token[dict[str | type[Any], Any]]) -> bool:
        """Take this block out of the registry, reporting whether this context held it.

        An ordinary exit resets through the token.  A block that opened later
        and closed first leaves every other token pointing at a snapshot taken
        before it, so the current mapping is repaired instead of reset.
        """
        entered, self._entered = self._entered, None
        current = _registry.get()
        # Ordinary nesting exits over the mapping it installed, so the common case is one check.
        if current is entered:
            try:
                _registry.reset(token)
            except (ValueError, RuntimeError):
                # A copied context shares the mapping, so only the refused reset marks this foreign.
                return False
            return True
        chain: tuple[_Provider[Any], ...] = current.get(_Open, ())
        # Membership is identity, since no provider defines __eq__.
        if self in chain:
            # A block opened later is still open, so every token here points at a stale mapping.
            _registry.set(_repaired(chain, self, current))
            return True
        return False

    def _warn_orphaned(self, *, displacing: bool) -> None:
        """Report a block whose value outlives it."""
        name = _describe_key(self._key)
        # The ledger's frame walk, so the warning names the user's line and not this unwind.
        levels = _user_site()[1]
        message = (
            f"nodrill: the provider for {name} is closing from a context it did not "
            f"open in, so the block cannot unwind and its value stays in the context "
            f"that holds it. An async generator abandoned without contextlib.aclosing() "
            f"is the usual cause, and an ExitStack entered and closed in different tasks "
            f"or a block entered on one thread and exited on another do the same. "
            f"Close the block in the context that opened it."
        )
        try:
            warnings.warn(message, OrphanedProviderWarning, stacklevel=levels)
        except Warning:
            # A filter chose the error, and the failure on its way out is still worth more.
            if not displacing:
                raise

    def _noted(self) -> Any:
        """Return what a note describes, which is what this block itself provided."""
        return self._value

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


class _PerEntryProvider(_Provider[Any]):
    """Base of the providers that mint their value on entry rather than at the call.

    Clearing on the way out is what keeps one entry's value from outliving
    the block that built it.  Empty slots, since the value lives in the
    fields _Provider already has.
    """

    __slots__ = ()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            super().__exit__(exc_type, exc, tb)
        finally:
            # In a finally, since a repr raising a BaseException would otherwise pin the value.
            self._value = self._public = None


class _LazyProvider(_PerEntryProvider):
    """Context manager returned by provider() for a lazy() target.

    Mints a fresh build per entry, so a reused provider resolves again, and
    with frozen=True two views of it.
    """

    __slots__ = ("_carrier", "_frozen")

    def __init__(self, carrier: _Lazy, *, frozen: bool, annotate: bool | None) -> None:
        super().__init__(carrier.key, None, None, annotate)
        self._carrier = carrier
        self._frozen = frozen

    def __enter__(self) -> Any:
        if self._token is not None:
            # Delegated, so an already-active provider has one error and one wording.
            return super().__enter__()
        # Two views only when frozen, so the block keeps writing while callees read only.
        state = _Resolution(self._carrier.key, self._carrier.factory)
        self._value = _LazyCell(state, frozen=False)
        self._public = _LazyCell(state, frozen=True) if self._frozen else self._value
        yielded = super().__enter__()
        # After publishing, so a factory reading its own key meets the guard, not the outer value.
        state.context = copy_context()
        return yielded


class _ExtendingProvider(_PerEntryProvider):
    """Context manager returned by provider() for extend=True.

    Mints the merged namespace per entry, so a reused provider object layers
    over whatever encloses it at that moment rather than over what enclosed
    it when provider() was called.
    """

    __slots__ = ("_frozen", "_name", "_values")

    def __init__(
        self, name: str, values: dict[str, Any], *, frozen: bool, annotate: bool | None
    ) -> None:
        super().__init__(name, None, None, annotate)
        # The same string as _key, kept narrowed, since only a name can be extended.
        self._name = name
        self._values = values
        self._frozen = frozen

    def __enter__(self) -> Namespace:
        if self._token is None:
            merged = self._extended(_registry.get())
            self._value = merged
            self._public = _FrozenProxy(merged) if self._frozen else merged
        # Delegated, so an already-active provider has one error and one wording.
        namespace: Namespace = super().__enter__()
        return namespace

    def _noted(self) -> Namespace:
        """Return this layer alone, since the merged copy holds what an enclosing block provided."""
        return Namespace._named(self._name, self._values)  # noqa: SLF001

    def _extended(self, registry: dict[str | type[Any], Any]) -> Namespace:
        """Return a fresh namespace with this layer's values over the enclosing ones."""
        outer = registry.get(self._name, _MISSING)
        if outer is _MISSING:
            return Namespace._named(self._name, self._values)  # noqa: SLF001
        if not isinstance(outer, Namespace):
            # Named through __class__, so a frozen outer value reports its own type.
            raise TypeError(
                f"provider({self._name!r}, extend=True) can only extend a Namespace, and "
                f"{self._name!r} currently provides a {outer.__class__.__name__}. Drop "
                f"extend=True to shadow it, as a plain provider does"
            )
        # A copy, since sibling tasks hold a reference to the outer dict itself.
        values = dict(vars(outer))
        values.update(self._values)
        return Namespace._named(self._name, values)  # noqa: SLF001


class _Sealing:
    """Expires this entry's views on the way out of the block.

    A class rather than a field, so an ordinary provider pays nothing for a
    flag it would always read as False, and the exit half costs it no branch
    at all.  Empty slots, since a second slotted base would conflict with the
    layout _Provider already has.
    """

    __slots__ = ()

    _sealed = True

    # What the mixin reads off whichever provider it sits in front of, declared because
    # a self typed as that host would leave super() with nothing to resolve against.
    _scope: _Scope
    _token: Token[dict[str | type[Any], Any]] | None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Before the unwind, so a value that escaped is dead the moment its block ends.
        if self._token is not None:
            self._scope.exited = _user_site()[0]
        super().__exit__(exc_type, exc, tb)  # type: ignore[misc]  # a provider is always under it


class _SealedProvider(_Sealing, _Provider[Any]):
    """Context manager returned by provider(..., sealed=True)."""

    __slots__ = ()


class _SealedLazyProvider(_Sealing, _LazyProvider):
    """Context manager returned by provider(lazy(...), sealed=True)."""

    __slots__ = ()


class _SealedExtendingProvider(_Sealing, _ExtendingProvider):
    """Context manager returned by provider(..., extend=True, sealed=True)."""

    __slots__ = ()


def _refuse_data_flags(**flags: Any) -> None:
    """Refuse a flag carrying data, which would otherwise eat a namespace attribute.

    provider("plan", extend="v1") reads as an attribute and binds the
    parameter, so the value disappears and the feature turns itself on.
    """
    for name, value in flags.items():
        if value is not True and value is not False:
            raise TypeError(
                f"provider({name}=...) is a flag and cannot carry data, and "
                f"{value!r} would turn it on as well as vanish. For a namespace "
                f"attribute of that name write "
                f"provider(Namespace({name}={value!r}, ...), key=<the name>)"
            )


@overload
def provider(
    name: str,
    /,
    *,
    frozen: bool = ...,
    extend: bool = ...,
    annotate: bool | None = ...,
    sealed: bool = ...,
    **values: Any,
) -> _Provider[Namespace]: ...
@overload
def provider(
    instance: T,
    /,
    *,
    key: str | type[Any] = ...,
    frozen: bool = ...,
    annotate: bool | None = ...,
    sealed: bool = ...,
) -> _Provider[T]: ...
@overload
def provider(
    *,
    name: str,
    frozen: bool = ...,
    extend: bool = ...,
    annotate: bool | None = ...,
    sealed: bool = ...,
    **values: Any,
) -> _Provider[Namespace]: ...
def provider(
    *args: Any,
    key: str | type[Any] | None = None,
    frozen: bool = False,
    extend: bool = False,
    annotate: bool | None = None,
    sealed: bool = False,
    **values: Any,
) -> _Provider[Any]:
    """Make a value available to the whole call subtree through use().

    provider("app") yields a fresh Namespace registered under the name,
    prefilled from keyword arguments.  provider(instance) registers the
    instance under its class for a typed use(type(instance)) lookup, or under
    key= if one is given.  provider(lazy(Cls, factory)) registers a value
    that is built on the first read inside the scope, and not at all if
    nothing reads it.  Same-key providers shadow outer ones, and the outer
    value is restored on exit even if the block raises.  With extend=True, a
    string-named provider lays its values over a copy of the namespace the
    same name already holds instead of shadowing it.  With frozen=True,
    consumers get a read-only view while the yielded object stays writable.
    annotate decides whether an exception leaving the block carries a note
    naming what the block provided, where None follows annotate_exceptions()
    and True or False decides for this block alone.  With sealed=True the
    block and its consumers both get a view that raises ExpiredScopeError
    once the block has exited, so a value captured by a closure or a
    background task reports the escape where it happens.
    """
    _refuse_data_flags(frozen=frozen, extend=extend, sealed=sealed)
    target = _target_of(args, values)
    if isinstance(target, str):
        if key is not None:
            raise TypeError(
                "provider(key=...) applies to instance providers. A string-named "
                "provider is already registered under its name"
            )
        if extend:
            extending = _SealedExtendingProvider if sealed else _ExtendingProvider
            return extending(target, values, frozen=frozen, annotate=annotate)
        registered: str | type[Any] = target
        value: Any = Namespace._named(target, values)  # noqa: SLF001
    else:
        if extend:
            raise TypeError(
                "provider(extend=True) applies to string-named providers, as "
                "provider('audit', extend=True, actor_id=7). A value provider layers by "
                "providing a new value, which for a dataclass is dataclasses.replace(instance, ...)"
            )
        if values:
            raise TypeError(
                "keyword values are only supported for string-named providers: "
                "provider('app', db=...)"
            )
        if _is_lazy(target):
            if key is not None:
                raise TypeError(
                    "provider(key=...) does not apply to a lazy target: lazy() already "
                    "names the key it registers under"
                )
            deferred = _SealedLazyProvider if sealed else _LazyProvider
            return deferred(target, frozen=frozen, annotate=annotate)
        registered = _instance_key(target, key)
        value = target
    public = _FrozenProxy(value) if frozen else value
    opened = _SealedProvider if sealed else _Provider
    return opened(registered, value, public, annotate)


def _target_of(args: tuple[Any, ...], values: dict[str, Any]) -> Any:
    """Return what provider() was pointed at, positionally or by name=.

    Pops the name= keyword when that is what names the provider, leaving
    values holding prefill data and nothing else.
    """
    if len(args) > 1:
        raise TypeError(f"provider() takes a single target, got {len(args)} positional arguments")
    if args:
        return args[0]
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
    return target


def _instance_key(target: Any, key: Any) -> str | type[Any]:
    """Return the registry key for an instance provider.

    A ref() key resolves here, so the registry only ever holds a class or a
    name.  Typed loosely on purpose, since this is where a key of the wrong
    kind is caught and the annotation cannot rule one out first.
    """
    if isinstance(target, type):
        raise TypeError(
            f"provider() takes an instance, not a class. "
            f"Did you mean provider({target.__name__}(...))?"
        )
    if _is_ref(target):
        # Described rather than resolved, so a bad path is not reported as a bad call.
        raise TypeError(
            f"provider() takes an instance, not a key. {target!r} names the class to "
            f"register under: pass the value, as provider(instance, key={target!r})"
        )
    if key is None:
        # The value's own answer, so a view or a lazy cell keys by what is under it.
        answer: type[Any] = target.__class__
        return answer
    key = _key_target(key)
    if isinstance(key, _KEY_TYPES):
        return key
    if _is_lazy(key):
        raise TypeError(
            "provider(key=...) expects a string name or a class. A lazy() target goes in "
            "the positional slot, as provider(lazy(Cls, factory))"
        )
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
    return _resolve_miss(key, default)


def _resolve_miss(key: Any, default: Any = _MISSING) -> Any:
    """Finish a lookup that missed the registry.

    Validates the key kind, tries the set_default() factory, then the
    caller's default, then raises.  Split out of use() so compiled @inject
    wrappers can take the miss path without re-reading the registry.

    A ref that got this far already resolved, on the hash the lookup took, so
    reading its target costs nothing and every rule below is written once.
    """
    target = _key_target(key)
    if not isinstance(target, _KEY_TYPES):
        if _is_lazy(target):
            name = _describe_key(target.key)
            raise TypeError(
                f"use() received what lazy() returned, which is a target rather than a key. "
                f"Open it with provider(lazy({name}, factory)) and read it with use({name})"
            )
        raise TypeError(
            f"use() expects a string name or a class, got {type(target).__name__}: {target!r}"
        )
    if isinstance(target, type):
        factory = _defaults.get(target)
        if factory is not None:
            # A suspicious class pays the count, and a pending declaration one resolution check.
            if _pending or target in _fired:
                _note_fallback(target)
            return factory()
    if default is not _MISSING:
        return default
    # The resolved target, since that is what a provider registered under.
    recording = _debug_state.recording
    diagnosis = _diagnose(target) if recording else None
    available = [k for k in _registry.get() if k is not _Open]
    raise NoProviderError(
        key, available, diagnosis, provided_by=_expected_at(target), offer_debug=not recording
    )


def active() -> Mapping[str | type[Any], Any]:
    """Return a read-only view of the providers active right now.

    Keyed exactly as use() looks values up, for debugging and test
    assertions.  The view is a snapshot and does not track later scopes, and
    reading it is not a read of any provider under debug(unused=True).
    """
    registry = _registry.get()
    if _Open in registry:
        # A counting registry always carries _Open, so the filter is also the uncounting copy.
        registry = {key: value for key, value in registry.items() if key is not _Open}
    return MappingProxyType(registry)


def set_default(cls: type[T], factory: Callable[[], T] | None) -> type[T]:
    """Register a fallback factory for use(cls) outside any provider.

    The factory runs on every miss (a fresh instance each time, never a
    cached singleton).  None removes the registration.  A ref() resolves on
    the spot, since the table is keyed by class, and the class comes back.
    """
    key = _key_target(cls)
    if not isinstance(key, type):
        raise TypeError(f"set_default() registers classes, got {type(key).__name__}: {key!r}")
    # Ahead of the chain below, which a narrowing checker calls dead code after callable().
    if _is_lazy(factory):
        raise TypeError(
            "set_default() takes a plain factory, not what lazy() returns. A registered "
            "factory runs on every miss, while a lazy value is built once per scope: "
            "pass it to provider() instead"
        )
    if factory is None:
        _defaults.pop(key, None)
    elif callable(factory):
        _defaults[key] = factory
    else:
        raise TypeError(f"factory must be callable or None, got {type(factory).__name__}")
    return key


@contextmanager
def isolate() -> Iterator[None]:
    """Run a block against fresh context state, restoring the outer state on exit.

    Providers and ambient attributes start empty.  Any set_default()
    registration or declare() call made inside is rolled back, and so is any
    ref() the block itself created, which is what keeps one test's broken
    path out of another test's resolve_refs().  A ref or a declaration a
    module made while the block imported it belongs to that module and
    stays, since the module does.  Meant for test fixtures.
    """
    registry_token = _registry.set({})
    ambient_token = _ambient.set({})
    saved_defaults = dict(_defaults)
    saved_refs = _snapshot()
    saved_declared = _snapshot_declared()
    try:
        yield
    finally:
        _restore_declared(saved_declared)
        _restore(saved_refs)
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
