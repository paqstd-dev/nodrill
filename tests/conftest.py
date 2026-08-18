import threading
from collections.abc import Callable, Iterator
from typing import Any

import pytest

import nodrill

# Runs a callable on a fresh thread and hands back its result or its exception.
ThreadRunner = Callable[[Callable[[], Any]], Any]


@pytest.fixture
def in_thread() -> ThreadRunner:
    """Run a callable on its own thread, ferrying what it returned or raised back here."""

    def run(target: Callable[[], Any]) -> Any:
        box: list[Any] = []

        def runner() -> None:
            try:
                box.append(target())
            except Exception as exc:
                box.append(exc)

        thread = threading.Thread(target=runner)
        thread.start()
        thread.join()
        [result] = box
        return result

    return run


@pytest.fixture(autouse=True)
def _clean_context() -> Iterator[None]:
    # Async tests get most of this free by running in their own task, but sync tests do not.
    with nodrill.isolate():
        yield
