"""Tests for the ``perfproof find`` models in `perfproof.schema`."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, get_args

import pytest
from pydantic import BaseModel, ValidationError

from perfproof import schema
from perfproof.schema import (
    Established,
    ExistingBenchmark,
    FindBudget,
    FindCall,
    FindResult,
    FindState,
    Lead,
    MapRow,
    NativeCode,
    NextStep,
    Part,
    PartTooling,
    ProbeRecord,
    SizeKnobSpec,
    TestCommand,
    ToolStatus,
)

HASH = "a" * 64


def tooling() -> PartTooling:
    return PartTooling(
        profiler=ToolStatus(name="pyspy", available=True, reason=None),
        native_recorder=ToolStatus(
            name="samply", available=False, reason="not installed"
        ),
        coverage="missing",
        coverage_install=("uv", "tool", "install", "coverage"),
    )


def part() -> Part:
    return Part(
        id="root",
        root=".",
        manifests=("pyproject.toml",),
        target_id="app",
        language="python",
        target_class=Established[schema.TargetClass](value="python_cpu"),
        test_command=Established[TestCommand](
            value=TestCommand(
                argv=(".venv/bin/python", "-m", "pytest"),
                cwd=".",
                evidence="pyproject.toml",
            )
        ),
        native=(
            NativeCode(
                kind="pyo3",
                name="fast",
                own_module="fast.so",
                sources=("src/lib.rs",),
            ),
        ),
        benchmarks=(
            ExistingBenchmark(
                kind="script", name="b1", argv=("python", "b.py"), cwd="."
            ),
        ),
        entry_points=(
            schema.EntryPoint(
                kind="console_script", name="app", file="app/cli.py", line=3
            ),
        ),
        tooling=tooling(),
    )


def probe(key: str = "find-tests-root") -> ProbeRecord:
    return ProbeRecord(
        key=key,
        requirement=True,
        spec_hash=HASH,
        tree_id="t1",
        status="failed",
        run_ids=("r1",),
        reason="boom",
        log_tail="trace",
    )


def state(**over: Any) -> FindState:
    data: dict[str, Any] = {
        "parts_hash": HASH,
        "probes": (probe(),),
        "size_knobs": {"w1": SizeKnobSpec(kind="arg", name="--n", base=10)},
        "confirmed_tests": {"root": ("pytest",)},
        "waivers": {"define_workloads": "only one kind"},
        "calls": (
            FindCall(
                started_at=datetime(2026, 10, 30, tzinfo=UTC),
                spent_seconds=1.5,
            ),
        ),
    }
    data.update(over)
    return FindState(**data)


def result() -> FindResult:
    return FindResult(
        done=False,
        depth="medium",
        parts=(part(),),
        question="Confirm the class?",
        next_steps=(
            NextStep(
                code="install_or_confirm",
                message="m",
                command=("perfproof", "find"),
            ),
            NextStep(code="continue_find", message="again", command=None),
        ),
        map=(
            MapRow(
                function="f",
                file="a.py",
                line=1,
                part="root",
                layer="python",
                cap=2.0,
                total_fraction=0.5,
                seen_in=("find-tests-root",),
            ),
        ),
        leads=(
            Lead(
                kind="function",
                name="f",
                file="a.py",
                part="root",
                cap=None,
                seen_in=("find-tests-root",),
                via_test="test_f",
            ),
        ),
        not_reached=("app",),
        skipped=(probe(),),
        budget=FindBudget(
            spent_seconds=3.0, limit_seconds=900.0, exhausted=False
        ),
    )


def test_find_result_round_trips_through_json() -> None:
    original = result()
    text = original.model_dump_json()
    assert FindResult.model_validate_json(text) == original
    assert FindResult.model_validate(json.loads(text)) == original


def test_find_state_round_trips_and_is_hashable() -> None:
    original = state()
    text = original.model_dump_json()
    assert FindState.model_validate_json(text) == original
    assert json.loads(text)["waivers"] == {"define_workloads": "only one kind"}
    assert hash(original) == hash(FindState.model_validate_json(text))


@pytest.mark.parametrize(
    ("value", "reason"),
    [(None, None), ("python_cpu", "also a reason")],
)
def test_established_needs_exactly_one_side(
    value: str | None, reason: str | None
) -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        Established[str](value=value, reason=reason)


def test_established_accepts_either_side() -> None:
    assert Established[str](value="x").reason is None
    assert Established[str](reason="why").value is None


def test_part_target_class_rejects_unknown_class() -> None:
    data = part().model_dump(mode="json")
    data["target_class"] = {"value": "quantum", "reason": None}
    with pytest.raises(ValidationError):
        Part.model_validate(data)


@pytest.mark.parametrize("key", ["find-tests-root", "w@native", "w@sizes"])
def test_probe_key_accepts(key: str) -> None:
    assert probe(key).key == key


@pytest.mark.parametrize(
    "key",
    ["", "@native", "w@other", "w@native@sizes", "a b", "w@", "w@native\n"],
)
def test_probe_key_rejects(key: str) -> None:
    with pytest.raises(ValidationError):
        probe(key)


@pytest.mark.parametrize("code", ["install_or_confirm", "continue_find"])
def test_waivers_refuse_unwaivable_codes(code: str) -> None:
    with pytest.raises(ValidationError, match="cannot be waived"):
        state(waivers={code: "because"})


def test_waivers_refuse_unknown_code() -> None:
    with pytest.raises(ValidationError):
        state(waivers={"nonsense": "because"})


@pytest.mark.parametrize(
    "code", ["define_workloads", "add_size_knob", "reach_lead"]
)
def test_waivers_accept_other_codes(code: str) -> None:
    assert dict(state(waivers={code: "why"}).waivers) == {code: "why"}


@pytest.mark.parametrize("bad", ["a/b", "a b", "", "-x"])
def test_part_id_must_be_a_name(bad: str) -> None:
    data = part().model_dump(mode="json")
    data["id"] = bad
    with pytest.raises(ValidationError):
        Part.model_validate(data)


@pytest.mark.parametrize("base", [0, -1, 1.5, True, "3"])
def test_size_knob_base_must_be_positive_int(base: object) -> None:
    with pytest.raises(ValidationError):
        SizeKnobSpec.model_validate(
            {"kind": "arg", "name": "--n", "base": base}
        )


def test_models_are_closed_and_frozen() -> None:
    with pytest.raises(ValidationError):
        NextStep.model_validate(
            {"code": "reach_lead", "message": "m", "command": None, "x": 1}
        )
    with pytest.raises(ValidationError):
        part().id = "other"  # type: ignore[misc]


def literal_values(model: type[Any], field: str) -> tuple[str, ...]:
    return tuple(get_args(model.model_fields[field].annotation))


PINNED: list[tuple[type[Any], str, tuple[str, ...]]] = [
    (
        Part,
        "language",
        (
            "python",
            "javascript",
            "typescript",
            "rust",
            "go",
            "java",
            "kotlin",
            "c",
            "cpp",
            "csharp",
            "ruby",
            "other",
        ),
    ),
    (
        NativeCode,
        "kind",
        (
            "setuptools_ext",
            "pyo3",
            "maturin",
            "node_gyp",
            "cgo",
            "cmake",
            "cargo_cdylib",
        ),
    ),
    (
        ExistingBenchmark,
        "kind",
        ("pytest_benchmark", "asv", "cargo_bench", "go_bench", "script"),
    ),
    (
        schema.EntryPoint,
        "kind",
        ("console_script", "bin", "main_package", "route", "public_module"),
    ),
    (PartTooling, "coverage", ("not_needed", "present", "missing")),
    (
        ProbeRecord,
        "status",
        (
            "pending",
            "done",
            "failed",
            "timeout",
            "skipped_unconfirmed",
            "skipped_no_tests",
            "tree_modified",
        ),
    ),
    (
        NextStep,
        "code",
        (
            "install_or_confirm",
            "define_workloads",
            "add_size_knob",
            "reach_lead",
            "continue_find",
        ),
    ),
    (
        MapRow,
        "layer",
        (
            "python",
            "native",
            "gpu",
            "javascript",
            "typescript",
            "rust",
            "go",
            "java",
            "kotlin",
            "csharp",
            "ruby",
            "other",
        ),
    ),
    (FindResult, "depth", ("light", "medium", "deep")),
    (SizeKnobSpec, "kind", ("arg", "env")),
    (Lead, "kind", ("function", "file")),
]


@pytest.mark.parametrize(("model", "field", "values"), PINNED)
def test_enum_sets_are_pinned(
    model: type[Any], field: str, values: tuple[str, ...]
) -> None:
    assert literal_values(model, field) == values


FIND_MODELS = [
    "Established",
    "TestCommand",
    "NativeCode",
    "ExistingBenchmark",
    "EntryPoint",
    "ToolStatus",
    "PartTooling",
    "Part",
    "SizeKnobSpec",
    "ProbeRecord",
    "FindCall",
    "FindState",
    "MapRow",
    "Lead",
    "NextStep",
    "FindBudget",
    "FindResult",
]


def test_find_models_are_exported_and_in_the_json_schema() -> None:
    defs = json.loads(schema.json_schema_text())["$defs"]
    for name in FIND_MODELS:
        assert name in schema.__all__
    for name in FIND_MODELS[1:]:
        assert name in defs
    assert schema.SCHEMA_VERSION == 4
    assert json.loads(state().model_dump_json())["schema_version"] == 1


def find_examples() -> dict[str, BaseModel]:
    """One example of every find model, for the round trip test."""
    r = result()
    p = part()
    return {
        "find_test_command": TestCommand(
            argv=("pytest",), cwd=".", evidence="pyproject.toml"
        ),
        "find_native_code": p.native[0],
        "find_existing_benchmark": p.benchmarks[0],
        "find_entry_point": p.entry_points[0],
        "find_tool_status": p.tooling.profiler,
        "find_part_tooling": p.tooling,
        "find_part": p,
        "find_size_knob": SizeKnobSpec(kind="env", name="N", base=4),
        "find_probe": probe(),
        "find_call": state().calls[0],
        "find_state": state(),
        "find_map_row": r.map[0],
        "find_lead": r.leads[0],
        "find_next_step": r.next_steps[0],
        "find_budget": r.budget,
        "find_result": r,
    }
