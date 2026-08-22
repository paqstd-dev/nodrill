"""The ref() key, its resolution, and the list of refs created so far.

A ref names its target by import path and borrows that target's hash and
equality once it resolves, so the registry entry stored under the class is the
entry a lookup through the ref finds.  Nothing branches on a ref.  The dict
does the work, which leaves use() untouched and the compiled @inject wrappers
with it.

Resolution is deterministic and idempotent, so it runs unlocked.  The module
lock guards only the lists of created refs that resolve_refs() walks.  Holding a
lock across import_module() would order this module's lock against the import
system's per-module locks, which is the deadlock every lazy importer eventually
reports.
"""

from __future__ import annotations

import inspect
import threading
import weakref
from importlib import import_module
from types import ModuleType
from typing import Any

from ._errors import KeyResolutionError

# Distinct from None, which a path can name.
_PENDING = object()

# A path is a module part and at least one attribute, so two segments is the minimum.
_MINIMUM_SEGMENTS = 2

# Refs created between two sweeps, so a list stays at the live count plus this.
_SWEEP_EVERY = 64

_lock = threading.Lock()

# Split because isolate() rolls back what a block created, while a module outlives the block.
_created: list[weakref.ref[_Ref]] = []
_imported: list[weakref.ref[_Ref]] = []


class _Resolutions:
    """A count of successful fills, so a pending-declaration scan can skip.

    Mutated in place and never rebound, since _declare binds the object.
    """

    __slots__ = ("count",)

    def __init__(self) -> None:
        self.count = 0


_resolutions = _Resolutions()


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

        A racing second walk is harmless, since import_module caches and the
        walk is pure, so both threads end up at the one object the module
        holds.  A failure is not kept, because the same path resolves once the
        import that was in flight completes.
        """
        target = self._target = _locate(self._path)
        # A racing double fill bumps twice, which only costs one spare rescan.
        _resolutions.count += 1
        return target

    def __hash__(self) -> int:
        # Read from the slot rather than resolve(), which is the whole cost of a ref lookup.
        target = self._target
        if target is _PENDING:
            target = self._fill()
        return hash(target)

    def __eq__(self, other: object) -> bool:
        target = self._target
        if target is _PENDING:
            target = self._fill()
        if type(other) is _Ref:
            other = other.resolve()
        if other is target:
            return True
        if isinstance(target, str):
            # A name compares by value, since two equal strings need not be one object.
            return other == target
        # A dict reaches this through the reflected call, so one entry answers both spellings.
        return NotImplemented

    def __repr__(self) -> str:
        return f"ref({self._path!r})"


# What use() and provider(key=) accept, once a late-bound name is one of them.
_KeyArg = str | type[Any] | _Ref


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
    source: Any = module
    owner = module.__name__
    for name in attributes:
        try:
            target = getattr(source, name)
        except AttributeError:
            raise KeyResolutionError(path, _no_attribute(source, owner, name)) from None
        source = target
        owner = f"{owner}.{name}"
    return source


def _no_attribute(source: Any, owner: str, name: str) -> str:
    """Explain a missing attribute, naming the cycle when that is what it is.

    Only a module can be mid-import, and only the step that reads off the
    module itself can be the cycle.  A name missing from an object further
    along the path is missing for its own reasons.
    """
    if isinstance(source, ModuleType) and _initialising(source):
        return (
            f"{owner!r} is still executing its own import, so {name!r} does not "
            f"exist yet. The lookup ran during that import, so move it inside a "
            f"function and it will run once the module is loaded"
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
    that lives in a module importing it back.  It goes wherever a class key
    goes, so use(), provider(key=), lazy(), set_default(), from_ctx() and
    @inject(from_=) all take one.
    """
    if not isinstance(path, str):
        raise TypeError(f"ref() expects an import path as a string, got {type(path).__name__}")
    _validate(path)
    created = _Ref(path)
    _remember(created)
    return created


def _during_import() -> bool:
    """Report whether a module running its own body is on the stack.

    A ref created there belongs to the module that keeps it rather than to
    whatever block first triggered the import, and that is the distinction
    isolate() needs before it forgets what its own block created.
    """
    frame = inspect.currentframe()
    while frame is not None:
        if getattr(frame.f_globals.get("__spec__"), "_initializing", False):
            return True
        frame = frame.f_back
    return False


def _remember(created: _Ref) -> None:
    """Hold a weak entry for resolve_refs(), sweeping the dead ones now and then.

    Which of the two lists takes the entry is what says whether isolate() may
    forget it.  The sweep is here rather than in a collection callback, which
    would have to take this lock from whichever thread happened to be
    collecting, including one already holding it.
    """
    holders = _imported if _during_import() else _created
    with _lock:
        holders.append(weakref.ref(created))
        if len(holders) % _SWEEP_EVERY == 0:
            holders[:] = [holder for holder in holders if holder() is not None]


def _live(holders: list[weakref.ref[_Ref]]) -> list[_Ref]:
    """Return the refs still alive, dropping the entries of the ones that are not."""
    alive: list[_Ref] = []
    kept: list[weakref.ref[_Ref]] = []
    for holder in holders:
        target = holder()
        if target is not None:
            alive.append(target)
            kept.append(holder)
    holders[:] = kept
    return alive


def resolve_refs() -> None:
    """Import every ref created so far, raising on the first one that fails.

    For an application that would rather fail at startup than on its first
    request.  In Django that call belongs in AppConfig.ready().  Refs that
    already resolved are left alone, so a second call costs one read each.
    """
    with _lock:
        live = _live(_created) + _live(_imported)
    # Outside the lock, since resolving imports and an import can create a ref.
    for target in live:
        target.resolve()


def _snapshot() -> list[weakref.ref[_Ref]]:
    """Return the created-refs list, for isolate() to put back afterwards.

    Only the block-scoped list is taken.  What an import created is not the
    block's to roll back.
    """
    with _lock:
        return list(_created)


def _restore(snapshot: list[weakref.ref[_Ref]]) -> None:
    """Restore a snapshot, forgetting the refs created since it was taken."""
    with _lock:
        _created[:] = snapshot


__all__ = ["ref", "resolve_refs"]
