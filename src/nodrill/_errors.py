"""Exceptions raised by nodrill."""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from contextlib import suppress
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
        self,
        key: Any,
        active_keys: Iterable[Any] = (),
        diagnosis: str | None = None,
        *,
        offer_debug: bool = False,
    ) -> None:
        self.key = key
        self.active_keys: tuple[Any, ...] = tuple(active_keys)
        self.diagnosis = diagnosis
        # Never when debug mode is already on, or the hint names what the reader already did.
        self._offer_debug = offer_debug
        super().__init__(self._build_message())

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        return _reduced(self)

    def _subclassing_key(self) -> Any:
        """Return an active key that subclasses the wanted one, which exact keys do not answer."""
        if not isinstance(self.key, type):
            return None
        for active in self.active_keys:
            # A plain Protocol refuses the test, which is the case key= exists for.
            with suppress(TypeError):
                if isinstance(active, type) and issubclass(active, self.key):
                    return active
        return None

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
        elif (covered := self._subclassing_key()) is not None:
            parts.append(
                f"{_describe_key(covered)} is active and subclasses {wanted}, and keys "
                f"are exact. Register it under the base with "
                f"`provider(instance, key={wanted})`."
            )
        else:
            # A protocol cannot be instantiated, so it can only ever be named as a key.
            instantiable = isinstance(self.key, type) and not getattr(
                self.key, "_is_protocol", False
            )
            opener = f"{wanted}(...)" if instantiable else f"instance, key={wanted}"
            parts.append(
                f"Hint: did you forget `with provider({opener})`? "
                f"A fallback can be registered with "
                f"`set_default({wanted}, ...)`."
            )
        if self._offer_debug:
            parts.append(
                "Run under `with nodrill.debug():` to find out whether the value is "
                "open on another thread or task."
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


def _at_site(site: tuple[str, int]) -> str:
    return f"{site[0]}:{site[1]}"


class ExpiredScopeError(RuntimeError):
    """Raised when a value provided with sealed=True is used after its block exited.

    Carries the key, the operation that was attempted and the three sites,
    the one the block opened at, the one it exited at and the one the use
    ran at, as attributes.

    Deliberately not an AttributeError, unlike FrozenContextError, since a
    three-argument getattr would then swallow the expiry and hand back the
    default, which is the silent wrong value sealing exists to report.
    """

    def __init__(
        self,
        key: Any,
        operation: str,
        *,
        opened: tuple[str, int],
        exited: tuple[str, int],
        used: tuple[str, int],
    ) -> None:
        self.key = key
        self.operation = operation
        # Plain tuples, since the sites are documented as such and travel through a pickle.
        self.opened: tuple[str, int] = (opened[0], opened[1])
        self.exited: tuple[str, int] = (exited[0], exited[1])
        self.used: tuple[str, int] = (used[0], used[1])
        super().__init__(self._build_message())

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        return _reduced(self)

    def _build_message(self) -> str:
        where = f"opened at {_at_site(self.opened)}"
        # A with exits on its own line, so name the exit site only when something else closed it.
        if self.exited != self.opened:
            where += f" and exited at {_at_site(self.exited)}"
        return (
            f"{_describe_key(self.key)}.{self.operation} was used after its provider "
            f"block exited.\n"
            f"  {where}\n"
            f"  used here at {_at_site(self.used)}\n"
            f"Fix: do the work inside the block, or hand the later work a value of its "
            f"own, since a sealed value stops working the moment its block exits."
        )


class FrozenContextError(AttributeError):
    """Raised when writing to a context object provided with frozen=True."""


class UnusedProviderWarning(UserWarning):
    """Warned by debug(unused=True) when a block exits with nothing having read it."""


class OrphanedProviderWarning(UserWarning):
    """Warned when a block is closed from a context it did not open in.

    A token cannot be reset from a context it was not created in, so such
    a block never unwinds and its value is never taken back out of the
    context that holds it.
    """
