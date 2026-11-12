"""tree_id, the tree check, and the saved state."""

import subprocess
import sys
from pathlib import Path

import pytest
from find_helpers import ZERO, git, init_repo, make_part, record

from perfproof.commands import CommandError
from perfproof.find import gittree, state
from perfproof.schema import FindState


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / ".gitignore").write_text("ignored/\n")
    return init_repo(tmp_path)


def test_tree_id_is_stable_and_follows_content(repo: Path) -> None:
    first = gittree.tree_id(repo)
    assert gittree.tree_id(repo) == first
    (repo / "a.py").write_text("x = 2\n")
    edited = gittree.tree_id(repo)
    assert edited != first
    (repo / "new.py").write_text("y = 1\n")
    added = gittree.tree_id(repo)
    assert added not in (first, edited)
    (repo / "new.py").write_text("y = 2\n")
    assert gittree.tree_id(repo) != added


def test_tree_id_ignores_perfproof_and_ignored_files(repo: Path) -> None:
    first = gittree.tree_id(repo)
    (repo / ".perfproof").mkdir()
    (repo / ".perfproof" / "x.json").write_text("{}")
    (repo / "ignored").mkdir()
    (repo / "ignored" / "o").write_text("1")
    assert gittree.tree_id(repo) == first


def test_tree_id_changes_with_head(repo: Path) -> None:
    first = gittree.tree_id(repo)
    git(repo, "commit", "-q", "--allow-empty", "-m", "again")
    assert gittree.tree_id(repo) != first


def test_status_entries_and_added(repo: Path) -> None:
    (repo / "a.py").write_text("x = 3\n")
    before = gittree.status_entries(repo)
    assert before == {" M a.py"}
    (repo / "b.py").write_text("")
    (repo / "ignored").mkdir()
    (repo / "ignored" / "o").write_text("1")
    (repo / ".perfproof").mkdir()
    (repo / ".perfproof" / "p").write_text("1")
    after = gittree.status_entries(repo)
    assert gittree.added_entries(before, after) == ("b.py",)


def test_a_line_present_before_the_probe_is_not_the_probes(
    repo: Path,
) -> None:
    (repo / "a.py").write_text("x = 3\n")
    before = gittree.status_entries(repo)
    assert gittree.added_entries(before, gittree.status_entries(repo)) == ()


def test_a_deleted_tracked_file_counts(repo: Path) -> None:
    before = gittree.status_entries(repo)
    (repo / "a.py").unlink()
    after = gittree.status_entries(repo)
    assert gittree.added_entries(before, after) == ("a.py",)


def test_untracked_files_are_listed_one_by_one(repo: Path) -> None:
    (repo / "d").mkdir()
    (repo / "d" / "f1").write_text("")
    (repo / "d" / "f2").write_text("")
    assert gittree.status_entries(repo) == {"?? d/f1", "?? d/f2"}


def test_current_record_needs_spec_and_tree() -> None:
    s = state.fresh_state(ZERO)
    s = state.with_probe(s, record("p", "done", tree="t1"))
    assert state.current_record(s, "p", ZERO, "t1") is not None
    assert state.current_record(s, "p", ZERO, "t2") is None
    assert state.current_record(s, "p", "1" * 64, "t1") is None
    assert state.current_record(s, "q", ZERO, "t1") is None


@pytest.mark.parametrize("status", ["failed", "timeout"])
def test_a_failed_probe_is_not_rerun_on_the_same_tree(status: str) -> None:
    s = state.with_probe(state.fresh_state(ZERO), record("p", status))
    assert state.current_record(s, "p", ZERO, "t1") is not None
    assert state.current_record(s, "p", ZERO, "t2") is None


def test_a_tree_modifying_probe_is_never_current_again_by_tree() -> None:
    s = state.with_probe(
        state.fresh_state(ZERO), record("p", "tree_modified", tree="t1")
    )
    assert state.current_record(s, "p", ZERO, "other") is not None
    assert state.current_record(s, "p", "1" * 64, "t1") is None


@pytest.mark.parametrize("status", ["skipped_unconfirmed", "skipped_no_tests"])
def test_a_skipped_record_is_decided_again(status: str) -> None:
    s = state.with_probe(state.fresh_state(ZERO), record("p", status))
    assert state.current_record(s, "p", ZERO, "t1") is None


def test_with_probe_replaces_by_key() -> None:
    s = state.fresh_state(ZERO)
    s = state.with_probe(s, record("p", "failed"))
    s = state.with_probe(s, record("q", "done"))
    s = state.with_probe(s, record("p", "done"))
    assert [(p.key, p.status) for p in s.probes] == [
        ("q", "done"),
        ("p", "done"),
    ]


def test_reason_code() -> None:
    assert state.reason_code(record("p", reason="timeout: x")) == "timeout"
    assert state.reason_code(record("p", reason="no_tests")) == "no_tests"
    assert state.reason_code(record("p")) == ""


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    assert state.load_state(tmp_path) is None
    s = state.evolve(
        state.fresh_state(ZERO),
        confirmed_tests={"root": ("python", "-m", "pytest")},
        waivers={"define_workloads": "one kind"},
        size_knobs={"w": {"kind": "arg", "name": "--n", "base": 5}},
    )
    state.save_state(tmp_path, s)
    loaded = state.load_state(tmp_path)
    assert isinstance(loaded, FindState)
    assert loaded == s
    assert not list(state.state_path(tmp_path).parent.glob(".*.tmp"))


def test_an_unreadable_state_is_refused_not_replaced(tmp_path: Path) -> None:
    path = state.state_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    with pytest.raises(CommandError) as raised:
        state.load_state(tmp_path)
    assert raised.value.code == "state_read_failed"
    assert path.read_text() == "{not json"


def test_hash_parts_follows_the_parts() -> None:
    a = make_part("a")
    assert state.hash_parts([a]) == state.hash_parts([make_part("a")])
    assert state.hash_parts([a]) != state.hash_parts([make_part("b")])


def test_a_tool_blocked_failure_is_current_only_while_tool_is_missing() -> (
    None
):
    for reason in (
        "failed: profiler unavailable: py-spy not found",
        "recorder_unavailable: perf_event_open refused",
    ):
        s = state.with_probe(
            state.fresh_state(ZERO), record("p", "failed", reason=reason)
        )
        assert state.current_record(s, "p", ZERO, "t1") is not None
        assert (
            state.current_record(s, "p", ZERO, "t1", tool_present=True) is None
        )


def test_other_failures_stay_current_when_the_tool_is_present() -> None:
    s = state.with_probe(
        state.fresh_state(ZERO),
        record("p", "failed", reason="failed: oom: killed"),
    )
    assert state.current_record(s, "p", ZERO, "t1", tool_present=True)
    s = state.with_probe(
        state.fresh_state(ZERO), record("p", "timeout", reason="timeout: x")
    )
    assert state.current_record(s, "p", ZERO, "t1", tool_present=True)


_WRITER = """
import sys
from pathlib import Path
from perfproof.find import state

root, n, locked = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3] == "1"
tag = sys.argv[4]
for i in range(n):
    if locked:
        with state.state_lock(root, 30.0):
            s = state.load_state(root) or state.fresh_state("0" * 64)
            call = {
                "started_at": "2026-01-01T00:00:00+00:00",
                "spent_seconds": float(i + 100 * (tag == "b")),
            }
            calls = [*s.calls, call]
            state.save_state(root, state.evolve(s, calls=calls))
    else:
        state.save_state(root, state.fresh_state("0" * 64))
"""


def _spawn(root: Path, locked: bool, tag: str) -> "subprocess.Popen[str]":
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            _WRITER,
            str(root),
            "25",
            str(int(locked)),
            tag,
        ],
        text=True,
        stderr=subprocess.PIPE,
    )


def test_two_processes_updating_under_the_lock_lose_nothing(
    tmp_path: Path,
) -> None:
    procs = [_spawn(tmp_path, True, "a"), _spawn(tmp_path, True, "b")]
    errors = [p.communicate(timeout=120)[1] for p in procs]
    assert [p.returncode for p in procs] == [0, 0], errors
    loaded = state.load_state(tmp_path)
    assert loaded is not None
    spent = sorted(c.spent_seconds for c in loaded.calls)
    assert spent == sorted([*range(25), *range(100, 125)])


def test_concurrent_saves_share_no_temporary_file(tmp_path: Path) -> None:
    procs = [_spawn(tmp_path, False, "a"), _spawn(tmp_path, False, "b")]
    errors = [p.communicate(timeout=120)[1] for p in procs]
    assert [p.returncode for p in procs] == [0, 0], errors
    assert state.load_state(tmp_path) is not None
    leftovers = [
        p.name
        for p in state.state_path(tmp_path).parent.iterdir()
        if p.name.endswith(".tmp")
    ]
    assert leftovers == []


def test_a_second_process_gets_find_busy_while_the_lock_is_held(
    tmp_path: Path,
) -> None:
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time\n"
            "from pathlib import Path\n"
            "from perfproof.find import state\n"
            "with state.state_lock(Path(sys.argv[1])):\n"
            "    print('held', flush=True)\n"
            "    time.sleep(30)\n",
            str(tmp_path),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        with (
            pytest.raises(CommandError) as raised,
            state.state_lock(tmp_path, 0.2),
        ):
            pass
        assert raised.value.code == "find_busy"
    finally:
        holder.kill()
        assert holder.stdout is not None
        holder.stdout.close()
        holder.wait()
    with state.state_lock(tmp_path, 0.2):
        pass
