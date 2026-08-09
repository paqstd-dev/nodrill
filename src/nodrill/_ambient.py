"""The ambient attribute namespace, backed by its own ContextVar.

Kept separate from the provider registry so use() and context.attr cannot
shadow each other.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextvars import ContextVar
from typing import Any

# Shared as a default and never mutated, since every write path copies first.
_EMPTY_AMBIENT: dict[str, Any] = {}
_ambient: ContextVar[dict[str, Any]] = ContextVar("nodrill_ambient", default=_EMPTY_AMBIENT)


class _AmbientContext:
    """Ambient attribute namespace backed by a ContextVar.

    Writes are unscoped and nothing restores them on block exit, so use
    provider() for request-scoped state.  Only dunders are defined, so no
    user attribute name can collide with the API.
    """

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        try:
            return _ambient.get()[name]
        except KeyError:
            available = ", ".join(sorted(_ambient.get())) or "none"
            raise AttributeError(
                f"nodrill.context has no attribute {name!r} (currently set: {available})"
            ) from None

    def __setattr__(self, name: str, value: Any) -> None:
        updated = dict(_ambient.get())
        updated[name] = value
        _ambient.set(updated)

    def __delattr__(self, name: str) -> None:
        current = _ambient.get()
        if name not in current:
            raise AttributeError(f"nodrill.context has no attribute {name!r}")
        updated = dict(current)
        del updated[name]
        _ambient.set(updated)

    def __contains__(self, name: str) -> bool:
        return name in _ambient.get()

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(_ambient.get()))

    def __len__(self) -> int:
        return len(_ambient.get())

    def __repr__(self) -> str:
        keys = ", ".join(sorted(_ambient.get()))
        return f"<nodrill.context [{keys}]>"


context = _AmbientContext()


__all__ = ["context"]
