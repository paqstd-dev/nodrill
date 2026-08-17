.. _howto-find-out-why-the-context-is-missing:

Find out why the context is missing
===================================

``NoProviderError`` says nothing is registered, and you can see the ``with provider(...)`` a few frames up.
Both are true.
The provider is open, and this frame cannot see it, because the call crossed a boundary that does not carry context.

Debug mode records where every provider block is entered, so the miss can name the block, the thread and the task.

.. code-block:: python
   :caption: missing.py

   from collections.abc import Callable
   from concurrent.futures import ThreadPoolExecutor
   from dataclasses import dataclass

   import nodrill
   from nodrill import NoProviderError, debug, provider, use


   @dataclass
   class Session:
       tenant: str


   def handler() -> str:
       return f"tenant={use(Session).tenant}"


   def in_a_pool_worker() -> None:
       """The provider is open, but the worker thread was never given the context."""
       with provider(Session(tenant="acme")), ThreadPoolExecutor(max_workers=1) as pool:
           try:
               pool.submit(handler).result()
           except NoProviderError as exc:
               print(exc.diagnosis)


   def after_the_block() -> None:
       """The callback is registered inside the scope and runs once it has closed."""
       callbacks: list[Callable[[], str]] = []
       with provider(Session(tenant="acme")):
           callbacks.append(handler)
       try:
           callbacks[0]()
       except NoProviderError as exc:
           print(exc.diagnosis)


   if __name__ == "__main__":
       with debug():
           in_a_pool_worker()
           print()
           after_the_block()
           print()
           with provider(Session(tenant="acme")):
               print(nodrill.explain())

Output, with the absolute paths shortened::

   Session is open right now at missing.py:20, on thread 'MainThread'.
   This frame is on thread 'ThreadPoolExecutor-0_0', which did not inherit that context.
   Fix: submit through nodrill.Executor instead of ThreadPoolExecutor, or bind the callable with nodrill.wrap() inside the provider block.

   Session was open at missing.py:30, on thread 'MainThread'.
   This frame is running after that block closed.
   Fix: do the work inside the block, or bind the callback with nodrill.wrap() inside it, which carries the scope to wherever it runs.

   nodrill debug: 1 provider block open, innermost first within each thread.
     Session opened at missing.py:44, on thread 'MainThread'

Turning it on
-------------

``with nodrill.debug():`` around the entry point covers a whole run, and ``NODRILL_DEBUG=1`` in the environment does the same for a process you would rather not edit.
Recording is global and reference counted, so nesting the block is safe and the innermost one does not turn it off.

It is not for production.
Every provider entered reads the stack and writes a ledger entry, while a lookup that hits costs the same either way.

What it can tell you
--------------------

Four situations, and the message says which one it is.

The key is open on another thread, the case above.
A bare :class:`~concurrent.futures.ThreadPoolExecutor`, a :class:`threading.Thread`, or anything else started without carrying the context.
The fix is :class:`~nodrill.Executor` or :func:`~nodrill.wrap`, and :doc:`/content/howto/run-work-in-threads` has both.

The key is open in another task, and this task was created before the block was entered.
A task snapshots the context when it is created, so creating it inside the block is the fix.

The key is open on this thread and still invisible, so this frame is running under a different context.
A :meth:`contextvars.Context.run`, a :func:`~nodrill.wrap` snapshot taken before the block, or a generator resumed outside it.

The key was open here and the block has already exited, which is the callback registered inside a scope and run after it, as ``after_the_block`` shows.
Bind the callback with :func:`~nodrill.wrap` inside the block and it carries the scope wherever it runs.

A key no provider in the process ever opened gets no diagnosis, since a typo or a missing block is what the ordinary message already covers with its nearest-match suggestion.

Under load the answer stays about this request.
The record a miss reads is the one nearest the failing frame, its own task before its own thread before anywhere else, so a sibling request that closed the same key a moment ago does not get blamed for this one.
Only the 256 most recent exits are kept.
A key whose record has aged out says so rather than going quiet and reading as a key nothing ever provided.

Finding a provider nothing reads
--------------------------------

``debug(unused=True)`` counts reads as well, and warns when a block exits without anything having read what it provided.

.. code-block:: text

   UnusedProviderWarning: nodrill: the provider for Session at scope.py:31 was never read,
   since no use(Session) ran inside the block.

That is usually a key that moved or a layer whose readers went away, and nothing else makes it visible.
Reads are counted per block, so a shadowed provider is reported even when something read the inner one under the same key.
It is off by default even inside debug mode, since a warning changes what a program prints and a counting read costs roughly three times a plain one, and a block whose body raised is never blamed.
The warning is an :exc:`~nodrill.UnusedProviderWarning`, so `warnings.filterwarnings` can silence it by category.

A miss inside an adopt block
----------------------------

The hint on that miss asks whether you forgot ``with provider("trace")``, which is the right question everywhere except here.
Inside a consumer the name was supposed to arrive in the payload, so nothing in this service was ever going to open it, and ``debug()`` has nothing to say because no block was recorded for that key.

Read the payload instead.
``payload["ctx"]`` lists exactly what the producer sent, ``only`` on the ``adopt`` call lists what this consumer agreed to open, and the name is missing from one of the two.
:func:`~nodrill.explain` still helps with the other half of the question, since it names which codec halves this process holds, and a namespace that arrived as encoded data usually means the ``load`` was never registered.

.. rubric:: See also

:doc:`/content/ref/debugging` for both functions, and :doc:`/content/topics/concurrency` for what carries context in the first place.
