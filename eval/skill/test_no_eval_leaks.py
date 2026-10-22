"""The skill never contains an evaluation task's reference fix."""

import json
from pathlib import Path

import pytest

import leaks

HONO_LINE = (
    "check whether each is needed (a `Headers` built when there is"
    " nothing to merge)"
)


def _tasks(tmp_path: Path, *docs: object) -> Path:
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    for i, doc in enumerate(docs):
        (tasks / f"t{i}.json").write_text(json.dumps(doc))
    return tasks


def _task(files: list[str] | None = None) -> dict[str, object]:
    return {
        "id": "acme-pr1",
        "reference_change": {
            "title": "perf: skip Frobnicator rebuild when the widget is cold",
            "files": files or ["src/widget/frob.py"],
        },
    }


def _skills(tmp_path: Path, text: str) -> Path:
    skills = tmp_path / "skills"
    (skills / "perfproof").mkdir(parents=True, exist_ok=True)
    (skills / "perfproof" / "x.md").write_text(text + "\n")
    return skills


def _allow(tmp_path: Path, *entries: dict[str, str]) -> Path:
    allow = tmp_path / "allow.json"
    allow.write_text(json.dumps(list(entries)))
    return allow


def _entry(line: str, reason: str = "reviewed") -> dict[str, str]:
    return {
        "file": "perfproof/x.md",
        "task": "acme-pr1",
        "line": line,
        "reason": reason,
    }


LEAKY = "Skip the `Frobnicator` rebuild for a cold widget."


def test_the_shipped_skills_leak_no_task() -> None:
    found = leaks.find_leaks()
    assert found == [], "\n".join(
        f"{f.path.relative_to(leaks.ROOT)}:{f.line} {f.task}: {f.why}"
        for f in found
    )


def test_every_shipped_allowance_still_matches_a_line() -> None:
    assert leaks.unused_allowances() == []


def test_every_task_manifest_is_read() -> None:
    files = [
        p for p in leaks.TASKS.rglob("*.json") if p.name not in leaks.NOT_TASKS
    ]
    assert len(list(leaks._manifests(leaks.TASKS))) == len(files) > 0  # pyright: ignore[reportPrivateUsage]


def test_a_line_naming_a_fix_is_flagged(tmp_path: Path) -> None:
    # Hono PR #5122's fix, written as the Node playbook once wrote it.
    (tmp_path / "node.md").write_text(HONO_LINE + "\n")
    found = leaks.find_leaks(tmp_path)
    assert any(f.task == "honojs-hono-pr5122" for f in found)


def test_generic_method_is_not_flagged(tmp_path: Path) -> None:
    (tmp_path / "x.md").write_text(
        "Count the objects built per unit of work and ask whether each is"
        " needed; repeated allocation in a hot loop is a common cost.\n"
    )
    assert leaks.find_leaks(tmp_path) == []


def test_an_unreadable_manifest_fails_the_scan(tmp_path: Path) -> None:
    tasks = _tasks(tmp_path, _task())
    (tasks / "broken.json").write_text('{"id": "x", <<<<<<< HEAD')
    with pytest.raises(ValueError, match=r"broken\.json"):
        leaks.find_leaks(_skills(tmp_path, "ok"), tasks)


def test_a_manifest_without_a_reference_change_fails_the_scan(
    tmp_path: Path,
) -> None:
    tasks = _tasks(tmp_path, {"id": "acme-pr2"})
    with pytest.raises(ValueError, match="reference_change"):
        leaks.find_leaks(_skills(tmp_path, "ok"), tasks)


def test_the_selection_file_is_not_a_manifest(tmp_path: Path) -> None:
    tasks = _tasks(tmp_path, _task())
    (tasks / "selection.json").write_text("{}")
    assert leaks.find_leaks(_skills(tmp_path, "ok"), tasks) == []


def test_an_allowance_covers_its_exact_line_only(tmp_path: Path) -> None:
    tasks = _tasks(tmp_path, _task())
    allow = _allow(tmp_path, _entry(LEAKY))
    assert leaks.find_leaks(_skills(tmp_path, LEAKY), tasks, allow) == []
    edited = LEAKY.replace("cold", "cold or stale")
    assert leaks.find_leaks(_skills(tmp_path, edited), tasks, allow)
    other = "Do not rebuild the `Frobnicator` on cold starts."
    assert leaks.find_leaks(_skills(tmp_path, other), tasks, allow)


def test_an_allowance_never_covers_a_named_file(tmp_path: Path) -> None:
    tasks = _tasks(tmp_path, _task())
    line = "Look in src/widget/frob.py for rebuilds."
    allow = _allow(tmp_path, _entry(line))
    found = leaks.find_leaks(_skills(tmp_path, line), tasks, allow)
    assert [f.why for f in found] == ["names src/widget/frob.py"]


def test_an_allowance_without_a_reason_is_refused(tmp_path: Path) -> None:
    tasks = _tasks(tmp_path, _task())
    allow = _allow(tmp_path, _entry(LEAKY, reason=" "))
    with pytest.raises(ValueError, match="incomplete entry"):
        leaks.find_leaks(_skills(tmp_path, LEAKY), tasks, allow)


def test_a_malformed_allowlist_fails_the_scan(tmp_path: Path) -> None:
    allow = tmp_path / "allow.json"
    allow.write_text("{not json")
    with pytest.raises(ValueError, match="Expecting"):
        leaks.find_leaks(_skills(tmp_path, "ok"), _tasks(tmp_path), allow)


def test_an_allowance_for_an_edited_line_is_reported_stale(
    tmp_path: Path,
) -> None:
    tasks = _tasks(tmp_path, _task())
    allow = _allow(tmp_path, _entry(LEAKY))
    skills = _skills(tmp_path, "Nothing to see.")
    assert leaks.unused_allowances(skills, tasks, allow) == [
        leaks.Allowance("perfproof/x.md", "acme-pr1", LEAKY)
    ]
