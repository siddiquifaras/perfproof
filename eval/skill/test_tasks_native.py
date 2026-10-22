"""The C and C++ task file names built manifests of the three kinds."""

import json
from pathlib import Path
from typing import cast

import pytest

import realrepo
import taskset

NATIVE = taskset.load(Path(__file__).parent / "tasks-native.json")


def manifest(manifest_id: str) -> dict[str, object]:
    found = sorted(realrepo.TASKS_DIR.glob(f"*/{manifest_id}.json"))
    assert len(found) == 1, manifest_id
    loaded: dict[str, object] = json.loads(found[0].read_text())
    return loaded


def test_the_three_kinds_are_there() -> None:
    kinds = {
        t.id: (t.kind, t.expect.hotspot is not None) for t in NATIVE.tasks
    }
    assert kinds == {
        "native-simdjson-hidden-fix": ("find", True),
        "native-lz4-known-change": ("prove", False),
        "native-zstd-nothing-to-keep": ("find", False),
    }
    families = {
        t.id: manifest(t.repo.manifest)["family"]
        for t in NATIVE.tasks
        if t.repo is not None
    }
    assert families == {
        "native-simdjson-hidden-fix": "hidden-fix",
        "native-lz4-known-change": "known-change",
        "native-zstd-nothing-to-keep": "nothing-to-keep",
    }


@pytest.mark.parametrize("task", NATIVE.tasks, ids=lambda t: t.id)
def test_each_task_names_a_manifest_that_built_and_ran(
    task: taskset.Task,
) -> None:
    assert task.repo is not None
    found = manifest(task.repo.manifest)
    assert found["status"] == "runs"
    assert found["target_class"] == "native"
    for key in ("workload", "oracle"):
        entry = cast("dict[str, object]", found[key])
        assert entry["verified"] is True
    runs = cast("list[dict[str, object]]", found["runs"])
    steps = {(r["step"], r["tree"]): r["exit_code"] for r in runs}
    for tree in ("task", "reference"):
        assert steps[("workload", tree)] == 0
        assert steps[("oracle", tree)] == 0


def test_the_prove_task_names_a_branch_cut_at_the_reference() -> None:
    task = next(t for t in NATIVE.tasks if t.kind == "prove")
    assert task.repo is not None
    assert task.repo.branch == "pr-1040"
    assert "pr-1040" in task.prompt
