.. _intro-install:

Installation
============

nodrill is a pure-Python package with no dependencies.

.. code-block:: bash

   pip install nodrill

Or, with uv.

.. code-block:: bash

   uv add nodrill

Requirements
------------

Python 3.10 or newer.
That is the floor for the ``str | type`` union syntax the library uses internally, and for the typing behaviour ``@inject`` relies on.

nodrill is built on :mod:`contextvars` from the standard library and pulls in nothing else, in any environment.

Type checking
-------------

The package ships a ``py.typed`` marker, so type checkers read the inline annotations directly.
The public API is checked against both mypy in strict mode and pyright, and the one place where the two disagree is documented in :doc:`/content/topics/injection`.

Verifying the install
---------------------

.. code-block:: python

   from nodrill import provider, use

   with provider("greeting", text="it works"):
       print(use("greeting").text)

If that prints ``it works``, you are set.
Continue with the :doc:`tutorial <quickstart>`.
