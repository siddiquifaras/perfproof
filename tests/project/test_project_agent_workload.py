import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from project_helpers import PYTHON_FILES, MakeRepo

from perfproof.bench import compare as compare_module
from perfproof.cli import main
from perfproof.project import NotConfirmedError, require_confirmed
from perfproof.project.store import load_model
from perfproof.schema import BenchmarkSpec, GoalEntry, WorkloadEntry

type Run = Callable[..., tuple[int, dict[str, Any]]]

AGENT_FILES = {
    **PYTHON_FILES,
    "agent_bench.py": "print('PERFPROOF {\"value\": 0.5}')\n",
}
TARGET = "target-python_cpu"
NAME = "agent-bench"


def spec_fields(*, warmup: int = 1) -> dict[str, Any]:
    return {
        "command": ["python", "agent_bench.py"],
        "metric": {
            "kind": "wall",
            "direction": "lower_is_better",
            "unit": "s",
        },
        "correctness": {
            "command": ["python", "-m", "pytest", "-q"],
            "timeout_seconds": 60.0,
        },
        "warmup": warmup,
        "budget": {"max_rounds": 20, "max_seconds": 600.0},
        "looks": {"n0": 10, "k": 10, "max_looks": 3},
        "timeout_seconds": 30.0,
        "harness": ["agent_bench.py"],
        "oracle": ["tests"],
    }


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
    return cast("dict[str, Any]", doc["result"])


def refused(cli: Run, *argv: str) -> dict[str, Any]:
    status, doc = cli(*argv)
    assert status == 1, doc
    assert doc["ok"] is False
    return cast("dict[str, Any]", doc["error"])


def define(cli: Run, repo: str, tmp_path: Path, *, warmup: int = 1) -> str:
    path = tmp_path / f"spec-{warmup}.json"
    path.write_text(json.dumps(spec_fields(warmup=warmup)))
    result = ok(
        cli, "bench", "define", NAME, "--spec", str(path), "--repo", repo
    )
    return cast("str", result["spec_hash"])


@pytest.fixture
def repo(cli: Run, make_repo: MakeRepo) -> str:
    root = str(make_repo(AGENT_FILES))
    ok(cli, "init", "--repo", root)
    return root


def add(repo: str, *extra: str) -> tuple[str, ...]:
    return (
        "project",
        "add-workload",
        NAME,
        "--target",
        TARGET,
        *extra,
        "--repo",
        repo,
    )


def workload(repo: str, name: str = NAME) -> WorkloadEntry:
    model = load_model(Path(repo))
    assert model is not None
    return next(w for w in model.workloads if w.id == name)


def workload_problem(spec: BenchmarkSpec, repo: str) -> str | None:
    return compare_module._workload_problem(  # pyright: ignore[reportPrivateUsage]
        spec, Path(repo)
    )


def init_spec(
    cli: Run, repo: str, tmp_path: Path, warmup: int = 1
) -> BenchmarkSpec:
    """Define a benchmark named like the workload ``init`` found."""
    fields = {
        **spec_fields(warmup=warmup),
        "name": "bench",
        "command": ["python", "bench.py"],
        "harness": ["bench.py"],
        "target_class": "python_cpu",
    }
    path = tmp_path / f"bench-spec-{warmup}.json"
    path.write_text(json.dumps(fields))
    ok(cli, "bench", "define", "bench", "--spec", str(path), "--repo", repo)
    return BenchmarkSpec.model_validate(fields)


def confirm_init_workload(cli: Run, repo: str) -> None:
    ok(cli, "project", "confirm", TARGET, "bench", "--repo", repo)
    ok(
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


def test_confirming_an_init_workload_binds_its_spec(
    cli: Run, repo: str, tmp_path: Path
) -> None:
    confirm_init_workload(cli, repo)
    spec = init_spec(cli, repo, tmp_path)
    before = workload(repo, "bench")
    assert before.spec_hash is None
    # Confirmed before the benchmark existed: compare withholds KEEP.
    problem = workload_problem(spec, repo)
    assert problem is not None
    assert "confirmed before its benchmark was defined" in problem

    again = ok(cli, "project", "confirm", "bench", "--repo", repo)
    assert [(r["event"], r["id"]) for r in again["records"]] == [
        ("confirmed", "bench")
    ]
    after = workload(repo, "bench")
    assert after.spec_hash == spec.spec_hash
    assert after.version == before.version + 1
    assert after.status == "confirmed"
    shown = ok(cli, "project", "show", "--repo", repo)
    assert shown["measurable"]["bench"]["ok"] is True
    assert workload_problem(spec, repo) is None
    # A third confirmation changes nothing.
    third = ok(cli, "project", "confirm", "bench", "--repo", repo)
    assert third["records"] == []


def test_a_spec_defined_after_binding_is_not_the_confirmed_one(
    cli: Run, repo: str, tmp_path: Path
) -> None:
    confirm_init_workload(cli, repo)
    spec = init_spec(cli, repo, tmp_path)
    ok(cli, "project", "confirm", "bench", "--repo", repo)
    changed = BenchmarkSpec.model_validate(
        {**spec.model_dump(mode="json", exclude={"spec_hash"}), "warmup": 5}
    )
    problem = workload_problem(changed, repo)
    assert problem is not None
    assert "spec_changed" in problem


def test_add_workload_registers_the_spec_as_proposed(
    cli: Run, repo: str, tmp_path: Path
) -> None:
    spec_hash = define(cli, repo, tmp_path)
    result = ok(cli, *add(repo))
    assert [(r["event"], r["id"], r["status"]) for r in result["records"]] == [
        ("created", NAME, "proposed")
    ]
    entry = workload(repo)
    assert entry.status == "proposed"
    assert entry.source == "agent_defined"
    assert entry.command == ("python", "agent_bench.py")
    assert entry.cwd == "."
    assert entry.spec_hash == spec_hash
    assert entry.target_id == TARGET
    history = ok(cli, "project", "history", NAME, "--repo", repo)
    assert history["records"][0]["note"].startswith("defined by the agent")


def test_add_workload_records_given_evidence(
    cli: Run, repo: str, tmp_path: Path
) -> None:
    define(cli, repo, tmp_path)
    ok(cli, *add(repo, "--evidence", "the checkout handler under load"))
    history = ok(cli, "project", "history", NAME, "--repo", repo)
    assert history["records"][0]["note"] == "the checkout handler under load"


def test_add_workload_refuses_what_does_not_exist(
    cli: Run, repo: str, tmp_path: Path
) -> None:
    unknown = refused(
        cli,
        "project",
        "add-workload",
        "nope",
        "--target",
        TARGET,
        "--repo",
        repo,
    )
    assert unknown["code"] == "not_found"
    define(cli, repo, tmp_path)
    no_target = refused(
        cli,
        "project",
        "add-workload",
        NAME,
        "--target",
        "target-nope",
        "--repo",
        repo,
    )
    assert no_target["code"] == "not_found"
    not_a_target = refused(
        cli,
        "project",
        "add-workload",
        NAME,
        "--target",
        "bench",
        "--repo",
        repo,
    )
    assert not_a_target["code"] == "not_found"
    model = load_model(Path(repo))
    assert model is not None
    assert NAME not in {w.id for w in model.workloads}


def test_add_workload_is_idempotent_for_the_same_spec(
    cli: Run, repo: str, tmp_path: Path
) -> None:
    define(cli, repo, tmp_path)
    ok(cli, *add(repo))
    ok(cli, "project", "confirm", TARGET, NAME, "--repo", repo)
    again = ok(cli, *add(repo))
    assert again["records"] == []
    assert workload(repo).status == "confirmed"


def test_a_workload_with_another_spec_needs_replace(
    cli: Run, repo: str, tmp_path: Path
) -> None:
    first = define(cli, repo, tmp_path)
    ok(cli, *add(repo))
    ok(cli, "project", "confirm", TARGET, NAME, "--repo", repo)
    ok(
        cli,
        "project",
        "goal",
        NAME,
        "--metric",
        "wall",
        "--confirm",
        "--repo",
        repo,
    )
    second = define(cli, repo, tmp_path, warmup=2)
    assert second != first
    assert refused(cli, *add(repo))["code"] == "ambiguous_hash"
    clash = refused(cli, *add(repo, "--spec-hash", second))
    assert clash["code"] == "duplicate"
    assert "--replace" in clash["message"]
    assert workload(repo).spec_hash == first
    assert workload(repo).status == "confirmed"

    replaced = ok(cli, *add(repo, "--spec-hash", second, "--replace"))
    events = {(r["event"], r["id"], r["status"]) for r in replaced["records"]}
    assert events == {
        ("edited", NAME, "proposed"),
        ("marked_stale", f"goal-{NAME}", "stale"),
    }
    entry = workload(repo)
    assert (entry.spec_hash, entry.status, entry.version) == (
        second,
        "proposed",
        2,
    )


def test_a_retired_workload_needs_replace(
    cli: Run, repo: str, tmp_path: Path
) -> None:
    define(cli, repo, tmp_path)
    ok(cli, *add(repo))
    ok(
        cli,
        "project",
        "retire",
        NAME,
        "--reason",
        "not representative",
        "--repo",
        repo,
    )
    assert refused(cli, *add(repo))["code"] == "duplicate"
    ok(cli, *add(repo, "--replace"))
    assert workload(repo).status == "proposed"


def test_confirm_and_goal_work_for_an_agent_workload(
    cli: Run, repo: str, tmp_path: Path
) -> None:
    spec_hash = define(cli, repo, tmp_path)
    ok(cli, *add(repo))
    shown = ok(cli, "project", "show", "--repo", repo)
    assert shown["measurable"][NAME]["ok"] is False
    ok(cli, "project", "confirm", TARGET, NAME, "--repo", repo)
    goal = ok(
        cli,
        "project",
        "goal",
        NAME,
        "--metric",
        "wall",
        "--confirm",
        "--repo",
        repo,
    )
    assert {e["id"]: e["status"] for e in goal["entries"]} == {
        f"goal-{NAME}": "confirmed"
    }
    assert workload(repo).status == "confirmed"
    shown = ok(cli, "project", "show", "--repo", repo)
    assert shown["measurable"][NAME] == {"ok": True, "problems": []}

    # A constraint stated later does not send the workload stale: its
    # spec existed before it (an `init` workload has no frozen spec).
    ok(
        cli,
        "project",
        "constraint",
        "add",
        "--kind",
        "public_api",
        "--subject",
        "the public API",
        "--confirm",
        "--repo",
        repo,
    )
    shown = ok(cli, "project", "show", "--repo", repo)
    assert shown["measurable"][NAME] == {"ok": True, "problems": []}

    confirmed = require_confirmed(Path(repo), NAME, spec_hash=spec_hash)
    assert isinstance(confirmed.goal, GoalEntry)
    assert confirmed.workload.source == "agent_defined"

    # A spec defined after the confirmation is not what was confirmed.
    other = define(cli, repo, tmp_path, warmup=3)
    with pytest.raises(NotConfirmedError) as caught:
        require_confirmed(Path(repo), NAME, spec_hash=other)
    assert {p.reason for p in caught.value.problems} == {"spec_changed"}


def test_several_specs_need_a_hash_and_the_chosen_one_is_bound(
    cli: Run, repo: str, tmp_path: Path
) -> None:
    confirm_init_workload(cli, repo)
    first = init_spec(cli, repo, tmp_path, warmup=1)
    second = init_spec(cli, repo, tmp_path, warmup=2)
    assert first.spec_hash != second.spec_hash

    error = refused(cli, "project", "confirm", "bench", "--repo", repo)
    assert error["code"] == "ambiguous_hash"
    assert set(error["details"]["matches"]) == {
        first.spec_hash,
        second.spec_hash,
    }
    assert "--spec-hash" in error["message"]
    assert workload(repo, "bench").spec_hash is None

    none = refused(
        cli,
        "project",
        "confirm",
        "bench",
        "--spec-hash",
        "0" * 8,
        "--repo",
        repo,
    )
    assert none["code"] == "not_found"

    bound = ok(
        cli,
        "project",
        "confirm",
        "bench",
        "--spec-hash",
        second.spec_hash[:12],
        "--repo",
        repo,
    )
    assert [(r["event"], r["id"]) for r in bound["records"]] == [
        ("confirmed", "bench")
    ]
    assert workload(repo, "bench").spec_hash == second.spec_hash
    assert workload_problem(second, repo) is None
    problem = workload_problem(first, repo)
    assert problem is not None
    assert "spec_changed" in problem


def test_confirming_a_workload_with_no_benchmark_binds_nothing(
    cli: Run, repo: str
) -> None:
    result = ok(cli, "project", "confirm", TARGET, "bench", "--repo", repo)
    assert {r["id"] for r in result["records"]} == {TARGET, "bench"}
    assert workload(repo, "bench").spec_hash is None


def test_a_workload_bound_to_one_spec_refuses_another_hash(
    cli: Run, repo: str, tmp_path: Path
) -> None:
    confirm_init_workload(cli, repo)
    first = init_spec(cli, repo, tmp_path, warmup=1)
    second = init_spec(cli, repo, tmp_path, warmup=2)
    ok(
        cli,
        "project",
        "confirm",
        "bench",
        "--spec-hash",
        first.spec_hash,
        "--repo",
        repo,
    )
    before = workload(repo, "bench")

    error = refused(
        cli,
        "project",
        "confirm",
        "bench",
        "--spec-hash",
        second.spec_hash,
        "--repo",
        repo,
    )
    assert error["code"] == "spec_already_bound"
    assert error["details"]["bound"] == first.spec_hash
    assert error["details"]["requested"] == second.spec_hash
    assert first.spec_hash in error["message"]
    assert second.spec_hash in error["message"]
    assert "add-workload bench --target" in error["message"]
    assert "--replace" in error["message"]
    assert workload(repo, "bench") == before

    same = ok(
        cli,
        "project",
        "confirm",
        "bench",
        "--spec-hash",
        first.spec_hash[:12],
        "--repo",
        repo,
    )
    assert same["records"] == []
    assert workload(repo, "bench") == before
