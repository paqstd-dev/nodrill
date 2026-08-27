"""Provenance diagnostics for a lookup that found nothing.

A miss usually means the provider is open somewhere this frame cannot see,
because the call crossed a boundary that does not carry context.  The
evidence sits in another context, which is where a lookup cannot look.

While debug mode is on, every provider block records where it was entered
in a module-level ledger that a miss reads to report a cause.  A ContextVar
could not hold it, since one shows only the scopes this frame already sees.

The contract recorder rides the same instrumentation.  NODRILL_CONTRACT arms
it at import, every read under an entry point becomes a fact, and the process
writes its shard at exit through _audit.
"""

from __future__ import annotations

import atexit
import inspect
import itertools
import os
import sys
import threading
import warnings
from collections.abc import Iterable, Iterator, MutableMapping
from contextlib import contextmanager
from types import TracebackType
from typing import Any, NamedTuple
from weakref import WeakKeyDictionary

from ._declare import _report_lines
from ._errors import _NO_ENTRY, UnusedProviderWarning, _counted, _describe_key, _Key, _key_path
from ._refs import _key_target

_Registry = dict[_Key, Any]

# Skipped when naming a site, so an ExitStack or _LazyProvider entry names the user's line.
_RELAYS = (f"{__name__.rpartition('.')[0]}.", "contextlib.")

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
    """The debug switches, in one object so no function needs a global statement.

    recording and counting mirror the two depths rather than being read off
    them, since the provider path tests one of them on every block entered.
    watching is recording or auditing, so that path still tests one thing.
    """

    __slots__ = (
        "auditing",
        "counting",
        "depth",
        "reads_full",
        "recording",
        "seq",
        "unused_depth",
        "watching",
    )

    def __init__(self) -> None:
        self.depth = 0
        self.unused_depth = 0
        self.recording = False
        self.counting = False
        self.auditing = False
        self.watching = False
        self.reads_full = False
        self.seq = 0

    def watch(self) -> None:
        """Restate what the provider path tests, so the two flags behind it live in one place."""
        self.watching = self.recording or self.auditing


_state = _State()

# Written under the lock and read without one, since a torn read only degrades a message.
_lock = threading.Lock()
_open: dict[int, _Block] = {}
# Per thread and task, so one request's exit does not overwrite another's record.
_closed: dict[tuple[_Key, int, int | None], _Block] = {}
# Keys the cap above dropped, which a miss reports as gone rather than as absent.
_forgotten: dict[_Key, None] = {}

# Never rolled back, since a run is the unit, and capped since an entry point may carry data.
_reads: set[tuple[str, str, str]] = set()
# High enough that no honest run reaches it, low enough to stay an answer rather than a heap.
_READS_LIMIT = 100_000
# Keys NODRILL_CONTRACT_ENTRY names as boundaries, which mint a label even when nested.
_declared_entries: set[str] = set()


def _note(fact: tuple[str, str, str]) -> None:
    """Record one fact, and say once when a run stopped being one the contract can rest on."""
    if fact in _reads:
        return
    if len(_reads) >= _READS_LIMIT:
        if not _state.reads_full:
            _state.reads_full = True
            sys.stderr.write(
                f"nodrill: {_READS_LIMIT} facts recorded, so this run stopped recording. "
                f"An entry point carrying a request id mints one per request, and "
                f"NODRILL_CONTRACT_ENTRY names the block that is the boundary\n"
            )
        return
    _reads.add(fact)


def _entry_for(chain: tuple[Any, ...]) -> str:
    """Return the entry point a chain of open blocks answers to.

    A declared boundary wins over the block above it, innermost first, and an
    empty chain is no entry point at all rather than the last one to close.
    """
    labels = [_key_path(_key_target(block._key)) for block in chain]  # noqa: SLF001
    for label in reversed(labels):
        if label in _declared_entries:
            return label
    return labels[0] if labels else _NO_ENTRY


# Serials rather than id(), which the interpreter hands on as soon as a task dies.
_task_serials: WeakKeyDictionary[Any, int] = WeakKeyDictionary()
_next_task_serial = itertools.count(1).__next__

# Read once, so a process starts in debug mode without editing its code. 0 and empty are off.
_from_env = os.environ.get("NODRILL_DEBUG", "") not in {"", "0"}
_state.depth = 1 if _from_env else 0
_state.recording = _from_env
_state.watch()


def _arm(environ: MutableMapping[str, str]) -> None:
    """Turn the audit on from the environment, and arrange for this process to write its shard.

    A variable rather than a call, because a child interpreter inherits one
    and a call would have to be made again in every process a suite spawns.
    Takes the mapping rather than reading os.environ, so what it sets can be
    tested without a child interpreter.
    """
    # Spelled here rather than imported, since it decides whether _audit is loaded at all.
    directory = environ.get("NODRILL_CONTRACT", "")
    if not directory:
        return
    _state.auditing = True
    _state.watch()
    # Deferred, so a process that never audits pays for none of the tool's imports.
    from multiprocessing.util import Finalize, register_after_fork  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from ._audit import _ENTRY_VAR, _RUN_VAR, _declared, _dump, _new_run  # noqa: PLC0415

    # Resolved now, since the hooks below run at exit and a program may have moved by then.
    directory = str(Path(directory).resolve())
    # Written back too, so a child that starts elsewhere records here and not beside itself.
    environ["NODRILL_CONTRACT"] = directory
    _declared_entries.update(_declared(environ.get(_ENTRY_VAR, "")))
    run = environ.get(_RUN_VAR) or _new_run()
    # Written back so every child joins this run rather than starting one of its own.
    environ[_RUN_VAR] = run

    def _finalize(_: object = None) -> None:
        """Arrange the exit a worker takes when it never runs atexit."""
        Finalize(None, _dump, args=(directory, run, _reads), exitpriority=0)

    atexit.register(_dump, directory, run, _reads)
    # A multiprocessing worker exits through os._exit, which runs finalizers and not atexit.
    _finalize()
    # A fork clears that registry before the worker body runs, so the child registers again.
    register_after_fork(sys.modules[__name__], _finalize)


_arm(os.environ)


@contextmanager
def _recording(entries: Iterable[str] = ()) -> Iterator[set[tuple[str, str, str]]]:
    """Record a contract for the extent of a block, which nothing public does on purpose.

    Saved and restored rather than switched off at the end, since the process
    may be recording for real.  Meant for a test that wants to assert what a
    handler read without spawning a child interpreter.
    """
    saved = (_state.auditing, set(_reads), set(_declared_entries), _state.reads_full)
    _reads.clear()
    _declared_entries.clear()
    _declared_entries.update(entries)
    _state.auditing = True
    _state.reads_full = False
    _state.watch()
    try:
        yield _reads
    finally:
        _state.auditing = saved[0]
        _state.reads_full = saved[3]
        _state.watch()
        _reads.clear()
        _reads.update(saved[1])
        _declared_entries.clear()
        _declared_entries.update(saved[2])


class _InstrumentedRegistry(dict[_Key, Any]):
    """Registry that watches lookups, for read counting and for the audit.

    Installed instead of branching in use(), which is what keeps both
    features out of the hot path when neither is on.  owners maps a key to
    the block providing it, so a read credits that block and not every block
    sharing the key, and entry names the outermost block open above it.
    """

    __slots__ = ("entry", "owners")

    def __init__(self, registry: _Registry, owners: dict[_Key, _Reads], entry: str) -> None:
        super().__init__(registry)
        self.owners = owners
        self.entry = entry

    def _mark(self, key: _Key) -> None:
        """Mark the block providing key as read."""
        reads = self.owners.get(key)
        if reads is not None:
            reads.hit = True

    def __getitem__(self, key: Any) -> Any:
        # Typed loosely because this sees what a caller passed, not what the registry stores.
        value = super().__getitem__(key)
        if self.owners:
            self._mark(key)
        # A consumer read is a subscript, which is what leaves the chain key and a merge out.
        if _state.auditing:
            _note((self.entry, "requires", _key_path(_key_target(key))))
        return value

    def get(self, key: Any, default: Any = None) -> Any:
        """Return the value for key, marking the read, the way an extending layer reads it."""
        value = super().get(key, _MISS)
        if value is _MISS:
            return default
        if self.owners:
            self._mark(key)
        return value


def _reinstrument(
    registry: _Registry,
    replaced: _Registry,
    chain: tuple[Any, ...],
    *,
    restored: tuple[_Key, _Registry] | None = None,
) -> _Registry:
    """Return registry instrumented the way the mapping it replaces was.

    The label is derived from the chain rather than carried over, since a
    repaired mapping outlives the block that minted it.  A key restored from
    a block still open is credited to that block, or the next read of it
    would count for the block that just left.
    """
    if not isinstance(replaced, _InstrumentedRegistry):
        return registry
    owners = replaced.owners
    if restored is not None:
        key, entered = restored
        reads = entered.owners.get(key) if isinstance(entered, _InstrumentedRegistry) else None
        if reads is not None:
            owners = {**owners, key: reads}
    return _InstrumentedRegistry(registry, owners, _entry_for(chain))


def _record_fallback(key: _Key, source: str, chain: tuple[Any, ...]) -> None:
    """Note a miss a registration answered, which is the read a raise would never report.

    A set_default factory and a use(key, default=...) both return before
    anything reports a miss, so a NoProviderError a registration is hiding
    would otherwise never appear in a contract.
    """
    _note((_entry_for(chain), source, _key_path(_key_target(key))))


def _user_site() -> tuple[_Site, int]:
    """Return the innermost site outside this package, and how far up it is.

    The distance is the stacklevel warnings.warn() wants, counted from the caller.
    """
    frame = inspect.currentframe()
    levels = 0
    # Named exactly or dotted, so the prefix cannot swallow a user's contextlib_ext.
    while frame is not None and (
        (module := frame.f_globals.get("__name__", "")).startswith(_RELAYS)
        or module == "contextlib"
    ):
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


def _record_enter(
    key: _Key, enclosing: _Registry, registry: _Registry, *, outermost: bool
) -> tuple[int | None, _Registry]:
    """Note an entered provider block, and return its handle with the registry to install.

    The handle is the block's serial, which the provider holds until it exits,
    and it is None when only the audit is watching, since the ledger then has
    nothing to forget.  id() would be reused by whatever is allocated there next.
    """
    handle: int | None = None
    reads: _Reads | None = None
    if _state.recording:
        site, _ = _user_site()
        where = _where()
        reads = _Reads() if _state.counting else None
        with _lock:
            _state.seq += 1
            handle = _state.seq
            _open[handle] = _Block(key, site, where, handle, reads)
    owners: dict[_Key, _Reads] = {}
    # Only the audit reads a label, and rendering a key is not free on the block path.
    entry = _key_path(key) if _state.auditing else _NO_ENTRY
    # Noted on the open, so a boundary that reads nothing stays apart from one that never ran.
    if _state.auditing and entry in _declared_entries:
        _note((entry, "opened", "nothing"))
    if isinstance(enclosing, _InstrumentedRegistry):
        # Inherited whether or not counting is still on, since it is process-wide.
        owners = dict(enclosing.owners)
        # Outermost is read off the chain, since a repaired mapping outlives the one that made it.
        if not outermost and entry not in _declared_entries:
            entry = enclosing.entry
    if reads is not None:
        owners[key] = reads
    if not owners and not _state.auditing:
        return handle, registry
    return handle, _InstrumentedRegistry(registry, owners, entry)


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

    Nearest frame first, then a block still open over one that exited, and the
    innermost of those last.
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
            _state.watch()
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
            _state.watch()
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


def _codec_lines() -> list[str]:
    """Return the codec halves registered, which nothing else reports."""
    # Imported here, since _portable reaches this module through _core and a top import cycles.
    from ._portable import _codec  # noqa: PLC0415

    halves = [role for role in ("dump", "load") if getattr(_codec, role) is not None]
    # Only when there is one, so a process with no codec reads as it always did.
    return [f"nodrill codec: {' and '.join(halves)} registered."] if halves else []


def explain() -> str:
    """Return a report of the provider blocks open right now, a thread at a time.

    Written for a breakpoint, as print(nodrill.explain()).  Blocks opened on
    other threads and in other tasks are listed too, which is the reason to read
    this rather than active(), and the reader's own thread comes first with its
    own blocks innermost first.  The codec and any suspicious fallback that has
    fired are named above them, since nothing else in the process reports either.
    """
    heading = [*_codec_lines(), *_report_lines()]
    if not _state.recording:
        return "\n".join(
            [
                *heading,
                "nodrill debug mode is off, so no provider block is recorded.",
                (
                    "Turn it on with `with nodrill.debug():` or with NODRILL_DEBUG=1 "
                    "in the environment."
                ),
            ]
        )
    here = _where()
    blocks = sorted(_open.copy().values(), key=lambda entry: _listing(entry, here))
    if not blocks:
        return "\n".join([*heading, "nodrill debug: no provider block is open."])
    counted = _counted(len(blocks), "provider block")
    lines = [*heading, f"nodrill debug: {counted} open, innermost first within each thread."]
    lines += [
        f"  {_describe_key(entry.key)} opened at {entry.site.file}:{entry.site.line}, "
        f"{_on(entry.where)}"
        for entry in blocks
    ]
    return "\n".join(lines)


__all__ = ["debug", "explain"]
