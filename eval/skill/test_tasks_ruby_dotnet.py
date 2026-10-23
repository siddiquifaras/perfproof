"""The Ruby and .NET task files name built manifests of three kinds."""

import json
from pathlib import Path
from typing import cast

import pytest

import realrepo
import taskset

HERE = Path(__file__).parent
FILES = {
    "ruby": taskset.load(HERE / "tasks-ruby.json"),
    "dotnet": taskset.load(HERE / "tasks-dotnet.json"),
}
ALL = [(lang, t) for lang, loaded in FILES.items() for t in loaded.tasks]


def manifest(manifest_id: str) -> dict[str, object]:
    found = sorted(realrepo.TASKS_DIR.glob(f"*/{manifest_id}.json"))
    assert len(found) == 1, manifest_id
    loaded: dict[str, object] = json.loads(found[0].read_text())
    return loaded


def test_each_language_has_a_find_a_prove_and_a_nothing_to_keep() -> None:
    for lang, loaded in FILES.items():
        kinds = sorted(t.kind for t in loaded.tasks)
        assert kinds == ["find", "find", "prove"], lang
        landed = [
            t
            for t in loaded.tasks
            if t.repo is not None
            and manifest(t.repo.manifest)["family"] == "nothing-to-keep"
        ]
        assert len(landed) == 1, lang
        assert landed[0].expect.hotspot is None
        found = [
            t
            for t in loaded.tasks
            if t.kind == "find" and t.expect.hotspot is not None
        ]
        assert len(found) == 1, lang


@pytest.mark.parametrize(("lang", "task"), ALL, ids=lambda v: str(v))
def test_each_task_names_a_manifest_that_built_and_ran(
    lang: str, task: taskset.Task
) -> None:
    assert task.repo is not None
    found = manifest(task.repo.manifest)
    assert found["status"] == "runs"
    for key in ("workload", "oracle"):
        entry = cast("dict[str, object]", found[key])
        assert entry["verified"] is True
        assert str(entry["command"]).startswith(
            "bundle " if lang == "ruby" else "dotnet "
        )
    runs = cast("list[dict[str, object]]", found["runs"])
    steps = {(r["step"], r["tree"]): r["exit_code"] for r in runs}
    for tree in ("task", "reference"):
        assert steps[("workload", tree)] == 0
        assert steps[("oracle", tree)] == 0
    assert task.repo.shim is None
    assert task.repo.requires == (
        ["ruby", "bundle"] if lang == "ruby" else ["dotnet"]
    )


@pytest.mark.parametrize(("lang", "task"), ALL, ids=lambda v: str(v))
def test_a_prove_task_names_a_branch_cut_at_the_reference(
    lang: str, task: taskset.Task
) -> None:
    del lang
    assert task.repo is not None
    if task.kind != "prove":
        assert task.repo.branch is None
        return
    number = str(task.repo.branch).removeprefix("pr-")
    assert task.repo.manifest.endswith(f"pr{number}")
    assert str(task.repo.branch) in task.prompt


def test_a_known_hotspot_is_a_path_in_the_tasks_repository() -> None:
    for lang, loaded in FILES.items():
        for task in loaded.tasks:
            hotspot = task.expect.hotspot
            if hotspot is None:
                continue
            assert hotspot.file.endswith((".rb", ".cs")), lang
            assert "/" in hotspot.file, lang
