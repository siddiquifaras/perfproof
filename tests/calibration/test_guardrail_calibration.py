"""Guardrail calibration: the harness, the record, and a rerun subset.

``docs/method.md`` ("Guardrail cells" and "Guardrail calibration
results") is the specification. The first group holds the harness to
what it claims and runs in the fast suite. The marked group rereads the
recorded per-cell tables and reruns every cell nearest the bar: at the
seed of record each count must equal the recorded one, at any other seed
(``PERFPROOF_CALIBRATION_SEED``) every cell must still pass the
acceptance rule, the messy limit cells (outliers or a slow mode in the
candidate's own values) included.
"""

import csv
import math
from functools import cache
from pathlib import Path
from typing import Any, Final, cast

import guardrail_mc as g
import montecarlo as mc
import numpy as np
import pytest

RESULTS: Final = Path(__file__).parent / "results"
RECORD: Final = g.DEFAULT_SEED
SECOND: Final = 9113244239656782113
"""The second run; its seed was drawn after the first run finished."""
NEAREST: Final = {
    "rel1": ("1.10", "1.11"),
    "rel3": ("1.10", "1.11"),
    "limit": ("1.00", "1.01"),
}


@cache
def _recorded(seed: int) -> dict[str, dict[str, str]]:
    path = RESULTS / f"guardrails-seed-{seed}.csv"
    with path.open(newline="") as source:
        return {r["cell"]: r for r in csv.DictReader(source)}


# The harness


def test_the_cells_are_every_schedule_scenario_configuration_and_ratio() -> (
    None
):
    cells = g.all_cells()
    names = [c.name for c in cells]
    assert len(set(names)) == len(names)
    one_host = mc.scenarios("one_host")
    stationary = [n for n, s in one_host.items() if s.drift == 0.0]
    per_schedule = (
        (len(one_host) + len(g.PAGES)) * len(g.REL_RATIOS)
        + (len(one_host) + len(g.PAGES)) * len(g.REL3_RATIOS)
        + (len(stationary) + len(g.LIMIT_PAGES)) * len(g.LIMIT_RATIOS)
    )
    assert len(cells) == per_schedule * len(mc.SCHEDULES) == 1448
    for cell in cells:
        assert g.cell_by_name(cell.name) == cell


def test_a_cell_name_that_is_not_a_cell_is_refused() -> None:
    for name in (
        "python_native/drift/limit/1.00",
        "python_native/lognormal_cv5/rel9/1.00",
        "python_native/lognormal_cv5/rel1/1.001",
        "nowhere/lognormal_cv5/rel1/1.00",
    ):
        with pytest.raises((ValueError, KeyError)):
            g.cell_by_name(name)


def test_the_error_of_a_cell_is_the_side_its_true_value_is_on() -> None:
    sched = mc.SCHEDULES[0]
    for config, within, beyond in (
        ("rel1", 1.10, 1.11),
        ("limit", 1.00, 1.01),
    ):
        assert g.Cell(sched, "ar1", config, within).truly_within  # pyright: ignore[reportArgumentType]
        assert not g.Cell(sched, "ar1", config, beyond).truly_within  # pyright: ignore[reportArgumentType]


def test_only_limit_cells_with_rare_large_values_are_messy() -> None:
    messy = {c.name: c.messy for c in g.all_cells()}
    scenarios = {n.split("/")[1] for n, j in messy.items() if j}
    assert scenarios == {
        "bimodal",
        "outliers",
        "A00-outliers-only",
        "A00-outliers-5pct-x5-cv1",
        "A00-outliers-1pct-x5-cv1",
        "A20261053-04",
        "A20261054-08",
        "A20261055-17",
    }
    assert all(n.split("/")[2] == "limit" for n, j in messy.items() if j)
    assert sum(messy.values()) == 192


def test_the_median_of_the_candidates_log_time_is_what_the_model_says() -> (
    None
):
    # Symmetric noise has its median at the shifts alone: the order
    # effect moves half the rounds, so the median sits between.
    assert g.candidate_log_median("lognormal_cv5") == pytest.approx(
        0.0, abs=2e-4
    )
    assert g.candidate_log_median("student_t_df3") == pytest.approx(
        0.0, abs=2e-4
    )
    assert g.candidate_log_median("order_effect") == pytest.approx(
        -0.5 * math.log1p(0.03), abs=2e-3
    )
    # A slow mode of 10% moves the median by its rank: a little, far
    # less than its mean shift of 0.1 log 1.3.
    shifted = g.candidate_log_median("bimodal")
    assert 0.0 < shifted < 0.1 * math.log(1.3)
    # 2% outliers of 3 times move the median by a hair.
    assert 0.0 <= g.candidate_log_median("outliers") < 3e-3


def test_a_limit_is_the_candidates_median_over_the_ratio() -> None:
    sched = mc.SCHEDULES[0]
    flat = g.Cell(sched, "lognormal_cv5", "limit", 1.25)
    assert g.limit_value(flat) == pytest.approx(g.LEVEL / 1.25, rel=3e-4)
    pages = g.Cell(sched, "pages_noisy", "limit", 1.0)
    assert g.limit_value(pages) == 5_000 * g.PAGE
    with pytest.raises(ValueError, match="no fixed level"):
        g.limit_value(g.Cell(sched, "pages_exact", "limit", 1.0))


def test_page_values_are_whole_pages_with_the_true_geometric_ratio() -> None:
    rng = np.random.default_rng(1)
    base, cand, first = g.pages("pages_noisy", 1.2, 4000, 24, rng)
    assert np.all(base % g.PAGE == 0)
    assert np.all(cand % g.PAGE == 0)
    ratio = np.exp(np.log(cand / base).mean())
    assert ratio == pytest.approx(1.2, rel=2e-3)
    assert first.shape == base.shape
    assert np.all(first[:, 0::2] != first[:, 1::2])
    exact, moved, _ = g.pages("pages_exact", 1.1, 50, 12, rng)
    assert np.all(exact == exact[:, :1])
    assert np.all(moved == moved[:, :1])
    assert np.any(moved / exact != 1.1)
    assert np.abs(np.log(moved / exact) - math.log(1.1)).max() < 1e-4


def test_a_cell_gives_the_same_counts_whichever_cells_run_with_it() -> None:
    cell = g.cell_by_name("web_agentic/ar1/rel1/1.10")
    first = g.run_cell(cell, seed=7, replications=300)
    g.run_cell(
        g.cell_by_name("web_agentic/ar1/rel1/1.20"), seed=7, replications=300
    )
    again = g.run_cell(cell, seed=7, replications=300)
    for key in ("alone", "verdict", "rounds_p95"):
        assert first[key] == again[key]


def test_the_two_processes_agree_where_the_primary_always_keeps() -> None:
    # A true primary speedup of 1.2 keeps at the first look that has an
    # interval, so a guardrail that decides there ends both processes.
    out = g.run_cell(
        g.cell_by_name("python_native/lognormal_cv1/rel1/1.50"),
        seed=3,
        replications=300,
    )
    alone = out["alone"]
    verdict = out["verdict"]
    assert isinstance(alone, dict)
    assert isinstance(verdict, dict)
    assert alone["breached"] == verdict["reject"] == 300
    assert alone["errors"] == verdict["errors"] == 0


def test_a_narrow_interval_fails_the_cells_nearest_the_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The harness has teeth: a guardrail confidence of 0.95 breaches
    # the cell on the bar far more than 2.5% of the time.
    def narrow(alpha: float, looks: int, checks: int) -> float:
        return 0.95

    monkeypatch.setattr(g, "guardrail_confidence", narrow)
    out = g.run_cell(
        g.cell_by_name("python_native/lognormal_cv5/rel1/1.10"), seed=12345
    )
    alone = out["alone"]
    assert isinstance(alone, dict)
    assert not alone["passes"]
    assert alone["errors"] > 83


# The record


@pytest.mark.calibration
@pytest.mark.parametrize("seed", [RECORD, SECOND])
def test_the_recorded_tables_have_every_cell_and_each_one_passes(
    seed: int,
) -> None:
    rows = _recorded(seed)
    cells = g.all_cells()
    assert set(rows) == {c.name for c in cells}
    for cell in cells:
        row = rows[cell.name]
        assert int(row["replications"]) == mc.REPLICATIONS
        assert int(row["messy"]) == cell.messy
        assert int(row["truly_within"]) == cell.truly_within
        for process, own in (("alone", "passed"), ("verdict", "keep")):
            errors = int(row[f"{process}_errors"])
            if cell.truly_within:
                own = "breached" if process == "alone" else "reject"
            assert errors == int(row[f"{process}_{own}"])
            passes = mc.passes(errors, mc.REPLICATIONS)
            assert int(row[f"{process}_passes"]) == passes
            assert passes, (cell.name, process, errors)


@pytest.mark.calibration
@pytest.mark.parametrize("schedule", [s.name for s in mc.SCHEDULES])
def test_the_cells_nearest_the_bar_reproduce_and_pass(schedule: str) -> None:
    seed = g.calibration_seed()
    failing: list[str] = []
    for cell in g.all_cells():
        near = NEAREST.get(cell.config, ())
        if cell.schedule.name != schedule or f"{cell.ratio:.2f}" not in near:
            continue
        found = g.run_cell(cell, seed=seed)
        for process in ("alone", "verdict"):
            summary = cast("dict[str, Any]", found[process])
            errors = int(summary["errors"])
            if not mc.passes(errors, mc.REPLICATIONS):
                failing.append(f"{cell.name} {process}: {errors}")
            if seed == RECORD:
                row = _recorded(seed)[cell.name]
                assert summary["errors"] == int(row[f"{process}_errors"]), (
                    cell.name
                )
    assert not failing


def test_the_seed_comes_from_the_environment_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(g.SEED_ENV, raising=False)
    assert g.calibration_seed() == RECORD
    monkeypatch.setenv(g.SEED_ENV, "42")
    assert g.calibration_seed() == 42
    monkeypatch.setenv(g.SEED_ENV, "-1")
    with pytest.raises(ValueError, match="non-negative"):
        g.calibration_seed()
