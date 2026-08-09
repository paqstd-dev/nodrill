.. _howto-fan-out-with-asyncio:

Fan out with asyncio
====================

Each concurrent worker gets its own scope, and the workers interleave on one event loop without ever seeing each other's values.

Nothing here is nodrill-specific plumbing: ``create_task`` and ``gather`` snapshot the context themselves, so there is nothing to configure.

.. code-block:: python
   :caption: fanout.py

   import asyncio
   from dataclasses import dataclass

   from nodrill import provider, use


   @dataclass
   class Tenant:
       name: str
       shard: int


   async def query(sql: str) -> str:
       """Deep in the stack: no tenant parameter anywhere in between."""
       await asyncio.sleep(0.01)        # let the siblings interleave
       tenant = use(Tenant)
       return f"shard-{tenant.shard}({tenant.name}): {sql}"


   async def handle_tenant(name: str, shard: int) -> list[str]:
       with provider(Tenant(name=name, shard=shard)):
           # create_task snapshots the current context, so the spawned task
           # sees this tenant even though it runs while the block is busy.
           background = asyncio.create_task(query("UPDATE stats"))
           rows = [await query("SELECT 1"), await query("SELECT 2")]
           rows.append(await background)
           return rows


   async def main() -> None:
       results = await asyncio.gather(
           handle_tenant("acme", shard=1),
           handle_tenant("globex", shard=2),
           handle_tenant("initech", shard=3),
       )
       for rows in results:
           for row in rows:
               print(row)


   if __name__ == "__main__":
       asyncio.run(main())

Every printed line names the tenant its own task was handling, in spite of the
``sleep`` inside ``query`` interleaving all three::

   shard-1(acme): SELECT 1
   shard-1(acme): SELECT 2
   shard-1(acme): UPDATE stats
   shard-2(globex): SELECT 1
   ...

Notes
-----

The background task is created inside the provider block and awaited inside it too, but it would resolve the same tenant either way: it captured the context at creation, so exiting the block in the parent does not affect it.

``asyncio.gather`` runs the three coroutines in the calling task's context.
Each one then opens its own provider, and because entering a provider publishes a new registry rather than mutating a shared one, the three never collide.

The same holds for ``asyncio.TaskGroup`` and for :meth:`~asyncio.loop.run_in_executor`, though the latter crosses into a thread pool: see :doc:`run-work-in-threads`.

If a coroutine spawns a task and never awaits it, the task still holds its snapshot until it finishes, which keeps the provided object alive.
That is ordinary reference behaviour, worth knowing when the provided object is large.
