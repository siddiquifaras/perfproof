"""The task set is valid and its repos build, against real git."""

import subprocess
from pathlib import Path

import pytest

import runner
import taskset
from skill_eval import break_skill

TASKS = taskset.load().tasks


def test_task_ids_are_unique_and_cover_the_brief() -> None:
    ids = [t.id for t in TASKS]
    base = [t for t in TASKS if "-msg-" not in t.id]
    assert len(ids) == len(set(ids))
    kinds = {k: sum(t.kind == k for t in base) for k in ("prove", "find")}
    assert kinds == {"prove": 6, "find": 2}
    triggers = [t for t in TASKS if t.kind == "trigger"]
    assert sum(t.expect.trigger for t in triggers) == 8
    assert sum(not t.expect.trigger for t in triggers) == 4


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_every_branch_names_a_real_candidate(task: taskset.Task) -> None:
    assert task.example is not None
    for branch in task.branches:
        root = runner.EXAMPLES / task.example / "candidates"
        assert (root / branch.candidate).is_dir()
    for path in task.locked:
        assert (runner.EXAMPLES / task.example / path).is_file()
    if task.expect.hotspot is not None:
        hotspot = runner.EXAMPLES / task.example / task.expect.hotspot.file
        assert task.expect.hotspot.function in hotspot.read_text()


def test_decoys_expect_their_documented_refusal() -> None:
    by_id = {t.id: t.expect for t in TASKS}
    wrong = by_id["prove-rolling-wrong-output"]
    assert (wrong.outcomes, wrong.reason, wrong.gate) == (
        ["REJECT"],
        "correctness",
        "correctness",
    )
    bench = by_id["prove-membership-bench-edit"]
    assert (bench.outcomes, bench.reason, bench.gate) == (
        ["REJECT"],
        "integrity",
        "lock",
    )
    assert "KEEP" not in wrong.outcomes + bench.outcomes


def test_build_repo_seeds_branches_and_installs_the_skill(
    tmp_path: Path,
) -> None:
    task = next(t for t in TASKS if t.id == "prove-rolling-fix")
    repo = tmp_path / "repo"
    tips = runner.build_repo(task, repo, runner.SKILL_SOURCE)
    assert set(tips) == {"main", "agent-speedup"}
    assert not list(repo.glob("spec*.json"))
    assert not (repo / "candidates").exists()
    assert (repo / ".claude" / "skills" / "perfproof" / "SKILL.md").is_file()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert status.stdout == ""
    changed = subprocess.run(
        ["git", "diff", "--name-only", "main", "agent-speedup"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert changed.stdout.split() == ["rolling.py"]


def test_broken_skill_inverts_only_the_direction_rule(
    tmp_path: Path,
) -> None:
    dest = tmp_path / "broken"
    break_skill(dest)
    original = (runner.SKILL_SOURCE / "SKILL.md").read_text()
    broken = (dest / "SKILL.md").read_text()
    assert broken != original
    assert "Say plainly whether the estimate shows" in broken
    assert "Never call an estimate faster" not in broken
    assert runner.skill_hash(dest) != runner.skill_hash(runner.SKILL_SOURCE)


@pytest.mark.parametrize(
    "task", [t for t in TASKS if t.branches], ids=lambda t: t.id
)
def test_each_branch_differs_from_main_by_exactly_its_candidate(
    task: taskset.Task, tmp_path: Path
) -> None:
    assert task.example is not None
    repo = tmp_path / "repo"
    runner.build_repo(task, repo, runner.SKILL_SOURCE)
    for branch in task.branches:
        diff = subprocess.run(
            ["git", "diff", "--name-only", "main", branch.name],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        root = runner.EXAMPLES / task.example / "candidates"
        expected = sorted(
            p.relative_to(root / branch.candidate).as_posix()
            for p in (root / branch.candidate).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        )
        assert diff.stdout.split() == expected
