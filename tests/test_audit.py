"""What a run records, what the contract file says, and what the tool admits it cannot know."""

from __future__ import annotations

import asyncio
import atexit
import multiprocessing.util
import os
import runpy
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from nodrill import NoProviderError, provider, ref, set_default, use, wrap
from nodrill._audit import (
    _contract,
    _counted,
    _declared,
    _dump,
    _merge,
    _new_run,
    _parse,
    _render,
    _summary,
    _unseen,
    main,
)
from nodrill._debug import _arm, _declared_entries, _reads, _state
from tests.audit_app.app import User, open_connection, run_job, run_report, running, serve_http

_ROOT = Path(__file__).parent.parent
HEADER = "# nodrill contract 1"
APP = "tests.audit_app.app"
TAB = "\t"


@pytest.fixture
def recording() -> Iterator[set[tuple[str, str, str]]]:
    """Turn the audit on for one test, which no public name does on purpose."""
    # Saved and restored rather than switched off, since a process may be recording for real.
    saved = (_state.auditing, _state.watching, set(_reads), set(_declared_entries))
    _reads.clear()
    _state.auditing = True
    _state.watching = True
    try:
        yield _reads
    finally:
        _state.auditing, _state.watching = saved[0], saved[1]
        _reads.clear()
        _reads.update(saved[2])
        _declared_entries.clear()
        _declared_entries.update(saved[3])


@pytest.fixture
def declaring(recording: set[tuple[str, str, str]]) -> set[tuple[str, str, str]]:
    """Name the app's two boundaries the way NODRILL_CONTRACT_ENTRY does."""
    # The recording fixture restores the declared set, so this one only has to fill it.
    _declared_entries.update({"'http request'", "'celery worker'"})
    return recording


@pytest.fixture
def armed(recording: set[tuple[str, str, str]]) -> Iterator[list[tuple[Any, ...]]]:
    """Collect what _arm registers, so a test never leaves a real hook on this process."""
    calls: list[tuple[Any, ...]] = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(atexit, "register", lambda *call: calls.append(call))
        patch.setattr(
            multiprocessing.util, "Finalize", lambda *call, **kw: calls.append((call, kw))
        )
        yield calls


def _facts(reads: set[tuple[str, str, str]]) -> set[str]:
    """Render what was recorded the way the contract file does, minus the header."""
    return {line for line in _render(reads).splitlines() if line != HEADER}


def _entries(reads: set[tuple[str, str, str]]) -> set[str]:
    """The first column, which is the whole question the entry point rule answers."""
    return {entry for entry, _, _ in reads}


class TestWhatARunRecords:
    """A read is credited to the outermost block open above it."""

    def test_a_read_is_credited_to_the_entry_point(self, declaring: Any) -> None:
        with running():
            serve_http("ada")
        assert f"'http request'{TAB}requires{TAB}{APP}:User" in _facts(declaring)

    def test_a_nested_block_does_not_become_an_entry_point(self, declaring: Any) -> None:
        with running():
            serve_http("ada")
        assert _entries(declaring) == {"'http request'"}

    def test_a_class_keyed_block_is_an_entry_point_like_any_other(self, recording: Any) -> None:
        with running():
            use(User, default=None)
        assert _entries(recording) == {f"{APP}:Settings"}

    def test_a_string_key_keeps_its_quotes_and_a_class_key_is_a_path(self, declaring: Any) -> None:
        with running():
            serve_http("ada")
        assert f"'http request'{TAB}requires{TAB}'http request'" in _facts(declaring)
        assert f"'http request'{TAB}requires{TAB}{APP}:Origin" in _facts(declaring)

    def test_a_read_through_inject_is_recorded_like_any_other(self, declaring: Any) -> None:
        with running(), provider("http request"):
            open_connection()
        assert _facts(declaring) == {f"'http request'{TAB}requires{TAB}{APP}:Settings"}

    def test_a_ref_key_records_what_it_resolves_to(self, recording: Any) -> None:
        with provider("http request"), provider(User("ada")):
            assert use(ref(f"{APP}:User")).name == "ada"
        assert _facts(recording) == {f"'http request'{TAB}requires{TAB}{APP}:User"}

    def test_a_read_outside_every_block_has_no_entry_point(self, recording: Any) -> None:
        class Loose:
            pass

        set_default(Loose, Loose)
        use(Loose)
        assert _entries(recording) == {"(none)"}

    def test_two_classes_of_the_same_name_stay_apart(self, recording: Any) -> None:
        class User:  # the point is that this collides with the app's User
            pass

        with provider("boundary"), provider(User()):
            use(User)
        recorded = {line for line in _facts(recording) if "User" in line}
        assert len(recorded) == 1
        assert f"{APP}:User" not in next(iter(recorded))


class TestTheCollapseAndTheDeclaration:
    """A layer above the boundaries swallows them, which is why a boundary can be named."""

    def test_a_process_wide_layer_swallows_every_boundary(self, recording: Any) -> None:
        with running():
            serve_http("ada")
            run_job("grace")
        assert _entries(recording) == {f"{APP}:Settings"}

    def test_a_declared_key_mints_its_own_entry_point_under_that_layer(
        self, declaring: Any
    ) -> None:
        with running():
            serve_http("ada")
            run_job("grace")
        assert _entries(declaring) == {"'http request'", "'celery worker'"}

    def test_the_fallback_lands_on_the_boundary_that_let_it_happen(self, declaring: Any) -> None:
        with running():
            run_job("grace")
        assert f"'celery worker'{TAB}set_default{TAB}{APP}:Origin" in _facts(declaring)

    def test_a_declared_key_nothing_opened_is_reported(self) -> None:
        reads = {("'http request'", "requires", "x")}
        assert _unseen(reads, frozenset({"'http request'"})) is None
        message = _unseen(reads, frozenset({"'http request'", "'celery worker'"}))
        assert message is not None
        assert "no block opened 'celery worker'" in message

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("", frozenset()),
            ("'a'", frozenset({"'a'"})),
            ("'a', pkg:B ", frozenset({"'a'", "pkg:B"})),
        ],
        ids=["empty", "one", "several"],
    )
    def test_the_variable_is_rendered_keys_separated_by_commas(
        self, value: str, expected: frozenset[str]
    ) -> None:
        assert _declared(value) == expected


class TestWhatARaiseWouldNeverReport:
    """A registration answering a miss is the case the audit exists for."""

    def test_a_set_default_fallback_is_recorded_under_its_entry_point(self, recording: Any) -> None:
        run_job("grace")
        assert f"'celery worker'{TAB}set_default{TAB}{APP}:Origin" in _facts(recording)

    def test_a_default_argument_is_recorded_too(self, recording: Any) -> None:
        with provider("celery worker"):
            assert use(User, default=None) is None
        assert f"'celery worker'{TAB}default{TAB}{APP}:User" in _facts(recording)

    def test_a_miss_that_actually_raises_records_nothing(self, recording: Any) -> None:
        with pytest.raises(NoProviderError):
            run_report()
        assert not any("User" in line for line in _facts(recording))

    @pytest.mark.parametrize("call", [lambda: use(User), open_connection], ids=["use", "inject"])
    def test_a_miss_carries_no_internal_exception(self, call: Any) -> None:
        """The wrapper resolves inside its own except KeyError and must not show it."""
        with pytest.raises(NoProviderError) as raised:
            call()
        assert raised.value.__suppress_context__


class TestTheLabelSurvivesTheAwkwardPaths:
    """The entry point rides the registry, so it goes wherever the registry goes."""

    def test_a_block_closing_out_of_order_keeps_the_entry_point(self, recording: Any) -> None:
        def tenant(slug: str) -> Iterator[None]:
            with provider("tenant", slug=slug):
                yield
                yield

        with provider("http request", route="/"):
            first, second = tenant("acme"), tenant("globex")
            list(zip(first, second, strict=False))
            list(first)
            list(second)
            use("http request")
        assert _facts(recording) == {f"'http request'{TAB}requires{TAB}'http request'"}

    def test_a_repair_does_not_hand_its_label_to_the_next_boundary(self, recording: Any) -> None:
        """The mapping a repair leaves outlives its chain, and must not name what follows."""

        def tenant(slug: str) -> Iterator[None]:
            with provider("tenant", slug=slug):
                yield
                yield

        with provider("http request", route="/"):
            first, second = tenant("acme"), tenant("globex")
            list(zip(first, second, strict=False))
            list(first)
            list(second)
        with provider("celery worker"), provider(User("grace")):
            use(User)
        facts = _facts(recording)
        assert f"'celery worker'{TAB}requires{TAB}{APP}:User" in facts
        assert f"'http request'{TAB}requires{TAB}{APP}:User" not in facts

    def test_a_thread_carries_the_entry_point_it_was_wrapped_under(
        self, recording: Any, in_thread: Any
    ) -> None:
        with provider("http request", route="/"), provider(User("ada")):
            in_thread(wrap(lambda: use(User)))
        assert f"'http request'{TAB}requires{TAB}{APP}:User" in _facts(recording)

    def test_a_thread_nobody_wrapped_reads_under_no_entry_point(
        self, recording: Any, in_thread: Any
    ) -> None:
        """The bug the tool exists to surface, recorded as the fallback it becomes."""

        class Loose:
            pass

        set_default(Loose, Loose)
        with provider("http request", route="/"):
            in_thread(lambda: use(Loose))
        assert _entries(recording) == {"(none)"}

    async def test_a_sibling_task_reads_under_its_own_entry_point(self, recording: Any) -> None:
        async def worker(label: str) -> None:
            with provider(label), provider(User(label)):
                await asyncio.sleep(0)
                use(User)

        await asyncio.gather(worker("http request"), worker("celery worker"))
        assert _facts(recording) == {
            f"'http request'{TAB}requires{TAB}{APP}:User",
            f"'celery worker'{TAB}requires{TAB}{APP}:User",
        }


class TestTheSwitch:
    """Off is the default, and arming is a function so it can be tested without a child."""

    def test_an_unset_variable_arms_nothing(self) -> None:
        environ: dict[str, str] = {}
        before = (_state.auditing, _state.watching)
        _arm(environ)
        assert (_state.auditing, _state.watching) == before
        assert environ == {}

    def test_arming_sets_the_switches_and_joins_a_run(
        self, tmp_path: Path, armed: list[tuple[Any, ...]]
    ) -> None:
        environ = {"NODRILL_CONTRACT": str(tmp_path), "NODRILL_CONTRACT_ENTRY": "'a'"}
        _arm(environ)
        assert _state.auditing
        assert _state.watching
        assert "'a'" in _declared_entries
        # Written back so a child interpreter joins this run rather than starting one.
        assert environ["NODRILL_CONTRACT_RUN"]
        # Both, since a pool worker exits through os._exit and never runs atexit.
        assert len(armed) == 2

    def test_an_inherited_run_is_kept(self, tmp_path: Path, armed: list[tuple[Any, ...]]) -> None:
        environ = {"NODRILL_CONTRACT": str(tmp_path), "NODRILL_CONTRACT_RUN": "given"}
        _arm(environ)
        assert environ["NODRILL_CONTRACT_RUN"] == "given"
        assert all("given" in repr(call) for call in armed)

    def test_a_run_with_the_switch_off_records_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Off is the state a lookup pays nothing for, so it is worth pinning as behaviour."""
        monkeypatch.setattr(_state, "auditing", False)
        before = set(_reads)
        with running():
            serve_http("ada")
        assert set(_reads) == before


class TestTheContractFile:
    """The file is the delivery mechanism, so its shape is the deliverable."""

    def test_the_header_names_the_format(self) -> None:
        assert _render(set()) == f"{HEADER}\n"

    def test_lines_are_sorted_so_the_file_is_not_a_property_of_the_run(self) -> None:
        reads = {("'b'", "requires", "x"), ("'a'", "requires", "y"), ("'a'", "default", "z")}
        assert _render(reads).splitlines()[1:] == [
            f"'a'{TAB}default{TAB}z",
            f"'a'{TAB}requires{TAB}y",
            f"'b'{TAB}requires{TAB}x",
        ]

    def test_one_new_key_is_one_added_line(self) -> None:
        before = _render({("'a'", "requires", "x"), ("'a'", "requires", "z")})
        after = _render({("'a'", "requires", spelling) for spelling in "xyz"})
        assert set(after.splitlines()) - set(before.splitlines()) == {f"'a'{TAB}requires{TAB}y"}

    def test_a_key_holding_two_spaces_is_still_one_line_of_three_fields(
        self, recording: Any
    ) -> None:
        with provider("http  request"), provider("a  b", tag=1):
            use("a  b")
        [line] = _facts(recording)
        assert line.split(TAB) == ["'http  request'", "requires", "'a  b'"]

    def test_a_key_holding_a_newline_is_still_one_line(self, recording: Any) -> None:
        with provider("http request"), provider("two\nlines", tag=1):
            use("two\nlines")
        [line] = _facts(recording)
        assert line.split(TAB) == ["'http request'", "requires", "'two\\nlines'"]

    def test_a_contract_round_trips(self) -> None:
        reads = {("'a'", "requires", "x"), ("'b'", "set_default", "y")}
        assert _parse(_render(reads), "test") == reads

    @pytest.mark.parametrize(
        ("text", "shown"),
        [("", "an empty file"), ("# nodrill contract 2\n", "# nodrill contract 2")],
        ids=["empty", "another version"],
    )
    def test_a_version_this_reader_does_not_know_is_refused(self, text: str, shown: str) -> None:
        with pytest.raises(ValueError, match="not a nodrill contract this version reads") as raised:
            _parse(text, "somewhere")
        assert shown in str(raised.value)


class TestShards:
    """One run is many processes, so the record is written per process and merged."""

    def test_a_process_that_recorded_nothing_writes_no_shard(self, tmp_path: Path) -> None:
        _dump(str(tmp_path / "missing"), "run", set())
        assert not (tmp_path / "missing").exists()

    def test_a_shard_round_trips(self, tmp_path: Path) -> None:
        reads = {("'a'", "requires", "x"), ("'b'", "default", "y")}
        _dump(str(tmp_path), "run", set(reads))
        assert _merge(str(tmp_path)) == (reads, 1, 0)

    def test_dumping_twice_writes_one_shard(self, tmp_path: Path) -> None:
        """A pool worker is finalized as well as registered, so a second dump is a no-op."""
        reads = {("'a'", "requires", "x")}
        _dump(str(tmp_path), "run", reads)
        _dump(str(tmp_path), "run", reads)
        assert len(list(tmp_path.glob("*.shard"))) == 1

    def test_shards_from_several_processes_merge(self, tmp_path: Path) -> None:
        _dump(str(tmp_path), "run", {("'a'", "requires", "x")})
        _dump(str(tmp_path), "run", {("'b'", "requires", "y")})
        found, shards, stale = _merge(str(tmp_path))
        assert found == {("'a'", "requires", "x"), ("'b'", "requires", "y")}
        assert (shards, stale) == (2, 0)

    def test_an_earlier_run_in_the_same_directory_is_left_out(self, tmp_path: Path) -> None:
        first, second = _new_run(), _new_run()
        _dump(str(tmp_path), first, {("'a'", "requires", "gone")})
        _dump(str(tmp_path), second, {("'a'", "requires", "here")})
        assert _merge(str(tmp_path)) == ({("'a'", "requires", "here")}, 1, 1)

    def test_an_empty_directory_merges_to_nothing(self, tmp_path: Path) -> None:
        assert _merge(str(tmp_path)) == (set(), 0, 0)

    def test_a_run_id_is_unique(self) -> None:
        assert _new_run() != _new_run()


class TestWhatTheToolAdmits:
    """A guarantee that overstates itself is worse than no guarantee."""

    def test_the_summary_counts_what_it_rests_on(self) -> None:
        reads = {("'a'", "requires", "x"), ("'a'", "requires", "y"), ("'b'", "requires", "z")}
        assert _summary(reads, 2, 0) == (
            "nodrill: 3 facts under 2 entry points, recorded from 2 processes. "
            "A contract is only as complete as the run that recorded it."
        )

    def test_one_of_each_reads_as_a_sentence(self) -> None:
        assert _summary({("'a'", "requires", "x")}, 1, 0).startswith(
            "nodrill: 1 fact under 1 entry point, recorded from 1 process."
        )

    def test_shards_left_out_are_said_rather_than_dropped_quietly(self) -> None:
        assert _summary(set(), 1, 3).endswith("3 shards from an earlier run were left out.")

    @pytest.mark.parametrize(
        ("count", "rendered"), [(0, "0 processes"), (1, "1 process"), (2, "2 processes")]
    )
    def test_a_count_carries_its_noun(self, count: int, rendered: str) -> None:
        assert _counted(count, "process", "processes") == rendered


class TestTheCommandLine:
    """python -m nodrill is the whole surface, and it stays out of __all__."""

    def test_a_directory_nothing_recorded_is_an_error(self, tmp_path: Path, capsys: Any) -> None:
        assert _contract(str(tmp_path / "missing"), None, frozenset()) == 1
        assert "nothing recorded" in capsys.readouterr().err

    def test_a_directory_with_no_shards_says_the_recorder_never_armed(
        self, tmp_path: Path, capsys: Any
    ) -> None:
        assert _contract(str(tmp_path), None, frozenset()) == 1
        assert "nothing armed the recorder" in capsys.readouterr().err

    def test_the_contract_goes_to_stdout_by_default(self, tmp_path: Path, capsys: Any) -> None:
        _dump(str(tmp_path), "run", {("'a'", "requires", "x")})
        assert _contract(str(tmp_path), None, frozenset()) == 0
        captured = capsys.readouterr()
        assert captured.out == f"{HEADER}\n'a'{TAB}requires{TAB}x\n"
        assert "1 fact under 1 entry point" in captured.err

    def test_a_declared_key_nothing_opened_reaches_the_output(
        self, tmp_path: Path, capsys: Any
    ) -> None:
        _dump(str(tmp_path), "run", {("'a'", "requires", "x")})
        assert _contract(str(tmp_path), None, frozenset({"'b'"})) == 0
        assert "no block opened 'b'" in capsys.readouterr().err

    def test_write_names_the_file(self, tmp_path: Path, capsys: Any) -> None:
        _dump(str(tmp_path), "run", {("'a'", "requires", "x")})
        target = tmp_path / "nodrill.contract"
        assert main(["contract", "--from", str(tmp_path), "--write", str(target)]) == 0
        assert target.read_text(encoding="utf-8") == f"{HEADER}\n'a'{TAB}requires{TAB}x\n"
        assert capsys.readouterr().out == ""

    def test_a_file_it_cannot_write_is_a_message_and_not_a_traceback(
        self, tmp_path: Path, capsys: Any
    ) -> None:
        _dump(str(tmp_path), "run", {("'a'", "requires", "x")})
        target = tmp_path / "no" / "such" / "dir" / "out"
        assert _contract(str(tmp_path), str(target), frozenset()) == 1
        assert "cannot write" in capsys.readouterr().err

    def test_a_subcommand_is_required(self) -> None:
        with pytest.raises(SystemExit) as raised:
            main([])
        # argparse owns 2, which is why nothing recorded is 1.
        assert raised.value.code == 2

    def test_a_flag_cannot_be_abbreviated(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            main(["contract", "--fro", str(tmp_path)])


def _child(program: str, directory: Path, entries: str = "") -> subprocess.CompletedProcess[str]:
    """Run a program in a child interpreter with the recorder armed."""
    return subprocess.run(  # the interpreter running this suite, with a program written above
        [sys.executable, "-c", program],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(_ROOT),
        env={
            **os.environ,
            "NODRILL_CONTRACT": str(directory),
            "NODRILL_CONTRACT_ENTRY": entries,
            "PYTHONPATH": str(_ROOT),
        },
    )


class TestARecordedRun:
    """The end to end path, in a child interpreter, since the switch is read once at import."""

    def test_the_environment_variable_arms_a_whole_process(self, tmp_path: Path) -> None:
        program = f"from {APP} import running, serve_http\nwith running(): serve_http('ada')"
        _child(program, tmp_path)
        reads, _, _ = _merge(str(tmp_path))
        assert f"{APP}:Settings{TAB}requires{TAB}{APP}:User" in _render(reads)

    def test_declaring_the_boundaries_splits_the_entry_points(self, tmp_path: Path) -> None:
        _child(
            f"from {APP} import running, serve_http, run_job\n"
            "with running():\n    serve_http('ada')\n    run_job('grace')",
            tmp_path,
            entries="'http request','celery worker'",
        )
        reads, _, _ = _merge(str(tmp_path))
        assert _entries(reads) == {"'http request'", "'celery worker'"}

    def test_a_subprocess_the_run_spawns_joins_the_same_run(self, tmp_path: Path) -> None:
        program = (
            "import subprocess, sys\n"
            f"from {APP} import running, serve_http\n"
            "with running(): serve_http('ada')\n"
            "subprocess.run([sys.executable, '-c',"
            f" 'from {APP} import run_job; run_job(\"grace\")'], check=True)\n"
        )
        _child(program, tmp_path)
        reads, shards, stale = _merge(str(tmp_path))
        assert (shards, stale) == (2, 0)
        assert f"'celery worker'{TAB}set_default{TAB}{APP}:Origin" in _render(reads)

    def test_a_process_pool_worker_records_its_own_shard(self, tmp_path: Path) -> None:
        """A worker exits through os._exit, which runs finalizers and never atexit."""
        program = (
            "from concurrent.futures import ProcessPoolExecutor\n"
            f"from {APP} import run_job\n"
            "if __name__ == '__main__':\n"
            "    with ProcessPoolExecutor(max_workers=1) as pool:\n"
            "        pool.submit(run_job, 'grace').result()\n"
        )
        _child(program, tmp_path)
        reads, _, _ = _merge(str(tmp_path))
        assert f"'celery worker'{TAB}requires{TAB}{APP}:User" in _render(reads)

    def test_two_runs_of_the_same_program_agree_byte_for_byte(self, tmp_path: Path) -> None:
        program = f"from {APP} import running, serve_http\nwith running(): serve_http('ada')"
        first, second = tmp_path / "first", tmp_path / "second"
        _child(program, first)
        _child(program, second)
        assert _render(_merge(str(first))[0]) == _render(_merge(str(second))[0])

    def test_the_module_runs_as_a_command(self, tmp_path: Path) -> None:
        program = f"from {APP} import running, serve_http\nwith running(): serve_http('ada')"
        _child(program, tmp_path)
        result = subprocess.run(  # the interpreter running this suite
            [sys.executable, "-m", "nodrill", "contract", "--from", str(tmp_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout.startswith(f"{HEADER}\n")
        assert "A contract is only as complete as the run that recorded it." in result.stderr

    def test_the_dispatch_exits_with_what_the_command_returned(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """__main__ is the two lines that turn a return code into an exit code."""
        argv = ["nodrill", "contract", "--from", str(tmp_path / "missing")]
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as raised:
            runpy.run_module("nodrill", run_name="__main__")
        assert raised.value.code == 1
        assert "nothing recorded" in capsys.readouterr().err
