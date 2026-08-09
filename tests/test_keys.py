"""provider(key=...), active(), and the key kinds use() accepts."""

from typing import Any, Protocol, runtime_checkable

import pytest

from nodrill import NoProviderError, active, provider, use


class Repository(Protocol):
    def get(self, key: str) -> str: ...


@runtime_checkable
class Closable(Protocol):
    def close(self) -> None: ...


class PostgresRepository:
    def get(self, key: str) -> str:
        return f"pg:{key}"


class Base:
    pass


class Sub(Base):
    pass


class TestExplicitKey:
    def test_instance_registers_under_the_given_class(self) -> None:
        with provider(Sub(), key=Base):
            assert isinstance(use(Base), Sub)
        with pytest.raises(NoProviderError):
            use(Base)

    def test_concrete_type_is_no_longer_a_key(self) -> None:
        with provider(Sub(), key=Base), pytest.raises(NoProviderError):
            use(Sub)

    def test_protocol_as_key(self) -> None:
        # The point of key=: consumers depend on the protocol, not the class.
        with provider(PostgresRepository(), key=Repository):
            repo: Repository = use(Repository)  # type: ignore[type-abstract]
            assert repo.get("a") == "pg:a"

    def test_string_key_for_an_instance(self) -> None:
        with provider(PostgresRepository(), key="repo"):
            assert use("repo").get("a") == "pg:a"

    def test_frozen_applies_to_the_keyed_registration(self) -> None:
        with provider(Sub(), key=Base, frozen=True):
            assert isinstance(use(Base), Sub)

    def test_key_rejected_for_string_targets(self) -> None:
        with pytest.raises(TypeError, match="applies to instance providers"):
            provider("app", key=Base)

    def test_key_rejected_for_the_name_keyword_form(self) -> None:
        with pytest.raises(TypeError, match="applies to instance providers"):
            provider(name="app", key=Base)

    @pytest.mark.parametrize("bad", [1, ["a"], object()], ids=["int", "list", "object"])
    def test_key_must_be_a_string_or_a_class(self, bad: Any) -> None:
        with pytest.raises(TypeError, match="expects a string name or a class"):
            provider(Sub(), key=bad)


class TestUnhashableKey:
    def test_use_rejects_an_unhashable_key(self) -> None:
        with pytest.raises(TypeError, match="expects a string name or a class"):
            use(["a"])  # type: ignore[call-overload]

    def test_unhashable_key_is_rejected_with_providers_active(self) -> None:
        with provider("app"), pytest.raises(TypeError, match="got list"):
            use(["a"])  # type: ignore[call-overload]


class TestActive:
    def test_lists_the_active_keys(self) -> None:
        assert dict(active()) == {}
        with provider("app", db="x"), provider(Sub()):
            keys = set(active())
            assert keys == {"app", Sub}

    def test_values_are_what_use_returns(self) -> None:
        with provider(Sub()):
            assert active()[Sub] is use(Sub)

    def test_view_is_read_only(self) -> None:
        with provider("app"), pytest.raises(TypeError):
            active()["other"] = 1  # type: ignore[index]

    def test_view_does_not_track_later_scopes(self) -> None:
        with provider("app"):
            snapshot = active()
            with provider("other"):
                assert "other" not in snapshot
                assert "other" in active()
