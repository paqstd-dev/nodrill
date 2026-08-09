.. _howto-scope-a-web-request:

Scope a web request
===================

The goal is that one layer knows how the request scope is wired, the layers in between know nothing at all, and the leaves declare what they need.

The middleware opens a provider for the request.
The dispatch layer keeps its signature clean.
The handler takes the scope as an injected parameter, and a helper further down reaches for it with ``use()``.

.. code-block:: python
   :caption: request_scope.py

   from dataclasses import dataclass, field

   from nodrill import FromCtx, inject, injected, provider, use


   @dataclass
   class Request:
       path: str
       user_id: int


   @dataclass
   class RequestScope:
       request: Request
       trace: list[str] = field(default_factory=list)


   def middleware(request: Request) -> str:
       """Enter one provider per request; everything below shares the scope."""
       with provider(RequestScope(request=request)) as scope:
           response = dispatch()
           return f"{response}  [trace: {' > '.join(scope.trace)}]"


   def dispatch() -> str:
       """Framework plumbing: knows nothing about RequestScope."""
       return render_profile()


   @inject
   def render_profile(scope: FromCtx[RequestScope] = injected) -> str:
       scope.trace.append("render_profile")
       audit("profile-view")
       request = scope.request
       return f"profile of user {request.user_id} at {request.path}"


   def audit(event: str) -> None:
       # Plain use() works too, and the mutation is visible up the stack.
       use(RequestScope).trace.append(f"audit:{event}")


   if __name__ == "__main__":
       print(middleware(Request(path="/me", user_id=42)))
       print(middleware(Request(path="/me", user_id=7)))

Running it prints one line per request, each with its own trace::

   profile of user 42 at /me  [trace: render_profile > audit:profile-view]
   profile of user 7 at /me  [trace: render_profile > audit:profile-view]

Notes
-----

The scope object is shared by reference, which is why ``audit`` appending to ``scope.trace`` is visible to ``middleware`` after ``dispatch`` returns.
When callees should read but not write, provide it with ``frozen=True`` and keep the writable handle the ``with`` block yields.

``dispatch`` is the layer this exists for.
In a real application it is router code, a middleware chain, or a framework's view dispatcher, and adding a parameter to it is not an option.

The provider block covers the whole request and nothing else, so a value from one request can never be read by the next, even if an exception unwinds through the middleware.

On a thread-per-request server this works as written.
On a pool-per-request server it also works, because the provider is entered inside the worker rather than inherited from the accepting thread.
If you hand work off to another thread mid-request, see :doc:`run-work-in-threads`.
