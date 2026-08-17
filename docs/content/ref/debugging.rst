.. _ref-debugging:

Debugging
=========

.. currentmodule:: nodrill

Where the value a lookup missed actually is, which boundary dropped it, and what a failure was carrying when it happened.

debug
-----

.. function:: debug(*, unused=False)

   Record where every provider block is entered, for the extent of the block.
   Returns a context manager.

   :param bool unused: also count reads, and warn when a block exits without anything having read what it provided.

   While it is on, a :exc:`NoProviderError` says why the key is not visible.

   .. code-block:: text

      use(Session): no active provider for Session. No providers are active. Hint: did you
      forget `with provider(Session(...))`? A fallback can be registered with
      `set_default(Session, ...)`.

      Session is open right now at web.py:42, on thread 'MainThread', task 'request-17'.
      This frame is on thread 'ThreadPoolExecutor-0_0', which did not inherit that context.
      Fix: submit through nodrill.Executor instead of ThreadPoolExecutor, or bind the callable
      with nodrill.wrap() inside the provider block.

   The diagnosis goes below the ordinary message, never in place of it, and is readable on its own as :attr:`NoProviderError.diagnosis`.
   Four situations get one.
   The key is open on another thread, in another task, on this thread under a different context, or in a block that has already exited.
   A key no provider ever opened gets none, since the ordinary message already suggests the nearest active name.

   The record a miss reads is the one nearest the failing frame, same task before same thread before anywhere else, so a request running alongside this one never explains away its miss.
   Only the 256 most recent exits are kept, and a key whose record has aged out says so rather than reading as a key nothing ever provided.

   Recording is global and reference counted rather than scoped, since the block holding the answer is the one the failing frame cannot see.
   Nesting is therefore safe.

   .. code-block:: python

      with nodrill.debug():
          app.run()

   ``NODRILL_DEBUG`` does the same for a process you would rather not edit.
   It is read once, at import, and any value but the empty string or ``0`` turns recording on.

   .. code-block:: bash

      NODRILL_DEBUG=1 python -m myapp

   With ``unused=True``, a provider nothing read warns as its block exits, pointing at the ``with`` statement that opened it.

   .. code-block:: text

      UnusedProviderWarning: nodrill: the provider for Session at scope.py:31 was never read,
      since no use(Session) ran inside the block.

   It is an :exc:`UnusedProviderWarning`, so it can be silenced by category rather than by matching its message.
   Counting is off by default even inside debug mode, because a warning changes what a program prints.
   A block whose body raised is never warned about, since nothing had the chance to read it.

   Debug mode is not for production.
   Every provider entered reads the stack and writes to the ledger, while a lookup that hits costs what it costs with debug mode off.
   ``unused=True`` puts a counting registry in front of every read on top of that, which is roughly three times a plain hit.

   :ref:`howto-find-out-why-the-context-is-missing` runs all of it on a live program.

explain
-------

.. function:: explain()

   Return a report of the provider blocks open right now, a thread at a time.

   Written for a breakpoint.

   .. code-block:: pycon

      >>> print(nodrill.explain())
      nodrill debug: 3 provider blocks open, innermost first within each thread.
        Session opened at web.py:42, on thread 'MainThread', task 'request-17'
        'app' opened at main.py:10, on thread 'MainThread'
        Session opened at web.py:42, on thread 'worker-3', task 'request-18'

   Blocks opened on other threads and in other tasks are listed too, which is the reason to read this rather than :func:`active`.

   A process with a codec registered says so on a line of its own, above the blocks and whether or not recording is on.

   .. code-block:: text

      nodrill codec: dump and load registered.

   That line is the only way to ask which halves :func:`set_codec` holds, which is the first thing to check when a value arrived in its encoded form.
   A process with no codec prints nothing extra.
   The reader's own thread comes first, and a thread's blocks stay together, since a global ordering by age would interleave them and leave no stack readable anywhere.
   Outside debug mode nothing is recorded, and the returned string says so.

annotate_exceptions
-------------------

.. function:: annotate_exceptions(*, enabled=True)

   Attach the scope to every exception leaving a provider block, process wide.

   :param bool enabled: ``False`` turns the switch off again, which is how a test or a request-scoped experiment gives it back.

   An exception passing out of a provider block gains a :pep:`678` note naming what that block provided, and is re-raised as the same object.
   Nested blocks each add their own as it climbs, so the notes read innermost first.

   .. code-block:: text

      ValueError: invalid state transition
      nodrill scope: Namespace('audit', reason='bulk import', request_id='01JB2')
      nodrill scope: RequestScope(user_id=42, tenant='acme')

   .. warning::

      Anything you provide can be printed into a traceback, so a value holding a secret should hide it in its own ``__repr__``, and a block that carries one should be opened with ``annotate=False``.
      A traceback reaches error trackers and log aggregators that more people can read than can read the source, which is why this is off until you turn it on.

   A note is one line, the fixed prefix ``nodrill scope: `` followed by the ``repr`` of the provided value and nothing else.
   The rendering is bounded and guarded.
   A multi-line ``repr`` is flattened to one line, since a note prints at column zero and a second line would carry no prefix.
   A ``repr`` longer than 200 characters is cut to 200 and ends in three dots.
   A ``repr`` that raises renders as ``<unprintable RequestScope, repr raised ValueError>``, naming the key and the exception type but never its message, and the exception in flight is unaffected.

   A note names what its own block provided and never what the block inherited, so an ``extend=True`` layer reports the values it laid over the copy rather than the merged namespace.
   That is what keeps a credential in an enclosing layer opened with ``annotate=False`` out of the traceback when an inner layer extends it, and the price is that an attribute set on an extending layer inside the block does not appear in its note.

   The value is rendered after the scope is restored, so a ``__repr__`` calling :func:`use` reads the enclosing scope rather than the one being described.
   A :func:`lazy` provider reports the cell's state and never runs the factory, and a ``frozen=True`` provider reports the value rather than the proxy.

   Rendering runs the value's ``__repr__`` while the block is unwinding, which is the one place this library calls user code on an exception path.
   A ``__repr__`` that blocks on a lock the raising frame is holding blocks the unwind with it, so keep one cheap, and use ``annotate=False`` for a value whose ``__repr__`` is neither.
   An exception that refuses the note, a frozen dataclass exception among them, keeps its own failure and simply goes unannotated.
   One exception object raised out of the same block on every attempt of a retry loop collects one note per attempt, since a note records a block the exception left rather than a block that was open.

   Only an :exc:`Exception` is annotated.
   A :exc:`KeyboardInterrupt`, a :exc:`SystemExit`, a :exc:`GeneratorExit` and an :exc:`asyncio.CancelledError` pass through untouched, being control flow rather than failures, and a cancelled task would otherwise collect a note per open block.

   A block is named only when the exception actually leaves it, so a block that catches and swallows says nothing, and a retry that lets one exception object out twice leaves two notes.
   ``provider(..., annotate=True)`` and ``annotate=False`` override the switch for one block, in either direction, and are covered under :ref:`ref-provider`.

   The lookup path is untouched, and a block that exits without an exception costs one pointer comparison more than it did.

   ``BaseException.add_note`` is Python 3.11 and up.
   On 3.10 this call warns with a :exc:`RuntimeWarning` and changes nothing, rather than emulating notes by rewriting ``args`` or by chaining a synthetic exception over the traceback.
   The warning belongs to this call and not to ``provider()``, so a block opened with ``annotate=True`` on 3.10 attaches nothing and says nothing, which is worth knowing for a service that uses the per-block flag and never calls this at all.

   :ref:`howto-see-the-context-in-a-traceback` runs it on a whole program.

.. rubric:: See also

:func:`active` for what the current context can see, which is the question to ask in a test.
:ref:`howto-add-context-to-every-log-record` for the same values on every log line rather than only on a failure.
