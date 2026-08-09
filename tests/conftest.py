from collections.abc import Iterator

import pytest

import nodrill


@pytest.fixture(autouse=True)
def _clean_context() -> Iterator[None]:
    # Async tests get most of this free by running in their own task, but sync tests do not.
    with nodrill.isolate():
        yield
