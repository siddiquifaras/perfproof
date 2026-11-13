"""One find call: budget, resume, the confirm gate and the planning.

`run_probe` is replaced by a recorder that never profiles; the real
profiling is in test_find_integration.py. What is tested here is what
`find` decides to run and with what.
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pytest
from find_helpers import copy_fixture, make_part

from perfproof.commands import CommandError
from perfproof.find import engine, specs, state
from perfproof.find.engine import FindRequest, FindRun
from perfproof.find.probes import Outcome, PlannedProbe
from perfproof.find.state import load_state
from perfproof.schema import Part, ProbeRecord, SizeKnobSpec


class Fake:
    """A `run_probe` that records what it was asked and never runs."""

    def __init__(self, spend: float = 1.0, status: str = "done") -> None:
        self.calls: list[PlannedProbe] = []
        self.spend = spend
        self.status = status
        self.remaining: list[float] = []

    def __call__(
        self,
        root: Path,
        planned: PlannedProbe,
        *,
        tree: str,
        remaining_seconds: float,
        clock: Callable[[], float],
    ) -> Outcome:
        del root, clock
        self.remaining.append(remaining_seconds)
        if remaining_seconds < 1:
            return Outcome(None, 0.0)
        self.calls.append(planned)
        status = "failed" if planned.blocked else self.status
        rec = ProbeRecord.model_validate(
            {
                "key": planned.key,
                "requirement": planned.requirement,
                "spec_hash": planned.spec.spec_hash,
                "tree_id": tree,
                "status": status,
                "run_ids": [],
                "reason": planned.blocked,
                "log_tail": None,
            }
        )
        return Outcome(rec, self.spend)

    @property
    def keys(self) -> list[str]:
        return [c.key for c in self.calls]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return copy_fixture("py_tests", tmp_path / "proj")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    f = Fake()
    monkeypatch.setattr(engine, "run_probe", f)
    return f


def find(
    repo: Path,
    parts: Sequence[Part],
    depth: str = "medium",
    **over: Any,
) -> FindRun:
    args: dict[str, Any] = {
        "root": repo,
        "depth": depth,
        "budget_seconds": None,
        "size_knobs": {},
        "no_tests": False,
        "test_timeout": 300.0,
        "confirm_tests": (),
        "waive": {},
    }
    args.update(over)
    return engine.run_find(FindRequest(**args), detect=lambda _r, _m: parts)


def codes(run: FindRun) -> list[str]:
    return [s.code for s in run.result.next_steps]


def test_light_profiles_a_defined_workload_and_runs_no_test_probe(
    repo: Path, fake: Fake
) -> None:
    define_workload(repo, "w1")
    native = {
        "kind": "setuptools_ext",
        "name": "n",
        "own_module": "n.so",
        "sources": ["n.c"],
    }
    bench = {"kind": "script", "name": "b", "argv": ["python"], "cwd": "."}
    run = find(
        repo, [make_part(native=(native,), benchmarks=(bench,))], "light"
    )
    assert fake.keys == ["w1"]
    assert run.result.depth == "light"
    assert run.result.budget.limit_seconds == 300
    assert codes(run) == []
    assert run.result.done


def test_light_with_no_workload_asks_for_one(repo: Path, fake: Fake) -> None:
    run = find(repo, [make_part()], "light")
    assert fake.calls == []
    assert codes(run) == ["define_workloads"]


def test_the_test_command_is_not_run_before_it_is_confirmed(
    repo: Path, fake: Fake
) -> None:
    run = find(repo, [make_part()])
    assert fake.calls == []
    question = run.result.question
    assert question is not None
    assert "-m pytest -q" in question
    assert codes(run)[0] == "install_or_confirm"
    assert not run.result.done
    state = load_state(repo)
    assert state is not None
    assert [(p.key, p.status) for p in state.probes] == [
        ("find-tests-root", "skipped_unconfirmed")
    ]
    assert [s.status for s in run.result.skipped] == ["skipped_unconfirmed"]


def test_confirming_runs_it_with_the_contract_spec(
    repo: Path, fake: Fake
) -> None:
    run = find(repo, [make_part()], confirm_tests=("root",))
    assert fake.keys == ["find-tests-root"]
    planned = fake.calls[0]
    assert planned.define
    assert planned.spec.harness == ("pyproject.toml",)
    assert planned.spec.timeout_seconds == 300.0
    assert planned.own_timeout == 300.0
    assert planned.template == ()
    assert run.result.question is None
    state = load_state(repo)
    assert state is not None
    assert dict(state.confirmed_tests) == {
        "root": tuple(make_part().test_command.value.argv)  # type: ignore[union-attr]
    }


def test_a_changed_test_command_needs_a_new_confirmation(
    repo: Path, fake: Fake
) -> None:
    find(repo, [make_part()], confirm_tests=("root",))
    fake.calls.clear()
    changed = make_part(test=("python", "-m", "pytest", "-x"))
    run = find(repo, [changed])
    assert fake.calls == []
    assert run.result.question is not None
    assert "-x" in run.result.question


def test_no_tests_skips_the_probe_with_a_mechanical_reason(
    repo: Path, fake: Fake
) -> None:
    run = find(repo, [make_part()], no_tests=True)
    assert fake.calls == []
    assert run.result.question is None
    (skipped,) = run.result.skipped
    assert skipped.status == "skipped_no_tests"
    assert (skipped.reason or "").startswith("no_tests")


def test_a_part_without_a_test_command_is_waived_by_no_tests(
    repo: Path, fake: Fake
) -> None:
    run = find(repo, [make_part(test=None)])
    assert fake.calls == []
    (skipped,) = run.result.skipped
    assert (skipped.reason or "").startswith("no_tests: no test command")


def test_confirm_tests_refuses_unknown_parts_and_missing_commands(
    repo: Path, fake: Fake
) -> None:
    with pytest.raises(CommandError) as unknown:
        find(repo, [make_part()], confirm_tests=("nope",))
    assert unknown.value.code == "unknown_part"
    with pytest.raises(CommandError) as missing:
        find(repo, [make_part(test=None)], confirm_tests=("root",))
    assert missing.value.code == "no_test_command"


def bench_part() -> Part:
    return make_part(
        benchmarks=tuple(
            {
                "kind": "script",
                "name": name,
                "argv": ["python", f"{name}.py"],
                "cwd": ".",
            }
            for name in ("b1", "b2", "b3")
        )
    )


def test_the_budget_stops_the_call_and_the_next_call_resumes(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = Fake(spend=500.0)
    monkeypatch.setattr(engine, "run_probe", fake)
    first = find(
        repo, [bench_part()], budget_seconds=900, confirm_tests=("root",)
    )
    assert fake.keys == ["find-tests-root", "find-bench-root-b1"]
    assert fake.remaining[:3] == [900.0, 400.0, -100.0]
    assert first.result.budget.exhausted
    assert first.result.budget.spent_seconds == 1000.0
    assert codes(first)[-1] == "continue_find"
    assert not first.result.done
    fake.calls.clear()
    second = find(repo, [bench_part()], budget_seconds=900)
    assert fake.keys == ["find-bench-root-b2", "find-bench-root-b3"]
    assert not second.result.budget.exhausted
    assert "continue_find" not in codes(second)
    state = load_state(repo)
    assert state is not None
    assert len(state.calls) == 2
    assert state.calls[0].spent_seconds >= 0


def test_a_probe_whose_tree_is_unchanged_is_not_run_again(
    repo: Path, fake: Fake
) -> None:
    find(repo, [make_part()], confirm_tests=("root",))
    assert fake.keys == ["find-tests-root"]
    find(repo, [make_part()])
    assert fake.keys == ["find-tests-root"]


def test_a_changed_tree_makes_the_probe_stale(repo: Path, fake: Fake) -> None:
    find(repo, [make_part()], confirm_tests=("root",))
    (repo / "app" / "work.py").write_text("X = 1\n")
    find(repo, [make_part()])
    assert fake.keys == ["find-tests-root", "find-tests-root"]
    assert fake.calls[0].stale is None
    assert fake.calls[1].stale is not None


def test_a_changed_test_timeout_makes_the_probe_stale(
    repo: Path, fake: Fake
) -> None:
    find(repo, [make_part()], confirm_tests=("root",))
    find(repo, [make_part()], test_timeout=60.0)
    assert fake.keys == ["find-tests-root", "find-tests-root"]
    assert fake.calls[1].own_timeout == 60.0


def test_a_failed_probe_is_listed_and_waived(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "run_probe", Fake(status="failed"))
    run = find(repo, [make_part()], confirm_tests=("root",))
    assert [s.status for s in run.result.skipped] == ["failed"]
    assert "continue_find" not in codes(run)


def test_native_code_adds_the_native_reprofile(repo: Path, fake: Fake) -> None:
    native = {
        "kind": "setuptools_ext",
        "name": "fastsum",
        "own_module": "fastsum.so",
        "sources": ["fastsum.c"],
    }
    run = find(repo, [make_part(native=(native,))], confirm_tests=("root",))
    assert fake.keys == ["find-tests-root", "find-tests-root@native"]
    planned = fake.calls[1]
    assert not planned.define
    assert planned.own_modules == ("fastsum.so",)
    assert planned.template[1:4] == (
        "-m",
        "perfproof.profilers.native",
        "record",
    )
    assert planned.spec.spec_hash == fake.calls[0].spec.spec_hash
    assert "continue_find" not in codes(run)


def test_the_native_recorder_being_unavailable_waives_it(
    repo: Path, fake: Fake
) -> None:
    native = {
        "kind": "setuptools_ext",
        "name": "fastsum",
        "own_module": "fastsum.so",
        "sources": ["fastsum.c"],
    }
    part = make_part(native=(native,), recorder=False)
    run = find(repo, [part], confirm_tests=("root",))
    assert fake.calls[1].blocked is not None
    assert fake.calls[1].blocked.startswith("recorder_unavailable")
    skipped = {s.key: s for s in run.result.skipped}
    assert "perf_event_open" in (
        skipped["find-tests-root@native"].reason or ""
    )


def test_a_c_part_is_profiled_with_the_native_recorder_once(
    repo: Path, fake: Fake
) -> None:
    part = make_part(language="c", test=("make", "test"))
    find(repo, [part], confirm_tests=("root",))
    assert fake.keys == ["find-tests-root"]
    assert fake.calls[0].template[2] == "perfproof.profilers.native"


def test_a_node_part_uses_the_node_profiler(repo: Path, fake: Fake) -> None:
    part = make_part(language="javascript", test=("npm", "test"))
    find(repo, [part], confirm_tests=("root",))
    assert fake.calls[0].template[2] == "perfproof.profilers.node"


def test_an_unknown_language_fails_the_probe_with_the_reason(
    repo: Path, fake: Fake
) -> None:
    part = make_part(language="other", test=("make", "test"))
    run = find(repo, [part], confirm_tests=("root",))
    (skipped,) = run.result.skipped
    assert "no profiler is known for other" in (skipped.reason or "")


def test_existing_benchmarks_come_after_tests(repo: Path, fake: Fake) -> None:
    find(repo, [bench_part()], confirm_tests=("root",))
    assert fake.keys[0] == "find-tests-root"
    assert fake.keys[1:] == [f"find-bench-root-b{i}" for i in (1, 2, 3)]
    assert fake.calls[1].own_timeout == 300.0


def define_workload(repo: Path, name: str) -> None:
    specs.define_spec(
        repo,
        specs.build_spec(
            name=name,
            command=("python", "-m", "app.cli"),
            cwd=".",
            harness="pyproject.toml",
            target_class="python_cpu",
            timeout_seconds=120.0,
        ),
    )


def test_a_size_knob_for_an_undefined_workload_is_refused(
    repo: Path, fake: Fake
) -> None:
    knob = SizeKnobSpec(kind="arg", name="--n", base=10)
    with pytest.raises(CommandError) as raised:
        find(repo, [make_part()], size_knobs={"nope": knob})
    assert raised.value.code == "not_found"


def test_user_workloads_are_profiled_sized_when_a_knob_is_stored(
    repo: Path, fake: Fake
) -> None:
    for name in ("w1", "w2"):
        define_workload(repo, name)
    knob = SizeKnobSpec(kind="arg", name="--n", base=10)
    find(
        repo,
        [make_part()],
        no_tests=True,
        size_knobs={"w2": knob},
    )
    assert fake.keys == ["w1", "w2@sizes"]
    assert fake.calls[0].knob is None
    assert fake.calls[1].knob == knob
    assert not fake.calls[0].define
    assert fake.calls[0].own_timeout == 120.0
    state = load_state(repo)
    assert state is not None
    assert state.size_knobs["w2"] == knob
    fake.calls.clear()
    find(repo, [make_part()], no_tests=True)
    assert fake.calls == []


def test_workloads_beyond_the_ask_are_not_requirements(
    repo: Path, fake: Fake
) -> None:
    for name in ("w1", "w2", "w3", "w4"):
        define_workload(repo, name)
    find(repo, [make_part()], no_tests=True)
    assert [c.requirement for c in fake.calls] == [True, True, True, False]


def test_the_waiver_is_stored_and_meets_the_ask(
    repo: Path, fake: Fake
) -> None:
    define_workload(repo, "w1")
    run = find(
        repo,
        [make_part()],
        no_tests=True,
        waive={
            "define_workloads": "only one kind of operation exists",
            "add_size_knob": "no size input",
        },
    )
    assert run.result.done
    state = load_state(repo)
    assert state is not None
    assert dict(state.waivers) == {
        "define_workloads": "only one kind of operation exists",
        "add_size_knob": "no size input",
    }
    again = find(repo, [make_part()], no_tests=True)
    assert again.result.done


def test_a_waiver_for_a_blocking_code_is_refused_by_the_cli(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from perfproof.cli import main

    code = main(
        ["find", "--waive", "continue_find=x", "--repo", str(repo), "--json"]
    )
    assert code == 2
    assert "usage" in capsys.readouterr().out


def test_the_profiler_templates_are_reported(repo: Path, fake: Fake) -> None:
    run = find(repo, [make_part(), record_less_part()], no_tests=True)
    assert run.profilers["root"] == ()
    assert run.profilers["web"][2] == "perfproof.profilers.node"


def record_less_part() -> Part:
    return make_part("web", root="web", language="javascript")


def test_a_probe_blocked_by_a_missing_tool_runs_once_it_is_there(
    repo: Path, fake: Fake
) -> None:
    native = {
        "kind": "setuptools_ext",
        "name": "fastsum",
        "own_module": "fastsum.so",
        "sources": ["fastsum.c"],
    }
    find(
        repo,
        [make_part(native=(native,), recorder=False)],
        confirm_tests=("root",),
    )
    blocked = [c for c in fake.calls if c.blocked is not None]
    assert [c.key for c in blocked] == ["find-tests-root@native"]
    fake.calls.clear()
    find(repo, [make_part(native=(native,), recorder=False)])
    assert fake.calls == []
    fake.calls.clear()
    find(repo, [make_part(native=(native,), recorder=True)])
    assert [c.key for c in fake.calls] == ["find-tests-root@native"]
    assert fake.calls[0].blocked is None
    fake.calls.clear()
    find(repo, [make_part(native=(native,), recorder=True)])
    assert fake.calls == []


def test_a_second_find_call_fails_while_the_first_holds_the_lock(
    repo: Path, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake

    def short_lock(root: Path) -> AbstractContextManager[None]:
        return state.state_lock(root, 0.1)

    monkeypatch.setattr(engine, "state_lock", short_lock)
    with state.state_lock(repo), pytest.raises(CommandError) as raised:
        find(repo, [make_part()], "light")
    assert raised.value.code == "find_busy"
