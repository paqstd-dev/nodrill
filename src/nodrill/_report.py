"""Notes naming the scopes an exception passed through on its way out.

A provider block exited by an exception attaches a PEP 678 note describing
what it provided, and re-raises the same object with __notes__ the only thing
about it that changed.  Nothing here runs until an exception is already
leaving a block, which is why the feature costs the lookup path nothing.

An exception that refuses the note keeps its own failure, and so does one
whose value has a broken __repr__, since the failure on its way out is always
worth more than the note describing it.

BaseException.add_note is 3.11 and up, so the last step of the note path is a
callable bound once at import.  On 3.10 it drops the finished note rather than
emulating one, since rewriting args breaks equality and pickling and chaining a
synthetic exception rewrites the traceback this feature exists to preserve.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from ._errors import _describe_key, _Key

# The string a log aggregator greps for, so it is a contract rather than a wording.
_PREFIX = "nodrill scope: "

# Its own word after the shared one, since a boundary call is not a scope.
_CODEC_PREFIX = "nodrill codec: "

# Wide enough for an ordinary dataclass, narrow enough to keep a traceback readable.
_WIDTH = 200

# ASCII, since a note travels through log pipelines with unknown encodings.
_MARKER = "..."

_UNSUPPORTED = (
    "nodrill.annotate_exceptions() has no effect on Python 3.10, where "
    "BaseException.add_note does not exist. Nothing is attached and nothing else changes, "
    "so the call can stay in place until the interpreter is 3.11 or newer."
)


def _drop_note(_exc: BaseException, _note: str) -> None:
    """Discard a note, since BaseException.add_note is 3.11 and up."""


# Bound once, so the exception path carries no version test and no attribute probe.
_add_note: Callable[[BaseException, str], None] = getattr(BaseException, "add_note", _drop_note)


class _State:
    """The annotation switch, in one object so no function needs a global statement."""

    __slots__ = ("enabled",)

    def __init__(self) -> None:
        self.enabled = False


_state = _State()


def _scope_note(key: _Key, value: Any) -> str:
    """Return the one-line bounded note text for one provider block."""
    try:
        text = repr(value)
    # A broken __repr__ must not replace the exception already in flight.
    except Exception as exc:  # noqa: BLE001
        # The message is as likely to hold the secret as the repr was, so only the type is named.
        return f"{_PREFIX}<unprintable {_describe_key(key)}, repr raised {type(exc).__name__}>"
    # A note prints at column zero with no continuation, so a second line would lose the prefix.
    flat = " ".join(text.splitlines())
    if len(flat) > _WIDTH:
        flat = flat[: _WIDTH - len(_MARKER)] + _MARKER
    return _PREFIX + flat


def _annotate(exc: BaseException, key: _Key, value: Any, *, annotate: bool | None) -> None:
    """Attach a note naming the scope this exception is leaving."""
    if annotate is None:
        annotate = _state.enabled
    # A BaseException that is not an Exception is control flow rather than a failure.
    if annotate and isinstance(exc, Exception):
        # An exception that refuses the note keeps its own failure, which is the one that matters.
        with suppress(Exception):
            _add_note(exc, _scope_note(key, value))


def _boundary_note(exc: BaseException, where: str) -> None:
    """Attach a note naming the boundary call a codec raised inside."""
    # The same switch as a scope note, though this one names a call rather than a value.
    if _state.enabled and isinstance(exc, Exception):
        with suppress(Exception):
            _add_note(exc, f"{_CODEC_PREFIX}raised during {where}")


def annotate_exceptions(*, enabled: bool = True) -> None:
    """Attach the scope to every exception leaving a provider block, process wide.

    An exception passing out of a provider block gains a note naming what
    that block provided, and comes out as the same object with __notes__ the
    only thing about it that changed.  Nested blocks each add their own as it
    climbs, innermost first.  One block decides for itself with
    provider(..., annotate=True) or annotate=False, and enabled=False turns
    the switch off again.  Rendering a note calls the value's repr while the
    block is unwinding, so whatever a provider holds can be printed into a
    traceback, and a value carrying a secret should hide it in its own repr.
    On Python 3.10 this warns and does nothing, since exception notes are
    3.11 and up.
    """
    if enabled and _add_note is _drop_note:
        warnings.warn(_UNSUPPORTED, RuntimeWarning, stacklevel=2)
    _state.enabled = enabled


__all__ = ["annotate_exceptions"]
