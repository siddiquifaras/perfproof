"""The rules of docs/find.md: question, next steps and done."""

from collections.abc import Sequence
from typing import Any

import pytest
from find_helpers import make_part, record

from perfproof.find import plan
from perfproof.find.plan import Asks, ProbeView
from perfproof.schema import Lead

NONE = Asks((), (), ())


def view(
    key: str = "find-tests-root",
    kind: str = "tests",
    status: str | None = "done",
    reason: str | None = None,
    requirement: bool = True,
) -> ProbeView:
    rec = None if status is None else record(key, status, reason=reason)
    return ProbeView(key, kind, "root", requirement, rec)


def lead(name: str = "hot", via_test: str | None = "tests/t.py::t") -> Lead:
    return Lead(
        kind="function",
        name=name,
        file="a.py",
        part="root",
        cap=2.0,
        seen_in=("find-tests-root",),
        via_test=via_test,
    )


def decide(depth: str = "medium", **over: Any) -> plan.Decision:
    args: dict[str, Any] = {
        "depth": depth,
        "asked": NONE,
        "views": [view()],
        "workloads": ["w1", "w2", "w3"],
        "knobs": ["w1"],
        "uncovered_entries": [],
        "entry_points": 2,
        "covered_entries": 2,
        "distinct_tops": 2,
        "leads": [],
        "waivers": {},
    }
    args.update(over)
    return plan.decide(**args)


def codes(d: plan.Decision) -> list[str]:
    return [s.code for s in d.next_steps]


def test_medium_is_done_when_every_ask_is_met() -> None:
    d = decide()
    assert d.done
    assert d.question is None
    assert d.next_steps == ()


@pytest.mark.parametrize(
    ("depth", "workloads", "covered", "short"),
    [
        ("light", 1, 0, False),
        ("light", 0, 0, True),
        ("medium", 3, 2, False),
        ("medium", 2, 2, True),
        ("medium", 3, 1, True),
        ("deep", 1, 2, False),
        ("deep", 1, 1, True),
    ],
)
def test_workloads_asked_by_depth(
    depth: str, workloads: int, covered: int, short: bool
) -> None:
    d = decide(
        depth,
        workloads=[f"w{i}" for i in range(workloads)],
        knobs=["w0"] if workloads else [],
        covered_entries=covered,
        entry_points=2,
    )
    assert ("define_workloads" in codes(d)) is short
    assert d.done is (not short)


def test_define_workloads_names_entry_points_and_leads() -> None:
    d = decide(
        workloads=[],
        knobs=[],
        covered_entries=0,
        uncovered_entries=["root:serve"],
        leads=[lead("hot_loop")],
    )
    message = d.next_steps[0].message
    assert "root:serve" in message
    assert "hot_loop" in message


def test_size_knob_rules() -> None:
    assert "add_size_knob" not in codes(decide("light", knobs=[]))
    assert "add_size_knob" in codes(decide("medium", knobs=[]))
    assert "add_size_knob" not in codes(decide("medium", knobs=["w2"]))
    d = decide("deep", knobs=["w1"], covered_entries=2)
    assert "add_size_knob" in codes(d)
    assert "w2" in d.next_steps[0].message


def test_a_knob_for_an_undefined_workload_does_not_count() -> None:
    assert "add_size_knob" in codes(decide("medium", knobs=["gone"]))


def test_waiver_meets_define_workloads_and_size_knob() -> None:
    d = decide(
        workloads=["w1"],
        knobs=[],
        covered_entries=0,
        waivers={
            "define_workloads": "only one kind of operation exists",
            "add_size_knob": "no size input",
        },
    )
    assert d.done
    assert codes(d) == []


def test_steps_come_in_priority_order() -> None:
    asked = Asks((("t", "python_cpu"),), (), ())
    d = decide(
        asked=asked,
        workloads=["w1"],
        knobs=[],
        covered_entries=0,
        views=[view(status="done"), view("find-bench-x", "bench", None)],
        leads=[lead()],
    )
    assert codes(d) == [
        "install_or_confirm",
        "define_workloads",
        "add_size_knob",
        "reach_lead",
        "continue_find",
    ]
    assert not d.done


def test_a_pending_probe_means_continue_find_and_not_done() -> None:
    d = decide(views=[view(status=None)])
    assert codes(d) == ["continue_find"]
    assert d.next_steps[0].command == (
        "perfproof",
        "find",
        "--depth",
        "medium",
    )
    assert not d.done


def test_an_open_question_blocks_done() -> None:
    d = decide(asked=Asks((), (("root", ("pytest",), 300.0),), ()))
    assert not d.done
    assert codes(d) == ["install_or_confirm"]
    assert d.next_steps[0].command == (
        "perfproof",
        "find",
        "--confirm-tests",
        "root",
    )


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("skipped_no_tests", "no_tests: --no-tests"),
        ("failed", "recorder_unavailable: perf_event_open refused"),
        ("tree_modified", "tests_modified_tree: leftover.txt"),
        ("timeout", "timeout: workload_failed"),
        ("failed", "failed: workload_failed: exited 1"),
    ],
)
def test_mechanical_reasons_waive_a_probe(status: str, reason: str) -> None:
    d = decide(views=[view(status=status, reason=reason)])
    assert d.done


def test_skipped_unconfirmed_does_not_waive() -> None:
    d = decide(views=[view(status="skipped_unconfirmed")])
    assert not d.done


def test_an_extra_workload_failing_does_not_block_done() -> None:
    d = decide(
        views=[
            view(status="done"),
            view("w4", "workload", "failed", "failed: x", requirement=False),
        ]
    )
    assert d.done


def test_reach_lead_names_the_test_and_does_not_block_done() -> None:
    d = decide(leads=[lead()])
    assert d.done
    assert codes(d) == ["reach_lead"]
    assert "tests/t.py::t" in d.next_steps[0].message


def test_reach_lead_is_waived_by_no_tests_and_by_the_agent() -> None:
    no_tests = [view(status="skipped_no_tests", reason="no_tests: x")]
    assert "reach_lead" not in codes(decide(views=no_tests, leads=[lead()]))
    waived = decide(leads=[lead()], waivers={"reach_lead": "unreachable"})
    assert "reach_lead" not in codes(waived)
    assert "reach_lead" not in codes(decide("light", leads=[lead()]))


def test_reach_lead_without_a_test_names_the_probe() -> None:
    d = decide(leads=[lead(via_test=None)])
    assert "find-tests-root" in d.next_steps[0].message


def test_depth_budgets() -> None:
    assert plan.DEPTH_BUDGET_SECONDS == {
        "light": 300,
        "medium": 900,
        "deep": 2400,
    }


def parts_for_asks() -> Sequence[Any]:
    return [
        make_part("a", target_id="t-a"),
        make_part(
            "b",
            target_id="t-b",
            target_class="gpu_kernel",
            coverage="missing",
            coverage_install=("uv", "pip", "install", "cov"),
        ),
        make_part(
            "c",
            coverage="missing",
            coverage_install=("uv", "pip", "install", "cov"),
        ),
        make_part(
            "d",
            target_class=None,
            coverage="missing",
            coverage_install=("uv", "pip", "install", "cov"),
            test=None,
        ),
    ]


def ask(depth: str = "medium", **over: Any) -> Asks:
    args: dict[str, Any] = {
        "depth": depth,
        "unconfirmed_targets": {"t-a": "python_cpu"},
        "confirmed_tests": {},
        "no_tests": False,
        "test_timeout": 300.0,
    }
    args.update(over)
    return plan.asks(parts_for_asks(), **args)


def test_asks_gathers_targets_tests_and_installs() -> None:
    a = ask()
    assert a.targets == (("t-a", "python_cpu"),)
    assert [t[0] for t in a.tests] == ["a", "b", "c"]
    assert [i[0] for i in a.installs] == ["c"]
    question = a.question()
    assert question is not None
    assert "t-a is python_cpu" in question
    assert "-m pytest -q" in question
    assert "up to 300 s" in question
    assert "uv pip install cov" in question
    assert question.count("?") == 1


def test_asks_skips_gpu_and_unknown_class_installs() -> None:
    installed = [p[0] for p in ask().installs]
    assert "b" not in installed
    assert "d" not in installed


def test_asks_light_and_no_tests_ask_no_test_command() -> None:
    assert ask("light").tests == ()
    assert ask(no_tests=True).tests == ()


def test_a_confirmation_holds_for_the_same_argv_only() -> None:
    import sys

    same = (sys.executable, "-m", "pytest", "-q")
    assert [t[0] for t in ask(confirmed_tests={"a": same}).tests] == ["b", "c"]
    changed = {"a": (*same, "-x")}
    assert "a" in [t[0] for t in ask(confirmed_tests=changed).tests]


def test_nothing_to_ask_is_a_null_question() -> None:
    assert NONE.question() is None
    assert NONE.empty


def test_install_command_is_given_when_it_is_the_only_ask() -> None:
    only = Asks((), (), (("c", ("uv", "pip", "install", "cov")),))
    assert only.command() == ("uv", "pip", "install", "cov")
    mixed = Asks((("t", "python_cpu"),), (), only.installs)
    assert mixed.command() is None
    assert "perfproof project confirm t" in mixed.how()
