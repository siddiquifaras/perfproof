# pyright: reportPrivateUsage=false
"""A failed agent start is a harness failure; the final-tree snapshot.

Both came out of the first skill-vs-baseline run: every baseline task
ran for seconds at no cost because ``sandbox-exec`` could not find
``claude`` on the filtered ``PATH`` (exit 71), and three skill tasks had
no speed check because ``git add`` refused the ``:!.perfproof``
pathspec in a repository that ignores ``.perfproof``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import arms
import compare
import runner
import speedcheck
from runner import Meta
from taskset import Task, TaskSet
from test_arms import HOTSPOT_TASK, _git, _write_run

# --- a call that never started is a harness failure -------------------


def _failed_start(run_dir: Path, task: Task, stderr: str) -> None:
    """Write a task directory as left when claude never ran."""
    task_dir = _write_run(run_dir, task, "baseline", "vague", [], "")
    (task_dir / "turn-1.jsonl").write_text("")
    meta = Meta.model_validate_json((task_dir / "meta.json").read_text())
    turn = meta.turns[0].model_copy(
        update={"exit_code": 71, "stderr_tail": stderr}
    )
    (task_dir / "meta.json").write_text(
        meta.model_copy(update={"turns": [turn]}).model_dump_json()
    )


def test_a_task_with_no_agent_output_is_a_harness_failure(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _failed_start(
        run_dir,
        HOTSPOT_TASK,
        "sandbox-exec: execvp() of 'claude' failed: No such file\n",
    )
    score = arms.score_arm(HOTSPOT_TASK, run_dir / HOTSPOT_TASK.id)
    assert "agent produced no output (exit 71)" in score.harness_failure
    assert "execvp() of 'claude' failed" in score.harness_failure
    assert score.found is None
    assert score.correct is None
    assert score.claims == []


def test_harness_failures_stay_out_of_the_totals(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _failed_start(run_dir, HOTSPOT_TASK, "boom")
    tasks = TaskSet.model_construct(version=1, model="m", tasks=[HOTSPOT_TASK])
    found = compare.load([run_dir], tasks)
    text = compare.render(found, tasks)
    assert "HARNESS FAILURE" in text
    summed = compare.totals(list(found[("vague", "baseline")].values()))
    assert summed.tasks == 0
    assert summed.harness_failures == 1
    assert summed.unbacked == 0


def test_a_call_records_its_stderr_and_runs_the_executable_given(
    tmp_path: Path,
) -> None:
    meta, _ = runner.run_turn(
        ["claude", "-p", "p"],
        tmp_path,
        {"PATH": "/usr/bin:/bin"},
        tmp_path / "out.jsonl",
        tmp_path / "err",
        30.0,
        executable="/bin/sh",
    )
    # sh reads "-p" as an option; whatever it says is kept, not lost.
    assert meta.argv == ["claude", "-p", "p"]
    assert meta.exit_code != 0
    assert meta.stderr_tail == (tmp_path / "err").read_text()
    assert meta.stderr_tail.strip()


def test_stderr_kept_in_meta_is_redacted_and_bounded(tmp_path: Path) -> None:
    code = (
        "import sys;sys.stderr.write('x'*5000+' sk-secret-value');sys.exit(3)"
    )
    meta, _ = runner.run_turn(
        [sys.executable, "-c", code],
        tmp_path,
        dict(os.environ),
        tmp_path / "out.jsonl",
        tmp_path / "err",
        30.0,
        redact=["sk-secret-value"],
    )
    assert meta.exit_code == 3
    assert "sk-secret-value" not in meta.stderr_tail
    assert len(meta.stderr_tail) <= runner.STDERR_TAIL_CHARS


# --- the final-tree snapshot ---


def _snapshot_repo(tmp_path: Path, ignore: bool) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("x = 1\n")
    if ignore:
        (repo / ".gitignore").write_text(".perfproof/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / "a.py").write_text("x = 2\n")
    (repo / "new.py").write_text("y = 1\n")
    (repo / ".perfproof" / "runs").mkdir(parents=True)
    (repo / ".perfproof" / "runs" / "r.json").write_text("{}")
    return repo


@pytest.mark.parametrize("ignored", [True, False])
def test_snapshot_works_with_perfproof_ignored_or_not(
    tmp_path: Path, ignored: bool
) -> None:
    repo = _snapshot_repo(tmp_path, ignored)
    speedcheck.snapshot_working_tree(repo, lambda *a: _git(repo, *a))
    files = _git(repo, "ls-tree", "-r", "--name-only", "HEAD").split()
    assert "a.py" in files
    assert "new.py" in files
    assert not [f for f in files if f.startswith(".perfproof")]
    assert _git(repo, "show", "HEAD:a.py") == "x = 2\n"
    # Twice is harmless: the exclude entry is not duplicated.
    speedcheck.snapshot_working_tree(repo, lambda *a: _git(repo, *a))
    exclude = (repo / ".git" / "info" / "exclude").read_text().split()
    assert exclude.count(".perfproof/") == 1


def test_snapshot_drops_a_change_to_a_tracked_perfproof_file(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / ".perfproof").mkdir()
    (repo / ".perfproof" / "kept.txt").write_text("one\n")
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / ".perfproof" / "kept.txt").write_text("two\n")
    (repo / "a.py").write_text("x = 2\n")
    speedcheck.snapshot_working_tree(repo, lambda *a: _git(repo, *a))
    assert _git(repo, "show", "HEAD:.perfproof/kept.txt") == "one\n"
    assert _git(repo, "show", "HEAD:a.py") == "x = 2\n"


# --- code spans are not claims ---


@pytest.mark.parametrize(
    "sentence",
    [
        "I created no `perfproof/speedups` branch, and `git status` is clean.",
        "The tree is back on `main`, and I deleted the attempt branch "
        "`perfproof/speedups`.",
    ],
)
def test_claim_words_in_code_spans_are_not_claims(sentence: str) -> None:
    assert arms.extract_claims(f"Done. {sentence}") == []


def test_claim_words_outside_a_code_span_still_count() -> None:
    sentence = "It is now `2x` faster."
    assert arms.extract_claims(f"Done. {sentence}") == [sentence]


def test_a_harness_failure_leaves_the_task_out_of_both_arms(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skill"
    base_dir = tmp_path / "baseline"
    _write_run(skill_dir, HOTSPOT_TASK, "skill", "vague", [], "ok")
    _failed_start(base_dir, HOTSPOT_TASK, "boom")
    tasks = TaskSet.model_construct(version=1, model="m", tasks=[HOTSPOT_TASK])
    found = compare.load([skill_dir, base_dir], tasks)
    skill_scores = list(found[("vague", "skill")].values())
    base_scores = list(found[("vague", "baseline")].values())
    bad = frozenset({HOTSPOT_TASK.id})
    assert compare.totals(skill_scores).tasks == 1
    assert compare.totals(skill_scores, bad).tasks == 0
    assert compare.totals(skill_scores, bad).excluded == (HOTSPOT_TASK.id,)
    assert compare.totals(base_scores, bad).tasks == 0
    text = compare.render(found, tasks)
    totals = text.split("## Totals")[1]
    assert "| vague | skill | 0 (1 excluded) |" in totals
    assert "| vague | baseline | 0 (1 excluded) |" in totals
    assert f"harness failure in either arm): {HOTSPOT_TASK.id}" in totals


def test_a_claim_inside_a_code_span_with_spaces_still_counts() -> None:
    sentence = "I made it `3.2x faster`."
    assert arms.extract_claims(f"Done. {sentence}") == [sentence]
