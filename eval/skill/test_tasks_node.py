"""The Node tasks name built, development-split manifests.

Reads ``tasks-node.json`` as plain JSON, so it does not depend on the
real-repository loader. A task whose manifest is a held-out one would
let development see the held-out set (``docs/evaluation.md``), and one
that was never built here would fail at setup.
"""

import json
from pathlib import Path
from typing import Any, cast

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TASKS = HERE / "tasks-node.json"
CHECKS = ROOT / "eval" / "mine" / "checks.json"
MANIFESTS = ROOT / "eval" / "tasks"


def load(path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(path.read_text("utf-8")))


def manifest(identifier: str) -> dict[str, Any]:
    found = sorted(MANIFESTS.glob(f"*/{identifier}.json"))
    assert len(found) == 1, identifier
    return load(found[0])


TASK_LIST = cast("list[dict[str, Any]]", load(TASKS)["tasks"])


def test_the_set_covers_find_prove_and_nothing_to_keep() -> None:
    by_id = {
        t["id"]: (t["kind"], manifest(t["repo"]["manifest"])["family"])
        for t in TASK_LIST
    }
    assert sorted(by_id.values()) == [
        ("find", "known-change"),
        ("find", "nothing-to-keep"),
        ("prove", "known-change"),
    ]


@pytest.mark.parametrize("task", TASK_LIST, ids=lambda t: t["id"])
def test_a_task_names_a_built_development_manifest(
    task: dict[str, Any],
) -> None:
    identifier = task["repo"]["manifest"]
    data = manifest(identifier)
    assert data["split"] != "held-out"
    assert data["status"] == "builds"
    assert data["workload"]["verified"] is False
    assert data["oracle"]["verified"] is True
    steps = {(run["step"], run["tree"]) for run in data["runs"]}
    assert {("workload", "task"), ("oracle", "task")} <= steps
    assert all(
        run["exit_code"] == 0
        for run in data["runs"]
        if run["step"] in ("setup", "workload") and run["tree"] == "task"
    )
    assert identifier in load(CHECKS)


@pytest.mark.parametrize("task", TASK_LIST, ids=lambda t: t["id"])
def test_a_prove_task_names_its_branch(task: dict[str, Any]) -> None:
    has_branch = task["repo"].get("branch") is not None
    assert has_branch == (task["kind"] == "prove")
    if has_branch:
        assert task["repo"]["branch"] in task["prompt"]
