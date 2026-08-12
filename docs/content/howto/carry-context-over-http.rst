.. _howto-carry-context-over-http:

Carry context over HTTP as W3C baggage
======================================

Two services in one request path want the same tenant and request id.
The wire format for that is already standardised, the W3C ``baggage`` header, which is a comma-separated list of ``key=value`` members.

An envelope from :func:`~nodrill.export` is a mapping of names to values, and a baggage header is a mapping of names to strings, so the recipe is the twenty lines that turn one into the other.

.. code-block:: python
   :caption: baggage.py

   from typing import Any
   from urllib.parse import quote, unquote

   from nodrill import adopt, export, provider, use


   def to_baggage() -> str:
       """Render the trace namespace as a baggage header value."""
       values = export("trace")["ctx"]["trace"]
       return ",".join(f"{key}={quote(str(value))}" for key, value in values.items())


   def from_baggage(header: str) -> dict[str, Any]:
       """Rebuild an envelope from a baggage header value."""
       members = (member for member in header.split(",") if member.strip())
       values = {}
       for member in members:
           key, _, value = member.partition("=")
           values[key.strip()] = unquote(value.strip())
       return {"v": 1, "ctx": {"trace": values}}


   def call_downstream() -> dict[str, str]:
       """The client side, which is one header on the outgoing request."""
       return {"baggage": to_baggage()}


   def serve(headers: dict[str, str]) -> None:
       """The server side, which is one adopt around the handler."""
       with adopt(from_baggage(headers.get("baggage", ""))):
           handle()


   def handle() -> None:
       print(f"serving {use('trace').request_id} for {use('trace').tenant}")


   if __name__ == "__main__":
       with provider("trace", request_id="req-42", tenant="acme corp"):
           request_headers = call_downstream()
       print(request_headers)
       serve(request_headers)

Output::

   {'baggage': 'request_id=req-42,tenant=acme%20corp'}
   serving req-42 for acme corp

Notes
-----

Percent-encoding is not optional.
A baggage value is a token by the specification, so a space, a comma or an equals sign in a tenant name breaks the header for every service downstream, and :func:`~urllib.parse.quote` is what keeps that from happening.

Baggage is a string-to-string map, so a namespace crossing it should hold strings.
An ``int`` sent over baggage arrives as a ``str``, which is the one place this format is looser than the envelope, so convert on the way in if the receiving side wants a number.

The specification caps a header at 8192 bytes and 64 members, and an intermediary is free to drop what exceeds that.
A request id and a tenant fit with room to spare, and a namespace that does not fit is a namespace that should be a lookup key rather than a payload.

The version field does not survive.
Baggage has no room for one, so the receiving side rebuilds the envelope at the version it understands, and the compatibility contract becomes the header's rather than the envelope's.

``traceparent`` is a different header with a different job, the trace and span ids, and it belongs to whatever tracing library is already producing spans.
Baggage is the one that carries application values, which is what nodrill has.

Every value on that header reached you from another service, so it is input.
An adopted ``tenant`` decides nothing on its own, and it is authorised exactly the way a ``tenant`` from a query string is authorised.

.. seealso::

   :doc:`/content/howto/carry-context-onto-a-queue` for a boundary where you own the format.
   :doc:`/content/topics/concurrency` for what crosses which boundary.
