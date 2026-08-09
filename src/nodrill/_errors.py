"""Exceptions raised by nodrill."""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from typing import Any


def _describe_key(key: Any) -> str:
    return repr(key) if isinstance(key, str) else getattr(key, "__qualname__", repr(key))


class NoProviderError(LookupError):
    """Raised by use() when no provider is active for the requested key.

    Carries the requested key and the active keys as attributes.
    """

    def __init__(self, key: Any, active_keys: Iterable[Any] = ()) -> None:
        self.key = key
        self.active_keys = tuple(active_keys)
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        wanted = _describe_key(self.key)
        parts = [f"use({wanted}): no active provider for {wanted}."]
        if self.active_keys:
            listed = ", ".join(sorted(_describe_key(k) for k in self.active_keys))
            parts.append(f"Active providers: {listed}.")
        else:
            parts.append("No providers are active.")
        if isinstance(self.key, str):
            names = [k for k in self.active_keys if isinstance(k, str)]
            close = difflib.get_close_matches(self.key, names, n=1)
            if close:
                parts.append(f"Did you mean {close[0]!r}?")
            parts.append(f"Hint: did you forget `with provider({self.key!r})`?")
        elif isinstance(self.key, type):
            parts.append(
                f"Hint: did you forget `with provider({wanted}(...))`? "
                f"A fallback can be registered with "
                f"`set_default({wanted}, ...)`."
            )
        # Any other key reaches this only when raised by hand: use() rejects it.
        # Describe it and skip the hints, rather than fail inside the constructor.
        return " ".join(parts)


class FrozenContextError(AttributeError):
    """Raised when writing to a context object provided with frozen=True."""
