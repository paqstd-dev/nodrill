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

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

_Reads = set[tuple[str, str, str]]

# The one format, carried by a shard and by a contract alike, so there is one reader.
_HEADER = "# nodrill contract 1"
_SUFFIX = ".shard"
# A tab, because repr escapes one and a key may legally hold two spaces in a row.
_GAP = "\t"
# Written and diffed on machines nobody here chose, so the encoding is named rather than guessed.
_ENCODING = "utf-8"
# The verbs, in the order a reader cares about them, since anything but requires is worth a look.
_VERBS = ("requires", "set_default", "default")
# Named here rather than in _debug, so one module owns the spelling of both variables.
_ENTRY_VAR = "NODRILL_CONTRACT_ENTRY"


def _declared(value: str) -> frozenset[str]:
    """Read NODRILL_CONTRACT_ENTRY, whose value is rendered keys separated by commas.

    A key holding a comma cannot be named this way, which is the price of a
    spelling somebody types into a CI file by hand.
    """
    return frozenset(entry for entry in (part.strip() for part in value.split(",")) if entry)


def _new_run() -> str:
    """Mint an id for this run, so a directory reused tomorrow does not merge into today."""
    return f"{time.time_ns()}-{uuid.uuid4().hex}"


def _fact(read: tuple[str, str, str]) -> str:
    """Render one recorded fact as the file's one line shape."""
    return _GAP.join(read)


def _render(reads: _Reads) -> str:
    """Render a contract, sorted so the file is a property of the run and not of its order."""
    lines = [_HEADER, *(_fact(read) for read in sorted(reads))]
    return "".join(f"{line}\n" for line in lines)


def _parse(text: str, source: str) -> _Reads:
    """Read a contract or a shard back, refusing a version this reader does not know."""
    lines = text.splitlines()
    if not lines or lines[0] != _HEADER:
        opening = lines[0] if lines else "an empty file"
        raise ValueError(
            f"{source} is not a nodrill contract this version reads. "
            f"Expected {_HEADER!r} on the first line and found {opening!r}"
        )
    found: _Reads = set()
    for line in lines[1:]:
        entry, verb, key = line.split(_GAP)
        found.add((entry, verb, key))
    return found


def _dump(directory: str, run: str, reads: _Reads) -> None:
    """Write this process's records into its own shard of the run, then forget them.

    Forgetting is what makes a second call a no-op, which matters because a
    multiprocessing worker is finalized as well as registered at exit.
    """
    if not reads:
        return
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    # The run first so a merge can group by it, then pid and a token, since a pid is reused.
    shard = target / f"{run}-{uuid.uuid4().hex}{_SUFFIX}"
    shard.write_text(_render(reads), encoding=_ENCODING)
    reads.clear()


def _merge(directory: str) -> tuple[_Reads, int, int]:
    """Read the newest run in a directory, and say how many shards it left behind.

    A directory reused across runs holds both, and a contract built from
    yesterday's reads describes a program that no longer exists.
    """
    shards = sorted(Path(directory).glob(f"*{_SUFFIX}"))
    if not shards:
        return set(), 0, 0
    newest = max(shard.name.rpartition("-")[0] for shard in shards)
    current = [shard for shard in shards if shard.name.startswith(f"{newest}-")]
    found: _Reads = set()
    for shard in current:
        found |= _parse(shard.read_text(encoding=_ENCODING), str(shard))
    return found, len(current), len(shards) - len(current)


def _counted(count: int, singular: str, plural: str) -> str:
    """Render a count and its noun, since every figure below reads as a sentence."""
    return f"{count} {singular if count == 1 else plural}"


def _summary(reads: _Reads, shards: int, stale: int) -> str:
    """Say what the contract rests on, since a guarantee that overstates itself is worse than none.

    The figures are what this stage can honestly own, which is what a run
    observed rather than what a tree contains.
    """
    entries = len({entry for entry, _, _ in reads})
    said = (
        f"nodrill: {_counted(len(reads), 'fact', 'facts')} under "
        f"{_counted(entries, 'entry point', 'entry points')}, "
        f"recorded from {_counted(shards, 'process', 'processes')}. "
        f"A contract is only as complete as the run that recorded it."
    )
    if stale:
        said += f" {_counted(stale, 'shard', 'shards')} from an earlier run were left out."
    return said


def _unseen(reads: _Reads, declared: frozenset[str]) -> str | None:
    """Report a declared entry point no block opened, since a renamed key would go quiet."""
    missing = sorted(declared - {entry for entry, _, _ in reads})
    if not missing:
        return None
    return f"nodrill: no block opened {', '.join(missing)}, named by NODRILL_CONTRACT_ENTRY"


def _contract(source: str, target: str | None, declared: frozenset[str]) -> int:
    """Render the contract a recorded run left, to a file or to stdout."""
    directory = Path(source)
    if not directory.is_dir():
        sys.stderr.write(f"nodrill: nothing recorded at {source}, so there is no contract\n")
        return 1
    reads, shards, stale = _merge(source)
    if not shards:
        sys.stderr.write(
            f"nodrill: {source} holds no shards, so nothing armed the recorder. "
            f"Run the suite with NODRILL_CONTRACT={source} first\n"
        )
        return 1
    text = _render(reads)
    if target is None:
        sys.stdout.write(text)
    else:
        try:
            Path(target).write_text(text, encoding=_ENCODING)
        except OSError as error:
            sys.stderr.write(f"nodrill: cannot write {target}, {error.strerror}\n")
            return 1
    sys.stderr.write(f"{_summary(reads, shards, stale)}\n")
    unseen = _unseen(reads, declared)
    if unseen is not None:
        sys.stderr.write(f"{unseen}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run one subcommand and return the code the interpreter should exit with."""
    parser = argparse.ArgumentParser(
        prog="python -m nodrill",
        description="Record and review what each entry point reads out of the context.",
        allow_abbrev=False,
    )
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
