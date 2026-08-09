.. _howto-replace-a-contextvar:

Replace a hand-rolled ContextVar
================================

If you already keep per-request state in :mod:`contextvars`, the migration is mechanical.
This page maps the hand-rolled pattern onto nodrill line by line.

The pattern being replaced
--------------------------

.. code-block:: python
   :caption: before

   import contextvars
   from contextlib import contextmanager

   _current_user: contextvars.ContextVar[User] = contextvars.ContextVar("current_user")
   _current_db: contextvars.ContextVar[Engine] = contextvars.ContextVar("current_db")


   @contextmanager
   def request_scope(user: User, db: Engine):
       user_token = _current_user.set(user)
       db_token = _current_db.set(db)
       try:
           yield
       finally:
           _current_db.reset(db_token)
           _current_user.reset(user_token)


   def current_user() -> User:
       return _current_user.get()       # bare LookupError on a miss

Each value needs a module-level variable, a token, and a ``finally``.
Adding a third value means editing four places, and a miss produces a :exc:`LookupError` that says nothing about what was missing.

The replacement
---------------

.. code-block:: python
   :caption: after

   from dataclasses import dataclass

   from nodrill import provider, use


   @dataclass
   class RequestScope:
       user: User
       db: Engine


   with provider(RequestScope(user=user, db=engine)):
       handle()


   def current_user() -> User:
       return use(RequestScope).user

The tokens, the ``finally``, and the module-level variables are gone.
Adding a field to the dataclass adds it everywhere at once, and a miss raises :exc:`~nodrill.NoProviderError` naming the key and listing what is active.

Mapping the operations
----------------------

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - contextvars
     - nodrill
   * - ``var.set(value)`` plus ``reset(token)``
     - ``with provider(value):``
   * - ``var.get()``
     - ``use(SomeClass)``
   * - ``var.get(fallback)``
     - ``use(SomeClass, default=fallback)``
   * - ``ContextVar("name", default=...)``
     - ``set_default(SomeClass, factory)``
   * - one ``ContextVar`` per loose value
     - ``provider("name", a=1, b=2)`` and ``use("name").a``
   * - ``copy_context().run(fn)`` for a thread
     - ``wrap(fn)``, or ``Executor``

Keeping some of both
--------------------

There is no conflict. nodrill's registry is one :class:`~contextvars.ContextVar` among however many your application already has, and its scopes nest with yours in the usual way.
Migrate the values that hurt, leave the rest, and both see the same context propagation across tasks and threads.

One case does not translate: a ``ContextVar`` created at runtime, per key.
That leaks by design, since a context keeps its variables alive, and it is the reason nodrill uses a single variable holding a copy-on-write mapping instead.
See :doc:`/content/misc/design`.
