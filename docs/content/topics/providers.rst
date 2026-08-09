.. _topics-providers:

Providers and scopes
====================

A provider makes one value visible to everything that runs inside a ``with`` block, however deep.
This page covers the shapes ``provider`` accepts, what a scope does on entry and exit, how mutation flows, and what ``lazy`` and ``frozen=True`` change.

.. contents::
   :local:
   :depth: 2

The two shapes
--------------

``provider`` takes either a name or an instance.

Named providers
~~~~~~~~~~~~~~~

``provider("app")`` yields a fresh :class:`~nodrill.Namespace` registered under that string, and keyword arguments prefill it.

.. code-block:: python

   from nodrill import provider, use

   with provider("app", db=engine, user_id=42) as ctx:
       ctx.trace = []
       handle()

   def handle() -> None:
       app = use("app")
       app.trace.append("handle")
       print(app.db, app.user_id)

The namespace is a plain attribute bag: read, write, and delete attributes on it as you would on any object.
Reading a name that was never set raises :exc:`AttributeError` naming the provider and listing what is available, which is worth a lot once several named providers are active at once.

There is a keyword spelling too, for the case where the name is computed:

.. code-block:: python

   with provider(name=scope_name, db=engine):
       ...

``name=`` is only the key when no positional target is given.
With a positional target it is ordinary data, so ``provider("doc", name="report.pdf")`` sets an attribute called ``name`` on the ``"doc"`` namespace.

Instance providers
~~~~~~~~~~~~~~~~~~

``provider(instance)`` registers the object under its own class.

.. code-block:: python

   from dataclasses import dataclass

   @dataclass
   class Config:
       url: str = "sqlite://"

   with provider(Config(url="postgres://prod")):
       handler()

This is the typed form, and :doc:`typed-contexts` covers it in full.
It takes no keyword values: the object already carries its own state.
Passing a class rather than an instance is an error, and says so.

.. code-block:: python

   provider(Config)     # TypeError: did you mean provider(Config(...))?

What a scope does
-----------------

Entering a provider copies the registry of active keys, adds one entry, and publishes the result on a :class:`~contextvars.ContextVar`.
Exiting resets it through the token that ``set`` returned.

Three consequences follow, and all three are the point:

Exit is unconditional.
   The value is removed whether the block finished or raised.
   There is no cleanup to remember and no state to leak into the next request.

Same-key providers nest.
   An inner block shadows the outer value for its duration; the outer value is restored on exit.

   .. code-block:: python

      with provider(Config(url="outer")):
          with provider(Config(url="inner")):
              assert use(Config).url == "inner"
          assert use(Config).url == "outer"

Siblings never see each other.
   Two asyncio tasks entering their own providers hold references to two different registries.
   Nothing is mutated in place, so there is no ordering to reason about.
   :doc:`concurrency` covers the task and thread story.

Reuse and re-entrancy
~~~~~~~~~~~~~~~~~~~~~

The object ``provider(...)`` returns is a context manager that can be entered again after it exits, sequentially:

.. code-block:: python

   p = provider(Config())
   with p:
       ...
   with p:            # fine
       ...

It cannot be active twice at once.
Entering a provider that is already open raises :exc:`RuntimeError`, because a single token cannot describe two nested resets.
Build a second provider for a nested or concurrent block.

Mutation is shared
------------------

The provided object is shared by reference, not copied.
A callee that mutates it mutates the caller's object, which is exactly what you want for an accumulating trace or a per-request cache:

.. code-block:: python

   @dataclass
   class RequestScope:
       trace: list[str] = field(default_factory=list)

   with provider(RequestScope()) as scope:
       dispatch()                       # deep code appends to scope.trace
       log(scope.trace)

Rebinding the name inside a callee does nothing, as with any Python object: ``use(Config)`` returns the object, and assigning to that local only changes the local.
To publish a different value, open another provider.

Layers that accumulate
----------------------

A request scope is rarely known at the boundary.
Middleware knows the request id, authentication adds the actor, the view adds the action, and something five frames below wants all three.
Same-key providers shadow, so a second ``provider("audit", ...)`` would hide what the first one set.

``extend=True`` lays a layer over the namespace the same name already holds:

.. code-block:: python

   with provider("audit", request_id=rid):
       with provider("audit", extend=True, actor_id=user.pk):
           with provider("audit", extend=True, reason=payload["reason"]):
               document.save()              # use("audit") sees all three

       # here use("audit") sees request_id and actor_id again

It is still one name, one registry entry and one O(1) lookup; what changed is what the entry holds.
Exit is the ordinary token reset, so each block restores exactly the layer that was open before it.
Values are laid over attribute by attribute, so a name both layers set takes the inner value.

With nothing open under that name, ``extend=True`` behaves as a plain provider.
That is the property the feature exists for: a layer does not have to know whether it is the first one.

The copy is a snapshot
~~~~~~~~~~~~~~~~~~~~~~

The enclosing namespace is copied on entry and never written to.
That is what keeps a sibling task or thread, which holds a reference to the outer namespace, from seeing a layer that was opened after it started.

From the moment the inner layer is entered, the two layers are two objects.

.. code-block:: python

   with provider("audit", request_id="r-1") as outer:
       with provider("audit", extend=True, actor_id=7):
           outer.request_id = "r-2"         # not visible in here
           use("audit").reason = "cleanup"  # not visible out there

       use("audit").request_id              # "r-2"

Within one layer nothing is copied: a callee still mutates the namespace the block yielded, as it does for any provided value.
It is the layer boundary that copies.

The merge happens on entry rather than when ``provider()`` is called, so a provider object entered twice layers over whatever encloses it each time.

What it does not do
~~~~~~~~~~~~~~~~~~~

``extend=True`` belongs to string-named providers.
An instance provider layers by providing another value, which for a dataclass is :func:`dataclasses.replace`, and asking for ``extend=True`` there says so.

The merge is one level deep, always.
A namespace holding a ``dict`` gets the inner layer's ``dict``, not a merge of the two: merging by value type would make the rule about types rather than about scopes.

Extending a name that holds something other than a :class:`~nodrill.Namespace` raises on entry, naming both types, rather than quietly shadowing it.

Extending a ``frozen=True`` layer works, reading the outer attributes through the read-only view, and the layer it produces is writable unless it asks for ``frozen=True`` itself.
Freezing is a property of the provider rather than of the value, so it is not inherited.

Lazy providers
--------------

A boundary that opens a provider has to build the value first, and it does not know whether anything below will read it.
A middleware that wants request context available for auditing pays for ``request.user`` on every request, while the audit path runs on a few percent of them.

``lazy`` moves the construction to the first read:

.. code-block:: python

   from nodrill import lazy, provider, use

   with provider(lazy(Origin, lambda: Origin(actor_id=request.user.pk))):
       response = get_response(request)     # nothing built yet

   # deep inside, on the few requests that care
   use(Origin).actor_id                     # the factory runs here, once

The key is given explicitly, because there is no value yet to derive it from.

What the scope holds is a cell that resolves on the first operation needing the value and then delegates to it.
Reads, writes, comparisons and the operators behave as the value does, and ``isinstance`` holds before and after resolution, so ordinary code cannot tell the difference:

.. code-block:: python

   with provider(lazy(Config, load)):
       cfg = use(Config)
       isinstance(cfg, Config)              # True, and load has still not run
       cfg.dsn                              # now it runs

What can tell is ``type()``, which reports the cell, exactly as it does for a frozen view.
So does ``repr``, and that one is deliberate: it reports the cell's state instead of resolving.
Inspecting an unresolved value is a debugging act, and a debugging act that opens a database connection is a bad one, which is what keeps :func:`~nodrill.active` safe to print:

.. code-block:: python

   with provider(lazy(Config, load)):
       print(active())                      # {<class 'Config'>: <lazy Config, unresolved>}

``str`` is not treated that way, because ``str(x)`` asks the value for its own text, so ``print(use(Config))`` does resolve.

Once per scope
~~~~~~~~~~~~~~

The result is cached until the scope exits, and a second scope starts unresolved again, including a second entry of the same provider object.
The cache belongs to the scope rather than to the object, which is what keeps a value out of the next request.

The factory runs under the context the scope was entered with, so ``use()`` inside it reads the scope that declared the value:

.. code-block:: python

   with provider(Tenant("acme")):
       with provider(lazy(Report, lambda: Report(owner=use(Tenant).name))):
           with provider(Tenant("other")):
               use(Report).owner            # "acme", not "other"

Two threads inside one scope resolve once: the second waits for the first and reads what it built.
That wait is a plain lock, so keep a slow factory off an event loop, as you would any blocking call.
A factory that raises has its exception cached and re-raised on every later touch, so the failure does not depend on which frame happened to read first.
A factory that reads or returns the key it is building raises :exc:`RuntimeError` instead of recursing.

What it costs
~~~~~~~~~~~~~

Every read goes through the cell, which is the same proxy hop ``frozen=True`` charges, and opening the scope builds the cell and snapshots the context.
That is the right trade for a value that costs a round trip to build and is read on a minority of requests, and the wrong one for a value that is cheap to build and read in a loop.
The README table prices both ends.

``lazy`` composes with ``frozen=True``: the block and the registry get two cells over one build rather than a proxy stacked on a proxy, so a frozen lazy read still costs one hop and the block keeps writing:

.. code-block:: python

   with provider(lazy(Config, load), frozen=True) as cfg:
       cfg.dsn = "the owner can still write"

       use(Config).dsn = "..."              # FrozenContextError

Frozen providers
----------------

When callees should read but not write, provide the value frozen:

.. code-block:: python

   from nodrill import FrozenContextError, provider, use

   with provider(Config(), frozen=True) as cfg:
       cfg.url = "the owner can still write"

       use(Config).url = "..."          # FrozenContextError

The block yields the real object, so whoever opened the scope keeps a writable handle.
Everything reached through ``use()`` gets a read-only view instead.

The view is a proxy that delegates reads to the target.
Attribute access, ``repr``, ``==``, ``hash``, ``dir``, iteration, ``len``, indexing and ``in`` all behave as the real object does, and ``isinstance`` holds, because the proxy reports the target's class:

.. code-block:: python

   with provider(Config(), frozen=True):
       cfg = use(Config)
       isinstance(cfg, Config)          # True
       type(cfg)                        # the proxy class, not Config

Two operations are refused outright.
Writes and deletions raise :exc:`~nodrill.FrozenContextError`, which subclasses :exc:`AttributeError`, so ``getattr``-style guards keep working.
Pickling and copying raise :exc:`TypeError` rather than silently handing back an unfrozen duplicate.

This is a guard rail against accidental writes, not a security boundary.
Code that goes looking for the wrapped object can reach it.

.. seealso::

   :doc:`typed-contexts` for class-keyed lookups and fallbacks.
   :doc:`/content/ref/provider` for the exact signatures.
