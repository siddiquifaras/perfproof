"""The mixed-stack tasks name built manifests and honest prompts.

Reads ``tasks-mixed.json`` as plain JSON, so it does not depend on the
task loader. Each task is a hidden fix in a public repository that mixes
languages in the path users run. The detailed prompt describes the
symptom from the user's side and never names the cause.
"""

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, cast

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TASKS = HERE / "tasks-mixed.json"
CHECKS = ROOT / "eval" / "mine" / "checks.json"
MANIFESTS = ROOT / "eval" / "tasks"
VAGUE_BASELINES = {
    "make the app faster",
    "fix the slowness",
    "speed up the app",
    "it's slow, fix it",
    "make this faster",
}
LANGUAGES = {
    ".py": "Python",
    ".rs": "Rust",
    ".c": "C/C++",
    ".cc": "C/C++",
    ".cpp": "C/C++",
    ".h": "C/C++",
    ".js": "JavaScript/TypeScript",
}


def load(path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(path.read_text("utf-8")))


def manifest(identifier: str) -> dict[str, Any]:
    found = sorted(MANIFESTS.glob(f"*/{identifier}.json"))
    assert len(found) == 1, identifier
    return load(found[0])


TASK_LIST = cast("list[dict[str, Any]]", load(TASKS)["tasks"])


def test_there_are_five_tasks_with_unique_ids_and_repositories() -> None:
    assert len(TASK_LIST) == 5
    assert len({t["id"] for t in TASK_LIST}) == 5
    repos = [manifest(t["repo"]["manifest"])["repository"] for t in TASK_LIST]
    assert len(set(repos)) == 5


@pytest.mark.parametrize("task", TASK_LIST, ids=lambda t: t["id"])
def test_a_task_names_a_built_development_manifest(
    task: dict[str, Any],
) -> None:
    identifier = task["repo"]["manifest"]
    data = manifest(identifier)
    assert data["split"] == "development-candidate"
    assert data["status"] == "runs"
    assert data["workload"]["verified"] is True
    assert data["oracle"]["verified"] is True
    steps = {(run["step"], run["tree"]) for run in data["runs"]}
    assert {("workload", "task"), ("oracle", "task")} <= steps
    assert all(
        run["exit_code"] == 0
        for run in data["runs"]
        if run["step"] != "workload" or run["tree"] == "task"
    )
    assert identifier in load(CHECKS)


@pytest.mark.parametrize("task", TASK_LIST, ids=lambda t: t["id"])
def test_a_task_scores_by_the_hidden_fix_rule(task: dict[str, Any]) -> None:
    assert task["kind"] == "find"
    hot = task["expect"]["hotspot"]
    assert hot["top_n"] == "skill"
    assert hot["file"]
    assert hot["function"]
    assert task["expect"]["fix"] == "optional_branch"
    assert task["expect"]["outcomes"] == []
    assert task["budget_usd"] == 3.0
    assert task["task_budget_usd"] == 3.0
    assert task["timeout_s"] == 2400


def test_no_language_has_more_than_two_tasks() -> None:
    counts: Counter[str] = Counter()
    for task in TASK_LIST:
        files = manifest(task["repo"]["manifest"])["reference_change"]["files"]
        suffixes = {Path(f).suffix for f in files}
        hot = Path(task["expect"]["hotspot"]["file"]).suffix
        assert hot in suffixes
        counts[LANGUAGES[hot]] += 1
    assert max(counts.values()) <= 2


@pytest.mark.parametrize("task", TASK_LIST, ids=lambda t: t["id"])
def test_the_prompts_follow_the_two_levels(task: dict[str, Any]) -> None:
    prompts = task["prompts"]
    assert prompts["vague"]["skill"] == "/perfproof"
    assert task["prompt"] == prompts["vague"]["skill"]
    assert prompts["vague"]["baseline"] in VAGUE_BASELINES
    detailed = prompts["detailed"]
    assert detailed["skill"] == "/perfproof " + detailed["baseline"]


def test_the_vague_baselines_are_all_used() -> None:
    used = {t["prompts"]["vague"]["baseline"] for t in TASK_LIST}
    assert used == VAGUE_BASELINES


@pytest.mark.parametrize("task", TASK_LIST, ids=lambda t: t["id"])
def test_the_detailed_text_describes_a_symptom_and_names_no_cause(
    task: dict[str, Any],
) -> None:
    text = task["prompts"]["detailed"]["baseline"]
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if s]
    assert 2 <= len(sentences) <= 4
    hot = task["expect"]["hotspot"]
    names = [hot["function"], *(a["function"] for a in hot.get("accept", []))]
    for name in names:
        for part in {name, re.split(r"[.:#]+", name)[-1]}:
            assert part.lower() not in text.lower(), (task["id"], part)
    assert Path(hot["file"]).name.lower() not in text.lower()


@pytest.mark.parametrize("task", TASK_LIST, ids=lambda t: t["id"])
def test_the_speed_check_runs_the_manifests_workload(
    task: dict[str, Any],
) -> None:
    check = task["speed_check"]
    assert check["metric"] == "wall"
    spec = load(CHECKS)[task["repo"]["manifest"]]
    assert check["command"] == spec["workload"]
