---
paths:
  - "tests/**/*.py"
---

# Tests

- `conftest.py` wraps every test in `nodrill.isolate()`, so providers, ambient state and default registrations start clean.
  Never reach into module-private state to reset things.
- Test docstrings describe the behavior under test in one line.
  They never reference tickets, task specs or review rounds.
- The suite runs under `pytest-randomly`, so order is shuffled: no test may depend on another having run.
  `filterwarnings = ["error"]` is on, and every test has a 30s thread timeout.
- Concurrency tests use real threads and event objects rather than sleeps; the existing harnesses in `test_threads.py` ferry exceptions back to the main thread, reuse them.
- Async tests need no marker — `asyncio_mode = "auto"`, one fresh loop per test.
- Coverage is enforced at 100% with branches.
  Prefer a test over a pragma.
