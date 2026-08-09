import linecache
from collections.abc import Callable
from dataclasses import dataclass
from types import FunctionType
from typing import Annotated, Any

import pytest

from nodrill import FromCtx, NoProviderError, from_ctx, inject, injected, provider


@dataclass
class Database:
    dsn: str = "sqlite://"


@dataclass
class AppCtx:
    db: str = "default-db"
    timeout: float = 1.0


@inject
def subscript_marker(cfg: FromCtx[AppCtx] = injected) -> str:
    return cfg.db


@inject
def annotated_marker(cfg: Annotated[AppCtx, FromCtx()] = injected) -> str:
    return cfg.db


@inject
def bare_class_marker(cfg: Annotated[AppCtx, FromCtx] = injected) -> str:
    return cfg.db


@inject
def keyed_marker(anything: Annotated[Any, FromCtx(Database)] = injected) -> str:
    dsn: str = anything.dsn
    return dsn


@inject
def keyed_marker_with_attr(dsn: Annotated[str, FromCtx(Database, attr="dsn")] = injected) -> str:
    return dsn


@inject
def combine(db: Annotated[str, FromCtx("app")] = injected, n: int = 0) -> tuple[str, int]:
    return db, n


@inject
def echo_db(db: Annotated[Any, FromCtx("app")] = injected) -> Any:
    return db


class TestMarkerInjection:
    def test_all_params_injected_string_marker(self) -> None:
        @inject
        def handler(
            db: Annotated[str, FromCtx("app")] = injected,
            timeout: Annotated[float, FromCtx("app")] = injected,
        ) -> tuple[str, float]:
            return db, timeout

        with provider("app", db="pg", timeout=2.5):
            assert handler() == ("pg", 2.5)

    def test_string_marker_with_attr_override(self) -> None:
        @inject
        def handler(engine: Annotated[str, FromCtx("app", attr="db")] = injected) -> str:
            return engine

        with provider("app", db="the-engine"):
            assert handler() == "the-engine"

    @pytest.mark.parametrize(
        "handler",
        [subscript_marker, annotated_marker, bare_class_marker],
        ids=["subscript", "annotated", "bare-class-metadata"],
    )
    def test_class_marker_spellings(self, handler: Callable[[], str]) -> None:
        with provider(AppCtx(db="pg")):
            assert handler() == "pg"

    @pytest.mark.parametrize(
        "handler",
        [keyed_marker, keyed_marker_with_attr],
        ids=["whole-instance", "single-attr"],
    )
    def test_explicit_class_key(self, handler: Callable[[], str]) -> None:
        with provider(Database(dsn="pg://x")):
            assert handler() == "pg://x"

    def test_from_ctx_function_spelling(self) -> None:
        """from_ctx(...) is the pyright-clean equivalent of FromCtx(...)."""

        @inject
        def handler(
            db: Annotated[str, from_ctx("app")] = injected,
            cfg: Annotated[AppCtx, from_ctx()] = injected,
            dsn: Annotated[str, from_ctx(Database, attr="dsn")] = injected,
        ) -> tuple[str, str, str]:
            return db, cfg.db, dsn

        with provider("app", db="named"), provider(AppCtx(db="whole")), provider(Database("d")):
            assert handler() == ("named", "whole", "d")


class TestOptionalMarker:
    """A union around a marker must not hide it."""

    def test_explicit_optional_still_injects(self) -> None:
        @inject
        def handler(cfg: FromCtx[AppCtx] | None = None) -> str | None:
            return None if cfg is None else cfg.db

        with provider(AppCtx(db="pg")):
            assert handler() == "pg"

    def test_none_default_still_injects(self) -> None:
        # Python 3.10 rewrites this annotation to Optional[...] on its own.
        @inject
        def handler(cfg: FromCtx[AppCtx] = None) -> str:  # type: ignore[assignment]
            return cfg.db

        with provider(AppCtx(db="pg")):
            assert handler() == "pg"

    def test_explicit_none_still_wins(self) -> None:
        @inject
        def handler(cfg: FromCtx[AppCtx] | None = None) -> str | None:
            return None if cfg is None else cfg.db

        with provider(AppCtx(db="pg")):
            assert handler(None) is None

    def test_union_without_a_marker_is_left_alone(self) -> None:
        def plain(qty: int | None = None) -> int | None:
            return qty

        assert inject(plain) is plain


class TestExplicitArgumentsWin:
    @pytest.mark.parametrize(
        ("args", "kwargs", "expected"),
        [
            ((), {"db": "explicit"}, ("explicit", 0)),
            (("explicit-pos", 5), {}, ("explicit-pos", 5)),
            ((injected, 3), {}, ("ctx-db", 3)),
        ],
        ids=["keyword", "positional", "sentinel-passed-back"],
    )
    def test_precedence_over_context(
        self, args: tuple[Any, ...], kwargs: dict[str, Any], expected: tuple[str, int]
    ) -> None:
        with provider("app", db="ctx-db"):
            assert combine(*args, **kwargs) == expected

    @pytest.mark.parametrize(
        "call",
        [lambda: echo_db(None), lambda: echo_db(db=None)],
        ids=["positional", "keyword"],
    )
    def test_explicit_none_counts_as_provided(self, call: Callable[[], Any]) -> None:
        with provider("app", db="ctx-db"):
            assert call() is None

    def test_all_explicit_outside_any_provider(self) -> None:
        assert combine("no-ctx", 1) == ("no-ctx", 1)

    def test_missing_provider_still_raises_for_missing_args(self) -> None:
        with pytest.raises(NoProviderError):
            combine()


class TestCallTimeResolution:
    def test_decorate_at_import_provide_later(self) -> None:
        @inject
        def handler(cfg: FromCtx[AppCtx] = injected) -> str:
            return cfg.db

        # No provider yet, so decoration must not have raised.
        with provider(AppCtx(db="late")):
            assert handler() == "late"

    def test_nearest_provider_at_each_call(self) -> None:
        @inject
        def handler(cfg: FromCtx[AppCtx] = injected) -> str:
            return cfg.db

        with provider(AppCtx(db="outer")):
            assert handler() == "outer"
            with provider(AppCtx(db="inner")):
                assert handler() == "inner"
            assert handler() == "outer"


class TestFromNameMode:
    def test_fills_matching_missing_params(self) -> None:
        @inject(from_="app")
        def handler(db: str, timeout: float, other: str = "kept") -> tuple[str, float, str]:
            return db, timeout, other

        with provider("app", db="pg", timeout=9.0):
            assert handler() == ("pg", 9.0, "kept")  # type: ignore[call-arg]

    def test_non_matching_params_keep_defaults(self) -> None:
        @inject(from_="app")
        def handler(db: str, missing: str = "default") -> tuple[str, str]:
            return db, missing

        with provider("app", db="pg", unrelated=1):
            assert handler() == ("pg", "default")  # type: ignore[call-arg]

    def test_explicit_beats_from_name(self) -> None:
        @inject(from_="app")
        def handler(db: str) -> str:
            return db

        with provider("app", db="ctx"):
            assert handler("explicit") == "explicit"
            assert handler(db="kw") == "kw"

    def test_from_class_key(self) -> None:
        @inject(from_=AppCtx)
        def handler(db: str, timeout: float) -> tuple[str, float]:
            return db, timeout

        with provider(AppCtx(db="from-class", timeout=3.0)):
            assert handler() == ("from-class", 3.0)  # type: ignore[call-arg]

    def test_required_param_absent_from_context_raises_helpful_error(self) -> None:
        @inject(from_="app")
        def handler(absent_everywhere: str) -> str:
            return absent_everywhere

        with provider("app", db="pg"), pytest.raises(TypeError) as exc_info:
            handler()  # type: ignore[call-arg]
        message = str(exc_info.value)
        assert "'absent_everywhere'" in message
        assert "not passed explicitly" in message
        assert "use('app')" in message

    def test_context_attribute_overrides_parameter_default(self) -> None:
        @inject(from_="app")
        def handler(lang: str = "en") -> str:
            return lang

        with provider("app", lang="fr"):
            assert handler() == "fr"  # the documented from_ contract
            assert handler(lang="de") == "de"

    def test_sentinel_argument_is_treated_as_omitted(self) -> None:
        @inject(from_="app")
        def handler(db: str, lang: str = "en") -> tuple[str, str]:
            return db, lang

        with provider("app", db="pg"):
            # `lang` is nowhere in the context, so the parameter default applies.
            assert handler(injected, injected) == ("pg", "en")

    def test_sentinel_for_a_required_param_raises_like_an_omitted_one(self) -> None:
        @inject(from_="app")
        def handler(absent_everywhere: str) -> str:
            return absent_everywhere

        with provider("app", db="pg"), pytest.raises(TypeError, match="not passed explicitly"):
            handler(injected)

    def test_combines_with_markers(self) -> None:
        @inject(from_="app")
        def handler(db: str, dsn: Annotated[str, FromCtx(Database, attr="dsn")] = injected) -> str:
            return f"{db}+{dsn}"

        with provider("app", db="pg"), provider(Database(dsn="d1")):
            assert handler() == "pg+d1"  # type: ignore[call-arg]


class TestDecorationGuards:
    def test_no_marker_no_from_returns_original(self) -> None:
        def plain(a: int, b: str = "x") -> str:
            return f"{a}{b}"

        assert inject(plain) is plain

    def test_inject_with_empty_parens(self) -> None:
        @inject()
        def plain(a: int) -> int:
            return a

        assert plain(1) == 1

    def test_variadic_marker_rejected(self) -> None:
        with pytest.raises(TypeError, match=r"variadic parameter \*args"):

            @inject
            def bad(*args: Annotated[str, FromCtx("app")]) -> None: ...

    def test_bare_marker_on_non_class_annotation_rejected(self) -> None:
        with pytest.raises(TypeError, match="not a plain class"):

            @inject
            def bad(items: Annotated[list[str], FromCtx()] = injected) -> None: ...

    def test_non_callable_rejected(self) -> None:
        with pytest.raises(TypeError, match="expects a callable"):
            inject(42)  # type: ignore[call-overload]

    def test_class_rejected(self) -> None:
        """A class is callable, but replacing it with a function breaks isinstance."""
        with pytest.raises(TypeError, match="does not support classes"):
            inject(Database)

    def test_bad_from_type_rejected(self) -> None:
        with pytest.raises(TypeError, match="from_ must be"):
            inject(from_=42)  # type: ignore[call-overload]

    def test_bad_fromctx_key_rejected(self) -> None:
        with pytest.raises(TypeError, match="FromCtx key must be"):
            from_ctx(42)  # type: ignore[arg-type]

    def test_fromctx_repr(self) -> None:
        assert repr(from_ctx("app", attr="db")) == "FromCtx('app', attr='db')"
        assert repr(from_ctx()) == "FromCtx()"

    def test_wrapper_preserves_metadata(self) -> None:
        @inject
        def documented(cfg: FromCtx[AppCtx] = injected) -> None:
            """Docstring survives."""

        assert documented.__name__ == "documented"
        assert documented.__doc__ == "Docstring survives."

    def test_injected_repr(self) -> None:
        assert repr(injected) == "<nodrill.injected>"

    def test_generated_source_is_registered_for_tracebacks(self) -> None:
        @inject
        def handler(cfg: FromCtx[AppCtx] = injected) -> str:
            return cfg.db

        assert isinstance(handler, FunctionType)
        assert linecache.getline(handler.__code__.co_filename, 1).startswith("def handler(")

    def test_same_label_wrappers_keep_distinct_sources(self) -> None:
        def make() -> FunctionType:
            @inject
            def handler(cfg: FromCtx[AppCtx] = injected) -> str:
                return cfg.db

            assert isinstance(handler, FunctionType)
            return handler

        first, second = make(), make()
        assert first.__code__.co_filename != second.__code__.co_filename
        assert linecache.getline(second.__code__.co_filename, 1).startswith("def handler(")


class TestSentinelLeak:
    """A leaked sentinel fails loudly instead of surfacing as a puzzle later."""

    def test_attribute_access_names_the_cause(self) -> None:
        def forgot_inject(cfg: FromCtx[AppCtx] = injected) -> str:
            url: str = cfg.db
            return url

        with pytest.raises(AttributeError, match="Check that @inject is applied"):
            forgot_inject()

    def test_truthiness_raises(self) -> None:
        with pytest.raises(TypeError, match="reached the function body"):
            bool(injected)

    def test_call_raises(self) -> None:
        with pytest.raises(TypeError, match="reached the function body"):
            injected()

    def test_annotated_without_marker_is_not_injected(self) -> None:
        def plain(qty: Annotated[int, "unit: pieces"] = 5) -> int:
            return qty

        assert inject(plain) is plain  # foreign Annotated metadata is ignored
        assert plain() == 5

    def test_positional_only_marker_parameter(self) -> None:
        @inject
        def handler(cfg: FromCtx[AppCtx] = injected, /) -> str:
            return cfg.db

        with provider(AppCtx(db="pos")):
            assert handler() == "pos"
        assert handler(AppCtx(db="explicit")) == "explicit"

    def test_lazy_plan_that_turns_out_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A forward-ref annotation forces lazy planning, and the plan comes out empty.
        @inject
        def late(x: "DefinedAfterDecoration" = None) -> Any:  # type: ignore[name-defined]  # noqa: F821
            return x

        class Probe:
            pass

        monkeypatch.setitem(globals(), "DefinedAfterDecoration", Probe)
        assert late() is None
        assert late(Probe()) is not None
