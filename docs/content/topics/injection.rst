.. _topics-injection:

Dependency injection
====================

``@inject`` moves a context lookup out of the function body and into the signature.
The decorator inspects the function once, at decoration time, and resolves values at call time.

.. contents::
   :local:
   :depth: 2

Marker style
------------

Annotate the parameter with a marker and give it the ``injected`` default.

.. code-block:: python

   from nodrill import FromCtx, inject, injected, provider

   @inject
   def report(cfg: FromCtx[Config] = injected) -> str:
       return cfg.url

   with provider(Config(url="postgres://prod")):
       report()

``FromCtx[SomeClass]`` injects the nearest instance of that class: it is ``use(SomeClass)``, written in the signature.

To pull a single attribute out of a named provider, annotate with ``from_ctx("name")``:

.. code-block:: python

   from typing import Annotated
   from nodrill import from_ctx

   @inject
   def query(
       sql: str,
       db: Annotated[Engine, from_ctx("app")] = injected,
   ) -> Rows:
       return db.execute(sql)

   with provider("app", db=engine):
       query("SELECT 1")

The attribute taken from the namespace is the one named after the parameter, so ``db`` reads ``use("app").db``.
``from_ctx("app", attr="engine")`` overrides that when the parameter is named something else.
``from_ctx(SomeClass)`` is the explicit spelling of the whole-instance form, for when the annotation is not a plain class.

.. admonition:: from_ctx or FromCtx
   :class: note

   The two build the same marker.
   ``FromCtx`` is the subscript form, ``from_ctx`` the call form.

   Under mypy both work in either position. pyright statically sees ``FromCtx`` as an ``Annotated`` alias and refuses to call it, so ``from_ctx(...)`` is the spelling to use in pyright-checked code and the one used throughout these docs.
   :doc:`/content/misc/design` explains why no single symbol can be both.

Explicit arguments always win
-----------------------------

An argument the caller passes is never overridden, an explicit ``None`` included:

.. code-block:: python

   report()                             # injected from the context
   report(Config(url="sqlite://"))      # exactly what was passed

This is what makes injected code testable with nothing set up: a test calls the function with fakes, outside any provider, and the context is never consulted.
See :doc:`/content/howto/test-injected-code`.

The ``injected`` sentinel
-------------------------

``injected`` is an ``Any``-typed default whose job is to keep the signature satisfiable for type checkers when callers omit the parameter.
``@inject`` treats a parameter still bound to it as one to resolve.

It also fails loudly if it ever reaches a function body.
Attribute access, truthiness and calls on it all raise, with a message pointing at the likely cause, which is a missing ``@inject`` or a missing provider.
The alternative is an :exc:`AttributeError` three frames away from the mistake.

By-name mode
------------

``@inject(from_="app")`` is the opt-in bulk form.
Every parameter whose name matches an attribute of ``use("app")`` is filled from it; ``self`` and ``cls`` are skipped.

.. code-block:: python

   @inject(from_="app")
   def render(user_id: int, theme: str = "light") -> str:
       ...

   with provider("app", user_id=42, theme="dark"):
       render()                         # user_id=42, theme="dark"

Note the second parameter.
In this mode a matching attribute **overrides the parameter default**, which is the reason to opt in: the context is the source of truth for that scope, and a default is only what to do when the context is silent.
Passing the argument explicitly restores control at any call site.

A required parameter that neither the caller nor the context supplied raises :exc:`TypeError` naming the parameter and the context key.

``from_`` accepts a class as well as a string, in which case attributes are read off the provided instance.

What can be decorated
---------------------

``@inject`` works on plain functions, ``async def`` coroutines, instance methods, classmethods and staticmethods, in either decorator order:

.. code-block:: python

   class Service:
       @inject
       def run(self, cfg: FromCtx[Config] = injected) -> None: ...

       @classmethod
       @inject
       def build(cls, cfg: FromCtx[Config] = injected) -> "Service": ...

       @staticmethod
       @inject
       def check(cfg: FromCtx[Config] = injected) -> bool: ...

Two things are rejected.
Injecting into ``*args`` or ``**kwargs`` raises at decoration time, since there is no parameter to fill.
And generator functions, sync or async, are refused outright:

.. code-block:: python

   @inject
   def rows(db: FromCtx[Db] = injected):    # TypeError at decoration
       yield from db.fetch()

Injection resolves when the function is called, but a generator body runs later, at the first ``next()``, possibly under different providers.
Whatever was resolved at call time would be silently stale by then.
Call ``use()`` inside the body instead, and iterate while the provider block is still open:

.. code-block:: python

   def rows():
       db = use(Db)                     # resolved per iteration
       yield from db.fetch()

   with provider(Db()):
       for row in rows():
           ...

When it resolves
----------------

The injection plan is built once, at decoration time, from :func:`inspect.signature` and :func:`typing.get_type_hints`, and compiles into a wrapper that mirrors the function's own signature.
A call binds its arguments natively and pays one sentinel check per injectable parameter: whatever the caller passed explicitly is left untouched, and the rest resolves from the context active at that moment.

If the annotations name something not yet defined, a string annotation under ``from __future__ import annotations`` pointing at a class further down the module, hint resolution raises :exc:`NameError` at decoration time.
``@inject`` catches that and defers the plan to the first call, then caches it.
A name that never resolves fails at call time, with the function named in the error.

Nothing is resolved at import: no provider needs to be active when the module is loaded.

.. seealso::

   :doc:`/content/ref/inject` for the exact signatures.
   :doc:`testing` for the fixture side.
