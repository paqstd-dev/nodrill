.. _ref-index:

API reference
=============

.. module:: nodrill

Everything nodrill exports, described exactly.
The whole public surface is twenty-one names, all importable from the top-level package:

.. code-block:: python

   from nodrill import (
       Executor,
       FromCtx,
       FrozenContextError,
       KeyResolutionError,
       Namespace,
       NoProviderError,
       active,
       context,
       debug,
       explain,
       from_ctx,
       inject,
       injected,
       isolate,
       lazy,
       provider,
       ref,
       resolve_refs,
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
   debugging

.. rubric:: Version

.. data:: __version__
   :type: str

   The installed version, as a string.

.. rubric:: See also

The :doc:`topic guides </content/topics/index>` explain how these fit together.
