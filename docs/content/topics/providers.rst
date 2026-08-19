.. _topics-providers:

Providers and scopes
====================

A provider makes one value visible to everything that runs inside a ``with`` block, however deep.
This page covers the shapes ``provider`` accepts, what a scope does on entry and exit, how mutation flows, and what ``lazy``, ``frozen=True`` and ``sealed=True`` change.

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

The namespace is a plain attribute bag, so read, write and delete attributes on it as you would on any object.
Reading a name that was never set raises :exc:`AttributeError` naming the provider and listing what is available, which is worth a lot once several named providers are active at once.

There is a keyword spelling too, for the case where the name is computed.

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
It takes no keyword values, since the object already carries its own state.
Passing a class rather than an instance is an error, and says so.

.. code-block:: python

   provider(Config)     # TypeError: did you mean provider(Config(...))?

What a scope does
-----------------

Entering a provider copies the registry of active keys, adds one entry, and publishes the result on a :class:`~contextvars.ContextVar`.
Exiting resets it through the token that ``set`` returned.

Three consequences follow, and all three are the point.

Exit is unconditional.
   The value is removed whether the block finished or raised.
   There is no cleanup to remember and no state to leak into the next request.
   The one shape that does not hold is a block closed from a context it did not open in, which cannot unwind and reports itself with :exc:`~nodrill.OrphanedProviderWarning`.

Same-key providers nest.
   An inner block shadows the outer value for its duration, and the outer value is restored on exit.

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

The object ``provider(...)`` returns is a context manager that can be entered again after it exits, sequentially.

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
A callee that mutates it mutates the caller's object, which is exactly what you want for an accumulating trace or a per-request cache.

.. code-block:: python

   @dataclass
   class RequestScope:
       trace: list[str] = field(default_factory=list)

   with provider(RequestScope()) as scope:
       dispatch()                       # deep code appends to scope.trace
       log(scope.trace)

Rebinding the name inside a callee does nothing, as with any Python object, because ``use(Config)`` returns the object and assigning to that local only changes the local.
To publish a different value, open another provider.

Layers that accumulate
----------------------

A request scope is rarely known at the boundary.
Middleware knows the request id, authentication adds the actor, the view adds the action, and something five frames below wants all three.
Same-key providers shadow, so a second ``provider("audit", ...)`` would hide what the first one set.

``extend=True`` lays a layer over the namespace the same name already holds.

.. code-block:: python

   with provider("audit", request_id=rid):
       with provider("audit", extend=True, actor_id=user.pk):
           with provider("audit", extend=True, reason=payload["reason"]):
               document.save()              # use("audit") sees all three

       # here use("audit") sees request_id and actor_id again

It is still one name, one registry entry and one O(1) lookup, and what changed is what the entry holds.
Exit is the ordinary token reset, so each block restores exactly the layer that was open before it.
Values are laid over attribute by attribute, so a name both layers set takes the inner value.

With nothing open under that name, ``extend=True`` behaves as a plain provider.
That is the property the feature exists for, since a layer does not have to know whether it is the first one.

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

Within one layer nothing is copied, so a callee still mutates the namespace the block yielded, as it does for any provided value.
It is the layer boundary that copies.

The merge happens on entry rather than when ``provider()`` is called, so a provider object entered twice layers over whatever encloses it each time.

What it does not do
~~~~~~~~~~~~~~~~~~~

``extend=True`` belongs to string-named providers.
An instance provider layers by providing another value, which for a dataclass is :func:`dataclasses.replace`, and asking for ``extend=True`` there says so.

The merge is one level deep, always.
A namespace holding a ``dict`` gets the inner layer's ``dict`` rather than a merge of the two, because merging by value type would make the rule about types rather than about scopes.

Extending a name that holds something other than a :class:`~nodrill.Namespace` raises on entry, naming both types, rather than quietly shadowing it.

Extending a ``frozen=True`` layer works, reading the outer attributes through the read-only view, and the layer it produces is writable unless it asks for ``frozen=True`` itself.
Freezing is a property of the provider rather than of the value, so it is not inherited.

Lazy providers
--------------

A boundary that opens a provider has to build the value first, and it does not know whether anything below will read it.
A middleware that wants request context available for auditing pays for ``request.user`` on every request, while the audit path runs on a few percent of them.

``lazy`` moves the construction to the first read.

.. code-block:: python

   from nodrill import lazy, provider, use

   with provider(lazy(Origin, lambda: Origin(actor_id=request.user.pk))):
       response = get_response(request)     # nothing built yet

   # deep inside, on the few requests that care
   use(Origin).actor_id                     # the factory runs here, once

The key is given explicitly, because there is no value yet to derive it from.

What the scope holds is a cell that resolves on the first operation needing the value and then delegates to it.
Reads, writes, comparisons and the operators behave as the value does, and ``isinstance`` holds before and after resolution, so ordinary code cannot tell the difference.

.. code-block:: python

   with provider(lazy(Config, load)):
       cfg = use(Config)
       isinstance(cfg, Config)              # True, and load has still not run
       cfg.dsn                              # now it runs

What can tell is ``type()``, which reports the cell, exactly as it does for a frozen view.
So does ``repr``, and that one is deliberate, reporting the cell's state instead of resolving.
Inspecting an unresolved value is a debugging act, and a debugging act that opens a database connection is a bad one, which is what keeps :func:`~nodrill.active` safe to print.

.. code-block:: python

   with provider(lazy(Config, load)):
       print(active())                      # {<class 'Config'>: <lazy Config, unresolved>}

``str`` is not treated that way, because ``str(x)`` asks the value for its own text, so ``print(use(Config))`` does resolve.

Once per scope
~~~~~~~~~~~~~~

The result is cached until the scope exits, and a second scope starts unresolved again, including a second entry of the same provider object.
The cache belongs to the scope rather than to the object, which is what keeps a value out of the next request.

The factory runs under the context the scope was entered with, so ``use()`` inside it reads the scope that declared the value.

.. code-block:: python

   with provider(Tenant("acme")):
       with provider(lazy(Report, lambda: Report(owner=use(Tenant).name))):
           with provider(Tenant("other")):
               use(Report).owner            # "acme", not "other"

Two threads inside one scope resolve once, and the second waits for the first and reads what it built.
That wait is a plain lock, so keep a slow factory off an event loop, as you would any blocking call.
A factory that raises has its exception cached and re-raised on every later touch, so the failure does not depend on which frame happened to read first.
A factory that reads or returns the key it is building raises :exc:`RuntimeError` instead of recursing.

What it costs
~~~~~~~~~~~~~

Every read goes through the cell, which is the same proxy hop ``frozen=True`` charges, and opening the scope builds the cell and snapshots the context.
That is the right trade for a value that costs a round trip to build and is read on a minority of requests, and the wrong one for a value that is cheap to build and read in a loop.
The README table prices both ends.

``lazy`` composes with ``frozen=True``, where the block and the registry get two cells over one build rather than a proxy stacked on a proxy, so a frozen lazy read still costs one hop and the block keeps writing.

.. code-block:: python

   with provider(lazy(Config, load), frozen=True) as cfg:
       cfg.dsn = "the owner can still write"

       use(Config).dsn = "..."              # FrozenContextError

Frozen providers
----------------

When callees should read but not write, provide the value frozen.

.. code-block:: python

   from nodrill import FrozenContextError, provider, use

   with provider(Config(), frozen=True) as cfg:
       cfg.url = "the owner can still write"

       use(Config).url = "..."          # FrozenContextError

The block yields the real object, so whoever opened the scope keeps a writable handle.
Everything reached through ``use()`` gets a read-only view instead.

The view is a proxy that delegates reads to the target.
Attribute access, ``repr``, ``==``, ``hash``, ``dir``, iteration, ``len``, indexing and ``in`` all behave as the real object does, and ``isinstance`` holds, because the proxy reports the target's class.

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

.. _topics-providers-sealed:

Sealed providers
----------------

Freezing says what a consumer may do with a value.
Sealing says when.

A provided value can outlive the block that provided it, and usually by accident.

.. code-block:: python

   with provider(Session(dsn)) as session:
       background.append(lambda: session.query(...))    # runs later

By the time that closure runs the session is closed, or worse, back in a pool and serving another request.
Nothing raises where the mistake was made.
Something fails later, somewhere else, with a message about a closed connection or with quietly wrong results.

The routes into a stale reference are all ordinary code, a closure, a background task, a cache, a callback registered inside the block.

``sealed=True`` makes the value say so.

.. code-block:: python

   from nodrill import ExpiredScopeError, provider

   with provider(Session(dsn), sealed=True) as session:
       handle(session)

   session.query(...)                   # ExpiredScopeError

.. code-block:: text

   ExpiredScopeError: Session.query was used after its provider block exited.
     opened at web.py:42
     used here at worker.py:88

The sites are the feature.
The frame that raises is never the frame that made the mistake, so a message naming only the last one would leave the search where it started.
Where the block exited is recorded too and printed only when it differs from where it opened, since a ``with`` statement exits on its own line and an :class:`~contextlib.ExitStack` does not.

The seal covers the object the block yields
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is the one place where sealing and freezing are not symmetric.
``frozen=True`` deliberately hands the block a writable handle, because somebody has to write.
``sealed=True`` seals both sides, because the reference that escapes is the one the ``with`` statement yielded, and sealing only the registry side would miss the case the feature is for.

While the block is open the value behaves as itself, ``use(Session) is session`` holds as it does for an ordinary provider, and ``isinstance`` answers as it always did.

The one thing to know is that the block now holds a view rather than the object, which the interpreter can tell apart even though your code cannot.
A C-level check of the concrete type sees the view, so ``json.dumps`` on a sealed ``dict``, ``open()`` on a sealed :class:`~pathlib.Path` and :class:`weakref.ref` on a sealed object all refuse.
Every view is like this, ``frozen=True`` and :func:`~nodrill.lazy` included, and what ``frozen=True`` changes is only who meets it, since the block keeps the raw object and a consumer reading through :func:`~nodrill.use` holds the view.
The same applies to :func:`dataclasses.asdict`, :func:`dataclasses.replace` and :func:`dataclasses.is_dataclass`, which read ``type()`` and so do not see a dataclass, while :func:`dataclasses.fields` reads the instance and works.
Seal the objects your own code reads and pass the raw value where a C-level API is going to inspect it.

A second entry does not revive the first
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Each entry mints its own seal, so a reference held from an earlier entry of the same provider object stays dead.

.. code-block:: python

   block = provider(Session(dsn), sealed=True)

   with block as first:
       pass
   with block as second:
       first.query(...)                 # ExpiredScopeError, and second works

A reference that came back to life would be a worse bug than the one being reported, since it would work in testing and hand a pooled session to the wrong request in production.

Composing with lazy and frozen
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The three flags answer three different questions, so they stack, and the seal is the outer one.

.. code-block:: python

   with provider(lazy(Session, connect), frozen=True, sealed=True) as session:
       session.dsn = "the owner can still write"

An expired write reports the expiry rather than the freeze, and an expired read of a lazy value raises without running the factory, which is what keeps a scope nobody read from opening a connection on its way out.
``isinstance`` holds through either combination, since each view answers with the class the one under it reports.

What it does not catch
~~~~~~~~~~~~~~~~~~~~~~

This is a guard rail, in exactly the sense ``frozen=True`` is one, and the limits are the same ones.

- An attribute that escapes instead of the object is not detected, so ``session.connection`` handed out separately stays live, and so does a bound method such as ``session.query`` stored somewhere before the block ended.
- A protocol that hands back another object hands back the target's own, so an iterator taken with ``iter()`` and whatever ``with use(...)`` yields both outlive the block.
- Sealing is shallow, as freezing is.
- ``type(proxy)`` tells the truth, and code that goes looking for the wrapped object can reach it.
- A layer opened with ``extend=True`` copies the attributes it inherits, so the copy is an ordinary namespace and outlives the sealed block it came from.
- The check costs a hop on every access for the whole life of the block, which is why it is off by default.
- A sealed proxy holds its target for as long as anything holds the proxy, so this reports an escape rather than preventing one.

Pickling and copying are refused outright, live or expired, since either would hand back an unsealed duplicate.

The expiry is reported and never softened.
:exc:`~nodrill.ExpiredScopeError` is not an :exc:`AttributeError`, so ``getattr(value, name, default)`` raises rather than answering with the default, and ``hasattr`` raises rather than returning ``False``.
A guard that quietly took the other branch would hide exactly the escape being reported.

Three things keep answering after the block exits, and none of them hands back the target.
``isinstance`` holds, because a check inside an ``except`` clause must not itself raise, while ``repr`` describes the expiry instead of blowing up and ``dir`` still lists the names, because those two are what a debugger reaches for.

.. code-block:: python

   repr(session)                        # '<expired Session, opened at web.py:42, exited at web.py:47>'

What it does catch is the common case, which is the whole object captured and touched later.

.. seealso::

   :doc:`typed-contexts` for class-keyed lookups and fallbacks.
   :doc:`/content/ref/provider` for the exact signatures.
