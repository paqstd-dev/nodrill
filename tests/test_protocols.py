"""Dunder behaviour on Namespace, the ambient context, provider and injected."""

from typing import Any

import pytest

from nodrill import Namespace, active, context, injected, provider, use


class TestNamespaceEquality:
    def test_equal_by_attributes(self) -> None:
        assert Namespace(a=1, b=2) == Namespace(b=2, a=1)

    def test_unequal_attributes(self) -> None:
        assert Namespace(a=1) != Namespace(a=2)

    def test_provider_name_is_not_part_of_equality(self) -> None:
        with provider("app", a=1):
            assert use("app") == Namespace(a=1)

    def test_other_types_are_not_equal(self) -> None:
        assert Namespace(a=1) != {"a": 1}

    def test_unhashable_like_simplenamespace(self) -> None:
        with pytest.raises(TypeError, match="unhashable"):
            hash(Namespace(a=1))

    def test_label_stays_out_of_the_attribute_bag(self) -> None:
        with provider("app", a=1) as ctx:
            assert vars(ctx) == {"a": 1}
            assert repr(ctx) == "Namespace('app', a=1)"

    def test_unnamed_namespace_reports_no_label(self) -> None:
        assert repr(Namespace(a=1)) == "Namespace(a=1)"

    def test_missing_attribute_names_the_provider(self) -> None:
        with provider("app", a=1):
            with pytest.raises(AttributeError, match="namespace 'app' has no attribute 'b'"):
                use("app").b  # noqa: B018


class TestAmbientIteration:
    def test_len_and_iter(self) -> None:
        assert len(context) == 0
        context.b = 2
        context.a = 1
        assert len(context) == 2
        assert list(context) == ["a", "b"]

    def test_iteration_yields_names_matching_contains(self) -> None:
        context.trace = "t"
        assert all(name in context for name in context)


class TestAsyncProvider:
    async def test_async_with(self) -> None:
        async with provider("app", db="x") as ctx:
            assert use("app") is ctx
        assert "app" not in active()

    async def test_async_with_restores_on_error(self) -> None:
        with pytest.raises(ValueError, match="boom"):
            async with provider("app"):
                raise ValueError("boom")
        assert not active()

    async def test_async_with_rejects_a_live_provider(self) -> None:
        prov = provider("app")
        async with prov:
            with pytest.raises(RuntimeError, match="already active"):
                async with prov:
                    pass


class TestInjectedSentinelLeaks:
    @pytest.mark.parametrize(
        "operation",
        [
            list,
            len,
            lambda s: "x" in s,
            lambda s: s["x"],
            int,
            float,
            lambda s: [0][s],
            lambda s: s < 1,
            lambda s: s <= 1,
            lambda s: s > 1,
            lambda s: s >= 1,
            lambda s: s + 1,
            lambda s: 1 + s,
            lambda s: s - 1,
            lambda s: 1 - s,
            lambda s: s * 1,
            lambda s: 1 * s,
            next,
        ],
        ids=[
            "iter",
            "len",
            "contains",
            "getitem",
            "int",
            "float",
            "index",
            "lt",
            "le",
            "gt",
            "ge",
            "add",
            "radd",
            "sub",
            "rsub",
            "mul",
            "rmul",
            "next",
        ],
    )
    def test_protocols_raise_with_the_leak_message(self, operation: Any) -> None:
        with pytest.raises(TypeError, match="reached the function body"):
            operation(injected)

    def test_context_manager_protocols_raise(self) -> None:
        with pytest.raises(TypeError, match="reached the function body"):
            with injected:
                pass

    async def test_async_protocols_raise(self) -> None:
        with pytest.raises(TypeError, match="reached the function body"):
            async with injected:
                pass
        with pytest.raises(TypeError, match="reached the function body"):
            await injected
        with pytest.raises(TypeError, match="reached the function body"):
            [x async for x in injected]

    def test_identity_comparison_still_works(self) -> None:
        # inject() checks with `is`, so == is deliberately left at the default.
        same = injected
        assert same is injected
        assert (injected == 1) is False
