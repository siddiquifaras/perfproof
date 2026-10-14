import copy
import json
import math
import pickle
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel, ValidationError

from perfproof import schema
from perfproof.schema import (
    MODELS,
    Arm,
    BenchmarkSpec,
    Budget,
    CorrectnessSpec,
    DependencyLock,
    Diagnostics,
    Difference,
    GateResult,
    GpuInfo,
    Hotspot,
    Interval,
    LockedFile,
    LockManifest,
    LookSchedule,
    MachineFingerprint,
    MetricSpec,
    OutputCheck,
    RunRecord,
    RunWarning,
    Sample,
    TreeEnvironment,
    Verdict,
)

SCHEMA_FILE = Path(schema.__file__).with_name("schema.json")
T0 = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
HASH_A = "a" * 64
HASH_B = "b" * 64
PINNED_SPEC_HASH = (
    "c96961a77154cbd14987e39382171f23a625e23d43bf78a7f8c2a46f3f52e55b"
)
COMMIT = "0123456789abcdef0123456789abcdef01234567"


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


def spec() -> BenchmarkSpec:
    return BenchmarkSpec(
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
        diagnostics=Diagnostics(
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
        calibration="uncalibrated",
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
        calibration="uncalibrated",
        diagnostics=Diagnostics(
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
    )


def gates_passed() -> tuple[GateResult, ...]:
    return (
        GateResult(gate="correctness", status="passed"),
        GateResult(gate="lock", status="passed"),
        GateResult(gate="procedure", status="passed"),
        GateResult(gate="runtime", status="passed"),
        GateResult(gate="same_host", status="passed"),
    )


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
    "diagnostics": Diagnostics(lag1_autocorrelation=-0.2),
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
            "runtime_executable": "interpreter not found on PATH",
            "runtime_version": "interpreter not found on PATH",
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
}


def as_json(model: BaseModel) -> dict[str, Any]:
    # Without spec_hash, so an edited spec fails on the rule under test
    # rather than on its stored hash.
    return model.model_dump(mode="json", exclude_computed_fields=True)


def with_value(data: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """Return a deep copy of ``data`` with the dotted ``path`` set."""
    out = copy.deepcopy(data)
    *parents, last = path.split(".")
    node: Any = out
    for key in parents:
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
    data = with_value(as_json(record()), "schema_version", 2)
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
    ("record", "schema_version", 1.0),
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
    # unknown fields are refused, not dropped
    ("verdict_keep", "speedup", 1.08),
    ("sample", "note", "x"),
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
    ("path", "value"),
    [
        ("estimate", 1.2),
        ("interval", {"lower": 1.1, "upper": 1.3, "confidence": 0.99}),
        (
            "diagnostics.wall_interval",
            {"lower": 1.1, "upper": 1.3, "confidence": 0.99},
        ),
        ("regression", True),
    ],
)
def test_gate_verdict_carries_no_speed_claim(
    factory: Any, path: str, value: Any
) -> None:
    data = with_value(as_json(factory()), path, value)
    with pytest.raises(ValidationError):
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
    ("verdict_keep", {"target_class": None, "calibration": "calibrated"}),
    (
        "verdict_inconclusive",
        {"diagnostics": {"metric_disagreement": True}},
    ),
]


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
    data["diagnostics"] = {}
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


def iv(lower: float, upper: float) -> Interval:
    return Interval(lower=lower, upper=upper, confidence=0.99)


def cross(
    verdict: Verdict, pairs: int, interval: Interval | None = None
) -> Verdict:
    data: dict[str, Any] = {
        **as_json(verdict),
        "cross_host": True,
        "session_pairs": pairs,
        "diagnostics": {},
    }
    if interval is not None:
        data["interval"] = as_json(interval)
    return Verdict.model_validate(data)


@pytest.mark.parametrize(
    "update",
    [
        {"gates": (lock_failed(), *gates_passed()[2:])},
        {"verdict": integrity()},
        {
            "verdict": integrity(),
            "gates": (
                GateResult(gate="runtime", status="failed", detail="3.13"),
            ),
        },
        {"gates": (*gates_passed(), GateResult(gate="lock", status="passed"))},
        {"gates": ()},
        {"gates": gates_passed()[1:]},
        {"verdict": inconclusive(), "gates": gates_passed()[:4]},
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
        {
            "gates": (
                *gates_passed()[:4],
                GateResult(gate="same_host", status="waived", detail="x"),
            )
        },
        {"started_at": T0.replace(tzinfo=None)},
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
            "diagnostics": {},
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


def cross_record(verdict: Verdict) -> RunRecord:
    return record(
        verdict=verdict,
        spec=spec().model_copy(update={"allow_cross_host": True}),
        gates=(
            *gates_passed()[:4],
            GateResult(gate="same_host", status="waived", detail="opt-in"),
        ),
        machines=(machine(), machine()),
    )


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
    "metric": {"kind": "cpu", "direction": "lower_is_better", "unit": "s"},
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
}


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
