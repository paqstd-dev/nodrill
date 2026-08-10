import asyncio
import contextlib
import warnings
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any

import pytest

import nodrill
from nodrill import _report, lazy, provider, use
from nodrill._report import _drop_note, _scope_note

# True from 3.11, which is where PEP 678 notes exist at all.
NOTES = hasattr(BaseException, "add_note")


@dataclass
class RequestScope:
    user_id: int
    tenant: str


@dataclass
class Config:
    url: str


class Boom:
    def __repr__(self) -> str:
        raise ValueError("hunter2")


class NotAString:
    def __repr__(self) -> Any:
        # The point of the class, and repr() itself is what turns it into a TypeError.
        return 7


class Wide:
    def __init__(self, width: int) -> None:
        self.width = width

    def __repr__(self) -> str:
        return "x" * self.width


class TwoLines:
    def __repr__(self) -> str:
        return "a\nb"


class Chatty:
    def __repr__(self) -> str:
        # Never repr'd inside its own block, where this would be the value it is building.
        return f"Chatty({use(Chatty)})"


def notes(exc: BaseException) -> list[str]:
    """Return the notes attached to an exception, on any supported version."""
    return list(getattr(exc, "__notes__", ()))


def expected(*scopes: str) -> list[str]:
    """Return the notes a supported interpreter attaches, and none on 3.10."""
    return [f"nodrill scope: {scope}" for scope in scopes] if NOTES else []


@contextmanager
def annotating() -> Iterator[None]:
    """Turn the process-wide switch on for the block, through the public API only."""
    with warnings.catch_warnings():
        # The 3.10 no-op warning is the subject of its own tests rather than of every opt-in.
        warnings.simplefilter("ignore", RuntimeWarning)
        nodrill.annotate_exceptions()
    try:
        yield
    finally:
        nodrill.annotate_exceptions(enabled=False)


def test_a_class_keyed_scope_renders_as_the_value_repr() -> None:
    note = _scope_note(RequestScope, RequestScope(user_id=42, tenant="acme"))
    assert note == "nodrill scope: RequestScope(user_id=42, tenant='acme')"


def test_a_named_scope_renders_as_the_labelled_namespace() -> None:
    with provider("audit", request_id="01J", reason="bulk import") as ctx:
        assert _scope_note("audit", ctx) == (
            "nodrill scope: Namespace('audit', reason='bulk import', request_id='01J')"
        )


def test_a_long_repr_is_truncated_at_the_documented_width() -> None:
    rendered = _scope_note(Wide, Wide(500)).removeprefix("nodrill scope: ")
    assert len(rendered) == 200
    assert rendered == "x" * 197 + "..."


def test_a_repr_of_exactly_the_width_is_left_alone() -> None:
    rendered = _scope_note(Wide, Wide(200)).removeprefix("nodrill scope: ")
    assert rendered == "x" * 200


def test_a_raising_repr_names_the_key_and_not_the_message() -> None:
    note = _scope_note(Boom, Boom())
    assert note == "nodrill scope: <unprintable Boom, repr raised ValueError>"
    assert "hunter2" not in note


def test_a_raising_repr_under_a_string_key_names_the_key() -> None:
    assert _scope_note("audit", Boom()) == (
        "nodrill scope: <unprintable 'audit', repr raised ValueError>"
    )


def test_a_multiline_repr_renders_as_one_line() -> None:
    assert _scope_note(TwoLines, TwoLines()) == "nodrill scope: a b"


def test_a_repr_that_is_not_a_string_is_reported_as_a_type_error() -> None:
    assert _scope_note(NotAString, NotAString()) == (
        "nodrill scope: <unprintable NotAString, repr raised TypeError>"
    )


def test_nested_blocks_stack_their_notes_innermost_first() -> None:
    err = ValueError("boom")
    with pytest.raises(ValueError, match="boom"):
        with provider("outer", tag="o", annotate=True):
            with provider(RequestScope(user_id=42, tenant="acme"), annotate=True):
                with provider("inner", tag="i", annotate=True):
                    raise err
    assert notes(err) == expected(
        "Namespace('inner', tag='i')",
        "RequestScope(user_id=42, tenant='acme')",
        "Namespace('outer', tag='o')",
    )


def test_a_block_that_completes_attaches_nothing() -> None:
    err = ValueError("caught inside")
    with provider(RequestScope(user_id=42, tenant="acme"), annotate=True):
        with contextlib.suppress(ValueError):
            raise err
    assert notes(err) == []


def test_a_caught_and_re_raised_exception_collects_the_outer_scope() -> None:
    err = ValueError("boom")
    with pytest.raises(ValueError, match="boom"), provider("outer", tag="o", annotate=True):
        with contextlib.suppress(ValueError), provider("inner", tag="i", annotate=True):
            raise err
        raise err
    assert notes(err) == expected("Namespace('inner', tag='i')", "Namespace('outer', tag='o')")


def test_annotation_leaves_the_exception_itself_alone() -> None:
    raised = ValueError("boom")
    cause = RuntimeError("why")
    earlier = KeyError("earlier")
    raised.__cause__ = cause
    raised.__context__ = earlier
    with (
        pytest.raises(ValueError, match="boom") as caught,
        provider(RequestScope(1, "acme"), annotate=True),
    ):
        raise raised
    err = caught.value
    assert err is raised
    assert err.args == ("boom",)
    assert err.__cause__ is cause
    assert err.__context__ is earlier
    deepest = err.__traceback__
    assert deepest is not None
    while deepest.tb_next is not None:
        deepest = deepest.tb_next
    # The raise itself, so nothing re-raised the exception out of __exit__.
    assert deepest.tb_frame.f_code.co_filename == __file__


def test_a_base_exception_passes_through_untouched() -> None:
    err = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt), provider(RequestScope(1, "acme"), annotate=True):
        raise err
    assert notes(err) == []


async def test_a_cancelled_task_carries_no_notes() -> None:
    err = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        async with provider(RequestScope(user_id=42, tenant="acme"), annotate=True):
            raise err
    assert notes(err) == []


async def test_async_with_annotates_exactly_as_with_does() -> None:
    err = ValueError("boom")
    with pytest.raises(ValueError, match="boom"):
        async with provider(RequestScope(user_id=42, tenant="acme"), annotate=True):
            raise err
    assert notes(err) == expected("RequestScope(user_id=42, tenant='acme')")


def test_an_exit_stack_notes_the_same_scopes_in_the_same_order() -> None:
    err = ValueError("boom")
    with pytest.raises(ValueError, match="boom"), ExitStack() as stack:
        stack.enter_context(provider("outer", tag="o", annotate=True))
        stack.enter_context(provider("inner", tag="i", annotate=True))
        raise err
    assert notes(err) == expected("Namespace('inner', tag='i')", "Namespace('outer', tag='o')")


def test_the_process_switch_annotates_a_provider_that_asked_for_nothing() -> None:
    err = ValueError("boom")
    with annotating(), pytest.raises(ValueError, match="boom"), provider("app", tag="a"):
        raise err
    assert notes(err) == expected("Namespace('app', tag='a')")


def test_the_switch_off_is_the_default_and_attaches_nothing() -> None:
    err = ValueError("boom")
    with pytest.raises(ValueError, match="boom"), provider("app", tag="a"):
        raise err
    assert notes(err) == []


def test_a_block_can_stay_out_of_the_traceback_while_the_switch_is_on() -> None:
    err = ValueError("boom")
    with annotating(), pytest.raises(ValueError, match="boom"), provider("app", tag="a"):
        with provider("secrets", dsn="postgres://user:hunter2@db", annotate=False):
            raise err
    assert notes(err) == expected("Namespace('app', tag='a')")
    assert "hunter2" not in "".join(notes(err))


def test_a_block_can_annotate_while_the_switch_is_off() -> None:
    err = ValueError("boom")
    with pytest.raises(ValueError, match="boom"), provider("app", tag="a", annotate=True):
        raise err
    assert notes(err) == expected("Namespace('app', tag='a')")


def test_annotate_is_the_fourth_name_that_cannot_be_prefilled() -> None:
    with provider("app", annotate=True) as ctx:
        assert not hasattr(ctx, "annotate")


def test_annotate_exceptions_is_exported() -> None:
    assert "annotate_exceptions" in nodrill.__all__
    assert nodrill.annotate_exceptions is _report.annotate_exceptions


def test_a_lazy_scope_is_named_without_running_the_factory() -> None:
    def factory() -> Config:
        pytest.fail("rendering a note must not build a lazy value")

    err = ValueError("boom")
    with pytest.raises(ValueError, match="boom"), provider(lazy(Config, factory), annotate=True):
        raise err
    assert notes(err) == expected("<lazy Config, unresolved>")


def test_a_resolved_lazy_scope_names_the_built_value() -> None:
    err = ValueError("boom")
    with (
        pytest.raises(ValueError, match="boom"),
        provider(lazy(Config, lambda: Config(url="x")), annotate=True),
    ):
        assert use(Config).url == "x"
        raise err
    assert notes(err) == expected("<lazy Config, Config(url='x')>")


def test_a_frozen_scope_names_the_value_and_not_the_proxy() -> None:
    err = ValueError("boom")
    with (
        pytest.raises(ValueError, match="boom"),
        provider(Config(url="x"), frozen=True, annotate=True),
    ):
        raise err
    assert notes(err) == expected("Config(url='x')")


def test_an_extending_layer_names_the_merged_namespace() -> None:
    err = ValueError("boom")
    with pytest.raises(ValueError, match="boom"), provider("audit", reason="bulk import"):
        with provider("audit", extend=True, actor=1, annotate=True):
            raise err
    assert notes(err) == expected("Namespace('audit', actor=1, reason='bulk import')")


def test_one_note_lands_per_block_the_exception_left() -> None:
    err = ValueError("boom")
    scope = provider(RequestScope(user_id=1, tenant="acme"), annotate=True)
    for _ in range(2):
        with contextlib.suppress(ValueError), scope:
            raise err
    named = "RequestScope(user_id=1, tenant='acme')"
    assert notes(err) == expected(named, named)


def test_a_repr_that_reads_its_own_key_sees_the_enclosing_scope() -> None:
    err = ValueError("boom")
    with pytest.raises(ValueError, match="boom"), provider(Chatty(), annotate=True):
        raise err
    assert notes(err) == expected("<unprintable Chatty, repr raised NoProviderError>")


def test_without_note_support_the_switch_warns_and_nothing_is_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_report, "_add_note", _drop_note)
    err = ValueError("boom")
    with pytest.warns(RuntimeWarning, match="has no effect on Python 3.10"):
        nodrill.annotate_exceptions()
    try:
        with pytest.raises(ValueError, match="boom"), provider("app", tag="a"):
            raise err
    finally:
        nodrill.annotate_exceptions(enabled=False)
    assert not hasattr(err, "__notes__")


def test_turning_the_switch_off_never_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_report, "_add_note", _drop_note)
    # filterwarnings = ["error"] is the assertion, since a warning here would raise.
    nodrill.annotate_exceptions(enabled=False)


def test_with_note_support_the_switch_is_quiet_and_the_note_is_handed_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[str] = []

    def record(_exc: BaseException, note: str) -> None:
        recorded.append(note)

    monkeypatch.setattr(_report, "_add_note", record)
    nodrill.annotate_exceptions()
    try:
        with pytest.raises(ValueError, match="boom"), provider("app", tag="a"):
            raise ValueError("boom")
    finally:
        nodrill.annotate_exceptions(enabled=False)
    assert recorded == ["nodrill scope: Namespace('app', tag='a')"]
