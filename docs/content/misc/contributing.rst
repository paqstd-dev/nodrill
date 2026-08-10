.. _misc-contributing:

Contributing
============

The repository is at https://github.com/paqstd-dev/nodrill, and the full guide lives in `CONTRIBUTING.md <https://github.com/paqstd-dev/nodrill/blob/main/.github/CONTRIBUTING.md>`_.
This page is the short version.

Development setup
-----------------

The project uses `uv <https://docs.astral.sh/uv/>`_ with a committed lockfile.

.. code-block:: bash

   git clone https://github.com/paqstd-dev/nodrill
   cd nodrill
   make install

Checks
------

A ``Makefile`` wraps everything, and ``make help`` lists the targets.

.. code-block:: bash

   make      # lint, mypy, pyright, coverage, docs, workflow audit
   make -k   # the same, without stopping at the first failure

The individual pieces are ``make lint``, ``make typecheck``, ``make test``, ``make testcov`` and ``make docs``.
Narrow a test run with ``make test ARGS="-k inject -x"``.

Coverage is enforced at 100 percent on branches, and both mypy and pyright have to stay clean.

Building the docs
-----------------

.. code-block:: bash

   make docs         # warnings are errors, as in CI and on Read the Docs
   make docs-serve   # live reload while writing

Open ``docs/_build/html/index.html`` to read the result of a plain build.

House style
-----------

Docstrings are plain PEP 257 prose, an imperative first line, no reStructuredText roles, no bullet lists.
The reference pages in :doc:`/content/ref/index` carry the detailed descriptions, so docstrings stay terse.

Prose in the docs uses semantic line breaks, one sentence per line, so diffs stay readable.

Reporting a bug
---------------

Open an issue with the smallest program that reproduces it, the Python version, and whether threads or asyncio are involved.
Context bugs are almost always about which context a piece of code is running in, so say where the provider was entered and where the lookup happened.
