.. _ref-index:

API reference
=============

.. module:: nodrill

Everything nodrill exports, described exactly.
The whole public surface is fifteen names, all importable from the top-level package:

.. code-block:: python

   from nodrill import (
       Executor,
       FromCtx,
       FrozenContextError,
       Namespace,
       NoProviderError,
       active,
       context,
       from_ctx,
       inject,
       injected,
       isolate,
       provider,
       set_default,
       use,
       wrap,
   )

Nothing under ``nodrill._*`` is public, and the module layout may change between releases.

.. toctree::
   :maxdepth: 2

   provider
   inject
   concurrency
   exceptions
   testing

.. rubric:: Version

.. data:: __version__
   :type: str

   The installed version, as a string.

.. rubric:: See also

The :doc:`topic guides </content/topics/index>` explain how these fit together.
