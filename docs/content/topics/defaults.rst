.. _topics-defaults:

Defaults and misses
===================

``use()`` outside any matching provider has three possible answers, tried in a fixed order.
This page covers all three, and when each one is the right tool.

.. contents::
   :local:
   :depth: 2

The resolution order
--------------------

1. A factory registered for the class with ``set_default``.
2. The call-site ``default=`` argument.
3. :exc:`~nodrill.NoProviderError`.

A registered factory wins over a call-site default, because registration declares the canonical fallback for the class while ``default=`` only says what one particular caller can live with.

Registered factories
--------------------

``set_default(cls, factory)`` names the value ``use(cls)`` should produce when no provider is active.

.. code-block:: python

   from dataclasses import dataclass
   from nodrill import provider, set_default, use

   @dataclass
   class Settings:
       retries: int = 3

   set_default(Settings, Settings)

   use(Settings).retries               # 3, no provider needed

   with provider(Settings(retries=10)):
       use(Settings).retries           # 10

The factory takes no arguments and returns an instance.
Passing the class itself, as above, is the common case, and any zero-argument callable works, so ``lambda: Settings(retries=env_retries())`` is fine too.

``set_default`` returns the class it was given, so the registration can be written as a single binding where that reads better.

.. code-block:: python

   Settings = set_default(Settings, Settings)

The factory runs on every miss
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Each miss constructs a fresh instance.
The result is never cached.

.. code-block:: python

   set_default(Settings, Settings)
   use(Settings) is use(Settings)      # False

A cached instance would be a global mutable singleton, with every stale-state bug that implies, since one request's mutation would be the next request's starting point.
Per-miss construction mirrors ``dataclasses.field(default_factory=...)``, and if you really do want one shared object, that is a provider at the top of the program.

Registration is configuration, not state.
It lives in a module-level table written at import time, is global to the process rather than scoped to a context, and applies to class keys only.
``set_default(cls, None)`` removes it.

Call-site defaults
------------------

``use(key, default=...)`` is the local escape hatch, shaped like :meth:`dict.get`.

.. code-block:: python

   feature_flags = use("flags", default=None)
   if feature_flags is not None:
       ...

It works for string keys and class keys alike, and it is the only fallback available to string keys.
Use it where a missing context is a normal condition for this one call, and where the fallback value is a property of the caller rather than of the type.

The default is a value rather than a factory, evaluated at the call site like any argument, so a mutable default is shared exactly as it would be anywhere else in Python.

The error
---------

With neither fallback in reach, ``use`` raises :exc:`~nodrill.NoProviderError`.

.. code-block:: text

   NoProviderError: use('datbase'): no active provider for 'datbase'.
   Active providers: 'cache', 'database'. Did you mean 'database'?
   Hint: did you forget `with provider('datbase')`?

The message names the key that was asked for, lists the keys that are active, suggests the nearest string match, and states the fix.
For a class key it also mentions ``set_default`` as the other way out.

The exception subclasses :exc:`LookupError`, so ``except LookupError`` catches it alongside ``KeyError`` and ``IndexError``.
It carries the lookup as data too, for code that needs to act on it.

.. code-block:: python

   try:
       cfg = use(Config)
   except NoProviderError as exc:
       log.warning("missing %r, active: %r", exc.key, exc.active_keys)

Choosing between them
---------------------

Reach for ``set_default`` when the class has a sensible standalone value and you would otherwise write the same fallback at a dozen call sites, settings objects, no-op collectors, or in-memory stand-ins for a real backend.

Reach for ``default=`` when the absence is local and interesting, when the key is a string, or when the fallback is ``None`` and the caller branches on it.

Reach for neither when a missing provider is a bug.
The raised error is more useful than a silent default, and it points at the scope you forgot to open.

.. seealso::

   :doc:`/content/ref/provider` for the exact signatures of ``use`` and ``set_default``.
