.. _topics-typed-contexts:

Typed, class-keyed contexts
===========================

A string key is quick to type and returns an untyped attribute bag.
A class key returns your object with its static type intact.
This page is about the second form: what the key actually is, what it does not match, and how it behaves under a type checker.

.. contents::
   :local:
   :depth: 2

Providing and using by class
----------------------------

``provider(instance)`` registers the object under ``type(instance)``.
``use(SomeClass)`` looks that key up and is annotated to return ``SomeClass``.

.. code-block:: python

   from dataclasses import dataclass
   from nodrill import provider, use

   @dataclass
   class Config:
       debug: bool = False
       url: str = "sqlite://"

   def handler() -> None:
       cfg = use(Config)               # inferred: Config
       print(cfg.url)

   with provider(Config(url="postgres://prod")):
       handler()

Any class works: dataclasses, plain classes, ``NamedTuple`` subclasses, an ``attrs`` class, a Pydantic model, an SQLAlchemy engine. nodrill never constructs it and never inspects it.
It stores what you gave it and hands the same object back.

A dataclass is the usual choice because it gives you the constructor, the ``repr`` and the field types for free, but nothing in the library requires one.

Keys are exact
--------------

The key is exactly ``type(instance)``, and the lookup is exactly the class you ask for.
Inheritance is not consulted:

.. code-block:: python

   class Base: ...
   class Sub(Base): ...

   with provider(Sub()):
       use(Sub)                        # the instance
       use(Base)                       # NoProviderError

This is deliberate.
Context keys are identities rather than hierarchies: an MRO search would make every lookup proportional to the class hierarchy, and it would turn "which provider answers ``use(Base)``" into an ordering question the moment two subclasses are active at once.

The reasoning is in :doc:`/content/misc/design`.

What the rule says is that the key is exact, not that it has to be the concrete class.
``key=`` names it:

.. code-block:: python

   with provider(Sub(), key=Base):
       use(Base)                       # the Sub instance
       use(Sub)                        # NoProviderError: one provider, one key

Still one key and one lookup; the only thing that changed is which one.

Protocols
~~~~~~~~~

``key=`` is what makes a :class:`typing.Protocol` usable as a key, and it is the reason to reach for it.
The consumer then names the capability it needs instead of the class that happens to provide it:

.. code-block:: python

   class Storage(Protocol):
       def put(self, key: str, blob: bytes) -> None: ...


   with provider(S3Backend(bucket="prod"), key=Storage):
       store: Storage = use(Storage)

Nothing checks that the instance satisfies the protocol at runtime, because :func:`isinstance` against a plain ``Protocol`` is not allowed.
The type checker checks the call site, which is where the mistake would be.

A string key does the same job without the typed return: ``provider(S3Backend(), key="storage")``, then ``use("storage")``.

Parameterised generics like ``list[str]`` are not keys at all; wrap the value in a small class.

Late-bound keys
---------------

A class key has to be imported to be named, and the module that owns the key is usually upstream of the module that reads it.
Importing back is a cycle, which is where :func:`~nodrill.ref` comes in.
It names the key by import path and imports it the first time the key is used.

.. code-block:: python

   from nodrill import ref, use

   RequestScope = ref("myapp.context:RequestScope")

   def on_save(sender, instance, **kwargs):
       scope = use(RequestScope)       # myapp.context is imported here, once

Nothing is imported at module level, so the cycle never forms.
The colon says where the module ends.
``ref("myapp.context.RequestScope")`` is accepted too, resolved from the longest importable prefix.

A ref is not a second kind of key.
Once it resolves it borrows the target's identity, so a ref and the class it names are one key in one registry entry.
``use(ref(...))`` finds what ``provider(instance)`` stored under the class, and ``provider(instance, key=ref(...))`` answers ``use(TheClass)``.
Providers resolve the ref immediately, so the registry only ever holds the class and :func:`~nodrill.active` shows it.

The typed spelling is the one :data:`~nodrill.FromCtx` already uses, two names for one thing:

.. code-block:: python

   from typing import TYPE_CHECKING

   if TYPE_CHECKING:
       from myapp.context import RequestScope
   else:
       RequestScope = ref("myapp.context:RequestScope")

   use(RequestScope)                   # the checker sees the class, the runtime the ref

The checker follows the import and types ``use(RequestScope)`` as ``RequestScope``, and the runtime never runs it.
Both spellings appear in :ref:`howto-refer-to-a-key-you-cannot-import`, along with what the resolution errors look like.

Several contexts at once
------------------------

Providers compose; open as many as the scope needs.

.. code-block:: python

   with provider(Config()), provider(Mailer(host="smtp:25")), provider("request", id="r-42"):
       handle()

Each key is independent, so string and class keys never collide, and two different classes never shadow each other.

What a type checker sees
------------------------

``use`` is overloaded.
A class argument returns that class; a string argument returns :class:`~nodrill.Namespace`; adding ``default=`` widens the return type to include the default.

.. code-block:: python

   use(Config)                         # Config
   use("app")                          # Namespace
   use(Config, default=None)           # Config | None
   use("app", default=None)            # Namespace | None

All four hold under mypy in strict mode and under pyright.
That is the practical argument for class keys: the attribute you read off the result is checked, so a rename breaks the build rather than a request.

.. seealso::

   :doc:`defaults` for what happens when no provider is active.
   :doc:`injection` for putting the lookup in the signature instead of the body.
