"""Scoped context for Python call trees.

provider() puts values on the current context and use() reads them anywhere
below, without threading them through intermediate signatures.  Built on
contextvars, so lookups are thread-safe and asyncio-task-safe.
"""

from ._ambient import context
from ._concurrency import Executor, wrap
from ._core import Namespace, active, isolate, provider, set_default, use
from ._debug import debug, explain
from ._errors import (
    FrozenContextError,
    KeyResolutionError,
    NoProviderError,
    UnusedProviderWarning,
)
from ._inject import FromCtx, from_ctx, inject, injected
from ._lazy import lazy
from ._refs import ref, resolve_refs
from ._report import annotate_exceptions

__version__ = "0.3.0"
"""The installed version, as a string."""

__all__ = [
    "Executor",
    "FromCtx",
    "FrozenContextError",
    "KeyResolutionError",
    "Namespace",
    "NoProviderError",
    "UnusedProviderWarning",
    "__version__",
    "active",
    "annotate_exceptions",
    "context",
    "debug",
    "explain",
    "from_ctx",
    "inject",
    "injected",
    "isolate",
    "lazy",
    "provider",
    "ref",
    "resolve_refs",
    "set_default",
    "use",
    "wrap",
]
