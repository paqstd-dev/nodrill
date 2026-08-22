"""The application itself, a process-wide layer with three entry points under it.

Shaped after a real service rather than after what flatters the tool.  Config
is opened once in main and every boundary nests under it, which is the shape
that collapses to one entry point unless the boundaries are declared.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from nodrill import FromCtx, inject, injected, provider, set_default, use


class Settings:
    """What the process is configured with, opened once above everything."""

    def __init__(self, dsn: str = "sqlite://") -> None:
        self.dsn = dsn


class User:
    """Who the request is for."""

    def __init__(self, name: str = "anonymous") -> None:
        self.name = name


class Origin:
    """Where a write came from, which an audit table records."""

    def __init__(self, label: str = "system") -> None:
        self.label = label


set_default(Origin, Origin)


def record_write() -> str:
    """Write a row, naming the user and where the write came from."""
    return f"{use(User).name} from {use(Origin).label}"


@inject
def open_connection(settings: FromCtx[Settings] = injected) -> str:
    """Read through a compiled wrapper, which is a different code path from use()."""
    return settings.dsn


@contextmanager
def running() -> Iterator[None]:
    """Open the process-wide layer the way a main function does."""
    with provider(Settings()):
        yield


def serve_http(name: str) -> str:
    """The web entry point, which opens both keys the handler reads."""
    with provider("http request", route="/writes"), provider(User(name)):
        with provider(Origin("http")):
            return f"{record_write()} {open_connection()} {use('http request').route}"


def run_job(name: str) -> str:
    """The queue entry point, which opens the user and leaves the origin to fall back."""
    with provider("celery worker"), provider(User(name)):
        return record_write()


def run_report() -> str:
    """The reporting entry point, which opens a boundary and forgets the user."""
    with provider("nightly report"):
        return record_write()
