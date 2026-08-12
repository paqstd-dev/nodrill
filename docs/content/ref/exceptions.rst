.. _ref-exceptions:

Exceptions
==========

.. currentmodule:: nodrill

Every exception subclasses a builtin, so existing ``except`` clauses keep working.

NoProviderError
---------------

.. exception:: NoProviderError

   Bases: :exc:`LookupError`

   Raised by :func:`use` when no provider is active for the requested key and no fallback is available.

   .. attribute:: key

      The key that was looked up, the string or the class passed to :func:`use`.

   .. attribute:: active_keys

      A tuple of the keys that were active at the time, in registration order.

   .. attribute:: diagnosis

      Why the key was not visible from here, as a string, or ``None``.
      Set only when :func:`debug` was on for the lookup, and rendered below the message when it is.

   The message names the requested key, lists the active ones, and states the fix.
   For a string key it also offers the nearest match, found with :mod:`difflib`, and for a class key it mentions :func:`set_default`.

   .. code-block:: text

      NoProviderError: use('datbase'): no active provider for 'datbase'.
      Active providers: 'cache', 'database'. Did you mean 'database'?
      Hint: did you forget `with provider('datbase')`?

   Because it subclasses :exc:`LookupError`, ``except LookupError`` catches it alongside :exc:`KeyError` and :exc:`IndexError`.

KeyResolutionError
------------------

.. exception:: KeyResolutionError

   Bases: :exc:`LookupError`

   Raised when a key built with :func:`ref` cannot be resolved to what its path names.

   .. attribute:: path

      The import path the ref was created with, exactly as written.

   Resolution happens the first time the ref is used as a key, so this is raised out of :func:`use`, :func:`provider`, :func:`set_default` or :func:`resolve_refs` rather than out of :func:`ref` itself.
   The underlying :exc:`ImportError`, where there is one, is the exception's ``__cause__``.

   Three things go wrong, and each says which.

   .. code-block:: text

      KeyResolutionError: ref('myapp.contxt:RequestScope'): cannot import 'myapp.contxt':
      No module named 'myapp.contxt'

      KeyResolutionError: ref('myapp.context:RequestScope'): 'myapp.context' has no
      attribute 'RequestScope'

      KeyResolutionError: ref('myapp.context:RequestScope'): 'myapp.context' is still
      executing its own import, so 'RequestScope' does not exist yet. The lookup ran
      during that import, so move it inside a function and it will run once the module
      is loaded

   The third is the one worth recognising.
   The lookup ran at module scope inside an import cycle, which is the one place a ref cannot help, because the name genuinely does not exist yet.
   See :ref:`howto-refer-to-a-key-you-cannot-import`.

   A failure is not cached, so the same ref resolves normally once the import that was in flight completes.

   It subclasses :exc:`LookupError` alongside :exc:`NoProviderError`, so ``except LookupError`` still catches everything a lookup can raise.

FrozenContextError
------------------

.. exception:: FrozenContextError

   Bases: :exc:`AttributeError`

   Raised when setting or deleting an attribute on a value that was provided with ``frozen=True``.

   The object the ``with`` block yields stays writable, and only the view reached through :func:`use` refuses writes.

   It subclasses :exc:`AttributeError` so that ``getattr``-style guards and duck-typing checks behave as they would on any other object.

EnvelopeVersionError
--------------------

.. exception:: EnvelopeVersionError

   Bases: :exc:`ValueError`

   Raised by :func:`adopt` when a payload carries an envelope version this release cannot read.

   .. attribute:: version

      The version the payload carries, as an integer.

   .. attribute:: supported

      The version this release reads.

   .. code-block:: text

      EnvelopeVersionError: this nodrill reads envelope version 1, and the payload
      carries version 2

   A producer running a release ahead of its consumer is what this exists for, so it is a class of its own rather than a bare :exc:`ValueError`.
   Catching it around a ``with adopt(...)`` block catches the version mismatch and nothing the block itself raises.

   .. code-block:: python

      try:
          with adopt(payload):
              handle()
      except EnvelopeVersionError as mismatch:
          log.warning("dropping context from a newer producer: %s", mismatch)
          handle()

   A payload that is not an envelope at all raises :exc:`TypeError` instead, since that is a caller error rather than a version skew.

UnusedProviderWarning
---------------------

.. exception:: UnusedProviderWarning

   Bases: :exc:`UserWarning`

   Warned by :func:`debug` with ``unused=True`` when a provider block exits and nothing read what it provided.

   A category of its own, so the warning can be silenced by kind rather than by matching the text of its message.

   .. code-block:: python

      warnings.filterwarnings("ignore", category=nodrill.UnusedProviderWarning)

   It subclasses :exc:`UserWarning`, which is what an unfiltered warning is reported as anyway, so nothing that already catches those stops seeing it.
