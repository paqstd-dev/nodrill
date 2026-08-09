.. _topics-ambient:

The ambient context
===================

``nodrill.context`` is an attribute namespace with no scope: set an attribute anywhere, read it anywhere below, isolated per thread and per asyncio task.
It is the cheap option for breadcrumbs, and it is deliberately weaker than a provider.

.. contents::
   :local:
   :depth: 2

Setting and reading
-------------------

.. code-block:: python

   from nodrill import context

   def middleware(request):
       context.request_id = request.headers["X-Request-Id"]
       return handle(request)

   def log(message: str) -> None:
       print(f"[{context.request_id}] {message}")

There is no block, no key, and no registration.
Any attribute name works, and the value is visible to everything that runs afterwards in the same thread or task.

Deleting works too, and ``in`` tests membership without raising:

.. code-block:: python

   if "request_id" in context:
       del context.request_id

Reading a name that was never set raises :exc:`AttributeError` listing the names that are currently set, so ``getattr(context, "user", None)`` and ``hasattr`` behave the way they do on any object.

Isolation
---------

The namespace is backed by its own :class:`~contextvars.ContextVar`, which gives it the standard-library isolation rules:

Threads start empty.
   A new thread sees none of the values set in the thread that started it, because contextvars are not inherited across ``Thread.start()``.
   :doc:`concurrency` covers propagating them on purpose.

Tasks inherit a snapshot and diverge.
   ``asyncio.create_task`` copies the current context, so the task sees what was set before it started, and anything it sets afterwards stays inside it.
   Sibling tasks never see each other's writes.

Writes are unscoped
-------------------

This is the property that decides when to use it.
Nothing restores an ambient value at the end of a block:

.. code-block:: python

   def handler():
       context.tenant = "acme"          # no scope, no restore

   handler()
   context.tenant                       # still "acme"

In a pooled worker thread, that means a value set while handling one job is still there for the next job the same thread picks up.
For a request id that is harmless noise; for an authenticated tenant it is a data leak.

The rule of thumb:

Use ``provider`` when the value belongs to a scope.
   Requests, transactions, tenants, anything whose lifetime is a block.
   Exit cleans up for you, even on an exception.

Use ``context`` for what would otherwise be a :class:`threading.local`.
   Correlation ids, a debug flag, the name of the current test.
   Losing or over-keeping one is a nuisance, not a bug.

Adding a scope to the ambient namespace was considered and rejected: it would just be ``provider`` again, with a second spelling.

Why it is attribute-only
------------------------

The object exposes nothing but dunder methods.
There is no ``context.get``, no ``context.pop``, no ``context.setdefault``.

That is not minimalism for its own sake.
Flask's ``g`` carries ``g.get`` and ``g.pop``, and a user whose value happens to be called ``get`` collides with the API.
Every public method on an attribute bag is a name your application can never use, and the collision cannot be fixed after release.
Keeping the surface empty means every attribute name in Python is yours.

Per-class fallbacks are a separate feature for the same reason; they live in :func:`~nodrill.set_default`, not on this object.

.. seealso::

   :doc:`providers` for scoped values.
   :doc:`concurrency` for what crosses a thread boundary.
