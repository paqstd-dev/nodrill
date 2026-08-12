.. _howto-carry-context-into-a-process-pool:

Carry context into a process pool
=================================

A :class:`~concurrent.futures.ProcessPoolExecutor` runs its tasks in other interpreters, which share no memory with yours.
:func:`~nodrill.wrap` and :class:`~nodrill.Executor` cannot help there, since both hand over the objects themselves.
What crosses is a copy, so :func:`~nodrill.export` makes one and :func:`~nodrill.adopt` opens it on the other side.

.. code-block:: python
   :caption: render.py

   import os
   from concurrent.futures import ProcessPoolExecutor
   from typing import Any

   from nodrill import adopt, export, provider, use


   def render(page: int) -> str:
       """Ordinary code, reading the context the way it always does."""
       return f"page {page} for {use('trace').request_id} in pid {os.getpid()}"


   def worker(payload: dict[str, Any], page: int) -> str:
       """The task the pool runs, which opens the context before doing the work."""
       with adopt(payload):
           return render(page)


   def handle_request() -> None:
       with provider("trace", request_id="req-42", tenant="acme"):
           payload = export("trace")
           with ProcessPoolExecutor(max_workers=2) as pool:
               for line in pool.map(worker, [payload] * 3, range(3)):
                   print(line)


   if __name__ == "__main__":
       print(f"parent is pid {os.getpid()}")
       handle_request()

Output, with pids that will differ on your machine::

   parent is pid 40191
   page 0 for req-42 in pid 40194
   page 1 for req-42 in pid 40194
   page 2 for req-42 in pid 40194

Notes
-----

The ``if __name__ == "__main__"`` guard is not optional.
macOS and Windows start a worker by launching a fresh interpreter and importing the module the task came from, so without the guard the module would start another pool while being imported by one.

``worker`` is a module-level function for the same reason.
The pool sends the callable by name, so a closure or a local function cannot be a task, and neither can the payload hold anything that does not pickle.
The payload here is a plain dict of strings, which is the point of ``export``.

The context does not appear in the worker on its own.
``adopt`` is one line at the top of the task, and the code under it reads ``use("trace")`` with no idea it is in another process.

Export once, outside the loop.
Every task gets the same payload, and building it per task would walk and copy the same values three times.

The pool pickles the payload as part of the arguments, which is why it has to be portable in the first place.
If a value cannot make the trip, ``export`` says so at the boundary and names the attribute, rather than letting :mod:`pickle` fail somewhere in the pool's own machinery.

.. seealso::

   :doc:`/content/howto/carry-context-onto-a-queue` for the same shape where the two halves are separate programs.
   :doc:`/content/topics/concurrency` for the boundaries that need no copy at all.
