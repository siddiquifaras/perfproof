"""Tests for the split draw in `draw` and the committed splits.

The rule is in docs/evaluation.md ("How the held-out set is chosen").
Synthetic pools exercise each step of it; the last tests check that
the committed manifests and ``eval/tasks/SPLITS.md`` are what the rule
gives for the committed review.
"""

import re
from pathlib import Path
from typing import Any

import pytest

import check
import draw
from draw import Review, Selection, assigned_splits, draw_splits, order_key
from task import Run, Status, Task, load_tasks


def _task(
    task_id: str,
    family: str,
    klass: str = "python",
    status: Status = "runs",
) -> Task:
    data: dict[str, Any] = {
        "id": task_id,
        "repository": "o/r",
        "stars": None,
        "pushed_at": None,
        "task_commit": "a" * 40,
        "task_commit_basis": "test",
        "reference_change": {
            "kind": "commit",
            "url": "u",
            "title": "t",
            "commit": "b" * 40,
            "merged_at": None,
            "files": [],
        },
        "revert": None,
        "family": family,
        "change_kind": "improvement",
        "target_class": klass,
        "workload": {"command": None, "source": "unknown", "verified": False},
        "oracle": {"command": None, "source": "unknown", "verified": False},
        "evidence": [],
        "audit": None,
        "status": status,
        "status_reason": f"status {status}",
        "runs": [],
        "split": "development-candidate",
        "mined_at": "t",
        "miner": "m",
    }
    return Task.model_validate(data)


def _selection(
    tasks: list[Task],
    drop: frozenset[str] = frozenset(),
    unbuilt: dict[str, str] | None = None,
) -> Selection:
    review = {
        t.id: Review(keep=t.id not in drop, reason=f"read {t.id}", read=[])
        for t in tasks
    }
    return Selection.model_validate(
        {
            "salts": {"held-out": "h", "development": "d"},
            "unbuilt_classes": unbuilt or {},
            "review": review,
        }
    )


def _pool(per_family: int, klass: str = "python") -> list[Task]:
    families = (
        "known-change",
        "hidden-fix",
        "nothing-to-keep",
        "published-benchmark",
    )
    return [
        _task(f"{klass}-{fam}-{i}", fam, klass)
        for fam in families
        for i in range(per_family)
    ]


def _first(tasks: list[Task], family: str, salt: str, n: int) -> list[str]:
    ids = [t.id for t in tasks if t.family == family]
    return sorted(ids, key=lambda i: order_key(salt, i))[:n]


def test_order_key_is_sha256_of_salt_and_id() -> None:
    # sha256("ab"), so the salt is prepended with no separator.
    assert order_key("a", "b") == (
        "fb8e20fc2e4c3f248c60c39bd652f3c1347298bb977b8b4d5903b85055620603"
    )


def test_slots_take_the_first_runs_in_hash_order() -> None:
    tasks = _pool(4)
    out = draw_splits(tasks, _selection(tasks))
    picks = [v.task_id for v in out["held-out"]["python"].picks]
    expected = (
        _first(tasks, "known-change", "h", 2)
        + _first(tasks, "hidden-fix", "h", 1)
        + _first(tasks, "nothing-to-keep", "h", 1)
        + _first(tasks, "published-benchmark", "h", 1)
    )
    assert picks == expected
    assert out["held-out"]["python"].stopped is None


def test_development_draws_from_the_rest_with_its_own_salt() -> None:
    tasks = _pool(4)
    out = draw_splits(tasks, _selection(tasks))
    held = {v.task_id for v in out["held-out"]["python"].picks}
    dev = [v.task_id for v in out["development"]["python"].picks]
    assert len(dev) == len(draw.SLOTS)
    assert not held & set(dev)
    rest = [t for t in tasks if t.id not in held]
    assert dev[:2] == _first(rest, "known-change", "d", 2)


def test_removed_and_unbuildable_candidates_are_passed_over() -> None:
    tasks = _pool(4)
    kc = _first(tasks, "known-change", "h", 4)
    failed = {kc[1]: "failed"}
    tasks = [
        t.model_copy(update={"status": failed[t.id]}) if t.id in failed else t
        for t in tasks
    ]
    out = draw_splits(tasks, _selection(tasks, drop=frozenset({kc[0]})))
    by = out["held-out"]["python"]
    outcomes = {v.task_id: v.outcome for v in by.visits}
    assert outcomes[kc[0]] == "removed"
    assert outcomes[kc[1]] == "passed"
    assert [v.task_id for v in by.picks][:2] == kc[2:4]


def test_a_reference_only_failure_is_drawn_not_passed_over() -> None:
    tasks = _pool(4)
    first = _first(tasks, "known-change", "h", 1)[0]
    tasks = [
        t.model_copy(update={"status": "reference-fails"})
        if t.id == first
        else t
        for t in tasks
    ]
    out = draw_splits(tasks, _selection(tasks))
    picks = [v.task_id for v in out["held-out"]["python"].picks]
    assert picks[0] == first


def _runs(**codes: int) -> list[Run]:
    # Keyword names are <tree>_<step>; every step not named exits 0.
    # As in check.check, a failed setup ends the runs there.
    out: list[Run] = []
    for tree in ("task", "reference"):
        for step in ("setup", "oracle", "workload"):
            code = codes.get(f"{tree}_{step}", 0)
            out.append(
                Run(
                    step=step,
                    tree=tree,
                    command=step,
                    exit_code=code,
                    duration_s=1.0,
                    date="t",
                    machine="m",
                    output_tail="",
                )
            )
            if step == "setup" and code != 0:
                return out
    return out


@pytest.mark.parametrize(
    ("codes", "need_task_oracle", "status"),
    [
        ({}, True, "runs"),
        ({"reference_oracle": 1}, True, "reference-fails"),
        ({"reference_workload": 1}, True, "reference-fails"),
        # Buildability is judged on the task tree alone: a hang, a
        # missing entry point or a failed build on the reference tree
        # only is a result of the change.
        ({"reference_oracle": 124}, True, "reference-fails"),
        ({"reference_workload": 127}, True, "reference-fails"),
        ({"reference_setup": 1}, True, "reference-fails"),
        ({"task_oracle": 124, "reference_oracle": 124}, True, "builds"),
        ({"task_workload": 127}, True, "builds"),
        ({"task_oracle": 1, "reference_oracle": 1}, True, "builds"),
        ({"task_workload": 1}, True, "builds"),
        ({"task_oracle": 1}, False, "runs"),
        ({"task_oracle": 124}, False, "runs"),
        ({"task_setup": 1}, True, "failed"),
    ],
)
def test_classify(
    codes: dict[str, int], need_task_oracle: bool, status: str
) -> None:
    assert check.classify(_runs(**codes), need_task_oracle)[0] == status


def test_a_timed_out_docker_run_is_killed_by_its_name() -> None:
    name = check.container_name(
        ["docker", "run", "--rm", "--init", "--name", "x-1", "img", "ls"]
    )
    assert name == "x-1"
    assert check.container_name(["docker", "build", "."]) is None


def test_salts_are_the_documented_ones() -> None:
    sel = draw.load_selection()
    root = Path(draw.__file__).resolve().parents[2]
    spec = (root / "docs" / "evaluation.md").read_text()
    documented = re.findall(r"salt `([^`]+)`", " ".join(spec.split()))
    assert documented == [
        sel.salts["held-out"],
        sel.salts["development"],
    ]
    readme = (root / "eval" / "README.md").read_text()
    assert f"`{sel.salts['development']}`" in readme


def test_a_missing_family_goes_to_the_next_known_change() -> None:
    tasks = [t for t in _pool(6) if t.family not in ("published-benchmark",)]
    out = draw_splits(tasks, _selection(tasks))
    by = out["held-out"]["python"]
    kc = _first(tasks, "known-change", "h", 3)
    assert [v.task_id for v in by.picks if v.family == "known-change"] == kc
    assert by.picks[-1].slot == "published-benchmark"


def test_draw_stops_at_an_unreviewed_or_unbuilt_candidate() -> None:
    tasks = _pool(3)
    first = _first(tasks, "known-change", "h", 1)[0]
    sel = _selection([t for t in tasks if t.id != first])
    out = draw_splits(tasks, sel)
    assert out["held-out"]["python"].stopped == (
        f"{first} needs its step-1 review"
    )
    unbuilt = [
        t.model_copy(update={"status": "candidate"}) if t.id == first else t
        for t in tasks
    ]
    out = draw_splits(unbuilt, _selection(unbuilt))
    assert out["held-out"]["python"].stopped == f"{first} needs a build"


def test_unbuilt_class_is_drawn_unbuilt_and_not_for_development() -> None:
    tasks = [
        t.model_copy(update={"status": "candidate"}) for t in _pool(3, "gpu")
    ]
    sel = _selection(tasks, unbuilt={"gpu": "needs a GPU host"})
    out = draw_splits(tasks, sel)
    picks = out["held-out"]["gpu"].picks
    assert len(picks) == len(draw.SLOTS)
    assert {v.outcome for v in picks} == {"unbuilt"}
    assert "gpu" not in out["development"]


def test_committed_splits_match_the_draw() -> None:
    tasks = load_tasks()
    sel = draw.load_selection()
    draws = draw_splits(tasks, sel)
    splits = assigned_splits(draws)
    for task in tasks:
        assert task.split == splits.get(task.id, "development-candidate"), (
            task.id
        )
    assert draw.REPORT.read_text() == draw.report(draws, sel)


def test_every_review_names_a_task_in_the_pool() -> None:
    ids = {t.id for t in load_tasks()}
    assert set(draw.load_selection().review) <= ids
