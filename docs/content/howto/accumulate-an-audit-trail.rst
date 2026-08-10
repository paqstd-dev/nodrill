.. _howto-accumulate-an-audit-trail:

Accumulate an audit trail
=========================

An audit row wants the request id, the actor, the action and the reason, and no single layer knows all four.
Middleware knows the request, authentication knows the actor, the view names the action, the serializer knows the reason, and the receiver that writes the row runs below all of them.

Each layer opens ``provider("audit", extend=True, ...)`` and adds what it knows.
The receiver reads one name and gets everything that was contributed above it.

The example is written against stand-ins for the Django pieces, so it runs as pasted.
In a real project ``post_save`` is a signal receiver, ``audit_middleware`` is middleware, and ``Document`` is a model.

.. code-block:: python
   :caption: audit_trail.py

   from dataclasses import dataclass, field

   from nodrill import provider, use


   # Stand-ins for the framework, so the file runs as pasted.
   @dataclass
   class Request:
       path: str
       user_id: int
       payload: dict[str, str] = field(default_factory=dict)


   @dataclass
   class Document:
       title: str

       def save(self) -> None:
           post_save(self)


   AUDIT_LOG: list[str] = []


   def post_save(document: Document) -> None:
       """A signal receiver, far below anything that knows what a request is."""
       fields = ", ".join(f"{name}={value!r}" for name, value in sorted(vars(use("audit")).items()))
       AUDIT_LOG.append(f"saved {document.title!r}: {fields}")


   def audit_middleware(request: Request) -> str:
       """The outermost layer: it knows the request and nothing else about the scope."""
       with provider("audit", extend=True, request_id=f"req-{len(AUDIT_LOG)}", path=request.path):
           return authenticate(request)


   def authenticate(request: Request) -> str:
       """The next layer adds the actor, without knowing what came before or after."""
       with provider("audit", extend=True, actor_id=request.user_id):
           return update_view(request)


   def update_view(request: Request) -> str:
       """The view names the action, and the serializer below it adds the reason."""
       with provider("audit", extend=True, action="document.update"):
           return serialize(request.payload)


   def serialize(payload: dict[str, str]) -> str:
       with provider("audit", extend=True, reason=payload["reason"]):
           Document(title=payload["title"]).save()
           return "204 No Content"


   def nightly_cleanup() -> None:
       """No middleware ran here, so this layer is the only one."""
       with provider("audit", extend=True, action="document.purge"):
           Document(title="stale draft").save()


   if __name__ == "__main__":
       audit_middleware(
           Request(path="/documents/7", user_id=42, payload={"title": "Q3 report", "reason": "typo"})
       )
       nightly_cleanup()
       for line in AUDIT_LOG:
           print(line)

Running it prints the row the request built, and then the row the job built out of one layer::

   saved 'Q3 report': action='document.update', actor_id=42, path='/documents/7', reason='typo', request_id='req-0'
   saved 'stale draft': action='document.purge'

Notes
-----

Every layer is spelled the same way, ``provider("audit", extend=True, ...)``, and none of them checks whether anything is open above it.
``nightly_cleanup`` is the case that makes this matter, running with no middleware, no actor and no request, and needing no branch for that.

The receiver reads ``vars(use("audit"))`` rather than named attributes, because what accumulated depends on which layers ran.
Where a field is genuinely required, read it as an attribute and let the :exc:`AttributeError` name the provider and list what was set.

The layers do not share a namespace.
Each ``extend=True`` copies what it found and lays its own values over the copy, so a request that hands work to a thread or a task mid-flight cannot have a later layer appear underneath it.
The flip side is that a write made in an outer layer after an inner one was entered stays outside it.
:ref:`topics-providers` has the rule in full.

Nothing here unwinds by hand.
Each ``with`` block restores exactly the layer that was open before it, including when the view raises, which is the difference from an accumulating request-global such as Flask's ``g``.

The receiver would work just as well with :func:`~nodrill.inject`, which fills parameters from a namespace by name.

.. code-block:: python

   @inject(from_="audit")
   def post_save(document: Document, request_id: str = "", actor_id: int | None = None) -> None:
       ...

.. seealso::

   :doc:`scope-a-web-request` for the single-layer case, where one provider at the boundary is enough.
