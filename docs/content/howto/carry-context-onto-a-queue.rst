.. _howto-carry-context-onto-a-queue:

Carry context onto a queue
==========================

A background job runs later, in another process, from a message a broker held in the meantime.
Celery, RQ, Dramatiq and a bare :class:`queue.Queue` all come down to the same thing, a string that goes in and a string that comes out.
:func:`~nodrill.export` produces something :func:`json.dumps` accepts, so the context rides along in the message it belongs to.

The program below is both halves at once, with a :class:`queue.Queue` standing in for the broker, because the part worth showing is the JSON, not the broker.

.. code-block:: python
   :caption: jobs.py

   import json
   import queue

   from nodrill import NoProviderError, adopt, export, provider, use

   broker: queue.Queue[str] = queue.Queue()


   def enqueue(job: str) -> None:
       """The producer side, which puts the context in the message."""
       broker.put(json.dumps({"job": job, "nodrill_ctx": export("trace", "audit")}))


   def run_next() -> None:
       """The consumer side, which opens it again around the work."""
       message = json.loads(broker.get())
       with adopt(message["nodrill_ctx"]):
           handle(message["job"])


   def handle(job: str) -> None:
       print(f"{job} for {use('trace').request_id} by actor {use('audit').actor_id}")


   def main() -> None:
       with provider("trace", request_id="req-42"), provider("audit", actor_id=7):
           enqueue("resize-avatar")

       # Outside every provider block, which is where a worker always is.
       try:
           handle("resize-avatar")
       except NoProviderError as miss:
           print(f"without adopt: {type(miss).__name__}")
       run_next()


   if __name__ == "__main__":
       main()

Output::

   without adopt: NoProviderError
   resize-avatar for req-42 by actor 7

Notes
-----

The envelope goes in the message, not beside it.
A broker that loses the message loses the context with it, and a message replayed onto another queue carries the context it was created with.

Name every namespace the job needs, in one call.
``export("trace", "audit")`` produces one envelope holding both, and ``adopt`` opens both.

For Celery the same two lines go in the task's own signature and body.

.. code-block:: python

   @app.task
   def resize_avatar(user_id: int, nodrill_ctx: dict) -> None:
       with adopt(nodrill_ctx):
           ...

   resize_avatar.delay(user_id=7, nodrill_ctx=export("trace"))

Nothing about that is Celery-specific, which is why it is two lines here rather than an integration in the package.
The same two work for RQ's ``enqueue``, for Dramatiq's ``send``, and for a table of pending rows you poll yourself.

A worker that runs the same code for adopted and un-adopted messages wants ``use("trace", default=...)`` rather than a bare ``use``, since a message enqueued by an older producer has no context in it.

The payload is JSON, so it is readable in the broker's own inspector.
That is why the envelope is a plain dict rather than a pickle.

.. seealso::

   :doc:`/content/howto/carry-context-into-a-process-pool` for the same shape inside one program.
   :doc:`/content/howto/carry-context-over-http` for a boundary whose format is fixed by somebody else.
