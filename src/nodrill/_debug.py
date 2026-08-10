"""Provenance diagnostics for a lookup that found nothing.

A miss usually means the provider is open somewhere this frame cannot see,
because the call crossed a boundary that does not carry context.  The
evidence for that sits in another context, which is where a lookup cannot
look.

While debug mode is on, every provider block records where it was entered in
a module-level ledger, and a miss reads the ledger to report a cause.  A
ContextVar could not hold it, since one would only ever show the scopes this
frame already sees.
"""

from __future__ import annotations

import inspect
import os
import threading
import warnings
from types import TracebackType
from typing import Any, NamedTuple

from ._errors import _describe_key

_Key = str | type[Any]
_Registry = dict[_Key, Any]

# Skipped when naming a site, so a block entered through _LazyProvider or an
# ExitStack names the user's line rather than the relay's.
_RELAYS = (f"{__name__.rpartition('.')[0]}.", "contextlib")


class _Site(NamedTuple):
    """A file and a line in the user's code."""

    file: str
    line: int


_UNKNOWN = _Site("<unknown>", 0)
_MISS = object()


class _Block(NamedTuple):
    """One provider block, as the ledger remembers it.

    Holds the key and the sites, never the value, so nothing outlives its
    scope because debug mode was on.
    """

    key: _Key
    site: _Site
    thread: str
    task: str | None
    seq: int
    reads: int
    closed: _Site | None = None


class _State:
    """The debug switches, in one object so no function needs a global statement."""

    __slots__ = ("counting", "depth", "recording", "seq", "unused_depth")

    def __init__(self) -> None:
        self.depth = 0
        self.unused_depth = 0
        self.recording = False
        self.counting = False
        self.seq = 0


_state = _State()

# Written under the lock and read without one, since a read only happens on a
# miss and a torn read can only degrade the message.  A walk copies first.
_lock = threading.Lock()
_open: dict[int, _Block] = {}
_closed: dict[_Key, _Block] = {}
_reads: dict[_Key, int] = {}

# Read once, so a process can be started in debug mode without editing its code.
# Any value but the empty string or 0 turns it on for good.
_from_env = os.environ.get("NODRILL_DEBUG", "") not in {"", "0"}
_state.depth = 1 if _from_env else 0
_state.recording = _from_env


class _CountingRegistry(dict[_Key, Any]):
    """Registry that counts what a lookup reads out of it.

    Installed only while debug(unused=True) is on, which is what keeps read
    counting out of use() itself.
    """

    __slots__ = ()

    def __getitem__(self, key: _Key) -> Any:
        value = super().__getitem__(key)
        _reads[key] = _reads.get(key, 0) + 1
        return value

    def get(self, key: _Key, default: Any = None) -> Any:
        """Return the value for key, counting the read, the way @inject reads it."""
        value = super().get(key, _MISS)
        if value is _MISS:
            return default
        _reads[key] = _reads.get(key, 0) + 1
        return value


def _user_site() -> tuple[_Site, int]:
    """Return the innermost site outside this package, and how far up it is.

    The distance is the stacklevel warnings.warn() wants, counted from the
    caller.
    """
    frame = inspect.currentframe()
    levels = 0
    while frame is not None and frame.f_globals.get("__name__", "").startswith(_RELAYS):
        frame = frame.f_back
        levels += 1
    # None only where the implementation has no frames at all.
    site = _UNKNOWN if frame is None else _Site(frame.f_code.co_filename, frame.f_lineno)
    return site, levels


def _task_name() -> str | None:
    """Return the name of the asyncio task running this frame, if there is one."""
    # Imported here so that importing nodrill does not pay for asyncio.
    import asyncio  # noqa: PLC0415

    try:
        task = asyncio.current_task()
    except RuntimeError:
        # No running loop, the ordinary case for synchronous code.
        return None
    return None if task is None else task.get_name()


def _record_enter(owner: object, key: _Key, registry: _Registry) -> _Registry:
    """Note an entered provider block and return the registry it should install."""
    site, _ = _user_site()
    thread = threading.current_thread().name
    task = _task_name()
    with _lock:
        _state.seq += 1
        # Keyed by identity, so the ledger holds no reference to the provider either.
        _open[id(owner)] = _Block(key, site, thread, task, _state.seq, _reads.get(key, 0))
    return _CountingRegistry(registry) if _state.counting else registry


def _record_exit(owner: object, *, failed: bool) -> None:
    """Forget a provider block, and warn when nothing read what it provided."""
    site, levels = _user_site()
    with _lock:
        entry = _open.pop(id(owner), None)
        if entry is not None:
            _closed[entry.key] = entry._replace(closed=site)
    # A body that raised never had the chance to read, so it is not blamed.
    if entry is None or failed or not _state.counting:
        return
    if _reads.get(entry.key, 0) != entry.reads:
        return
    name = _describe_key(entry.key)
    warnings.warn(
        f"nodrill: the provider for {name} at {entry.site.file}:{entry.site.line} was never "
        f"read, since no use({name}) ran inside the block.",
        stacklevel=levels,
    )


def _diagnose(key: _Key) -> str | None:
    """Return why this frame cannot see key, or None when the ledger knows nothing."""
    live = [entry for entry in _open.copy().values() if entry.key == key]
    if live:
        # The innermost block, since that is the one the frame most likely meant.
        return _open_elsewhere(max(live, key=lambda entry: entry.seq))
    entry = _closed.get(key)
    return None if entry is None else _already_closed(entry)


def _on(entry: _Block) -> str:
    """Describe the thread and task a block was entered on."""
    on = f"on thread {entry.thread!r}"
    return on if entry.task is None else f"{on}, task {entry.task!r}"


def _open_elsewhere(entry: _Block) -> str:
    """Explain a key that is open right now somewhere this frame cannot see."""
    name = _describe_key(entry.key)
    thread = threading.current_thread().name
    task = _task_name()
    where = f"{name} is open right now at {entry.site.file}:{entry.site.line}, {_on(entry)}."
    if entry.thread != thread:
        return (
            f"{where}\n"
            f"This frame is on thread {thread!r}, which did not inherit that context.\n"
            f"Fix: submit through nodrill.Executor instead of ThreadPoolExecutor, or bind the "
            f"callable with nodrill.wrap() inside the provider block."
        )
    if task is not None and entry.task != task:
        return (
            f"{where}\n"
            f"This frame is running in task {task!r}, which was created outside that block, so it "
            f"never snapshotted it.\n"
            f"Fix: create the task inside the provider block, or await the work there."
        )
    return (
        f"{where}\n"
        f"This frame is on that thread and still cannot see it, so it is running under a different "
        f"context: a contextvars.Context.run(), a nodrill.wrap() snapshot taken before the block, "
        f"or a generator resumed outside it.\n"
        f"Fix: enter the provider inside the frame that reads it, or bind the callable with "
        f"nodrill.wrap() inside the block."
    )


def _already_closed(entry: _Block) -> str:
    """Explain a key whose block has already exited."""
    name = _describe_key(entry.key)
    closed = _UNKNOWN if entry.closed is None else entry.closed
    where = f"{name} was open at {entry.site.file}:{entry.site.line}"
    # The interpreter attributes a with statement's exit to the statement's own line,
    # so the exit site is worth printing only when something else closed the block.
    if closed != entry.site:
        where += f" and exited at {closed.file}:{closed.line}"
    return (
        f"{where}, {_on(entry)}.\n"
        f"This frame is running after that block closed.\n"
        f"Fix: do the work inside the block, or bind the callback with nodrill.wrap() inside it, "
        f"which carries the scope to wherever it runs."
    )


class _DebugMode:
    """Context manager returned by debug().

    Carries no state of its own, so it is reusable and can be entered from
    several threads at once.
    """

    __slots__ = ("_unused",)

    def __init__(self, *, unused: bool) -> None:
        self._unused = unused

    def __enter__(self) -> None:
        with _lock:
            _state.depth += 1
            _state.recording = True
            if self._unused:
                _state.unused_depth += 1
                _state.counting = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        with _lock:
            _state.depth -= 1
            _state.recording = _state.depth > 0
            if self._unused:
                _state.unused_depth -= 1
                _state.counting = _state.unused_depth > 0
            if not _state.recording:
                # A block still open here will never record its exit, so its entry
                # would outlive it.
                _open.clear()
                _closed.clear()
                _reads.clear()


def debug(*, unused: bool = False) -> _DebugMode:
    """Record where every provider block is entered, for the extent of the block.

    A lookup that misses while this is on names the thread, the task and
    the line the provider was opened on.  Recording is global and reference
    counted rather than scoped, since the block holding the answer is the
    one the failing frame cannot see, and it costs a stack read and a dict
    write per provider entered.  With unused=True, reads are counted too
    and a provider that nothing read warns when its block exits.
    """
    return _DebugMode(unused=unused)


def explain() -> str:
    """Return a report of the provider blocks open right now, innermost first.

    Written for a breakpoint, as print(nodrill.explain()).  Blocks opened
    on other threads and in other tasks are listed too, which is the reason
    to read this rather than active().
    """
    if not _state.recording:
        return (
            "nodrill debug mode is off, so no provider block is recorded.\n"
            "Turn it on with `with nodrill.debug():` or with NODRILL_DEBUG=1 in the environment."
        )
    blocks = sorted(_open.copy().values(), key=lambda entry: entry.seq, reverse=True)
    if not blocks:
        return "nodrill debug: no provider block is open."
    counted = f"{len(blocks)} provider block{'' if len(blocks) == 1 else 's'}"
    lines = [f"nodrill debug: {counted} open, innermost first."]
    lines += [
        f"  {_describe_key(entry.key)} opened at {entry.site.file}:{entry.site.line}, {_on(entry)}"
        for entry in blocks
    ]
    return "\n".join(lines)


__all__ = ["debug", "explain"]
