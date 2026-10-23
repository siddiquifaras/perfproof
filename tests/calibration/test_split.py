"""Acceptance of the split, the procedure ``perfproof compare`` ships.

"The T1.11 decision" in ``docs/method.md``: ``hac_lf`` decides the
``python_native``, ``web_agentic`` and ``llm_serving`` schedules and
the T1.8 gated batch means decides ``gpu_kernels`` and any other
schedule. `montecarlo.SPLIT` runs each calibration schedule through
`perfproof.stats.sequential.procedure_for_schedule`, so these tests
exercise the selection and the procedure it names in one pass.

The acceptance rule of ``docs/method.md`` applies unchanged: in every
cell of the main grid (the development and exposed scenarios) and the
held-out grid, both host modes, the one-sided 95% Clopper-Pearson upper
bound on the error rate at ``R = 4000`` is at most 2.5%, that is at
most 83 errors. The Monte Carlo error of a count is its binomial
standard error, ``sqrt(R p (1 - p))``: 9.9 errors at the limit's
``p = 0.025`` (0.25 percentage points), 7.0 at ``p = 0.0125``, which is
what the Bonferroni split designs to. The seed is fixed, so a rerun
reproduces the committed counts exactly.
"""

import csv
import os
from functools import cache
from pathlib import Path
from typing import Any, Final, cast

import montecarlo as mc
import pytest

from perfproof.stats import sequential

RESULTS: Final = Path(__file__).parent / "results"
SPLIT_SEED: Final = 5499072700741048927
"""Drawn from the OS entropy pool before the first split run; no result
under it had been seen when it was fixed."""
RECORDED: Final = RESULTS / f"t113-split-seed-{SPLIT_SEED}.csv"
GATED: Final = "t_log_ratio_batch_means"


@cache
def _recorded() -> dict[str, dict[str, str]]:
    with RECORDED.open(newline="") as source:
        return {
            r["cell"]: r
            for r in csv.DictReader(source)
            if r["procedure"] == mc.SPLIT
        }


def _cells(schedule: str) -> list[str]:
    return [
        c.name
        for grid in ("main", "heldout")
        for c in mc.cells_for(grid)
        if c.schedule.name == schedule
    ]


def test_the_shipped_schedules_are_the_calibrated_ones() -> None:
    for sched in mc.SCHEDULES:
        expected = GATED if sched.name == "gpu_kernels" else "hac_lf"
        got = sequential.procedure_for_schedule(sched.looks, sched.max_rounds)
        assert got == expected, sched.name
    shipped = sequential._HAC_SCHEDULES  # pyright: ignore[reportPrivateUsage]
    assert shipped == {
        (s.looks.n0, s.looks.k, s.looks.max_looks, s.max_rounds)
        for s in mc.SCHEDULES
        if s.name != "gpu_kernels"
    }


def test_the_recorded_split_has_every_cell_and_each_one_passes() -> None:
    rows = _recorded()
    names = [n for s in mc.SCHEDULES for n in _cells(s.name)]
    assert len(names) == 1344 + 1680
    assert set(rows) == set(names)
    for name in names:
        row = rows[name]
        assert int(row["replications"]) == mc.REPLICATIONS
        column = "keep" if mc.cell_by_name(name).error == "KEEP" else "reject"
        assert int(row["errors"]) == int(row[column])
        assert mc.passes(int(row["errors"]), mc.REPLICATIONS), name
        assert row["passes"] == "1", name


@pytest.mark.calibration
@pytest.mark.parametrize("schedule", [s.name for s in mc.SCHEDULES])
def test_split_meets_the_acceptance_rule_in_every_cell(
    schedule: str,
) -> None:
    # Another seed, set in the environment, is a fresh audit of the same
    # rule: it must pass too, and no recorded count applies to it.
    seed = mc.calibration_seed() if mc.SEED_ENV in os.environ else SPLIT_SEED
    failing: list[str] = []
    for name in _cells(schedule):
        result = mc.run_cell(
            mc.cell_by_name(name), seed=seed, procedures=[mc.SPLIT]
        )
        found = cast("dict[str, Any]", result["procedures"])[mc.SPLIT]
        if not mc.passes(found["errors"], mc.REPLICATIONS):
            failing.append(f"{name}: {found['errors']}")
        if seed == SPLIT_SEED:
            row = _recorded()[name]
            assert found["keep"] == int(row["keep"]), name
            assert found["reject"] == int(row["reject"]), name
            assert found["inconclusive"] == int(row["inconclusive"]), name
            assert found["rounds_p95"] == int(row["rounds_p95"]), name
    assert not failing
