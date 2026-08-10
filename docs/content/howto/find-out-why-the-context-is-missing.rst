.. _howto-find-out-why-the-context-is-missing:

Find out why the context is missing
===================================

``NoProviderError`` says nothing is registered, and you can see the ``with provider(...)`` a few frames up.
Both are true: the provider is open, and this frame cannot see it, because the call crossed a boundary that does not carry context.

Debug mode records where every provider block is entered, so the miss can name the block, the thread and the task instead of only reporting that the registry is empty.

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

   nodrill debug: 1 provider block open, innermost first.
     Session opened at missing.py:44, on thread 'MainThread'

Turning it on
-------------

``with nodrill.debug():`` around the entry point covers a whole run, and ``NODRILL_DEBUG=1`` in the environment does the same for a process you would rather not edit.
Recording is global and reference counted, so nesting the block is safe and the innermost one does not turn it off.

It is not for production.
Every provider entered reads the stack and writes a dict entry, which is a debugging cost paid gladly and a request cost paid for nothing.

What it can tell you
--------------------

Four situations, and each says which one it is.

The key is open on another thread, which is the case above: a bare :class:`~concurrent.futures.ThreadPoolExecutor`, a :class:`threading.Thread`, or anything else started without carrying the context.
The fix is :class:`~nodrill.Executor` or :func:`~nodrill.wrap`, and :doc:`/content/howto/run-work-in-threads` has both.

The key is open in another task, and this task was created before the block was entered.
A task snapshots the context when it is created, so creating it inside the block is the fix.

The key is open on this thread and still invisible, which means this frame is running under a different context: a :meth:`contextvars.Context.run`, a :func:`~nodrill.wrap` snapshot taken before the block, or a generator resumed outside it.

The key was open here and the block has already exited, which is the callback registered inside a scope and run after it, as ``after_the_block`` shows.
Bind the callback with :func:`~nodrill.wrap` inside the block and it carries the scope wherever it runs.

A key no provider in the process ever opened gets no diagnosis at all, because there is nothing to say beyond what the message already says: it is a typo or a missing block, and the message already suggests the nearest active name.

Finding a provider nothing reads
--------------------------------

``debug(unused=True)`` counts reads as well, and warns when a block exits without anything having read what it provided:

.. code-block:: text

   UserWarning: nodrill: the provider for Session at scope.py:31 was never read, since no
   use(Session) ran inside the block.

That is usually a key that moved or a layer that no longer has readers below it, and it is invisible without the counter.
It is off by default even inside debug mode, since a warning changes what a program prints.
A block whose body raised is never blamed, because nothing had the chance to read it.

.. rubric:: See also

:doc:`/content/ref/debugging` for both functions, and :doc:`/content/topics/concurrency` for what does and does not carry context in the first place.
