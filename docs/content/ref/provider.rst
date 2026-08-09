.. _ref-provider:

Providers and lookups
=====================

.. currentmodule:: nodrill

The core of the library: open a scope, defer what it costs to fill, read from it, register a fallback, and the namespace object string-named providers hand back.

provider
--------

.. function:: provider(name, /, *, frozen=False, **values)
               provider(instance, /, *, key=None, frozen=False)
               provider(*, name, frozen=False, **values)

   Make a value available to the whole call subtree through :func:`use`.
   Returns a context manager; the value is registered on entry and removed on exit, whether the block completes or raises.

   :param name: A string key.
      The block yields a fresh :class:`Namespace` registered under it, prefilled from ``**values``.
   :param instance: Any object.
      It is registered under ``type(instance)`` and yielded unchanged.
      Keyword values are rejected in this form.
   :param key: The key to register an instance under, instead of its own class.
      A string name or a class; see :ref:`explicit-keys` below.
      Rejected for string-named providers, which already have a key.
   :param frozen: When true, code reading through :func:`use` gets a read-only view while the handle the block yields stays writable.
      See :ref:`frozen-views` below.
   :raises TypeError: More than one positional argument, no target at all, a class rather than an instance, keyword values with an instance target, a non-string ``name=``, a ``key=`` that is neither a string nor a class, or a ``key=`` beside a :func:`lazy` target, which already names its own key.
   :raises RuntimeError: On entering a provider object that is already active.
      Create a separate provider for a nested or concurrent block.

   .. code-block:: python

      with provider("app", db=engine) as ctx:
          ctx.user_id = 42

      with provider(Config(url="postgres://prod")):
          ...

   The ``name=`` keyword is the key only when no positional target is given.
   With a positional target it is ordinary data, so ``provider("doc", name="report.pdf")`` sets an attribute called ``name``.
   ``frozen`` and ``key`` are the two names that cannot be prefilled this way, since they are the function's own parameters.

   The returned object may be entered again after it exits, but not while it is active.

   ``async with`` works too, and does exactly what ``with`` does.
   Nothing here awaits; the async protocol exists so the statement reads the way async code expects.

.. _explicit-keys:

Explicit keys
~~~~~~~~~~~~~

``key=`` decides what an instance is registered under, so a consumer can depend on an abstraction rather than on the class that happens to implement it.

.. code-block:: python

   class Repository(Protocol):
       def get(self, key: str) -> str: ...


   with provider(PostgresRepository(dsn), key=Repository):
       repo = use(Repository)

Keys stay exact.
The instance answers ``use(Repository)`` and nothing else: ``use(PostgresRepository)`` misses, because one provider registers one key.

A string works as well, ``provider(instance, key="repo")``, for a role that has no type to name it.
Note that ``use()`` on a string key is typed as returning :class:`Namespace`, so this form gives up the typed return.

Whether the instance actually satisfies ``key`` is not checked: a runtime :func:`isinstance` against a plain :class:`~typing.Protocol` is not possible, and a type checker already checks the call site.

.. _frozen-views:

Frozen views
~~~~~~~~~~~~

With ``frozen=True`` the registry stores a proxy rather than the object itself.

The proxy forwards the protocols an object is normally used through: attribute reads, ``repr`` and ``str``, ``format``, ``==``, ``!=``, ``hash``, ``bool``, ``dir``, ``len``, iteration, indexing, ``in``, the orderings, arithmetic and its reflected forms, the numeric conversions, calling, and the ``with``, ``async with``, ``await`` and ``async for`` protocols.
``isinstance`` holds, because the proxy reports the target's class.

Three things do not go through.
Setting or deleting an attribute raises :exc:`FrozenContextError`, and so does item assignment or deletion.
Pickling and copying raise :exc:`TypeError`, rather than silently producing an unfrozen duplicate.
In-place operators have no dunder on the proxy at all, so ``frozen += x`` falls back to ``+`` and rebinds the caller's name, leaving the target untouched.

Freezing is shallow.
A mutable object reached *through* the target is still mutable::

   use(Config).tags.append("x")     # goes through

``type(proxy)`` still reports the proxy class.
This is a guard rail against accidental writes, not a security boundary.

.. _lazy-values:

lazy
----

.. function:: lazy(key, factory, /)

   Build a provided value on the first read inside the scope, and not at all without one.
   Pass the result to :func:`provider`; nothing else accepts it.

   :param key: The class the value is registered under.
      Given explicitly, since there is no value yet to derive it from, and not checked against what the factory returns.
      A :class:`~typing.Protocol` or an abstract base class works, as it does for ``provider(instance, key=...)``.
   :param factory: A zero-argument callable returning the value.
   :raises TypeError: ``key`` is not a class, or ``factory`` is not callable.

   .. code-block:: python

      with provider(lazy(Origin, lambda: Origin(actor_id=request.user.pk))):
          response = get_response(request)      # the factory has not run

      use(Origin).actor_id                      # it runs here, once

   The factory runs on the first operation that needs the value, and its result is cached until the scope exits.
   A second scope, including a second entry of the same provider object, starts unresolved again.
   It runs under the context the scope was entered with, so a factory calling :func:`use` reads the scope that declared the value and not whichever one happened to touch it first.
   Writes to a :class:`~contextvars.ContextVar` inside the factory therefore stay in that snapshot, as they do inside :func:`wrap`.

   Two threads inside one scope run the factory once; the second waits for the first.
   That wait is an ordinary lock acquisition, so a coroutine reading a key another thread is still building blocks its whole event loop.
   A factory that hands that read to another thread and waits for it deadlocks, as any once-only initialisation does.

   An :exc:`Exception` is cached and re-raised on every later touch, so a failure does not depend on which frame happened to read first.
   It is the same object each time, so its traceback grows and its ``__context__`` follows the last reader.
   A :exc:`BaseException` is not cached, since a cancelled task says nothing about the factory.
   A factory that reads or returns the key it is building raises :exc:`RuntimeError` rather than recursing, provided it does so on its own thread.

   The registry stores a cell that resolves and delegates, so ``isinstance`` holds before and after resolution and reads, writes and the operators behave as the value does.
   Its gaps are the ones :ref:`frozen-views` has, and for the same reason.
   ``repr`` is deliberately different: it reports the cell's state rather than resolving, which is what keeps :func:`active` free of side effects.
   ``str`` is not, so ``print(use(Cls))`` does run the factory.

   .. code-block:: python

      with provider(lazy(Config, load)):
          repr(use(Config))                     # '<lazy Config, unresolved>'
          use(Config).dsn                       # resolves
          repr(use(Config))                     # "<lazy Config, Config(dsn='...')>"

   With ``frozen=True`` the block and the registry get two views of one build: the block's handle stays writable and consumers get the read-only view described in :ref:`frozen-views`.
   The factory still runs once, on whichever view is touched first, and either costs one proxy hop per read.
   The block yields a cell rather than the object, since with nothing built yet there is no object to hand over.

   String keys are not accepted, since a string-named provider fills its :class:`Namespace` at the call and has nothing left to defer.

use
---

.. function:: use(key)
               use(key, *, default)

   Return the value provided for ``key`` by the nearest enclosing provider.

   :param key: A string name or a class.
      A string returns the provider's :class:`Namespace`; a class returns the provided instance, typed as that class.
   :param default: Returned when no provider matches and no factory is registered.
   :raises TypeError: ``key`` is neither a string nor a class.
   :raises NoProviderError: Nothing matched and no fallback was available.

   Class lookups are by exact type.
   Providing a ``Sub`` instance does not answer ``use(Base)``.

   On a miss the fallbacks are tried in order: a factory registered with :func:`set_default`, then ``default``, then the exception.
   A registered factory therefore takes precedence over a call-site default.

   The overloads give type checkers the four return types:

   .. code-block:: python

      use(Config)                   # Config
      use("app")                    # Namespace
      use(Config, default=None)     # Config | None
      use("app", default=None)      # Namespace | None

set_default
-----------

.. function:: set_default(cls, factory)

   Register a fallback factory for ``use(cls)`` outside any provider.
   Returns ``cls``.

   :param cls: The class used as the lookup key.
   :param factory: A zero-argument callable returning an instance, or ``None`` to remove an existing registration.
   :raises TypeError: ``cls`` is not a class, or ``factory`` is neither callable nor ``None``.

   The factory runs on every miss and returns a fresh instance each time; the result is never cached.

   Registrations are global to the process and are not scoped to a context.
   They apply to class keys only: string keys have no equivalent, and use ``use(key, default=...)`` instead.

   :func:`isolate` rolls back registrations made inside its block, and leaves pre-existing ones visible.

active
------

.. function:: active()

   Return a read-only :class:`~types.MappingProxyType` over the providers active right now, keyed exactly as :func:`use` looks them up.

   For debugging a missing provider without having to trigger the error, and for test assertions.
   The result is a snapshot of the current scope; providers opened afterwards do not appear in it.

   .. code-block:: python

      with provider("app", db=engine), provider(Config()):
          assert set(active()) == {"app", Config}

Namespace
---------

.. class:: Namespace(**values)

   The mutable attribute bag a string-named provider yields.

   Constructed from keyword arguments, which become attributes.
   Read, write and delete attributes on it as on any object.
   ``self`` is positional-only, so ``Namespace(self=1)`` is legal data rather than a collision.

   Reading an attribute that was never set raises :exc:`AttributeError` naming the provider it belongs to and listing the attributes that are set.

   Two namespaces are equal when their attributes are, whatever provider made them; ``vars(ns)`` gives the attributes as a plain :class:`dict`.
   As with :class:`types.SimpleNamespace`, value equality on a mutable object means instances are unhashable.

   Instances are usually created by :func:`provider` rather than directly; the class is exported so it can be used in annotations.

   .. code-block:: python

      def render(ctx: Namespace) -> str: ...

      with provider("app", title="Home") as ctx:
          render(ctx)
