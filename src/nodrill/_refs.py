"""Late-bound keys: the ref() reference, its resolution and the created-refs list.

A ref names its target by import path and borrows that target's hash and
equality once it resolves, so the registry entry stored under the class is the
entry a lookup through the ref finds.  Nothing branches on a ref: the dict does
the work, use() is untouched and the compiled @inject wrappers are too.

Resolution is deterministic and idempotent, so it runs unlocked; the module
lock guards only the list of created refs that resolve_refs() walks.  Holding a
lock across import_module() would order this module's lock against the import
system's per-module locks, which is the deadlock every lazy importer eventually
reports.
"""

from __future__ import annotations

import threading
import weakref
from importlib import import_module
from types import ModuleType
from typing import Any

from ._errors import KeyResolutionError

# Distinct from None, which a path can perfectly well name.
_PENDING = object()

# A path is a module part and at least one attribute, so two segments at the least.
_MINIMUM_SEGMENTS = 2

_lock = threading.Lock()
_created: list[weakref.ref[_Ref]] = []


class _Ref:
    """A key that names its target by import path and resolves on first use.

    Hash and equality are the target's, so a ref and the class it names are one
    key.  Both are what force resolution, which is why a ref cannot enter a set
    or a dict before its module can be imported.
    """

    __slots__ = ("__weakref__", "_path", "_target")

    def __init__(self, path: str) -> None:
        self._path = path
        self._target: Any = _PENDING

    def resolve(self) -> Any:
        """Return what the path names, importing it on the first call."""
        target = self._target
        if target is _PENDING:
            return self._fill()
        return target

    def _fill(self) -> Any:
        """Walk the path once and keep what it named.

        A racing second walk is harmless: import_module caches, the walk is
        pure, and both threads arrive at the one object the module holds.  A
        failure is not kept, since the same path resolves once the import that
        was in flight completes.
        """
        target = self._target = _locate(self._path)
        return target

    def __hash__(self) -> int:
        # The slot is read here rather than through resolve(), because these two
        # methods are the whole cost of a lookup through a ref.
        target = self._target
        if target is _PENDING:
            target = self._fill()
        return hash(target)

    def __eq__(self, other: object) -> bool:
        target = self._target
        if target is _PENDING:
            target = self._fill()
        if other is target:
            return True
        if type(other) is _Ref:
            return other.resolve() is target
        # A dict comparing a stored class against a ref reaches this through the
        # reflected call, which is what makes one entry answer both spellings.
        return NotImplemented

    def __repr__(self) -> str:
        return f"ref({self._path!r})"


# What use() and provider(key=) accept, once a late-bound name is one of them.
_Key = str | type[Any] | _Ref


def _is_ref(value: Any) -> bool:
    """Report whether value is what ref() returned.

    Called rather than inlined, since against an annotated parameter a
    narrowing type checker reads such a guard as dead code.
    """
    return type(value) is _Ref


def _key_target(key: Any) -> Any:
    """Return what key names, resolving a late-bound reference to its target.

    The registry only ever holds a resolved key, so every write path calls this
    and the read path never has to.
    """
    return key.resolve() if type(key) is _Ref else key


def _import(path: str, name: str) -> ModuleType:
    """Import name, reporting a failure as the ref that asked for it."""
    try:
        return import_module(name)
    except ImportError as exc:
        raise KeyResolutionError(path, f"cannot import {name!r}: {exc}") from exc


def _import_prefix(path: str, name: str) -> ModuleType | None:
    """Import a candidate prefix, returning None when there is no such module.

    An ImportError from inside the module's own body is a real failure and is
    reported, rather than mistaken for a prefix that was one component too long.
    """
    try:
        return _import(path, name)
    except KeyResolutionError as exc:
        cause = exc.__cause__
        if isinstance(cause, ModuleNotFoundError) and cause.name == name:
            return None
        raise


def _initialising(module: ModuleType) -> bool:
    """Report whether module is still running its own body.

    importlib marks the spec while the body runs, and that mark is the only
    signal separating an import cycle from a plain typo.  A module without one
    is treated as loaded, so the ordinary message is what a caller gets.
    """
    return bool(getattr(getattr(module, "__spec__", None), "_initializing", False))


def _walk(path: str, module: ModuleType, attributes: list[str]) -> Any:
    """Follow the attribute part of a path from the module it starts at."""
    target: Any = module
    owner = module.__name__
    for name in attributes:
        try:
            target = getattr(target, name)
        except AttributeError:
            raise KeyResolutionError(path, _no_attribute(module, owner, name)) from None
        owner = f"{owner}.{name}"
    return target


def _no_attribute(module: ModuleType, owner: str, name: str) -> str:
    """Explain a missing attribute, naming the cycle when that is what it is."""
    if _initialising(module):
        return (
            f"{module.__name__!r} is still executing its own import, so {name!r} does not "
            f"exist yet. The lookup ran during that import: move it inside a function, so "
            f"it runs once the module is loaded"
        )
    return f"{owner!r} has no attribute {name!r}"


def _locate(path: str) -> Any:
    """Return what path names, importing whatever that takes.

    The colon says where the module ends.  Without one the module is the
    longest importable prefix, which is how pydoc.locate reads a dotted name.
    """
    module_name, colon, attributes = path.partition(":")
    if colon:
        return _walk(path, _import(path, module_name), attributes.split("."))
    parts = path.split(".")
    for cut in range(len(parts) - 1, 1, -1):
        module = _import_prefix(path, ".".join(parts[:cut]))
        if module is not None:
            return _walk(path, module, parts[cut:])
    return _walk(path, _import(path, parts[0]), parts[1:])


def _validate(path: str) -> None:
    """Reject a path that names no attribute, before anything defers to it."""
    module_name, colon, attributes = path.partition(":")
    parts = [*module_name.split("."), *(attributes.split(".") if colon else [])]
    if len(parts) < _MINIMUM_SEGMENTS or not all(part.isidentifier() for part in parts):
        raise ValueError(
            f"ref({path!r}) is not an import path. Write 'package.module:Name', or "
            f"'package.module.Name' with an importable module part"
        )


def ref(path: str, /) -> Any:
    """Name a key by import path, to be imported the first time it is used.

    'package.module:Name' is the canonical form, and 'package.module.Name' is
    accepted too.  Nothing is imported at the call, so a module can name a key
    that lives in a module importing it back.  Use it wherever a class key goes:
    use(), provider(key=), set_default(), from_ctx() and @inject(from_=).
    """
    if not isinstance(path, str):
        raise TypeError(f"ref() expects an import path as a string, got {type(path).__name__}")
    _validate(path)
    created = _Ref(path)
    with _lock:
        _created.append(weakref.ref(created))
    return created


def resolve_refs() -> None:
    """Import every ref created so far, raising on the first one that fails.

    For an application that would rather fail at startup than on its first
    request; in Django that call belongs in AppConfig.ready().  Refs that
    already resolved are left alone, so a second call costs one read each.
    """
    with _lock:
        live: list[_Ref] = []
        holders: list[weakref.ref[_Ref]] = []
        for holder in _created:
            target = holder()
            if target is not None:
                live.append(target)
                holders.append(holder)
        _created[:] = holders
    # Outside the lock, since resolving imports and an import can create a ref.
    for target in live:
        target.resolve()


def _snapshot() -> list[weakref.ref[_Ref]]:
    """Return the created-refs list, for isolate() to put back afterwards."""
    with _lock:
        return list(_created)


def _restore(snapshot: list[weakref.ref[_Ref]]) -> None:
    """Restore a snapshot, forgetting the refs created since it was taken."""
    with _lock:
        _created[:] = snapshot


__all__ = ["ref", "resolve_refs"]
