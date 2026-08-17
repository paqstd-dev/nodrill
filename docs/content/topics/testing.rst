.. _topics-testing:

Testing
=======

Most code that uses nodrill is testable with no setup at all.
The parts that are not need one fixture.

.. contents::
   :local:
   :depth: 2

Prefer explicit arguments
-------------------------

``@inject`` never overrides an argument the caller passed, so a test can call an injected function directly, outside any provider, with whatever fakes it likes.

.. code-block:: python

   @inject
   def send_welcome(to: str, mailer: FromCtx[Mailer] = injected) -> str:
       return mailer.send(to, "hello")

   def test_send_welcome():
       assert send_welcome("bob@test", mailer=FakeMailer()) == "..."

No providers, no patching, no fixture.
This is the main reason injection is worth having over a bare module-level global, because the seam is a parameter, and parameters are the easiest thing in Python to control from a test.

Code that calls ``use()`` in the body has no such seam, and does need a scope.

.. code-block:: python

   def test_audit():
       with provider(RequestScope(request_id="r-1")):
           assert audit("login").startswith("r-1")

A ``with provider(...)`` in a test reads well and needs no fixture either.
Reach for the fixture below when you want a guarantee rather than a habit.

isolate()
---------

``isolate()`` runs a block against fresh context state and restores the outer state on exit.
Inside it, no providers are active and the ambient namespace is empty, and any ``set_default`` registration made inside is rolled back on the way out.

As a pytest fixture.

.. code-block:: python
   :caption: conftest.py

   import nodrill
   import pytest

   @pytest.fixture(autouse=True)
   def _clean_context():
       with nodrill.isolate():
           yield

That makes every test start from the same state regardless of what ran before it, which matters most for the ambient namespace, whose writes are unscoped by design and would otherwise survive from one test into the next.

It also makes the suite order-independent, which is worth having if you run tests in a random order.

Registrations made outside the block stay visible inside it, being configuration written at import time rather than state, and hiding them would break every test of code that relies on a registered default.
Overriding one inside a block is fine and is undone on exit.

Testing async code
------------------

Nothing special is required.
``asyncio.run`` and pytest-asyncio both run the coroutine in a context that inherits the test's, so a provider opened in the test body is visible inside the coroutine.

.. code-block:: python

   async def test_handler():
       with provider(Config(url="sqlite://")):
           assert await handler() == "sqlite://"

Testing threaded code
---------------------

A thread started from a test sees no providers, exactly as it would in production.
Test the two paths deliberately, asserting that the bare thread raises :exc:`~nodrill.NoProviderError` and that the wrapped one does not.

.. code-block:: python

   def test_thread_needs_wrap():
       errors: list[BaseException] = []

       def target() -> None:
           try:
               use(Config)
           except NoProviderError as exc:
               errors.append(exc)

       with provider(Config()):
           t = Thread(target=target)
           t.start()
           t.join()

       assert errors

Exceptions raised inside a thread do not propagate to the caller, so ferry them back through a list, as above, or the assertion will pass no matter what the thread did.

Testing code that adopts
------------------------

A task body written as ``with adopt(payload):`` needs a payload, and there are two ways to make one.

Build it with :func:`~nodrill.export` inside a provider block when the test is about the round trip, since that is the payload a real producer sends.
Write the envelope literally when the test is about the consumer alone, which keeps the fixture readable and lets it carry exactly the wrong thing on purpose.

.. code-block:: python

   def envelope(**values):
       """Return a one-namespace envelope, for tests that do not go through export()."""
       return {"v": 1, "ctx": {"trace": values}}


   def test_handler_reads_the_adopted_id():
       with adopt(envelope(request_id="req-42")):
           assert handle() == "req-42"

A literal fixture pins the version, so a test suite full of them is a place the envelope's own version number goes stale.
That is the same trade the HTTP recipe makes, as long as the round-trip test above exists somewhere too.

:func:`~nodrill.isolate` does not clear the codec, so a test that registers one clears it itself.

.. code-block:: python

   @pytest.fixture
   def actor_codec():
       nodrill.set_codec(dump=dump, load=load)
       yield
       nodrill.set_codec()

Without that fixture the codec leaks into every later test in the process, and because the suite is order-shuffled the failure lands on whichever test ran next rather than on the one that registered it.

.. seealso::

   :doc:`/content/howto/test-injected-code` for a worked example.
