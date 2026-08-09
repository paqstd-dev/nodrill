"""Argument binding in @inject: the compiled wrapper mirrors the signature.

The wrapper's parameter list is generated from the function's own, so the
interpreter binds every call shape natively; these tests pin the shapes down.
"""

from collections.abc import Callable
from typing import Annotated, Any

import pytest

from nodrill import FromCtx, from_ctx, inject, injected, provider, use


class Db:
    def __init__(self, dsn: str = "pg://") -> None:
        self.dsn = dsn


class TestLeadingPositionalArguments:
    def test_leading_positional_argument(self) -> None:
        @inject
        def handler(request: str, db: FromCtx[Db] = injected) -> tuple[str, str]:
            return request, db.dsn

        with provider(Db()):
            assert handler("r") == ("r", "pg://")

    def test_several_leading_positional_arguments(self) -> None:
        @inject
        def handler(a: int, b: int, db: FromCtx[Db] = injected) -> tuple[int, int, str]:
            return a, b, db.dsn

        with provider(Db()):
            assert handler(1, 2) == (1, 2, "pg://")

    def test_method_self_counts_as_a_leading_argument(self) -> None:
        class Service:
            @inject
            def run(self, db: FromCtx[Db] = injected) -> str:
                return db.dsn

        with provider(Db()):
            assert Service().run() == "pg://"

    def test_keyword_only_injection_after_var_positional(self) -> None:
        @inject
        def handler(*rest: int, db: FromCtx[Db] = injected) -> tuple[tuple[int, ...], str]:
            return rest, db.dsn

        with provider(Db()):
            assert handler(1, 2, 3) == ((1, 2, 3), "pg://")

    def test_mixed_positional_and_keyword_arguments(self) -> None:
        @inject
        def handler(a: int, b: int = 0, db: FromCtx[Db] = injected) -> tuple[int, int, str]:
            return a, b, db.dsn

        with provider(Db()):
            assert handler(1, b=5) == (1, 5, "pg://")


class TestInjectedParametersPassedPositionally:
    def test_injected_parameter_passed_positionally(self) -> None:
        @inject
        def handler(request: str, db: FromCtx[Db] = injected) -> str:
            return db.dsn

        with provider(Db()):
            assert handler("r", Db("explicit://")) == "explicit://"

    def test_sentinel_passed_positionally_is_still_resolved(self) -> None:
        @inject
        def handler(request: str, db: FromCtx[Db] = injected) -> str:
            return db.dsn

        with provider(Db()):
            assert handler("r", injected) == "pg://"

    def test_positional_only_injected_parameter(self) -> None:
        @inject
        def handler(db: FromCtx[Db] = injected, /) -> str:
            return db.dsn

        with provider(Db()):
            assert handler() == "pg://"

    def test_explicit_none_wins_over_the_context(self) -> None:
        @inject
        def handler(request: str, db: FromCtx[Db] | None = injected) -> Any:
            return db

        with provider(Db()):
            assert handler("r", None) is None


class TestByNameBinding:
    def test_leading_argument_reaches_an_eligible_parameter(self) -> None:
        @inject(from_="app")
        def render(user: str = "", theme: str = "dark") -> tuple[str, str]:
            return user, theme

        with provider("app", user="u", theme="light"):
            assert render() == ("u", "light")
            assert render("explicit") == ("explicit", "light")

    def test_method_skips_self(self) -> None:
        class View:
            @inject(from_="app")
            def render(self, user: str = "") -> str:
                return user

        with provider("app", user="u"):
            assert View().render() == "u"

    def test_missing_required_parameter_names_the_key(self) -> None:
        @inject(from_="app")
        def render(user: str) -> str:
            return user

        with provider("app"), pytest.raises(TypeError, match="'user'"):
            render()  # type: ignore[call-arg]

    def test_sentinel_falls_back_to_the_parameter_default(self) -> None:
        @inject(from_="app")
        def render(user: str = "fallback") -> str:
            return user

        with provider("app"):
            assert render(injected) == "fallback"


class TestAttributeMarkers:
    def test_attribute_marker_with_a_leading_argument(self) -> None:
        @inject
        def handler(
            request: str,
            db: Annotated[str, from_ctx("app", attr="database")] = injected,
        ) -> tuple[str, str]:
            return request, db

        with provider("app", database="pg://"):
            assert handler("r") == ("r", "pg://")


class TestAsyncBinding:
    async def test_leading_positional_argument(self) -> None:
        @inject
        async def handler(request: str, db: FromCtx[Db] = injected) -> tuple[str, str]:
            return request, db.dsn

        with provider(Db()):
            assert await handler("r") == ("r", "pg://")

    async def test_injected_parameter_passed_positionally(self) -> None:
        @inject
        async def handler(request: str, db: FromCtx[Db] = injected) -> str:
            return db.dsn

        with provider(Db()):
            assert await handler("r", Db("explicit://")) == "explicit://"


class TestErrorsOnBadCalls:
    def test_unknown_keyword_still_fails(self) -> None:
        @inject
        def handler(db: FromCtx[Db] = injected) -> str:
            return db.dsn

        with provider(Db()), pytest.raises(TypeError, match="nope"):
            handler(nope=1)  # type: ignore[call-arg]

    def test_too_many_positional_arguments_still_fails(self) -> None:
        @inject
        def handler(request: str, db: FromCtx[Db] = injected) -> str:
            return db.dsn

        with provider(Db()), pytest.raises(TypeError, match="positional arguments"):
            handler("a", "b", "c")  # type: ignore[call-arg, arg-type]

    def test_a_bad_call_fails_before_resolution(self) -> None:
        # The wrapper mirrors the signature, so Python rejects the call exactly
        # as it would reject the undecorated function, provider or no provider.
        @inject
        def handler(db: FromCtx[Db] = injected) -> str:
            return db.dsn

        with pytest.raises(TypeError, match="nope"):
            handler(nope=1)  # type: ignore[call-arg]


class TestSignatureShapes:
    def test_var_keyword_parameter_passes_through(self) -> None:
        @inject
        def handler(db: FromCtx[Db] = injected, **extra: int) -> tuple[str, dict[str, int]]:
            return db.dsn, extra

        with provider(Db()):
            assert handler(x=1) == ("pg://", {"x": 1})

    def test_keyword_only_injection_without_var_positional(self) -> None:
        @inject
        def handler(*, db: FromCtx[Db] = injected) -> str:
            return db.dsn

        with provider(Db()):
            assert handler() == "pg://"

    def test_positional_only_parameter_followed_by_a_positional(self) -> None:
        @inject
        def handler(db: FromCtx[Db] = injected, /, tag: str = "t") -> tuple[str, str]:
            return db.dsn, tag

        with provider(Db()):
            assert handler(tag="x") == ("pg://", "x")

    def test_required_parameter_after_a_default_free_marker(self) -> None:
        @inject
        def handler(db: FromCtx[Db], tag: str) -> tuple[str, str]:
            return db.dsn, tag

        with provider(Db()):
            assert handler(tag="x") == ("pg://", "x")  # type: ignore[call-arg]
            with pytest.raises(TypeError, match="missing 1 required positional argument: 'tag'"):
                handler()  # type: ignore[call-arg]

    def test_missing_required_parameters_are_reported_together(self) -> None:
        @inject
        def handler(db: FromCtx[Db], a: str, b: str) -> str:
            return db.dsn

        with provider(Db()), pytest.raises(TypeError, match="arguments: 'a' and 'b'"):
            handler()  # type: ignore[call-arg]

    def test_missing_required_argument_beats_the_provider_miss(self) -> None:
        # The guard runs before any resolution, so the caller's mistake is
        # reported even when no provider is active.
        @inject
        def handler(db: FromCtx[Db], tag: str) -> str:
            return tag

        with pytest.raises(TypeError, match="missing 1 required positional argument: 'tag'"):
            handler()  # type: ignore[call-arg]

    def test_sentinel_for_a_required_positional_counts_as_omitted(self) -> None:
        @inject
        def handler(db: FromCtx[Db], tag: str) -> str:
            return tag

        with provider(Db()), pytest.raises(TypeError, match="missing 1 required"):
            handler(injected, injected)

    def test_missing_arguments_read_like_the_interpreter(self) -> None:
        @inject
        def handler(db: FromCtx[Db], a: str, b: str, c: str) -> None: ...

        def twin(a: str, b: str, c: str) -> None: ...

        with provider(Db()), pytest.raises(TypeError) as via_wrapper:
            handler()  # type: ignore[call-arg]
        with pytest.raises(TypeError) as via_twin:
            twin()  # type: ignore[call-arg]
        assert str(via_wrapper.value).split("() ", 1)[1] == str(via_twin.value).split("() ", 1)[1]

    def test_parameter_sharing_the_internal_prefix(self) -> None:
        @inject
        def handler(_nd_tag: str = "t", db: FromCtx[Db] = injected) -> tuple[str, str]:
            return _nd_tag, db.dsn

        with provider(Db()):
            assert handler() == ("t", "pg://")

    def test_attribute_marker_with_a_non_identifier_attribute(self) -> None:
        @inject
        def handler(v: Annotated[str, from_ctx("app", attr="strange-name")] = injected) -> str:
            return v

        with provider("app", **{"strange-name": "x"}):  # type: ignore[call-overload]
            assert handler() == "x"

    def test_lambda_in_by_name_mode(self) -> None:
        handler: Callable[..., Any] = inject(from_="app")(lambda db=injected: db)

        with provider("app", db="pg"):
            assert handler() == "pg"


class TestCallerKwargsAreNotLeaked:
    def test_wrapper_does_not_mutate_a_reused_mapping(self) -> None:
        @inject
        def handler(a: int = 0, db: FromCtx[Db] = injected) -> tuple[int, str]:
            return a, db.dsn

        payload = {"a": 1}
        with provider(Db()):
            assert handler(**payload) == (1, "pg://")  # type: ignore[arg-type]
            assert payload == {"a": 1}
            assert handler(**payload) == (1, "pg://")  # type: ignore[arg-type]


class TestPassThroughPlans:
    def test_function_with_nothing_to_inject_is_returned_unwrapped(self) -> None:
        def plain(a: int) -> int:
            return a

        assert inject(plain) is plain

    def test_use_inside_the_body_is_unaffected(self) -> None:
        @inject
        def handler(request: str, db: FromCtx[Db] = injected) -> tuple[str, str]:
            return db.dsn, use(Db).dsn

        with provider(Db()):
            assert handler("r") == ("pg://", "pg://")
