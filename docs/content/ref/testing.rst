.. _ref-testing:

Ambient context and testing
===========================

.. currentmodule:: nodrill

The unscoped namespace and the isolation helper.

context
-------

.. data:: context

   An ambient attribute namespace, isolated per thread and per asyncio task.

   Set an attribute anywhere, read it anywhere below.
   Backed by its own :class:`~contextvars.ContextVar`, so a new thread starts empty and a new task inherits a snapshot then diverges.

   .. code-block:: python

      context.request_id = "r-42"
      ...
      log(f"[{context.request_id}]")

   Writes are unscoped, so nothing restores them at the end of a block, and a value set in a pooled worker thread survives into that thread's next job.
   Use :func:`provider` for anything scoped.

   The object deliberately exposes no methods, only dunders, so no attribute name your application chooses can collide with the API.
   These are the supported operations.

   ``context.name``
      Read.
      Raises :exc:`AttributeError` listing the names currently set if it was never assigned.

   ``context.name = value``
      Write.

   ``del context.name``
      Delete.
      Raises :exc:`AttributeError` if the name is not set.

   ``"name" in context``
      Membership test, without raising.

   ``iter(context)``, ``len(context)``
      The names currently set, in sorted order, and how many there are.
      Dunders cannot collide with an application's attribute names, which is why these are safe to have and ``keys()`` is not.

isolate
-------

.. function:: isolate()

   Run a block against fresh context state, restoring the outer state on exit.
   Returns a context manager.

   Inside the block no providers are active and the ambient namespace is empty.
   Registrations made with :func:`set_default` inside the block are rolled back on exit.
   Registrations made outside stay visible inside, since they are configuration rather than state.
   Refs built with :func:`ref` inside the block are rolled back the same way, so a path one test made up on purpose is not a path the next test's :func:`resolve_refs` has to import.

   Intended for test fixtures.

   .. code-block:: python
      :caption: conftest.py

      import nodrill
      import pytest


      @pytest.fixture(autouse=True)
      def _clean_context():
          with nodrill.isolate():
              yield
