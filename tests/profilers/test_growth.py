"""The growth fit on fixed inputs, and a captured profile."""

from pathlib import Path
from typing import Any, cast

import pytest

from perfproof.bench.scaling import SizeKnob, parse_factors, size_spec
from perfproof.profilers.growth import (
    LEAD_MARGIN,
    FunctionKey,
    GrowthResult,
    SizePoint,
    analyse_growth,
    function_key,
    project_functions,
)
from perfproof.schema import BenchmarkSpec, Hotspot

FIXTURES = Path(__file__).parent.parent / "fixtures"
NATIVE = FIXTURES / "py-spy" / "workload-native.txt"
ROOT = Path("/srv/project")
"""Where the workload was mounted when py-spy recorded the fixture."""

Table = dict[FunctionKey, tuple[Hotspot, SizePoint]]


def hotspot(name: str, fraction: float = 0.5) -> Hotspot:
    return Hotspot(
        function=name,
        file=f"app/{name}.py",
        line=3,
        frame_kind="python",
        is_library=False,
        self_fraction=fraction,
        total_fraction=fraction,
    )


def table(**costs: tuple[float, float]) -> Table:
    """Map each name to ``(total_fraction, samples)``."""
    out: Table = {}
    for name, (fraction, samples) in costs.items():
        h = hotspot(name, fraction)
        out[function_key(h)] = (h, SizePoint(fraction, samples))
    return out


def by_name(result: GrowthResult, name: str) -> dict[str, Any]:
    (found,) = [f for f in result.functions if f["function"] == name]
    return cast("dict[str, Any]", found)


def test_exponents_of_exact_powers_are_recovered() -> None:
    sizes = [10, 20, 40, 80]
    walls = [float(s) for s in sizes]
    # cost = share * wall, and quad's share is size / 100.
    tables = [table(quad=(s / 100, 500.0), lin=(0.2, 500.0)) for s in sizes]
    result = analyse_growth(sizes, walls, tables)
    assert result.workload is not None
    assert result.workload.exponent == pytest.approx(1.0, abs=1e-9)
    quad = by_name(result, "quad")["fit"]
    lin = by_name(result, "lin")["fit"]
    assert isinstance(quad, dict)
    assert isinstance(lin, dict)
    assert quad["exponent"] == pytest.approx(2.0, abs=1e-9)
    assert lin["exponent"] == pytest.approx(1.0, abs=1e-9)
    assert quad["n"] == lin["n"] == 4
    assert by_name(result, "quad")["evidence"] == "shown"
    assert by_name(result, "quad")["notes"] == []


SIZES = [10, 20, 40, 80]
WALLS = [float(s) for s in SIZES]


def test_exact_data_shows_even_a_small_excess() -> None:
    """With no scatter the intervals are points: any excess shows."""
    excess = LEAD_MARGIN / 5
    tables = [table(slow=(0.2 * (s / 10) ** excess, 500.0)) for s in SIZES]
    entry = by_name(analyse_growth(SIZES, WALLS, tables), "slow")
    assert entry["fit"]["exponent"] == pytest.approx(1.0 + excess, abs=1e-9)
    assert entry["evidence"] == "shown"
    assert "interval_overlaps_workload" not in entry["notes"]


def test_a_lead_is_not_shown_when_the_intervals_overlap() -> None:
    """Fixed scatter around size**1.5: a point estimate 0.53 above the
    workload's, an interval of 0.49 to 2.58 holding the workload's."""
    noise = [1.0, 0.7, 1.45, 0.85]
    tables = [
        table(f=(0.02 * s**0.5 * n, 500.0))
        for s, n in zip(SIZES, noise, strict=True)
    ]
    result = analyse_growth(SIZES, WALLS, tables)
    entry = by_name(result, "f")
    fit = entry["fit"]
    assert result.workload is not None
    assert fit["exponent"] - result.workload.exponent >= LEAD_MARGIN
    assert fit["low"] <= result.workload.high
    assert entry["evidence"] == "lead"
    assert "interval_overlaps_workload" in entry["notes"]


def test_scatter_around_the_workload_exponent_shows_nothing() -> None:
    noise = [1.0, 1.3, 0.8, 1.1]
    tables = [table(f=(0.02 * n, 500.0)) for n in noise]
    entry = by_name(analyse_growth(SIZES, WALLS, tables), "f")
    assert entry["evidence"] == "none"
    assert "interval_overlaps_workload" not in entry["notes"]


def test_a_point_estimate_just_under_the_margin_is_not_a_lead() -> None:
    noise = [1.0, 0.7, 1.45, 0.85]
    tables = [
        table(f=(0.02 * s**0.1 * n, 500.0))
        for s, n in zip(SIZES, noise, strict=True)
    ]
    entry = by_name(analyse_growth(SIZES, WALLS, tables), "f")
    assert entry["fit"]["exponent"] < 1.0 + LEAD_MARGIN
    assert entry["evidence"] == "none"


def test_sizes_are_sorted_before_fitting() -> None:
    sizes = [40, 10, 80, 20]
    walls = [float(s) for s in sizes]
    tables = [table(quad=(s / 100, 500.0)) for s in sizes]
    result = analyse_growth(sizes, walls, tables)
    quad = by_name(result, "quad")
    fit = quad["fit"]
    assert isinstance(fit, dict)
    assert fit["exponent"] == pytest.approx(2.0, abs=1e-9)
    assert quad["total_fractions"] == [0.1, 0.2, 0.4, 0.8]


def test_three_points_carry_the_few_points_note() -> None:
    sizes = [10, 20, 40]
    walls = [float(s) for s in sizes]
    tables = [table(quad=(s / 100, 500.0)) for s in sizes]
    entry = by_name(analyse_growth(sizes, walls, tables), "quad")
    assert entry["notes"] == ["few_points"]
    assert entry["evidence"] == "shown"


def test_a_scattered_series_is_noisy() -> None:
    sizes = [10, 20, 40, 80]
    walls = [float(s) for s in sizes]
    shares = [0.10, 0.40, 0.05, 0.50]
    tables = [table(wobble=(p, 500.0)) for p in shares]
    notes = by_name(analyse_growth(sizes, walls, tables), "wobble")["notes"]
    assert "noisy" in notes


def test_few_samples_at_any_size_is_noted() -> None:
    sizes = [10, 20, 40, 80]
    walls = [float(s) for s in sizes]
    tables = [table(thin=(s / 100, 5.0 if s == 10 else 500.0)) for s in sizes]
    notes = by_name(analyse_growth(sizes, walls, tables), "thin")["notes"]
    assert notes == ["few_samples"]


def test_a_function_absent_at_small_sizes_has_no_fit_but_is_marked() -> None:
    sizes = [10, 20, 40, 80]
    walls = [float(s) for s in sizes]
    tables = [
        table(late=(0.3, 500.0)) if s >= 40 else table(other=(0.5, 500.0))
        for s in sizes
    ]
    late = by_name(analyse_growth(sizes, walls, tables), "late")
    assert late["fit"] is None
    assert late["evidence"] is None
    assert late["appears_at_larger_sizes"] is True
    assert late["costs"][:2] == [None, None]
    assert late["notes"] == ["missing_sizes", "no_fit"]


def test_a_function_seen_at_three_of_four_sizes_is_fitted() -> None:
    sizes = [10, 20, 40, 80]
    walls = [float(s) for s in sizes]
    tables = [
        table(gap=(s / 100, 500.0)) if s != 20 else table(x=(0.5, 500.0))
        for s in sizes
    ]
    gap = by_name(analyse_growth(sizes, walls, tables), "gap")
    fit = gap["fit"]
    assert isinstance(fit, dict)
    assert fit["n"] == 3
    assert fit["exponent"] == pytest.approx(2.0, abs=1e-9)
    assert gap["notes"] == ["missing_sizes", "few_points"]
    assert gap["appears_at_larger_sizes"] is False


def test_the_same_function_in_another_file_is_another_function() -> None:
    a, b = hotspot("f"), hotspot("f").model_copy(update={"file": "app/g.py"})
    assert function_key(a) != function_key(b)
    assert function_key(a) == function_key(a.model_copy(update={"line": 9}))


def test_two_sizes_cannot_fit_a_workload() -> None:
    result = analyse_growth(
        [1, 2], [1.0, 2.0], [table(a=(0.5, 500.0)), table(a=(0.5, 500.0))]
    )
    assert result.workload is None
    entry = by_name(result, "a")
    assert entry["fit"] is None
    assert entry["evidence"] is None


def test_bad_inputs_are_refused() -> None:
    with pytest.raises(ValueError, match="equal in length"):
        analyse_growth([1, 2, 4], [1.0, 2.0], [{}, {}, {}])
    with pytest.raises(ValueError, match="equal in length"):
        analyse_growth([], [], [])


def test_project_functions_reads_the_captured_profile() -> None:
    found, total = project_functions(NATIVE, ROOT, [], weight_unit="samples")
    assert total == 512
    names = {h.function for h, _ in found.values()}
    assert "slow_sum" in names
    assert all(not h.is_library for h, _ in found.values())
    slow = next(p for h, p in found.values() if h.function == "slow_sum")
    assert slow.samples == pytest.approx(slow.total_fraction * 512)


def test_microseconds_count_as_one_millisecond_samples() -> None:
    samples, total_s = project_functions(
        NATIVE, ROOT, [], weight_unit="samples"
    )
    micro, total_us = project_functions(
        NATIVE, ROOT, [], weight_unit="microseconds"
    )
    assert total_us == pytest.approx(total_s / 1000)
    key = next(iter(samples))
    assert micro[key][1].total_fraction == samples[key][1].total_fraction
    assert micro[key][1].samples == pytest.approx(
        samples[key][1].samples / 1000
    )


def test_parse_factors_matches_what_measure_accepts() -> None:
    assert parse_factors("1,2,4") == (1, 2, 4)
    assert parse_factors("1x, 2x,4x") == (1, 2, 4)
    for bad in ("1,2", "1,1,2", "0,1,2", "a,b,c", ""):
        with pytest.raises(ValueError, match="three distinct"):
            parse_factors(bad)


def sized_spec() -> BenchmarkSpec:
    return BenchmarkSpec.model_validate(
        {
            "name": "w",
            "command": ["python", "work.py"],
            "env": {"KEEP": "1"},
            "metric": {
                "kind": "wall",
                "direction": "lower_is_better",
                "unit": "s",
            },
            "correctness": {"command": ["true"], "timeout_seconds": 30.0},
            "warmup": 0,
            "budget": {"max_rounds": 20, "max_seconds": 60.0},
            "looks": {"n0": 10, "k": 10, "max_looks": 2},
            "timeout_seconds": 60.0,
            "harness": ["work.py"],
        }
    )


def test_size_spec_sets_the_flag_or_the_variable() -> None:
    spec = sized_spec()
    with_arg = size_spec(spec, SizeKnob("arg", "--n", 1000), 4)
    assert with_arg.command == ("python", "work.py", "--n", "4000")
    with_env = size_spec(spec, SizeKnob("env", "ITEMS", 500), 2)
    assert with_env.command == spec.command
    assert dict(with_env.env) == {"KEEP": "1", "ITEMS": "1000"}
    assert spec.command == ("python", "work.py")
