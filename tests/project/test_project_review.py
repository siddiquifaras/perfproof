import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from project_helpers import (
    NOW,
    PYTHON_FILES,
    VERSION,
    MakeRepo,
    Records,
    confirmed,
    proposed,
)

from perfproof.cli import main
from perfproof.project import (
    NotConfirmedError,
    ProjectError,
    require_confirmed,
    store,
)
from perfproof.project.entries import find, put, with_fields
from perfproof.project.goals import parse_guardrail
from perfproof.project.lifecycle import GoalUpdate, confirm, set_goal
from perfproof.project.provenance import needs_rederivation
from perfproof.schema import (
    ConstraintEntry,
    EntryKind,
    GoalEntry,
    ProjectModel,
)

LATER = NOW + timedelta(hours=1)

type Run = Callable[..., tuple[int, dict[str, Any]]]


@pytest.fixture
def cli(capsys: pytest.CaptureFixture[str]) -> Run:
    def run(*argv: str) -> tuple[int, dict[str, Any]]:
        status = main([*argv, "--json"])
        out, _ = capsys.readouterr()
        return status, cast("dict[str, Any]", json.loads(out))

    return run


def goal_of(model: ProjectModel) -> GoalEntry:
    entry = find(model, "goal-bench")
    assert isinstance(entry, GoalEntry)
    return entry


# B3: default guardrails follow the metric


def test_default_guardrails_come_back_when_the_metric_switches_back(
    make_repo: MakeRepo,
) -> None:
    model = proposed(make_repo(PYTHON_FILES))

    def goal(m: ProjectModel, metric: str) -> ProjectModel:
        return set_goal(m, "bench", GoalUpdate(metric=metric), NOW, VERSION)[0]

    model = goal(model, "wall_time")
    assert [g.metric for g in goal_of(model).guardrails or ()] == [
        "peak_memory"
    ]
    # peak_memory is now the primary, so it cannot also be a guardrail.
    model = goal(model, "peak_memory")
    assert goal_of(model).guardrails == ()
    model = goal(model, "wall_time")
    rails = goal_of(model).guardrails or ()
    assert [(g.metric, g.allowance_source) for g in rails] == [
        ("peak_memory", "default")
    ]


def test_project_guardrails_survive_a_metric_switch(
    make_repo: MakeRepo,
) -> None:
    model = proposed(make_repo(PYTHON_FILES))
    chosen = (parse_guardrail("memory=0.2"),)
    model, _ = set_goal(
        model,
        "bench",
        GoalUpdate(metric="wall_time", guardrails=chosen),
        NOW,
        VERSION,
    )
    model, _ = set_goal(
        model, "bench", GoalUpdate(metric="cpu_time"), NOW, VERSION
    )
    rails = list(goal_of(model).guardrails or ())
    assert len(rails) == 1
    rail = rails[0]
    assert (rail.allowance, rail.allowance_source) == (0.2, "project")


# A target must be confirmed too


def test_a_workload_is_confirmed_only_after_its_target(
    make_repo: MakeRepo,
) -> None:
    model = proposed(make_repo(PYTHON_FILES))
    with pytest.raises(ProjectError) as caught:
        confirm(model, ["bench"], "s", NOW)
    assert caught.value.code == "target_not_confirmed"


def test_the_guard_needs_a_confirmed_target(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    model = confirmed(repo)
    target = with_fields(
        model.targets[0], status="proposed", confirmation=None
    )
    store.mutate(
        repo,
        lambda m: (
            put(m, target),
            [_line("reproposed", target)],
        ),
    )
    with pytest.raises(NotConfirmedError) as caught:
        require_confirmed(repo, "bench")
    assert {(p.entry_id, p.reason) for p in caught.value.problems} == {
        ("target-python_cpu", "not_confirmed")
    }


def _line(event: Any, entry: Any) -> Any:
    from perfproof.project.entries import record

    return record(event, entry, NOW)


# Confirming cannot clear everything


def test_confirming_a_goal_bound_to_another_machine_is_refused(
    make_repo: MakeRepo,
) -> None:
    from perfproof.project.service import check_project

    repo = make_repo(PYTHON_FILES)
    confirmed(repo)

    def bind(m: ProjectModel) -> tuple[ProjectModel, Records]:
        m, a = set_goal(
            m, "bench", GoalUpdate(host_id="a" * 64), LATER, VERSION
        )
        m, b = confirm(m, ["goal-bench"], "s", LATER)
        return m, [*a, *b]

    store.mutate(repo, bind)
    result = check_project(
        repo, now=LATER, version=VERSION, host_id=lambda: "b" * 64
    )
    assert [f.trigger for f in result.marked_stale] == ["machine_differs"]
    model = store.load_model(repo)
    assert model is not None
    with pytest.raises(ProjectError) as caught:
        confirm(model, ["goal-bench"], "s", LATER)
    assert caught.value.code == "stale_needs_edit"
    assert "--this-machine" in caught.value.message
    # Setting it again on the machine it now runs on clears the trigger.
    again, _ = set_goal(
        model, "bench", GoalUpdate(host_id="b" * 64), LATER, VERSION
    )
    assert goal_of(again).status == "proposed"
    assert goal_of(again).host_id == "b" * 64


# Protected files record their hash


def test_a_protected_file_records_its_hash_and_goes_stale_when_edited(
    cli: Run, make_repo: MakeRepo
) -> None:
    repo = make_repo({**PYTHON_FILES, "api.py": "def f(): ...\n"})
    r = str(repo)
    assert cli("init", "--repo", r)[0] == 0
    added = cli(
        "project", "constraint", "add", "--kind", "protected_file",
        "--subject", "api.py", "--confirm", "--repo", r,
    )  # fmt: skip
    assert added[0] == 0, added
    model = store.load_model(repo)
    assert model is not None
    constraint = model.constraints[0]
    assert isinstance(constraint, ConstraintEntry)
    assert [f.path for f in constraint.provenance.files] == ["api.py"]
    (repo / "api.py").write_text("def f(): ...\ndef g(): ...\n")
    status, doc = cli("project", "check", "--repo", r)
    assert status == 0
    assert doc["result"]["marked_stale"][0]["id"] == "constraint-1"
    assert doc["result"]["unresolved"][0]["id"] == "constraint-1"
    # Confirming it again takes the file's new hash.
    assert cli("project", "confirm", "constraint-1", "--repo", r)[0] == 0
    fresh = store.load_model(repo)
    assert fresh is not None
    entry = fresh.constraints[0]
    assert entry.status == "confirmed"
    assert entry.provenance.files[0].sha256 != (
        constraint.provenance.files[0].sha256
    )
    assert not cli("project", "check", "--repo", r)[1]["result"][
        "marked_stale"
    ]


def test_a_protected_file_must_exist_and_stay_in_the_project(
    cli: Run, make_repo: MakeRepo
) -> None:
    repo = make_repo(PYTHON_FILES)
    r = str(repo)
    cli("init", "--repo", r)
    for subject, code in (
        ("missing.py", "not_found"),
        ("../x", "invalid_entry"),
    ):
        status, doc = cli(
            "project", "constraint", "add", "--kind", "protected_file",
            "--subject", subject, "--repo", r,
        )  # fmt: skip
        assert status == 1
        assert doc["error"]["code"] == code


# Versions compare with padding


@pytest.mark.parametrize(
    ("derived_by", "revision", "older"),
    [
        ("0.1", "0.1.0", False),
        ("0.1.0", "0.1", False),
        ("0.1", "0.1.1", True),
        ("1", "0.9.9", False),
        ("0.2.0", "0.10.0", True),
        ("garbage", "0.1.0", True),
    ],
)
def test_versions_are_padded_before_they_are_compared(
    derived_by: str, revision: str, older: bool
) -> None:
    table: dict[EntryKind, str] = {
        "target": revision,
        "workload": revision,
        "how_to_run": revision,
        "goal": revision,
        "constraint": revision,
    }
    assert needs_rederivation("workload", derived_by, table) is older


# Orphan history lines are marked


CRASH = textwrap.dedent(
    """
    import os, sys
    from pathlib import Path
    from datetime import datetime, UTC
    from perfproof.project import store
    from perfproof.project.entries import record, with_fields

    repo = Path(sys.argv[1])
    Path.replace = lambda self, target: os._exit(9)

    def change(model):
        entry = with_fields(
            model.workloads[0], status="retired", retired_reason="x"
        )
        return model, [record("retired", entry, datetime.now(UTC))]

    store.mutate(repo, change)
    """
)


def test_history_marks_the_line_of_a_write_that_never_reached_the_model(
    cli: Run, make_repo: MakeRepo
) -> None:
    repo = make_repo(PYTHON_FILES)
    r = str(repo)
    assert cli("init", "--repo", r)[0] == 0
    done = subprocess.run(
        [sys.executable, "-c", CRASH, r],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 9, done.stderr
    status, doc = cli("project", "history", "--repo", r)
    assert status == 0
    rows = doc["result"]["records"]
    assert [row["orphan"] for row in rows] == [False] * (len(rows) - 1) + [
        True
    ]
    assert rows[-1]["event"] == "retired"


def test_text_history_says_which_lines_are_orphans(
    capsys: pytest.CaptureFixture[str], make_repo: MakeRepo
) -> None:
    repo = make_repo(PYTHON_FILES)
    assert main(["init", "--repo", str(repo)]) == 0
    capsys.readouterr()
    subprocess.run(
        [sys.executable, "-c", CRASH, str(repo)],
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert main(["project", "history", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "[orphan: not in the model]" in out[-1]
    assert not any("[orphan" in line for line in out[:-1])


# The rule: a workload's own files must match its confirmed hashes


@pytest.fixture
def python_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").symlink_to(sys.executable)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def _check(repo: Path, *, probe: bool = False) -> Any:
    from perfproof.project.service import check_project

    return check_project(
        repo,
        now=LATER,
        version=VERSION,
        host_id=lambda: None,
        probe=probe,
        probe_timeout=60,
    )


def _bench(model: ProjectModel) -> Any:
    entry = find(model, "bench")
    assert entry is not None
    return entry


def _model(repo: Path) -> ProjectModel:
    model = store.load_model(repo)
    assert model is not None
    return model


def test_stale_on_command_failed_then_script_changed_is_not_probed(
    make_repo: MakeRepo, python_on_path: None, tmp_path: Path
) -> None:
    marker = tmp_path / "ran"
    repo = make_repo(
        {
            **PYTHON_FILES,
            "data.txt": "ok\n",
            "bench.py": (
                "from pathlib import Path\n"
                "if __name__ == '__main__':\n"
                "    text = Path('data.txt').read_text()\n"
                "    print(text)\n"
                "    raise SystemExit(0 if text.strip() == 'ok' else 3)\n"
            ),
        }
    )
    confirmed(repo)
    _check(repo, probe=True)
    (repo / "data.txt").write_text("broken\n")
    failed = _check(repo, probe=True)
    assert [f.trigger for f in failed.marked_stale] == ["command_failed"]
    assert _bench(_model(repo)).status == "stale"
    # The script itself changes while the workload waits.
    (repo / "bench.py").write_text(
        "from pathlib import Path\n"
        "if __name__ == '__main__':\n"
        f"    Path({str(marker)!r}).write_text('x')\n"
    )
    result = _check(repo, probe=True)
    assert not marker.exists()
    assert result.reproposed == ("bench",)
    entry = _bench(_model(repo))
    assert (entry.status, entry.version) == ("proposed", 2)
    with pytest.raises(NotConfirmedError):
        require_confirmed(repo, "bench")


def test_stale_on_dependency_edited_then_script_changed_is_reproposed(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    path = repo / "pyproject.toml"
    path.write_text(path.read_text() + "# a comment\n")
    (repo / "extra.py").write_text("x = 1\n")
    assert _check(repo).reproposed == ("target-python_cpu",)
    before = _bench(_model(repo))
    assert before.status == "stale"
    assert before.invalidation.trigger == "dependency_edited"
    confirmed_files = {f.path: f.sha256 for f in before.provenance.files}
    # New benchmark code arrives while it waits for its target.
    (repo / "bench.py").write_text(PYTHON_FILES["bench.py"] + "# new code\n")
    store.mutate(repo, lambda m: confirm(m, ["target-python_cpu"], "s", LATER))
    result = _check(repo)
    assert result.reproposed == ("bench",)
    assert result.revalidated == ()
    entry = _bench(_model(repo))
    assert (entry.status, entry.version) == ("proposed", 2)
    assert entry.confirmation is None
    assert {f.path: f.sha256 for f in entry.provenance.files} != (
        confirmed_files
    )
    with pytest.raises(NotConfirmedError):
        require_confirmed(repo, "bench")
