.. _topics-declaring:

Declaring keys
==============

.. currentmodule:: nodrill

No single place knows what context keys an application has.
A key is a class in a module somewhere, so answering "what is in the context here" means grepping for ``use(``, a missing provider can only be diagnosed from what happens to be active, and a :func:`set_default` fallback turns a forgotten boundary into a silent wrong value.

:func:`declare` is the operations answer.
It records intent about a key that already exists, at import time, next to the key, and changes no lookup behaviour at all.
It is not a type system and not a container, since nothing is constructed, cached or enforced.
What it records is read back in three places.

The catalogue
-------------

:func:`keys` returns every declaration made so far, keyed exactly as :func:`use` looks values up.

.. code-block:: python

   import nodrill
   from dataclasses import dataclass


   @nodrill.declare(
       doc="Who is acting and how they got here",
       provided_by=("http middleware", "celery worker", "management command"),
   )
   @dataclass(frozen=True)
   class Origin:
       user_id: int
       via: str


   nodrill.declare("audit", doc="What the request is doing", provided_by="request boundary")

   for key, record in nodrill.keys().items():
       print(key, "—", record.doc)

A startup check can assert the catalogue holds what the deployment expects, an admin page can render it, and a test can read it instead of grepping.
String keys benefit most, since a string key has no definition site for a reader to find.

Keys you do not own are declared through the call form, and a :func:`ref` defers the import exactly as it does everywhere else.

.. code-block:: python

   nodrill.declare(nodrill.ref("app.models:Origin"), provided_by=("http middleware",))

Nothing is imported by the call or by :func:`keys`, and the declaration is listed once the ref has resolved on its own.
The catalogue therefore lists what the modules imported so far have declared, which for a check that runs at startup, after the application has loaded, is everything.

Better misses
-------------

A miss for a declared key stops guessing.
:exc:`NoProviderError` names the boundaries the declaration listed, straight from ``provided_by``, beside the hints it already carried.

.. code-block:: text

   NoProviderError: use(Origin): no active provider for Origin. No providers are
   active. Origin is declared as provided by http middleware, celery worker,
   management command. Hint: did you forget `with provider(Origin(...))`? A
   fallback can be registered with `set_default(Origin, ...)`.

The message now says which boundary forgot to open its provider, which is the question the reader was about to spend an hour on.
The boundaries are free text on purpose.
They are a hint for a human, not an address for a machine, and structure belongs to tooling that can actually check it.

Suspicious fallbacks
--------------------

A :func:`set_default` factory is a deliberate fallback, and its danger is that it also swallows a missing boundary.
A new entry point that forgets its provider does not fail.
It writes the fallback value into every record it touches, for as long as nobody looks.

Declaring the fallback ``"suspicious"`` makes that visible.

.. code-block:: python

   @nodrill.declare(
       doc="Who is acting",
       provided_by=("http middleware",),
       fallback="suspicious",
   )
   @dataclass(frozen=True)
   class Origin:
       user_id: int = 0
       via: str = "system"


   nodrill.set_default(Origin, Origin)

Every firing of the factory is counted, and :func:`explain` reports the count on a line of its own.

.. code-block:: text

   nodrill declare: the 'suspicious' fallback for Origin has fired 8514 times.

A fallback meant for a management command that turns out to carry production traffic shows up as a number instead of as an audit table full of ``system``.
Counting is always on and costs one probe of the catalogue on the fallback path, a lookup that hits pays nothing, and the count is reported rather than enforced, so nothing breaks while you find out.
A suspicious declaration made through a :func:`ref` starts counting once the ref has resolved, so an application that declares through refs wants a :func:`resolve_refs` at startup.

What a declaration is not
-------------------------

A declaration never makes a key required.
Whether every entry point actually provides what its handlers read is a whole-program question, and answering it is a build step rather than a runtime check.

Declarations are configuration, exactly as the :func:`set_default` table is.
:func:`isolate` rolls back declarations its block made and leaves pre-existing ones visible, so a test declares freely, and a declaration a module body made while the block imported it belongs to the module and survives.

.. rubric:: See also

:ref:`ref-declaring` describes the three names exactly.
:ref:`topics-defaults` covers the fallback mechanics a ``"suspicious"`` declaration watches.
