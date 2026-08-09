"""Scoped context for Python call trees.

provider() puts values on the current context and use() reads them anywhere
below, without threading them through intermediate signatures.  Built on
contextvars, so lookups are thread-safe and asyncio-task-safe.
"""

from ._ambient import context
from ._concurrency import Executor, wrap
from ._core import Namespace, active, isolate, provider, set_default, use
from ._errors import FrozenContextError, NoProviderError
from ._inject import FromCtx, from_ctx, inject, injected

__version__ = "0.1.0"

__all__ = [
    "Executor",
    "FromCtx",
    "FrozenContextError",
    "Namespace",
    "NoProviderError",
    "__version__",
    "active",
    "context",
    "from_ctx",
    "inject",
    "injected",
    "isolate",
    "provider",
    "set_default",
    "use",
    "wrap",
]
