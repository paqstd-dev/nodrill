.. _misc-faq:

FAQ
===

Is this a service locator, and is that not an anti-pattern?
-----------------------------------------------------------

It is closer to a scoped dynamic variable than to a service locator, since nothing is registered globally by type and nothing is constructed for you.
A provider block is a lexical scope with a lifetime, and what it holds is whatever the caller put there.

The criticism the anti-pattern label points at is real, though, because a dependency read from a context does not appear in the signature.
That is why ``@inject`` exists, and why explicit arguments always win.
Where visibility matters, put the dependency in the signature and let the decorator fill it.

Does it work with FastAPI, Django, Flask, Celery?
-------------------------------------------------

Yes, and none of them need an integration.
Open a provider wherever that framework gives you a boundary, middleware, a dependency, a signal, or a task's entry point.
Everything below it, including third-party code, can read from the scope.

There are no framework integrations in the package, deliberately.
:doc:`scope` says what else is out of scope.

Can I use it together with a DI container?
------------------------------------------

Yes, and the two do different jobs.
A container such as dishka owns construction, lifetimes, and the dependency graph. nodrill carries an already-constructed value down a call stack.

A common arrangement is to let the container build the request scope and hand it to ``provider`` at the boundary.

Why is my value missing in a thread?
------------------------------------

Because :class:`threading.Thread` does not inherit contextvars.
That is standard-library behaviour, not something nodrill adds.
Use :func:`~nodrill.wrap` or :class:`~nodrill.Executor`, which :doc:`/content/topics/concurrency` covers in full.

Why does use(Base) not find my Sub instance?
--------------------------------------------

Keys are exact types.
An MRO search would make every lookup proportional to the class hierarchy and would be ambiguous whenever two subclasses are active at once.

Name the key you want instead, so ``provider(Sub(), key=Base)`` registers the instance under ``Base``, and ``use(Base)`` finds it.
That is also how a consumer depends on a :class:`~typing.Protocol` rather than on a concrete class.
The reasoning is in :doc:`design`.

Why can I not inject into a generator?
--------------------------------------

Because a generator body runs after the call, possibly under different providers, so the injected value would be silently stale.
Call :func:`~nodrill.use` inside the body and iterate while the provider block is open.
:doc:`/content/topics/injection` has the worked example.

Is it fast?
-----------

A lookup is a dict access on a :class:`~contextvars.ContextVar`, with the key check moved off the hit path.
Entering a provider copies the registry, which holds one entry per active key, so it is proportional to how many providers are open, typically a handful.
``@inject`` compiles a wrapper mirroring the function's signature at decoration time, so a call binds natively and pays one sentinel check per injectable parameter.

None of that is free, and none of it is likely to be what your profile blames.
The README carries the current numbers, measured against the same read with the value handed in as a parameter, which is the alternative the library replaces.
``benchmarks/bench.py`` in the repository is what produced them, and running it prints the table for your own machine.

Does it work on free-threaded builds?
-------------------------------------

Nothing in the library assumes the GIL, and the values it stores live in contextvars, whose isolation is per-thread either way.

That said, free-threaded builds are not part of the tested matrix, which covers CPython 3.10 through 3.14.
Reports are welcome.

What does the name mean?
------------------------

Passing a value down through layers that do not use it is usually called "prop drilling", or "tramp data" in older literature.
The library is for not doing that.
