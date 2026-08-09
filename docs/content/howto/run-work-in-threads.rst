.. _howto-run-work-in-threads:

Run work in threads
===================

A plain :class:`threading.Thread` starts with an empty context, so a job that calls ``use()`` inside one fails unless you propagate the context yourself.

This program shows all three cases: the failure, ``wrap`` for a bare thread, and ``Executor`` for a pool.

.. code-block:: python
   :caption: jobs.py

   import threading
   from dataclasses import dataclass

   from nodrill import Executor, NoProviderError, provider, use, wrap


   @dataclass
   class JobConfig:
       priority: str


   def describe_job(job_id: int) -> str:
       return f"job-{job_id} priority={use(JobConfig).priority}"


   def plain_thread() -> None:
       """The documented behaviour: a bare Thread starts with no context."""
       failures: list[str] = []

       def target() -> None:
           try:
               describe_job(0)
           except NoProviderError:
               failures.append("plain Thread saw no providers (expected)")

       with provider(JobConfig(priority="high")):
           thread = threading.Thread(target=target)
           thread.start()
           thread.join()
       print(failures[0])


   def wrapped_thread() -> None:
       """wrap(fn) snapshots the context where wrap() is called."""
       results: list[str] = []

       def target() -> None:
           results.append(describe_job(1))

       with provider(JobConfig(priority="high")):
           thread = threading.Thread(target=wrap(target))
           thread.start()
           thread.join()
       print("wrapped Thread  :", results[0])


   def context_aware_pool() -> None:
       """Executor propagates the submit-time context into every worker."""
       with provider(JobConfig(priority="batch")), Executor(max_workers=4) as pool:
           for line in pool.map(describe_job, range(3)):
               print("Executor worker :", line)


   if __name__ == "__main__":
       plain_thread()
       wrapped_thread()
       context_aware_pool()

Output::

   plain Thread saw no providers (expected)
   wrapped Thread  : job-1 priority=high
   Executor worker : job-0 priority=batch
   Executor worker : job-1 priority=batch
   Executor worker : job-2 priority=batch

Notes
-----

``wrap`` snapshots at the point where it is called, not where the wrapped callable eventually runs.
Wrapping at module level binds an empty context; wrap inside the scope you want to carry, as ``wrapped_thread`` does.

The wrapped callable is safe to call from several threads at once.
Each call replays the snapshot into a fresh context, so two calls never share mutable contextvar state, and a write one call makes stays in that call.

``Executor`` is a :class:`~concurrent.futures.ThreadPoolExecutor` subclass, so it is a drop-in replacement: same constructor, same ``submit``, same ``map``, same shutdown semantics.
Only the context propagation is added.

Results come back through the future, and so do exceptions.
That is worth remembering for the failure case above: a bare ``Thread`` swallows whatever its target raised, which is why the example ferries the error out through a list.

Both helpers propagate the ambient :data:`~nodrill.context` namespace along with the providers, since both live in the same underlying context.
