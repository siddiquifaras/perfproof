"""Snapshot and determinism tests for `perfproof.report`.

Every record here is built in the test and validated by
`perfproof.schema.RunRecord`. Every number is a hand-picked test
value, not a measurement.
"""

import ast
import errno
import math
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, get_args

import pytest

from perfproof import report
from perfproof.report import files, read_record, record_json, render, write
from perfproof.schema import (
    GateName,
    GateStatus,
    GuardrailStatus,
    Outcome,
    Reason,
    RunRecord,
)

SNAPSHOTS = Path(__file__).with_name("snapshots")
RENDER_SOURCE = Path(report.__file__).with_name("render.py")
T0 = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
IST = timezone(timedelta(hours=5, minutes=30))
HASH_A = "a" * 64
HASH_B = "b" * 64
COMMIT = "0123456789abcdef0123456789abcdef01234567"
GATES: tuple[GateName, ...] = get_args(GateName)

Data = dict[str, Any]


# Building blocks, as JSON-shaped data so a case can override any field.


def iv(lower: float, upper: float, confidence: float = 0.99) -> Data:
    return {"lower": lower, "upper": upper, "confidence": confidence}


def memory_guardrail() -> Data:
    return {
        "metric": "peak_memory",
        "direction": "lower_is_better",
        "unit": "bytes",
        "allowance": 0.10,
        "allowance_source": "default",
        "limits": [{"value": 8e9, "label": "container memory limit"}],
    }


def gpu_guardrail() -> Data:
    return {
        "metric": "peak_gpu_memory",
        "direction": "lower_is_better",
        "unit": "bytes",
        "allowance": 0.10,
        "allowance_source": "target_class",
        "reason": "the project serves a model on the GPU",
    }


def latency_not_applicable() -> Data:
    return {
        "metric": "p99_latency",
        "direction": "lower_is_better",
        "unit": "s",
        "applicable": False,
        "reason": "a batch job serves no requests",
    }


def spec(**update: Any) -> Data:
    data: Data = {
        "name": "sum-loop",
        "command": ["python", "bench.py", "--n", "1000"],
        "env": {"PYTHONHASHSEED": "0", "OMP_NUM_THREADS": "1"},
        "metric": {
            "kind": "wall",
            "direction": "lower_is_better",
            "unit": "s",
        },
        "correctness": {
            "command": ["python", "-m", "pytest", "tests/test_core.py"],
            "timeout_seconds": 120.0,
            "outputs": [
                {"path": "out/result.npy", "compare": "numbers", "rtol": 1e-9}
            ],
        },
        "warmup": 2,
        "budget": {"max_rounds": 200, "max_seconds": 600.0},
        "looks": {"n0": 10, "k": 10, "max_looks": 5},
        "timeout_seconds": 30.0,
        "harness": ["bench.py"],
        "oracle": ["tests/test_core.py"],
        "inputs": ["data/input.bin"],
        "config": ["pyproject.toml"],
        "objective": "wall_time",
        "guardrails": [memory_guardrail(), latency_not_applicable()],
    }
    data.update(update)
    return data


def diagnostics(**update: Any) -> Data:
    data: Data = {
        "procedure_uncalibrated": False,
        "lag1_autocorrelation": 0.05,
        "cv_baseline": 0.02,
        "cv_candidate": 0.03,
        "sd_log_ratio": 0.01,
    }
    data.update(update)
    return data


def keep_verdict(**update: Any) -> Data:
    data: Data = {
        "outcome": "KEEP",
        "reason": "performance",
        "rule": 2,
        "estimate": 1.08,
        "interval": iv(1.05, 1.11),
        "rounds": 40,
        "looks": 4,
        "procedure": "t_log_ratio",
        "target_class": "python_cpu",
        "calibration": "calibrated",
        "diagnostics": diagnostics(),
    }
    data.update(update)
    return data


def gate_verdict(gate: GateName) -> Data:
    reason = (
        "correctness" if gate in ("correctness", "coverage") else ("integrity")
    )
    return {
        "outcome": "REJECT",
        "reason": reason,
        "rule": 1,
        "gate": gate,
        "rounds": 0,
        "looks": 0,
        "procedure": "t_log_ratio",
        "target_class": "python_cpu",
        "calibration": "calibrated",
        "diagnostics": {"procedure_uncalibrated": False},
    }


def passed(*gates: GateName) -> list[Data]:
    return [{"gate": g, "status": "passed"} for g in gates]


def all_gates(**replace: Data) -> list[Data]:
    """Every gate passed, except those given, in rule 1 order."""
    return [replace.get(g, {"gate": g, "status": "passed"}) for g in GATES]


def machine(host: str = "host-1", **update: Any) -> Data:
    data: Data = {
        "captured_at": T0,
        "host_id": host,
        "os": "Darwin",
        "os_version": "15.6",
        "kernel": "24.6.0",
        "arch": "arm64",
        "cpu_model": "Apple M4 Pro",
        "physical_cores": 12,
        "logical_cores": 12,
        "load_average": [1.5, 1.25, 1.0],
        "power_source": "ac",
        "battery_percent": 80.0,
        "memory_bytes": 48 * 2**30,
        "python_version": "3.12.12",
        "gpus": [],
        "missing": {
            "frequency_policy": "macOS exposes no frequency governor",
            "thermal_state": "pmset -g therm timed out",
        },
    }
    data.update(update)
    return data


def environment(version: str, session: int = 0) -> Data:
    return {
        "version": version,
        "session": session,
        "runtime_executable": f"/work/{version}/.venv/bin/python",
        "runtime_version": "3.12.12",
        "env_names": ["PATH", "OMP_NUM_THREADS", "HOME"],
        "env_values": {"OMP_NUM_THREADS": "1"},
        "dependency_locks": [{"path": "uv.lock", "sha256": HASH_A}],
    }


def memory_result(
    status: str = "passed",
    worsening: tuple[float, float] = (0.99, 1.05),
    estimate: float = 1.02,
) -> Data:
    return {
        "metric": "peak_memory",
        "status": status,
        "estimate": estimate,
        "worsening": iv(*worsening, confidence=0.995),
        "candidate": iv(5e8, 6e8, confidence=0.995),
    }


def latency_result() -> Data:
    return {
        "metric": "p99_latency",
        "status": "not_applicable",
        "reason": "a batch job serves no requests",
    }


def gpu_unmeasurable() -> Data:
    return {
        "metric": "peak_gpu_memory",
        "status": "unmeasurable",
        "reason": "nvidia-smi is not installed on this host",
    }


def record(**update: Any) -> RunRecord:
    data: Data = {
        "run_id": "20261015-120000-sum-loop",
        "perfproof_version": "0.1.0",
        "started_at": T0,
        "finished_at": T0 + timedelta(minutes=3),
        "spec": spec(),
        "baseline_commit": COMMIT,
        "order_seed": 12345,
        "verdict": keep_verdict(),
        "gates": all_gates(),
        "guardrails": [memory_result(), latency_result()],
        "machines": [machine()],
        "environments": [
            environment("baseline"),
            environment("candidate"),
        ],
        "warnings": [
            {"code": "high_load", "message": "1-minute load average is 1.5"}
        ],
        "samples_path": "samples.npz",
        "profile_paths": ["profiles/baseline.speedscope.json"],
    }
    data.update(update)
    return RunRecord.model_validate(data)


def gate_record(gate: GateName, failed: Data, **update: Any) -> RunRecord:
    """A gate REJECT: the gates before ``gate`` passed, it failed."""
    before = GATES[: GATES.index(gate)]
    fields: Data = {
        "verdict": gate_verdict(gate),
        "gates": [*passed(*before), {"gate": gate, **failed}],
        "guardrails": [],
        "samples_path": None,
        "profile_paths": [],
        "warnings": [],
    }
    fields.update(update)
    return record(**fields)


# One record per outcome and reason, per gate failure, and per label.


def keep() -> RunRecord:
    return record()


def keep_with_waivers() -> RunRecord:
    waived = {
        "side_effects": "DATABASE_URL points at db.internal, not local",
        "runtime": "the candidate upgrades numpy",
        "coverage": "perfproof can't instrument this target yet",
    }
    return record(
        started_at=datetime(2026, 10, 15, 17, 30, tzinfo=IST),
        finished_at=datetime(2026, 10, 15, 17, 34, 5, tzinfo=IST),
        spec=spec(
            allow_remote_services=True,
            allow_runtime_change=True,
            allow_unchecked_coverage=True,
        ),
        verdict=keep_verdict(
            target_class="native", calibration="uncalibrated"
        ),
        gates=all_gates(
            **{
                g: {"gate": g, "status": "waived", "detail": d}
                for g, d in waived.items()
            }
        ),
        profile_paths=[
            "profiles/baseline (run 1).speedscope.json",
            "profiles/candidate<2>.folded",
        ],
    )


def reject_performance() -> RunRecord:
    return record(
        verdict=keep_verdict(
            outcome="REJECT",
            rule=3,
            estimate=1.01,
            interval=iv(0.99, 1.02),
            rounds=50,
            looks=5,
        )
    )


def reject_regression() -> RunRecord:
    return record(
        verdict=keep_verdict(
            outcome="REJECT",
            rule=3,
            estimate=0.94,
            interval=iv(0.91, 0.97),
            rounds=10,
            looks=1,
            regression=True,
        )
    )


def reject_guardrail() -> RunRecord:
    return record(
        verdict=keep_verdict(outcome="REJECT", reason="guardrail"),
        guardrails=[
            memory_result("breached", (1.15, 1.25), 1.2),
            latency_result(),
        ],
    )


def reject_side_effects() -> RunRecord:
    return gate_record(
        "side_effects",
        {
            "status": "failed",
            "detail": "reset command `scripts/reset_db.py` failed | exit 2",
            "output": {
                "command": ["python", "scripts/reset_db.py", "--db", "a b"],
                "exit_code": 2,
                "timed_out": False,
                "stdout": "",
                "stderr": (
                    "psql: error: connection to server at "
                    '"localhost" failed\n```\n| Connection refused\n'
                ),
                "truncated": True,
            },
        },
        spec=spec(
            reset={
                "command": ["python", "scripts/reset_db.py", "--db", "a b"],
                "timeout_seconds": 30.0,
            }
        ),
    )


def reject_lock() -> RunRecord:
    return gate_record(
        "lock",
        {
            "status": "failed",
            "detail": "bench.py differs between the trees",
            "differences": [
                {"item": "bench.py", "baseline": HASH_A, "candidate": HASH_B},
                {
                    "item": "data/extra.bin",
                    "baseline": None,
                    "candidate": HASH_B,
                },
            ],
            "truncated": True,
        },
    )


def reject_procedure() -> RunRecord:
    return gate_record(
        "procedure",
        {
            "status": "failed",
            "detail": "the perfproof version differs between the arms",
            "differences": [
                {
                    "item": "perfproof version",
                    "baseline": "0.1.0",
                    "candidate": "0.1.1",
                }
            ],
        },
    )


def reject_runtime() -> RunRecord:
    return gate_record(
        "runtime",
        {
            "status": "failed",
            "detail": "the interpreter differs between the trees",
            "differences": [
                {
                    "item": "runtime_version",
                    "baseline": "3.12.12",
                    "candidate": "3.13.1",
                }
            ],
        },
    )


def reject_same_host() -> RunRecord:
    return gate_record(
        "same_host",
        {
            "status": "failed",
            "detail": "the arms ran on different hosts",
            "differences": [
                {
                    "item": "host_id",
                    "baseline": "host-1",
                    "candidate": "host-2",
                }
            ],
        },
        machines=[machine("host-1"), machine("host-2")],
        environments=[environment("baseline", 0), environment("candidate", 1)],
    )


def reject_correctness() -> RunRecord:
    return gate_record(
        "correctness",
        {
            "status": "failed",
            "detail": "replayed calls differ",
            "quarantined": [
                {
                    "test_id": "tests/test_core.py::test_timing",
                    "baseline_results": ["passed", "failed", "passed"],
                }
            ],
            "differential": [
                {
                    "function": "app.core:slow_sum",
                    "captured": 50,
                    "replayed": 48,
                    "generated": 100,
                    "skipped": [
                        {
                            "stage": "capture",
                            "reason": "argument 0 is an open file",
                            "count": 3,
                        },
                        {
                            "stage": "replay",
                            "reason": "result is a generator",
                            "count": 2,
                        },
                    ],
                    "mismatches": 1,
                    "differences": [
                        {
                            "item": "call 17 return value",
                            "baseline": "3",
                            "candidate": "4",
                        }
                    ],
                }
            ],
        },
    )


def reject_coverage() -> RunRecord:
    return gate_record(
        "coverage",
        {
            "status": "failed",
            "detail": "changed lines never ran under any check",
            "unverified": [
                {"path": "src/app/io.py", "lines": [158, 152, 153]},
                {"path": "src/app/util.py", "lines": [7]},
            ],
        },
    )


def inconclusive_budget() -> RunRecord:
    return record(
        spec=spec(
            metric={
                "kind": "reported",
                "direction": "lower_is_better",
                "unit": "ms",
            }
        ),
        verdict=keep_verdict(
            outcome="INCONCLUSIVE",
            reason="insufficient_evidence",
            rule=5,
            estimate=1.05,
            interval=iv(1.02, 1.08),
            rounds=200,
            looks=5,
            diagnostics=diagnostics(
                metric_disagreement=True,
                wall_interval=iv(0.97, 1.0),
                lag1_autocorrelation=-0.125,
            ),
        ),
    )


def inconclusive_gate_not_available() -> RunRecord:
    return record(
        verdict=keep_verdict(
            outcome="INCONCLUSIVE", reason="insufficient_evidence"
        ),
        gates=all_gates(
            coverage={
                "gate": "coverage",
                "status": "not_available",
                "detail": "perfproof can't measure coverage for this target",
            },
            side_effects={
                "gate": "side_effects",
                "status": "not_available",
                "detail": "not built yet",
            },
        ),
    )


def inconclusive_guardrail() -> RunRecord:
    return record(
        spec=spec(
            guardrails=[
                memory_guardrail(),
                gpu_guardrail(),
                latency_not_applicable(),
            ]
        ),
        verdict=keep_verdict(
            outcome="INCONCLUSIVE", reason="insufficient_evidence"
        ),
        guardrails=[
            memory_result("not_established", (1.05, 1.15), 1.1),
            gpu_unmeasurable(),
            latency_result(),
        ],
    )


def procedure_uncalibrated() -> RunRecord:
    return record(
        spec=spec(
            guardrails=[
                memory_guardrail(),
                gpu_guardrail(),
                latency_not_applicable(),
            ]
        ),
        verdict=keep_verdict(
            outcome="INCONCLUSIVE",
            reason="insufficient_evidence",
            rule=5,
            rounds=200,
            looks=5,
            target_class=None,
            calibration="uncalibrated",
            diagnostics=diagnostics(procedure_uncalibrated=True),
        ),
        gates=all_gates(
            side_effects={
                "gate": "side_effects",
                "status": "not_available",
                "detail": "not built yet",
            },
            coverage={
                "gate": "coverage",
                "status": "not_available",
                "detail": "not built yet",
            },
        ),
        guardrails=[memory_result(), gpu_unmeasurable(), latency_result()],
    )


def cross_host() -> RunRecord:
    gpu = {
        "name": "NVIDIA L4",
        "driver_version": "560.35.03",
        "sm_clock_mhz": 2040.0,
        "missing": {"memory_clock_mhz": "nvidia-smi reported [N/A]"},
    }
    linux: Data = {
        "os": "Linux",
        "os_version": None,
        "kernel": "6.8.0-1015-gcp",
        "arch": "x86_64",
        "cpu_model": "Intel Xeon @ 2.20GHz",
        "physical_cores": 4,
        "logical_cores": 8,
        "frequency_policy": "performance",
        "power_source": None,
        "battery_percent": None,
        "thermal_state": None,
        "gpus": [gpu],
        "missing": {
            "os_version": "no /etc/os-release in the container",
            "power_source": "no power supply class in /sys",
            "battery_percent": "no battery",
            "thermal_state": "no thermal zone readable",
        },
    }
    machines = [
        machine(f"modal-{i}", captured_at=T0 + timedelta(minutes=i), **linux)
        for i in range(4)
    ]
    machines[3]["gpus"] = None
    machines[3]["missing"] = {
        **linux["missing"],
        "gpus": "nvidia-smi timed out after 10 s",
    }
    return record(
        spec=spec(allow_cross_host=True),
        verdict=keep_verdict(
            outcome="INCONCLUSIVE",
            reason="insufficient_evidence",
            rule=5,
            estimate=1.12,
            interval=iv(1.04, 1.2),
            rounds=40,
            looks=1,
            cross_host=True,
            session_pairs=2,
            calibration="uncalibrated",
        ),
        gates=all_gates(
            same_host={
                "gate": "same_host",
                "status": "waived",
                "detail": "cross-host session design under allow_cross_host",
            }
        ),
        machines=machines,
        environments=[
            environment("baseline", 0),
            environment("candidate", 1),
            environment("baseline", 2),
            environment("candidate", 3),
        ],
        warnings=[
            {
                "code": "host_types_differ",
                "message": "session 3 has no GPU reading; sessions 0-2 "
                "have NVIDIA L4",
            }
        ],
    )


CASES: dict[str, Callable[[], RunRecord]] = {
    f.__name__: f
    for f in (
        keep,
        keep_with_waivers,
        reject_performance,
        reject_regression,
        reject_guardrail,
        reject_side_effects,
        reject_lock,
        reject_procedure,
        reject_runtime,
        reject_same_host,
        reject_correctness,
        reject_coverage,
        inconclusive_budget,
        inconclusive_gate_not_available,
        inconclusive_guardrail,
        procedure_uncalibrated,
        cross_host,
    )
}


def snapshot(name: str) -> Path:
    return SNAPSHOTS / f"{name}.md"


# Snapshots


@pytest.mark.parametrize("name", list(CASES))
def test_report_matches_snapshot(name: str) -> None:
    expected = snapshot(name).read_bytes()
    assert render(CASES[name]()).encode("utf-8") == expected


def test_every_snapshot_has_a_case() -> None:
    assert {p.stem for p in SNAPSHOTS.glob("*.md")} == set(CASES)


def test_cases_cover_every_outcome_reason_gate_and_status() -> None:
    records = [build() for build in CASES.values()]
    pairs = {(r.verdict.outcome, r.verdict.reason) for r in records}
    expected: set[tuple[Outcome, Reason]] = {
        ("KEEP", "performance"),
        ("REJECT", "performance"),
        ("REJECT", "guardrail"),
        ("REJECT", "correctness"),
        ("REJECT", "integrity"),
        ("INCONCLUSIVE", "insufficient_evidence"),
    }
    assert pairs == expected
    assert {r.verdict.gate for r in records} - {None} == set(GATES)
    gate_statuses = {g.status for r in records for g in r.gates}
    assert gate_statuses == set(get_args(GateStatus))
    guardrail_statuses = {g.status for r in records for g in r.guardrails}
    assert guardrail_statuses == set(get_args(GuardrailStatus))
    assert {r.verdict.rule for r in records} == {1, 2, 3, 5}
    assert any(r.verdict.regression for r in records)
    assert any(r.verdict.cross_host for r in records)
    assert any(r.verdict.diagnostics.procedure_uncalibrated for r in records)
    assert any(r.verdict.diagnostics.metric_disagreement for r in records)
    assert any(r.verdict.target_class is None for r in records)


# What the snapshots pin, checked directly so a regenerated snapshot
# can't quietly drop it.


def test_not_available_gates_are_named_as_blockers() -> None:
    text = render(inconclusive_gate_not_available())
    blocked = text.split("Blocked by:\n\n", 1)[1].split("\n\n", 1)[0]
    assert blocked.splitlines() == [
        "- Gate `side_effects`: not available (not built yet)",
        "- Gate `coverage`: not available (perfproof can't measure "
        "coverage for this target)",
    ]


def test_guardrail_blockers_are_named() -> None:
    text = render(inconclusive_guardrail())
    blocked = text.split("Blocked by:\n\n", 1)[1].split("\n\n", 1)[0]
    assert [line.split(":")[0] for line in blocked.splitlines()] == [
        "- Guardrail `peak_memory`",
        "- Guardrail `peak_gpu_memory`",
    ]


def test_gate_verdict_makes_no_speed_claim() -> None:
    for gate in GATES:
        build = CASES[f"reject_{gate}"]
        text = render(build())
        assert "Speedup estimate" not in text
        assert "No claim about speed." in text
        assert f"decided by rule 1, gate `{gate}`." in text


def test_labels() -> None:
    assert "Coverage not checked." in render(keep_with_waivers())
    assert "Cross-host: decided under" in render(cross_host())
    uncalibrated = render(procedure_uncalibrated())
    assert "Procedure not calibrated:" in uncalibrated
    assert "Target class: unknown" in uncalibrated
    assert "Not calibrated for this target class." in render(
        keep_with_waivers()
    )
    assert "Not calibrated for this target class." not in render(keep())


def test_metric_disagreement_leads_the_explanation() -> None:
    text = render(inconclusive_budget())
    verdict = text.split("## Verdict\n\n", 1)[1]
    paragraphs = verdict.split("\n\n")
    assert paragraphs[0].startswith("**INCONCLUSIVE**")
    assert paragraphs[1].startswith("**Metric disagreement.**")


def headline(text: str) -> str:
    return text.split("## Verdict\n\n", 1)[1].split("\n", 1)[0]


def verdict_section(text: str) -> str:
    return text.split("## Verdict\n\n", 1)[1].split("## Claim", 1)[0]


def test_headline_prints_stored_bounds_of_a_regression() -> None:
    rec = record(
        verdict=keep_verdict(
            outcome="REJECT",
            rule=3,
            estimate=0.95,
            interval=iv(0.9, 0.99996),
            rounds=10,
            looks=1,
            regression=True,
        )
    )
    assert headline(render(rec)) == (
        "**REJECT**, reason `performance`, decided by rule 3: speedup "
        "0.95, interval 0.9 to 0.99996."
    )


def test_headline_prints_a_lower_bound_one_ulp_above_the_bar() -> None:
    lower = math.nextafter(1 + 0.03, 2)
    assert repr(lower) == "1.0300000000000002"
    rec = record(verdict=keep_verdict(estimate=1.06, interval=iv(lower, 1.1)))
    assert headline(render(rec)) == (
        "**KEEP**, reason `performance`, decided by rule 2: speedup 1.06, "
        "interval 1.0300000000000002 to 1.1."
    )


@pytest.mark.parametrize(
    "name", ["inconclusive_budget", "procedure_uncalibrated", "cross_host"]
)
def test_an_interval_that_decided_nothing_stays_out_of_the_headline(
    name: str,
) -> None:
    assert "speedup" not in headline(render(CASES[name]()))


def test_too_few_cross_host_pairs_is_explained_as_such() -> None:
    text = verdict_section(render(cross_host()))
    assert "fewer than n0 = 10" in text
    assert "straddl" not in text


def test_rule_5_without_an_interval_claims_no_straddle() -> None:
    rec = record(
        verdict=keep_verdict(
            outcome="INCONCLUSIVE",
            reason="insufficient_evidence",
            rule=5,
            estimate=None,
            interval=None,
            rounds=1,
            looks=0,
        )
    )
    text = render(rec)
    assert "before any interval was computed" in verdict_section(text)
    assert "straddl" not in verdict_section(text)
    assert "- Interval at the last look: not computed\n" in text


def test_timestamps_keep_their_recorded_offset() -> None:
    text = render(keep_with_waivers())
    assert "- started_at: 2026-10-15T17:30:00+05:30\n" in text
    assert "- finished_at: 2026-10-15T17:34:05+05:30\n" in text


def test_report_has_no_trailing_spaces() -> None:
    for build in CASES.values():
        text = render(build())
        assert text.endswith("\n")
        assert not text.endswith("\n\n")
        assert all(line == line.rstrip() for line in text.splitlines())


# Determinism


@pytest.mark.parametrize("name", list(CASES))
def test_record_json_round_trips_byte_for_byte(name: str) -> None:
    rec = CASES[name]()
    text = record_json(rec)
    reloaded = RunRecord.model_validate_json(text)
    assert reloaded == rec
    assert record_json(reloaded) == text
    assert render(reloaded) == render(rec)


def test_rendering_is_identical_across_processes_and_time_zones(
    tmp_path: Path,
) -> None:
    paths: list[str] = []
    for name, build in CASES.items():
        path = tmp_path / f"{name}.json"
        path.write_text(record_json(build()), encoding="utf-8")
        paths.append(str(path))
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from perfproof.report import read_record, render\n"
        "tag = sys.argv[1]\n"
        "for p in sys.argv[2:]:\n"
        "    out = Path(p).with_suffix(f'.{tag}.md')\n"
        "    out.write_bytes(render(read_record(Path(p))).encode())\n"
    )
    environments = {
        "utc": {"TZ": "UTC", "PYTHONHASHSEED": "0", "LC_ALL": "C"},
        "kolkata": {"TZ": "Asia/Kolkata", "PYTHONHASHSEED": "1"},
        "newfoundland": {
            "TZ": "America/St_Johns",
            "PYTHONHASHSEED": "random",
            "LANG": "de_DE.UTF-8",
        },
    }
    for tag, env in environments.items():
        subprocess.run(
            [sys.executable, "-c", code, tag, *paths],
            env=env,
            check=True,
            timeout=120,
        )
    for name in CASES:
        expected = snapshot(name).read_bytes()
        for tag in environments:
            out = tmp_path / f"{name}.{tag}.md"
            assert out.read_bytes() == expected, (name, tag)


def test_render_module_reads_no_clock_environment_or_file() -> None:
    tree = ast.parse(RENDER_SOURCE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported == {
        "json",
        "collections.abc",
        "datetime",
        "typing",
        "pydantic",
        "perfproof.schema",
    }
    forbidden = {
        "now",
        "today",
        "utcnow",
        "astimezone",
        "timestamp",
        "fromtimestamp",
        "open",
        "read_text",
        "read_bytes",
        "getenv",
        "environ",
    }
    names = {
        node.attr if isinstance(node, ast.Attribute) else node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute | ast.Name)
    }
    assert not names & forbidden


# Files


def test_write_puts_record_and_report_in_the_run_dir(tmp_path: Path) -> None:
    rec = keep()
    record_path, report_path = write(rec, tmp_path)
    assert record_path == tmp_path / "record.json"
    assert report_path == tmp_path / "report.md"
    assert record_path.read_bytes() == record_json(rec).encode("utf-8")
    assert report_path.read_bytes() == snapshot("keep").read_bytes()
    assert read_record(record_path) == rec


@pytest.mark.parametrize("existing", ["record.json", "report.md"])
def test_write_refuses_to_overwrite(tmp_path: Path, existing: str) -> None:
    (tmp_path / existing).write_text("old", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write(keep(), tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == [existing]
    assert (tmp_path / existing).read_text(encoding="utf-8") == "old"


def fail_fsync_on_call(
    monkeypatch: pytest.MonkeyPatch, failing_call: int
) -> None:
    """Make the given call to ``os.fsync`` fail with ENOSPC."""
    real = os.fsync
    calls: list[int] = []

    def fsync(fd: int) -> None:
        calls.append(fd)
        if len(calls) == failing_call:
            raise OSError(errno.ENOSPC, "No space left on device")
        real(fd)

    monkeypatch.setattr(os, "fsync", fsync)


@pytest.mark.parametrize(
    ("failing_call", "left"), [(1, []), (2, ["record.json"])]
)
def test_out_of_space_leaves_a_state_a_retry_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failing_call: int,
    left: list[str],
) -> None:
    fail_fsync_on_call(monkeypatch, failing_call)
    with pytest.raises(OSError, match="No space left") as raised:
        write(keep(), tmp_path)
    assert raised.value.errno == errno.ENOSPC
    assert sorted(p.name for p in tmp_path.iterdir()) == left
    monkeypatch.undo()
    write(keep(), tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "record.json",
        "report.md",
    ]
    assert read_record(tmp_path / "record.json") == keep()
    assert (tmp_path / "report.md").read_bytes() == snapshot(
        "keep"
    ).read_bytes()


@pytest.mark.parametrize("raced", ["record.json", "report.md"])
def test_two_writers_of_the_same_record_both_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raced: str
) -> None:
    # Writer B runs a whole write just before writer A publishes
    # ``raced``, so A finds the file B linked.
    real = files._publish  # pyright: ignore[reportPrivateUsage]
    raced_once: list[str] = []

    def publish(path: Path, data: bytes) -> None:
        if path.name == raced and not raced_once:
            raced_once.append(raced)
            assert write(keep(), tmp_path) == (
                tmp_path / "record.json",
                tmp_path / "report.md",
            )
        real(path, data)

    monkeypatch.setattr(files, "_publish", publish)
    assert write(keep(), tmp_path) == (
        tmp_path / "record.json",
        tmp_path / "report.md",
    )
    assert raced_once == [raced]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "record.json",
        "report.md",
    ]
    assert (tmp_path / "record.json").read_bytes() == record_json(
        keep()
    ).encode("utf-8")
    assert (tmp_path / "report.md").read_bytes() == snapshot(
        "keep"
    ).read_bytes()


def test_a_writer_losing_a_race_to_a_different_record_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = files._publish  # pyright: ignore[reportPrivateUsage]
    other = record_json(reject_lock())

    def publish(path: Path, data: bytes) -> None:
        if path.name == "record.json" and not path.exists():
            (tmp_path / "record.json").write_text(other, encoding="utf-8")
        real(path, data)

    monkeypatch.setattr(files, "_publish", publish)
    with pytest.raises(FileExistsError, match="different content"):
        write(keep(), tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["record.json"]
    assert (tmp_path / "record.json").read_text(encoding="utf-8") == other


def test_a_crash_mid_write_leaves_no_record_and_allows_a_retry(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    source.write_text(record_json(keep()), encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # The process dies after writing the record's temporary file and
    # before linking it: no cleanup runs, as in a real crash.
    code = (
        "import os, sys\n"
        "from pathlib import Path\n"
        "from perfproof.report import read_record, write\n"
        "rec = read_record(Path(sys.argv[1]))\n"
        "os.link = lambda *args, **kwargs: os._exit(3)\n"
        "write(rec, Path(sys.argv[2]))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code, str(source), str(run_dir)],
        check=False,
        timeout=120,
    )
    assert done.returncode == 3
    leftovers = list(run_dir.iterdir())
    assert len(leftovers) == 1
    assert leftovers[0].name.startswith(".record.json.")
    write(keep(), run_dir)
    assert (run_dir / "record.json").read_bytes() == source.read_bytes()
    assert (run_dir / "report.md").read_bytes() == snapshot(
        "keep"
    ).read_bytes()


def test_a_truncated_leftover_neither_blocks_nor_leaks(tmp_path: Path) -> None:
    text = record_json(keep())
    leftover = tmp_path / ".record.json.k3j9x2.tmp"
    leftover.write_text(text[: len(text) // 2], encoding="utf-8")
    write(keep(), tmp_path)
    assert (tmp_path / "record.json").read_text(encoding="utf-8") == text
    assert read_record(tmp_path / "record.json") == keep()


def test_a_crash_between_the_two_files_is_resumed(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text(record_json(keep()), encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # The process dies on the second link, after record.json is linked
    # and before report.md is: no cleanup runs, as in a real crash.
    code = (
        "import os, sys\n"
        "from pathlib import Path\n"
        "from perfproof.report import read_record, write\n"
        "rec = read_record(Path(sys.argv[1]))\n"
        "real = os.link\n"
        "calls = []\n"
        "def link(*args, **kwargs):\n"
        "    calls.append(args)\n"
        "    if len(calls) == 2:\n"
        "        os._exit(3)\n"
        "    real(*args, **kwargs)\n"
        "os.link = link\n"
        "write(rec, Path(sys.argv[2]))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code, str(source), str(run_dir)],
        check=False,
        timeout=120,
    )
    assert done.returncode == 3
    names = sorted(p.name for p in run_dir.iterdir())
    assert len(names) == 2
    assert names[0].startswith(".report.md.")
    assert names[1] == "record.json"
    assert (run_dir / "record.json").read_bytes() == source.read_bytes()
    write(keep(), run_dir)
    assert (run_dir / "report.md").read_bytes() == snapshot(
        "keep"
    ).read_bytes()
    assert (run_dir / "record.json").read_bytes() == source.read_bytes()


def test_write_is_idempotent_for_the_same_record(tmp_path: Path) -> None:
    first = write(keep(), tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert write(keep(), tmp_path) == first
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_write_refuses_a_different_stored_record(tmp_path: Path) -> None:
    stored = record_json(reject_lock())
    (tmp_path / "record.json").write_text(stored, encoding="utf-8")
    with pytest.raises(FileExistsError, match="different content"):
        write(keep(), tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["record.json"]
    assert (tmp_path / "record.json").read_text(encoding="utf-8") == stored


def test_write_refuses_a_report_that_differs(tmp_path: Path) -> None:
    (tmp_path / "record.json").write_text(
        record_json(keep()), encoding="utf-8"
    )
    (tmp_path / "report.md").write_text("old", encoding="utf-8")
    with pytest.raises(FileExistsError, match="different content"):
        write(keep(), tmp_path)
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "old"
    assert (tmp_path / "record.json").exists()
