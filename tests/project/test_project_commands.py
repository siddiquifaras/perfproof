import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from project_helpers import PYTHON_FILES, MakeRepo, git

from perfproof.cli import main

type Run = Callable[..., tuple[int, dict[str, Any]]]


@pytest.fixture
def cli(capsys: pytest.CaptureFixture[str]) -> Run:
    def run(*argv: str) -> tuple[int, dict[str, Any]]:
        status = main([*argv, "--json"])
        out, _ = capsys.readouterr()
        return status, cast("dict[str, Any]", json.loads(out))

    return run


def ok(cli: Run, *argv: str) -> dict[str, Any]:
    status, doc = cli(*argv)
    assert status == 0, doc
    assert doc["ok"] is True
    return cast("dict[str, Any]", doc["result"])


def refused(cli: Run, *argv: str) -> dict[str, Any]:
    status, doc = cli(*argv)
    assert status == 1, doc
    assert doc["ok"] is False
    return cast("dict[str, Any]", doc["error"])


def test_init_dry_run_writes_nothing(
    cli: Run, example_repo: Callable[[str], Path]
) -> None:
    repo = example_repo("membership")
    result = ok(cli, "init", "--dry-run", "--repo", str(repo))
    assert result["dry_run"] is True
    assert result["model_path"] is None
    assert [w["id"] for w in result["workloads"]] == ["bench"]
    assert result["workloads"][0]["command"] == ["python", "bench.py"]
    assert result["goals"] == [
        {
            "id": "goal-bench",
            "workload_id": "bench",
            "unknown": ["bar", "direction", "guardrails", "metric", "unit"],
        }
    ]
    assert not (repo / ".perfproof").exists()
    assert git(repo, "status", "--porcelain") == ""
    assert "goal:bench" in {q["id"] for q in result["questions"]}


def test_init_writes_only_the_state_directory(
    cli: Run, example_repo: Callable[[str], Path]
) -> None:
    repo = example_repo("rolling")
    result = ok(cli, "init", "--repo", str(repo))
    assert result["model_path"] == str(repo / ".perfproof" / "project.json")
    assert "bench" in result["added"]
    assert git(repo, "status", "--porcelain") == ""
    exclude = (repo / ".git" / "info" / "exclude").read_text()
    assert ".perfproof/" in exclude
    again = ok(cli, "init", "--repo", str(repo))
    assert again["added"] == []
    assert {s["id"] for s in again["skipped"]} >= {"bench", "goal-bench"}


def test_init_outside_a_repository(cli: Run, tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert refused(cli, "init", "--repo", str(plain))["code"] == (
        "not_a_repository"
    )


def test_text_output_lists_evidence_and_questions(
    capsys: pytest.CaptureFixture[str], make_repo: MakeRepo
) -> None:
    repo = make_repo(PYTHON_FILES)
    assert main(["init", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "Proposed project model (nothing is confirmed yet):" in out
    assert "workload bench" in out
    assert "bench.py is a runnable benchmark script" in out
    assert "Questions for the developer:" in out


def test_project_needs_a_model(cli: Run, make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    assert refused(cli, "project", "show", "--repo", str(repo))["code"] == (
        "no_model"
    )


def test_a_session_from_proposal_to_measurable(
    cli: Run, make_repo: MakeRepo
) -> None:
    repo = str(make_repo(PYTHON_FILES))
    ok(cli, "init", "--repo", repo)
    shown = ok(cli, "project", "show", "--repo", repo)
    assert shown["measurable"]["bench"]["ok"] is False
    assert "goal:bench" in {q["id"] for q in shown["questions"]}

    unknown = refused(
        cli,
        "project",
        "confirm",
        "target-python_cpu",
        "bench",
        "goal-bench",
        "--repo",
        repo,
    )
    assert unknown["code"] == "goal_unknown"
    assert unknown["details"]["unknown"] == [
        "metric",
        "direction",
        "unit",
        "guardrails",
    ]
    # The refused command wrote nothing.
    after = ok(cli, "project", "show", "--repo", repo)
    assert after["model"]["workloads"][0]["status"] == "proposed"

    early = refused(
        cli,
        "project",
        "goal",
        "bench",
        "--metric",
        "wall",
        "--confirm",
        "--repo",
        repo,
    )
    assert early["code"] == "workload_not_confirmed"

    ok(
        cli,
        "project",
        "confirm",
        "target-python_cpu",
        "bench",
        "--session",
        "s-1",
        "--repo",
        repo,
    )
    done = ok(
        cli,
        "project",
        "goal",
        "bench",
        "--metric",
        "wall",
        "--confirm",
        "--repo",
        repo,
    )
    assert [r["event"] for r in done["records"]] == ["edited", "confirmed"]
    goal = done["entries"][0]
    assert goal["metric"] == "wall_time"
    assert goal["bar"] is None
    assert goal["guardrails"][0]["allowance_source"] == "default"
    final = ok(cli, "project", "show", "--repo", repo)
    assert final["measurable"]["bench"] == {"ok": True, "problems": []}


def test_constraints_retire_check_and_history(
    cli: Run, make_repo: MakeRepo
) -> None:
    repo_path = make_repo(PYTHON_FILES)
    repo = str(repo_path)
    ok(cli, "init", "--repo", repo)
    added = ok(
        cli,
        "project",
        "constraint",
        "add",
        "--kind",
        "public_api",
        "--subject",
        "demo.tile",
        "--confirm",
        "--repo",
        repo,
    )
    assert [r["event"] for r in added["records"]] == ["created", "confirmed"]
    assert (
        refused(
            cli, "project", "retire", "bench", "--reason", "x", "--repo", repo
        )["code"]
        == "has_dependents"
    )
    ok(
        cli,
        "project",
        "retire",
        "bench",
        "--reason",
        "x",
        "--cascade",
        "--repo",
        repo,
    )
    (repo_path / "bench.py").write_text("# changed\n")
    checked = ok(cli, "project", "check", "--repo", repo)
    assert checked["marked_stale"] == []
    history = ok(cli, "project", "history", "bench", "--repo", repo)
    assert [r["event"] for r in history["records"]] == ["created", "retired"]
    one = ok(cli, "project", "show", "constraint-1", "--repo", repo)
    assert one["entry"]["status"] == "confirmed"


def test_check_reports_a_stale_entry_and_its_revalidation(
    cli: Run, make_repo: MakeRepo
) -> None:
    repo_path = make_repo(PYTHON_FILES)
    repo = str(repo_path)
    ok(cli, "init", "--repo", repo)
    ok(cli, "project", "confirm", "target-python_cpu", "bench", "--repo", repo)
    (repo_path / "bench.py").write_text(PYTHON_FILES["bench.py"] + "# n\n")
    checked = ok(cli, "project", "check", "--repo", repo)
    assert checked["marked_stale"][0]["trigger"] == "file_changed"
    assert checked["revalidated"] == []
    assert checked["reproposed"] == ["bench"]
    assert checked["unresolved"] == []


def test_a_goal_with_a_custom_metric_asks_for_direction_and_unit(
    cli: Run, make_repo: MakeRepo
) -> None:
    repo = str(make_repo(PYTHON_FILES))
    ok(cli, "init", "--repo", repo)
    ok(cli, "project", "confirm", "target-python_cpu", "bench", "--repo", repo)
    ok(
        cli,
        "project",
        "goal",
        "bench",
        "--metric",
        "frames_per_s",
        "--repo",
        repo,
    )
    error = refused(cli, "project", "confirm", "goal-bench", "--repo", repo)
    assert error["code"] == "goal_unknown"
    assert error["details"]["unknown"] == ["direction", "unit"]
    ok(
        cli,
        "project",
        "goal",
        "bench",
        "--direction",
        "higher_is_better",
        "--unit",
        "frames/s",
        "--guardrail",
        "memory=0.2",
        "--confirm",
        "--repo",
        repo,
    )
    goal = ok(cli, "project", "show", "goal-bench", "--repo", repo)["entry"]
    assert goal["status"] == "confirmed"
    assert goal["guardrails"][0]["allowance_source"] == "project"
