.. _topics-providers:

Providers and scopes
====================

A provider makes one value visible to everything that runs inside a ``with`` block, however deep.
This page covers the shapes ``provider`` accepts, what a scope does on entry and exit, how mutation flows, and what ``frozen=True`` changes.

.. contents::
   :local:
   :depth: 2

The two shapes
--------------

``provider`` takes either a name or an instance.

Named providers
~~~~~~~~~~~~~~~

``provider("app")`` yields a fresh :class:`~nodrill.Namespace` registered under that string, and keyword arguments prefill it.

.. code-block:: python

   from nodrill import provider, use

   with provider("app", db=engine, user_id=42) as ctx:
       ctx.trace = []
       handle()

   def handle() -> None:
       app = use("app")
       app.trace.append("handle")
       print(app.db, app.user_id)

The namespace is a plain attribute bag: read, write, and delete attributes on it as you would on any object.
Reading a name that was never set raises :exc:`AttributeError` naming the provider and listing what is available, which is worth a lot once several named providers are active at once.

There is a keyword spelling too, for the case where the name is computed:

.. code-block:: python

   with provider(name=scope_name, db=engine):
       ...

``name=`` is only the key when no positional target is given.
With a positional target it is ordinary data, so ``provider("doc", name="report.pdf")`` sets an attribute called ``name`` on the ``"doc"`` namespace.

Instance providers
~~~~~~~~~~~~~~~~~~

``provider(instance)`` registers the object under its own class.

.. code-block:: python

   from dataclasses import dataclass

   @dataclass
   class Config:
       url: str = "sqlite://"

   with provider(Config(url="postgres://prod")):
       handler()

This is the typed form, and :doc:`typed-contexts` covers it in full.
It takes no keyword values: the object already carries its own state.
Passing a class rather than an instance is an error, and says so.

.. code-block:: python

   provider(Config)     # TypeError: did you mean provider(Config(...))?

What a scope does
-----------------

Entering a provider copies the registry of active keys, adds one entry, and publishes the result on a :class:`~contextvars.ContextVar`.
Exiting resets it through the token that ``set`` returned.

Three consequences follow, and all three are the point:

Exit is unconditional.
   The value is removed whether the block finished or raised.
   There is no cleanup to remember and no state to leak into the next request.

Same-key providers nest.
   An inner block shadows the outer value for its duration; the outer value is restored on exit.

   .. code-block:: python

      with provider(Config(url="outer")):
          with provider(Config(url="inner")):
              assert use(Config).url == "inner"
          assert use(Config).url == "outer"

Siblings never see each other.
   Two asyncio tasks entering their own providers hold references to two different registries.
   Nothing is mutated in place, so there is no ordering to reason about.
   :doc:`concurrency` covers the task and thread story.

Reuse and re-entrancy
~~~~~~~~~~~~~~~~~~~~~

The object ``provider(...)`` returns is a context manager that can be entered again after it exits, sequentially:

.. code-block:: python

   p = provider(Config())
   with p:
       ...
   with p:            # fine
       ...

It cannot be active twice at once.
Entering a provider that is already open raises :exc:`RuntimeError`, because a single token cannot describe two nested resets.
Build a second provider for a nested or concurrent block.

Mutation is shared
------------------

The provided object is shared by reference, not copied.
A callee that mutates it mutates the caller's object, which is exactly what you want for an accumulating trace or a per-request cache:

.. code-block:: python

   @dataclass
   class RequestScope:
       trace: list[str] = field(default_factory=list)

   with provider(RequestScope()) as scope:
       dispatch()                       # deep code appends to scope.trace
       log(scope.trace)

Rebinding the name inside a callee does nothing, as with any Python object: ``use(Config)`` returns the object, and assigning to that local only changes the local.
To publish a different value, open another provider.

Frozen providers
----------------

When callees should read but not write, provide the value frozen:

.. code-block:: python

   from nodrill import FrozenContextError, provider, use

   with provider(Config(), frozen=True) as cfg:
       cfg.url = "the owner can still write"

       use(Config).url = "..."          # FrozenContextError

The block yields the real object, so whoever opened the scope keeps a writable handle.
Everything reached through ``use()`` gets a read-only view instead.

The view is a proxy that delegates reads to the target.
Attribute access, ``repr``, ``==``, ``hash``, ``dir``, iteration, ``len``, indexing and ``in`` all behave as the real object does, and ``isinstance`` holds, because the proxy reports the target's class:

.. code-block:: python

   with provider(Config(), frozen=True):
       cfg = use(Config)
       isinstance(cfg, Config)          # True
       type(cfg)                        # the proxy class, not Config

Two operations are refused outright.
Writes and deletions raise :exc:`~nodrill.FrozenContextError`, which subclasses :exc:`AttributeError`, so ``getattr``-style guards keep working.
Pickling and copying raise :exc:`TypeError` rather than silently handing back an unfrozen duplicate.

This is a guard rail against accidental writes, not a security boundary.
Code that goes looking for the wrapped object can reach it.

.. seealso::

   :doc:`typed-contexts` for class-keyed lookups and fallbacks.
   :doc:`/content/ref/provider` for the exact signatures.
