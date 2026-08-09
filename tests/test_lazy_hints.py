from __future__ import annotations

from dataclasses import dataclass

import pytest

from nodrill import FromCtx, NoProviderError, inject, injected, provider


@inject
def refers_forward(cfg: FromCtx[DefinedLater] = injected) -> str:
    """Decorated while `DefinedLater` does not exist yet — must not raise."""
    return cfg.tag


@dataclass
class DefinedLater:
    tag: str = "later"


class TestLazyPlan:
    def test_decoration_before_name_exists_then_call_works(self) -> None:
        with provider(DefinedLater(tag="resolved")):
            assert refers_forward() == "resolved"
        # Second call takes the cached-plan fast path.
        with provider(DefinedLater(tag="again")):
            assert refers_forward() == "again"

    def test_unresolvable_name_fails_at_first_call_not_import(self) -> None:
        @inject
        def broken(cfg: FromCtx[NeverDefined] = injected) -> None:  # type: ignore[name-defined]  # noqa: F821
            ...

        with pytest.raises(NameError, match="NeverDefined") as exc_info:
            broken()
        message = str(exc_info.value)
        assert "@inject: cannot resolve type hints for" in message
        assert "TYPE_CHECKING-only import" in message

    def test_unresolvable_hints_without_markers_pass_through(self) -> None:
        """Annotations only a type checker reads must not break the call."""

        @inject
        def handler(payload: NeverImported, n: int = 1) -> int:  # type: ignore[name-defined]  # noqa: F821
            return n

        assert handler("anything") == 1
        assert handler("anything", 2) == 2  # the cached empty plan, same result

    def test_by_name_mode_still_reports_unresolvable_hints(self) -> None:
        @inject(from_="app")
        def handler(payload: NeverImported) -> None:  # type: ignore[name-defined]  # noqa: F821
            ...

        with pytest.raises(NameError, match="cannot resolve type hints"):
            handler()  # type: ignore[call-arg]

    def test_lazy_plan_still_honors_explicit_args(self) -> None:
        with pytest.raises(NoProviderError):
            refers_forward()
        assert refers_forward(DefinedLater(tag="explicit")) == "explicit"
