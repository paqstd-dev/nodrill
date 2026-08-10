.. _ref-concurrency:

Threads
=======

.. currentmodule:: nodrill

:class:`threading.Thread` does not inherit context, so these two helpers carry it across the boundary. asyncio needs neither: tasks snapshot the context natively.

wrap
----

.. function:: wrap(fn)

   Bind ``fn`` to a snapshot of the context active when ``wrap`` was called, and return a callable with the same signature.

   :raises TypeError: ``fn`` is a coroutine or async-generator function.
      Calling one only builds the coroutine, whose body then resumes in the caller's context, so the snapshot would be silently dropped.
      asyncio needs no wrapping, since a task created inside a provider block snapshots the context itself.

   The snapshot is taken at ``wrap()`` time, not at call time.
   Wrapping at import time binds import-time state.

   Each invocation replays the snapshot into a fresh :class:`~contextvars.Context`, so the wrapped callable is safe to call concurrently, and any contextvar writes the callee makes stay inside that call.

   The wrapper is built with :func:`functools.wraps`, so ``__name__``, ``__doc__`` and ``__wrapped__`` carry through.

   .. code-block:: python

      with provider(Config()):
          Thread(target=wrap(job)).start()

Executor
--------

.. class:: Executor

   A :class:`~concurrent.futures.ThreadPoolExecutor` subclass whose tasks run under the context active when they were submitted.

   The constructor, ``shutdown``, and the context-manager protocol are inherited unchanged.
   Only :meth:`~concurrent.futures.Executor.submit` is overridden, so :meth:`~concurrent.futures.Executor.map` propagates context as well.

   Each task runs under its own :func:`~contextvars.copy_context` snapshot, so a worker's writes never leak into another task or back to the submitter.

   .. code-block:: python

      with provider(Config()), Executor(max_workers=4) as pool:
          results = list(pool.map(job, range(10)))
