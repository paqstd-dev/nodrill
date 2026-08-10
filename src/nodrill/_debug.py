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
import itertools
import os
import threading
import warnings
from types import TracebackType
from typing import Any, NamedTuple
from weakref import WeakKeyDictionary

from ._errors import UnusedProviderWarning, _describe_key, _Key

_Registry = dict[_Key, Any]

# Skipped when naming a site, so an ExitStack or _LazyProvider entry names the user's line.
_RELAYS = (f"{__name__.rpartition('.')[0]}.", "contextlib")

# Bounded, since an entry names a key and a class key would otherwise be pinned.
_CLOSED_LIMIT = 256


class _Site(NamedTuple):
    """A file and a line in the user's code."""

    file: str
    line: int


class _Where(NamedTuple):
    """The thread and the task a frame is running on, by identity and by name."""

    thread: int
    thread_name: str
    task: int | None
    task_name: str | None


_UNKNOWN = _Site("<unknown>", 0)
_MISS = object()


class _Reads:
    """Whether anything read one block's value."""

    __slots__ = ("hit",)

    def __init__(self) -> None:
        self.hit = False


class _Block(NamedTuple):
    """One provider block, as the ledger remembers it.

    Holds the key and the sites, never the value, so nothing outlives its
    scope because debug mode was on.
    """

    key: _Key
    site: _Site
    where: _Where
    seq: int
    reads: _Reads | None
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

# Written under the lock and read without one, since a torn read only degrades a message.
_lock = threading.Lock()
_open: dict[int, _Block] = {}
# Per thread and task, so one request's exit does not overwrite another's record.
_closed: dict[tuple[_Key, int, int | None], _Block] = {}
# Keys the cap above dropped, which a miss reports as gone rather than as absent.
_forgotten: dict[_Key, None] = {}

# Serials rather than id(), which the interpreter hands on as soon as a task dies.
_task_serials: WeakKeyDictionary[Any, int] = WeakKeyDictionary()
_next_task_serial = itertools.count(1).__next__

# Read once, so a process starts in debug mode without editing its code. 0 and empty are off.
_from_env = os.environ.get("NODRILL_DEBUG", "") not in {"", "0"}
_state.depth = 1 if _from_env else 0
_state.recording = _from_env


class _CountingRegistry(dict[_Key, Any]):
    """Registry that marks which block's value a lookup read.

    Installed only while debug(unused=True) is on, which is what keeps read
    counting out of use() itself.  owners maps a key to the block providing
    it, so a read credits that block and not every block sharing the key.
    """

    __slots__ = ("owners",)

    def __init__(self, registry: _Registry, owners: dict[_Key, _Reads]) -> None:
        super().__init__(registry)
        self.owners = owners

    def _mark(self, key: _Key) -> None:
        """Note that something read the block providing key."""
        reads = self.owners.get(key)
        if reads is not None:
            reads.hit = True

    def __getitem__(self, key: _Key) -> Any:
        value = super().__getitem__(key)
        self._mark(key)
        return value

    def get(self, key: _Key, default: Any = None) -> Any:
        """Return the value for key, marking the read, the way @inject reads it."""
        value = super().get(key, _MISS)
        if value is _MISS:
            return default
        self._mark(key)
        return value


def _uncounted(registry: _Registry) -> _Registry:
    """Return a registry that scores no reads, for active() rather than a lookup."""
    return dict(registry) if isinstance(registry, _CountingRegistry) else registry


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


def _where() -> _Where:
    """Return the thread and the task this frame is running on."""
    # Imported here so that importing nodrill does not pay for asyncio.
    import asyncio  # noqa: PLC0415

    try:
        task = asyncio.current_task()
    except RuntimeError:
        # No running loop, the ordinary case for synchronous code.
        task = None
    ident = threading.get_ident()
    name = threading.current_thread().name
    if task is None:
        return _Where(ident, name, None, None)
    serial = _task_serials.get(task)
    if serial is None:
        # setdefault, so two threads minting at once agree on the winner.
        serial = _task_serials.setdefault(task, _next_task_serial())
    return _Where(ident, name, serial, task.get_name())


def _record_enter(key: _Key, enclosing: _Registry, registry: _Registry) -> tuple[int, _Registry]:
    """Note an entered provider block, and return its handle with the registry to install.

    The handle is the block's serial, which the provider holds until it exits.
    id() would be reused by the next provider at that address.
    """
    site, _ = _user_site()
    where = _where()
    reads = _Reads() if _state.counting else None
    with _lock:
        _state.seq += 1
        handle = _state.seq
        _open[handle] = _Block(key, site, where, handle, reads)
    owners: dict[_Key, _Reads] = {}
    if isinstance(enclosing, _CountingRegistry):
        # Inherited whether or not counting is still on, since it is process-wide.
        owners = dict(enclosing.owners)
    if reads is not None:
        owners[key] = reads
    return handle, _CountingRegistry(registry, owners) if owners else registry


def _remember_closed(entry: _Block) -> None:
    """File an exited block, dropping the oldest record once the ledger is full."""
    slot = (entry.key, entry.where.thread, entry.where.task)
    # Reinserted rather than assigned, so eviction takes the least recent exit.
    _closed.pop(slot, None)
    _closed[slot] = entry
    _forgotten.pop(entry.key, None)
    if len(_closed) > _CLOSED_LIMIT:
        dropped = next(iter(_closed))
        del _closed[dropped]
        _forgotten[dropped[0]] = None
        if len(_forgotten) > _CLOSED_LIMIT:
            del _forgotten[next(iter(_forgotten))]


def _record_exit(handle: int, *, failed: bool) -> None:
    """Forget a provider block, and warn when nothing read what it provided."""
    site, levels = _user_site()
    with _lock:
        # Absent when recording stopped meanwhile, which wipes what was open.
        entry = _open.pop(handle, None)
        if entry is not None:
            _remember_closed(entry._replace(closed=site))
    # A body that raised never had the chance to read, so it is not blamed.
    if entry is None or failed or entry.reads is None or entry.reads.hit:
        return
    name = _describe_key(entry.key)
    warnings.warn(
        f"nodrill: the provider for {name} at {entry.site.file}:{entry.site.line} was never "
        f"read, since no use({name}) ran inside the block.",
        UnusedProviderWarning,
        stacklevel=levels,
    )


def _rank(entry: _Block, here: _Where) -> tuple[int, int, int]:
    """Order the ledger by how likely a block is to explain this frame's miss.

    Nearest frame first, then a block still open over one that exited, then
    the innermost.
    """
    if entry.where.thread != here.thread:
        near = 2
    elif entry.where.task != here.task:
        near = 1
    else:
        near = 0
    return (near, 1 if entry.closed is not None else 0, -entry.seq)


def _listing(entry: _Block, here: _Where) -> tuple[bool, bool, str, str, int]:
    """Order the report by thread and task, the reader's own first.

    Sequence alone interleaves the threads and leaves no stack readable.
    """
    return (
        entry.where.thread != here.thread,
        entry.where.task != here.task,
        entry.where.thread_name,
        entry.where.task_name or "",
        -entry.seq,
    )


def _diagnose(key: _Key) -> str | None:
    """Return why this frame cannot see key, or None when the ledger knows nothing."""
    here = _where()
    recorded = [*_open.copy().values(), *_closed.copy().values()]
    candidates = [entry for entry in recorded if entry.key == key]
    if not candidates:
        return _forgotten_record(key) if key in _forgotten else None
    entry = min(candidates, key=lambda block: _rank(block, here))
    closed = entry.closed
    return _open_elsewhere(entry, here) if closed is None else _already_closed(entry, closed)


def _on(where: _Where) -> str:
    """Describe the thread and task a block was entered on."""
    on = f"on thread {where.thread_name!r}"
    return on if where.task_name is None else f"{on}, task {where.task_name!r}"


def _open_elsewhere(entry: _Block, here: _Where) -> str:
    """Explain a key that is open right now somewhere this frame cannot see."""
    name = _describe_key(entry.key)
    where = f"{name} is open right now at {entry.site.file}:{entry.site.line}, {_on(entry.where)}."
    if entry.where.thread != here.thread:
        return (
            f"{where}\n"
            f"This frame is on thread {here.thread_name!r}, which did not inherit that context.\n"
            f"Fix: submit through nodrill.Executor instead of ThreadPoolExecutor, or bind the "
            f"callable with nodrill.wrap() inside the provider block."
        )
    if here.task is not None and entry.where.task != here.task:
        return (
            f"{where}\n"
            f"This frame is running in task {here.task_name!r}, which was created outside that "
            f"block, so it never snapshotted it.\n"
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


def _already_closed(entry: _Block, closed: _Site) -> str:
    """Explain a key whose block has already exited."""
    name = _describe_key(entry.key)
    where = f"{name} was open at {entry.site.file}:{entry.site.line}"
    # A with exits on its own line, so print the exit site only when something else closed it.
    if closed != entry.site:
        where += f" and exited at {closed.file}:{closed.line}"
    return (
        f"{where}, {_on(entry.where)}.\n"
        f"This frame is running after that block closed.\n"
        f"Fix: do the work inside the block, or bind the callback with nodrill.wrap() inside it, "
        f"which carries the scope to wherever it runs."
    )


def _forgotten_record(key: _Key) -> str:
    """Say that a key was provided and that the ledger no longer knows where."""
    name = _describe_key(key)
    return (
        f"{name} was provided somewhere in this process, but debug mode keeps only the "
        f"{_CLOSED_LIMIT} most recent exits and this one has aged out.\n"
        f"Fix: narrow the run so fewer provider blocks close between the one you are looking "
        f"for and the miss."
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
                # A block still open here never records its exit, so its entry would outlive it.
                _open.clear()
                _closed.clear()
                _forgotten.clear()


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
    """Return a report of the provider blocks open right now, a thread at a time.

    Written for a breakpoint, as print(nodrill.explain()).  Blocks opened
    on other threads and in other tasks are listed too, which is the reason
    to read this rather than active(), and the reader's own thread comes
    first with its own blocks innermost first.
    """
    if not _state.recording:
        return (
            "nodrill debug mode is off, so no provider block is recorded.\n"
            "Turn it on with `with nodrill.debug():` or with NODRILL_DEBUG=1 in the environment."
        )
    here = _where()
    blocks = sorted(_open.copy().values(), key=lambda entry: _listing(entry, here))
    if not blocks:
        return "nodrill debug: no provider block is open."
    counted = f"{len(blocks)} provider block{'' if len(blocks) == 1 else 's'}"
    lines = [f"nodrill debug: {counted} open, innermost first within each thread."]
    lines += [
        f"  {_describe_key(entry.key)} opened at {entry.site.file}:{entry.site.line}, "
        f"{_on(entry.where)}"
        for entry in blocks
    ]
    return "\n".join(lines)


__all__ = ["debug", "explain"]
