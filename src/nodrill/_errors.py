"""Exceptions raised by nodrill."""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from typing import Any

# What the registry is keyed by, once a ref() has resolved to the class it names.
_Key = str | type[Any]


def _describe_key(key: Any) -> str:
    return repr(key) if isinstance(key, str) else getattr(key, "__qualname__", repr(key))


def _rebuilt(
    cls: type[BaseException], args: tuple[Any, ...], state: dict[str, Any]
) -> BaseException:
    """Rebuild an exception from its parts, without the __init__ that built its message."""
    error = cls.__new__(cls)
    error.args = args
    error.__dict__.update(state)
    return error


def _reduced(error: BaseException) -> tuple[Any, tuple[Any, ...]]:
    """Return what pickle needs for an exception whose args hold a built message."""
    return (_rebuilt, (error.__class__, error.args, error.__dict__))


class NoProviderError(LookupError):
    """Raised by use() when no provider is active for the requested key.

    Carries the requested key, the active keys and, under debug mode, the
    diagnosis of where the value is, as attributes.
    """

    def __init__(
        self, key: Any, active_keys: Iterable[Any] = (), diagnosis: str | None = None
    ) -> None:
        self.key = key
        self.active_keys = tuple(active_keys)
        self.diagnosis = diagnosis
        super().__init__(self._build_message())

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        return _reduced(self)

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
            # A class opens its own provider, while anything else naming one is a key.
            opener = f"{wanted}(...)" if isinstance(self.key, type) else f"instance, key={wanted}"
            parts.append(
                f"Hint: did you forget `with provider({opener})`? "
                f"A fallback can be registered with "
                f"`set_default({wanted}, ...)`."
            )
        message = " ".join(parts)
        # Below the message rather than instead of it, so debug mode off reads as it always did.
        return f"{message}\n\n{self.diagnosis}" if self.diagnosis else message


class KeyResolutionError(LookupError):
    """Raised when a ref() key cannot be resolved to what its path names.

    Carries the import path as an attribute.
    """

    def __init__(self, path: str, problem: str) -> None:
        self.path = path
        super().__init__(f"ref({path!r}): {problem}")

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        return _reduced(self)


class EnvelopeVersionError(ValueError):
    """Raised by adopt() when a payload carries a version this release cannot read.

    Carries the payload's version and the supported one as attributes.
    """

    def __init__(self, version: int, supported: int) -> None:
        self.version = version
        self.supported = supported
        side = "ahead of" if version > supported else "behind"
        where = "here" if version > supported else "there"
        super().__init__(
            f"this nodrill reads envelope version {supported}, and the payload "
            f"carries version {version}. The producer is {side} this service, "
            f"so upgrade nodrill {where}"
        )

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        return _reduced(self)


class FrozenContextError(AttributeError):
    """Raised when writing to a context object provided with frozen=True."""


class UnusedProviderWarning(UserWarning):
    """Warned by debug(unused=True) when a block exits with nothing having read it."""
