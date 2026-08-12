.. _ref-concurrency:

Carrying context
================

.. currentmodule:: nodrill

:class:`threading.Thread` does not inherit context, so ``wrap`` and ``Executor`` carry it across that boundary. asyncio needs neither: tasks snapshot the context natively.

Beyond one process nothing can be carried by reference, so ``export`` and ``adopt`` carry a copy of what you name.

wrap
----

.. function:: wrap(fn)

   Bind ``fn`` to a snapshot of the context active when ``wrap`` was called, and return a callable with the same signature.

   :raises TypeError: ``fn`` is a coroutine or async-generator function.
      Calling one only builds the coroutine, whose body then resumes in the caller's context, so the snapshot would be silently dropped.
      asyncio needs no wrapping, since a task created inside a provider block snapshots the context itself.

   The snapshot is taken at ``wrap()`` time, not at call time.
   Wrapping at import time binds import-time state.

   Each invocation replays the snapshot into a fresh :class:`~contextvars.Context`, so the wrapped callable is safe to call concurrently, and any contextvar writes the callee makes stay inside that call.

   The wrapper is built with :func:`functools.wraps`, so ``__name__``, ``__doc__`` and ``__wrapped__`` carry through.

   .. code-block:: python

      with provider(Config()):
          Thread(target=wrap(job)).start()

Executor
--------

.. class:: Executor

   A :class:`~concurrent.futures.ThreadPoolExecutor` subclass whose tasks run under the context active when they were submitted.

   The constructor, ``shutdown``, and the context-manager protocol are inherited unchanged.
   Only :meth:`~concurrent.futures.Executor.submit` is overridden, so :meth:`~concurrent.futures.Executor.map` propagates context as well.

   Each task runs under its own :func:`~contextvars.copy_context` snapshot, so a worker's writes never leak into another task or back to the submitter.

   .. code-block:: python

      with provider(Config()), Executor(max_workers=4) as pool:
          results = list(pool.map(job, range(10)))

export
------

.. function:: export(*names)

   Return the named string providers as a plain ``dict``, ready for :func:`json.dumps`.

   :raises NoProviderError: no provider is active for one of the names, exactly as :func:`use` raises it.
   :raises TypeError: a name is not a string, a name holds something other than a namespace, or a value is not portable.
   :raises ValueError: a value is a float :mod:`json` cannot represent, or a container holds itself.

   Nothing travels unless it is named here, so ``export()`` with no names is an empty envelope and a provider holding a database session cannot leave by accident.

   .. code-block:: python

      with provider("trace", request_id=rid, tenant="acme"):
          payload = export("trace")    # {'v': 1, 'ctx': {'trace': {...}}}

   A value has to be exactly a :class:`str`, :class:`int`, :class:`float`, :class:`bool`, ``None``, :class:`list` or :class:`dict`, all the way down, with dict keys strings and floats finite.
   Exactly, so a :class:`~uuid.UUID`, a :class:`~decimal.Decimal`, an :class:`~enum.Enum` member and a :class:`tuple` are all refused rather than converted, because each would arrive on the other side as a different type than the one that was provided.
   The message names the provider, the path to the value and its type.

   .. code-block:: text

      TypeError: export('trace'): tags[1] is of type UUID, which does not survive a
      round trip through JSON. A portable value is a str, int, float, bool, None, or
      a list or dict of those

   Class-keyed providers do not travel, since a class cannot be reopened on the other side without importing it, and ``export(Config)`` says so.

   The returned lists and dicts are copies, so the envelope is a snapshot of the moment it was taken.
   The registry holds one value per key, so a name resolves to the nearest enclosing provider, which is what :func:`use` would return on the same line.

   A name nobody opened raises rather than exporting an empty namespace.
   Where a namespace really is optional, ``"trace" in active()`` is the guard, since :func:`active` is keyed the same way.

adopt
-----

.. function:: adopt(payload)

   Open the providers an envelope carries, for the length of the block.

   :raises EnvelopeVersionError: the payload carries a version this release does not read.
   :raises TypeError: the payload is not an envelope, or a value in it is not portable.
   :raises ValueError: a value is a float :mod:`json` cannot represent, or a container holds itself.
      :func:`json.loads` accepts the ``Infinity`` literal, so a payload really can carry one.

   Each namespace becomes an ordinary provider, so it shadows any provider of the same name, unwinds on exit even if the block raises, and appears in :func:`debug` and in exception notes like any other.

   .. code-block:: python

      with adopt(payload):
          handle()                     # use("trace").request_id

   The payload is checked the way :func:`export` checks what it writes, and the whole payload is checked before the first provider opens, so a bad namespace leaves nothing half-adopted.

   An adopted value is input.
   Nothing here says the payload came from a producer you trust, and a ``tenant`` that arrived this way is authorised the way any other request field is authorised.

set_codec
---------

.. function:: set_codec(*, dump=None, load=None)

   Register the pair that carries what JSON cannot, process wide.

   :raises TypeError: ``dump`` or ``load`` is neither callable nor ``None``.

   ``dump(values)`` maps one namespace's values on the way out and ``load(values)`` maps them back on the way in, each taking a mapping of attribute names to values and returning one.

   .. code-block:: python

      set_codec(dump=to_builtins, load=rebuild)   # once, at startup

   Whatever ``dump`` returns is checked the way any exported value is, so the envelope stays JSON however the codec works inside, and a codec handing back an object of its own fails at the boundary that made it.
   ``load`` runs after the payload has been checked, never before, so a malformed payload is refused without reaching the codec at all.

   Each call states the whole codec, so ``set_codec()`` clears both halves, and a service that only produces or only consumes registers the one half it needs.
   The pair is process-wide configuration rather than something a scope decides, because both ends of a boundary have to agree on the format.
   :func:`isolate` does not clear it, matching :func:`annotate_exceptions`.

   An exception raised by your codec propagates as itself.

   .. warning::

      ``load`` runs on whatever arrived, so a codec that unpickles executes whatever the payload says.
      Keep such a codec to a boundary inside one trust domain, and never put one on a broker or an HTTP header.

   :doc:`/content/howto/carry-an-object-across-a-boundary` shows a tagged dataclass codec, an ``msgspec`` one, and what pickle costs.
