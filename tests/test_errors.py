import copy
import pickle
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import pytest

from nodrill import (
    EnvelopeVersionError,
    ExpiredScopeError,
    KeyResolutionError,
    NoProviderError,
    provider,
    set_default,
    use,
)


@dataclass
class Settings:
    retries: int = 3


class TestNoProvider:
    @pytest.mark.parametrize("key", ["app", Settings], ids=["string-key", "class-key"])
    def test_use_without_provider_raises(self, key: Any) -> None:
        with pytest.raises(NoProviderError):
            use(key)

    def test_is_lookup_error(self) -> None:
        with pytest.raises(LookupError):
            use("app")

    @pytest.mark.parametrize(
        "fragment",
        ["'database'", "'app'", "Settings", "did you forget `with provider('database')`"],
        ids=["missing-key", "active-string-key", "active-class-key", "hint"],
    )
    def test_message_includes(self, fragment: str) -> None:
        with provider("app"), provider(Settings()):
            with pytest.raises(NoProviderError) as exc_info:
                use("database")
        assert fragment in str(exc_info.value)

    def test_exception_carries_key_and_active_keys(self) -> None:
        with provider("app"), provider(Settings()):
            with pytest.raises(NoProviderError) as exc_info:
                use("database")
        assert exc_info.value.key == "database"
        assert set(exc_info.value.active_keys) == {"app", Settings}

    def test_message_when_nothing_is_active(self) -> None:
        with pytest.raises(NoProviderError, match="No providers are active"):
            use("app")

    def test_class_key_message_mentions_set_default(self) -> None:
        with pytest.raises(NoProviderError, match=r"set_default\(Settings, \.\.\.\)"):
            use(Settings)

    def test_typo_suggestion(self) -> None:
        with provider("database"), provider("cache"):
            with pytest.raises(NoProviderError, match="Did you mean 'database'\\?"):
                use("datbase")

    def test_no_suggestion_when_nothing_is_close(self) -> None:
        with provider("cache"):
            with pytest.raises(NoProviderError) as exc_info:
                use("zzz")
        assert "Did you mean" not in str(exc_info.value)

    def test_hand_built_error_with_an_odd_key_still_builds_a_message(self) -> None:
        """NoProviderError is public API: use() screens keys, a direct caller need not."""
        assert "42" in str(NoProviderError(42))

    def test_use_rejects_non_key_types(self) -> None:
        with pytest.raises(TypeError, match="string name or a class"):
            use(42)  # type: ignore[call-overload]


class TestErrorsSurviveATrip:
    """A worker process delivers a failure by pickling it, so both errors have to survive that."""

    def test_no_provider_error_pickles_back_to_itself(self) -> None:
        original = NoProviderError("database", ("app", Settings))
        restored = pickle.loads(pickle.dumps(original))
        assert restored.key == "database"
        assert restored.active_keys == ("app", Settings)
        assert str(restored) == str(original)

    def test_no_provider_error_copies_back_to_itself(self) -> None:
        original = NoProviderError("database")
        assert copy.copy(original).key == "database"

    def test_key_resolution_error_pickles_back_to_itself(self) -> None:
        original = KeyResolutionError("myapp.context:Scope", "cannot import 'myapp.context'")
        restored = pickle.loads(pickle.dumps(original))
        assert restored.path == "myapp.context:Scope"
        assert str(restored) == str(original)

    def test_key_resolution_error_copies_back_to_itself(self) -> None:
        original = KeyResolutionError("myapp.context:Scope", "no such attribute")
        assert str(copy.copy(original)) == str(original)

    @pytest.mark.skipif(not hasattr(BaseException, "add_note"), reason="notes are 3.11 and up")
    @pytest.mark.parametrize(
        "make",
        [
            lambda: NoProviderError("database"),
            lambda: KeyResolutionError("myapp.context:Scope", "no such attribute"),
            lambda: EnvelopeVersionError(2, 1),
            lambda: ExpiredScopeError(
                "app", "query", opened=("a.py", 1), exited=("a.py", 1), used=("b.py", 9)
            ),
        ],
        ids=["no-provider", "key-resolution", "envelope-version", "expired-scope"],
    )
    def test_an_error_carries_its_notes_across(self, make: Callable[[], Exception]) -> None:
        """The scope annotate_exceptions() attached in the worker is why the trip is taken."""
        original = make()
        # Ignored rather than guarded, since mypy reads this at 3.10, where notes do not exist.
        original.add_note("nodrill scope: Namespace('trace')")  # type: ignore[attr-defined]
        restored = pickle.loads(pickle.dumps(original))
        assert restored.__notes__ == ["nodrill scope: Namespace('trace')"]


class TestSetDefault:
    @pytest.mark.parametrize(
        ("factory", "expected_retries"),
        [(Settings, 3), (lambda: Settings(retries=9), 9)],
        ids=["class-as-factory", "lambda-factory"],
    )
    def test_default_returned_outside_provider(
        self, factory: Callable[[], Settings], expected_retries: int
    ) -> None:
        set_default(Settings, factory)
        assert use(Settings).retries == expected_retries

    def test_provider_still_wins_over_default(self) -> None:
        set_default(Settings, Settings)
        with provider(Settings(retries=1)):
            assert use(Settings).retries == 1
        assert use(Settings).retries == 3

    def test_factory_called_per_miss(self) -> None:
        calls: list[int] = []

        def factory() -> Settings:
            calls.append(1)
            return Settings()

        set_default(Settings, factory)
        use(Settings)
        use(Settings)
        assert len(calls) == 2

    def test_clearing_default_restores_error(self) -> None:
        set_default(Settings, Settings)
        set_default(Settings, None)
        with pytest.raises(NoProviderError):
            use(Settings)

    def test_returns_class_for_chaining(self) -> None:
        assert set_default(Settings, Settings) is Settings

    def test_rejects_non_class(self) -> None:
        with pytest.raises(TypeError, match="registers classes"):
            set_default("app", dict)  # type: ignore[arg-type]

    def test_rejects_non_callable_factory(self) -> None:
        with pytest.raises(TypeError, match="factory must be callable"):
            set_default(Settings, Settings())  # type: ignore[arg-type]

    def test_default_with_mutable_field(self) -> None:
        @dataclass
        class Bag:
            items: list[str] = field(default_factory=list)

        set_default(Bag, Bag)
        use(Bag).items.append("x")
        assert use(Bag).items == []  # fresh instance per miss, not a cached singleton


class TestUseDefault:
    def test_call_site_default_for_string_key(self) -> None:
        assert use("app", default=None) is None

    def test_call_site_default_for_class_key(self) -> None:
        fallback = Settings(retries=0)
        assert use(Settings, default=fallback) is fallback

    def test_provider_wins_over_call_site_default(self) -> None:
        with provider("app", mode="live"):
            namespace = use("app", default=None)
        assert namespace is not None
        assert namespace.mode == "live"

    def test_registered_default_wins_over_call_site_default(self) -> None:
        set_default(Settings, lambda: Settings(retries=7))
        assert use(Settings, default=Settings(retries=0)).retries == 7

    def test_none_is_a_real_default(self) -> None:
        assert use("missing", default=None) is None


class Store(Protocol):
    def put(self, blob: bytes) -> None: ...


class Base:
    pass


class Derived(Base):
    pass


class TestTheHintCanBeFollowed:
    """A hint proposing what the caller cannot write is worse than no hint."""

    def test_a_subclass_in_scope_names_the_exact_key_rule(self) -> None:
        with provider(Derived()), pytest.raises(NoProviderError) as raised:
            use(Base)
        message = str(raised.value)
        assert "Derived is active and subclasses Base" in message
        assert "provider(instance, key=Base)" in message
        assert "provider(Base(...))" not in message

    def test_a_protocol_is_never_offered_as_a_constructor(self) -> None:
        with pytest.raises(NoProviderError) as raised:
            # The point of key= is that consumers depend on the protocol, not the class.
            use(Store)  # type: ignore[type-abstract]
        message = str(raised.value)
        assert "provider(instance, key=Store)" in message
        assert "provider(Store(...))" not in message
