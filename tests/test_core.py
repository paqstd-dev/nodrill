from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from nodrill import Namespace, NoProviderError, provider, use


@dataclass
class Config:
    debug: bool = False
    url: str = "sqlite://"


@pytest.fixture
def app() -> Iterator[Namespace]:
    # The shape real suites use, a provider held open for the whole test.
    with provider("app", db="x") as ctx:
        yield ctx


class TestBasics:
    def test_string_key_provider_and_use(self) -> None:
        with provider("app") as ctx:
            ctx.db = "postgres://x"
            ctx.user_id = 42

            def deep_callee() -> tuple[str, int]:
                got = use("app")
                return got.db, got.user_id

            assert deep_callee() == ("postgres://x", 42)

    def test_string_provider_prefill_kwargs(self) -> None:
        with provider("app", db="pg", user_id=7):
            assert use("app").db == "pg"
            assert use("app").user_id == 7

    def test_class_key_provider_and_use(self) -> None:
        cfg = Config(debug=True)
        with provider(cfg):
            assert use(Config) is cfg
            assert use(Config).debug is True

    def test_provider_name_keyword_spelling(self) -> None:
        with provider(name="app") as ctx:
            ctx.x = 1
            assert use("app").x == 1

    def test_use_returns_namespace_instance_for_string_keys(self) -> None:
        with provider("app"):
            assert isinstance(use("app"), Namespace)


class TestShadowing:
    def test_nested_same_key_shadows_and_restores(self) -> None:
        with provider("app", v="outer"):
            assert use("app").v == "outer"
            with provider("app", v="inner"):
                assert use("app").v == "inner"
            assert use("app").v == "outer"

    def test_nested_same_class_key_shadows_and_restores(self) -> None:
        outer, inner = Config(url="outer"), Config(url="inner")
        with provider(outer):
            with provider(inner):
                assert use(Config) is inner
            assert use(Config) is outer

    def test_restoration_after_exception_inside_block(self) -> None:
        with provider("app", v="outer"):
            with pytest.raises(RuntimeError, match="boom"):
                with provider("app", v="inner"):
                    assert use("app").v == "inner"
                    raise RuntimeError("boom")
            assert use("app").v == "outer"

    def test_exception_restores_to_no_provider(self) -> None:
        with pytest.raises(ValueError, match="bad"), provider("app"):
            raise ValueError("bad")
        with pytest.raises(NoProviderError):
            use("app")

    def test_nested_different_keys_coexist(self) -> None:
        with provider("app", region="eu"), provider(Config(debug=True)):
            with provider("request", rid="r-1"):
                assert use("app").region == "eu"
                assert use("request").rid == "r-1"
                assert use(Config).debug is True


class TestMutability:
    def test_callee_mutation_visible_to_caller(self, app: Namespace) -> None:
        def callee() -> None:
            use("app").result = "computed below"

        callee()
        assert app.result == "computed below"

    def test_class_instance_mutation_visible_to_caller(self) -> None:
        def callee() -> None:
            use(Config).url = "mutated"

        cfg = Config()
        with provider(cfg):
            callee()
        assert cfg.url == "mutated"


class TestProviderGuards:
    @pytest.mark.parametrize(
        ("bad_call", "match"),
        [
            (lambda: provider(Config), r"provider\(Config\(\.\.\.\)\)"),
            (lambda: provider(Config(), extra=1), "string-named"),  # type: ignore[call-overload]
            (lambda: provider("a", "b"), "single target"),  # type: ignore[call-overload]
            (provider, "needs a target"),
            (lambda: provider(name=Config()), "Pass instances"),  # type: ignore[call-overload]
        ],
        ids=["class-arg", "values-for-instance", "two-positionals", "no-target", "name-kw-not-str"],
    )
    def test_invalid_arguments(self, bad_call: Callable[[], Any], match: str) -> None:
        with pytest.raises(TypeError, match=match):
            bad_call()

    def test_reentering_active_provider_raises(self) -> None:
        p = provider("app")
        with p, pytest.raises(RuntimeError, match="already active"):
            p.__enter__()

    def test_provider_sequential_reuse_is_allowed(self) -> None:
        p = provider(Config(debug=True))
        with p:
            assert use(Config).debug
        with p:
            assert use(Config).debug

    def test_exit_without_enter_is_noop(self) -> None:
        provider("app").__exit__(None, None, None)

    def test_name_keyword_is_a_plain_value_when_target_is_positional(self) -> None:
        with provider("doc", name="report.pdf"):
            assert use("doc").name == "report.pdf"


class TestNamespace:
    @pytest.mark.parametrize(
        "match",
        [r"namespace 'app' has no attribute 'oops'", r"\(available: db\)"],
        ids=["names-the-provider", "lists-available"],
    )
    @pytest.mark.usefixtures("app")
    def test_missing_attribute_message(self, match: str) -> None:
        with pytest.raises(AttributeError, match=match):
            _ = use("app").oops

    def test_bare_namespace_error_message(self) -> None:
        with pytest.raises(AttributeError, match="context namespace has no attribute 'x'"):
            _ = Namespace().x

    def test_repr(self) -> None:
        assert repr(Namespace(b=2, a=1)) == "Namespace(a=1, b=2)"

    def test_repr_of_provider_namespace_includes_name(self) -> None:
        with provider("app", a=1) as ctx:
            assert repr(ctx) == "Namespace('app', a=1)"

    def test_repr_of_an_empty_provider_namespace_is_the_name_alone(self) -> None:
        with provider("app") as ctx:
            assert repr(ctx) == "Namespace('app')"
