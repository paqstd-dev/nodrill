"""Helpers for carrying context into threads.

threading.Thread does not inherit contextvars state: a thread started inside
a provider block sees no providers.  These helpers propagate the context
explicitly.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import Context, copy_context
from functools import wraps
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def wrap(fn: Callable[P, R]) -> Callable[P, R]:
    """Bind fn to a snapshot of the context active when wrap() was called.

    Each invocation runs under a fresh copy of the snapshot, so the result
    is safe to call concurrently and callee writes stay per-call.  The
    snapshot is taken at wrap() time: wrapping at import binds import-time
    state.  Async functions are rejected: asyncio propagates context itself.
    """
    if inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn):
        raise TypeError(
            "wrap() does not support async functions: running one only builds the "
            "coroutine, whose body then resumes in the caller's context, so the "
            "snapshot would be silently dropped. asyncio needs no wrapping at all: "
            "a task created inside a provider block snapshots the context by itself."
        )
    snapshot = tuple(copy_context().items())

    @wraps(fn)
    def bound(*args: P.args, **kwargs: P.kwargs) -> R:
        def restore_and_call() -> R:
            for var, value in snapshot:
                var.set(value)
            return fn(*args, **kwargs)

        return Context().run(restore_and_call)

    return bound


class Executor(ThreadPoolExecutor):
    """ThreadPoolExecutor whose tasks see the submit-time context.

    Each task runs under its own context copy, so worker-side writes never
    leak between tasks or back to the submitter.  map() inherits the
    behavior via submit().
    """

    def submit(self, fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> Future[R]:
        snapshot = copy_context()

        def run_in_snapshot() -> R:
            return snapshot.run(fn, *args, **kwargs)

        return super().submit(run_in_snapshot)


__all__ = ["Executor", "wrap"]
