.. _ref-debugging:

Debugging
=========

.. currentmodule:: nodrill

Where the value a lookup missed actually is, and which boundary dropped it.

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
   The reader's own thread comes first, and a thread's blocks stay together, since a global ordering by age would interleave them and leave no stack readable anywhere.
   Outside debug mode nothing is recorded, and the returned string says so.

.. rubric:: See also

:func:`active` for what the current context can see, which is the question to ask in a test.
