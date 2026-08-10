.. _howto-add-context-to-every-log-record:

Add the context to every log record
===================================

A request id belongs on every line a request logs, and passing it to each call is the drilling this library removes.
The usual answers are a :class:`logging.Filter` over a private :class:`~contextvars.ContextVar`, structlog's contextvars integration, or a whole tracing dependency pulled in for one field.

A provider is already that ContextVar, so the filter is ten lines and reads the same public API everything else reads.

.. code-block:: python
   :caption: logs.py

   import logging

   from nodrill import provider, use


   class ScopeFilter(logging.Filter):
       """Put one string-named provider's fields on every record, under a flat prefix."""

       def __init__(self, scope: str, *fields: str) -> None:
           super().__init__()
           self.scope = scope
           self.fields = fields

       def filter(self, record: logging.LogRecord) -> bool:
           namespace = use(self.scope, default=None)
           for field in self.fields:
               setattr(record, f"nodrill_{self.scope}_{field}", getattr(namespace, field, "-"))
           return True


   def main() -> None:
       handler = logging.StreamHandler()
       handler.setFormatter(logging.Formatter("%(nodrill_audit_request_id)s %(message)s"))
       handler.addFilter(ScopeFilter("audit", "request_id", "reason"))
       logging.basicConfig(level=logging.INFO, handlers=[handler])

       logging.getLogger("shop").info("no scope is open")
       with provider("audit", request_id="01JB2", reason="bulk import"):
           logging.getLogger("shop.orders").info("one line about an order")


   if __name__ == "__main__":
       main()

Output::

   - no scope is open
   01JB2 one line about an order

The four rules the recipe encodes
---------------------------------

**The filter goes on the handler, not on the root logger.**
A logger's filters run only for records logged through that logger, and a record propagating up from ``shop.orders`` never meets the root logger's filters.
A handler's filters run for every record it emits, which is what "on every line" means.

**The fields are declared rather than discovered.**
A ``%``-style formatter naming an attribute the record does not carry fails at emit time, and :meth:`logging.Handler.handleError` swallows it, so the line is dropped and a traceback goes to stderr instead of to the log.
Listing the fields in the constructor is what lets the filter fill each one with a placeholder when no scope is open, as the first output line shows.
A structured handler that reads whatever it finds can drop the list and copy ``vars(namespace)`` instead.

**Names are flattened with an underscore and prefixed.**
``%(nodrill_audit_request_id)s`` works in any stdlib formatter, while a dotted name reads as an attribute access that is not one, and an unprefixed ``request_id`` can collide with a :class:`~logging.LogRecord` attribute or with another library's field.

**One filter per named provider.**
Two providers both claiming ``request_id`` would otherwise race for one attribute, and which one wins would depend on iteration order.
Adding a second ``ScopeFilter("http", "path")`` is clearer than one filter that walks :func:`~nodrill.active`.

Class-keyed providers are left out on purpose, since the ``repr`` of a ``RequestScope`` is not a set of fields, and a class that wants to contribute can expose one through a plain accessor the filter calls.

This is a recipe and not a shipped class, because the questions above have per-project answers and a name in the public API cannot be withdrawn once it has one.

.. rubric:: See also

:doc:`/content/howto/see-the-context-in-a-traceback` for the same values on a failure, where no logging is configured at all.
:doc:`/content/ref/provider` for :func:`~nodrill.use` and the ``default=`` that keeps the filter working outside every scope.
