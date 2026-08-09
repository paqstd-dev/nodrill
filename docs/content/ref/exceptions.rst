.. _ref-exceptions:

Exceptions
==========

.. currentmodule:: nodrill

Both exceptions subclass a builtin, so existing ``except`` clauses keep working.

NoProviderError
---------------

.. exception:: NoProviderError

   Bases: :exc:`LookupError`

   Raised by :func:`use` when no provider is active for the requested key and no fallback is available.

   .. attribute:: key

      The key that was looked up: the string or the class passed to :func:`use`.

   .. attribute:: active_keys

      A tuple of the keys that were active at the time, in registration order.

   The message names the requested key, lists the active ones, and states the fix.
   For a string key it also offers the nearest match, found with :mod:`difflib`; for a class key it mentions :func:`set_default`.

   .. code-block:: text

      NoProviderError: use('datbase'): no active provider for 'datbase'.
      Active providers: 'cache', 'database'. Did you mean 'database'?
      Hint: did you forget `with provider('datbase')`?

   Because it subclasses :exc:`LookupError`, ``except LookupError`` catches it alongside :exc:`KeyError` and :exc:`IndexError`.

FrozenContextError
------------------

.. exception:: FrozenContextError

   Bases: :exc:`AttributeError`

   Raised when setting or deleting an attribute on a value that was provided with ``frozen=True``.

   The object the ``with`` block yields stays writable; only the view reached through :func:`use` refuses writes.

   It subclasses :exc:`AttributeError` so that ``getattr``-style guards and duck-typing checks behave as they would on any other object.
