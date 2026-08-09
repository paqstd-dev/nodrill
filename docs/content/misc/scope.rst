.. _misc-scope:

What nodrill is for
===================

The library exists for one shape: a value that is known at a boundary and needed several frames below, where the frames in between have no business knowing about it.

Why not just pass arguments
---------------------------

Passing arguments is the right default, and nodrill is built so it keeps working: an explicitly passed argument always wins over injection.

The case the library is for is the other one, when a request id or a database handle has to cross five layers of framework code you do not own, and every intermediate signature grows a parameter it never reads.
Each of those parameters is a place to make a mistake, a reason to touch an unrelated function in a diff, and a small tax on every future change to the leaf.

A scoped context ends that: the boundary sets the value once, and the code that actually needs it asks for it directly.

The trade is real and worth stating.
A parameter is visible in the signature; a context lookup is not.
That is why the class-keyed form exists, why ``@inject`` puts the dependency back in the signature where you want it there, and why the error on a miss goes to the trouble of listing what was active.

Why not raw contextvars
-----------------------

Doing this by hand means one module-level :class:`~contextvars.ContextVar` per value, token bookkeeping around every ``set()``, no typed lookup, and a bare :exc:`LookupError` when something is missing.

nodrill packages exactly that discipline: scoped ``with`` blocks, class-keyed typed lookups, and errors that name the active providers.
:doc:`/content/howto/replace-a-contextvar` maps the hand-rolled pattern onto the library line by line.

Not an IoC container
--------------------

``use(Config)`` returns what a provider upstream put there. nodrill never constructs a dependency graph, resolves a lifetime, or decides what to build.

For factory graphs and lifecycles, use a container such as dishka.
For endpoint-level injection wired into request validation, FastAPI's ``Depends`` already does that job.

The two compose: a container can own construction while nodrill carries the per-request scope to any depth of the call stack.

What is not in this release
---------------------------

No framework integrations.
No middleware, no plugins, no ASGI helpers.

No global singletons.
All state lives in :mod:`contextvars`, except the ``set_default`` table, which is configuration written at import time.

No lifecycle management.
Nothing is constructed, cached, pooled, or closed on your behalf.
