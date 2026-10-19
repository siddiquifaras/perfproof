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

from perfproof.project import store
from perfproof.project.entries import EVENT_STATUS, find, with_fields
from perfproof.project.errors import ProjectError
from perfproof.project.goals import (
    bar_label,
    default_guardrails,
    parse_bar,
    parse_guardrail,
)
from perfproof.project.lifecycle import (
    GoalUpdate,
    add_constraint,
    confirm,
    retire,
    set_goal,
)
from perfproof.project.provenance import make_provenance
from perfproof.schema import (
    ConstraintEntry,
    GoalEntry,
    ProjectModel,
    WorkloadEntry,
)

LATER = NOW + timedelta(hours=1)


def model_of(repo: Path) -> ProjectModel:
    model = store.load_model(repo)
    assert model is not None
    return model


def goal_of(model: ProjectModel, wid: str = "bench") -> GoalEntry:
    return next(g for g in model.goals if g.workload_id == wid)


def workload(model: ProjectModel, wid: str = "bench") -> WorkloadEntry:
    entry = find(model, wid)
    assert isinstance(entry, WorkloadEntry)
    return entry


# Propose and confirm


def test_propose_then_confirm(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    model = proposed(repo)
    assert {e.status for e in model.workloads} == {"proposed"}
    after, records = confirm(
        model, ["target-python_cpu", "bench"], "sess-1", LATER
    )
    entry = workload(after)
    assert entry.status == "confirmed"
    assert entry.confirmation is not None
    assert entry.confirmation.session == "sess-1"
    assert entry.confirmation.confirmed_at == LATER
    assert entry.version == 1
    assert [r.entry.id for r in records] == ["target-python_cpu", "bench"]


def test_confirming_twice_writes_nothing(make_repo: MakeRepo) -> None:
    model = confirmed(make_repo(PYTHON_FILES))
    again, records = confirm(model, ["bench"], "s", LATER)
    assert records == []
    assert again == model


def test_init_again_adds_only_what_is_new(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    (repo / "bench_two.py").write_text(PYTHON_FILES["bench.py"])
    again = proposed(repo)
    assert workload(again).status == "confirmed"
    assert workload(again, "bench_two").status == "proposed"
    assert len(again.workloads) == 2


def test_init_never_revives_a_retired_entry(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    model = proposed(repo)
    model, records = retire(model, "bench", "not real use", NOW, cascade=True)
    assert [r.event for r in records] == ["retired", "retired"]
    store.mutate(repo, lambda m: (model, records))
    again = proposed(repo)
    assert workload(again).status == "retired"
    assert goal_of(again).status == "retired"


# Unknown goals are asked, never filled


def test_an_unknown_goal_cannot_be_confirmed(make_repo: MakeRepo) -> None:
    model = proposed(make_repo(PYTHON_FILES))
    model, _ = confirm(model, ["target-python_cpu", "bench"], "s", NOW)
    with pytest.raises(ProjectError) as caught:
        confirm(model, ["goal-bench"], "s", NOW)
    assert caught.value.code == "goal_unknown"
    assert caught.value.details["unknown"] == [
        "metric",
        "direction",
        "unit",
        "guardrails",
    ]


def test_a_partly_known_goal_keeps_the_rest_unknown(
    make_repo: MakeRepo,
) -> None:
    model = proposed(make_repo(PYTHON_FILES))
    model, _ = set_goal(
        model, "bench", GoalUpdate(metric="throughput"), NOW, VERSION
    )
    goal = goal_of(model)
    assert goal.metric == "throughput"
    assert goal.direction == "higher_is_better"
    assert goal.unit is None
    assert "not fixed by its name" in goal.missing["unit"]
    assert goal.bar is None
    model, _ = confirm(model, ["target-python_cpu", "bench"], "s", NOW)
    with pytest.raises(ProjectError) as caught:
        confirm(model, ["goal-bench"], "s", NOW)
    assert caught.value.details["unknown"] == ["unit"]


def test_a_goal_is_confirmed_only_after_its_workload(
    make_repo: MakeRepo,
) -> None:
    model = proposed(make_repo(PYTHON_FILES))
    model, _ = set_goal(
        model, "bench", GoalUpdate(metric="wall_time"), NOW, VERSION
    )
    with pytest.raises(ProjectError) as caught:
        confirm(model, ["goal-bench"], "s", NOW)
    assert caught.value.code == "workload_not_confirmed"
    after, _ = confirm(
        model, ["target-python_cpu", "bench", "goal-bench"], "s", NOW
    )
    assert goal_of(after).status == "confirmed"


def test_known_metrics_fill_direction_and_unit_not_the_bar(
    make_repo: MakeRepo,
) -> None:
    model = proposed(make_repo(PYTHON_FILES))
    model, _ = set_goal(
        model, "bench", GoalUpdate(metric="wall_time"), NOW, VERSION
    )
    goal = goal_of(model)
    assert (goal.direction, goal.unit) == ("lower_is_better", "s")
    assert goal.bar is None
    assert set(goal.missing) == {"bar"}


# Default guardrails and the default bar's name


def test_default_guardrails_follow_the_class_and_leave_out_the_primary() -> (
    None
):
    python = default_guardrails("python_cpu", "wall_time")
    assert [(g.metric, g.allowance, g.allowance_source) for g in python] == [
        ("peak_memory", 0.10, "default")
    ]
    web = default_guardrails("web_service", "p99_latency")
    assert [g.metric for g in web] == ["peak_memory"]
    serving = default_guardrails("llm_serving", "throughput")
    assert [(g.metric, g.allowance) for g in serving] == [
        ("peak_memory", 0.10),
        ("peak_gpu_memory", 0.10),
        ("p99_latency", 0.05),
    ]


def test_goal_gets_default_guardrails_with_their_source(
    make_repo: MakeRepo,
) -> None:
    model = proposed(make_repo(PYTHON_FILES))
    model, _ = set_goal(
        model, "bench", GoalUpdate(metric="wall_time"), NOW, VERSION
    )
    rails_now = goal_of(model).guardrails or ()
    assert len(rails_now) == 1
    rail = rails_now[0]
    assert (rail.metric, rail.allowance, rail.allowance_source) == (
        "peak_memory",
        0.10,
        "default",
    )


def test_project_guardrails_override_and_cannot_guard_the_goal_metric(
    make_repo: MakeRepo,
) -> None:
    model = proposed(make_repo(PYTHON_FILES))
    rails = (parse_guardrail("memory=0.05"),)
    model, _ = set_goal(
        model,
        "bench",
        GoalUpdate(metric="wall_time", guardrails=rails),
        NOW,
        VERSION,
    )
    rails_now = goal_of(model).guardrails or ()
    assert len(rails_now) == 1
    rail = rails_now[0]
    assert (rail.allowance, rail.allowance_source) == (0.05, "project")
    with pytest.raises(ProjectError) as caught:
        set_goal(
            model,
            "bench",
            GoalUpdate(
                guardrails=(parse_guardrail("wall_time=0.1"),),
            ),
            NOW,
            VERSION,
        )
    assert caught.value.code == "guardrail_conflict"


def test_the_default_bar_is_named_and_never_written_into_the_goal(
    make_repo: MakeRepo,
) -> None:
    model = proposed(make_repo(PYTHON_FILES))
    model, _ = set_goal(
        model, "bench", GoalUpdate(metric="wall_time"), NOW, VERSION
    )
    assert goal_of(model).bar is None
    assert bar_label(None, 0.03) == "default bar (min_effect 0.03)"
    assert bar_label(parse_bar("ratio:1.2"), 0.03) == "goal bar (ratio 1.2)"
    with pytest.raises(ValueError, match="above 1"):
        parse_bar("ratio:0.9")


# Versions: bump exactly when the claim changes


def test_version_bumps_on_a_changed_claim_only(make_repo: MakeRepo) -> None:
    model = proposed(make_repo(PYTHON_FILES))
    model, _ = set_goal(
        model, "bench", GoalUpdate(metric="wall_time"), NOW, VERSION
    )
    assert goal_of(model).version == 2
    same, records = set_goal(
        model, "bench", GoalUpdate(metric="wall_time"), LATER, VERSION
    )
    assert records == []
    assert same == model
    bar, records = set_goal(
        model,
        "bench",
        GoalUpdate(bar=parse_bar("ratio:1.1")),
        LATER,
        VERSION,
    )
    assert goal_of(bar).version == 3
    assert [r.event for r in records] == ["edited"]


def test_confirming_and_retiring_do_not_bump_the_version(
    make_repo: MakeRepo,
) -> None:
    model = confirmed(make_repo(PYTHON_FILES))
    assert workload(model).version == 1
    retired, _ = retire(model, "goal-bench", "x", LATER)
    assert find(retired, "goal-bench").version == 2  # type: ignore[union-attr]


# History statuses


def test_created_and_edited_lines_carry_proposed_entries_only(
    make_repo: MakeRepo,
) -> None:
    assert EVENT_STATUS["created"] == {"proposed"}
    assert EVENT_STATUS["edited"] == {"proposed"}
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    model = model_of(repo)
    edited, records = set_goal(
        model, "bench", GoalUpdate(bar=parse_bar("ratio:1.5")), LATER, VERSION
    )
    assert goal_of(edited).status == "proposed"
    assert goal_of(edited).confirmation is None
    assert [r.event for r in records] == ["edited"]
    history, _ = store.read_history(repo)
    for r in history:
        if r.event in ("created", "edited"):
            assert r.entry.status == "proposed"


# Cross-entry consistency


def test_retiring_a_workload_needs_cascade_and_retires_its_goal(
    make_repo: MakeRepo,
) -> None:
    model = confirmed(make_repo(PYTHON_FILES))
    with pytest.raises(ProjectError) as caught:
        retire(model, "bench", "gone", LATER)
    assert caught.value.code == "has_dependents"
    after, records = retire(model, "bench", "gone", LATER, cascade=True)
    assert workload(after).status == "retired"
    assert goal_of(after).status == "retired"
    assert "bench retired: gone" in (goal_of(after).retired_reason or "")
    assert [r.entry.id for r in records] == ["goal-bench", "bench"]


def test_a_confirmed_goal_on_a_retired_workload_cannot_exist(
    make_repo: MakeRepo,
) -> None:
    model = confirmed(make_repo(PYTHON_FILES))
    bench = with_fields(
        workload(model), status="retired", retired_reason="gone"
    )
    broken = ProjectModel(
        updated_at=NOW,
        targets=model.targets,
        workloads=(bench,),
        goals=model.goals,
        how_to_run=model.how_to_run,
    )
    from perfproof.project.entries import check_consistency

    with pytest.raises(ProjectError) as caught:
        check_consistency(broken)
    assert caught.value.code == "inconsistent_model"
    assert "goal-bench is live on a retired workload" in str(caught.value)


def test_retiring_a_target_cascades_through_workloads_and_goals(
    make_repo: MakeRepo,
) -> None:
    model = confirmed(make_repo(PYTHON_FILES))
    after, records = retire(
        model, "target-python_cpu", "gone", LATER, cascade=True
    )
    assert {r.entry.id for r in records} == {
        "goal-bench",
        "bench",
        "target-python_cpu",
    }
    assert all(e.status == "retired" for e in (*after.targets, *after.goals))


def test_a_confirmed_goal_of_a_stale_workload_is_allowed_but_unusable(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    model = confirmed(repo)
    (repo / "bench.py").write_text("# changed\n")
    from perfproof.project.staleness import find_stale, mark_findings

    findings, _ = find_stale(repo, model, host_id=None)
    stale, _ = mark_findings(model, findings, LATER)
    assert workload(stale).status == "stale"
    assert goal_of(stale).status == "confirmed"


def test_two_live_goals_for_one_workload_are_refused(
    make_repo: MakeRepo,
) -> None:
    model = proposed(make_repo(PYTHON_FILES))
    twin = with_fields(goal_of(model), id="goal-other")
    from perfproof.project.entries import check_consistency, put

    with pytest.raises(ProjectError) as caught:
        check_consistency(put(model, twin))
    assert "two live goals" in str(caught.value)


# Constraints, dependents


def test_constraints_start_proposed_and_do_not_duplicate(
    make_repo: MakeRepo,
) -> None:
    model = proposed(make_repo(PYTHON_FILES))
    prov = make_provenance((), (), VERSION, NOW)
    kwargs = {
        "constraint": "public_api",
        "subject": "demo.tile",
        "rtol": None,
        "atol": None,
        "host_id": None,
        "provenance": prov,
        "now": NOW,
    }
    model, records = add_constraint(model, **kwargs)  # type: ignore[arg-type]
    (c,) = model.constraints
    assert (c.id, c.status) == ("constraint-1", "proposed")
    assert [r.event for r in records] == ["created"]
    with pytest.raises(ProjectError) as caught:
        add_constraint(model, **kwargs)  # type: ignore[arg-type]
    assert caught.value.code == "duplicate"


def test_a_tolerance_states_both_bounds(make_repo: MakeRepo) -> None:
    model = proposed(make_repo(PYTHON_FILES))
    with pytest.raises(ProjectError) as caught:
        add_constraint(
            model,
            constraint="numeric_tolerance",
            subject="outputs",
            rtol=1e-6,
            atol=None,
            host_id=None,
            provenance=make_provenance((), (), VERSION, NOW),
            now=NOW,
        )
    assert caught.value.code == "invalid_entry"


def test_editing_a_goal_stales_a_confirmed_workload_with_a_frozen_spec(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    model = confirmed(repo)
    frozen = with_fields(workload(model), spec_hash="a" * 64)
    from perfproof.project.entries import put

    model = put(model, frozen)
    edited, records = set_goal(
        model, "bench", GoalUpdate(bar=parse_bar("ratio:2")), LATER, VERSION
    )
    entry = workload(edited)
    assert entry.status == "stale"
    assert entry.invalidation is not None
    assert entry.invalidation.trigger == "dependency_edited"
    assert "goal-bench" in entry.invalidation.detail
    assert [r.event for r in records] == ["edited", "marked_stale"]
    # A workload with no frozen spec depends on no goal.
    plain, _ = set_goal(
        confirmed(make_repo(PYTHON_FILES)),
        "bench",
        GoalUpdate(bar=parse_bar("ratio:2")),
        LATER,
        VERSION,
    )
    assert workload(plain).status == "confirmed"


def test_a_constraint_edit_stales_every_frozen_workload(
    make_repo: MakeRepo,
) -> None:
    model = confirmed(make_repo(PYTHON_FILES))
    from perfproof.project.entries import put

    model = put(model, with_fields(workload(model), spec_hash="b" * 64))
    after, _ = add_constraint(
        model,
        constraint="forbidden_dependency",
        subject="pandas",
        rtol=None,
        atol=None,
        host_id=None,
        provenance=make_provenance((), (), VERSION, LATER),
        now=LATER,
    )
    assert workload(after).status == "stale"
    assert isinstance(after.constraints[0], ConstraintEntry)


def test_stale_goal_can_be_reconfirmed_but_not_a_stale_workload(
    make_repo: MakeRepo,
) -> None:
    from perfproof.project.entries import mark_stale, put

    model = confirmed(make_repo(PYTHON_FILES))
    stale_goal = mark_stale(goal_of(model), "dependency_edited", "x", LATER)
    stale_workload = mark_stale(workload(model), "file_changed", "y", LATER)
    with pytest.raises(ProjectError) as caught:
        confirm(put(model, stale_workload), ["bench"], "s", LATER)
    assert caught.value.code == "stale_needs_revalidation"
    model = put(model, stale_goal)
    again, records = confirm(model, ["goal-bench"], "s2", LATER)
    assert goal_of(again).status == "confirmed"
    assert goal_of(again).version == goal_of(model).version
    assert [r.event for r in records] == ["confirmed"]
