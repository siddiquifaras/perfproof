import os
import shutil
import sys
from pathlib import Path

import pytest

from perfproof.bench.conditions import (
    Condition,
    MeasureError,
    run_conditions,
)
from perfproof.bench.scaling import (
    SizeKnob,
    measure_sizes,
    parse_knob,
)
from perfproof.bench.setting import measure_setting, parse_setting
from perfproof.schema import BenchmarkSpec, ResetSpec

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def project(tmp_path: Path, example: str, *, fix: bool = False) -> Path:
    root = tmp_path / f"{example}-{'fix' if fix else 'base'}"
    shutil.copytree(
        EXAMPLES / example,
        root,
        ignore=shutil.ignore_patterns("candidates", "__pycache__", "output"),
    )
    if fix:
        for source in (EXAMPLES / example / "candidates" / "fix").iterdir():
            shutil.copy2(source, root / source.name)
    return root


def example_spec(example: str) -> BenchmarkSpec:
    spec = BenchmarkSpec.model_validate_json(
        (EXAMPLES / example / "spec.json").read_text()
    )
    return spec.model_copy(
        update={"command": (sys.executable, *spec.command[1:])}
    )


# Scaling, on the planted quadratic


def sizes(root: Path, tmp_path: Path, rounds: int = 3):
    return measure_sizes(
        example_spec("growth"),
        root,
        SizeKnob("arg", "--n", 500),
        (1, 2, 4),
        rounds=rounds,
        work_dir=tmp_path / "runs",
        seed=1,
        process_env=os.environ,
    )


def test_a_planted_quadratic_is_fitted_as_quadratic(tmp_path: Path) -> None:
    result = sizes(project(tmp_path, "growth"), tmp_path)
    assert result.sizes == (500, 1000, 2000)
    assert result.fit.n == 3 * 3
    assert 1.6 < result.fit.exponent < 2.4
    assert result.fit.low < result.fit.exponent < result.fit.high
    assert result.fit.high - result.fit.low < 1.0
    assert result.metric_kind == "reported"


def test_its_real_fix_is_fitted_as_linear(tmp_path: Path) -> None:
    result = sizes(project(tmp_path, "growth", fix=True), tmp_path)
    assert 0.5 < result.fit.exponent < 1.6


def test_a_size_in_the_environment_is_scaled_too(tmp_path: Path) -> None:
    root = project(tmp_path, "growth")
    (root / "bench.py").write_text(
        (root / "bench.py")
        .read_text()
        .replace(
            "DEFAULT_N = 1_000",
            'DEFAULT_N = int(__import__("os").environ.get("ITEMS", "1000"))',
        )
    )
    result = measure_sizes(
        example_spec("growth"),
        root,
        SizeKnob("env", "ITEMS", 500),
        (1, 2, 4),
        rounds=3,
        work_dir=tmp_path / "runs",
        seed=1,
        process_env=os.environ,
    )
    assert 1.6 < result.fit.exponent < 2.4


def test_a_wall_metric_is_noted_to_include_start_up(tmp_path: Path) -> None:
    spec = example_spec("growth")
    wall = spec.model_copy(
        update={
            "metric": spec.metric.model_copy(
                update={"kind": "wall", "unit": "s"}
            )
        }
    )
    result = measure_sizes(
        wall,
        project(tmp_path, "growth"),
        SizeKnob("arg", "--n", 500),
        (1, 2, 4),
        rounds=3,
        work_dir=tmp_path / "runs",
        seed=1,
        process_env=os.environ,
    )
    assert any("start-up" in note for note in result.notes)
    assert len(result.fit.local_exponents) == 2


@pytest.mark.parametrize("factors", [(1, 2), (1, 1, 2), (0, 1, 2), ()])
def test_fewer_than_three_sizes_or_a_zero_multiplier_is_refused(
    tmp_path: Path, factors: tuple[int, ...]
) -> None:
    with pytest.raises(MeasureError) as info:
        measure_sizes(
            example_spec("growth"),
            project(tmp_path, "growth"),
            SizeKnob("arg", "--n", 500),
            factors,
            rounds=3,
            work_dir=tmp_path / "runs",
            seed=1,
            process_env=os.environ,
        )
    assert info.value.code == "invalid_arguments"


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        ("arg", "--n=1000"),
        ("env", "ITEMS=250"),
        ("arg", "--size=7"),
    ],
)
def test_a_size_knob_is_parsed(kind: str, text: str) -> None:
    knob = parse_knob("arg" if kind == "arg" else "env", text)
    assert knob.name == text.rpartition("=")[0]
    assert knob.base == int(text.rpartition("=")[2])


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        ("arg", "n=1000"),
        ("arg", "--n"),
        ("arg", "--n=0"),
        ("arg", "--n=ten"),
        ("env", "1ITEMS=5"),
        ("env", "ITEMS="),
        ("env", "ITEMS=-3"),
    ],
)
def test_a_bad_size_knob_is_refused(kind: str, text: str) -> None:
    with pytest.raises(MeasureError) as info:
        parse_knob("arg" if kind == "arg" else "env", text)
    assert info.value.code == "invalid_arguments"


# Conditions


def test_conditions_run_interleaved_and_each_round_has_two_executions(
    tmp_path: Path,
) -> None:
    observed = run_conditions(
        example_spec("growth"),
        project(tmp_path, "growth"),
        (Condition("a"), Condition("b", argv_extra=("--n", "200"))),
        rounds=3,
        work_dir=tmp_path / "runs",
        seed=5,
        process_env=os.environ,
    )
    assert len(observed) == 6
    assert {(o.key, o.round) for o in observed} == {
        (k, r) for k in "ab" for r in range(3)
    }
    assert all(len(o.values) == 2 for o in observed)
    # Each round runs both conditions before the next round starts.
    assert sorted(o.round for o in observed[:2]) == [0, 0]
    # Fewer readings make the benchmark's metric smaller.
    small = [v for o in observed if o.key == "b" for v in o.values]
    large = [v for o in observed if o.key == "a" for v in o.values]
    assert max(small) < min(large)


def test_the_order_within_rounds_follows_the_seed(tmp_path: Path) -> None:
    root = project(tmp_path, "growth")

    def keys(name: str) -> list[str]:
        return [
            o.key
            for o in run_conditions(
                example_spec("growth"),
                root,
                (
                    Condition("a", argv_extra=("--n", "50")),
                    Condition("b", argv_extra=("--n", "50")),
                ),
                rounds=8,
                work_dir=tmp_path / name,
                seed=11,
                process_env=os.environ,
            )
        ]

    first = keys("one")
    assert keys("two") == first
    pairs = {tuple(first[i : i + 2]) for i in range(0, len(first), 2)}
    assert pairs == {("a", "b"), ("b", "a")}


@pytest.mark.parametrize(
    ("conditions", "rounds"),
    [((), 3), ((Condition("a"), Condition("a")), 3)],
)
def test_conditions_need_distinct_keys(
    tmp_path: Path, conditions: tuple[Condition, ...], rounds: int
) -> None:
    with pytest.raises(MeasureError) as info:
        run_conditions(
            example_spec("growth"),
            project(tmp_path, "growth"),
            conditions,
            rounds=rounds,
            work_dir=tmp_path / "runs",
            seed=1,
            process_env=os.environ,
        )
    assert info.value.code == "invalid_arguments"


def test_rounds_beyond_the_budget_are_refused(tmp_path: Path) -> None:
    spec = example_spec("growth")
    with pytest.raises(
        MeasureError, match=f"budget of {spec.budget.max_rounds}"
    ) as info:
        run_conditions(
            spec,
            project(tmp_path, "growth"),
            (Condition("a"),),
            rounds=spec.budget.max_rounds + 1,
            work_dir=tmp_path / "runs",
            seed=1,
            process_env=os.environ,
        )
    assert info.value.code == "invalid_arguments"


def test_a_failing_condition_is_named(tmp_path: Path) -> None:
    with pytest.raises(MeasureError) as info:
        run_conditions(
            example_spec("growth"),
            project(tmp_path, "growth"),
            (Condition("ok"), Condition("bad", argv_extra=("--nope",))),
            rounds=3,
            work_dir=tmp_path / "runs",
            seed=1,
            process_env=os.environ,
        )
    assert info.value.code == "workload_failed"
    assert info.value.details["condition"] == "bad"
    assert Path(str(info.value.details["stderr"])).is_file()


def test_a_spec_with_a_reset_is_refused(tmp_path: Path) -> None:
    spec = example_spec("growth").model_copy(
        update={
            "reset": ResetSpec(command=("true",), cwd=".", timeout_seconds=5.0)
        }
    )
    with pytest.raises(MeasureError) as info:
        run_conditions(
            spec,
            project(tmp_path, "growth"),
            (Condition("a"),),
            rounds=3,
            work_dir=tmp_path / "runs",
            seed=1,
            process_env=os.environ,
        )
    assert info.value.code == "unsupported_spec"


# Settings


def test_a_planted_setting_is_measured_with_its_ratio(tmp_path: Path) -> None:
    result = measure_setting(
        example_spec("settings"),
        project(tmp_path, "settings"),
        "FLUSH_EVERY",
        "100",
        rounds=3,
        work_dir=tmp_path / "runs",
        seed=1,
        process_env=os.environ,
    )
    # Flushing every record waits 1,000 times, every 100 waits 10 times.
    assert result.ratio.value > 3
    assert result.ratio.low is not None
    assert result.ratio.low > 1.5
    assert result.unchanged.value > result.changed.value
    assert result.declared is None
    assert (result.name, result.value) == ("FLUSH_EVERY", "100")


def test_a_setting_the_spec_declares_is_reported_as_declared(
    tmp_path: Path,
) -> None:
    spec = example_spec("settings")
    declared = spec.model_copy(update={"env": {"FLUSH_EVERY": "50"}})
    result = measure_setting(
        declared,
        project(tmp_path, "settings"),
        "FLUSH_EVERY",
        "200",
        rounds=3,
        work_dir=tmp_path / "runs",
        seed=1,
        process_env=os.environ,
    )
    assert result.declared == "50"
    assert result.unchanged.value > result.changed.value


def test_a_setting_that_reaches_a_remote_service_is_refused(
    tmp_path: Path,
) -> None:
    with pytest.raises(MeasureError) as info:
        measure_setting(
            example_spec("settings"),
            project(tmp_path, "settings"),
            "DATABASE_URL",
            "postgresql://db.example.com:5432/orders",
            rounds=3,
            work_dir=tmp_path / "runs",
            seed=1,
            process_env=os.environ,
        )
    assert info.value.code == "side_effects"
    assert not (tmp_path / "runs").exists()


def test_a_setting_needs_three_rounds(tmp_path: Path) -> None:
    with pytest.raises(MeasureError) as info:
        measure_setting(
            example_spec("settings"),
            project(tmp_path, "settings"),
            "FLUSH_EVERY",
            "100",
            rounds=2,
            work_dir=tmp_path / "runs",
            seed=1,
            process_env=os.environ,
        )
    assert info.value.code == "invalid_arguments"


@pytest.mark.parametrize(
    ("text", "pair"),
    [("A=1", ("A", "1")), ("_x9=", ("_x9", "")), ("K=a=b", ("K", "a=b"))],
)
def test_a_setting_is_parsed(text: str, pair: tuple[str, str]) -> None:
    assert parse_setting(text) == pair


@pytest.mark.parametrize(
    "text",
    ["A", "=1", "1A=2", "A-B=1", "PYTHONPATH=x", "PERFPROOF_STUB_CONFIG=x"],
)
def test_a_bad_or_reserved_setting_is_refused(text: str) -> None:
    with pytest.raises(MeasureError) as info:
        parse_setting(text)
    assert info.value.code == "invalid_arguments"
