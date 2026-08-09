.. _misc-design:

Design notes
============

Notes on the non-obvious decisions, for future maintainers.

.. contents::
   :local:
   :depth: 1

One copy-on-write registry, not a ContextVar per key
----------------------------------------------------

A single module-level :class:`~contextvars.ContextVar` holds a ``dict`` mapping keys, strings and classes alike, to provided values.
Entering a provider copies the dict, adds its key, and calls ``set()``, keeping the token; exiting calls ``reset(token)``.

Keys are created dynamically, while ContextVars are documented to be module-level only; creating one per key at runtime leaks by design, because contexts keep dead vars alive.
One var plus copy-on-write gives O(1) lookup and parent isolation for free: sibling tasks hold references to the old dict, which nobody ever mutates.
A :class:`~collections.ChainMap` of scopes would make lookups O(depth), and mutating a shared dict with undo-on-exit would break sibling-task isolation.
The cost is that entering a provider is O(active keys); registries hold a handful of entries, not thousands.

Exact-type keys, and key=
-------------------------

``provider(instance)`` registers under exactly ``type(instance)``, and ``use(SomeClass)`` looks up exactly ``SomeClass``.
Registering a ``Sub`` instance does not answer ``use(Base)``.

Context keys are identities, not hierarchies: MRO search would make ``use`` O(mro x registry) and turn "which provider answers ``use(Base)``" into an ordering question the moment two subclasses are active.

``provider(instance, key=Base)`` names the key instead of deriving it, which keeps that model intact: still one provider, still one exact key, still an O(1) lookup.
It is the library's one extension point, and it exists for a reason the alternatives do not cover: without it, ``use()`` can only be given a concrete class, so a consumer cannot depend on a :class:`~typing.Protocol` or an ABC.
A string key would work but throws away the typed return.

The key is not validated against the instance.
``isinstance`` against a plain ``Protocol`` raises, so the check would rule out the main use case; the call site is where a type checker already looks.

use() checks the key on the miss path
--------------------------------------

``use()`` reads the registry first and validates the key only when the lookup does not hit.
An unhashable key makes the ``dict`` lookup raise :exc:`TypeError`, which is caught and falls into the same validation, so every wrong key kind still gets one message.

The check itself compares against a module-level tuple rather than ``str | type``.
The union form is not a constant: it builds a new :class:`types.UnionType` on every evaluation, which on this path was most of the cost of a lookup.

set_default and use(default=)
-----------------------------

Fallbacks for a miss come in two shapes.

``set_default(cls, factory)`` registers a per-class factory consulted when no provider is active.
The factory runs on every miss rather than caching its first result: a cached instance would be a global mutable singleton with stale-state bugs attached, and per-miss construction mirrors ``dataclasses.field(default_factory=...)``.

``use(key, default=...)`` is the local, ``dict.get``-style escape hatch, and the only fallback available to string keys.
A registered factory wins over the call-site default, because registration declares the canonical fallback for the class, while the call-site default only says what this one caller can live with.

The defaults table itself is a module-level dict of factories written at import time.
That is configuration, not flowing state; state lives only in ContextVars.

The ambient context object
--------------------------

``nodrill.context`` is an attribute namespace backed by its own ContextVar: set anywhere, read anywhere below, per-thread and per-task.
:class:`threading.local` would give the same thread story but leaks across asyncio tasks sharing a worker; contextvars handles both.
It is deliberately separate from the provider registry, so ``use()`` and ``context.attr`` cannot shadow each other.

Ambient writes are unscoped, and that is documented rather than fixed: adding scope would recreate ``provider``.

The object exposes only dunder methods, so user attribute names can never collide with API names.
Flask's ``g`` has ``g.get`` and ``g.pop``, which shadow user keys named ``get`` and ``pop``; that mistake is avoidable at design time and not after release.

An earlier revision also made the object callable as the default-registration API.
Registration and ambient state are unrelated features, and a callable-plus-attribute-bag object is the kind of dual-role API that cannot be renamed once shipped, so registration moved to ``set_default()``.

FromCtx typing
--------------

The goal: ``def f(cfg: FromCtx[AppCtx])`` must type-check with the body seeing ``AppCtx``, and ``Annotated[Engine, FromCtx("app")]`` must be expressible.

Under ``TYPE_CHECKING``, ``FromCtx`` is a generic alias over :data:`~typing.Annotated`, so ``FromCtx[AppCtx]`` is ``AppCtx`` to a checker; at runtime it is the marker class, and ``FromCtx[AppCtx]`` returns ``Annotated[AppCtx, FromCtx()]``, keeping both views in agreement.

mypy accepts all spellings, since it does not evaluate ``Annotated`` metadata. pyright evaluates the metadata and refuses to call what it statically sees as an ``Annotated`` alias, while accepting the subscript form.
No single symbol can be both callable and an ``Annotated`` alias under pyright today; dishka hit the same wall and ships ``FromDishka`` for subscripts next to ``FromComponent`` for calls.
Hence ``from_ctx()``, a plain function returning the same marker, clean under every checker and the documented call spelling.

A ``Generic[T]`` marker class was rejected: the subscript then types the parameter as ``FromCtx[AppCtx]``, which breaks explicit-argument calls.

Generators are rejected
-----------------------

``@inject`` raises :exc:`TypeError` for generator and async-generator functions at decoration time.

Injection resolves when the function is called; a generator body runs later, at ``next()``, possibly under different providers, so whatever was resolved at call time is silently stale.
Deferring resolution to the first iteration is the same trap with different timing.

Re-entering a captured context on every ``__next__`` was considered and shelved: it changes iteration cost and semantics for a niche win, and nothing here precludes adding it later.
The working alternative, ``use()`` inside the body, resolves per iteration and is tested.

frozen=True is a registry-side proxy
------------------------------------

The registry stores a read-only proxy while the ``with`` block yields the raw object.
Consumers reached through ``use()`` can read but not write; the owner keeps a writable handle without any enter/exit mutation of the target.

Special methods are looked up on the type, never through ``__getattr__``, so a proxy forwards exactly the dunders written on its class and nothing else.
That is why the forwarded set is a table the class is generated from, rather than a handful of hand-written methods: the gaps in a hand-written set are invisible until something prints ``<frozen ...>`` out of an f-string.

Reads, ``repr``, ``str``, ``format``, comparison, ``hash``, ``dir``, the container, numeric, calling, context-manager and awaitable protocols all delegate; ``__class__`` is spoofed so ``isinstance`` holds; pickling and copying are refused, since they would silently produce unfrozen duplicates.
``__eq__`` and ``__ne__`` pass the target's own answer through instead of coercing it to ``bool``, which matters for targets that return something else.

Item assignment and deletion raise.
In-place operators deliberately have no dunder: without ``__iadd__`` the interpreter falls back to ``__add__`` and rebinds the caller's name, which leaves the target alone, so blocking them would only break the harmless case.

Freezing is shallow, and stays that way: a deep freeze means proxying every value read off the target, which changes identities and costs on every access.
``type(proxy)`` still tells the truth; this is a guard rail, not a security boundary.

Patching ``__setattr__`` on the instance was rejected, since it mutates user objects, breaks on ``__slots__`` and frozen dataclasses, and is unsafe under concurrency.

lazy is a cell, not a branch in use()
-------------------------------------

``provider(lazy(Cls, factory))`` stores a second registry-side proxy, generated from the same tables as the frozen one.

The alternative was one line in ``use()``::

   if type(value) is _LazyCell:
       value = value.get()

It reads better and costs every lookup in every program that never uses the feature, on the one path this project has optimised hardest.
It also cannot write the resolved value back without mutating a registry dict that sibling contexts hold, which is the copy-on-write invariant.
Resolving at ``provider()`` enter behind a flag is eager construction with extra words, and caching in ``set_default`` was already rejected for being a global mutable singleton, where a cell caches per scope.

The build belongs to the scope, minted on entry and dropped on exit, so re-entering one provider object resolves again.
A cell handed to a callee can still outlive the block, and resolving it late is well defined rather than forbidden: the factory runs under a snapshot of the scope the provider was entered with, taken once the key itself is published.
Without that snapshot, deferring construction would also move it from the definition site to whichever scope read first, so an inner provider for the same key could decide what an outer lazy value is built from, and the wrong answer would be cached.
It costs one ``copy_context()`` per entry, and unlike ``wrap()`` the snapshot needs no per-call replay, since a factory runs at most once and so enters that ``Context`` at most once.

Once-only is a reentrant lock's job.
The owner check beside it covers what that lock lets through, a factory reading the key it is building, which comes back on its own thread and raises rather than recursing.
A failing factory has its exception cached, because a failure that depends on which frame touched the value first is not debuggable, while a ``BaseException`` is not, since a cancelled task says nothing about the factory.

``repr`` is the one operation that does not resolve, because ``active()`` is printed exactly when something has already gone wrong.

Unlike the frozen proxy, the cell forwards writes, item assignment and the in-place operators: a lazy value that is not also frozen has to behave as the value would, and an absent ``__iadd__`` would silently rebind the caller's name instead of extending the provided list.

``frozen=True`` composes by splitting the views rather than by stacking a second proxy, so the block holds a plain cell and the registry a freezing one over the same build.
Wrapping inside the build would have handed the block a read-only handle, which is the half of the frozen contract that exists so the owner keeps writing.

``lazy(Cls, factory)`` is annotated as returning ``Cls`` while it really returns an inert carrier, the one place here where the annotation is not the runtime type.
It describes what the caller ends up holding, since ``provider()`` turns the carrier into a cell answering ``isinstance`` and every read as a ``Cls``; typing it as the carrier would type every ``use(Cls)`` downstream as something no caller ever sees.

The name takes the key first, since ``lazy(factory, key=Origin)`` puts the interesting word last, and string keys are refused because a string-named provider has nothing to defer.

@inject mechanics
-----------------

The plan is built once at decoration: :func:`inspect.signature` plus ``get_type_hints(include_extras=True)`` find the marked parameters and their context keys.

Calls never touch :mod:`inspect` again.
The plan compiles into a wrapper that mirrors the function's own signature, the way :mod:`dataclasses` builds ``__init__``: the interpreter binds arguments natively, each injectable parameter defaults to the public ``injected`` sentinel, and the body is one identity check per parameter — an inlined registry read on the hit path, with a miss handed to the same fallback path ``use()`` takes, which owns ``set_default`` and the error.
There is no repacking through ``*args`` and no signature walk at call time, whatever shape the call takes; compilation is paid once, at decoration, in microseconds per function.
The generated source is registered in :mod:`linecache` under a counter-unique filename, so a traceback through a wrapper shows its actual lines and ``pdb`` can step through them; the entry is removed again when the wrapper itself is garbage collected.
The resolution helpers are bound into the wrapper at decoration, so patching nodrill internals afterwards does not change compiled wrappers; the supported seams are ``provider()``, ``set_default()`` and ``isolate()``.

Two consequences are worth knowing.
A bad call fails before any resolution runs: unknown keywords and over-long positional lists natively, and an under-supplied call with a missing-argument :exc:`TypeError` worded exactly as the interpreter words it — though the arity range such messages report counts injectable parameters as optional, which from the caller's side they are.
And a call that passes an injected parameter positionally costs the same as any other, since there is no fallback path for it to land on.

If hint resolution hits a :exc:`NameError` at decoration, from string annotations naming things defined later, plan build and compilation move behind a dispatching wrapper to the first call and are cached; a racing double build is harmless because plans are deterministic.
A name that never resolves fails at call time with the function named in the error, unless nothing in the signature asked for injection: hints exist here to find markers, so a function without one is called through rather than broken over a ``TYPE_CHECKING``-only annotation.

The ``injected`` sentinel is an ``Any``-typed default with teeth: attribute access, truthiness, and calls on it raise with a message pointing at the missing ``@inject`` or provider, because the alternative is an :exc:`AttributeError` three frames away from the cause.

Marker parameters always resolve, and a resolution failure raises even if the parameter has an unrelated default; class-level fallbacks belong in ``set_default``, not scattered through signatures.
By-name mode is looser on purpose: attributes fill matching parameters, defaults included, and a required parameter that neither the caller nor the context supplied raises a :exc:`TypeError` naming the parameter and the context key.

wrap() rebuilds a Context per call
----------------------------------

:func:`~contextvars.copy_context` is taken once, at ``wrap()`` time; that is the semantic, binding the callable to the context where it was wrapped.

A single :class:`~contextvars.Context` object raises if entered concurrently, so sharing the snapshot between calls would make the wrapped callable non-reentrant across threads.
Each call therefore builds an empty ``Context`` and replays the snapshot's items into it: equal state, safely concurrent, and callee writes stay in the per-call copy.
``Executor.submit`` needs no replay, since each submitted task takes its own fresh ``copy_context()``.

Async functions are rejected instead of wrapped.
Running one inside a ``Context`` only builds the coroutine, whose body then resumes in whatever context awaits it, so the snapshot would be dropped without a word.
Driving the coroutine step by step under the snapshot is what a :class:`~asyncio.Task` already does, and creating the task inside the provider block is the supported answer.

provider() signature
--------------------

``provider(name="app")`` must work as a keyword, and string mode also accepts prefill kwargs, so ``name`` cannot be an ordinary first parameter: ``provider("doc", name="report.pdf")`` has to treat ``name`` as data.

The implementation takes ``*args``; a positional target wins and a ``name=`` keyword becomes a prefill value, and with no positional the ``name=`` keyword is the key, strings only.
``Namespace.__init__(self, /, **values)`` is positional-only for the same reason, so ``Namespace(self=1)`` is legal data.

Errors
------

``NoProviderError`` subclasses :exc:`LookupError` and carries the requested key and active keys; the message lists active providers, suggests close string matches via :mod:`difflib`, and hints at the fix for each key kind.

Namespaces remember which provider created them and say so in attribute errors, which matters once several named providers are active.
The name lives in a private slot, mangled to ``_Namespace__label``, so it stays out of ``__dict__`` and cannot collide with a value; a weak side table did the same job before, at the cost of a weakref per named provider and a hashable ``Namespace``.

``Namespace`` compares by attributes and is therefore unhashable, following :class:`types.SimpleNamespace`.

The ambient ``context`` raises plain :exc:`AttributeError` listing currently-set keys, keeping ``getattr`` and ``hasattr`` idioms working.

Module layout
-------------

``_core`` is the registry: ``provider``, ``use``, ``set_default``, ``active``, ``Namespace``, ``isolate``.
``_frozen`` and ``_ambient`` hold the two things that share nothing with it but a ContextVar, the read-only proxy and the ambient namespace, both of which are mostly protocol tables and would otherwise dominate the file they sit in.
``_inject``, ``_concurrency`` and ``_errors`` are the remaining features.
Nothing under ``nodrill._*`` is public.

isolate()
---------

The test suite needs fresh context state per test, and so does every downstream suite; without a public helper, each of them would reach into private module state.

``isolate()`` is that fixture body: providers and ambient state start empty inside the block, default registrations are rolled back on exit, and pre-existing defaults stay visible because they are configuration, not state.
