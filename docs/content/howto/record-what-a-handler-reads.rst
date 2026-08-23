.. _howto-record-what-a-handler-reads:

Record what a handler reads
===========================

A parameter is visible in a signature and a context lookup is not, so the question a reviewer actually asks, which is whether this handler can miss in production, is answered today by deploying.

Recording answers it from a test run instead.
Run the suite with ``NODRILL_CONTRACT`` pointing at a directory, render what it recorded into a file, and commit the file.
From then on every pull request that changes what a handler reads changes one line of that file, in the diff, where somebody can see it.

.. code-block:: python
   :caption: app.py

   from collections.abc import Iterator
   from contextlib import contextmanager

   from nodrill import provider, set_default, use


   class Settings:
       def __init__(self, dsn: str = "sqlite://") -> None:
           self.dsn = dsn


   class User:
       def __init__(self, name: str = "anonymous") -> None:
           self.name = name


   class Origin:
       def __init__(self, label: str = "system") -> None:
           self.label = label


   set_default(Origin, Origin)


   def record_write() -> str:
       return f"{use(User).name} from {use(Origin).label} on {use(Settings).dsn}"


   @contextmanager
   def running() -> Iterator[None]:
       """The process-wide layer, opened once the way a main function does."""
       with provider(Settings()):
           yield


   def serve_http(name: str) -> str:
       """The web entry point, which opens both keys the handler reads."""
       with provider("http request"), provider(User(name)), provider(Origin("http")):
           return record_write()


   def run_job(name: str) -> str:
       """The queue entry point, which opens the user and leaves the origin to fall back."""
       with provider("celery worker"), provider(User(name)):
           return record_write()

Record a run, then render it.
Anything that exercises the entry points will do, and a test suite is the usual one.

.. code-block:: console

   $ NODRILL_CONTRACT=.nodrill python -c "
   import app
   with app.running():
       app.serve_http('ada'); app.run_job('grace')"
   $ python -m nodrill contract --from .nodrill
   nodrill: 4 facts under 1 entry point, recorded from 1 process. A contract is only as complete as the run that recorded it.

.. code-block:: text

   # nodrill contract 1
   app:Settings	requires	app:Origin
   app:Settings	requires	app:Settings
   app:Settings	requires	app:User
   app:Settings	set_default	app:Origin

Four facts under one entry point, and the entry point is the configuration this process opened in ``running()``.
That is correct and it is useless.

Name the boundaries
-------------------

An entry point is the outermost provider block open above a read.
An application that opens configuration, a database handle or a settings object above its server loop makes that block the entry point for everything underneath, and the first column stops distinguishing anything.

``NODRILL_CONTRACT_ENTRY`` names the blocks that are boundaries, as rendered keys separated by commas, so a block whose key is in that list mints its own entry point even when something is open above it.

.. code-block:: console

   $ NODRILL_CONTRACT=.nodrill NODRILL_CONTRACT_ENTRY="'http request','celery worker'" python -c "
   import app
   with app.running():
       app.serve_http('ada'); app.run_job('grace')"
   $ python -m nodrill contract --from .nodrill
   nodrill: 6 facts under 2 entry points, recorded from 1 process. A contract is only as complete as the run that recorded it.

.. code-block:: text

   # nodrill contract 1
   'celery worker'	requires	app:Settings
   'celery worker'	requires	app:User
   'celery worker'	set_default	app:Origin
   'http request'	requires	app:Origin
   'http request'	requires	app:Settings
   'http request'	requires	app:User

Now the file says something.
The two boundaries read the same three keys, except that the queue never opens `Origin`, so a :func:`~nodrill.set_default` factory answers for it and every row it writes is labelled `system`.
That is a bug the code cannot show you and no test fails on, and it is one line of a diff.

Pass the same value to the command, so it can tell you about a boundary you named that no block opened, which is what a renamed key looks like.
A boundary that opened and read nothing is a row of the file rather than that message, so the two cases stay apart.

Reading the file
----------------

Three tab-separated fields, sorted, one fact per line.
The first is the entry point, the second is how the read was answered, the third is the key, rendered the way :func:`~nodrill.ref` spells one so two same-named classes in different modules stay apart.
A string key keeps the quotes Python puts on it, which is also what keeps a key holding a tab or a newline from becoming two lines.

The second field is the one to read.

`requires`
   A provider answered, which is the ordinary case.

`set_default`
   No provider was open and a :func:`~nodrill.set_default` factory answered instead.
   Every one of these is a boundary that does not open a key somebody registered a fallback for.

`default`
   No provider was open and the ``use(key, default=...)`` at the call site answered.

`opened`
   A boundary you named opened and nothing under it read the context, so the third field is `nothing` rather than a key.
   It is written only for a boundary that read nothing, which is what keeps a handler reading nothing apart from a boundary the run never reached.

An entry point of `(none)` means no provider block was open at all, which a read can only survive by falling back.
It is what an unwrapped worker thread looks like, and what a read at import time looks like.

Wiring it into CI
-----------------

Two steps, recording and reviewing.

.. code-block:: yaml
   :caption: .github/workflows/ci.yml

   env:
     NODRILL_CONTRACT_ENTRY: "'http request','celery worker'"
   steps:
     - run: NODRILL_CONTRACT=.nodrill pytest
     - run: python -m nodrill contract --from .nodrill --write nodrill.contract
     - run: git diff --exit-code nodrill.contract

The variable is on the job rather than on the recording step, because the command reads it too, and that is what lets it report a boundary you named that no block opened.
The contract file has to be committed for the third step to compare anything, since ``git diff`` says nothing about a path git does not track.

Recording is off unless ``NODRILL_CONTRACT`` is set, and the variable is read once when `nodrill` is imported, which is also why a subprocess your suite spawns records too.
Each process writes its own file into the directory and the command merges them, so a suite that shells out or one using a :class:`~concurrent.futures.ProcessPoolExecutor` needs nothing extra.
The directory is resolved once and written back into the environment, so a relative ``.nodrill`` means the same place to a child your suite starts in another directory.
A directory reused by a later run is not a problem either, since every process of one run shares a run id and the command reads the newest run and says how many older files it left out.

A run id is inherited through the environment, so processes of one run share it only when the process that started them imported `nodrill` itself.
A runner that starts its workers directly is the case where that does not hold, and `pytest -n` from a controller whose `conftest.py` never imports the library is the one you are most likely to meet.
Either import `nodrill` in `conftest.py`, or set ``NODRILL_CONTRACT_RUN`` yourself alongside ``NODRILL_CONTRACT``, and the summary will then report one run rather than shards left out.

What the contract is worth
--------------------------

Exactly as much as the run that recorded it.

A contract lists what the run observed and nothing else, so a key only one untested branch reads is a key the file does not mention.
The summary line says how many facts under how many entry points the conclusion rests on, and it says it every time rather than only when the number is small, because a guarantee that overstates itself is worse than no guarantee.

Five limits are worth knowing before you rely on it.

The entry point is a key, so two boundaries that open the same one are one row set, and a boundary you have not named is whatever is open above it.

A read that raises :exc:`~nodrill.NoProviderError` is not recorded, because it is already loud.
The file is about what an entry point needs and gets, and a miss that reaches a traceback needs no file to be noticed.

A value read through :func:`~nodrill.inject` is recorded exactly like one read through :func:`~nodrill.use`, but a value a handler receives as an ordinary argument is not context and never appears.
The file describes the context a boundary depends on, which is the part of its input that no signature shows.

An ambient read through `nodrill.context` is not recorded, because the ambient namespace is unscoped and has no entry point to be credited to.
A handler that reaches for `context.request_id` shows nothing in the file, and a provider block is what makes a dependency reviewable.

A :func:`~nodrill.lazy` factory runs under the context its own block was opened in, so the keys it reads are credited to whatever entry point was current then rather than to the boundary whose request forced the build.
Read the factory's dependencies under the boundary as well if the row matters, or open the lazy block inside the boundary.

.. rubric:: See also

- :doc:`find-out-why-the-context-is-missing` for a miss that is happening now rather than one that might.
- :doc:`/content/topics/declaring` for naming, in the code, which boundary was meant to provide a key.
