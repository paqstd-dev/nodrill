.. _ref-provider:

Providers and lookups
=====================

.. currentmodule:: nodrill

The core of the library, opening a scope, deferring what it costs to fill, reading from it, registering a fallback, and the namespace object string-named providers hand back.

provider
--------

.. function:: provider(name, /, *, frozen=False, extend=False, annotate=None, **values)
               provider(instance, /, *, key=None, frozen=False, annotate=None)
               provider(*, name, frozen=False, extend=False, annotate=None, **values)

   Make a value available to the whole call subtree through :func:`use`.
   Returns a context manager, and the value is registered on entry and removed on exit, whether the block completes or raises.

   :param name: A string key.
      The block yields a fresh :class:`Namespace` registered under it, prefilled from ``**values``.
   :param instance: Any object.
      It is registered under ``type(instance)`` and yielded unchanged.
      Keyword values are rejected in this form.
   :param key: The key to register an instance under, instead of its own class.
      A string name, a class, or a :func:`ref` naming one, covered under :ref:`explicit-keys` below.
      Rejected for string-named providers, which already have a key.
   :param frozen: When true, code reading through :func:`use` gets a read-only view while the handle the block yields stays writable.
      See :ref:`frozen-views` below.
   :param extend: When true, the block lays ``**values`` over a copy of the namespace the same name already holds, instead of shadowing it.
      String-named providers only, covered under :ref:`extending-providers` below.
   :param annotate: Whether an exception leaving this block carries a note naming what the block provided.
      ``None``, the default, follows :func:`annotate_exceptions`, while ``True`` and ``False`` decide for this block whatever the process-wide switch says.
      ``False`` is how a layer holding a credential stays out of a traceback, and the note format is covered under :func:`annotate_exceptions`.
      Exception notes are Python 3.11 and up, so on 3.10 ``annotate=True`` attaches nothing, and the warning that says so is on :func:`annotate_exceptions` rather than here.
   :raises TypeError: More than one positional argument, no target at all, a class or a :func:`ref` rather than an instance, keyword values with an instance target, a non-string ``name=``, a ``key=`` that is neither a string nor a class, a ``key=`` beside a :func:`lazy` target, which already names its own key, or ``extend=True`` on anything but a string name.
      On entry, ``extend=True`` over a name that holds something other than a :class:`Namespace`.
   :raises RuntimeError: On entering a provider object that is already active.
      Create a separate provider for a nested or concurrent block.

   .. code-block:: python

      with provider("app", db=engine) as ctx:
          ctx.user_id = 42

      with provider(Config(url="postgres://prod")):
          ...

   The ``name=`` keyword is the key only when no positional target is given.
   With a positional target it is ordinary data, so ``provider("doc", name="report.pdf")`` sets an attribute called ``name``.
   ``frozen``, ``key``, ``extend`` and ``annotate`` are the four names that cannot be prefilled this way, since they are the function's own parameters.

   The returned object may be entered again after it exits, but not while it is active.

   ``async with`` works too, and does exactly what ``with`` does.
   Nothing here awaits, and the async protocol exists so the statement reads the way async code expects.

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
The instance answers ``use(Repository)`` and nothing else, so ``use(PostgresRepository)`` misses, because one provider registers one key.

A string works as well, ``provider(instance, key="repo")``, for a role that has no type to name it.
Note that ``use()`` on a string key is typed as returning :class:`Namespace`, so this form gives up the typed return.

Whether the instance actually satisfies ``key`` is not checked, since a runtime :func:`isinstance` against a plain :class:`~typing.Protocol` is not possible, and a type checker already checks the call site.

.. _frozen-views:

Frozen views
~~~~~~~~~~~~

With ``frozen=True`` the registry stores a proxy rather than the object itself.

The proxy forwards the protocols an object is normally used through, attribute reads, ``repr`` and ``str``, ``format``, ``==``, ``!=``, ``hash``, ``bool``, ``dir``, ``len``, iteration, indexing, ``in``, the orderings, arithmetic and its reflected forms, the numeric conversions, calling, and the ``with``, ``async with``, ``await`` and ``async for`` protocols.
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

.. _extending-providers:

Extending providers
~~~~~~~~~~~~~~~~~~~

With ``extend=True`` the block copies the namespace the same name currently holds and lays its own values over the copy.

.. code-block:: python

   with provider("audit", request_id="r-1"):
       with provider("audit", extend=True, actor_id=7):
           use("audit").request_id          # "r-1"
           use("audit").actor_id            # 7

       use("audit").actor_id                # AttributeError

Attributes shadow one at a time, so a name set by both layers takes the inner value, and the merge is one level deep, where a value that is itself a mapping is replaced rather than merged.
With no enclosing provider under that name it is an ordinary provider, so a layer needs no branch for being the first one.

The copy is taken when the block is entered, not when ``provider()`` is called, which is observable for a provider object entered twice around different enclosing layers.
It is a snapshot in both directions, so a later write to the outer namespace is invisible inside the block, and a write inside the block is invisible outside it.
Nothing is copied within a layer, so a callee still mutates the namespace the block yielded.

The layer inherits the name, so an :exc:`AttributeError` still reports which provider the namespace came from, and it does not inherit ``frozen``.
An outer layer provided with ``frozen=True`` is read through its view and produces a writable layer, unless the inner one asks for ``frozen=True`` in its own right.

``extend=True`` is refused for instance and :func:`lazy` targets, where providing a new value, built with :func:`dataclasses.replace` or otherwise, is the equivalent.
Extending a name that holds something other than a :class:`Namespace` raises :exc:`TypeError` on entry, naming the type found, rather than shadowing it silently.

.. _lazy-values:

lazy
----

.. function:: lazy(key, factory, /)

   Build a provided value on the first read inside the scope, and not at all without one.
   Pass the result to :func:`provider`, which is the only thing that accepts it.

   :param key: The class the value is registered under, or a :func:`ref` naming one, which resolves at this call.
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

   Two threads inside one scope run the factory once, and the second waits for the first.
   That wait is an ordinary lock acquisition, so a coroutine reading a key another thread is still building blocks its whole event loop.
   A factory that hands that read to another thread and waits for it deadlocks, as any once-only initialisation does.

   An :exc:`Exception` is cached and re-raised on every later touch, so a failure does not depend on which frame happened to read first.
   It is the same object each time, so its traceback grows and its ``__context__`` follows the last reader.
   Under :func:`annotate_exceptions` its notes grow the same way, one per read that escapes an annotated block, since a note records a block the exception left.
   A :exc:`BaseException` is not cached, since a cancelled task says nothing about the factory.
   A factory that reads or returns the key it is building raises :exc:`RuntimeError` rather than recursing, provided it does so on its own thread.

   The registry stores a cell that resolves and delegates, so ``isinstance`` holds before and after resolution and reads, writes and the operators behave as the value does.
   Its gaps are the ones :ref:`frozen-views` has, and for the same reason.
   ``repr`` is deliberately different and reports the cell's state rather than resolving, which is what keeps :func:`active` free of side effects.
   ``str`` is not, so ``print(use(Cls))`` does run the factory.

   .. code-block:: python

      with provider(lazy(Config, load)):
          repr(use(Config))                     # '<lazy Config, unresolved>'
          use(Config).dsn                       # resolves
          repr(use(Config))                     # "<lazy Config, Config(dsn='...')>"

   With ``frozen=True`` the block and the registry get two views of one build, so the block's handle stays writable and consumers get the read-only view described in :ref:`frozen-views`.
   The factory still runs once, on whichever view is touched first, and either costs one proxy hop per read.
   The block yields a cell rather than the object, since with nothing built yet there is no object to hand over.

   String keys are not accepted, since a string-named provider fills its :class:`Namespace` at the call and has nothing left to defer.

use
---

.. function:: use(key)
               use(key, *, default)

   Return the value provided for ``key`` by the nearest enclosing provider.

   :param key: A string name, a class, or a :func:`ref` naming one.
      A string returns the provider's :class:`Namespace`, and a class returns the provided instance, typed as that class.
   :param default: Returned when no provider matches and no factory is registered.
   :raises TypeError: ``key`` is neither a string nor a class.
   :raises NoProviderError: Nothing matched and no fallback was available.
   :raises KeyResolutionError: ``key`` is a :func:`ref` whose path cannot be imported.

   Class lookups are by exact type.
   Providing a ``Sub`` instance does not answer ``use(Base)``.

   On a miss the fallbacks are tried in order, a factory registered with :func:`set_default`, then ``default``, then the exception.
   A registered factory therefore takes precedence over a call-site default.

   The overloads give type checkers the four return types.

   .. code-block:: python

      use(Config)                   # Config
      use("app")                    # Namespace
      use(Config, default=None)     # Config | None
      use("app", default=None)      # Namespace | None

.. _late-bound-keys:

ref
---

.. function:: ref(path, /)

   Name a class key by import path, to be imported the first time the key is used.
   Nothing is imported at the call, so a module can name a key that lives in a module importing it back.

   :param path: ``'package.module:Name'``, the canonical form, in which the colon says where the module ends.
      ``'package.module.Name'`` is accepted too and resolved from the longest importable prefix, walking the rest as attributes.
   :raises TypeError: ``path`` is not a string.
   :raises ValueError: ``path`` is not two or more identifier segments, as ``'Name'`` or ``'a..b'`` is.
   :raises KeyResolutionError: On the first use, when the path cannot be imported.

   Nothing else about a path can be checked at the call.
   ``'package.module'`` is written exactly as ``'module.Name'`` is, so it is accepted and resolves to the module, which is no kind of key and raises :exc:`TypeError` out of :func:`use`.

   .. code-block:: python

      from nodrill import ref, use

      RequestScope = ref("myapp.context:RequestScope")

      def on_save(sender, instance, **kwargs):
          scope = use(RequestScope)

   The result goes wherever a class key goes, into :func:`use`, ``provider(instance, key=...)``, :func:`lazy`, :func:`set_default`, :func:`from_ctx` and ``@inject(from_=...)``.
   It is a key and not a value, so ``provider(ref(...))`` raises.
   The instance is what a provider takes, and the ref names what to register it under.
   A path naming a string constant works as well and answers the name that string holds, compared by value the way any string key is.
   A ref borrows its target's identity once it resolves, so it is not a second kind of key.
   ``use(ref(...))`` finds the entry a plain ``provider(instance)`` stored under the class, and a provider opened with ``key=ref(...)`` answers ``use(TheClass)``.
   Two refs to one target are equal and hash equal, so a dict keyed by refs behaves as the registry does.

   What a ref keeps is the object it resolved, not the path it walked.
   Two paths to one class, a module and the package that re-exports it, are therefore one key and one registry entry.
   :func:`importlib.reload` is the other side of that.
   It rebinds the name to a new class, and a ref that already resolved goes on naming the old one, as a ``from ... import`` in any other module would.

   The provider side resolves immediately, at the ``provider()``, :func:`lazy` or :func:`set_default` call, so the registry only ever holds a class and :func:`active` shows one.
   Only the consumer side is deferred, which is the side with the import problem.

   The typed spelling is the one :data:`FromCtx` uses, two names for one thing.

   .. code-block:: python

      from typing import TYPE_CHECKING

      if TYPE_CHECKING:
          from myapp.context import RequestScope
      else:
          RequestScope = ref("myapp.context:RequestScope")

      use(RequestScope)              # the checker sees the class, the runtime the ref

   Hashing a ref resolves it, since the hash is the target's.
   Putting one in a set or a dict therefore imports, and so does comparing one to anything.
   A resolution failure is not cached, so a path that failed inside an import cycle resolves normally once that import completes.

   ``use(ref(...), default=...)`` does not cover a broken path.
   The default answers a missing provider.
   A path that cannot be imported raises :exc:`KeyResolutionError` before any lookup happens.

resolve_refs
------------

.. function:: resolve_refs()

   Import every ref created so far, in creation order, and raise on the first one that fails.

   :raises KeyResolutionError: Any ref cannot be resolved.

   For an application that would rather fail at startup than on its first request.
   In Django that call belongs in ``AppConfig.ready()``.
   Refs that already resolved are left alone, so a second call costs one read each, and refs no longer referenced anywhere are forgotten rather than resolved.

   :func:`isolate` rolls back the refs its own block created, the way it rolls back :func:`set_default` registrations, so one test's deliberately broken path is not another test's startup failure.
   A ref a module made while the block imported it is kept, because the module keeps it and stays imported after the block, so the path goes on being checked.

set_default
-----------

.. function:: set_default(cls, factory)

   Register a fallback factory for ``use(cls)`` outside any provider.
   Returns ``cls``.

   :param cls: The class used as the lookup key, or a :func:`ref` naming one.
      A ref resolves here and the class is what comes back, since the table is keyed by class, and this is the one call that is not deferred.
   :param factory: A zero-argument callable returning an instance, or ``None`` to remove an existing registration.
   :raises TypeError: ``cls`` is not a class, or ``factory`` is neither callable nor ``None``.

   The factory runs on every miss and returns a fresh instance each time, and the result is never cached.

   Registrations are global to the process and are not scoped to a context.
   They apply to class keys only, since string keys have no equivalent and use ``use(key, default=...)`` instead.

   :func:`isolate` rolls back registrations made inside its block, and leaves pre-existing ones visible.

active
------

.. function:: active()

   Return a read-only :class:`~types.MappingProxyType` over the providers active right now, keyed exactly as :func:`use` looks them up.

   For debugging a missing provider without having to trigger the error, and for test assertions.
   The result is a snapshot of the current scope, and providers opened afterwards do not appear in it.

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

   Two namespaces are equal when their attributes are, whatever provider made them, and ``vars(ns)`` gives the attributes as a plain :class:`dict`.
   As with :class:`types.SimpleNamespace`, value equality on a mutable object means instances are unhashable.

   Instances are usually created by :func:`provider` rather than directly, and the class is exported so it can be used in annotations.

   .. code-block:: python

      def render(ctx: Namespace) -> str: ...

      with provider("app", title="Home") as ctx:
          render(ctx)
