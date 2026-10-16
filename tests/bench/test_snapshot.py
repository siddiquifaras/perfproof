import subprocess
from pathlib import Path

import pytest

from perfproof.bench.snapshot import (
    SnapshotError,
    add_baseline_worktree,
    exclude_state_dir,
    resolve_commit,
    verify_baseline_worktree,
    worktree_commit,
)


@pytest.fixture(autouse=True)
def isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "gitconfig"
    config.write_text(
        "[user]\n\tname = Test\n\temail = test@example.invalid\n"
        "[commit]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    (root / "bench.py").write_text("print('v1')\n")
    git(root, "add", "bench.py")
    git(root, "commit", "-q", "-m", "first")
    (root / "bench.py").write_text("print('v2')\n")
    git(root, "commit", "-q", "-am", "second")
    return root


def test_baseline_worktree_matches_the_snapshot_commit(repo: Path) -> None:
    first = resolve_commit(repo, "HEAD~1")

    path = add_baseline_worktree(repo, first, "baseline")

    assert path == repo / ".perfproof" / "worktrees" / "baseline"
    assert worktree_commit(path) == first
    assert (path / "bench.py").read_text() == git(
        repo, "show", f"{first}:bench.py"
    ) + "\n"
    assert git(path, "status", "--porcelain") == ""
    verify_baseline_worktree(path, first)
    assert worktree_commit(repo) == resolve_commit(repo, "HEAD")


def test_baseline_worktree_is_detached(repo: Path) -> None:
    path = add_baseline_worktree(repo, resolve_commit(repo, "HEAD"), "b")

    assert git(path, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    assert git(repo, "branch", "--list") == "* main"


def test_resolve_commit_gives_the_full_sha(repo: Path) -> None:
    full = git(repo, "rev-parse", "HEAD")

    assert resolve_commit(repo, "HEAD") == full
    assert resolve_commit(repo, full[:10]) == full
    assert resolve_commit(repo, "main") == full


@pytest.mark.parametrize("rev", ["no-such-ref", "--all", ""])
def test_unknown_revisions_are_refused(repo: Path, rev: str) -> None:
    with pytest.raises(SnapshotError):
        resolve_commit(repo, rev)


def test_state_dir_is_excluded_once(repo: Path) -> None:
    (repo / ".perfproof").mkdir()
    (repo / ".perfproof" / "x.json").write_text("{}")

    exclude = exclude_state_dir(repo)
    exclude_state_dir(repo)

    assert exclude == Path(git(repo, "rev-parse", "--absolute-git-dir")) / (
        "info/exclude"
    )
    lines = exclude.read_text().splitlines()
    assert lines.count(".perfproof/") == 1
    assert git(repo, "status", "--porcelain", "--untracked-files=all") == ""


def test_exclude_from_a_subdirectory_edits_the_repo_file(
    repo: Path,
) -> None:
    sub = repo / "sub" / "deeper"
    sub.mkdir(parents=True)

    exclude = exclude_state_dir(sub)

    assert exclude == Path(git(repo, "rev-parse", "--absolute-git-dir")) / (
        "info/exclude"
    )
    assert ".perfproof/" in exclude.read_text().splitlines()
    assert not (sub / ".git").exists()


def test_exclude_keeps_existing_lines(repo: Path) -> None:
    exclude = Path(git(repo, "rev-parse", "--absolute-git-dir")) / (
        "info/exclude"
    )
    exclude.write_text("*.log")

    exclude_state_dir(repo)

    assert exclude.read_text() == "*.log\n.perfproof/\n"


def test_adding_a_worktree_excludes_state_dir(repo: Path) -> None:
    add_baseline_worktree(repo, resolve_commit(repo, "HEAD"), "baseline")

    assert git(repo, "status", "--porcelain", "--untracked-files=all") == ""


def test_exclude_from_a_linked_worktree_uses_the_common_dir(
    repo: Path,
) -> None:
    linked = add_baseline_worktree(repo, resolve_commit(repo, "HEAD"), "w")
    common = Path(git(repo, "rev-parse", "--absolute-git-dir"))

    assert exclude_state_dir(linked) == common / "info" / "exclude"


def test_existing_matching_worktree_is_reused(repo: Path) -> None:
    head = resolve_commit(repo, "HEAD")
    path = add_baseline_worktree(repo, head, "baseline")

    assert add_baseline_worktree(repo, head, "baseline") == path


def test_existing_worktree_at_another_commit_is_refused(repo: Path) -> None:
    add_baseline_worktree(repo, resolve_commit(repo, "HEAD"), "baseline")

    with pytest.raises(SnapshotError, match="is at"):
        add_baseline_worktree(repo, resolve_commit(repo, "HEAD~1"), "baseline")


def test_existing_plain_directory_is_refused(repo: Path) -> None:
    target = repo / ".perfproof" / "worktrees" / "baseline"
    target.mkdir(parents=True)

    with pytest.raises(SnapshotError, match="not a worktree"):
        add_baseline_worktree(repo, resolve_commit(repo, "HEAD"), "baseline")


def test_edited_baseline_worktree_fails_verification(repo: Path) -> None:
    head = resolve_commit(repo, "HEAD")
    path = add_baseline_worktree(repo, head, "baseline")
    (path / "bench.py").write_text("print('edited')\n")

    with pytest.raises(SnapshotError, match="changes"):
        verify_baseline_worktree(path, head)
    with pytest.raises(SnapshotError, match="changes"):
        add_baseline_worktree(repo, head, "baseline")


def test_moved_baseline_worktree_fails_verification(repo: Path) -> None:
    head = resolve_commit(repo, "HEAD")
    path = add_baseline_worktree(repo, head, "baseline")
    git(path, "checkout", "-q", "--detach", "HEAD~1")

    with pytest.raises(SnapshotError, match="is at"):
        verify_baseline_worktree(path, head)


def test_untracked_byproducts_do_not_fail_verification(repo: Path) -> None:
    head = resolve_commit(repo, "HEAD")
    path = add_baseline_worktree(repo, head, "baseline")
    (path / "__pycache__").mkdir()
    (path / "__pycache__" / "bench.cpython-312.pyc").write_bytes(b"\0")

    verify_baseline_worktree(path, head)


@pytest.mark.parametrize("commit", ["HEAD", "abc123", "A" * 40])
def test_only_full_commit_shas_are_checked_out(
    repo: Path, commit: str
) -> None:
    with pytest.raises(ValueError, match="full commit SHA"):
        add_baseline_worktree(repo, commit, "baseline")


@pytest.mark.parametrize("name", ["../x", "a/b", ".hidden", ""])
def test_unsafe_worktree_names_are_refused(repo: Path, name: str) -> None:
    with pytest.raises(ValueError, match="worktree name"):
        add_baseline_worktree(repo, resolve_commit(repo, "HEAD"), name)


def test_commit_absent_from_the_repo_is_refused(repo: Path) -> None:
    with pytest.raises(SnapshotError):
        add_baseline_worktree(repo, "0" * 40, "baseline")


def test_not_a_repository_is_refused(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    with pytest.raises(SnapshotError):
        resolve_commit(plain, "HEAD")
