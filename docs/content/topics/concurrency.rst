.. _topics-concurrency:

Threads and asyncio
===================

nodrill stores everything in :mod:`contextvars`, so its concurrency behaviour is the standard library's behaviour.
That means asyncio needs nothing from you and plain threads need two helpers.

.. contents::
   :local:
   :depth: 2

asyncio
-------

``asyncio.create_task`` copies the current context when the task is created.
A task therefore runs under the providers that were active at creation time, and anything it sets afterwards stays inside it.

.. code-block:: python

   async def handle(tenant: str) -> str:
       with provider(Tenant(name=tenant)):
           return await query("SELECT 1")

   async def query(sql: str) -> str:
       await asyncio.sleep(0)           # siblings interleave here
       return f"{use(Tenant).name}: {sql}"

   await asyncio.gather(handle("acme"), handle("globex"))

The three properties worth stating plainly:

Awaits do not lose context.
   The lookup after an ``await`` sees the same providers as the one before it.

Sibling tasks are isolated.
   ``gather`` over three tenants gives three separate registries.
   Interleaving on one event loop changes nothing, because no task mutates another's state.

A task keeps what it had at creation.
   A task created inside a provider block still sees that provider after the block has exited in the parent, since it holds its own snapshot.

There is nothing to configure and no nodrill-specific helper for asyncio.

Plain threads
-------------

:class:`threading.Thread` does not inherit contextvars.
A thread started inside a provider block starts with an empty context:

.. code-block:: python

   with provider(Config()):
       t = Thread(target=job)           # job() raises NoProviderError
       t.start()

This is a documented property of the standard library, not a nodrill quirk, and it applies to the ambient :data:`~nodrill.context` namespace too.
Two helpers propagate the context on purpose.

wrap
~~~~

``wrap(fn)`` binds a callable to a snapshot of the context active where ``wrap`` was called.

.. code-block:: python

   from nodrill import provider, wrap

   with provider(Config()):
       Thread(target=wrap(job)).start()

Note *where*, not *when*.
The snapshot is taken at ``wrap()`` time, so wrapping at import time binds import-time state, which is usually empty.
Wrap inside the scope you want to carry.

Each invocation of the wrapped callable runs under a fresh copy of the snapshot.
That makes the result safe to call concurrently, and it keeps any writes the callee makes local to that call rather than shared between them.

Executor
~~~~~~~~

``Executor`` is a :class:`~concurrent.futures.ThreadPoolExecutor` subclass whose tasks see the context that was active when they were submitted.

.. code-block:: python

   from nodrill import Executor, provider

   with provider(Config()), Executor(max_workers=4) as pool:
       futures = [pool.submit(job, n) for n in range(10)]
       results = [f.result() for f in futures]

Everything else about it is inherited: the constructor arguments, ``map``, ``shutdown``, the context-manager protocol.
``map`` goes through ``submit``, so it propagates the context as well.

Each task runs under its own copy, so a worker's writes never leak into another task or back to the submitter.
Submitting from inside different provider blocks gives each task the scope it was submitted from.

Choosing between them
~~~~~~~~~~~~~~~~~~~~~

Use ``Executor`` for pools.
Use ``wrap`` for a callable you hand to something you do not control: a bare ``Thread``, a timer, a third-party scheduler, a C extension's callback slot.

Both are thin.
``wrap`` replays a snapshot into a fresh :class:`~contextvars.Context` on every call; ``Executor.submit`` takes a fresh :func:`~contextvars.copy_context` per task and runs the callable inside it.
The README's cost table prices that per-call replay, for code that puts a wrapped callable in a hot loop.

.. seealso::

   :doc:`/content/howto/fan-out-with-asyncio` and :doc:`/content/howto/run-work-in-threads` for complete programs.
   :doc:`/content/howto/find-out-why-the-context-is-missing` for when the context did not arrive and it is not obvious which boundary dropped it.
