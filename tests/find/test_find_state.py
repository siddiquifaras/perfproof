"""tree_id, the tree check, and the saved state."""

import hashlib
import os
import subprocess
import tracemalloc
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


def test_tree_id_streams_a_large_diff_into_the_same_hash(
    repo: Path,
) -> None:
    blob = repo / "big.bin"
    blob.write_bytes(bytes(1000))
    git(repo, "add", "big.bin")
    git(repo, "commit", "-q", "-m", "blob")
    blob.write_bytes(os.urandom(24 << 20))
    diff = subprocess.run(
        ["git", "-C", str(repo), "diff", "HEAD", "--binary", "--", "."],
        capture_output=True,
        check=True,
    ).stdout
    expected = hashlib.sha256(diff).digest()
    del diff
    tracemalloc.start()
    try:
        streamed = gittree._git_sha256(  # pyright: ignore[reportPrivateUsage]
            repo, "diff", "HEAD", "--binary", "--", "."
        )
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert streamed == expected
    # Buffering would peak above the 24 MiB output; chunks are 1 MiB.
    assert peak < 8 << 20
