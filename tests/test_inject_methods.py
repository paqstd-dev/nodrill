from collections.abc import Callable
from dataclasses import dataclass

import pytest

from nodrill import FromCtx, inject, injected, provider


@dataclass
class Config:
    tag: str = "cfg"


class Service:
    def __init__(self, name: str = "svc") -> None:
        self.name = name

    @inject
    def method(self, cfg: FromCtx[Config] = injected) -> str:
        return f"{self.name}:{cfg.tag}"

    @classmethod
    @inject
    def cls_method(cls, cfg: FromCtx[Config] = injected) -> str:
        return f"{cls.__name__}:{cfg.tag}"

    @staticmethod
    @inject
    def static_method(cfg: FromCtx[Config] = injected) -> str:
        return f"static:{cfg.tag}"


class ReversedOrder:
    """@inject applied over the classmethod/staticmethod descriptor."""

    @inject
    @classmethod
    def cls_method(cls, cfg: FromCtx[Config] = injected) -> str:
        return f"{cls.__name__}:{cfg.tag}"

    @inject
    @staticmethod
    def static_method(cfg: FromCtx[Config] = injected) -> str:
        return f"static:{cfg.tag}"


class NamedService:
    @inject(from_="app")
    def method(self, db: str = injected) -> str:
        return f"{type(self).__name__}:{db}"


class TestMethods:
    def test_instance_method(self) -> None:
        with provider(Config(tag="live")):
            assert Service("a").method() == "a:live"

    def test_instance_method_explicit_override(self) -> None:
        assert Service("a").method(Config(tag="manual")) == "a:manual"

    @pytest.mark.parametrize(
        ("call", "expected"),
        [
            (Service.cls_method, "Service:{tag}"),
            (Service.static_method, "static:{tag}"),
            (ReversedOrder.cls_method, "ReversedOrder:{tag}"),
            (ReversedOrder.static_method, "static:{tag}"),
        ],
        ids=["classmethod", "staticmethod", "classmethod-reversed", "staticmethod-reversed"],
    )
    def test_descriptor_kinds_in_both_decorator_orders(
        self, call: Callable[[], str], expected: str
    ) -> None:
        with provider(Config(tag="live")):
            assert call() == expected.format(tag="live")

    def test_staticmethod_via_instance(self) -> None:
        with provider(Config(tag="live")):
            assert Service().static_method() == "static:live"

    def test_from_mode_skips_self(self) -> None:
        with provider("app", db="pg", self="poison"):
            assert NamedService().method() == "NamedService:pg"
