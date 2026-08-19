import copy
import pickle
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import pytest

from nodrill import FrozenContextError, provider, use


@dataclass
class Config:
    debug: bool = False
    url: str = "sqlite://"


class TestFrozenClassKeyed:
    def test_reads_allowed_writes_blocked(self) -> None:
        with provider(Config(debug=True), frozen=True):
            seen = use(Config)
            assert seen.debug is True
            with pytest.raises(FrozenContextError, match="frozen=True"):
                seen.debug = False
            with pytest.raises(FrozenContextError, match="cannot delete"):
                del seen.url

    def test_frozen_error_is_an_attribute_error(self) -> None:
        with provider(Config(), frozen=True):
            with pytest.raises(AttributeError):
                use(Config).debug = True

    def test_provider_block_keeps_writable_handle(self) -> None:
        with provider(Config(), frozen=True) as cfg:
            cfg.url = "pg://set-by-owner"
            assert use(Config).url == "pg://set-by-owner"

    def test_isinstance_matches_the_real_class(self) -> None:
        with provider(Config(), frozen=True):
            assert isinstance(use(Config), Config)

    def test_eq_hash_repr_dir_delegate(self) -> None:
        @dataclass(frozen=True)
        class Point:
            x: int = 1

        point = Point()
        with provider(point, frozen=True):
            proxy = use(Point)
            assert proxy == Point()
            assert hash(proxy) == hash(point)
            assert "frozen" in repr(proxy)
            assert "Point" in repr(proxy)
            assert "x" in dir(proxy)

    def test_container_protocols_delegate(self) -> None:
        @dataclass
        class Bundle:
            items: list[str] = field(default_factory=lambda: ["a", "b"])

            def __len__(self) -> int:
                return len(self.items)

            def __iter__(self) -> Iterator[str]:
                return iter(self.items)

            def __getitem__(self, index: int) -> str:
                return self.items[index]

            def __contains__(self, item: object) -> bool:
                return item in self.items

            def __bool__(self) -> bool:
                return bool(self.items)

        with provider(Bundle(), frozen=True):
            proxy = use(Bundle)
            assert len(proxy) == 2
            assert list(proxy) == ["a", "b"]
            assert proxy[0] == "a"
            assert "b" in proxy
            assert bool(proxy)

    @pytest.mark.parametrize(
        "operation",
        [pickle.dumps, copy.copy, copy.deepcopy],
        ids=["pickle", "copy", "deepcopy"],
    )
    def test_pickle_and_copy_refuse(self, operation: Callable[[object], object]) -> None:
        with provider(Config(), frozen=True):
            with pytest.raises(TypeError, match="cannot be pickled or copied"):
                operation(use(Config))

    def test_re_providing_a_frozen_view_registers_its_own_key(self) -> None:
        with provider(Config(), frozen=True):
            view = use(Config)
        # The class the view stands for, never the private one underneath it.
        with provider(view):
            assert use(Config) is view

    def test_raw_instance_is_never_frozen(self) -> None:
        cfg = Config()
        with provider(cfg, frozen=True):
            pass
        cfg.debug = True
        assert cfg.debug


class TestFrozenStringKeyed:
    def test_string_namespace_can_be_frozen(self) -> None:
        with provider("app", frozen=True, mode="ro") as ctx:
            assert use("app").mode == "ro"
            with pytest.raises(FrozenContextError):
                use("app").mode = "rw"
            ctx.mode = "owner-write"
            assert use("app").mode == "owner-write"

    def test_unfrozen_by_default(self) -> None:
        with provider("app", mode="rw"):
            use("app").mode = "changed"
            assert use("app").mode == "changed"
