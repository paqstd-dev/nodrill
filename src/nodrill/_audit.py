"""The contract a run records, and the file a pull request reviews.

A parameter is visible in a signature and a context lookup is not, so
whether a handler can miss in production is a question answered today by
deploying.  Recording what each entry point actually read, and reviewing the
diff of that record, is how snapshot testing already answers the same
question about the same class of problem.

Recording is armed by NODRILL_CONTRACT and is off otherwise, and the ledger
does the observing, so nothing here is reachable from a lookup.  What a
contract says is only ever as true as the run that recorded it, which is a
limit the output states rather than one the reader has to infer.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

from ._errors import _NO_ENTRY, _counted

_Reads = set[tuple[str, str, str]]

# The one format, carried by a shard and by a contract alike, so there is one reader.
_HEADER = "# nodrill contract 1"
_SUFFIX = ".shard"
# A tab, because repr escapes one and a key may legally hold two spaces in a row.
_GAP = "\t"
# Written and diffed on machines nobody here chose, so nothing about the bytes is the platform's.
_ENCODING = "utf-8"
_NEWLINE = "\n"
# The one verb this file compares against, since the render drops it where a read says more.
_OPENED = "opened"
# The vocabulary a fact is written in, which _parse refuses a line outside of.
_VERBS = frozenset({"requires", "set_default", "default", _OPENED})
# Owned here with the file, unlike NODRILL_CONTRACT, which gates this import and lives in _debug.
_ENTRY_VAR = "NODRILL_CONTRACT_ENTRY"
_RUN_VAR = "NODRILL_CONTRACT_RUN"


def _declared(value: str) -> frozenset[str]:
    """Read NODRILL_CONTRACT_ENTRY, whose value is rendered keys separated by commas.

    A key holding a comma cannot be named this way, which is the price of a
    spelling somebody types into a CI file by hand.
    """
    return frozenset(entry for entry in (part.strip() for part in value.split(",")) if entry)


def _new_run() -> str:
    """Mint an id for this run, so a directory reused tomorrow does not merge into today."""
    return f"{time.time_ns()}-{uuid.uuid4().hex}"


def _render(reads: _Reads) -> str:
    """Render a contract, sorted so the file is a property of the run and not of its order."""
    lines = [_HEADER, *(_GAP.join(read) for read in sorted(reads))]
    return "".join(f"{line}{_NEWLINE}" for line in lines)


def _visible(reads: _Reads) -> _Reads:
    """Drop the opened row of a boundary that went on to read, since its reads already say so.

    What survives is the boundary a run opened and read nothing under, which
    is a fact about that entry point and not the absence of one.
    """
    read = {entry for entry, verb, _ in reads if verb != _OPENED}
    return {fact for fact in reads if fact[1] != _OPENED or fact[0] not in read}


def _refuse(source: str, saw: str, expected: str) -> ValueError:
    """Build the one refusal, so a caller can say which file and what it expected."""
    return ValueError(
        f"{source} is not a nodrill contract this version reads. {expected}, found {saw!r}"
    )


def _parse(text: str, source: str) -> _Reads:
    """Read a contract or a shard back, refusing anything this reader does not know."""
    lines = text.splitlines()
    if not lines or lines[0] != _HEADER:
        opening = lines[0] if lines else "an empty file"
        raise _refuse(source, opening, f"Expected {_HEADER!r} on the first line")
    found: _Reads = set()
    for number, line in enumerate(lines[1:], start=2):
        fields = line.split(_GAP)
        if len(fields) != 3:  # noqa: PLR2004
            raise _refuse(source, line, f"Expected three fields on line {number}")
        entry, verb, key = fields
        if verb not in _VERBS:
            raise _refuse(source, verb, f"Expected one of {sorted(_VERBS)} on line {number}")
        found.add((entry, verb, key))
    return found


def _write(target: Path, text: str) -> None:
    """Write one file of the format, with nothing about it left to the platform."""
    target.write_text(text, encoding=_ENCODING, newline=_NEWLINE)


def _dump(directory: str, run: str, reads: _Reads) -> None:
    """Write this process's records into its own shard of the run, then forget them.

    Forgetting is what makes a second call a no-op, which matters because a
    multiprocessing worker is finalized as well as registered at exit.  Taken
    before the write, so a thread still recording during shutdown cannot
    change the set the render is walking.
    """
    if not reads:
        return
    facts = set(reads)
    reads.clear()
    target = Path(directory)
    # The run first so a merge can group by it, then one token, since rpartition recovers the run.
    shard = target / f"{run}-{uuid.uuid4().hex}{_SUFFIX}"
    try:
        target.mkdir(parents=True, exist_ok=True)
        _write(shard, _render(facts))
    except OSError as error:
        # A message rather than a traceback out of an exit hook, which exits 0 either way.
        sys.stderr.write(f"nodrill: cannot record to {directory}, {error.strerror}\n")


def _merge(directory: Path) -> tuple[_Reads, int, int]:
    """Read the newest run in a directory, and say how many shards it left behind.

    A directory reused across runs holds both, and a contract built from
    yesterday's reads describes a program that no longer exists.
    """
    shards = sorted(directory.glob(f"*{_SUFFIX}"))
    if not shards:
        return set(), 0, 0
    runs: dict[str, list[Path]] = {}
    for shard in shards:
        runs.setdefault(shard.name.rpartition("-")[0], []).append(shard)
    # By when a run last wrote rather than by its id, since a run id may be one a CI system chose.
    current = max(runs.values(), key=lambda group: max(shard.stat().st_mtime for shard in group))
    found: _Reads = set()
    for shard in current:
        found |= _parse(shard.read_text(encoding=_ENCODING), str(shard))
    return found, len(current), len(shards) - len(current)


def _summary(reads: _Reads, shards: int, stale: int) -> str:
    """Say what the contract rests on, since a guarantee that overstates itself is worse than none.

    The figures are what this stage can honestly own, which is what a run
    observed rather than what a tree contains.
    """
    entries = len({entry for entry, _, _ in reads} - {_NO_ENTRY})
    said = (
        f"nodrill: {_counted(len(reads), 'fact')} under "
        f"{_counted(entries, 'entry point')}, "
        f"recorded from {_counted(shards, 'process', 'processes')}. "
        f"A contract is only as complete as the run that recorded it."
    )
    if stale:
        said += f" {_counted(stale, 'shard')} from an earlier run were left out."
    return said


def _unseen(reads: _Reads, declared: frozenset[str]) -> str | None:
    """Report a declared entry point no block opened, since a renamed key would go quiet.

    Read off the whole run rather than off the rendered contract, because a
    boundary that opened and read nothing is recorded and not rendered.
    """
    missing = sorted(declared - {entry for entry, _, _ in reads})
    if not missing:
        return None
    return f"nodrill: no block opened {', '.join(missing)}, named by {_ENTRY_VAR}"


def _say(message: str) -> None:
    """Put one diagnostic on standard error, so the artefact on standard output stays the file."""
    sys.stderr.write(f"{message}\n")


def _contract(source: str, target: str | None, declared: frozenset[str]) -> int:
    """Render the contract a recorded run left, to a file or to stdout."""
    directory = Path(source)
    if not directory.is_dir():
        _say(f"nodrill: nothing recorded at {source}, so there is no contract")
        return 1
    try:
        reads, shards, stale = _merge(directory)
    except (OSError, ValueError) as error:
        _say(f"nodrill: cannot read the run at {source}, {error}")
        return 1
    if not shards:
        _say(
            f"nodrill: {source} holds no shards, so nothing armed the recorder. "
            f"Run the suite with NODRILL_CONTRACT={source} first"
        )
        return 1
    facts = _visible(reads)
    text = _render(facts)
    if target is None:
        # Through the buffer, so neither the locale nor the platform edits the artefact.
        sys.stdout.flush()
        sys.stdout.buffer.write(text.encode(_ENCODING))
        sys.stdout.buffer.flush()
    else:
        try:
            _write(Path(target), text)
        except OSError as error:
            _say(f"nodrill: cannot write {target}, {error.strerror}")
            return 1
    _say(_summary(facts, shards, stale))
    unseen = _unseen(reads, declared)
    if unseen is not None:
        _say(unseen)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run one subcommand and return the code the interpreter should exit with."""
    # Deferred, so arming a process does not pay for the command line it will never run.
    import argparse  # noqa: PLC0415

    from . import __version__  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        prog="python -m nodrill",
        description="Record and review what each entry point reads out of the context.",
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"nodrill {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    contract = commands.add_parser(
        "contract",
        help="render the contract a run recorded under NODRILL_CONTRACT",
        description=(
            "Run the suite with NODRILL_CONTRACT set to a directory, then render what it "
            "recorded into a file a pull request can review."
        ),
        allow_abbrev=False,
    )
    contract.add_argument(
        "--from",
        dest="source",
        required=True,
        metavar="DIR",
        help="the directory NODRILL_CONTRACT named during the run",
    )
    contract.add_argument(
        "--write",
        dest="target",
        metavar="FILE",
        help="the contract file to write, where the default is stdout",
    )
    args = parser.parse_args(argv)
    return _contract(args.source, args.target, _declared(os.environ.get(_ENTRY_VAR, "")))
