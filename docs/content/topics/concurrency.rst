.. _topics-concurrency:

Threads, tasks and other boundaries
===================================

nodrill stores everything in :mod:`contextvars`, so its concurrency behaviour is the standard library's behaviour.
That means asyncio needs nothing from you and plain threads need two helpers.
A boundary that ends the process ends what :mod:`contextvars` can do, and the last section is about that one.

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

Four properties are worth stating plainly.

Awaits do not lose context.
   The lookup after an ``await`` sees the same providers as the one before it.

Sibling tasks are isolated.
   ``gather`` over three tenants gives three separate registries.
   Interleaving on one event loop changes nothing, because no task mutates another's state.

A task keeps what it had at creation.
   A task created inside a provider block still sees that provider after the block has exited in the parent, since it holds its own snapshot.

A block held by a generator has to be closed where it was iterated.
   An abandoned async generator is finalized through a task of its own, so the block inside it closes from a context that never held it, cannot unwind, and leaves its value behind in the caller's.
   Wrap the iteration in :func:`contextlib.aclosing`, or open the block around the loop rather than inside the generator.
   :exc:`~nodrill.OrphanedProviderWarning` reports it when it happens.

There is no nodrill-specific helper for asyncio, and that last shape is the one to know about.

The third property is the one ``sealed=True`` changes, and deliberately.
A sealed value travels into the task or the thread like any other, and it stops working when the block exits in the parent, wherever the work has got to.
That is the point for a value the parent is about to return to a pool, and it is a report that can land on work that was still legitimately running, so seal a scope whose lifetime the background work is meant to respect and leave it unsealed where the work is meant to outlive it.
:ref:`topics-providers-sealed` covers the rest of what it does and does not catch.

Plain threads
-------------

:class:`threading.Thread` does not inherit contextvars.
A thread started inside a provider block starts with an empty context.

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

Everything else about it is inherited, the constructor arguments, ``map``, ``shutdown`` and the context-manager protocol.
``map`` goes through ``submit``, so it propagates the context as well.

Each task runs under its own copy, so a worker's writes never leak into another task or back to the submitter.
Submitting from inside different provider blocks gives each task the scope it was submitted from.

Choosing between them
~~~~~~~~~~~~~~~~~~~~~

Use ``Executor`` for pools.
Use ``wrap`` for a callable you hand to something you do not control, a bare ``Thread``, a timer, a third-party scheduler, or a C extension's callback slot.

Both are thin.
``wrap`` replays a snapshot into a fresh :class:`~contextvars.Context` on every call.
``Executor.submit`` takes a fresh :func:`~contextvars.copy_context` per task and runs the callable inside it.
The README's cost table prices that per-call replay, for code that puts a wrapped callable in a hot loop.

.. _topics-beyond-one-process:

Beyond one process
------------------

A thread and a task share memory with you, which is the only reason ``wrap`` and ``Executor`` can hand over the objects themselves.
A process pool, a Celery worker, an outbound HTTP call and a message on a queue share nothing, so nothing can be handed over.
What crosses such a boundary is a copy, in a format both sides can read, and that is what :func:`~nodrill.export` and :func:`~nodrill.adopt` produce and consume.

.. code-block:: python

   with provider("trace", request_id=rid, tenant="acme"):
       payload = export("trace")             # a plain dict, ready for json.dumps
       queue.enqueue(job, nodrill_ctx=payload)

   # in the worker, which shares nothing with the code above
   with adopt(payload):
       handle()                              # use("trace").request_id

Four rules make that safe.

Nothing travels unless you name it.
   ``export("trace")`` carries the ``trace`` provider and no other, so a provider holding a database session cannot be serialised by accident.
   The list lives at the boundary, on the line where you are already thinking about what goes over the wire, rather than as a mark spread across the providers.

Values cross as themselves or not at all.
   A value has to be exactly a ``str``, ``int``, ``float``, ``bool``, ``None``, ``list`` or ``dict``, all the way down.
   A :class:`~uuid.UUID` request id is refused rather than quietly converted, because a value that leaves as a ``UUID`` and arrives as a ``str`` is a bug waiting for the first consumer that compares the two.
   Write ``str(request_id)`` and both sides agree, or register a codec that does it for you.

Class keys stay here.
   ``use(RequestScope)`` needs the class, and reconstructing it on the other side means importing it there, which is a different problem and usually a different codebase.
   A string namespace is what actually crosses a boundary in practice, so that is what ``export`` takes.

An adopted payload is input.
   ``adopt`` checks that a payload is shaped like an envelope and that its values are JSON-shaped.
   It cannot check that they are true.
   A context value normally reads like something this process decided about itself, and after an adopt it is not, so an adopted ``tenant`` is authorised exactly the way a ``tenant`` from a query string is authorised.

The envelope carries its own version, and :func:`~nodrill.adopt` refuses one it does not read, naming both numbers and the side to move.
That matters the first time a producer is deployed a release ahead of its consumer, which on a rolling deploy is a window rather than an instant.
Deploy the consumers first and every message in that window carries a version its consumer already reads.
Deploy the producers first and every in-flight message from a new producer is one an old consumer drops, so a consumer catching :exc:`~nodrill.EnvelopeVersionError` should log the drop rather than pass it.
The number has been ``1`` since the feature shipped and only a change to the envelope itself would move it, never a change to what your namespaces hold.

Where to call :func:`~nodrill.export` is a decision worth making once.
It reads the nearest enclosing provider at the moment it is called, so exporting at the request boundary and enqueuing later captures the scope of the export rather than of the enqueue, which is the wrong one as soon as a layer in between opens its own ``trace``.
Export where the values are the ones you mean to send, and export once outside a loop rather than per item, since the walk copies every container it is given.

The copy is also what bounds the size.
The envelope carries what you name, all of it, so a namespace holding a feature-flag dict crosses in full on every message.
A namespace that does not fit is a namespace that should be a lookup key rather than a payload, and that holds on a queue as much as it holds on the 8192-byte header the HTTP recipe writes to.

What a namespace holds is not always a scalar, and :func:`~nodrill.set_codec` is the hook for the rest.
It registers one pair, ``dump`` on the way out and ``load`` on the way in, process wide and at startup, since both ends of a boundary have to agree on the format.
Whatever ``dump`` returns is checked like any other value, so a codec widens what you may provide without widening what goes on the wire, and the envelope is still the JSON that the queue and header recipes need.
The tagging is the codec's business rather than the library's, because a namespace on the wire has forgotten every type it held, and only you know whether the payload should carry the type or the consumer already knows it.

Nothing here is a framework integration.
The envelope is a dict, the boundary is yours, and the recipes below connect the two for a process pool, a queue and a W3C ``baggage`` header.

.. seealso::

   :doc:`/content/howto/fan-out-with-asyncio` and :doc:`/content/howto/run-work-in-threads` for complete programs.
   :doc:`/content/howto/carry-context-into-a-process-pool`, :doc:`/content/howto/carry-context-onto-a-queue` and :doc:`/content/howto/carry-context-over-http` for the boundaries that end the process.
   :doc:`/content/howto/carry-an-object-across-a-boundary` for the values none of them can carry as they stand.
   :doc:`/content/howto/find-out-why-the-context-is-missing` for when the context did not arrive and the boundary that dropped it is not obvious.
