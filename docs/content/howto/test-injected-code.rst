.. _howto-test-injected-code:

Test injected code
==================

Injected code is testable without providers, because an argument the caller passes is never overridden.
The production path opens providers at the composition root.
The test path passes fakes and never touches ``use()``.

.. code-block:: python
   :caption: welcome.py

   from dataclasses import dataclass

   from nodrill import FromCtx, inject, injected, provider


   @dataclass
   class Mailer:
       smtp_host: str

       def send(self, to: str, body: str) -> str:
           return f"[{self.smtp_host}] -> {to}: {body}"


   @dataclass
   class AppConfig:
       sender_name: str = "nodrill-app"


   @inject
   def send_welcome(
       to: str,
       mailer: FromCtx[Mailer] = injected,
       config: FromCtx[AppConfig] = injected,
   ) -> str:
       return mailer.send(to, f"Welcome from {config.sender_name}!")


   def production_path() -> str:
       """Real wiring: one provider block at the composition root."""
       with provider(Mailer(smtp_host="smtp.prod:25")), provider(AppConfig()):
           return send_welcome("alice@example.com")

The test needs neither a provider nor a patch.

.. code-block:: python
   :caption: test_welcome.py

   from welcome import AppConfig, Mailer, send_welcome


   def test_send_welcome_uses_the_configured_sender():
       fake = Mailer(smtp_host="fake")

       result = send_welcome(
           "bob@test",
           mailer=fake,
           config=AppConfig(sender_name="test-suite"),
       )

       assert result == "[fake] -> bob@test: Welcome from test-suite!"

Notes
-----

An explicit ``None`` counts as an explicit argument and is not replaced.
That matters when ``None`` is a meaningful value for the parameter, and it means you can test the "no mailer" branch without inventing a sentinel.

Fakes need no relationship to the real class beyond the methods under test.
The parameter is annotated for the type checker's benefit, and at runtime nodrill only fills what was left out.

For code that calls ``use()`` in the body rather than taking it as a parameter, open a provider in the test.

.. code-block:: python

   def test_audit_records_the_request():
       with provider(RequestScope(request_id="r-1")):
           assert audit("login").startswith("r-1")

Add ``isolate()`` as an autouse fixture if the suite touches the ambient namespace, whose writes are unscoped and would otherwise carry from one test into the next.

.. code-block:: python
   :caption: conftest.py

   import nodrill
   import pytest


   @pytest.fixture(autouse=True)
   def _clean_context():
       with nodrill.isolate():
           yield

.. seealso::

   :doc:`/content/topics/testing` for the full picture.
