from datetime import timedelta
from pathlib import Path

import pytest
from project_helpers import (
    NOW,
    PYTHON_FILES,
    VERSION,
    MakeRepo,
    confirmed,
    proposed,
)

from perfproof.project import (
    NotConfirmedError,
    ProjectError,
    require_confirmed,
)
from perfproof.project.entries import find, put, record, with_fields
from perfproof.project.lifecycle import (
    GoalUpdate,
    add_constraint,
    confirm,
    retire,
    set_goal,
)
from perfproof.project.provenance import make_provenance
from perfproof.project.service import check_project
from perfproof.project.store import mutate
from perfproof.schema import (
    GoalEntry,
    ProjectHistoryRecord,
    ProjectModel,
    WorkloadEntry,
)

LATER = NOW + timedelta(hours=1)
HOST_A = "a" * 64
HOST_B = "b" * 64

type Changed = tuple[ProjectModel, list[ProjectHistoryRecord]]


def refused(repo: Path, **kwargs: object) -> NotConfirmedError:
    with pytest.raises(NotConfirmedError) as caught:
        require_confirmed(repo, "bench", **kwargs)  # type: ignore[arg-type]
    return caught.value


def reasons(error: NotConfirmedError) -> set[tuple[str, str]]:
    return {(p.entry_id, p.reason) for p in error.problems}


def test_a_confirmed_workload_is_accepted(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    ok = require_confirmed(repo, "bench")
    assert ok.workload.id == "bench"
    assert ok.goal.id == "goal-bench"
    assert ok.constraints == ()


def test_no_model_is_refused(make_repo: MakeRepo) -> None:
    error = refused(make_repo(PYTHON_FILES))
    assert error.code == "not_confirmed"
    assert reasons(error) == {("bench", "missing")}


def test_an_unknown_workload_is_refused(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    with pytest.raises(NotConfirmedError) as caught:
        require_confirmed(repo, "nope")
    assert reasons(caught.value) == {("nope", "missing")}


def test_a_proposed_workload_is_refused(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    proposed(repo)
    error = refused(repo)
    assert ("bench", "not_confirmed") in reasons(error)
    assert ("goal-bench", "not_confirmed") in reasons(error)


def test_a_stale_workload_is_refused(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    (repo / "uv.lock").write_text("version = 1\n")
    (repo / "bench.py").write_text(PYTHON_FILES["bench.py"] + "# n\n")
    check_project(repo, now=LATER, version=VERSION, host_id=lambda: HOST_A)
    # The check re-derived it with a different command, so it needs the
    # developer again: it is proposed, not measurable.
    error = refused(repo)
    assert ("bench", "not_confirmed") in reasons(error)
    assert error.details["workload"] == "bench"


def test_a_stale_status_is_named_with_its_trigger(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    (repo / "bench.py").unlink()
    check_project(repo, now=LATER, version=VERSION, host_id=lambda: HOST_A)
    error = refused(repo)
    (problem,) = [p for p in error.problems if p.entry_id == "bench"]
    assert problem.reason == "stale"
    assert "file_changed" in problem.detail
    assert "project check" in problem.detail


def test_a_file_changed_since_confirmation_is_refused_before_any_check(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    (repo / "bench.py").write_text("# someone edited this\n")
    error = refused(repo)
    assert reasons(error) == {("bench", "changed_since_confirmation")}


def test_an_unconfirmed_goal_is_refused(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    proposed(repo)
    mutate(
        repo, lambda m: confirm(m, ["target-python_cpu", "bench"], "s", NOW)
    )
    assert reasons(refused(repo)) == {("goal-bench", "not_confirmed")}


def test_a_workload_with_no_live_goal_is_refused(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    mutate(repo, lambda m: retire(m, "goal-bench", "no longer wanted", LATER))
    (problem,) = refused(repo).problems
    assert (problem.kind, problem.reason) == ("goal", "missing")
    assert "ask the developer" in problem.detail


def test_an_unconfirmed_constraint_blocks_every_workload(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    mutate(
        repo,
        lambda m: add_constraint(
            m,
            constraint="public_api",
            subject="demo.tile",
            rtol=None,
            atol=None,
            host_id=None,
            provenance=make_provenance((), (), VERSION, NOW),
            now=NOW,
        ),
    )
    error = refused(repo)
    assert reasons(error) == {("constraint-1", "not_confirmed")}
    mutate(repo, lambda m: confirm(m, ["constraint-1"], "s", LATER))
    assert require_confirmed(repo, "bench").constraints[0].id == "constraint-1"


def test_every_problem_is_listed_together(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    proposed(repo)
    mutate(
        repo,
        lambda m: add_constraint(
            m,
            constraint="public_api",
            subject="x",
            rtol=None,
            atol=None,
            host_id=None,
            provenance=make_provenance((), (), VERSION, NOW),
            now=NOW,
        ),
    )
    error = refused(repo)
    assert reasons(error) == {
        ("target-python_cpu", "not_confirmed"),
        ("bench", "not_confirmed"),
        ("goal-bench", "not_confirmed"),
        ("constraint-1", "not_confirmed"),
    }
    assert "bench" in str(error)
    assert len(error.details["problems"]) == 4  # type: ignore[arg-type]


def test_a_recorded_spec_must_match_the_one_measured(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)

    def freeze(m: ProjectModel) -> Changed:
        bench = find(m, "bench")
        assert isinstance(bench, WorkloadEntry)
        entry = with_fields(bench, spec_hash="d" * 64)
        return put(m, entry), [record("revalidated", entry, NOW)]

    mutate(repo, freeze)
    assert require_confirmed(repo, "bench", spec_hash="d" * 64)
    error = refused(repo, spec_hash="e" * 64)
    assert reasons(error) == {("bench", "spec_changed")}
    assert require_confirmed(repo, "bench")


def bind_goal_to(repo: Path, host: str) -> None:
    def bind(m: ProjectModel) -> Changed:
        m, a = set_goal(m, "bench", GoalUpdate(host_id=host), LATER, VERSION)
        m, b = confirm(m, ["goal-bench"], "s", LATER)
        return m, [*a, *b]

    mutate(repo, bind)


def test_a_goal_bound_to_another_machine_is_refused(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    bind_goal_to(repo, HOST_A)
    assert require_confirmed(repo, "bench", host_id=lambda: HOST_A)
    error = refused(repo, host_id=lambda: HOST_B)
    assert reasons(error) == {("goal-bench", "machine_differs")}


def test_an_unidentifiable_machine_is_refused_for_a_bound_goal(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    bind_goal_to(repo, HOST_A)
    error = refused(repo, host_id=lambda: None)
    assert reasons(error) == {("goal-bench", "machine_unknown")}


def test_the_machine_is_not_probed_when_nothing_is_bound(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)

    def boom() -> str | None:
        raise AssertionError("fingerprint was taken")

    assert require_confirmed(repo, "bench", host_id=boom)


def test_the_bar_is_named_default_or_goal(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    ok = require_confirmed(repo, "bench")
    assert ok.bar is None
    assert ok.bar_label(0.03) == "default bar (min_effect 0.03)"

    from perfproof.project.goals import parse_bar

    def stated(m: ProjectModel) -> Changed:
        m, a = set_goal(
            m, "bench", GoalUpdate(bar=parse_bar("ratio:1.25")), LATER, VERSION
        )
        m, b = confirm(m, ["goal-bench"], "s", LATER)
        return m, [*a, *b]

    mutate(repo, stated)
    assert require_confirmed(repo, "bench").bar_label(0.03) == (
        "goal bar (ratio 1.25)"
    )


def test_an_unreadable_model_is_not_confirmation(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    (repo / ".perfproof" / "project.json").write_text("{")
    with pytest.raises(ProjectError) as caught:
        require_confirmed(repo, "bench")
    assert caught.value.code == "invalid_model"


def test_types_of_what_is_returned(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    ok = require_confirmed(repo, "bench")
    assert isinstance(ok.workload, WorkloadEntry)
    assert isinstance(ok.goal, GoalEntry)
