"""The declared-key catalogue behind declare() and keys().

A module-level table written at import time, configuration in exactly the
sense the defaults table in _core is, and never flowing state.  Nothing here
touches a lookup that hits.  The miss path reads the table to name the
boundaries a declared key was expected from, and keys() hands the table to a
startup check or an admin page.

A declaration made through a ref() waits in a pending list until the ref has
resolved on its own, because forcing the ref would import, and a declaration
must never be the thing that triggers an import.  A pending entry that
resolves to something no lookup could take is dropped with a note explain()
reports, never a warning, since the drop is discovered on paths that cannot
afford a filter turning it into a raise.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Literal, TypeVar, overload

from ._errors import _KEY_TYPES, _describe_key, _Key
from ._refs import _PENDING, _during_import, _is_ref, _Ref, _resolutions

T = TypeVar("T")

# Distinct from None, so declare(None) is reported instead of read as the decorator form.
_UNSET: Any = object()

_lock = threading.Lock()

# Every container below is mutated in place and never rebound, since _core binds the objects.
_declared: dict[_Key, Declaration] = {}

_pending: list[_Pending] = []

# What a module body declared during its own import, which isolate() may not forget.
_imported_declared: dict[_Key, Declaration] = {}

# Firings per suspicious class, seeded at zero so the fallback gate is one probe of this dict.
_fired: dict[type[Any], int] = {}

# Rendered notes about dropped declarations, keyed by themselves so a repeat says it once.
_dropped: dict[str, None] = {}


class _Scan:
    """Remembers the resolution count the last pending scan saw.

    A holder rather than a module global, so no writer needs a global
    statement.
    """

    __slots__ = ("at",)

    def __init__(self) -> None:
        self.at = -1


_scan = _Scan()


@dataclass(frozen=True, slots=True)
class Declaration:
    """What declare() recorded about one context key."""

    key: str | type[Any]
    doc: str | None
    provided_by: tuple[str, ...]
    fallback: str | None


@dataclass(eq=False, slots=True)
class _Pending:
    """A declaration waiting for its ref to resolve.

    Carries the metadata unassembled, since the Declaration is built with
    the resolved key, and eq=False keeps identity comparison, since
    comparing the ref inside would force the import this list exists to
    avoid.
    """

    ref: _Ref
    doc: str | None
    provided_by: tuple[str, ...]
    fallback: str | None
    imported: bool


@overload
def declare(
    key: type[T],
    /,
    *,
    doc: str | None = ...,
    provided_by: str | Iterable[str] = ...,
    fallback: Literal["suspicious"] | None = ...,
) -> type[T]: ...
@overload
def declare(
    key: str,
    /,
    *,
    doc: str | None = ...,
    provided_by: str | Iterable[str] = ...,
) -> str: ...
@overload
def declare(
    *,
    doc: str | None = ...,
    provided_by: str | Iterable[str] = ...,
    fallback: Literal["suspicious"] | None = ...,
) -> Callable[[type[T]], type[T]]: ...
def declare(
    key: Any = _UNSET,
    /,
    *,
    doc: str | None = None,
    provided_by: str | Iterable[str] = (),
    fallback: str | None = None,
) -> Any:
    """Record what a context key is for, who provides it, and how its fallback is meant.

    Registers metadata about a key in an import-time catalogue and changes no
    lookup behaviour at all.  keys() lists the catalogue, and a
    NoProviderError for a declared key names the boundaries from provided_by.
    With fallback="suspicious", every set_default() firing for the class is
    counted, and explain() reports the count, so a fallback quietly carrying
    production traffic becomes visible.  Takes a class, a string name or a
    ref(), returning the key unchanged, and without a key returns a class
    decorator carrying the same metadata.  A ref() is not imported at the
    call, and its declaration takes effect once the ref has resolved on its
    own.  Declaring a key twice keeps the later declaration to take effect,
    as set_default() keeps the later factory.
    """
    # Before the dispatch, so the decorator form reports a typo at this call.
    boundaries = _boundaries(provided_by)
    if doc is not None and not isinstance(doc, str):
        raise TypeError(f"declare(doc=...) expects a string, got {type(doc).__name__}: {doc!r}")
    if fallback is not None and fallback != "suspicious":
        raise TypeError(f"declare(fallback=...) accepts 'suspicious' or None, got {fallback!r}")
    if key is _UNSET:

        def deferred(cls: type[Any]) -> type[Any]:
            _declare_key(cls, doc, boundaries, fallback)
            return cls

        return deferred
    return _declare_key(key, doc, boundaries, fallback)


def _declare_key(
    key: Any, doc: str | None, boundaries: tuple[str, ...], fallback: str | None
) -> Any:
    """Write one validated declaration, returning the key unchanged."""
    # First, so a pending declaration whose ref already resolved lands before this one.
    _absorb()
    if _is_ref(key):
        entry = _Pending(key, doc, boundaries, fallback, _during_import())
        with _lock:
            _pending.append(entry)
        return key
    if not isinstance(key, _KEY_TYPES):
        raise TypeError(
            f"declare() expects a string name, a class, or a ref(), got {type(key).__name__}: "
            f"{key!r}"
        )
    if fallback is not None and isinstance(key, str):
        raise TypeError(
            f"declare(fallback=...) applies to class keys. set_default() registers classes, "
            f"so a fallback for {key!r} has nothing to count"
        )
    with _lock:
        _register(Declaration(key, doc, boundaries, fallback), imported=_during_import())
    return key


def _boundaries(provided_by: str | Iterable[str]) -> tuple[str, ...]:
    """Return provided_by as a tuple, taking one boundary as a bare string."""
    try:
        boundaries = (provided_by,) if isinstance(provided_by, str) else tuple(provided_by)
    except TypeError:
        raise TypeError(
            f"declare(provided_by=...) expects strings naming boundaries, got "
            f"{type(provided_by).__name__}: {provided_by!r}"
        ) from None
    for boundary in boundaries:
        if not isinstance(boundary, str):
            raise TypeError(
                f"declare(provided_by=...) expects strings naming boundaries, got "
                f"{type(boundary).__name__}: {boundary!r}"
            )
        if not boundary.strip():
            raise TypeError(
                f"declare(provided_by=...) expects boundary names, and {boundary!r} names nothing"
            )
    return boundaries


def _register(declaration: Declaration, *, imported: bool) -> None:
    """Write one declaration into the catalogue.  Runs under the lock."""
    key = declaration.key
    if declaration.fallback is not None and isinstance(key, str):
        # Only a ref delivers this state, since the direct spelling raises at the call.
        _dropped[
            f"nodrill declare: {key!r} is a string key, so its 'suspicious' fallback "
            f"has nothing to count and was dropped."
        ] = None
        declaration = replace(declaration, fallback=None)
    _declared[key] = declaration
    if imported:
        _imported_declared[key] = declaration
    if isinstance(key, type):
        # A fallback the later declaration dropped takes its firings with it.
        if declaration.fallback == "suspicious":
            _fired.setdefault(key, 0)
        else:
            _fired.pop(key, None)


def _absorb() -> None:
    """Move every pending declaration whose ref has resolved into the catalogue.

    A ref still pending is left alone, since forcing it would import.  The
    epoch check keeps a ref that never resolves cheap, one attribute read per
    call rather than a scan, and a target no lookup could take is dropped
    with a note explain() reports.
    """
    if not _pending:
        return
    # Read before the scan, so a resolution racing it is caught by the next call.
    seen = _resolutions.count
    if seen == _scan.at:
        return
    with _lock:
        waiting: list[_Pending] = []
        for entry in _pending:
            target = entry.ref._target  # noqa: SLF001
            if target is _PENDING:
                waiting.append(entry)
            elif isinstance(target, _KEY_TYPES):
                _register(
                    Declaration(target, entry.doc, entry.provided_by, entry.fallback),
                    imported=entry.imported,
                )
            else:
                _dropped[
                    f"nodrill declare: {entry.ref!r} resolved to a "
                    f"{type(target).__name__}, which use() cannot look up, so its "
                    f"declaration was dropped."
                ] = None
        _pending[:] = waiting
        _scan.at = seen


def keys() -> Mapping[str | type[Any], Declaration]:
    """Return the declared keys as a read-only snapshot, keyed as use() looks them up.

    For a startup check, an admin page or a test.  Nothing is imported by the
    call, so a declaration made through a ref() appears once the ref has
    resolved, and the catalogue lists what the modules imported so far have
    declared.
    """
    _absorb()
    with _lock:
        return MappingProxyType(dict(_declared))


def _expected_at(target: _Key) -> tuple[str, ...]:
    """Return the boundaries declared for target, for a miss to report."""
    _absorb()
    declaration = _declared.get(target)
    return declaration.provided_by if declaration is not None else ()


def _note_fallback(target: type[Any]) -> None:
    """Count a set_default() firing for a class declared suspicious.

    Reached only when the gate in _core saw a pending declaration or a
    seeded counter, so an application that never declares pays the gate's
    two reads and nothing else.
    """
    _absorb()
    # Unlocked on purpose, since a torn count only degrades a report.
    count = _fired.get(target)
    if count is not None:
        _fired[target] = count + 1


def _report_lines() -> list[str]:
    """Return the fired counts and the dropped declarations, for explain()."""
    # A copy, since another thread's firing may resize the dict under the sort.
    counts = sorted(dict(_fired).items(), key=lambda item: _describe_key(item[0]))
    lines = [
        f"nodrill declare: the 'suspicious' fallback for {_describe_key(target)} has fired "
        f"{count} time{'' if count == 1 else 's'}."
        for target, count in counts
        if count
    ]
    return lines + list(_dropped)


_Saved = tuple[dict[_Key, Declaration], list[_Pending], dict[type[Any], int], dict[str, None]]


def _snapshot() -> _Saved:
    """Return the declaration state, for isolate() to put back afterwards."""
    with _lock:
        return dict(_declared), list(_pending), dict(_fired), dict(_dropped)


def _restore(saved: _Saved) -> None:
    """Restore a snapshot, keeping what a module body has declared.

    A declaration a module made while the block imported it belongs to the
    module and stays, exactly as a ref does, since the module outlives the
    block.  A snapshot entry for the same key wins, being what the block
    actually started from.  The firings kept are the snapshot's, filtered to
    keys the merged catalogue still marks suspicious.  A pre-block pending
    declaration whose ref resolved during the block is absorbed on the way
    out, since it is pre-block configuration whose moment simply arrived, and
    leaving it pending would let it re-land over a later declaration.
    """
    declared, pending, fired, dropped = saved
    with _lock:
        merged = _imported_declared | declared
        # Update then prune, so a concurrent reader never sees the table empty.
        _declared.update(merged)
        for key in [key for key in _declared if key not in merged]:
            del _declared[key]
        alive = set(pending)
        _pending[:] = pending + [
            entry for entry in _pending if entry.imported and entry not in alive
        ]
        merged_fired = {
            key: fired.get(key, 0)
            for key, record in merged.items()
            if record.fallback == "suspicious" and isinstance(key, type)
        }
        _fired.update(merged_fired)
        for key in [key for key in _fired if key not in merged_fired]:
            del _fired[key]
        _dropped.clear()
        _dropped.update(dropped)
        # Forced, since a restored pending entry may have resolved inside the block.
        _scan.at = -1
    _absorb()


__all__ = ["Declaration", "declare", "keys"]
