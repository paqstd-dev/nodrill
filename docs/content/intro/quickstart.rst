.. _intro-quickstart:

Tutorial
========

This page builds one small application and, along the way, touches every feature nodrill has.
It should take about ten minutes.

.. contents::
   :local:
   :depth: 2

The problem
-----------

Here is a shape that turns up in every codebase that grows past a few thousand lines.

.. code-block:: python
   :caption: before

   def handle_request(request):
       db = connect()
       return render_page(request, db)

   def render_page(request, db):
       return render_sidebar(request, db)

   def render_sidebar(request, db):
       return f"{request.user_id} @ {db}"

``render_page`` does not use ``db``.
It carries it because ``render_sidebar`` needs it.
Every function between the place a value is created and the place it is read grows a parameter it never touches, and each one has to be edited again the next time the leaf needs something new.

Passing arguments is the right default, and for three functions it is fine.
The cost shows up when the layers in between are framework code you do not own, or when the leaf is fifteen frames down.

Providing a value
-----------------

``provider`` puts a value on the current context, and ``use`` reads it anywhere below.

.. code-block:: python
   :caption: after

   from nodrill import provider, use

   def handle_request(request):
       with provider("db", handle=connect()):
           return render_page(request)

   def render_page(request):
       return render_sidebar(request)

   def render_sidebar(request):
       return f"{request.user_id} @ {use('db').handle}"

``provider("db", handle=...)`` registers a namespace under the name ``"db"`` and prefills it from the keyword arguments.
``use("db")`` returns that namespace to any code running inside the ``with`` block, at any depth.
``render_page`` is back to one parameter and never has to change again.

The block is a scope in the ordinary Python sense.
On exit the value is gone, whether the block ended normally or raised.

.. code-block:: python

   with provider("db", handle="postgres://"):
       use("db")        # the namespace
   use("db")            # NoProviderError

The namespace is a plain attribute bag.
Read it, write to it, and callees see the write, because everyone is looking at the same object.

.. code-block:: python

   with provider("request") as ctx:
       ctx.trace = []
       do_work()                   # appends to ctx.trace
       print(ctx.trace)

Using classes as keys
---------------------

String names are quick to write.
Classes are typed.

.. code-block:: python

   from dataclasses import dataclass
   from nodrill import provider, use

   @dataclass
   class Config:
       debug: bool = False
       url: str = "sqlite://"

   def handler() -> None:
       cfg = use(Config)           # inferred type: Config
       print(cfg.url)

   with provider(Config(url="postgres://prod")):
       handler()

``provider(instance)`` registers the instance under ``type(instance)``, and ``use(SomeClass)`` hands it back with the static type intact.
Your editor completes ``cfg.url``, and a typo in the attribute name is a type error rather than a runtime one.

Lookups are by exact class.
Providing a subclass instance does not answer ``use(Base)`` unless you say so with ``provider(instance, key=Base)``; the reasoning is in :doc:`/content/misc/design`.

Nesting and shadowing
---------------------

A provider for a key already in scope shadows the outer one, and the outer value comes back on exit.

.. code-block:: python

   with provider(Config(url="outer")):
       with provider(Config(url="inner")):
           assert use(Config).url == "inner"
       assert use(Config).url == "outer"

This is what makes per-request and per-tenant overrides work: the outer scope sets a default, an inner block replaces it for a subtree, and nothing has to be restored by hand.

When nothing is provided
------------------------

A miss has three answers, tried in order.

.. code-block:: python

   from nodrill import set_default

   set_default(Config, Config)          # 1. a registered factory

   use(Config, default=None)            # 2. a call-site fallback

   use(Config)                          # 3. NoProviderError

``set_default(cls, factory)`` declares the canonical fallback for a class: the factory runs on every miss and returns a fresh instance each time, never a cached singleton.
``default=`` is the local, ``dict.get``-shaped escape hatch, and the only fallback available to string keys.
With neither, ``use`` raises :exc:`~nodrill.NoProviderError`, which names the key, lists what is active, and suggests the close match:

.. code-block:: text

   NoProviderError: use('datbase'): no active provider for 'datbase'.
   Active providers: 'cache', 'database'. Did you mean 'database'?
   Hint: did you forget `with provider('datbase')`?

Injecting parameters
--------------------

``use()`` inside a function body works everywhere.
When you would rather see the dependency in the signature, ``@inject`` fills it in for you.

.. code-block:: python

   from nodrill import FromCtx, inject, injected, provider

   @inject
   def report(cfg: FromCtx[Config] = injected) -> str:
       return cfg.url

   with provider(Config(url="postgres://prod")):
       report()                         # "postgres://prod"

   report(Config(url="sqlite://"))      # "sqlite://"

``FromCtx[Config]`` marks the parameter as coming from the context, and ``injected`` is the default that keeps the signature satisfiable when the caller omits it.
The last line is the point of the design: **an explicitly passed argument always wins**.
Tests call the function directly with a fake and never set up a provider.

Threads and tasks
-----------------

asyncio needs nothing from you.
``create_task`` and ``gather`` snapshot the context natively, so a task sees the providers that were active when it was created, and sibling tasks cannot see each other's.

Plain threads are the exception: :class:`threading.Thread` does not inherit context, so a thread started inside a provider block starts empty.
Two helpers cover it.

.. code-block:: python

   from threading import Thread
   from nodrill import Executor, provider, wrap

   with provider(Config()):
       Thread(target=wrap(job)).start()      # bound to a snapshot

       with Executor(max_workers=4) as pool: # ThreadPoolExecutor subclass
           pool.submit(job)                  # sees the submit-time context

Isolating tests
---------------

Because explicit arguments win, most injected code needs no fixture at all.
For the tests that do exercise providers, ``isolate()`` gives each one a clean slate.

.. code-block:: python

   import pytest
   import nodrill

   @pytest.fixture(autouse=True)
   def _clean_context():
       with nodrill.isolate():
           yield

Where to go from here
---------------------

You have now seen the whole library.
The :doc:`topic guides </content/topics/index>` go through each piece properly: what ``frozen=True`` does, how the ambient :data:`~nodrill.context` namespace differs from a provider, what ``@inject`` will and will not accept.

If you would rather read a finished program, the :doc:`how-to guides </content/howto/index>` are complete, runnable files.
