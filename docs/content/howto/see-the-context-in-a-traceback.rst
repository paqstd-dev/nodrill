.. _howto-see-the-context-in-a-traceback:

See the context in a traceback
==============================

Something fails five frames below the boundary.
The traceback names the frames and none of the values, because the values are exactly what this library took out of the signatures in between.
So the one question that matters, which request, which tenant, which actor, cannot be answered from the traceback already sitting in the error tracker.

:func:`~nodrill.annotate_exceptions` makes every provider block an exception passes through say what it was holding.

.. warning::

   Anything you provide can be printed into a traceback, so a value holding a secret should hide it in its own ``__repr__``, and a block that carries one should be opened with ``annotate=False``.
   A traceback travels further than the process that made it, into log aggregators and error trackers that more people can read than can read the source.
   That is why the feature is off until you turn it on.

.. code-block:: python
   :caption: notes.py

   from dataclasses import dataclass

   import nodrill
   from nodrill import provider


   @dataclass
   class RequestScope:
       user_id: int
       tenant: str


   def handle(state: str) -> None:
       """Open the scopes a request runs under, then do the work."""
       with provider(RequestScope(user_id=42, tenant="acme")):
           with provider("audit", request_id="01JB2", reason="bulk import"):
               transition(state)


   def transition(state: str) -> None:
       """Fail where nothing in the signature says which request this is."""
       if state == "archived":
           raise ValueError("invalid state transition")


   if __name__ == "__main__":
       nodrill.annotate_exceptions()
       handle("archived")

Output, with the absolute paths shortened::

   Traceback (most recent call last):
     File "notes.py", line 28, in <module>
       handle("archived")
       ~~~~~~^^^^^^^^^^^^
     File "notes.py", line 17, in handle
       transition(state)
       ~~~~~~~~~~^^^^^^^
     File "notes.py", line 23, in transition
       raise ValueError("invalid state transition")
   ValueError: invalid state transition
   nodrill scope: Namespace('audit', reason='bulk import', request_id='01JB2')
   nodrill scope: RequestScope(user_id=42, tenant='acme')

The notes read innermost first, since the innermost block is the first one the exception leaves.
They print in Sentry, in pytest output, in journald and in a bare ``python -m``, with no logging configured at all, because :pep:`678` notes are printed by the interpreter itself.

What a note says
----------------

One line per block, ``nodrill scope: `` followed by the ``repr`` of what the block provided and nothing else.
The prefix is fixed, so it is one string to search an aggregator for.

The rendering is bounded and guarded, because a traceback is a bad place to be surprised.
A ``repr`` spanning several lines is flattened to one, since a note prints at column zero and a second line would carry no prefix.
A ``repr`` longer than 200 characters is cut to 200 and ends in three dots.
A ``repr`` that raises an :exc:`Exception` is replaced by ``<unprintable RequestScope, repr raised ValueError>``, which names the key and the exception type and never its message.

The exception is the same object throughout.
Its ``args``, its ``__cause__``, its ``__context__`` and its traceback are exactly what they were, and ``__notes__`` is the only thing that changed.
An exception that refuses the note, a frozen dataclass exception among them, keeps its own failure and simply goes unannotated, since the failure on its way out is worth more than the note describing it.
The one thing that does get through is a :exc:`BaseException` raised inside a ``__repr__``, which is not caught anywhere, exactly as one raised inside a :func:`~nodrill.lazy` factory is not.

Rendering runs the value's ``__repr__`` while the block is unwinding, which is the one place this library calls your code on an exception path.
Keep it cheap, and reach for ``annotate=False`` when it is not, since a ``__repr__`` that waits on a lock the raising frame is holding waits forever.

Choosing which blocks speak
---------------------------

``annotate_exceptions()`` is the process-wide switch, and ``annotate=`` on a block overrides it in either direction.

.. code-block:: python

   with provider(RequestScope(user_id=42, tenant="acme")):         # follows the switch
       with provider("db", dsn=dsn, annotate=False):               # never in a traceback
           with provider("audit", request_id=rid, annotate=True):  # always in one
               ...

``annotate=False`` is the answer for a layer holding a credential, a session token or a whole request body.
``annotate=True`` is the answer for a service that would rather not turn the switch on globally, and for a test that wants the note without touching process state.
On Python 3.10 the per-block flag attaches nothing and says nothing about it, since the warning belongs to ``annotate_exceptions()`` and a service using only the flag never calls it.

A note names what its own block provided and never what the block inherited.
An ``extend=True`` layer therefore reports the values it laid over the copy rather than the whole merged namespace, which is what keeps a credential in the layer it extends out of the traceback, and it means an attribute set on such a layer inside the block does not appear in its note.

A block is named only when the exception actually leaves it.
A block that catches and swallows says nothing, and a retry loop that lets the same exception object out three times leaves three notes, which is a true record of what happened.
A single exception instance kept as a module-level constant and raised on every request therefore accumulates a note per request, so raise a fresh exception if you keep one around like that.
A :func:`~nodrill.lazy` factory that failed does the same on every later read, since its exception is cached and re-raised as the same object, which is already how its traceback grows.
Only an :exc:`Exception` is annotated, so a :exc:`KeyboardInterrupt`, a :exc:`SystemExit`, a :exc:`GeneratorExit` and an :exc:`asyncio.CancelledError` pass through untouched, being control flow rather than failures.

``async with`` behaves exactly as ``with`` does, and so does a block entered through :class:`contextlib.ExitStack`.

On Python 3.10
--------------

``BaseException.add_note`` arrived in 3.11, so on 3.10 this does nothing and says so once, with a :exc:`RuntimeWarning` at the ``annotate_exceptions()`` call.
Nothing else changes, and the call can stay where it is until the interpreter moves.

Emulating notes was considered and rejected.
Rewriting ``args`` mutates the user's exception in a way that breaks equality and pickling, and raising a synthetic exception from the original rewrites the traceback this feature exists to preserve.

.. rubric:: See also

:doc:`/content/howto/add-context-to-every-log-record` for the same values on every log line rather than only on a failure.
:doc:`/content/ref/debugging` for the reference entry, and :doc:`/content/howto/find-out-why-the-context-is-missing` for the other half of debugging, which is a value that is not there at all.
