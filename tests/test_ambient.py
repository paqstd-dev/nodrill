import asyncio
import threading

import pytest

from nodrill import context


class TestAmbientBasics:
    def test_set_then_read_elsewhere(self) -> None:
        def reader() -> str:
            value: str = context.request_id
            return value

        context.request_id = "r-42"
        assert reader() == "r-42"

    def test_contains_and_delete(self) -> None:
        context.tmp = 1
        assert "tmp" in context
        del context.tmp
        assert "tmp" not in context

    @pytest.mark.parametrize(
        ("preset", "match"),
        [
            (["alpha"], r"no attribute 'beta' \(currently set: alpha\)"),
            ([], r"no attribute 'beta' \(currently set: none\)"),
        ],
        ids=["some-keys-set", "nothing-set"],
    )
    def test_missing_attribute_message(self, preset: list[str], match: str) -> None:
        for name in preset:
            setattr(context, name, 1)
        with pytest.raises(AttributeError, match=match):
            _ = context.beta

    def test_delete_missing_attribute_raises(self) -> None:
        with pytest.raises(AttributeError, match="'ghost'"):
            del context.ghost

    def test_overwrite(self) -> None:
        context.n = 1
        context.n = 2
        assert context.n == 2

    def test_repr_lists_keys(self) -> None:
        context.b = 1
        context.a = 1
        assert repr(context) == "<nodrill.context [a, b]>"


class TestMonkeypatchInterop:
    def test_undo_removes_a_key_that_was_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(context, "feature_flag", True, raising=False)
        assert context.feature_flag is True
        monkeypatch.undo()
        assert "feature_flag" not in context

    def test_undo_restores_the_previous_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        context.mode = "live"
        monkeypatch.setattr(context, "mode", "test")
        assert context.mode == "test"
        monkeypatch.undo()
        assert context.mode == "live"


class TestAmbientIsolation:
    def test_new_thread_starts_empty(self) -> None:
        context.main_only = "yes"
        seen: list[bool] = []

        thread = threading.Thread(target=lambda: seen.append("main_only" in context))
        thread.start()
        thread.join()
        assert seen == [False]

    def test_thread_writes_do_not_leak_back(self) -> None:
        def worker() -> None:
            context.thread_local_value = "worker"

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        assert "thread_local_value" not in context

    async def test_task_inherits_snapshot_but_writes_stay_local(self) -> None:
        context.inherited = "from-parent"

        async def child() -> str:
            assert context.inherited == "from-parent"
            context.child_only = True
            inherited: str = context.inherited
            return inherited

        assert await asyncio.create_task(child()) == "from-parent"
        assert "child_only" not in context

    async def test_sibling_tasks_do_not_share_writes(self) -> None:
        async def sibling(name: str) -> bool:
            context.owner = name
            await asyncio.sleep(0)
            saw_own: bool = context.owner == name
            return saw_own

        results = await asyncio.gather(sibling("a"), sibling("b"))
        assert list(results) == [True, True]
