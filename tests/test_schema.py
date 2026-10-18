import copy
import json
import math
import pickle
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast, get_args

import pytest
from pydantic import BaseModel, ValidationError

from perfproof import schema
from perfproof.schema import (
    DEFAULT_GUARDRAILS,
    GATE_REASON,
    HYPOTHESIS_EVENT,
    MODELS,
    AbsoluteLimit,
    Anchors,
    Arm,
    BenchmarkSpec,
    Budget,
    Cap,
    CodeAnchor,
    CommandOutput,
    Confirmation,
    ConstraintEntry,
    CorrectnessSpec,
    DependencyLock,
    Diagnostics,
    Difference,
    DifferentialCheck,
    EntryRef,
    EvidenceRef,
    FalsificationEvent,
    FileHash,
    GateName,
    GateResult,
    GoalBar,
    GoalEntry,
    GpuInfo,
    GuardrailResult,
    GuardrailSpec,
    Hotspot,
    HowToRunEntry,
    ImplementationEvent,
    Interval,
    Invalidation,
    LinkedLibrary,
    LockedFile,
    LockManifest,
    LookSchedule,
    MachineFingerprint,
    MeasuredSpeedup,
    MechanismEvent,
    MetricSpec,
    ObservationEvent,
    OutcomeEvent,
    OutputCheck,
    PredictionEvent,
    ProjectHistoryRecord,
    ProjectModel,
    Provenance,
    QuarantinedTest,
    ReorderedEvent,
    ResetSpec,
    RevalidatedEvent,
    RunRecord,
    RunWarning,
    Sample,
    ServiceSpec,
    SkippedCalls,
    SkippedEvent,
    StaleEvent,
    TargetClass,
    TargetEntry,
    TreeEnvironment,
    UnverifiedLines,
    Verdict,
    WorkloadEntry,
)

SCHEMA_FILE = Path(schema.__file__).with_name("schema.json")
T0 = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
HASH_A = "a" * 64
HASH_B = "b" * 64
# Version 1 pinned c96961a7... for this spec's content without an
# objective or guardrails. Version 2 added both to the fixture and
# pinned 98398968.... Version 3 changes schema_version and adds the
# metric's resolution and the differential tolerances, at their
# defaults, to the canonical form.
PINNED_SPEC_HASH = (
    "18b92a8e3582909ea75028680ca1b408f945c55f134d3aae4a8dae0f00373a19"
)
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def as_json(model: BaseModel) -> dict[str, Any]:
    # Without spec_hash, so an edited spec fails on the rule under test
    # rather than on its stored hash.
    return model.model_dump(mode="json", exclude_computed_fields=True)


def iv(lower: float, upper: float) -> Interval:
    return Interval(lower=lower, upper=upper, confidence=0.99)


def metric() -> MetricSpec:
    return MetricSpec(kind="wall", direction="lower_is_better", unit="s")


def correctness() -> CorrectnessSpec:
    return CorrectnessSpec(
        command=("python", "-m", "pytest", "tests/test_core.py"),
        timeout_seconds=120.0,
        outputs=(
            OutputCheck(path="out/result.npy", compare="numbers", rtol=1e-9),
        ),
    )


FIXTURE_NA = "not measured in these fixtures"


def memory_not_applicable() -> GuardrailSpec:
    return GuardrailSpec(
        metric="peak_memory",
        direction="lower_is_better",
        unit="bytes",
        applicable=False,
        reason=FIXTURE_NA,
    )


def memory_not_applicable_result() -> GuardrailResult:
    return GuardrailResult(
        metric="peak_memory", status="not_applicable", reason=FIXTURE_NA
    )


def calibrated(**fields: Any) -> Diagnostics:
    return Diagnostics(procedure_uncalibrated=False, **fields)


def spec() -> BenchmarkSpec:
    return BenchmarkSpec(
        objective="wall_time",
        guardrails=(memory_not_applicable(),),
        name="sum-loop",
        command=("python", "bench.py", "--n", "1000"),
        cwd=".",
        env={"PYTHONHASHSEED": "0", "OMP_NUM_THREADS": "1"},
        metric=metric(),
        correctness=correctness(),
        warmup=2,
        budget=Budget(max_rounds=200, max_seconds=600.0),
        looks=LookSchedule(n0=10, k=10, max_looks=5),
        timeout_seconds=30.0,
        harness=("bench.py",),
        oracle=("tests/test_core.py",),
        inputs=("data/input.bin",),
        config=("pyproject.toml",),
    )


def interval() -> Interval:
    return Interval(lower=1.05, upper=1.11, confidence=0.99)


def keep() -> Verdict:
    return Verdict(
        outcome="KEEP",
        reason="performance",
        rule=2,
        estimate=1.08,
        interval=interval(),
        rounds=40,
        looks=4,
        procedure="t_log_ratio",
        target_class="python_cpu",
        calibration="uncalibrated",
        diagnostics=calibrated(
            lag1_autocorrelation=0.05,
            cv_baseline=0.02,
            cv_candidate=0.03,
            sd_log_ratio=0.01,
        ),
    )


def regression() -> Verdict:
    return Verdict(
        outcome="REJECT",
        reason="performance",
        rule=3,
        estimate=0.94,
        interval=Interval(lower=0.91, upper=0.97, confidence=0.99),
        rounds=10,
        looks=1,
        procedure="t_log_ratio",
        regression=True,
        target_class="python_cpu",
        calibration="uncalibrated",
        diagnostics=calibrated(),
    )


def inconclusive() -> Verdict:
    return Verdict(
        outcome="INCONCLUSIVE",
        reason="insufficient_evidence",
        rule=5,
        estimate=1.05,
        interval=Interval(lower=1.02, upper=1.08, confidence=0.99),
        rounds=200,
        looks=5,
        procedure="t_log_ratio",
        target_class="python_cpu",
        calibration="uncalibrated",
        diagnostics=calibrated(
            metric_disagreement=True,
            wall_interval=Interval(lower=0.97, upper=1.0, confidence=0.99),
        ),
    )


def integrity() -> Verdict:
    return Verdict(
        outcome="REJECT",
        reason="integrity",
        rule=1,
        gate="lock",
        rounds=0,
        looks=0,
        procedure="t_log_ratio",
        calibration="uncalibrated",
        diagnostics=calibrated(),
    )


def correctness_reject() -> Verdict:
    return Verdict(
        outcome="REJECT",
        reason="correctness",
        rule=1,
        gate="correctness",
        rounds=0,
        looks=0,
        procedure="t_log_ratio",
        calibration="uncalibrated",
        diagnostics=calibrated(),
    )


EVERY_GATE: tuple[GateName, ...] = (
    "side_effects",
    "lock",
    "procedure",
    "runtime",
    "same_host",
    "correctness",
    "coverage",
)


def gates_passed() -> tuple[GateResult, ...]:
    return tuple(GateResult(gate=g, status="passed") for g in EVERY_GATE)


def with_gate(
    gates: tuple[GateResult, ...], gate: GateName, status: str, **fields: Any
) -> tuple[GateResult, ...]:
    """Return ``gates`` with ``gate`` replaced by one in ``status``."""
    detail = fields.pop("detail", "" if status == "passed" else "x")
    new = GateResult.model_validate(
        {"gate": gate, "status": status, "detail": detail, **fields}
    )
    return tuple(new if g.gate == gate else g for g in gates)


def without_gate(
    gates: tuple[GateResult, ...], gate: GateName
) -> tuple[GateResult, ...]:
    return tuple(g for g in gates if g.gate != gate)


def lock_failed() -> GateResult:
    return GateResult(
        gate="lock",
        status="failed",
        detail="bench.py differs between the trees",
        differences=(
            Difference(item="bench.py", baseline=HASH_A, candidate=HASH_B),
        ),
    )


def machine() -> MachineFingerprint:
    return MachineFingerprint(
        captured_at=T0,
        host_id="host-1",
        os="Darwin",
        os_version="15.6",
        kernel="24.6.0",
        arch="arm64",
        cpu_model="Apple M4 Pro",
        physical_cores=12,
        logical_cores=12,
        load_average=(1.5, 1.2, 1.0),
        power_source="ac",
        battery_percent=80.0,
        memory_bytes=48 * 2**30,
        python_version="3.12.12",
        gpus=(),
        missing={
            "frequency_policy": "macOS exposes no frequency governor",
            "thermal_state": "pmset -g therm timed out",
        },
    )


def gpu_no_clocks() -> GpuInfo:
    return GpuInfo(
        name="NVIDIA A10G",
        driver_version="560.35.03",
        missing={
            "sm_clock_mhz": "nvidia-smi reported [N/A]",
            "memory_clock_mhz": "nvidia-smi reported [N/A]",
        },
    )


def environment(version: Arm = "baseline") -> TreeEnvironment:
    return TreeEnvironment(
        version=version,
        runtime_kind="interpreter",
        runtime_executable=f"/work/{version}/.venv/bin/python",
        runtime_version="3.12.12",
        env_names=("PATH", "OMP_NUM_THREADS", "HOME"),
        env_values={"OMP_NUM_THREADS": "1"},
        dependency_locks=(DependencyLock(path="uv.lock", sha256=HASH_A),),
    )


def environments() -> tuple[TreeEnvironment, ...]:
    return (environment("baseline"), environment("candidate"))


def record(**update: Any) -> RunRecord:
    fields: dict[str, Any] = {
        "run_id": "20261015-120000-sum-loop",
        "perfproof_version": "0.1.0",
        "started_at": T0,
        "finished_at": T0 + timedelta(minutes=3),
        "spec": spec(),
        "baseline_commit": COMMIT,
        "order_seed": 12345,
        "verdict": keep(),
        "gates": gates_passed(),
        "machines": (machine(),),
        "environments": environments(),
        "warnings": (
            RunWarning(code="high_load", message="load average 1.5"),
        ),
        "samples_path": "samples.npz",
        "profile_paths": ("profiles/baseline.speedscope.json",),
    }
    fields.update(update)
    if "guardrails" not in update and fields["verdict"].gate is None:
        # spec() declares peak memory, not applicable, so every other
        # verdict carries its result; a gate verdict carries none.
        fields["guardrails"] = (memory_not_applicable_result(),)
    return RunRecord(**fields)


def sample() -> Sample:
    return Sample(
        version="candidate",
        round=3,
        position=1,
        value=0.125,
        wall_seconds=0.125,
        cpu_seconds=0.12,
        max_rss_bytes=50_000_000,
        exit_code=0,
        command_hash=HASH_A,
    )


# Safety checks: guardrails, reset, opt-ins, gate details, thin path.
# Every number below is a hand-picked test value, not a measurement.

ALLOWANCE = 0.10
MEMORY_LIMIT = 8e9
NA_REASON = "a batch job serves no requests"


def memory_guardrail() -> GuardrailSpec:
    return GuardrailSpec(
        metric="peak_memory",
        direction="lower_is_better",
        unit="bytes",
        allowance=ALLOWANCE,
        allowance_source="default",
        limits=(
            AbsoluteLimit(value=MEMORY_LIMIT, label="container memory limit"),
        ),
    )


def latency_not_applicable() -> GuardrailSpec:
    return GuardrailSpec(
        metric="p99_latency",
        direction="lower_is_better",
        unit="s",
        applicable=False,
        reason=NA_REASON,
    )


def reset() -> ResetSpec:
    return ResetSpec(
        command=("python", "scripts/reset_db.py"), timeout_seconds=30.0
    )


def safety_spec(**update: Any) -> BenchmarkSpec:
    data: dict[str, Any] = {
        **as_json(spec()),
        "reset": as_json(reset()),
        "objective": "wall_time",
        "guardrails": [
            as_json(memory_guardrail()),
            as_json(latency_not_applicable()),
        ],
        **update,
    }
    return BenchmarkSpec.model_validate(data)


def memory_result(
    status: str = "passed",
    worsening: tuple[float, float] | None = (0.99, 1.05),
    candidate: tuple[float, float] | None = (5e8, 6e8),
) -> GuardrailResult:
    return GuardrailResult.model_validate(
        {
            "metric": "peak_memory",
            "status": status,
            "estimate": None if worsening is None else 1.02,
            "worsening": None
            if worsening is None
            else as_json(iv(*worsening)),
            "candidate": None
            if candidate is None
            else as_json(iv(*candidate)),
        }
    )


def latency_result() -> GuardrailResult:
    return GuardrailResult(
        metric="p99_latency", status="not_applicable", reason=NA_REASON
    )


def guardrail_reject() -> Verdict:
    return Verdict.model_validate(
        {**as_json(keep()), "outcome": "REJECT", "reason": "guardrail"}
    )


def keep_blocked() -> Verdict:
    return Verdict.model_validate(
        {
            **as_json(keep()),
            "outcome": "INCONCLUSIVE",
            "reason": "insufficient_evidence",
        }
    )


def uncalibrated(base: Verdict | None = None) -> Verdict:
    """A thin-path verdict: rule 5 whatever the interval shows."""
    data = as_json(keep() if base is None else base)
    data.update(
        outcome="INCONCLUSIVE",
        reason="insufficient_evidence",
        rule=5,
        regression=False,
        diagnostics={"procedure_uncalibrated": True},
    )
    return Verdict.model_validate(data)


def safety_record(**update: Any) -> RunRecord:
    fields: dict[str, Any] = {
        "spec": safety_spec(),
        "guardrails": (memory_result(), latency_result()),
    }
    fields.update(update)
    return record(**fields)


def quarantined() -> QuarantinedTest:
    return QuarantinedTest(
        test_id="tests/test_core.py::test_timing",
        baseline_results=("passed", "failed", "passed"),
    )


def differential(mismatches: int = 1) -> DifferentialCheck:
    return DifferentialCheck(
        function="app.core:slow_sum",
        captured=50,
        replayed=48,
        generated=100,
        skipped=(
            SkippedCalls(
                stage="capture", reason="argument 0 is an open file", count=3
            ),
            SkippedCalls(
                stage="replay", reason="result is a generator", count=2
            ),
        ),
        mismatches=mismatches,
        differences=(
            Difference(
                item="call 17 return value", baseline="3", candidate="4"
            ),
        )
        if mismatches
        else (),
    )


def correctness_failed() -> GateResult:
    return GateResult(
        gate="correctness",
        status="failed",
        detail="replayed calls differ",
        quarantined=(quarantined(),),
        differential=(differential(),),
    )


def coverage_failed() -> GateResult:
    return GateResult(
        gate="coverage",
        status="failed",
        detail="changed lines never ran",
        unverified=(UnverifiedLines(path="src/app/io.py", lines=(158, 152)),),
    )


def command_output() -> CommandOutput:
    return CommandOutput(
        command=("python", "scripts/reset_db.py"),
        exit_code=1,
        timed_out=False,
        stdout="",
        stderr="connection refused",
    )


def reset_failed() -> GateResult:
    return GateResult(
        gate="side_effects",
        status="failed",
        detail="reset command failed",
        output=command_output(),
    )


def not_available(gate: GateName) -> GateResult:
    return GateResult(gate=gate, status="not_available", detail="not built")


def thin_path_gates() -> tuple[GateResult, ...]:
    gates = gates_passed()
    for gate in ("side_effects", "coverage"):
        gates = with_gate(gates, gate, "not_available", detail="not built")
    return gates


# Method follow-ups: resolution, runtime kind, repository, calibration
# conditions, capture limit. Every value is a hand-picked test value.

CONDITIONS = ("log-normal, CV 5%, one host", "AR(1), rho 0.5, one host")
REPOSITORY = "https://github.com/example/sum-loop.git"


def library_file() -> LinkedLibrary:
    return LinkedLibrary(
        name="libfoo.so.1",
        source="file",
        path="/usr/lib/libfoo.so.1.2",
        sha256=HASH_A,
    )


def library_cache() -> LinkedLibrary:
    return LinkedLibrary(
        name="/usr/lib/libSystem.B.dylib",
        source="dyld_shared_cache",
        path="/usr/lib/libSystem.B.dylib",
    )


def library_weak() -> LinkedLibrary:
    return LinkedLibrary(name="libopt.dylib", source="weak_absent")


def native_environment(version: Arm = "baseline") -> TreeEnvironment:
    return TreeEnvironment(
        version=version,
        runtime_kind="native_build",
        runtime_executable=f"/work/{version}/build/bench",
        linked_libraries=(library_file(), library_cache(), library_weak()),
        env_names=("PATH",),
        dependency_locks=(),
    )


def resolved_sample() -> Sample:
    return Sample.model_validate(
        {
            **as_json(sample()),
            "resolution": 0.001,
            "resolution_source": "printed",
        }
    )


def resolution_stop() -> Verdict:
    """The rule 1 INCONCLUSIVE of the resolution stop, at a look."""
    return Verdict(
        outcome="INCONCLUSIVE",
        reason="insufficient_evidence",
        rule=1,
        rounds=10,
        looks=1,
        procedure="t_log_ratio",
        target_class="python_cpu",
        calibration="uncalibrated",
        diagnostics=calibrated(
            coarse_resolution=True, resolution_widening=0.2
        ),
    )


def calibrated_keep() -> Verdict:
    return Verdict.model_validate(
        {
            **as_json(keep()),
            "calibration": "calibrated",
            "calibration_conditions": list(CONDITIONS),
            "diagnostics": {
                "procedure_uncalibrated": False,
                "coarse_resolution": False,
                "resolution_widening": 0.0004,
            },
        }
    )


EXAMPLES: dict[str, BaseModel] = {
    "metric": metric(),
    "metric_counter": MetricSpec(
        kind="counter",
        direction="lower_is_better",
        unit="count",
        event="instructions",
    ),
    "output_check": OutputCheck(path="out.txt", compare="text"),
    "correctness": correctness(),
    "budget": Budget(max_rounds=50, max_seconds=60.0),
    "looks": LookSchedule(n0=10, k=5, max_looks=4),
    "spec": spec(),
    "locked_file": LockedFile(
        path="bench.py", role="harness", sha256=HASH_A, size_bytes=120
    ),
    "lock_manifest": LockManifest(
        spec_hash=HASH_B,
        files=(
            LockedFile(
                path="bench.py", role="harness", sha256=HASH_A, size_bytes=1
            ),
            LockedFile(
                path="data/in.bin", role="input", sha256=HASH_B, size_bytes=0
            ),
        ),
    ),
    "sample": sample(),
    "difference": Difference(item="PATH", baseline="/a", candidate=None),
    "gate": lock_failed(),
    "interval": interval(),
    "diagnostics": calibrated(lag1_autocorrelation=-0.2),
    "verdict_keep": keep(),
    "verdict_regression": regression(),
    "verdict_inconclusive": inconclusive(),
    "verdict_integrity": integrity(),
    "verdict_correctness": correctness_reject(),
    "hotspot": Hotspot(
        function="slow_sum",
        file="src/app/core.py",
        line=42,
        frame_kind="python",
        is_library=False,
        self_fraction=0.6,
        total_fraction=0.7,
    ),
    "gpu": GpuInfo(
        name="NVIDIA H100 80GB HBM3",
        driver_version="560.35.03",
        sm_clock_mhz=1980.0,
        memory_clock_mhz=2619.0,
    ),
    "machine": machine(),
    "gpu_no_clocks": gpu_no_clocks(),
    "dependency_lock": DependencyLock(path="uv.lock", sha256=HASH_B),
    "environment": environment(),
    "environment_unknown": TreeEnvironment(
        version="candidate",
        missing={
            "runtime_kind": "interpreter not found on PATH",
            "runtime_executable": "interpreter not found on PATH",
            "runtime_version": "interpreter not found on PATH",
            "linked_libraries": "interpreter not found on PATH",
            "env_names": "environment not captured",
            "dependency_locks": "tree unreadable",
        },
    ),
    "warning": RunWarning(code="on_battery", message="running on battery"),
    "record": record(),
    "record_integrity": record(
        verdict=integrity(),
        gates=(lock_failed(),),
        samples_path=None,
    ),
    "reset": reset(),
    "limit": AbsoluteLimit(value=0.25, label="latency SLO"),
    "guardrail_spec": memory_guardrail(),
    "guardrail_spec_na": latency_not_applicable(),
    "guardrail_spec_limits_only": GuardrailSpec(
        metric="throughput",
        direction="higher_is_better",
        unit="tokens/s",
        reason="the project requires a throughput floor",
        limits=(AbsoluteLimit(value=100.0, label="throughput floor"),),
    ),
    "spec_safety": safety_spec(),
    "spec_opt_ins": safety_spec(
        allow_remote_services=True, allow_unchecked_coverage=True
    ),
    "quarantined": quarantined(),
    "skipped": SkippedCalls(stage="capture", reason="a socket", count=1),
    "differential": differential(),
    "differential_clean": differential(mismatches=0),
    "unverified": UnverifiedLines(path="src/app/io.py", lines=(152, 158)),
    "command_output": command_output(),
    "gate_correctness": correctness_failed(),
    "gate_coverage": coverage_failed(),
    "gate_reset": reset_failed(),
    "gate_not_available": not_available("coverage"),
    "guardrail_passed": memory_result(),
    "guardrail_breached": memory_result("breached", worsening=(1.15, 1.25)),
    "guardrail_not_established": memory_result(
        "not_established", worsening=(1.05, 1.15)
    ),
    "guardrail_unmeasurable": GuardrailResult(
        metric="peak_memory",
        status="unmeasurable",
        reason="max RSS is not reported on this platform",
    ),
    "guardrail_not_applicable": latency_result(),
    "diagnostics_uncalibrated": Diagnostics(procedure_uncalibrated=True),
    "verdict_guardrail": guardrail_reject(),
    "verdict_blocked": keep_blocked(),
    "verdict_uncalibrated": uncalibrated(),
    "record_safety_keep": safety_record(),
    "record_guardrail_reject": safety_record(
        verdict=guardrail_reject(),
        guardrails=(
            memory_result("breached", worsening=(1.15, 1.25)),
            latency_result(),
        ),
    ),
    "record_thin_path": safety_record(
        verdict=uncalibrated(), gates=thin_path_gates()
    ),
    "record_reset_failed": safety_record(
        verdict=Verdict.model_validate(
            {**as_json(integrity()), "gate": "side_effects"}
        ),
        gates=(reset_failed(),),
        guardrails=(),
        samples_path=None,
    ),
    "metric_reported_resolution": MetricSpec(
        kind="reported",
        direction="lower_is_better",
        unit="ms",
        resolution=0.001,
    ),
    "correctness_tolerance": CorrectnessSpec.model_validate(
        {
            **as_json(correctness()),
            "differential_rtol": 1e-12,
            "differential_atol": 1e-15,
        }
    ),
    "sample_resolved": resolved_sample(),
    "differential_capped": DifferentialCheck.model_validate(
        {**as_json(differential()), "capture_limit": 50}
    ),
    "diagnostics_coarse": resolution_stop().diagnostics,
    "verdict_resolution_stop": resolution_stop(),
    "verdict_calibrated": calibrated_keep(),
    "library_file": library_file(),
    "library_cache": library_cache(),
    "library_weak": library_weak(),
    "environment_native": native_environment(),
    "environment_native_unlisted": TreeEnvironment(
        version="candidate",
        runtime_kind="native_build",
        runtime_executable="/work/candidate/build/bench",
        env_names=(),
        dependency_locks=(),
        missing={"linked_libraries": "listed by the runtime gate"},
    ),
    "record_named": record(
        repository=REPOSITORY,
        candidate_commit="f" * 40,
        candidate_dirty=False,
        verdict=calibrated_keep(),
        environments=(native_environment(), native_environment("candidate")),
    ),
    "record_resolution_stop": record(verdict=resolution_stop()),
}


HASH_C = "c" * 64
HASH_D = "d" * 64


def provenance(**changes: Any) -> Provenance:
    data: dict[str, Any] = {
        "files": [
            {"path": "src/app/io.py", "sha256": HASH_A},
            {"path": "bench/run.py", "sha256": HASH_B},
        ],
        "commands": [["python", "-m", "pytest", "--collect-only"]],
        "perfproof_version": "0.1.0",
        "derived_at": T0,
        **changes,
    }
    return Provenance.model_validate(data)


def confirmation() -> Confirmation:
    return Confirmation(session="s-1", confirmed_at=T0)


def invalidation() -> Invalidation:
    return Invalidation(
        trigger="file_changed", detail="src/app/io.py", detected_at=T0
    )


def entry_data(kind: str, **changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "kind": kind,
        "id": f"{kind}-1",
        "status": "proposed",
        "provenance": as_json(provenance()),
    }
    extra: dict[str, Any]
    if kind == "target":
        extra = {
            "target_class": "python_cpu",
            "evidence": ["pyproject.toml declares a Python package"],
        }
    elif kind == "workload":
        extra = {
            "target_id": "target-1",
            "source": "existing_benchmark",
            "command": ["python", "bench/run.py"],
            "cwd": ".",
        }
    elif kind == "goal":
        extra = {
            "workload_id": "workload-1",
            "metric": "wall_time",
            "direction": "lower_is_better",
            "unit": "s",
            "bar": {"kind": "ratio", "value": 1.2},
            "guardrails": [as_json(memory_guardrail())],
        }
    elif kind == "constraint":
        extra = {
            "constraint": "numeric_tolerance",
            "subject": "frames output",
            "rtol": 1e-9,
            "atol": 0.0,
        }
    else:
        extra = {
            "workload_id": "workload-1",
            "setup": [["uv", "sync"]],
            "build": [],
            "reset": "none_needed",
            "services": [
                {
                    "name": "db",
                    "command": ["docker", "compose", "up", "db"],
                    "timeout_seconds": 60.0,
                }
            ],
        }
    return {**base, **extra, **changes}


ENTRY_CLASSES: dict[str, type[BaseModel]] = {
    "target": TargetEntry,
    "workload": WorkloadEntry,
    "goal": GoalEntry,
    "constraint": ConstraintEntry,
    "how_to_run": HowToRunEntry,
}


def entry(kind: str, **changes: Any) -> Any:
    return ENTRY_CLASSES[kind].model_validate(entry_data(kind, **changes))


CONFIRMED: dict[str, Any] = {
    "status": "confirmed",
    "confirmation": as_json(confirmation()),
}
UNKNOWN_GOAL: dict[str, Any] = {
    "metric": None,
    "direction": None,
    "unit": None,
    "bar": None,
    "guardrails": None,
    "missing": {
        "metric": "no benchmark names one",
        "direction": "metric unknown",
        "unit": "metric unknown",
        "bar": "the developer has not said",
        "guardrails": "not asked yet",
    },
}


def project_model(**changes: Any) -> ProjectModel:
    data: dict[str, Any] = {
        "updated_at": T0,
        "targets": [entry_data("target")],
        "workloads": [entry_data("workload")],
        "goals": [entry_data("goal")],
        "constraints": [entry_data("constraint")],
        "how_to_run": [entry_data("how_to_run")],
        **changes,
    }
    return ProjectModel.model_validate(data)


def evidence(kind: str = "measurement", sha: str = HASH_A) -> dict[str, Any]:
    return {"kind": kind, "path": f"scratch/{kind}.json", "sha256": sha}


ANCHORS: dict[str, Any] = {
    "workload": "parse",
    "workload_spec_hash": HASH_B,
    "code": [
        {
            "file": "src/app/io.py",
            "qualified_name": "Reader.parse",
            "source_hash": HASH_C,
        }
    ],
    "entries": [{"kind": "goal", "id": "goal-1", "version": 1}],
}
AMDAHL: dict[str, Any] = {
    "kind": "amdahl",
    "share": 0.25,
    "upper_bound": 1 / 0.75,
    "derivation": "parse is 25% of wall time in the profile",
}
SPEEDUP: dict[str, Any] = {
    "estimate": 1.08,
    "interval": as_json(interval()),
}

EVENTS: dict[str, dict[str, Any]] = {
    "observation": {"anchors": ANCHORS, "evidence": [evidence("profile")]},
    "mechanism": {
        "claim": "parse re-reads the file for every row",
        "would_be_false_if": "the file is opened once per run",
        "evidence": [evidence("profile")],
    },
    "prediction": {
        "cap": AMDAHL,
        "expect_if_true": "stubbing parse removes about 25% of wall time",
        "expect_if_false": "wall time barely moves",
        "evidence": [evidence("profile")],
    },
    "falsification": {
        "test": "stub_out",
        "description": "ran the workload with parse stubbed out",
        "result": "supported",
        "evidence": [evidence("cheap_test", HASH_D)],
    },
    "implementation": {
        "diff_hash": HASH_C,
        "worktree": ".claude/worktrees/parse-cache",
        "approach": "cache the opened file across rows",
        "evidence": [evidence("diff", HASH_C)],
    },
    "outcome": {
        "verdict": "KEEP",
        "reason": "performance",
        "rule": 2,
        "calibration": "uncalibrated",
        "bar": 1.03,
        "run_record_hash": HASH_D,
        "speedup": SPEEDUP,
        "evidence": [evidence("run_record", HASH_D)],
    },
    "skipped": {
        "step": "falsification",
        "reason": "no cheap test isolates the allocator",
    },
    "reordered": {
        "before": "h-other",
        "reason": "cheaper to try first and shares a code path",
    },
    "stale": {
        "anchor": "code",
        "detail": "Reader.parse changed",
        "evidence": [evidence("measurement", HASH_D)],
    },
    "revalidated": {
        "anchors": ANCHORS,
        "what_differs": "parse now streams rows",
        "evidence": [evidence("profile", HASH_D)],
    },
}
EVENT_CLASSES: dict[str, type[BaseModel]] = {
    "observation": ObservationEvent,
    "mechanism": MechanismEvent,
    "prediction": PredictionEvent,
    "falsification": FalsificationEvent,
    "implementation": ImplementationEvent,
    "outcome": OutcomeEvent,
    "skipped": SkippedEvent,
    "reordered": ReorderedEvent,
    "stale": StaleEvent,
    "revalidated": RevalidatedEvent,
}


def event_data(name: str, **changes: Any) -> dict[str, Any]:
    return {
        "hypothesis_id": "h-1",
        "event_id": f"e-{name}",
        "recorded_at": T0,
        "session": "s-1",
        "event": name,
        **EVENTS[name],
        **changes,
    }


def without(data: dict[str, Any], key: str) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k != key}


def event(name: str, **changes: Any) -> Any:
    return EVENT_CLASSES[name].model_validate(event_data(name, **changes))


EXAMPLES.update(
    {
        "file_hash": FileHash(path="src/app/io.py", sha256=HASH_A),
        "provenance": provenance(),
        "confirmation": confirmation(),
        "invalidation": invalidation(),
        "target_entry": entry("target"),
        "workload_entry": entry("workload"),
        "workload_entry_with_spec": entry(
            "workload", spec_hash=HASH_B, cwd="bench", **CONFIRMED
        ),
        "goal_bar": GoalBar(kind="absolute", value=0.2),
        "goal_entry": entry("goal"),
        "goal_entry_unknown": entry("goal", **UNKNOWN_GOAL),
        "constraint_entry": entry("constraint"),
        "service": ServiceSpec(
            name="db", command=("docker", "start", "db"), timeout_seconds=30.0
        ),
        "how_to_run_entry": entry("how_to_run"),
        "project_model": project_model(),
        "project_history": ProjectHistoryRecord(
            recorded_at=T0,
            event="confirmed",
            entry=entry("workload", **CONFIRMED),
            note="confirmed by the developer",
        ),
        "evidence_ref": EvidenceRef.model_validate(evidence()),
        "code_anchor": CodeAnchor.model_validate(ANCHORS["code"][0]),
        "entry_ref": EntryRef.model_validate(ANCHORS["entries"][0]),
        "anchors": Anchors.model_validate(ANCHORS),
        "cap": Cap.model_validate(AMDAHL),
        "cap_model": Cap(
            kind="model",
            upper_bound=3.0,
            derivation="launch overhead bounds the kernel at 3x",
        ),
        "measured_speedup": MeasuredSpeedup.model_validate(SPEEDUP),
        **{f"event_{n}": event(n) for n in EVENTS},
    }
)


def with_value(data: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """Return a deep copy of ``data`` with the dotted ``path`` set."""
    out = copy.deepcopy(data)
    *parents, last = path.split(".")
    node: Any = out
    for key in parents:
        if isinstance(node, list):
            node = cast("list[Any]", node)[int(key)]
        else:
            node = node[key]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return out


# Round trips


@pytest.mark.parametrize("name", list(EXAMPLES))
def test_json_round_trip(name: str) -> None:
    model = EXAMPLES[name]
    cls = type(model)
    assert cls.model_validate_json(model.model_dump_json()) == model
    assert cls.model_validate(as_json(model)) == model


def test_every_model_has_a_round_trip_example() -> None:
    assert {type(m) for m in EXAMPLES.values()} == set(MODELS)


def test_round_trip_is_byte_stable() -> None:
    text = record().model_dump_json()
    assert RunRecord.model_validate_json(text).model_dump_json() == text


def test_models_are_frozen() -> None:
    v = keep()
    with pytest.raises(ValidationError, match="frozen"):
        v.estimate = 2.0  # type: ignore[misc]


def test_records_copy_pickle_and_hash() -> None:
    r = record()
    # Round-trips bytes this test just produced, nothing untrusted.
    copies = [
        pickle.loads(pickle.dumps(r)),  # noqa: S301
        copy.deepcopy(r),
        r.model_copy(deep=True),
        RunRecord.model_validate_json(r.model_dump_json()),
    ]
    for c in copies:
        # Each copy is equal, keeps the pinned spec hash, and stays
        # read-only through its nested mappings.
        assert c == r
        assert c.spec.spec_hash == PINNED_SPEC_HASH
        with pytest.raises(TypeError):
            c.spec.env["OMP_NUM_THREADS"] = "8"  # type: ignore[index]
        with pytest.raises(TypeError):
            c.environments[0].env_values["X"] = "1"  # type: ignore[index]
    assert hash(spec()) == hash(spec())
    assert hash(machine()) == hash(machine())
    assert hash(environment()) == hash(environment())


@pytest.mark.parametrize(
    "mapping",
    [
        spec().env,
        machine().missing,
        environment().env_values,
        gpu_no_clocks().missing,
        BenchmarkSpec.model_validate({**as_json(spec()), "env": {}}).env,
    ],
)
def test_mapping_fields_are_read_only(mapping: Any) -> None:
    with pytest.raises(TypeError):
        mapping["OMP_NUM_THREADS"] = "8"


def test_root_documents_carry_schema_version() -> None:
    for model in (spec(), EXAMPLES["lock_manifest"], record()):
        assert as_json(model)["schema_version"] == schema.SCHEMA_VERSION
    for version in (1, 2, 4):
        data = with_value(as_json(record()), "schema_version", version)
        with pytest.raises(ValidationError):
            RunRecord.model_validate(data)


# Rejections: each case takes a valid example, changes one value through
# its JSON form, and must fail validation.

REJECTED: list[tuple[str, str, Any]] = [
    # negative or zero counts
    ("sample", "round", -1),
    ("sample", "max_rss_bytes", -1),
    ("sample", "session", -1),
    ("budget", "max_rounds", 0),
    ("spec", "warmup", -1),
    ("locked_file", "size_bytes", -1),
    ("verdict_keep", "rounds", -1),
    ("verdict_keep", "looks", -1),
    ("machine", "logical_cores", 0),
    ("hotspot", "line", 0),
    # unknown enum values
    ("metric", "kind", "gpu_time"),
    ("metric", "direction", "faster"),
    ("verdict_keep", "outcome", "MAYBE"),
    ("verdict_keep", "reason", "speed"),
    ("verdict_keep", "procedure", "eyeball"),
    ("verdict_integrity", "gate", "vibes"),
    ("sample", "version", "both"),
    ("sample", "position", 2),
    ("gate", "status", "skipped"),
    ("locked_file", "role", "docs"),
    ("hotspot", "frame_kind", "java"),
    ("machine", "power_source", "solar"),
    ("output_check", "compare", "fuzzy"),
    # non-finite or out-of-range numbers
    ("verdict_keep", "estimate", math.nan),
    ("verdict_keep", "estimate", math.inf),
    ("verdict_keep", "estimate", 0.0),
    ("verdict_keep", "interval.lower", math.nan),
    ("interval", "lower", -1.0),
    ("interval", "confidence", 1.0),
    ("interval", "confidence", 0.0),
    ("sample", "value", 0.0),
    ("sample", "wall_seconds", math.nan),
    ("diagnostics", "lag1_autocorrelation", 1.5),
    ("hotspot", "self_fraction", 1.2),
    ("machine", "battery_percent", 101.0),
    ("spec", "alpha", 1.0),
    ("spec", "min_effect", -0.01),
    ("budget", "max_seconds", 0.0),
    # wrong types are not coerced
    ("sample", "round", "3"),
    ("sample", "value", "0.1"),
    ("spec", "warmup", 2.5),
    ("verdict_keep", "regression", "false"),
    ("verdict_keep", "rule", 2.0),
    ("verdict_keep", "rule", True),
    ("spec", "schema_version", True),
    ("record", "schema_version", 3.0),
    # malformed identifiers and paths
    ("sample", "command_hash", "ABC"),
    ("record", "baseline_commit", "main"),
    ("spec", "name", "../escape"),
    ("spec", "cwd", "/abs"),
    ("spec", "cwd", "a/../b"),
    ("spec", "harness.0", "a/./b.py"),
    ("spec", "harness.0", "bench.py/"),
    ("spec", "harness.0", "dir\\bench.py"),
    ("spec", "command", []),
    ("spec", "env", {"BAD-NAME": "1"}),
    ("warning", "code", "Not Snake"),
    ("environment", "session", -1),
    ("environment", "version", "both"),
    ("environment", "env_names.0", ""),
    ("dependency_lock", "sha256", "x"),
    ("dependency_lock", "path", "/abs/uv.lock"),
    ("gpu", "sm_clock_mhz", 0.0),
    # safety checks: one bad value each
    ("verdict_keep", "reason", "memory"),
    ("gate", "gate", "network"),
    ("gate_not_available", "status", "unavailable"),
    ("spec_safety", "objective", "Wall Time"),
    ("spec_safety", "allow_remote_services", "yes"),
    ("spec_safety", "allow_unchecked_coverage", 1),
    ("spec_safety", "guardrails.0.metric", "peak memory"),
    ("spec_safety", "reset.command", []),
    ("reset", "command", []),
    ("reset", "command.0", ""),
    ("reset", "cwd", "/abs"),
    ("reset", "timeout_seconds", 0.0),
    ("reset", "timeout_seconds", math.inf),
    ("limit", "value", 0.0),
    ("limit", "value", -1.0),
    ("limit", "value", math.nan),
    ("limit", "label", ""),
    ("guardrail_spec", "direction", "worse"),
    ("guardrail_spec", "unit", ""),
    ("guardrail_spec", "allowance", -0.1),
    ("guardrail_spec", "allowance", math.inf),
    ("guardrail_spec", "allowance", "10%"),
    ("guardrail_spec", "allowance_source", "guess"),
    ("guardrail_spec", "applicable", "false"),
    ("guardrail_passed", "status", "fine"),
    ("guardrail_passed", "metric", "Peak"),
    ("guardrail_passed", "estimate", 0.0),
    ("guardrail_passed", "estimate", math.nan),
    ("guardrail_passed", "worsening.lower", -1.0),
    ("guardrail_passed", "candidate.upper", math.inf),
    ("quarantined", "test_id", ""),
    ("quarantined", "baseline_results", ["passed"]),
    ("quarantined", "baseline_results.0", "Passed!"),
    ("skipped", "stage", "run"),
    ("skipped", "reason", ""),
    ("skipped", "count", 0),
    ("differential", "function", ""),
    ("differential", "captured", -1),
    ("differential", "generated", -1),
    ("differential", "mismatches", 1.0),
    ("unverified", "path", "/abs/io.py"),
    ("unverified", "lines", []),
    ("unverified", "lines.0", 0),
    ("command_output", "command", []),
    ("command_output", "exit_code", 1.0),
    ("command_output", "timed_out", 0),
    ("diagnostics_uncalibrated", "procedure_uncalibrated", "true"),
    # method follow-ups: one bad value each
    ("metric_reported_resolution", "resolution", 0.0),
    ("metric_reported_resolution", "resolution", -0.001),
    ("metric_reported_resolution", "resolution", math.inf),
    ("metric_reported_resolution", "resolution", "0.001"),
    ("correctness_tolerance", "differential_rtol", -1e-9),
    ("correctness_tolerance", "differential_atol", math.inf),
    ("correctness_tolerance", "differential_rtol", "0"),
    ("sample_resolved", "resolution", 0.0),
    ("sample_resolved", "resolution", math.nan),
    ("sample_resolved", "resolution_source", "guessed"),
    ("differential_capped", "capture_limit", 0),
    ("differential_capped", "capture_limit", 50.0),
    ("diagnostics_coarse", "coarse_resolution", "true"),
    ("diagnostics_coarse", "resolution_widening", -0.1),
    ("diagnostics_coarse", "resolution_widening", math.inf),
    ("verdict_calibrated", "calibration_conditions.0", ""),
    ("library_file", "name", ""),
    ("library_file", "source", "rom"),
    ("library_file", "sha256", "ABC"),
    ("library_file", "path", ""),
    ("environment_native", "runtime_kind", "jit"),
    ("environment_native", "runtime_kind", "unknown"),
    ("environment_native", "linked_libraries.0.source", "rom"),
    ("record_named", "repository", ""),
    ("record_named", "candidate_commit", "HEAD"),
    ("record_named", "candidate_dirty", "false"),
    # unknown fields are refused, not dropped
    ("verdict_keep", "speedup", 1.08),
    ("sample", "note", "x"),
    ("guardrail_spec", "budget", 0.1),
    ("guardrail_passed", "allowance", 0.1),
]


@pytest.mark.parametrize(("name", "path", "value"), REJECTED)
def test_rejects_invalid_value(name: str, path: str, value: Any) -> None:
    model = EXAMPLES[name]
    data = with_value(as_json(model), path, value)
    with pytest.raises(ValidationError):
        type(model).model_validate(data)


def test_rejects_nan_in_json_text() -> None:
    text = keep().model_dump_json().replace("1.08", "NaN", 1)
    assert "NaN" in text
    with pytest.raises(ValidationError, match="finite"):
        Verdict.model_validate_json(text)


# Verdict consistency with docs/method.md


@pytest.mark.parametrize("factory", [integrity, correctness_reject])
@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        ("estimate", 1.2, "carries no speedup estimate"),
        (
            "interval",
            {"lower": 1.1, "upper": 1.3, "confidence": 0.99},
            "carries no speedup estimate",
        ),
        (
            "diagnostics.wall_interval",
            {"lower": 1.1, "upper": 1.3, "confidence": 0.99},
            "carries no speedup estimate",
        ),
        ("regression", True, "regression"),
    ],
)
def test_gate_verdict_carries_no_speed_claim(
    factory: Any, path: str, value: Any, message: str
) -> None:
    # After rounds and looks, so only the no-speed-claim rule refuses.
    data = {**as_json(factory()), "rounds": 30, "looks": 2}
    data = with_value(data, path, value)
    with pytest.raises(ValidationError, match=message):
        Verdict.model_validate(data)


def test_gate_verdict_after_rounds_is_allowed() -> None:
    # The procedure and same-host gates are re-checked on finished
    # samples, so an integrity REJECT can follow measured rounds.
    data = with_value(as_json(integrity()), "rounds", 30)
    data["gate"] = "same_host"
    assert Verdict.model_validate(data).rounds == 30


INCONSISTENT: list[tuple[str, dict[str, Any]]] = [
    ("verdict_keep", {"reason": "insufficient_evidence"}),
    ("verdict_keep", {"rule": 3}),
    ("verdict_keep", {"outcome": "REJECT"}),
    ("verdict_keep", {"gate": "lock"}),
    ("verdict_keep", {"interval": None}),
    ("verdict_keep", {"estimate": None}),
    ("verdict_keep", {"looks": 0}),
    ("verdict_keep", {"regression": True}),
    ("verdict_regression", {"regression": False}),
    (
        "verdict_regression",
        {"interval": {"lower": 0.95, "upper": 1.01, "confidence": 0.99}},
    ),
    ("verdict_regression", {"interval": None}),
    ("verdict_inconclusive", {"reason": "performance"}),
    ("verdict_inconclusive", {"regression": True}),
    ("verdict_inconclusive", {"looks": 0}),
    ("verdict_keep", {"rounds": 1}),
    ("verdict_integrity", {"gate": None}),
    ("verdict_integrity", {"gate": "correctness"}),
    ("verdict_correctness", {"gate": "lock"}),
    ("verdict_integrity", {"rule": 3}),
    ("verdict_integrity", {"outcome": "INCONCLUSIVE"}),
    (
        "verdict_keep",
        {
            "target_class": None,
            "calibration": "calibrated",
            "calibration_conditions": ["log-normal, CV 5%, one host"],
        },
    ),
    (
        "verdict_inconclusive",
        {"diagnostics": {"metric_disagreement": True}},
    ),
    # A guardrail REJECT is rule 2 and keeps its estimate and interval.
    ("verdict_guardrail", {"rule": 3}),
    ("verdict_guardrail", {"rule": 5}),
    ("verdict_guardrail", {"rule": 1, "gate": "lock"}),
    ("verdict_guardrail", {"estimate": None}),
    ("verdict_guardrail", {"interval": None}),
    ("verdict_guardrail", {"regression": True}),
    ("verdict_keep", {"outcome": "INCONCLUSIVE", "reason": "guardrail"}),
    ("verdict_keep", {"outcome": "KEEP", "reason": "guardrail"}),
    ("verdict_regression", {"reason": "guardrail"}),
    ("verdict_inconclusive", {"outcome": "REJECT", "reason": "guardrail"}),
    # A blocked KEEP is rule 2 INCONCLUSIVE, with its interval.
    ("verdict_blocked", {"reason": "performance"}),
    ("verdict_blocked", {"interval": None}),
    ("verdict_blocked", {"gate": "coverage"}),
    # Gate reasons: coverage is correctness, side_effects is integrity.
    ("verdict_integrity", {"gate": "coverage"}),
    ("verdict_correctness", {"gate": "side_effects"}),
    # An uncalibrated procedure gives only rule 5 or a gate REJECT.
    *(
        (name, {"diagnostics": {"procedure_uncalibrated": True}})
        for name in (
            "verdict_keep",
            "verdict_regression",
            "verdict_guardrail",
            "verdict_blocked",
        )
    ),
    (
        "verdict_uncalibrated",
        {
            "calibration": "calibrated",
            "target_class": "python_cpu",
            "calibration_conditions": ["log-normal, CV 5%, one host"],
        },
    ),
    # The resolution stop is rule 1 INCONCLUSIVE, at a look, with no
    # gate and with coarse_resolution; nothing else has that flag set.
    ("verdict_resolution_stop", {"gate": "lock"}),
    ("verdict_resolution_stop", {"looks": 0}),
    ("verdict_resolution_stop", {"reason": "performance"}),
    (
        "verdict_resolution_stop",
        {"diagnostics": {"procedure_uncalibrated": False}},
    ),
    (
        "verdict_resolution_stop",
        {
            "diagnostics": {
                "procedure_uncalibrated": False,
                "coarse_resolution": False,
            }
        },
    ),
    *(
        (
            name,
            {
                "diagnostics": {
                    "procedure_uncalibrated": False,
                    "coarse_resolution": True,
                }
            },
        )
        for name in (
            "verdict_keep",
            "verdict_regression",
            "verdict_inconclusive",
            "verdict_guardrail",
            "verdict_blocked",
        )
    ),
    (
        "verdict_integrity",
        {"outcome": "INCONCLUSIVE", "reason": "insufficient_evidence"},
    ),
    # Calibration conditions go with a calibrated verdict, and only it.
    ("verdict_calibrated", {"calibration_conditions": []}),
    ("verdict_keep", {"calibration_conditions": list(CONDITIONS)}),
]


@pytest.mark.parametrize("gate", ["side_effects", "coverage"])
def test_new_gates_give_their_reason(gate: GateName) -> None:
    data = {**as_json(integrity()), "gate": gate}
    data["reason"] = {"side_effects": "integrity", "coverage": "correctness"}[
        gate
    ]
    assert Verdict.model_validate(data).gate == gate


def test_gate_reason_covers_every_gate() -> None:
    assert set(GATE_REASON) == set(EVERY_GATE)
    assert {g for g, r in GATE_REASON.items() if r == "correctness"} == {
        "correctness",
        "coverage",
    }
    assert set(GATE_REASON.values()) == {"correctness", "integrity"}
    with pytest.raises(TypeError):
        GATE_REASON["lock"] = "correctness"  # type: ignore[index]


def test_uncalibrated_gate_reject_is_allowed() -> None:
    data = {
        **as_json(integrity()),
        "diagnostics": {"procedure_uncalibrated": True},
    }
    assert Verdict.model_validate(data).diagnostics.procedure_uncalibrated


@pytest.mark.parametrize(("name", "update"), INCONSISTENT)
def test_rejects_inconsistent_verdict(
    name: str, update: dict[str, Any]
) -> None:
    data = {**as_json(EXAMPLES[name]), **update}
    with pytest.raises(ValidationError):
        Verdict.model_validate(data)


def test_inconclusive_without_interval_is_allowed() -> None:
    # A budget can run out before two rounds exist to form an interval.
    data = {**as_json(inconclusive()), "estimate": None, "interval": None}
    data["diagnostics"] = {"procedure_uncalibrated": False}
    data["looks"] = 0
    data["rounds"] = 1
    assert Verdict.model_validate(data).interval is None


# Cross-field rules on the other models


@pytest.mark.parametrize(
    ("name", "update"),
    [
        ("metric", {"direction": "higher_is_better"}),
        ("metric", {"unit": "ms"}),
        ("metric", {"event": "cycles"}),
        ("metric_counter", {"event": None}),
        ("metric_counter", {"direction": "higher_is_better"}),
        ("output_check", {"rtol": 0.1}),
        ("output_check", {"equal_nan": True}),
        ("correctness", {"command": None, "outputs": []}),
        ("correctness", {"timeout_seconds": None}),
        ("correctness", {"command": None}),
        ("correctness", {"command": []}),
        ("looks", {"n0": 9}),
        ("interval", {"lower": 1.2, "upper": 1.1}),
        ("hotspot", {"self_fraction": 0.8, "total_fraction": 0.7}),
        ("hotspot", {"file": None}),
        ("hotspot", {"file": ""}),
        ("difference", {"baseline": None}),
        ("spec", {"harness": ["."]}),
        ("machine", {"host_id": None}),
        ("machine", {"missing": {}}),
        (
            "machine",
            {"missing": {**machine().missing, "host_id": "probe failed"}},
        ),
        ("machine", {"missing": {**machine().missing, "no_such": "x"}}),
        ("gpu", {"sm_clock_mhz": None}),
        ("gpu_no_clocks", {"missing": {}}),
        ("gpu_no_clocks", {"sm_clock_mhz": 1980.0}),
        ("environment", {"runtime_version": None}),
        ("environment", {"env_values": {"NOT_LISTED": "1"}}),
        ("environment", {"env_names": ["HOME", "HOME"]}),
        (
            "environment",
            {"dependency_locks": [as_json(EXAMPLES["dependency_lock"])] * 2},
        ),
        ("environment_unknown", {"env_values": {"HOME": "/root"}}),
        ("gate", {"detail": ""}),
        ("gate", {"gate": "lock", "status": "waived"}),
        ("spec", {"harness": []}),
        ("spec", {"oracle": ["bench.py"]}),
        ("spec", {"budget": {"max_rounds": 9, "max_seconds": 60.0}}),
        ("lock_manifest", {"files": [as_json(EXAMPLES["locked_file"])] * 2}),
        # guardrail specs
        ("guardrail_spec", {"allowance": None}),
        ("guardrail_spec", {"allowance_source": None}),
        (
            "guardrail_spec",
            {"allowance": None, "allowance_source": None, "limits": []},
        ),
        ("guardrail_spec", {"applicable": False, "reason": "x"}),
        ("guardrail_spec_na", {"reason": ""}),
        (
            "guardrail_spec_na",
            {"allowance": 0.1, "allowance_source": "project"},
        ),
        ("guardrail_spec_na", {"limits": [as_json(EXAMPLES["limit"])]}),
        ("guardrail_spec_limits_only", {"limits": []}),
        # the primary objective and the guardrails
        ("spec_safety", {"objective": None}),
        ("spec_safety", {"objective": "peak_memory"}),
        ("spec_safety", {"objective": "p99_latency"}),
        ("spec_safety", {"guardrails": [as_json(memory_guardrail())] * 2}),
        # quarantined tests and differential checks
        ("quarantined", {"baseline_results": ["passed"] * 3}),
        ("quarantined", {"baseline_results": ["failed", "failed"]}),
        ("differential", {"replayed": 49}),
        ("differential", {"captured": 51}),
        ("differential", {"skipped": []}),
        ("differential", {"mismatches": 0}),
        ("differential", {"mismatches": 149}),
        ("differential_clean", {"mismatches": 1}),
        (
            "differential",
            {"skipped": [as_json(EXAMPLES["skipped"])] * 2, "captured": 48},
        ),
        ("unverified", {"lines": [152, 152]}),
        # gate details belong to their gate and to a failure
        ("gate_correctness", {"gate": "lock"}),
        ("gate_correctness", {"status": "passed"}),
        ("gate_correctness", {"status": "not_available"}),
        (
            "gate_correctness",
            {"quarantined": [as_json(quarantined())] * 2},
        ),
        (
            "gate_correctness",
            {"differential": [as_json(differential())] * 2},
        ),
        ("gate_coverage", {"gate": "correctness"}),
        ("gate_coverage", {"status": "passed"}),
        ("gate_coverage", {"status": "waived"}),
        (
            "gate_coverage",
            {"unverified": [as_json(EXAMPLES["unverified"])] * 2},
        ),
        ("gate_reset", {"status": "passed", "detail": ""}),
        ("gate_reset", {"status": "waived"}),
        ("gate_not_available", {"detail": ""}),
        # guardrail results
        ("guardrail_unmeasurable", {"reason": ""}),
        ("guardrail_not_applicable", {"reason": ""}),
        ("guardrail_unmeasurable", {"estimate": 1.0}),
        (
            "guardrail_unmeasurable",
            {"candidate": as_json(iv(5e8, 6e8))},
        ),
        (
            "guardrail_not_applicable",
            {"estimate": 1.0, "worsening": as_json(iv(0.9, 1.1))},
        ),
        ("guardrail_passed", {"estimate": None}),
        ("guardrail_passed", {"worsening": None}),
        (
            "guardrail_passed",
            {"estimate": None, "worsening": None, "candidate": None},
        ),
        (
            "guardrail_breached",
            {"estimate": None, "worsening": None, "candidate": None},
        ),
        # only a reported metric declares its resolution
        ("metric", {"resolution": 0.001}),
        ("metric_counter", {"resolution": 1.0}),
        (
            "metric",
            {"kind": "cpu", "resolution": 1e-6},
        ),
        # a sample's resolution and its source go together
        ("sample_resolved", {"resolution": None}),
        ("sample_resolved", {"resolution_source": None}),
        ("sample", {"resolution": 0.001}),
        # captured calls never exceed the capture limit
        ("differential_capped", {"capture_limit": 49}),
        # a linked library's path and hash follow its source
        ("library_file", {"sha256": None}),
        ("library_file", {"path": None}),
        ("library_cache", {"sha256": HASH_A}),
        ("library_cache", {"path": None}),
        ("library_weak", {"path": "/usr/lib/libopt.dylib"}),
        ("library_weak", {"sha256": HASH_A}),
        # the runtime kind decides which fields apply
        (
            "environment",
            {
                "runtime_kind": None,
                "missing": {
                    "runtime_kind": "not probed",
                    "linked_libraries": "not probed",
                },
            },
        ),
        (
            "environment",
            {
                "runtime_kind": "native_build",
                "missing": {"linked_libraries": "not probed"},
            },
        ),
        ("environment", {"linked_libraries": []}),
        ("environment", {"missing": {"linked_libraries": "not probed"}}),
        ("environment_native", {"runtime_version": "3.12.12"}),
        (
            "environment_native",
            {
                "missing": {
                    "runtime_version": (
                        "a native executable built in the tree; it has no "
                        "interpreter"
                    )
                }
            },
        ),
        (
            "environment_native",
            {
                "runtime_executable": None,
                "missing": {"runtime_executable": "not found"},
            },
        ),
        (
            "environment_native",
            {"linked_libraries": [as_json(library_file())] * 2},
        ),
        ("environment_native_unlisted", {"missing": {}}),
        (
            "environment_unknown",
            {
                "linked_libraries": [],
                "missing": {
                    k: v
                    for k, v in EXAMPLES["environment_unknown"]
                    .model_dump()["missing"]
                    .items()
                    if k != "linked_libraries"
                },
            },
        ),
        (
            "environment_unknown",
            {
                "missing": {
                    k: v
                    for k, v in EXAMPLES["environment_unknown"]
                    .model_dump()["missing"]
                    .items()
                    if k != "runtime_kind"
                },
            },
        ),
    ],
)
def test_rejects_inconsistent_fields(
    name: str, update: dict[str, Any]
) -> None:
    model = EXAMPLES[name]
    data = {**as_json(model), **update}
    with pytest.raises(ValidationError):
        type(model).model_validate(data)


def test_waivable_gates() -> None:
    for gate in ("runtime", "same_host"):
        GateResult(gate=gate, status="waived", detail="spec opt-in")


def test_reported_metric_allows_either_direction() -> None:
    for direction in ("lower_is_better", "higher_is_better"):
        MetricSpec(kind="reported", direction=direction, unit="tokens/s")


def test_environment_records_empty_values() -> None:
    data = {**as_json(environment()), "env_values": {"OMP_NUM_THREADS": ""}}
    assert TreeEnvironment.model_validate(data).env_values == {
        "OMP_NUM_THREADS": ""
    }


def test_environment_names_and_locks_are_sorted() -> None:
    data = as_json(environment())
    data["dependency_locks"] = [
        {"path": "web/package-lock.json", "sha256": HASH_B},
        {"path": "uv.lock", "sha256": HASH_A},
    ]
    env = TreeEnvironment.model_validate(data)
    assert env.env_names == ("HOME", "OMP_NUM_THREADS", "PATH")
    assert [d.path for d in env.dependency_locks or ()] == [
        "uv.lock",
        "web/package-lock.json",
    ]


def test_lock_manifest_files_are_sorted() -> None:
    files = EXAMPLES["lock_manifest"].model_dump(mode="json")["files"]
    shuffled = {"spec_hash": HASH_B, "files": files[::-1]}
    manifest = LockManifest.model_validate(shuffled)
    assert [f.path for f in manifest.files] == ["bench.py", "data/in.bin"]


BAR = 1 + spec().min_effect


def cross(
    verdict: Verdict, pairs: int, interval: Interval | None = None
) -> Verdict:
    data: dict[str, Any] = {
        **as_json(verdict),
        "cross_host": True,
        "session_pairs": pairs,
        "diagnostics": {"procedure_uncalibrated": False},
    }
    if interval is not None:
        data["interval"] = as_json(interval)
    return Verdict.model_validate(data)


@pytest.mark.parametrize(
    "update",
    [
        {"verdict": integrity()},
        {
            "verdict": integrity(),
            "gates": (
                GateResult(gate="runtime", status="failed", detail="3.13"),
            ),
        },
        {"gates": (*gates_passed(), GateResult(gate="lock", status="passed"))},
        {"gates": ()},
        *(
            {"gates": without_gate(gates_passed(), gate)}
            for gate in EVERY_GATE
        ),
        {
            "verdict": inconclusive(),
            "gates": without_gate(gates_passed(), "same_host"),
        },
        {"verdict": keep().model_copy(update={"rounds": 201})},
        {"verdict": keep().model_copy(update={"looks": 6})},
        {"verdict": inconclusive()},
        {"environments": (environment(),)},
        {"environments": (environment(), environment())},
        {
            "machines": (machine(), machine()),
            "environments": (
                environment(),
                environment().model_copy(update={"session": 1}),
            ),
        },
        {
            "environments": (
                environment(),
                environment("candidate").model_copy(update={"session": 1}),
            )
        },
        {"finished_at": T0 - timedelta(seconds=1)},
        {"machines": ()},
        {"verdict": cross(keep(), 10)},
        {"samples_path": None},
        {
            "verdict": cross(keep(), 10),
            "spec": spec().model_copy(update={"allow_cross_host": True}),
            "gates": gates_passed(),
        },
        *(
            {"gates": with_gate(gates_passed(), gate, "waived")}
            for gate in ("side_effects", "runtime", "same_host", "coverage")
        ),
        {"started_at": T0.replace(tzinfo=None)},
        # the candidate commit and whether the tree had more go together
        {"candidate_commit": "f" * 40},
        {"candidate_dirty": True},
        # the resolution stop is no gate verdict: every gate is listed,
        # none failed, every guardrail has a result, and rounds ran
        {
            "verdict": resolution_stop(),
            "gates": without_gate(gates_passed(), "coverage"),
        },
        {"verdict": resolution_stop(), "gates": (lock_failed(),)},
        {"verdict": resolution_stop(), "guardrails": ()},
        {"verdict": resolution_stop(), "samples_path": None},
    ],
)
def test_rejects_inconsistent_record(update: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        record(**update)


def at_bar(rule: int, lower: float, upper: float) -> Verdict:
    """Return a validated verdict of ``rule`` with the given bounds."""
    base = {2: keep(), 3: regression(), 5: inconclusive()}[rule]
    return Verdict.model_validate(
        {
            **as_json(base),
            "interval": as_json(iv(lower, upper)),
            "regression": rule == 3 and upper < 1,
            "diagnostics": {"procedure_uncalibrated": False},
        }
    )


def near(where: str | float, bar: float) -> float:
    """Return the float64 just below, at or just above ``bar``."""
    if where == "below":
        return math.nextafter(bar, 0)
    if where == "bar":
        return bar
    if where == "above":
        return math.nextafter(bar, 2)
    return float(where)


@pytest.mark.parametrize("min_effect", [0.03, 0.0])
@pytest.mark.parametrize(
    ("rule", "bounds", "accepted"),
    [
        # KEEP needs L strictly above the bar.
        (2, ("below", 1.5), False),
        (2, ("bar", 1.5), False),
        (2, ("above", 1.5), True),
        (2, ("above", "above"), True),
        # A performance REJECT needs U at or below the bar.
        (3, (0.5, "below"), True),
        (3, (0.5, "bar"), True),
        (3, ("bar", "bar"), True),
        (3, (0.5, "above"), False),
        # INCONCLUSIVE needs L at or below the bar and U above it.
        (5, ("below", "above"), True),
        (5, ("bar", "above"), True),
        (5, (0.5, "bar"), False),
        (5, ("bar", "bar"), False),
        (5, ("above", 1.5), False),
    ],
)
def test_verdict_must_agree_with_the_bar(
    min_effect: float,
    rule: int,
    bounds: tuple[str | float, str | float],
    accepted: bool,
) -> None:
    bar = 1 + min_effect
    s = spec().model_copy(update={"min_effect": min_effect})
    v = at_bar(rule, near(bounds[0], bar), near(bounds[1], bar))
    if accepted:
        record(verdict=v, spec=s)
    else:
        with pytest.raises(ValidationError, match="contradicts the interval"):
            record(verdict=v, spec=s)


def cross_gates() -> tuple[GateResult, ...]:
    return with_gate(gates_passed(), "same_host", "waived", detail="opt-in")


def cross_record(
    verdict: Verdict, gates: tuple[GateResult, ...] | None = None
) -> RunRecord:
    return record(
        verdict=verdict,
        spec=spec().model_copy(update={"allow_cross_host": True}),
        gates=cross_gates() if gates is None else gates,
        machines=(machine(),) * (2 * (verdict.session_pairs or 1)),
    )


def test_session_pairs_need_their_sessions() -> None:
    with pytest.raises(ValidationError, match="session pairs need"):
        record(
            verdict=cross(keep(), 10),
            spec=spec().model_copy(update={"allow_cross_host": True}),
            gates=cross_gates(),
            machines=(machine(),) * 19,
        )


def gates_with_failure(
    on_cross_host: bool, failed: str
) -> tuple[GateResult, ...]:
    passed = cross_gates() if on_cross_host else gates_passed()
    return tuple(
        GateResult(gate=g.gate, status="failed", detail="differs")
        if g.gate == failed
        else g
        for g in passed
    )


@pytest.mark.parametrize("on_cross_host", [False, True])
@pytest.mark.parametrize("failed", EVERY_GATE)
def test_failed_gate_overrides_a_keep_worthy_interval(
    on_cross_host: bool, failed: str
) -> None:
    gates = gates_with_failure(on_cross_host, failed)
    if on_cross_host:
        with pytest.raises(ValidationError, match="must decide"):
            cross_record(cross(keep(), 10), gates)
    else:
        with pytest.raises(ValidationError, match="must decide"):
            record(gates=gates)


@pytest.mark.parametrize("on_cross_host", [False, True])
@pytest.mark.parametrize("failed", EVERY_GATE)
def test_failed_gate_rejects_before_any_session_pair(
    on_cross_host: bool, failed: GateName
) -> None:
    # A gate can fail before any remote session exists. The record then
    # holds the one machine that ran the gates and counts zero pairs.
    gates = gates_with_failure(on_cross_host, failed)
    still_cross = on_cross_host and failed != "same_host"
    verdict = Verdict.model_validate(
        {
            **as_json(integrity()),
            "reason": GATE_REASON[failed],
            "gate": failed,
            "cross_host": still_cross,
            "session_pairs": 0 if still_cross else None,
        }
    )
    spec_ = BenchmarkSpec.model_validate(
        {**as_json(spec()), "allow_cross_host": on_cross_host}
    )
    rec = record(verdict=verdict, spec=spec_, gates=gates, samples_path=None)
    assert rec.verdict.gate == failed
    assert rec.verdict.outcome == "REJECT"


@pytest.mark.parametrize(
    ("verdict", "accepted"),
    [
        # Below n0 = 10 pairs, only INCONCLUSIVE, whatever the interval.
        (cross(inconclusive(), 9, iv(1.04, 1.1)), True),
        (cross(inconclusive(), 9, iv(0.9, 1.0)), True),
        (cross(keep(), 9), False),
        (cross(regression(), 9), False),
        # From n0 pairs on, the usual rules apply.
        (cross(inconclusive(), 10, iv(1.04, 1.1)), False),
        (cross(inconclusive(), 10, iv(0.9, 1.0)), False),
        (cross(inconclusive(), 10, iv(1.0, 1.1)), True),
        (cross(keep(), 10), True),
        (cross(regression(), 10), True),
    ],
)
def test_cross_host_verdict_needs_n0_session_pairs(
    verdict: Verdict, accepted: bool
) -> None:
    if accepted:
        assert cross_record(verdict).verdict.cross_host
    else:
        with pytest.raises(ValidationError, match=r"n0|contradicts"):
            cross_record(verdict)


def test_session_pairs_go_with_cross_host() -> None:
    with pytest.raises(ValidationError, match="session_pairs"):
        Verdict.model_validate({**as_json(keep()), "cross_host": True})
    with pytest.raises(ValidationError, match="session_pairs"):
        Verdict.model_validate({**as_json(keep()), "session_pairs": 10})


@pytest.mark.parametrize(
    ("upper", "regression", "accepted"),
    [
        (1.0, False, True),
        (1.0, True, False),
        (math.nextafter(1.0, 0), True, True),
        (math.nextafter(1.0, 0), False, False),
    ],
)
def test_regression_flag_is_exactly_u_below_one(
    upper: float, regression: bool, accepted: bool
) -> None:
    data = {
        **as_json(EXAMPLES["verdict_regression"]),
        "interval": as_json(iv(0.9, upper)),
        "regression": regression,
    }
    if accepted:
        assert Verdict.model_validate(data).regression is regression
    else:
        with pytest.raises(ValidationError, match="regression"):
            Verdict.model_validate(data)


# Safety checks on the record

GUARDRAIL_RESULTS: dict[str, GuardrailResult] = {
    "passed": memory_result(),
    "breached": memory_result("breached", worsening=(1.15, 1.25)),
    "not_established": memory_result(
        "not_established", worsening=(1.05, 1.15)
    ),
    "unmeasurable": GuardrailResult(
        metric="peak_memory", status="unmeasurable", reason="no wait4 here"
    ),
}


@pytest.mark.parametrize(
    ("memory", "latency", "accepted"),
    [
        ("passed", "not_applicable", True),
        ("passed", "passed", True),
        ("breached", "not_applicable", False),
        ("not_established", "not_applicable", False),
        ("unmeasurable", "not_applicable", False),
        ("passed", "breached", False),
        ("passed", "not_established", False),
        ("passed", "unmeasurable", False),
    ],
)
def test_keep_needs_every_guardrail_passed_or_not_applicable(
    memory: str, latency: str, accepted: bool
) -> None:
    guardrails = [as_json(memory_guardrail())]
    latency_spec = {
        "metric": "p99_latency",
        "direction": "lower_is_better",
        "unit": "s",
    }
    if latency == "not_applicable":
        latency_spec |= {"applicable": False, "reason": NA_REASON}
        latency_result_ = latency_result()
    else:
        latency_spec |= {"allowance": 0.05, "allowance_source": "default"}
        worsening = {
            "passed": (0.98, 1.04),
            "breached": (1.06, 1.2),
            "not_established": (1.0, 1.1),
        }.get(latency)
        latency_result_ = GuardrailResult.model_validate(
            {
                "metric": "p99_latency",
                "status": latency,
                "estimate": None if worsening is None else 1.0,
                "worsening": None
                if worsening is None
                else as_json(iv(*worsening)),
                "reason": "no requests" if latency == "unmeasurable" else "",
            }
        )
    guardrails.append(latency_spec)
    rec = {
        "spec": safety_spec(guardrails=guardrails),
        "guardrails": (GUARDRAIL_RESULTS[memory], latency_result_),
    }
    if accepted:
        assert safety_record(**rec).verdict.outcome == "KEEP"
    else:
        with pytest.raises(ValidationError, match="a KEEP needs every"):
            safety_record(**rec)


@pytest.mark.parametrize("gate", EVERY_GATE)
def test_keep_is_refused_with_a_gate_not_available(gate: GateName) -> None:
    gates = with_gate(gates_passed(), gate, "not_available")
    with pytest.raises(ValidationError, match="gates not available"):
        safety_record(gates=gates)
    # The same evidence is a valid rule 2 INCONCLUSIVE: the KEEP was
    # blocked by the gate that could not be checked.
    rec = safety_record(gates=gates, verdict=keep_blocked())
    assert (rec.verdict.outcome, rec.verdict.rule) == ("INCONCLUSIVE", 2)


def breached_results(**update: Any) -> tuple[GuardrailResult, ...]:
    data = {**as_json(GUARDRAIL_RESULTS["breached"]), **update}
    return (GuardrailResult.model_validate(data), latency_result())


def test_guardrail_reject_keeps_its_estimate_and_both_intervals() -> None:
    rec = safety_record(
        verdict=guardrail_reject(), guardrails=breached_results()
    )
    assert rec.verdict.reason == "guardrail"
    assert rec.verdict.estimate is not None
    assert rec.verdict.interval is not None
    breach = rec.guardrails[0]
    assert breach.worsening is not None
    assert breach.candidate is not None
    stored = RunRecord.model_validate_json(rec.model_dump_json())
    assert stored == rec


@pytest.mark.parametrize(
    ("worsening", "candidate", "accepted"),
    [
        # The ratio check breaches (L = 1.30 > 1.10) with no candidate
        # interval: one check showing a breach is enough.
        ((1.30, 1.40), None, True),
        # The ceiling check breaches (9e9 > 8e9) with no ratio interval.
        (None, (9e9, 9.5e9), True),
        # Both measured, one breaching: every measured interval is kept.
        ((1.15, 1.25), (5e8, 6e8), True),
        # The breaching interval is missing: no breach is shown.
        (None, (5e8, 6e8), False),
        ((0.99, 1.05), None, False),
        (None, None, False),
    ],
)
def test_guardrail_reject_validates_only_with_its_breaching_interval(
    worsening: tuple[float, float] | None,
    candidate: tuple[float, float] | None,
    accepted: bool,
) -> None:
    def build() -> RunRecord:
        return safety_record(
            verdict=guardrail_reject(),
            guardrails=(
                memory_result("breached", worsening, candidate),
                latency_result(),
            ),
        )

    if accepted:
        rec = build()
        breach = rec.guardrails[0]
        assert (breach.worsening is None) == (worsening is None)
        assert (breach.candidate is None) == (candidate is None)
        assert RunRecord.model_validate_json(rec.model_dump_json()) == rec
    else:
        with pytest.raises(ValidationError, match=r"its checks|needs inter"):
            build()


@pytest.mark.parametrize("path", ["estimate", "interval"])
def test_guardrail_reject_needs_the_primary_estimate_and_interval(
    path: str,
) -> None:
    data = {**as_json(guardrail_reject()), path: None}
    with pytest.raises(ValidationError, match="estimate and interval"):
        Verdict.model_validate(data)


@pytest.mark.parametrize("status", ["passed", "not_established"])
def test_guardrail_reject_needs_a_breach(status: str) -> None:
    with pytest.raises(ValidationError, match="needs a breached guardrail"):
        safety_record(
            verdict=guardrail_reject(),
            guardrails=(GUARDRAIL_RESULTS[status], latency_result()),
        )


def test_guardrail_reject_needs_a_keep_worthy_interval() -> None:
    v = Verdict.model_validate(
        {**as_json(guardrail_reject()), "interval": as_json(iv(1.0, 1.1))}
    )
    with pytest.raises(ValidationError, match="contradicts the interval"):
        safety_record(verdict=v, guardrails=breached_results())


@pytest.mark.parametrize(
    ("memory", "gates", "accepted"),
    [
        ("not_established", None, True),
        ("unmeasurable", None, True),
        (
            "passed",
            with_gate(gates_passed(), "coverage", "not_available"),
            True,
        ),
        ("passed", None, False),
        ("breached", None, False),
        (
            "breached",
            with_gate(gates_passed(), "coverage", "not_available"),
            False,
        ),
    ],
)
def test_rule_2_inconclusive_needs_a_blocker_and_no_breach(
    memory: str, gates: tuple[GateResult, ...] | None, accepted: bool
) -> None:
    rec: dict[str, Any] = {
        "verdict": keep_blocked(),
        "guardrails": (GUARDRAIL_RESULTS[memory], latency_result()),
    }
    if gates is not None:
        rec["gates"] = gates
    if accepted:
        safety_record(**rec)
    else:
        with pytest.raises(ValidationError, match=r"blocked KEEP|REJECT"):
            safety_record(**rec)


def test_performance_verdicts_stand_alongside_a_breach() -> None:
    # A breach changes only a KEEP; a performance REJECT or a straddling
    # INCONCLUSIVE stands, with the guardrails reported alongside.
    for verdict in (regression(), inconclusive()):
        data: dict[str, Any] = {
            **as_json(verdict),
            "diagnostics": {"procedure_uncalibrated": False},
        }
        safety_record(
            verdict=Verdict.model_validate(data),
            guardrails=breached_results(),
        )


BAR_G = 1 + ALLOWANCE


@pytest.mark.parametrize(
    ("worsening", "candidate", "status"),
    [
        # Allowance: compliance inclusive at the bar, breach strict.
        ((0.9, BAR_G), (5e8, 6e8), "passed"),
        ((0.9, math.nextafter(BAR_G, 2)), (5e8, 6e8), "not_established"),
        ((BAR_G, 1.3), (5e8, 6e8), "not_established"),
        ((math.nextafter(BAR_G, 2), 1.3), (5e8, 6e8), "breached"),
        # Ceiling: compliance when the upper bound is within the limit,
        # a breach when the lower bound is beyond it.
        ((0.9, 1.0), (5e8, MEMORY_LIMIT), "passed"),
        (
            (0.9, 1.0),
            (5e8, math.nextafter(MEMORY_LIMIT, 1e10)),
            "not_established",
        ),
        ((0.9, 1.0), (MEMORY_LIMIT, 9e9), "not_established"),
        ((0.9, 1.0), (math.nextafter(MEMORY_LIMIT, 1e10), 9e9), "breached"),
        # Within its allowance, still beyond an absolute limit.
        ((0.98, 1.04), (8.5e9, 9e9), "breached"),
        # A breach of one check decides even while another is open.
        ((1.05, 1.15), (8.5e9, 9e9), "breached"),
        # Missing evidence: never passed, never breached.
        (None, (5e8, 6e8), "not_established"),
        ((0.9, 1.0), None, "not_established"),
        # One check breached is a breach, whatever the other is missing.
        ((1.30, 1.40), None, "breached"),
        (None, (9e9, 9.5e9), "breached"),
    ],
)
def test_guardrail_status_is_exactly_what_its_intervals_show(
    worsening: tuple[float, float] | None,
    candidate: tuple[float, float] | None,
    status: str,
) -> None:
    statuses = ("passed", "breached", "not_established")
    verdicts = {
        "passed": keep(),
        "breached": guardrail_reject(),
        "not_established": keep_blocked(),
    }

    def build(claimed: str) -> RunRecord:
        result = memory_result(claimed, worsening, candidate)
        return safety_record(
            verdict=verdicts[claimed], guardrails=(result, latency_result())
        )

    for claimed in statuses:
        if claimed == status:
            assert build(claimed).guardrails[0].status == status
        else:
            with pytest.raises(
                ValidationError, match=r"its checks|needs intervals"
            ):
                build(claimed)


def test_guardrail_floor_for_a_higher_is_better_metric() -> None:
    floor = 100.0
    throughput = GuardrailSpec(
        metric="throughput",
        direction="higher_is_better",
        unit="tokens/s",
        limits=(AbsoluteLimit(value=floor, label="throughput floor"),),
    )
    s = safety_spec(
        guardrails=[as_json(memory_not_applicable()), as_json(throughput)]
    )

    def result(status: str, lower: float, upper: float) -> RunRecord:
        verdict = {"passed": keep(), "breached": guardrail_reject()}.get(
            status, keep_blocked()
        )
        g = GuardrailResult.model_validate(
            {
                "metric": "throughput",
                "status": status,
                "candidate": as_json(iv(lower, upper)),
            }
        )
        return safety_record(
            spec=s,
            verdict=verdict,
            guardrails=(memory_not_applicable_result(), g),
        )

    result("passed", floor, 200.0)
    result("not_established", math.nextafter(floor, 0), 200.0)
    result("not_established", 50.0, floor)
    result("breached", 50.0, math.nextafter(floor, 0))
    with pytest.raises(ValidationError, match="its checks"):
        result("passed", math.nextafter(floor, 0), 200.0)


@pytest.mark.parametrize(
    ("guardrails", "message"),
    [
        ((memory_result(),), "must match the spec's guardrails"),
        (
            (memory_result(), latency_result(), memory_result()),
            "listed more than once",
        ),
        (
            (
                memory_result(),
                latency_result(),
                GuardrailResult(
                    metric="peak_gpu_memory", status="unmeasurable", reason="x"
                ),
            ),
            "must match the spec's guardrails",
        ),
        (
            (
                GuardrailResult(
                    metric="peak_memory", status="not_applicable", reason="x"
                ),
                latency_result(),
            ),
            "marks it applicable",
        ),
        (
            (
                memory_result(),
                GuardrailResult(
                    metric="p99_latency",
                    status="unmeasurable",
                    reason=NA_REASON,
                ),
            ),
            "marks it not",
        ),
        (
            (
                memory_result(),
                GuardrailResult(
                    metric="p99_latency",
                    status="not_applicable",
                    reason="another reason",
                ),
            ),
            "the spec's reason",
        ),
    ],
)
def test_guardrail_results_match_the_spec_one_to_one(
    guardrails: tuple[GuardrailResult, ...], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        safety_record(guardrails=guardrails)


def test_gate_verdict_carries_no_guardrail_results() -> None:
    with pytest.raises(ValidationError, match="no guardrail results"):
        safety_record(
            verdict=integrity(),
            gates=(lock_failed(),),
            samples_path=None,
        )


@pytest.mark.parametrize(
    ("gate", "opt_in"),
    [
        ("side_effects", "allow_remote_services"),
        ("runtime", "allow_runtime_change"),
        ("coverage", "allow_unchecked_coverage"),
    ],
)
def test_waivers_need_their_opt_in(gate: GateName, opt_in: str) -> None:
    gates = with_gate(gates_passed(), gate, "waived", detail="opt-in")
    with pytest.raises(ValidationError, match=f"{gate} gate waived"):
        safety_record(gates=gates)
    rec = safety_record(gates=gates, spec=safety_spec(**{opt_in: True}))
    assert rec.verdict.outcome == "KEEP"


@pytest.mark.parametrize(
    ("bounds", "straddles"),
    [
        # Method table rows: KEEP-worthy, regression, REJECT-worthy and
        # straddling intervals are all rule 5 on the thin path.
        ((1.05, 1.11), False),
        ((0.91, 0.97), False),
        ((0.99, 1.02), False),
        ((1.02, 1.08), True),
    ],
)
def test_thin_path_is_inconclusive_whatever_the_interval(
    bounds: tuple[float, float], straddles: bool
) -> None:
    base = Verdict.model_validate(
        {**as_json(keep()), "interval": as_json(iv(*bounds))}
    )
    v = uncalibrated(base)
    rec = safety_record(verdict=v, gates=thin_path_gates())
    assert (rec.verdict.outcome, rec.verdict.rule) == ("INCONCLUSIVE", 5)
    plain = Verdict.model_validate(
        {**as_json(v), "diagnostics": {"procedure_uncalibrated": False}}
    )
    if straddles:
        safety_record(verdict=plain, gates=thin_path_gates())
    else:
        # Without the diagnostic, the same interval contradicts rule 5.
        with pytest.raises(ValidationError, match="contradicts the interval"):
            safety_record(verdict=plain, gates=thin_path_gates())


def test_thin_path_gate_reject_is_allowed() -> None:
    v = Verdict.model_validate(
        {
            **as_json(integrity()),
            "gate": "side_effects",
            "diagnostics": {"procedure_uncalibrated": True},
        }
    )
    gates = (
        GateResult(gate="side_effects", status="failed", detail="db host"),
    )
    assert safety_record(
        verdict=v, gates=gates, guardrails=(), samples_path=None
    ).verdict.diagnostics.procedure_uncalibrated


def test_failed_gate_decides_over_one_not_available() -> None:
    gates = with_gate(thin_path_gates(), "lock", "failed")
    with pytest.raises(ValidationError, match="must decide"):
        safety_record(verdict=uncalibrated(), gates=gates)


def test_failed_correctness_gate_records_its_evidence() -> None:
    gate = correctness_failed()
    assert gate.quarantined[0].baseline_results == (
        "passed",
        "failed",
        "passed",
    )
    check = gate.differential[0]
    assert (check.captured, check.replayed, check.generated) == (50, 48, 100)
    assert [(s.stage, s.count) for s in check.skipped] == [
        ("capture", 3),
        ("replay", 2),
    ]
    assert check.differences[0].item == "call 17 return value"
    v = Verdict.model_validate(
        {**as_json(correctness_reject()), "rounds": 0, "looks": 0}
    )
    rec = record(verdict=v, gates=(gate,), samples_path=None)
    assert RunRecord.model_validate_json(rec.model_dump_json()) == rec


def test_unverified_lines_are_sorted_and_fail_coverage() -> None:
    gate = coverage_failed()
    assert gate.unverified[0].lines == (152, 158)
    v = Verdict.model_validate(
        {**as_json(correctness_reject()), "gate": "coverage"}
    )
    rec = record(verdict=v, gates=(gate,), samples_path=None)
    assert rec.verdict.reason == "correctness"


def test_reset_failure_keeps_its_output() -> None:
    rec = EXAMPLES["record_reset_failed"]
    assert isinstance(rec, RunRecord)
    (gate,) = rec.gates
    assert gate.output is not None
    assert gate.output.stderr == "connection refused"
    assert rec.verdict.reason == "integrity"


# Default guardrails: nothing is left implicit


def declared(objective: str | None, *metrics: str) -> BenchmarkSpec:
    """A spec declaring ``metrics`` not applicable, with a reason."""
    guardrails = [
        {
            "metric": m,
            "direction": "lower_is_better",
            "unit": "x",
            "applicable": False,
            "reason": f"{m} is not used by this workload",
        }
        for m in metrics
    ]
    return BenchmarkSpec.model_validate(
        {
            **as_json(spec()),
            "metric": as_json(metric_for(objective)),
            "objective": objective,
            "guardrails": guardrails,
        }
    )


def metric_for(objective: str | None) -> MetricSpec:
    """A metric whose kind can measure ``objective``."""
    if objective in (None, "wall_time"):
        return metric()
    direction = (
        "higher_is_better" if objective == "throughput" else "lower_is_better"
    )
    return MetricSpec(kind="reported", direction=direction, unit="x")


def declared_results(s: BenchmarkSpec) -> tuple[GuardrailResult, ...]:
    return tuple(
        GuardrailResult(
            metric=g.metric, status="not_applicable", reason=g.reason
        )
        for g in s.guardrails
    )


@pytest.mark.parametrize(
    "verdict",
    [
        keep(),
        regression(),
        # Without the wall interval, which a wall metric never has.
        inconclusive().model_copy(update={"diagnostics": calibrated()}),
        keep_blocked(),
    ],
)
def test_every_non_gate_verdict_needs_peak_memory_declared(
    verdict: Verdict,
) -> None:
    bare = declared(None)
    gates = gates_passed()
    if verdict.rule == 2 and verdict.outcome == "INCONCLUSIVE":
        gates = with_gate(gates, "coverage", "not_available")
    with pytest.raises(ValidationError, match=r"\['peak_memory'\]"):
        record(verdict=verdict, spec=bare, guardrails=(), gates=gates)
    # Declared not applicable with a reason, it is accounted for.
    s = declared("wall_time", "peak_memory")
    record(
        verdict=verdict, spec=s, guardrails=declared_results(s), gates=gates
    )


def test_peak_memory_as_the_objective_is_not_a_guardrail() -> None:
    # Only a reported metric can measure peak memory, so only then does
    # naming it the objective stand in for the default guardrail.
    s = declared("peak_memory")
    assert s.metric.kind == "reported"
    rec = record(spec=s, guardrails=())
    assert rec.verdict.outcome == "KEEP"


OBJECTIVE_KINDS: dict[str, set[str]] = {
    "wall_time": {"wall", "reported"},
    "cpu_time": {"cpu", "reported"},
    "peak_memory": {"reported"},
    "peak_gpu_memory": {"reported"},
    "p99_latency": {"reported"},
    "throughput": {"reported"},
    "instructions": {"reported", "counter"},
    "tokens_per_request": {"reported", "counter"},
}
METRIC_OF_KIND: dict[str, dict[str, Any]] = {
    "wall": {"kind": "wall", "direction": "lower_is_better", "unit": "s"},
    "cpu": {"kind": "cpu", "direction": "lower_is_better", "unit": "s"},
    "reported": {
        "kind": "reported",
        "direction": "lower_is_better",
        "unit": "x",
    },
    "counter": {
        "kind": "counter",
        "direction": "lower_is_better",
        "unit": "count",
        "event": "instructions",
    },
}


@pytest.mark.parametrize("kind", list(METRIC_OF_KIND))
@pytest.mark.parametrize("objective", list(OBJECTIVE_KINDS))
def test_objective_must_be_measurable_by_the_metric_kind(
    objective: str, kind: str
) -> None:
    metric_ = dict(METRIC_OF_KIND[kind])
    if kind == "reported":
        # Only a reported metric chooses its direction; give it the
        # objective's own, so this test isolates the kind.
        metric_["direction"] = OBJECTIVE_DIRECTION.get(
            objective, "lower_is_better"
        )
    data: dict[str, Any] = {
        **as_json(spec()),
        "metric": metric_,
        "objective": objective,
        "guardrails": [],
    }
    if kind in OBJECTIVE_KINDS[objective]:
        assert BenchmarkSpec.model_validate(data).objective == objective
    else:
        with pytest.raises(ValidationError, match="cannot measure"):
            BenchmarkSpec.model_validate(data)


OBJECTIVE_DIRECTION: dict[str, str] = {
    "wall_time": "lower_is_better",
    "cpu_time": "lower_is_better",
    "peak_memory": "lower_is_better",
    "peak_gpu_memory": "lower_is_better",
    "p99_latency": "lower_is_better",
    "throughput": "higher_is_better",
}
FLIPPED = {
    "lower_is_better": "higher_is_better",
    "higher_is_better": "lower_is_better",
}


@pytest.mark.parametrize("objective", list(OBJECTIVE_DIRECTION))
def test_objective_needs_its_direction(objective: str) -> None:
    # A reported metric, the one kind that chooses its direction and can
    # measure every known objective.
    right = OBJECTIVE_DIRECTION[objective]
    data: dict[str, Any] = {
        **as_json(spec()),
        "metric": {"kind": "reported", "direction": right, "unit": "x"},
        "objective": objective,
        "guardrails": [],
    }
    assert BenchmarkSpec.model_validate(data).metric.direction == right
    data["metric"]["direction"] = FLIPPED[right]
    with pytest.raises(ValidationError, match=f"is {right}, not"):
        BenchmarkSpec.model_validate(data)


@pytest.mark.parametrize("name", list(OBJECTIVE_DIRECTION))
def test_guardrail_needs_its_direction(name: str) -> None:
    right = OBJECTIVE_DIRECTION[name]
    data: dict[str, Any] = {
        "metric": name,
        "direction": right,
        "unit": "x",
        "allowance": 0.1,
        "allowance_source": "project",
    }
    assert GuardrailSpec.model_validate(data).direction == right
    data["direction"] = FLIPPED[right]
    with pytest.raises(ValidationError, match=f"is {right}, not"):
        GuardrailSpec.model_validate(data)
    data["applicable"] = False
    data["reason"] = "not used"
    del data["allowance"], data["allowance_source"]
    with pytest.raises(ValidationError, match=f"is {right}, not"):
        GuardrailSpec.model_validate(data)


@pytest.mark.parametrize("direction", ["lower_is_better", "higher_is_better"])
def test_project_names_choose_their_direction(direction: str) -> None:
    GuardrailSpec.model_validate(
        {
            "metric": "tokens_per_request",
            "direction": direction,
            "unit": "tokens",
            "allowance": 0.1,
            "allowance_source": "project",
        }
    )
    data: dict[str, Any] = {
        **as_json(spec()),
        "metric": {"kind": "reported", "direction": direction, "unit": "x"},
        "objective": "tokens_per_request",
        "guardrails": [],
    }
    BenchmarkSpec.model_validate(data)


def test_reported_p99_cannot_reward_rising_latency() -> None:
    # The reviewer's case: a higher_is_better reported metric naming
    # p99_latency would drop the p99 default and reward rising latency.
    data: dict[str, Any] = {
        **as_json(spec()),
        "metric": {
            "kind": "reported",
            "direction": "higher_is_better",
            "unit": "s",
        },
        "objective": "p99_latency",
        "guardrails": [as_json(memory_not_applicable())],
    }
    with pytest.raises(ValidationError, match="lower_is_better, not"):
        BenchmarkSpec.model_validate(data)


@pytest.mark.parametrize(
    ("objective", "target"),
    [
        ("peak_memory", "python_cpu"),
        ("p99_latency", "web_service"),
        ("peak_gpu_memory", "gpu_kernel"),
    ],
)
def test_wall_metric_cannot_name_a_default_guardrail_away(
    objective: str, target: str
) -> None:
    # The audit's case: a wall-clock KEEP labelled with an objective it
    # can't measure would have exempted that default guardrail.
    data: dict[str, Any] = {
        **as_json(spec()),
        "objective": objective,
        "guardrails": [],
    }
    with pytest.raises(ValidationError, match="cannot measure"):
        BenchmarkSpec.model_validate(data)
    v = Verdict.model_validate({**as_json(keep()), "target_class": target})
    with pytest.raises(ValidationError, match="default guardrails"):
        record(verdict=v, spec=declared("wall_time"), guardrails=())


def test_gate_verdict_needs_no_guardrails_declared() -> None:
    record(
        verdict=integrity(),
        spec=declared(None),
        gates=(lock_failed(),),
        samples_path=None,
    )


@pytest.mark.parametrize(
    ("target", "objective", "required"),
    [
        # An unknown class fails closed: every default of the table.
        (
            None,
            "wall_time",
            {"peak_memory", "peak_gpu_memory", "p99_latency"},
        ),
        (None, "p99_latency", {"peak_memory", "peak_gpu_memory"}),
        ("python_cpu", "wall_time", {"peak_memory"}),
        ("native", "wall_time", {"peak_memory"}),
        ("web_service", "throughput", {"peak_memory", "p99_latency"}),
        ("web_service", "p99_latency", {"peak_memory"}),
        ("gpu_kernel", "wall_time", {"peak_memory", "peak_gpu_memory"}),
        (
            "llm_serving",
            "throughput",
            {"peak_memory", "peak_gpu_memory", "p99_latency"},
        ),
        ("llm_serving", "p99_latency", {"peak_memory", "peak_gpu_memory"}),
        # A batch agentic app still declares p99, not applicable.
        ("agentic", "wall_time", {"peak_memory", "p99_latency"}),
        ("agentic", "p99_latency", {"peak_memory"}),
    ],
)
def test_target_class_default_guardrails_are_required(
    target: TargetClass | None, objective: str, required: set[str]
) -> None:
    assert required == set(DEFAULT_GUARDRAILS[target]) - {objective}
    v = Verdict.model_validate({**as_json(keep()), "target_class": target})
    s = declared(objective, *sorted(required))
    record(verdict=v, spec=s, guardrails=declared_results(s))
    for missing in sorted(required):
        rest = declared(objective, *sorted(required - {missing}))
        with pytest.raises(ValidationError, match=f"'{missing}'"):
            record(verdict=v, spec=rest, guardrails=declared_results(rest))


def test_default_guardrails_cover_every_target_class_and_unknown() -> None:
    assert set(DEFAULT_GUARDRAILS) == {*get_args(TargetClass), None}
    for metrics in DEFAULT_GUARDRAILS.values():
        assert "peak_memory" in metrics
    assert set(DEFAULT_GUARDRAILS[None]) == {
        m for metrics in DEFAULT_GUARDRAILS.values() for m in metrics
    }


def test_not_established_needs_an_interval_some_check_reads() -> None:
    # Allowance and no limits: a candidate interval alone feeds no
    # check, so nothing is established; the guardrail is unmeasurable.
    allowance_only = GuardrailSpec(
        metric="peak_memory",
        direction="lower_is_better",
        unit="bytes",
        allowance=ALLOWANCE,
        allowance_source="default",
    )
    s = safety_spec(
        guardrails=[as_json(allowance_only), as_json(latency_not_applicable())]
    )
    unread = memory_result("not_established", None, (5e8, 6e8))
    with pytest.raises(ValidationError, match="its checks"):
        safety_record(
            spec=s,
            verdict=keep_blocked(),
            guardrails=(unread, latency_result()),
        )
    read = memory_result("not_established", (1.05, 1.15), None)
    safety_record(
        spec=s, verdict=keep_blocked(), guardrails=(read, latency_result())
    )
    none = GuardrailResult(
        metric="peak_memory", status="unmeasurable", reason="no ratio"
    )
    safety_record(
        spec=s, verdict=keep_blocked(), guardrails=(none, latency_result())
    )


# Required diagnostics and evidence for not_established


def test_procedure_uncalibrated_has_no_default() -> None:
    with pytest.raises(ValidationError, match="procedure_uncalibrated"):
        Diagnostics()  # pyright: ignore[reportCallIssue]
    data = as_json(keep())
    del data["diagnostics"]["procedure_uncalibrated"]
    with pytest.raises(ValidationError, match="procedure_uncalibrated"):
        Verdict.model_validate(data)
    del data["diagnostics"]
    with pytest.raises(ValidationError, match="diagnostics"):
        Verdict.model_validate(data)
    text = (
        record()
        .model_dump_json()
        .replace('"procedure_uncalibrated":false,', "")
    )
    assert "procedure_uncalibrated" not in text
    with pytest.raises(ValidationError, match="procedure_uncalibrated"):
        RunRecord.model_validate_json(text)


def test_not_established_needs_an_interval() -> None:
    with pytest.raises(ValidationError, match="needs intervals"):
        GuardrailResult(metric="peak_memory", status="not_established")
    # With no interval at all, the guardrail is unmeasurable, with why.
    none = GuardrailResult(
        metric="peak_memory",
        status="unmeasurable",
        reason="fewer than 2 rounds ran",
    )
    v = Verdict.model_validate(
        {
            **as_json(inconclusive()),
            "estimate": None,
            "interval": None,
            "rounds": 1,
            "looks": 0,
            "diagnostics": {"procedure_uncalibrated": False},
        }
    )
    safety_record(verdict=v, guardrails=(none, latency_result()))


# Method follow-ups (schema version 3)


@pytest.mark.parametrize(("n0", "k"), [(11, 10), (10, 5), (11, 5)])
def test_one_host_look_schedule_needs_even_n0_and_k(n0: int, k: int) -> None:
    data = with_value(
        as_json(spec()), "looks", {"n0": n0, "k": k, "max_looks": 3}
    )
    with pytest.raises(ValidationError, match="order pairs"):
        BenchmarkSpec.model_validate(data)
    # Cross host, the observations are session pairs, so odd is fine.
    data["allow_cross_host"] = True
    assert BenchmarkSpec.model_validate(data).looks.n0 == n0


def test_look_schedule_documents_its_per_look_confidence() -> None:
    defs = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))["$defs"]
    max_looks = defs["LookSchedule"]["properties"]["max_looks"]
    assert "``1 - alpha / (2 K)``" in max_looks["description"]
    assert "alpha / max_looks" not in max_looks["description"]
    worsening = defs["GuardrailResult"]["properties"]["worsening"]
    assert "``1 - alpha / (2 K G)``" in worsening["description"]


def test_reported_resolution_is_declared_in_the_spec() -> None:
    reported = as_json(EXAMPLES["metric_reported_resolution"])
    data = {**as_json(spec()), "metric": reported, "objective": None}
    data["guardrails"] = []
    s = BenchmarkSpec.model_validate(data)
    assert s.metric.resolution == 0.001
    other = {**data, "metric": {**reported, "resolution": 0.01}}
    assert BenchmarkSpec.model_validate(other).spec_hash != s.spec_hash
    with pytest.raises(ValidationError, match="measured, not declared"):
        MetricSpec(
            kind="wall", direction="lower_is_better", unit="s", resolution=1e-9
        )


@pytest.mark.parametrize("source", ["clock", "count", "declared", "printed"])
def test_sample_records_its_resolution_and_source(source: str) -> None:
    data = {
        **as_json(sample()),
        "resolution": 1e-9,
        "resolution_source": source,
    }
    s = Sample.model_validate(data)
    assert (s.resolution, s.resolution_source) == (1e-9, source)
    with pytest.raises(ValidationError, match="set together"):
        Sample.model_validate({**data, "resolution_source": None})


def test_resolution_stop_is_a_rule_1_inconclusive_at_a_look() -> None:
    v = resolution_stop()
    assert v.diagnostics.coarse_resolution is True
    assert v.diagnostics.resolution_widening == 0.2
    r = record(verdict=v)
    assert r.guardrails == (memory_not_applicable_result(),)
    with pytest.raises(ValidationError, match="names no gate"):
        Verdict.model_validate({**as_json(v), "gate": "lock"})
    with pytest.raises(ValidationError, match="coarse_resolution diagnostic"):
        Verdict.model_validate(
            {**as_json(v), "diagnostics": {"procedure_uncalibrated": False}}
        )
    with pytest.raises(ValidationError, match="decided at a look"):
        Verdict.model_validate({**as_json(v), "looks": 0})
    with pytest.raises(ValidationError, match="ends the comparison under"):
        Verdict.model_validate(
            {
                **as_json(keep()),
                "diagnostics": {
                    "procedure_uncalibrated": False,
                    "coarse_resolution": True,
                },
            }
        )


def test_resolution_stop_applies_before_calibration_too() -> None:
    data = as_json(resolution_stop())
    data["diagnostics"]["procedure_uncalibrated"] = True
    v = Verdict.model_validate(data)
    assert record(verdict=v).verdict.rule == 1


@pytest.mark.parametrize(
    ("path", "value", "name"),
    [
        ("estimate", 1.08, "estimate"),
        (
            "interval",
            {"lower": 1.05, "upper": 1.1, "confidence": 0.99},
            "interval",
        ),
        (
            "diagnostics.wall_interval",
            {"lower": 1.05, "upper": 1.1, "confidence": 0.99},
            "wall_interval",
        ),
    ],
)
def test_resolution_stop_carries_no_speedup_claim(
    path: str, value: Any, name: str
) -> None:
    # A coarse clock can read a small change as a whole step, so the
    # stop must not keep the estimate or interval it would mislead with.
    data = with_value(as_json(resolution_stop()), path, value)
    with pytest.raises(ValidationError, match=f"speedup claim, so no {name}"):
        Verdict.model_validate(data)


def test_gate_reject_may_follow_a_resolution_stop() -> None:
    # Gates are re-checked on the finished samples, and a gate failure
    # decides first even after the resolution stop ended the rounds.
    data = {
        **as_json(integrity()),
        "rounds": 10,
        "looks": 1,
        "diagnostics": {
            "procedure_uncalibrated": False,
            "coarse_resolution": True,
        },
    }
    assert Verdict.model_validate(data).gate == "lock"


def test_cross_host_resolution_stop_needs_n0_session_pairs() -> None:
    data = {
        **as_json(resolution_stop()),
        "cross_host": True,
        "session_pairs": 9,
    }
    with pytest.raises(ValidationError, match="has taken no look"):
        cross_record(Verdict.model_validate(data))
    data["session_pairs"] = 10
    assert cross_record(Verdict.model_validate(data)).verdict.rule == 1


def test_calibrated_verdict_names_its_conditions() -> None:
    v = calibrated_keep()
    assert v.calibration_conditions == CONDITIONS
    assert record(verdict=v).verdict.calibration == "calibrated"
    with pytest.raises(ValidationError, match="conditions"):
        Verdict.model_validate({**as_json(v), "calibration_conditions": []})
    with pytest.raises(ValidationError, match="conditions"):
        Verdict.model_validate(
            {**as_json(keep()), "calibration_conditions": list(CONDITIONS)}
        )


def test_native_build_is_its_own_runtime_kind() -> None:
    env = native_environment()
    assert env.runtime_kind == "native_build"
    assert env.runtime_version is None
    assert dict(env.missing) == {}
    assert [lib.name for lib in env.linked_libraries or ()] == [
        "/usr/lib/libSystem.B.dylib",
        "libfoo.so.1",
        "libopt.dylib",
    ]
    marker = "a native executable built in the tree; it has no interpreter"
    with pytest.raises(ValidationError, match="missing"):
        TreeEnvironment.model_validate(
            {**as_json(env), "missing": {"runtime_version": marker}}
        )
    with pytest.raises(ValidationError, match="does not apply"):
        TreeEnvironment.model_validate(
            {**as_json(env), "runtime_version": "3.12.12"}
        )


def test_unknown_runtime_has_a_reason_and_no_kind() -> None:
    env = EXAMPLES["environment_unknown"]
    assert isinstance(env, TreeEnvironment)
    assert env.runtime_kind is None
    assert env.missing["runtime_kind"] == "interpreter not found on PATH"
    with pytest.raises(ValidationError, match="version is an interpreter"):
        TreeEnvironment.model_validate(
            {
                **as_json(env),
                "runtime_version": "3.12.12",
                "missing": {
                    k: v
                    for k, v in env.missing.items()
                    if k != "runtime_version"
                },
            }
        )
    with pytest.raises(ValidationError, match="native build only"):
        TreeEnvironment.model_validate(
            {
                **as_json(env),
                "linked_libraries": [],
                "missing": {
                    k: v
                    for k, v in env.missing.items()
                    if k != "linked_libraries"
                },
            }
        )


def test_interpreter_has_an_executable_and_version() -> None:
    data = as_json(environment())
    with pytest.raises(ValidationError, match="identified with its version"):
        TreeEnvironment.model_validate(
            {
                **data,
                "runtime_version": None,
                "missing": {"runtime_version": "x"},
            }
        )
    with pytest.raises(ValidationError, match="has an executable"):
        TreeEnvironment.model_validate(
            {
                **data,
                "runtime_executable": None,
                "missing": {"runtime_executable": "x"},
            }
        )
    with pytest.raises(ValidationError, match="does not apply"):
        TreeEnvironment.model_validate({**data, "linked_libraries": []})


@pytest.mark.parametrize(
    "factory", [library_file, library_cache, library_weak]
)
def test_linked_library_fields_follow_its_source(factory: Any) -> None:
    lib = factory()
    for field, value in (("path", "/x/lib.so"), ("sha256", HASH_B)):
        if getattr(lib, field) is None:
            with pytest.raises(ValidationError, match=f"takes no {field}"):
                LinkedLibrary.model_validate({**as_json(lib), field: value})


def test_record_names_its_repository_and_candidate() -> None:
    r = EXAMPLES["record_named"]
    assert isinstance(r, RunRecord)
    assert (r.repository, r.candidate_commit, r.candidate_dirty) == (
        REPOSITORY,
        "f" * 40,
        False,
    )
    # Not recorded stays not recorded; nothing is filled in.
    plain = record()
    assert (plain.repository, plain.candidate_commit) == (None, None)
    assert plain.candidate_dirty is None
    with pytest.raises(ValidationError, match="set together"):
        record(candidate_commit="f" * 40)


@pytest.mark.parametrize(
    "repository",
    [
        # Every value here is a made-up placeholder, not a credential.
        "https://PLACEHOLDER-TOKEN@github.com/example/sum-loop.git",
        "git+https://Placeholder0Token@github.com/example/sum-loop.git",
        "http://user:placeholder@git.example.test/sum-loop.git",
        "ssh://git:placeholder@github.com/example/sum-loop.git",
        "ssh://PLACEHOLDER@github.com/example/sum-loop.git",
        "user:placeholder@github.com:example/sum-loop.git",
        "a" * 33 + "@github.com:example/sum-loop.git",
        # On web schemes any user part is refused, however plain.
        "git+https://ghp_x@h/example/sum-loop.git",
        "https://user@h/example/sum-loop.git",
        "HTTP://git@h/example/sum-loop.git",
        "git+http://git@h/example/sum-loop.git",
    ],
)
def test_repository_user_that_may_be_a_credential_is_refused(
    repository: str,
) -> None:
    with pytest.raises(ValidationError, match="may be a credential"):
        record(repository=repository)


@pytest.mark.parametrize(
    "repository",
    [
        REPOSITORY,
        "ssh://git@github.com/example/sum-loop.git",
        "git+ssh://git@github.com/example/sum-loop.git",
        "git@github.com:example/sum-loop.git",
        "/Users/someone/code/sum-loop",
        "/Users/some@one/code/sum-loop",
        # The authority ends at the first "/", so this has no user part.
        "https://u:p/ss@h",
        # The stated SSH limit: a short lowercase token looks like a
        # login name, so it is kept.
        "ghp_x@h:o/r",
    ],
)
def test_repository_with_a_plain_login_or_no_user_is_kept(
    repository: str,
) -> None:
    assert record(repository=repository).repository == repository


def test_differential_capture_limit_marks_a_capped_count() -> None:
    capped = DifferentialCheck.model_validate(
        {**as_json(differential()), "capture_limit": 50}
    )
    assert capped.captured == capped.capture_limit
    assert differential().capture_limit is None
    with pytest.raises(ValidationError, match="past the capture limit"):
        DifferentialCheck.model_validate(
            {**as_json(differential()), "capture_limit": 49}
        )


def test_differential_tolerance_is_declared_in_the_spec() -> None:
    assert (
        correctness().differential_rtol,
        correctness().differential_atol,
    ) == (
        0.0,
        0.0,
    )
    data = as_json(spec())
    data["correctness"]["differential_rtol"] = 1e-12
    tolerant = BenchmarkSpec.model_validate(data)
    assert tolerant.correctness.differential_rtol == 1e-12
    assert tolerant.spec_hash != spec().spec_hash
    data["correctness"]["differential_atol"] = 1e-15
    assert BenchmarkSpec.model_validate(data).spec_hash != tolerant.spec_hash


# Spec hash


def reversed_keys(value: Any) -> Any:
    if isinstance(value, dict):
        items = list(cast("dict[str, Any]", value).items())
        return {k: reversed_keys(v) for k, v in reversed(items)}
    if isinstance(value, list):
        return [reversed_keys(v) for v in cast("list[Any]", value)]
    return value


def test_spec_hash_is_sha256_hex() -> None:
    h = spec().spec_hash
    assert len(h) == 64
    assert int(h, 16) >= 0
    assert spec().model_dump(mode="json")["spec_hash"] == h


def test_spec_hash_is_pinned() -> None:
    # Pinned so a change to the canonical form, which would orphan every
    # stored spec, fails here instead of silently.
    assert spec().spec_hash == PINNED_SPEC_HASH


def test_same_name_is_not_same_spec() -> None:
    other = spec().model_copy(update={"warmup": 3})
    other = BenchmarkSpec.model_validate(as_json(other))
    assert other.name == spec().name
    assert other != spec()
    assert other.spec_hash != spec().spec_hash


def test_spec_hash_ignores_key_order() -> None:
    data = as_json(spec())
    flipped = reversed_keys(data)
    assert list(flipped) != list(data)
    assert list(flipped["env"]) != list(data["env"])
    a = BenchmarkSpec.model_validate(data)
    b = BenchmarkSpec.model_validate_json(json.dumps(flipped))
    assert a.spec_hash == b.spec_hash == spec().spec_hash


CHANGED: dict[str, Any] = {
    "schema_version": None,
    "name": "other",
    "command": ["python", "bench.py", "--n", "1001"],
    "cwd": "sub",
    "env": {"PYTHONHASHSEED": "1", "OMP_NUM_THREADS": "1"},
    "metric": {
        "kind": "reported",
        "direction": "lower_is_better",
        "unit": "s",
    },
    "correctness": {
        "command": ["python", "-m", "pytest"],
        "timeout_seconds": 120.0,
    },
    "warmup": 3,
    "budget": {"max_rounds": 200, "max_seconds": 601.0},
    "looks": {"n0": 10, "k": 10, "max_looks": 6},
    "min_effect": 0.05,
    "alpha": 0.01,
    "timeout_seconds": 31.0,
    "harness": ["bench.py", "harness.py"],
    "oracle": ["tests/test_other.py"],
    "inputs": ["data/other.bin"],
    "config": [],
    "allow_runtime_change": True,
    "allow_cross_host": True,
    "allow_remote_services": True,
    "allow_unchecked_coverage": True,
    "reset": {"command": ["make", "reset"], "timeout_seconds": 10.0},
    # A wall metric measures only wall_time, and guardrails need an
    # objective, so test_spec_hash_changes_with_guardrails covers both.
    "objective": None,
    "guardrails": None,
}


def test_spec_hash_changes_with_guardrails() -> None:
    base = safety_spec()
    reported = {**METRIC_OF_KIND["reported"], "direction": "higher_is_better"}
    assert (
        safety_spec(metric=reported, objective="throughput").spec_hash
        != safety_spec(metric=reported, objective="tokens_per_s").spec_hash
    )
    for update in (
        {"guardrails": [as_json(memory_guardrail())]},
        {"guardrails": [as_json(g) for g in base.guardrails[::-1]]},
        {
            "guardrails": [
                {**as_json(memory_guardrail()), "allowance": 0.05},
                as_json(latency_not_applicable()),
            ]
        },
        {"reset": {**as_json(reset()), "timeout_seconds": 31.0}},
    ):
        assert safety_spec(**update).spec_hash != base.spec_hash


def test_spec_hash_change_table_covers_every_field() -> None:
    assert set(CHANGED) == set(BenchmarkSpec.model_fields)


@pytest.mark.parametrize(
    "field", [f for f, v in CHANGED.items() if v is not None]
)
def test_spec_hash_changes_with_any_field(field: str) -> None:
    data = as_json(spec())
    data[field] = CHANGED[field]
    assert BenchmarkSpec.model_validate(data).spec_hash != spec().spec_hash


def test_spec_hash_changes_with_nested_field() -> None:
    data = as_json(spec())
    data["correctness"]["outputs"][0]["rtol"] = 1e-8
    assert BenchmarkSpec.model_validate(data).spec_hash != spec().spec_hash


def test_stored_null_spec_hash_is_checked() -> None:
    data = {**as_json(spec()), "spec_hash": None}
    with pytest.raises(ValidationError, match="spec_hash"):
        BenchmarkSpec.model_validate(data)


def test_stored_spec_hash_is_checked() -> None:
    data = spec().model_dump(mode="json")
    assert BenchmarkSpec.model_validate(data) == spec()
    data["warmup"] = 5
    with pytest.raises(ValidationError, match="spec_hash"):
        BenchmarkSpec.model_validate(data)
    with pytest.raises(ValidationError, match="spec_hash"):
        BenchmarkSpec.model_validate_json(json.dumps(data))


# Checked-in JSON Schema


def test_json_schema_is_checked_in_and_current() -> None:
    assert (
        SCHEMA_FILE.read_text(encoding="utf-8") == schema.json_schema_text()
    ), (
        "src/perfproof/schema.json is out of date; regenerate it with\n"
        "uv run python -m perfproof.schema > src/perfproof/schema.json"
    )


def test_json_schema_covers_every_model() -> None:
    defs = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))["$defs"]
    assert {m.__name__ for m in MODELS} <= set(defs)
    assert "spec_hash" in defs["BenchmarkSpec"]["properties"]
    assert defs["Verdict"]["additionalProperties"] is False


# Project model and hypotheses (T3.1)

NEW_REJECTED: list[tuple[str, str, Any]] = [
    ("file_hash", "path", "../x"),
    ("file_hash", "sha256", "ABC"),
    ("provenance", "perfproof_version", ""),
    ("provenance", "commands.0", []),
    ("provenance", "derived_at", "2026-10-15T12:00:00"),
    ("confirmation", "session", ""),
    ("confirmation", "confirmed_at", "2026-10-15T12:00:00"),
    ("invalidation", "trigger", "vibes"),
    ("invalidation", "detail", ""),
    ("target_entry", "target_class", "quantum"),
    ("target_entry", "evidence", []),
    ("target_entry", "id", "../x"),
    ("target_entry", "version", 0),
    ("target_entry", "status", "done"),
    ("target_entry", "kind", "workload"),
    ("workload_entry", "source", "guess"),
    ("workload_entry", "command", []),
    ("workload_entry", "cwd", "/abs"),
    ("workload_entry", "spec_hash", "short"),
    ("goal_bar", "kind", "ratio"),
    ("goal_bar", "value", 0.0),
    ("goal_bar", "value", math.nan),
    ("goal_entry", "metric", "Wall Time"),
    ("goal_entry", "direction", "faster"),
    ("goal_entry", "bar.value", 0.9),
    ("goal_entry", "guardrails.0.direction", "worse"),
    ("constraint_entry", "constraint", "vibes"),
    ("constraint_entry", "rtol", -1.0),
    ("constraint_entry", "atol", math.inf),
    ("service", "name", "bad name"),
    ("service", "timeout_seconds", 0.0),
    ("service", "health_command", []),
    ("how_to_run_entry", "services.0.command", []),
    ("how_to_run_entry", "setup.0", []),
    ("project_model", "updated_at", "2026-10-15T12:00:00"),
    ("project_model", "targets.0.evidence", []),
    ("project_history", "event", "deleted"),
    ("project_history", "entry.kind", "dream"),
    ("evidence_ref", "kind", "hunch"),
    ("evidence_ref", "path", "/abs/x"),
    ("evidence_ref", "sha256", "x"),
    ("code_anchor", "file", "a/../b.py"),
    ("code_anchor", "qualified_name", ""),
    ("code_anchor", "source_hash", "x"),
    ("entry_ref", "kind", "hypothesis"),
    ("entry_ref", "version", 0),
    ("anchors", "workload", "../x"),
    ("anchors", "workload_spec_hash", "x"),
    ("cap", "kind", "guess"),
    ("cap", "upper_bound", 0.5),
    ("cap", "upper_bound", math.inf),
    ("cap", "share", -0.1),
    ("cap", "derivation", ""),
    ("cap_model", "upper_bound", "3"),
    ("measured_speedup", "estimate", 0.0),
    ("measured_speedup", "interval.lower", math.nan),
    ("event_observation", "evidence.0.sha256", "x"),
    ("event_observation", "schema_version", 2),
    ("event_observation", "schema_version", True),
    ("event_observation", "recorded_at", "2026-10-15T12:00:00"),
    ("event_observation", "session", ""),
    ("event_mechanism", "claim", ""),
    ("event_prediction", "expect_if_true", ""),
    ("event_falsification", "result", "proved"),
    ("event_falsification", "test", "vibes"),
    ("event_implementation", "worktree", ""),
    ("event_outcome", "verdict", "MAYBE"),
    ("event_outcome", "reason", "speed"),
    ("event_skipped", "step", "observation"),
    ("event_skipped", "reason", ""),
    ("event_reordered", "reason", ""),
    ("event_stale", "anchor", "mood"),
    ("event_revalidated", "what_differs", ""),
    # unknown fields are refused, not dropped
    ("event_prediction", "speedup", 1.33),
    ("event_prediction", "cap.speedup", 1.33),
    ("event_outcome", "cap", as_json(Cap.model_validate(AMDAHL))),
    ("measured_speedup", "upper_bound", 2.0),
    ("goal_entry", "default_metric", "wall_time"),
    ("project_model", "extra", 1),
]


@pytest.mark.parametrize(("name", "path", "value"), NEW_REJECTED)
def test_new_models_reject_invalid_value(
    name: str, path: str, value: Any
) -> None:
    model = EXAMPLES[name]
    data = with_value(as_json(model), path, value)
    with pytest.raises(ValidationError):
        type(model).model_validate(data)


def test_new_documents_do_not_bump_schema_version() -> None:
    assert schema.SCHEMA_VERSION == 3
    assert as_json(project_model())["schema_version"] == 1
    assert as_json(EXAMPLES["project_history"])["schema_version"] == 1
    assert as_json(event("observation"))["schema_version"] == 1
    for cls, doc in (
        (ProjectModel, project_model()),
        (ProjectHistoryRecord, EXAMPLES["project_history"]),
    ):
        for version in (0, 2, 3, True, 1.0):
            data = with_value(as_json(doc), "schema_version", version)
            with pytest.raises(ValidationError):
                cls.model_validate(data)


def test_provenance_sorts_files_and_refuses_duplicates() -> None:
    assert [f.path for f in provenance().files] == [
        "bench/run.py",
        "src/app/io.py",
    ]
    twice = [{"path": "a.py", "sha256": HASH_A}] * 2
    with pytest.raises(ValidationError, match="more than once"):
        provenance(files=twice)


# Entry status


@pytest.mark.parametrize("kind", list(ENTRY_CLASSES))
def test_each_entry_kind_accepts_every_status(kind: str) -> None:
    entry(kind)
    entry(kind, **CONFIRMED)
    stale = {
        "status": "stale",
        "invalidation": as_json(invalidation()),
        "previous_status": "proposed",
    }
    entry(kind, **stale)
    entry(
        kind,
        **{
            **stale,
            "previous_status": "confirmed",
            "confirmation": as_json(confirmation()),
        },
    )
    entry(kind, status="retired", retired_reason="workload removed")


STATUS_REFUSALS: list[dict[str, Any]] = [
    {"status": "confirmed"},
    {"status": "proposed", "confirmation": as_json(confirmation())},
    {"status": "proposed", "invalidation": as_json(invalidation())},
    {"status": "proposed", "previous_status": "proposed"},
    {"status": "proposed", "retired_reason": "gone"},
    {"status": "stale"},
    {"status": "stale", "invalidation": as_json(invalidation())},
    {"status": "stale", "previous_status": "proposed"},
    {
        "status": "stale",
        "invalidation": as_json(invalidation()),
        "previous_status": "confirmed",
    },
    {
        "status": "stale",
        "invalidation": as_json(invalidation()),
        "previous_status": "proposed",
        "confirmation": as_json(confirmation()),
    },
    {
        "status": "stale",
        "invalidation": as_json(invalidation()),
        "previous_status": "stale",
    },
    {**CONFIRMED, "invalidation": as_json(invalidation())},
    {**CONFIRMED, "retired_reason": "gone"},
    {"status": "retired"},
    {"status": "retired", "retired_reason": ""},
    {
        "status": "retired",
        "retired_reason": "gone",
        "invalidation": as_json(invalidation()),
    },
]


@pytest.mark.parametrize("changes", STATUS_REFUSALS)
def test_entry_status_must_match_its_fields(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        entry("workload", **changes)


def test_entry_version_defaults_to_one_and_is_explicit_after() -> None:
    assert entry("target").version == 1
    assert entry("target", version=4).version == 4


# Goals: an unknown stays unknown


def test_unknown_goal_stays_unknown() -> None:
    goal = entry("goal", **UNKNOWN_GOAL)
    assert goal.metric is None
    assert goal.direction is None
    assert goal.unit is None
    assert goal.bar is None
    assert goal.guardrails is None
    assert set(goal.missing) == {
        "metric",
        "direction",
        "unit",
        "bar",
        "guardrails",
    }
    again = GoalEntry.model_validate_json(goal.model_dump_json())
    assert again == goal
    assert again.metric is None


@pytest.mark.parametrize(
    "field", ["metric", "direction", "unit", "bar", "guardrails"]
)
def test_goal_field_is_never_defaulted(field: str) -> None:
    data = entry_data("goal")
    del data[field]
    with pytest.raises(ValidationError, match=field):
        GoalEntry.model_validate(data)


@pytest.mark.parametrize(
    "field", ["metric", "direction", "unit", "bar", "guardrails"]
)
def test_goal_none_needs_a_reason_and_a_reason_needs_none(
    field: str,
) -> None:
    with pytest.raises(ValidationError, match="reasons"):
        entry("goal", **{field: None})
    with pytest.raises(ValidationError, match="reasons"):
        entry("goal", missing={field: "asked, no answer"})
    entry("goal", **{field: None, "missing": {field: "not asked yet"}})


def test_empty_guardrails_differ_from_unknown_guardrails() -> None:
    assert entry("goal", guardrails=[]).guardrails == ()
    assert entry("goal", guardrails=[]).missing == {}


def test_confirmed_goal_cannot_have_an_unknown_metric() -> None:
    unknown = {**UNKNOWN_GOAL, **CONFIRMED}
    with pytest.raises(ValidationError, match="asked"):
        entry("goal", **unknown)
    for field in ("metric", "direction", "unit"):
        with pytest.raises(ValidationError, match="asked"):
            entry(
                "goal",
                **CONFIRMED,
                **{field: None, "missing": {field: "unknown"}},
            )


def test_confirmed_goal_may_leave_its_bar_unknown() -> None:
    goal = entry(
        "goal", **CONFIRMED, bar=None, missing={"bar": "no number given"}
    )
    assert goal.bar is None


def test_goal_known_metric_has_one_direction() -> None:
    with pytest.raises(ValidationError, match="throughput"):
        entry("goal", metric="throughput", direction="lower_is_better")
    entry("goal", metric="throughput", direction="higher_is_better")
    entry("goal", metric="tokens_per_s", direction="lower_is_better")


def test_ratio_bar_must_beat_one() -> None:
    for value in (1.0, 0.5):
        with pytest.raises(ValidationError, match="above 1"):
            GoalBar(kind="ratio", value=value)
    assert GoalBar(kind="absolute", value=0.5).value == 0.5


# Constraints and how to run


@pytest.mark.parametrize(
    "changes",
    [
        {"rtol": None},
        {"atol": None},
        {"rtol": None, "atol": None},
        {"constraint": "public_api"},
        {"constraint": "protected_file", "subject": "a/../b"},
        {"constraint": "protected_file", "subject": "/abs"},
        {"constraint": "target_hardware", "rtol": None},
        {"constraint": "forbidden_dependency", "atol": 0.0},
        {"constraint": "forbidden_dependency", "rtol": None, "atol": 0.0},
    ],
)
def test_constraint_rules(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        entry("constraint", **changes)


def test_valid_constraints() -> None:
    assert entry("constraint", rtol=0.0, atol=0.0).atol == 0.0
    entry("constraint", constraint="public_api", rtol=None, atol=None)
    entry(
        "constraint",
        constraint="protected_file",
        rtol=None,
        atol=None,
        subject="src/a.py",
    )
    hardware = entry(
        "constraint",
        constraint="target_hardware",
        rtol=None,
        atol=None,
        subject="M4 Pro",
        host_id="host-1",
    )
    assert hardware.host_id == "host-1"


HOW_FIELDS = ["setup", "build", "services", "reset"]


def test_how_to_run_none_needed_differs_from_not_established() -> None:
    nothing = entry(
        "how_to_run",
        setup=[],
        build=[],
        services=[],
        reset="none_needed",
    )
    assert nothing.setup == ()
    assert nothing.reset == "none_needed"
    assert nothing.missing == {}
    unknown = entry(
        "how_to_run",
        setup=None,
        build=None,
        services=None,
        reset=None,
        missing=dict.fromkeys(HOW_FIELDS, "not looked at yet"),
    )
    assert unknown.setup is None
    assert unknown.reset is None
    again = HowToRunEntry.model_validate_json(unknown.model_dump_json())
    assert again == unknown


@pytest.mark.parametrize("field", HOW_FIELDS)
def test_how_to_run_fields_are_never_defaulted(field: str) -> None:
    data = entry_data("how_to_run")
    del data[field]
    with pytest.raises(ValidationError, match=field):
        HowToRunEntry.model_validate(data)


@pytest.mark.parametrize("field", HOW_FIELDS)
def test_how_to_run_none_and_reason_go_together(field: str) -> None:
    with pytest.raises(ValidationError, match="reasons"):
        entry("how_to_run", **{field: None})
    with pytest.raises(ValidationError, match="reasons"):
        entry("how_to_run", missing={field: "asked, no answer"})
    entry("how_to_run", **{field: None, "missing": {field: "not asked"}})


def test_how_to_run_reset_command_is_kept() -> None:
    reset = {"command": ["make", "reset"], "timeout_seconds": 5.0}
    assert entry("how_to_run", reset=reset).reset == ResetSpec(
        command=("make", "reset"), timeout_seconds=5.0
    )
    with pytest.raises(ValidationError):
        entry("how_to_run", reset="whenever")


def test_workload_cwd_is_given_not_defaulted() -> None:
    with pytest.raises(ValidationError, match="cwd"):
        WorkloadEntry.model_validate(without(entry_data("workload"), "cwd"))


@pytest.mark.parametrize(
    "metric", ["p50_latency", "startup_time", "cost_per_token"]
)
def test_goal_metrics_have_one_direction(metric: str) -> None:
    with pytest.raises(ValidationError, match=metric):
        entry("goal", metric=metric, direction="higher_is_better")
    entry("goal", metric=metric, direction="lower_is_better")


# The project model


def test_project_model_may_be_empty() -> None:
    model = ProjectModel(updated_at=T0)
    assert model.targets == ()
    assert ProjectModel.model_validate_json(model.model_dump_json()) == model


PROJECT_REFUSALS: list[dict[str, Any]] = [
    {"workloads": [entry_data("workload", target_id="missing")]},
    {"goals": [entry_data("goal", workload_id="missing")]},
    {"how_to_run": [entry_data("how_to_run", workload_id="missing")]},
    {"targets": [entry_data("target"), entry_data("target")]},
    {"goals": [entry_data("goal", id="workload-1")]},
]


@pytest.mark.parametrize("changes", PROJECT_REFUSALS)
def test_project_model_links_are_checked(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        project_model(**changes)


def test_project_wide_how_to_run_needs_no_workload() -> None:
    project_model(how_to_run=[entry_data("how_to_run", workload_id=None)])


def test_retired_entries_stay_in_the_model() -> None:
    retired = entry_data(
        "workload", id="workload-0", status="retired", retired_reason="gone"
    )
    model = project_model(workloads=[entry_data("workload"), retired])
    assert [w.status for w in model.workloads] == ["proposed", "retired"]


def test_history_record_picks_the_entry_class_by_kind() -> None:
    for kind in ENTRY_CLASSES:
        record_ = ProjectHistoryRecord.model_validate(
            {
                "recorded_at": T0,
                "event": "created",
                "entry": entry_data(kind),
            }
        )
        assert type(record_.entry) is ENTRY_CLASSES[kind]
    with pytest.raises(ValidationError):
        ProjectHistoryRecord.model_validate(
            {
                "recorded_at": T0,
                "event": "created",
                "entry": {
                    k: v for k, v in entry_data("goal").items() if k != "kind"
                },
            }
        )


HISTORY_STATUS = [
    ("confirmed", "confirmed"),
    ("marked_stale", "stale"),
    ("reproposed", "proposed"),
    ("retired", "retired"),
    ("revalidated", "proposed"),
    ("revalidated", "confirmed"),
]
STATUS_FIELDS: dict[str, dict[str, Any]] = {
    "proposed": {},
    "confirmed": CONFIRMED,
    "stale": {
        "status": "stale",
        "invalidation": as_json(invalidation()),
        "previous_status": "proposed",
    },
    "retired": {"status": "retired", "retired_reason": "gone"},
}


@pytest.mark.parametrize(("event_name", "status"), HISTORY_STATUS)
def test_history_event_matches_entry_status(
    event_name: str, status: str
) -> None:
    for other in STATUS_FIELDS:
        data = {
            "recorded_at": T0,
            "event": event_name,
            "entry": entry_data("workload", **STATUS_FIELDS[other]),
        }
        if other == status:
            ProjectHistoryRecord.model_validate(data)
        elif not (event_name == "revalidated" and other in HISTORY_BACK):
            with pytest.raises(ValidationError, match=event_name):
                ProjectHistoryRecord.model_validate(data)


HISTORY_BACK = ("proposed", "confirmed")


@pytest.mark.parametrize("event_name", ["created", "edited"])
def test_history_created_and_edited_take_any_status(event_name: str) -> None:
    for fields in STATUS_FIELDS.values():
        ProjectHistoryRecord.model_validate(
            {
                "recorded_at": T0,
                "event": event_name,
                "entry": entry_data("workload", **fields),
            }
        )


# Caps and bounds are not speedups


def test_amdahl_cap_must_match_its_share() -> None:
    assert Cap.model_validate(AMDAHL).upper_bound == pytest.approx(4 / 3)
    for update in (
        {"upper_bound": 2.0},
        {"share": None},
        {"share": 1.0, "upper_bound": 1e12},
        {"share": 0.0, "upper_bound": 1.5},
    ):
        with pytest.raises(ValidationError):
            Cap.model_validate({**AMDAHL, **update})
    assert (
        Cap.model_validate(
            {**AMDAHL, "share": 0.0, "upper_bound": 1.0}
        ).upper_bound
        == 1.0
    )


def test_model_cap_has_no_share() -> None:
    with pytest.raises(ValidationError, match="amdahl"):
        Cap(kind="model", share=0.5, upper_bound=2.0, derivation="x")


def test_a_cap_cannot_be_stored_as_a_speedup() -> None:
    cap = as_json(Cap.model_validate(AMDAHL))
    # A bound where a measured effect goes, and a measured effect where
    # a bound goes, are both refused by the field names and types.
    with pytest.raises(ValidationError):
        MeasuredSpeedup.model_validate(cap)
    with pytest.raises(ValidationError):
        MeasuredSpeedup.model_validate({**SPEEDUP, "upper_bound": 1.33})
    with pytest.raises(ValidationError):
        event("outcome", speedup=cap)
    with pytest.raises(ValidationError):
        event("outcome", speedup=AMDAHL["upper_bound"])
    with pytest.raises(ValidationError):
        event("outcome", cap=cap)
    with pytest.raises(ValidationError):
        event("prediction", cap=SPEEDUP)
    with pytest.raises(ValidationError):
        event("prediction", speedup=1.33)
    with pytest.raises(ValidationError):
        event("prediction", cap={**cap, "speedup": 1.33})
    assert "speedup" not in Cap.model_fields
    assert "upper_bound" not in MeasuredSpeedup.model_fields
    assert "speedup" not in PredictionEvent.model_fields


# Hypothesis events


@pytest.mark.parametrize("name", list(EVENTS))
def test_event_union_parses_to_its_own_class(name: str) -> None:
    parsed = HYPOTHESIS_EVENT.validate_json(event(name).model_dump_json())
    assert type(parsed) is EVENT_CLASSES[name]
    assert parsed == event(name)
    assert HYPOTHESIS_EVENT.validate_python(as_json(event(name))) == parsed


def test_event_union_refuses_unknown_events() -> None:
    for bad in ("vibes", None):
        with pytest.raises(ValidationError):
            HYPOTHESIS_EVENT.validate_python(event_data("stale", event=bad))
    data = without(event_data("stale"), "event")
    with pytest.raises(ValidationError):
        HYPOTHESIS_EVENT.validate_python(data)


NEEDS_EVIDENCE = [n for n in EVENTS if "evidence" in EVENTS[n]]


@pytest.mark.parametrize("name", NEEDS_EVIDENCE)
def test_event_without_evidence_hashes_is_refused(name: str) -> None:
    with pytest.raises(ValidationError, match="evidence hashes"):
        event(name, evidence=[])
    data = without(event_data(name), "evidence")
    with pytest.raises(ValidationError, match="evidence hashes"):
        EVENT_CLASSES[name].model_validate(data)
    with pytest.raises(ValidationError, match="evidence hashes"):
        HYPOTHESIS_EVENT.validate_json(
            json.dumps({**data, "recorded_at": T0.isoformat()})
        )


def test_every_event_that_makes_a_claim_needs_evidence() -> None:
    assert set(NEEDS_EVIDENCE) == set(EVENTS) - {"skipped", "reordered"}


@pytest.mark.parametrize("name", ["skipped", "reordered"])
def test_skip_and_reorder_carry_reasons_not_evidence(name: str) -> None:
    assert event(name).evidence == ()


def test_evidence_hash_must_be_a_sha256() -> None:
    with pytest.raises(ValidationError):
        event("observation", evidence=[evidence(sha="abc")])
    with pytest.raises(ValidationError):
        event("observation", evidence=[{"kind": "measurement"}])


def test_event_cannot_correct_itself() -> None:
    assert event("mechanism", corrects="e-old").corrects == "e-old"
    with pytest.raises(ValidationError, match="itself"):
        event("mechanism", corrects="e-mechanism")


def test_retry_and_what_differs_go_together() -> None:
    with pytest.raises(ValidationError, match="what differs"):
        event("observation", retry_of="h-0")
    with pytest.raises(ValidationError, match="what differs"):
        event("observation", what_differs="new parser")
    with pytest.raises(ValidationError):
        event("observation", retry_of="h-0", what_differs="")
    retry = event("observation", retry_of="h-0", what_differs="new parser")
    assert retry.retry_of == "h-0"


def test_reordered_cannot_point_at_itself() -> None:
    with pytest.raises(ValidationError, match="itself"):
        event("reordered", before="h-1")
    event("reordered", before="h-2")


def test_implementation_evidence_must_be_the_diff() -> None:
    with pytest.raises(ValidationError, match="diff_hash"):
        event("implementation", diff_hash=HASH_A)
    # Right hash, wrong kind of evidence.
    with pytest.raises(ValidationError, match="diff_hash"):
        event(
            "implementation",
            evidence=[evidence("profile", HASH_C)],
        )
    refuted = event("implementation", override_reason="cheap test was noisy")
    assert refuted.override_reason == "cheap test was noisy"


def test_outcome_evidence_must_be_the_run_record() -> None:
    with pytest.raises(ValidationError, match="run_record_hash"):
        event("outcome", run_record_hash=HASH_A)
    with pytest.raises(ValidationError, match="run_record_hash"):
        event("outcome", evidence=[evidence("profile", HASH_D)])


def lost(lower: float, upper: float) -> dict[str, Any]:
    return {
        "estimate": 1.0,
        "interval": {"lower": lower, "upper": upper, "confidence": 0.99},
    }


GATE_OUTCOMES = [
    ("correctness", "correctness"),
    ("coverage", "correctness"),
    ("lock", "integrity"),
    ("procedure", "integrity"),
    ("runtime", "integrity"),
    ("same_host", "integrity"),
    ("side_effects", "integrity"),
]


@pytest.mark.parametrize(("gate", "reason"), GATE_OUTCOMES)
def test_gate_outcome_carries_no_speedup(gate: str, reason: str) -> None:
    gate_outcome = {
        "verdict": "REJECT",
        "reason": reason,
        "rule": 1,
        "gate": gate,
    }
    assert event("outcome", **gate_outcome, speedup=None).gate == gate
    # Faster but wrong: the speed is never stored.
    with pytest.raises(ValidationError, match="no speedup"):
        event("outcome", **gate_outcome, speedup={**SPEEDUP, "estimate": 3.0})


def test_gate_outcome_needs_its_gate_and_reason() -> None:
    base = {"verdict": "REJECT", "rule": 1, "speedup": None}
    with pytest.raises(ValidationError, match="gate"):
        event("outcome", **base, reason="correctness")
    with pytest.raises(ValidationError, match="gives reason"):
        event("outcome", **base, reason="correctness", gate="lock")
    with pytest.raises(ValidationError, match="gate"):
        event("outcome", gate="correctness")


def test_resolution_stop_carries_no_speedup_and_no_gate() -> None:
    stop = {
        "verdict": "INCONCLUSIVE",
        "reason": "insufficient_evidence",
        "rule": 1,
    }
    assert event("outcome", **stop, speedup=None).speedup is None
    with pytest.raises(ValidationError, match="no speedup"):
        event("outcome", **stop, speedup=SPEEDUP)
    with pytest.raises(ValidationError, match="gate"):
        event("outcome", **stop, speedup=None, gate="runtime")


def test_rule_five_inconclusive_may_carry_an_estimate() -> None:
    five = {
        "verdict": "INCONCLUSIVE",
        "reason": "insufficient_evidence",
        "rule": 5,
    }
    assert event("outcome", **five, speedup=lost(1.0, 1.1)).speedup
    # A budget spent before the first look leaves no interval to record.
    assert event("outcome", **five, speedup=None).speedup is None


def test_rule_two_interval_must_clear_the_bar() -> None:
    assert event("outcome", bar=1.0499).speedup is not None
    for bar in (1.05, 1.06):
        with pytest.raises(ValidationError, match="above the bar"):
            event("outcome", bar=bar)
    with pytest.raises(ValidationError, match="needs an estimate"):
        event("outcome", speedup=None)
    # A guardrail REJECT and a compliance INCONCLUSIVE keep their speed.
    event("outcome", verdict="REJECT", reason="guardrail")
    event(
        "outcome",
        verdict="INCONCLUSIVE",
        reason="insufficient_evidence",
    )
    with pytest.raises(ValidationError, match="needs an estimate"):
        event("outcome", verdict="REJECT", reason="guardrail", speedup=None)


def test_rule_three_interval_must_sit_at_or_below_the_bar() -> None:
    reject = {"verdict": "REJECT", "reason": "performance", "rule": 3}
    event("outcome", **reject, speedup=lost(0.9, 1.03))
    event("outcome", **reject, speedup=lost(0.8, 0.95))
    with pytest.raises(ValidationError, match="at or below"):
        event("outcome", **reject, speedup=lost(0.9, 1.0301))
    with pytest.raises(ValidationError, match="needs an estimate"):
        event("outcome", **reject, speedup=None)


def test_outcome_rule_verdict_and_reason_must_agree() -> None:
    for rule, verdict, reason in (
        (2, "KEEP", "correctness"),
        (2, "REJECT", "performance"),
        (3, "KEEP", "performance"),
        (5, "REJECT", "guardrail"),
        (1, "KEEP", "performance"),
        (2, "INCONCLUSIVE", "performance"),
    ):
        with pytest.raises(ValidationError, match="gives one of"):
            event("outcome", rule=rule, verdict=verdict, reason=reason)
    with pytest.raises(ValidationError):
        event("outcome", rule=4)


def test_outcome_records_calibration_and_a_real_bar() -> None:
    assert event("outcome", calibration="calibrated").calibration == (
        "calibrated"
    )
    with pytest.raises(ValidationError):
        event("outcome", calibration="mostly")
    with pytest.raises(ValidationError):
        event("outcome", bar=0.9)


def test_events_are_frozen_and_hashable() -> None:
    e = event("observation")
    with pytest.raises(ValidationError, match="frozen"):
        e.session = "s-2"  # type: ignore[misc]
    assert hash(event("stale")) == hash(event("stale"))


def test_new_models_are_in_the_json_schema() -> None:
    defs = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))["$defs"]
    for cls in (
        ProjectModel,
        ProjectHistoryRecord,
        GoalEntry,
        Cap,
        MeasuredSpeedup,
        *EVENT_CLASSES.values(),
    ):
        assert defs[cls.__name__]["additionalProperties"] is False
    assert "upper_bound" in defs["Cap"]["properties"]
    assert "estimate" not in defs["Cap"]["properties"]
    assert "upper_bound" not in defs["MeasuredSpeedup"]["properties"]
