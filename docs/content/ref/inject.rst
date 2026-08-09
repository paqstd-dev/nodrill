.. _ref-inject:

Injection
=========

.. currentmodule:: nodrill

The decorator, the parameter markers, and the sentinel default.

inject
------

.. decorator:: inject
               inject(*, from_=None)

   Fill missing parameters from the current context at call time.

   :param from_: Opt into by-name mode.
      A string name or a class; every parameter whose name matches an attribute of ``use(from_)`` is filled from it.
   :raises TypeError: ``from_`` is neither a string nor a class; the decorated object is not callable; it is a class, a generator or an async-generator function; or a marker annotates ``*args`` or ``**kwargs``.

   Two styles, usable together on one function.

   *Marker style* annotates individual parameters with :data:`FromCtx` or :func:`from_ctx` and defaults them to :data:`injected`.

   *By-name style*, ``@inject(from_="app")``, fills every eligible parameter from the attributes of one context object.
   ``self`` and ``cls`` are skipped, as are parameters that already carry a marker.
   In this mode a matching attribute overrides the parameter's default; a required parameter that neither the caller nor the context supplied raises :exc:`TypeError` naming the parameter and the key.

   An argument passed by the caller is never overridden, an explicit ``None`` included.

   Applies to plain functions, ``async def`` coroutines, instance methods, classmethods and staticmethods, in either decorator order.
   A function with no injectable parameters is returned unwrapped.

   The injection plan is built once, at decoration time, from :func:`inspect.signature` and :func:`typing.get_type_hints`.
   Calls whose positional arguments cannot reach an injected parameter, which includes every method call where only ``self`` is positional, resolve straight into keyword arguments and skip signature binding entirely.
   Only a call that passes an injected parameter positionally is bound against the signature.
   If hints cannot be resolved then, because a string annotation names something defined later, the plan is deferred to the first call and cached.
   A name that never resolves raises :exc:`NameError` at call time, naming the function, unless no annotation on it asks for injection at all: a function nodrill has nothing to do for is called through untouched rather than broken over hints only a checker reads.
   Annotations that do carry a marker have to name things that exist at runtime, so the import cannot sit behind ``TYPE_CHECKING``.

   .. code-block:: python

      @inject
      def report(cfg: FromCtx[Config] = injected) -> str:
          return cfg.url

      @inject(from_="app")
      def render(user_id: int, theme: str = "light") -> str: ...

FromCtx
-------

.. data:: FromCtx

   The parameter marker, in subscript form.

   ``FromCtx[SomeClass]`` marks a parameter as ``use(SomeClass)``.
   At runtime it evaluates to ``Annotated[SomeClass, FromCtx()]``; to a type checker it is ``SomeClass``, so the body and any explicit argument check against the real type.

   A union around the marker, ``FromCtx[SomeClass] | None``, still injects: the union only widens what an explicit argument may be.
   The same holds for the ``Optional`` that :func:`typing.get_type_hints` adds by itself on Python 3.10 when a marked parameter defaults to ``None``.

   The call form, ``FromCtx("app")``, builds the same marker as :func:`from_ctx` but is rejected by pyright, which statically sees an ``Annotated`` alias and refuses to call it.
   Use :func:`from_ctx` in pyright-checked code.

from_ctx
--------

.. function:: from_ctx(key=None, attr=None)

   Build a parameter marker for use inside :data:`typing.Annotated`.

   :param key: A string name or a class.
      ``None`` means the annotated type is the key, which requires that type to be a plain class.
   :param attr: The attribute to read off the context object.
      Defaults to the parameter's own name for string keys.
   :raises TypeError: ``key`` is neither a string, a class, nor ``None``; or a bare marker annotates something that is not a plain class.

   Three shapes:

   .. code-block:: python

      Annotated[Config, from_ctx()]                    # use(Config)
      Annotated[Engine, from_ctx("app")]               # use("app").<param name>
      Annotated[Engine, from_ctx("app", attr="db")]    # use("app").db

   ``FromCtx[Config]`` is shorthand for the first.
   Clean under both mypy and pyright, which is why it is the spelling used throughout these docs.

injected
--------

.. data:: injected
   :type: typing.Any

   The default for an injectable parameter.

   Typed :data:`~typing.Any`, so a signature like ``def f(db: FromCtx[Db] = injected)`` stays satisfiable for type checkers when callers omit the argument.
   :func:`inject` treats a parameter still bound to it as one to resolve, the same as an omitted one.

   If it ever reaches a function body it fails loudly.
   Attribute access, truthiness, calls, and the protocols a stray value tends to land in next, iteration, ``len``, indexing, ``in``, the orderings, arithmetic, ``with``, ``await`` and ``async for``, all raise with a message pointing at a missing ``@inject`` or a missing provider.
   ``==`` is left at the default, so containers and identity checks behave normally.

Rejected shapes
---------------

Generator functions, sync and async, raise :exc:`TypeError` at decoration time.
A generator body runs after the call, at the first ``next()``, possibly under different providers, so anything resolved at call time would be silently stale.
Call :func:`use` inside the body instead, and iterate while the provider block is open.

Variadic parameters cannot be injected.
A marker on ``*args`` or ``**kwargs`` raises :exc:`TypeError`, and in by-name mode variadics are skipped.

Classes raise :exc:`TypeError` too.
A class is callable, so it would decorate cleanly and then be replaced by a function, breaking :func:`isinstance` and subclassing.
Decorate ``__init__`` instead.
