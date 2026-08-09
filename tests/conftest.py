from collections.abc import Iterator

import pytest

import nodrill


@pytest.fixture(autouse=True)
def _clean_context() -> Iterator[None]:
    # Fresh provider registry and ambient namespace per test; default
    # registrations made inside a test are rolled back.  Async tests get most
    # of this for free (each test runs in its own task), sync tests do not.
    with nodrill.isolate():
        yield
