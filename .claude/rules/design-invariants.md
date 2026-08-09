---
paths:
  - "src/nodrill/**/*.py"
---

# Design invariants

Decisions that look arbitrary from the code and have already been argued through.
Changing one is a design change, not a cleanup.

**One registry ContextVar, copy-on-write.**
A single module-level `ContextVar` holds a dict of keys to values; entering copies the dict and keeps the token, exiting resets it.
A var per key would leak, since keys are dynamic and contexts keep dead vars alive.
A ChainMap would make lookup O(depth), and mutating a shared dict would break sibling-task isolation.

**Keys are exact, and `key=` is how you pick one.**
`provider(instance)` registers under `type(instance)` and `use(Cls)` looks up `Cls`; a `Sub` instance does not answer `use(Base)`.
MRO search would make "which provider wins" an ordering question as soon as two subclasses are live.
`provider(instance, key=Base)` names the key instead of deriving it — still one exact key, still O(1) — and is the only reason `use(SomeProtocol)` is expressible.
The key is not checked against the instance: `isinstance` against a plain `Protocol` raises, which would rule out the case `key=` exists for.

**`use()` validates the key only on a miss.**
Registry lookup first; an unhashable key raises `TypeError` from the `dict` and falls into the same validation, so every wrong key kind gets one message.
The check compares against a module-level tuple — `str | type` builds a fresh `UnionType` per evaluation and was most of a lookup's cost.

**Fallback order.**
`set_default(cls, factory)` beats the call-site `use(key, default=...)`, because registration declares the canonical fallback while the call-site default only speaks for one caller.
The factory runs on every miss; caching it would be a global mutable singleton.

**The defaults table is configuration.**
A module-level dict written at import time.
Flowing state lives only in ContextVars — keep it that way.

**Ambient `context` is attribute-only and unscoped.**
It exposes nothing but dunders, so no user attribute can collide with an API name (Flask's `g.get`/`g.pop` is the mistake being avoided).
That rule is what makes `__iter__`/`__len__`/`__contains__` safe to have and `keys()` not: an instance `__dict__` shadows a plain method, a dunder it cannot.
Adding scope to it would just recreate `provider`.
Per-class default registration deliberately lives in `set_default()`, not on this object.

**FromCtx has two spellings on purpose.**
Under `TYPE_CHECKING` it is a generic alias over `Annotated` so `FromCtx[Cfg]` types as `Cfg`; at runtime it is the marker class. pyright refuses to call what it sees as an `Annotated` alias, so `from_ctx()` is the documented call form.
A `Generic[T]` marker class was tried and rejected: it types the parameter as `FromCtx[Cfg]` and breaks explicit-argument calls.

**Generators and classes are rejected at decoration time.**
A generator body runs at `next()`, possibly under different providers, so anything resolved at call time is silently stale.
`use()` inside the body is the supported answer.
A class passes the `callable` check and would come back as a function, so `isinstance` and subclassing break; `__init__` is the thing to decorate.

**`wrap()` rejects async functions.**
`Context.run()` on an `async def` only builds the coroutine, whose body resumes in the caller's context, so the snapshot is lost silently.
Driving the coroutine under the snapshot is what a `Task` already does; creating the task inside the provider block is the answer.

**`frozen=True` is a registry-side proxy, generated from a table.**
The block yields the raw object while the registry stores a read-only view.
Patching `__setattr__` on the instance was rejected: it mutates user objects, breaks on `__slots__` and frozen dataclasses, and is unsafe under concurrency.
Special methods are looked up on the type, so `__getattr__` never sees them — every forwarded protocol has to be on the class, which is why `_frozen.py` generates them from `_FORWARDED`/`_REFLECTED`/`_INVOKED`/`_BLOCKED`.
Add a protocol by adding a table entry, not a method.
In-place operators stay absent on purpose: with no `__iadd__`, `+=` falls back to `__add__` and rebinds the caller's name, leaving the target alone.
Freezing is shallow, `__class__` is spoofed for `isinstance`, pickle and copy are refused; it is a guard rail, not a security boundary.

**`@inject` plans once, and most calls never touch `inspect` again.**
`inspect.signature` plus `get_type_hints(include_extras=True)` at decoration build per-parameter resolvers; a `NameError` there defers the build to the first call and caches it.
The plan carries `pos_guard`, the lowest positional index of any injectable parameter: at or below it no positional argument can have filled one, so with `kwargs_safe` the values go straight into the wrapper's own `kwargs`.
`bind_partial` is the fallback, needed for a parameter passed positionally — including the by-name case where the sentinel is *deleted* so the parameter default applies, which an argument tuple cannot express.
Consequences to keep: injected values arrive as keywords, and on the fast path a missing provider surfaces before a bad-argument `TypeError`.
A racing double build is harmless because plans are deterministic.
Hints that never resolve raise only when something in the signature actually asked for injection; otherwise the call passes through, because hints are read to find markers, not to police annotations.
A union around a marker (`FromCtx[Db] | None`, or the `Optional` Python 3.10 adds to a `None`-defaulted parameter) still injects; the union only widens what an explicit argument may be.
Marker parameters always resolve, even when the parameter has an unrelated default; by-name mode (`from_="app"`) is looser by design and does override defaults.

**`wrap()` snapshots at wrap time and replays per call.**
A single `Context` raises if entered concurrently, so each call replays the snapshot into a fresh `Context` instead of sharing one.
`Executor.submit` needs no replay — each task takes its own `copy_context()`.

**`provider()` takes `*args`.**
`provider("doc", name="report.pdf")` has to treat `name` as data, so a positional target wins and a bare `name=` keyword is the string key.
`Namespace.__init__` is positional-only for the same reason.
`frozen` and `key` are the two names that cannot be prefilled, being the function's own parameters.

**`Namespace` compares by attributes and is unhashable**, following `types.SimpleNamespace`.
Its provider name lives in the mangled `__label` slot, out of `__dict__` and uncollidable; the weak side table it replaced cost a weakref per named provider and forced the class to stay hashable.

**Module layout.**
`_core` is the registry; `_frozen` and `_ambient` hold the proxy and the ambient namespace, which share nothing with it but a ContextVar and are mostly protocol tables.
