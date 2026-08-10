.. _howto-refer-to-a-key-you-cannot-import:

Refer to a key you cannot import
================================

A class key has to be imported to be named.
When the module that owns the key already imports the module that wants to read it, importing back is a cycle and Python refuses.
:func:`~nodrill.ref` names the key by import path instead and imports it on the first lookup.

The cycle
---------

Django is the usual shape, where the context module builds the scope out of the models and a model wants to read that scope.

.. code-block:: python
   :caption: myapp/context.py

   from dataclasses import dataclass

   from myapp.models import Article       # the context is built out of the models


   @dataclass
   class RequestScope:
       user_id: int

.. code-block:: python
   :caption: myapp/models.py

   from myapp.context import RequestScope  # ImportError: partially initialized module

   def on_save(sender, instance, **kwargs):
       actor = use(RequestScope).user_id

Importing ``myapp.context`` runs ``myapp.models``, which imports ``myapp.context`` again while it is half-built, and the class does not exist yet.
Nothing about nodrill causes this, and nothing about nodrill can be rearranged to avoid it, because the two modules genuinely need each other.

Name the key instead
--------------------

.. code-block:: python
   :caption: myapp/models.py

   from nodrill import ref, use

   RequestScope = ref("myapp.context:RequestScope")


   def on_save(sender, instance, **kwargs):
       actor = use(RequestScope).user_id

The import happens the first time ``on_save`` runs, which is after both modules are loaded, so the cycle never forms.
The colon says where the module ends and the attribute begins.
``ref("myapp.context.RequestScope")`` works too, resolved from the longest importable prefix.

Nothing else changes.
The provider side stays exactly what it was, because a ref and the class it names are one key:

.. code-block:: python
   :caption: myapp/middleware.py

   from myapp.context import RequestScope


   def scope_middleware(get_response):
       def middleware(request):
           with provider(RequestScope(user_id=request.user.pk)):
               return get_response(request)

       return middleware

Keep the static type
--------------------

``ref()`` returns a key, not a class, so a checker has nothing to infer from.
Give it the import it can follow and give the runtime the one it cannot:

.. code-block:: python
   :caption: myapp/models.py

   from typing import TYPE_CHECKING

   from nodrill import ref, use

   if TYPE_CHECKING:
       from myapp.context import RequestScope
   else:
       RequestScope = ref("myapp.context:RequestScope")


   def on_save(sender, instance, **kwargs) -> int:
       return use(RequestScope).user_id     # inferred: int

mypy and pyright both read the ``TYPE_CHECKING`` branch and type ``use(RequestScope)`` as ``RequestScope``, while the runtime only ever runs the ``else``.
It is the same trade :data:`~nodrill.FromCtx` makes, and it works the same way in a signature:

.. code-block:: python

   from nodrill import FromCtx, inject, injected


   @inject
   def audit(scope: FromCtx[RequestScope] = injected) -> str:
       return f"user {scope.user_id}"

Fail at startup instead of at the first request
-----------------------------------------------

A ref resolves on first use, so a typo in the path surfaces on whichever request touches it first.
:func:`~nodrill.resolve_refs` forces all of them at once:

.. code-block:: python
   :caption: myapp/apps.py

   from django.apps import AppConfig

   from nodrill import resolve_refs


   class MyAppConfig(AppConfig):
       name = "myapp"

       def ready(self):
           resolve_refs()

Every path is imported there, in creation order, and the first bad one raises :exc:`~nodrill.KeyResolutionError` naming itself.

When it still fails
-------------------

A ref cannot make a name exist earlier than it does.
Reading one at module scope, inside the very import that is still running, fails and says so:

.. code-block:: text

   KeyResolutionError: ref('myapp.context:RequestScope'): 'myapp.context' is still
   executing its own import, so 'RequestScope' does not exist yet. The lookup ran
   during that import, so move it inside a function and it will run once the module
   is loaded

The fix is the last clause.
A lookup belongs in a function body, where it runs per request.
At module scope it runs once, at import time, which is the one moment the value it wants cannot be there.

.. seealso::

   :ref:`late-bound-keys` for the API, and :doc:`/content/topics/typed-contexts` for how keys work in general.
