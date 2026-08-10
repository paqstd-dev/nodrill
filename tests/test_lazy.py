"""Providers whose value is built on the first read, and not at all without one."""

import asyncio
import copy
import gc
import threading
import weakref
from dataclasses import dataclass
from types import TracebackType
from typing import Any

import pytest

from nodrill import (
    FromCtx,
    FrozenContextError,
    NoProviderError,
    active,
    inject,
    injected,
    lazy,
    provider,
    set_default,
    use,
    wrap,
)


@dataclass
class Config:
    tag: str = "?"


def touch() -> str:
    """Read the provided value, which is what makes a lazy provider resolve."""
    tag: str = use(Config).tag
    return tag


class Escapable:
    """A target that can hand the real object back, past the proxy."""

    def itself(self) -> "Escapable":
        return self


class Counter:
    """A factory that records how often it ran."""

    def __init__(self, tag: str = "built") -> None:
        self.tag = tag
        self.calls = 0

    def __call__(self) -> Config:
        self.calls += 1
        return Config(tag=self.tag)


class TestResolution:
    def test_unread_provider_never_builds(self) -> None:
        """The whole point: a scope nothing reads costs nothing to open."""
        factory = Counter()
        with provider(lazy(Config, factory)):
            pass
        assert factory.calls == 0

    def test_entering_alone_does_not_build(self) -> None:
        factory = Counter()
        with provider(lazy(Config, factory)):
            assert factory.calls == 0

    def test_many_reads_build_once(self) -> None:
        factory = Counter()
        with provider(lazy(Config, factory)):
            for _ in range(5):
                assert use(Config).tag == "built"
        assert factory.calls == 1

    def test_each_scope_builds_its_own_value(self) -> None:
        factory = Counter()
        for _ in range(2):
            with provider(lazy(Config, factory)):
                assert use(Config).tag == "built"
        assert factory.calls == 2

    def test_reused_provider_object_builds_again(self) -> None:
        """The cache belongs to the scope, so a second entry starts unresolved."""
        factory = Counter()
        opened = provider(lazy(Config, factory))
        with opened:
            assert use(Config).tag == "built"
        with opened:
            assert use(Config).tag == "built"
        assert factory.calls == 2

    def test_none_is_a_value_and_not_a_miss(self) -> None:
        """A factory returning None caches it, rather than looking unresolved forever."""
        calls: list[int] = []
        nothing: Any = None

        def factory() -> Any:
            calls.append(1)
            return None

        with provider(lazy(Config, factory)):
            assert use(Config) == nothing
            assert repr(use(Config)) == "<lazy Config, None>"
        assert calls == [1]

    def test_built_value_is_not_pinned_after_exit(self) -> None:
        """Nothing keeps the built value alive once the scope that built it is gone."""
        holder = provider(lazy(Escapable, Escapable))
        with holder:
            escaped = weakref.ref(use(Escapable).itself())
            assert escaped() is not None
        gc.collect()
        assert escaped() is None

    def test_value_is_removed_on_exit(self) -> None:
        with provider(lazy(Config, Config)):
            pass
        with pytest.raises(NoProviderError):
            use(Config)

    def test_inner_scope_shadows_the_outer_one(self) -> None:
        with provider(lazy(Config, Counter("outer"))):
            with provider(lazy(Config, Counter("inner"))):
                assert use(Config).tag == "inner"
            assert use(Config).tag == "outer"

    async def test_async_with_works(self) -> None:
        factory = Counter()
        async with provider(lazy(Config, factory)):
            assert factory.calls == 0
            assert use(Config).tag == "built"
        assert factory.calls == 1

    def test_reentering_an_open_provider_raises(self) -> None:
        opened = provider(lazy(Config, Config))
        with opened, pytest.raises(RuntimeError, match="already active"):
            with opened:
                pass


class TestFailure:
    def test_exception_is_cached_and_re_raised(self) -> None:
        calls: list[int] = []

        def factory() -> Config:
            calls.append(1)
            raise ValueError("factory failed")

        seen: list[BaseException] = []
        with provider(lazy(Config, factory)):
            for _ in range(3):
                with pytest.raises(ValueError, match="factory failed") as caught:
                    touch()
                seen.append(caught.value)
        assert calls == [1]
        assert seen[0] is seen[1] is seen[2]

    def test_a_base_exception_is_not_cached(self) -> None:
        """A cancelled task says nothing about the factory, so the next read runs it again."""
        attempts: list[int] = []

        def factory() -> Config:
            attempts.append(1)
            if len(attempts) == 1:
                raise asyncio.CancelledError
            return Config(tag="second")

        with provider(lazy(Config, factory)):
            with pytest.raises(asyncio.CancelledError):
                touch()
            assert use(Config).tag == "second"
        assert attempts == [1, 1]

    def test_a_later_scope_gets_a_fresh_attempt(self) -> None:
        attempts: list[int] = []

        def factory() -> Config:
            attempts.append(1)
            if len(attempts) == 1:
                raise ValueError("first attempt")
            return Config(tag="second")

        with provider(lazy(Config, factory)), pytest.raises(ValueError, match="first"):
            touch()
        with provider(lazy(Config, factory)):
            assert use(Config).tag == "second"

    def test_factory_reading_its_own_key_raises(self) -> None:
        """Reentrancy has to raise rather than deadlock or recurse."""

        def factory() -> Config:
            return Config(tag=use(Config).tag)

        with provider(lazy(Config, factory)):
            with pytest.raises(RuntimeError, match="cannot read or return its own key"):
                touch()
            with pytest.raises(RuntimeError, match="cannot read or return its own key"):
                touch()

    def test_factory_returning_its_own_key_raises(self) -> None:
        """The same mistake by return: the cell would otherwise become its own value."""

        def factory() -> Config:
            returned: Config = use(Config)
            return returned

        with provider(lazy(Config, factory)):
            with pytest.raises(RuntimeError, match="cannot read or return its own key"):
                touch()
            with pytest.raises(RuntimeError, match="cannot read or return its own key"):
                touch()


class TestThreads:
    def test_two_threads_in_one_scope_build_once(self) -> None:
        """Two readers in one scope get one build, and both of them get the value."""
        started = threading.Event()
        release = threading.Event()
        calls: list[int] = []

        def factory() -> Config:
            calls.append(1)
            started.set()
            release.wait(10)
            return Config(tag="shared")

        seen: list[Any] = []
        lock = threading.Lock()

        def read() -> None:
            tag = use(Config).tag
            with lock:
                seen.append(tag)

        with provider(lazy(Config, factory)):
            reader = wrap(read)
            first = threading.Thread(target=reader)
            first.start()
            started.wait(10)
            second = threading.Thread(target=reader)
            second.start()
            release.set()
            first.join(10)
            second.join(10)

        assert calls == [1]
        assert seen == ["shared", "shared"]


class TestFactoryContext:
    def test_factory_reads_the_scope_it_was_declared_in(self) -> None:
        """An inner provider must not decide what an outer lazy value is built from."""
        with provider("tenant", name="acme"):
            with provider(lazy(Config, lambda: Config(tag=use("tenant").name))):
                with provider("tenant", name="shadow"):
                    assert use(Config).tag == "acme"
                assert use(Config).tag == "acme"

    def test_escaped_view_resolves_against_its_own_scope(self) -> None:
        """A view stashed by a callee is still bound to the scope that declared it."""
        stashed: list[Any] = []
        with provider("tenant", name="acme"):
            with provider(lazy(Config, lambda: Config(tag=use("tenant").name))):
                stashed.append(use(Config))
        with provider("tenant", name="evil"):
            assert stashed[0].tag == "acme"


class TestBehavesLikeTheValue:
    def test_isinstance_holds_before_and_after(self) -> None:
        with provider(lazy(Config, Config)):
            held = use(Config)
            assert isinstance(held, Config)
            assert held.tag == "?"
            assert isinstance(held, Config)

    def test_class_reports_the_key_then_the_value(self) -> None:
        with provider(lazy(Config, Config)):
            held: Any = use(Config)
            assert held.__class__ is Config
            assert held.tag == "?"
            assert held.__class__ is Config

    def test_repr_does_not_resolve(self) -> None:
        factory = Counter()
        with provider(lazy(Config, factory)):
            assert repr(use(Config)) == "<lazy Config, unresolved>"
            assert factory.calls == 0
            touch()
            assert repr(use(Config)) == "<lazy Config, Config(tag='built')>"

    def test_repr_names_a_failure(self) -> None:
        def factory() -> Config:
            raise ValueError("no")

        with provider(lazy(Config, factory)):
            with pytest.raises(ValueError, match="no"):
                touch()
            assert repr(use(Config)) == "<lazy Config, failed with ValueError>"

    def test_active_shows_the_cell_without_resolving(self) -> None:
        factory = Counter()
        with provider(lazy(Config, factory)):
            snapshot = active()
            assert set(snapshot) == {Config}
            assert "unresolved" in repr(snapshot[Config])
        assert factory.calls == 0

    def test_attributes_read_write_and_delete(self) -> None:
        with provider(lazy(Bag, Bag)):
            held = use(Bag)
            held.entered = 7
            assert use(Bag).entered == 7
            del held.items
            assert not hasattr(use(Bag), "items")

    def test_equality_hashing_and_dir(self) -> None:
        with provider(lazy(Config, lambda: Config(tag="x"))):
            held = use(Config)
            assert held == Config(tag="x")
            assert held != Config(tag="y")
            assert "tag" in dir(held)
        with provider(lazy(Counter, Counter)):
            assert hash(use(Counter)) == hash(use(Counter))

    def test_copying_copies_the_value(self) -> None:
        with provider(lazy(Config, lambda: Config(tag="original"))):
            duplicate = copy.copy(use(Config))
            assert duplicate == Config(tag="original")
            assert type(duplicate) is Config


class Bag:
    """A target answering the container, numeric and context-manager protocols."""

    def __init__(self) -> None:
        self.items: dict[str, int] = {"a": 1}
        self.entered = 0

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, key: str) -> int:
        return self.items[key]

    def __setitem__(self, key: str, value: int) -> None:
        self.items[key] = value

    def __delitem__(self, key: str) -> None:
        del self.items[key]

    def __add__(self, other: int) -> int:
        return len(self.items) + other

    def __radd__(self, other: int) -> int:
        return other + len(self.items)

    def __enter__(self) -> str:
        self.entered += 1
        return "inside"

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


class TestProtocols:
    def test_forwarded_protocols_resolve_first(self) -> None:
        with provider(lazy(Bag, Bag)):
            held = use(Bag)
            assert len(held) == 1
            assert held["a"] == 1
            assert held + 1 == 2

    def test_reflected_operators_resolve_first(self) -> None:
        with provider(lazy(Bag, Bag)):
            assert 10 + use(Bag) == 11

    def test_invoked_protocols_resolve_first(self) -> None:
        with provider(lazy(Bag, Bag)):
            with use(Bag) as inside:
                assert inside == "inside"

    def test_item_writes_reach_the_value(self) -> None:
        bag = Bag()
        with provider(lazy(Bag, lambda: bag)):
            held = use(Bag)
            held["b"] = 2
            del held["a"]
        assert bag.items == {"b": 2}

    def test_in_place_operators_mutate_the_value(self) -> None:
        shared = ["first"]
        with provider(lazy(list, lambda: shared)):
            held: Any = use(list)
            held += ["second"]
            # The operator hands back the value itself, so the rebound name is the real list.
            assert held is shared
        assert shared == ["first", "second"]

    def test_first_touch_through_a_protocol_resolves(self) -> None:
        factory = Counter()
        with provider(lazy(Config, factory)):
            assert use(Config) == Config(tag="built")
        assert factory.calls == 1


class TestFrozen:
    def test_block_keeps_a_writable_handle(self) -> None:
        """frozen=True still means the owner writes and the callees do not."""
        with provider(lazy(Config, Config), frozen=True) as cfg:
            cfg.tag = "owner writes"
            assert use(Config).tag == "owner writes"
            with pytest.raises(FrozenContextError):
                use(Config).tag = "callee writes"

    def test_both_views_share_one_build(self) -> None:
        factory = Counter()
        with provider(lazy(Config, factory), frozen=True) as cfg:
            assert cfg.tag == "built"
            assert use(Config).tag == "built"
        assert factory.calls == 1

    def test_resolved_value_is_read_only(self) -> None:
        with provider(lazy(Config, Config), frozen=True):
            with pytest.raises(FrozenContextError):
                use(Config).tag = "no"
            assert use(Config).tag == "?"

    def test_frozen_lazy_still_builds_once(self) -> None:
        factory = Counter()
        with provider(lazy(Config, factory), frozen=True):
            assert use(Config).tag == "built"
            assert use(Config).tag == "built"
        assert factory.calls == 1

    def test_frozen_lazy_reports_the_value_class(self) -> None:
        with provider(lazy(Config, Config), frozen=True):
            held: Any = use(Config)
            assert isinstance(held, Config)
            assert held.tag == "?"
            assert held.__class__ is Config


class TestInject:
    def test_injected_parameter_resolves_at_the_touch(self) -> None:
        factory = Counter()

        @inject
        def hold(cfg: FromCtx[Config] = injected) -> Any:
            return cfg

        with provider(lazy(Config, factory)):
            held = hold()
            assert factory.calls == 0
            assert held.tag == "built"
        assert factory.calls == 1

    def test_injected_parameter_reads_the_value(self) -> None:
        @inject
        def read(cfg: FromCtx[Config] = injected) -> str:
            return cfg.tag

        with provider(lazy(Config, Counter("injected"))):
            assert read() == "injected"

    def test_explicit_argument_still_wins(self) -> None:
        factory = Counter()

        @inject
        def read(cfg: FromCtx[Config] = injected) -> str:
            return cfg.tag

        with provider(lazy(Config, factory)):
            assert read(Config(tag="passed")) == "passed"
        assert factory.calls == 0


class TestRejections:
    def test_string_key_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="nothing to defer"):
            lazy("app", Config)  # type: ignore[arg-type]

    def test_non_class_key_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="expects a class as its key"):
            lazy(42, Config)  # type: ignore[arg-type]

    def test_non_callable_factory_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="factory must be callable"):
            lazy(Config, Config())  # type: ignore[arg-type]

    def test_use_rejects_the_carrier(self) -> None:
        carrier: Any = lazy(Config, Config)
        with pytest.raises(TypeError, match="target rather than a key"):
            use(carrier)

    def test_set_default_rejects_the_carrier(self) -> None:
        carrier: Any = lazy(Config, Config)
        with pytest.raises(TypeError, match="not what lazy\\(\\) returns"):
            set_default(Config, carrier)

    def test_provider_rejects_the_carrier_as_a_key(self) -> None:
        carrier: Any = lazy(Config, Config)
        with pytest.raises(TypeError, match="goes in the positional slot"):
            provider(Config(), key=carrier)

    def test_provider_rejects_a_key_beside_a_lazy_target(self) -> None:
        with pytest.raises(TypeError, match="lazy\\(\\) already names the key"):
            provider(lazy(Config, Config), key=Bag)

    def test_provider_rejects_keyword_values_with_a_lazy_target(self) -> None:
        with pytest.raises(TypeError, match="only supported for string-named providers"):
            provider(lazy(Config, Config), tag="x")  # type: ignore[call-overload]

    def test_carrier_says_it_is_not_the_value(self) -> None:
        carrier: Any = lazy(Config, Config)
        with pytest.raises(AttributeError, match="target for provider"):
            _ = carrier.tag

    def test_carrier_repr_names_the_key(self) -> None:
        carrier: Any = lazy(Config, Config)
        assert repr(carrier).startswith("lazy(Config, ")
