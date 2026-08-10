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
        else:
            # A class opens its own provider.  Anything else naming one, a ref
            # included, is a key rather than a constructor.
            opener = f"{wanted}(...)" if isinstance(self.key, type) else f"instance, key={wanted}"
            parts.append(
                f"Hint: did you forget `with provider({opener})`? "
                f"A fallback can be registered with "
                f"`set_default({wanted}, ...)`."
            )
        return " ".join(parts)


class KeyResolutionError(LookupError):
    """Raised when a ref() key cannot be resolved to what its path names.

    Carries the import path as an attribute.
    """

    def __init__(self, path: str, problem: str) -> None:
        self.path = path
        super().__init__(f"ref({path!r}): {problem}")


class FrozenContextError(AttributeError):
    """Raised when writing to a context object provided with frozen=True."""
