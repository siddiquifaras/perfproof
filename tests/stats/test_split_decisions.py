"""The decision rule of ``docs/method.md``, row by row, under the split.

Every default look schedule is run through the procedure
`procedure_for_schedule` gives it, on data built so that each row of
the rule's table is the only possible answer: a gain far outside the
noise, a flat result far inside the bar, a regression, a wide interval
that straddles the bar, a value at its resolution, an uncalibrated
procedure and a held KEEP. A procedure that withholds or mislabels a row
fails the row it breaks. These rows are far from the bar, so they do not
tell the two procedures apart (the compare-level tests in
tests/bench/test_compare.py pin which one a schedule gets); the rows at
the bar, to the last ulp, are in tests/stats/test_decision.py.
"""

from dataclasses import dataclass
from typing import Final

import numpy as np
import pytest
from numpy.typing import NDArray

from perfproof.schema import LookSchedule
from perfproof.stats.sequential import (
    Resolution,
    SequentialResult,
    procedure_for_schedule,
    run_looks,
)

FloatArray = NDArray[np.float64]
BAR: Final = 1.03


@dataclass(frozen=True)
class Case:
    name: str
    looks: LookSchedule
    max_rounds: int
    procedure: str


CASES: Final = (
    Case(
        "python_native", LookSchedule(n0=10, k=10, max_looks=6), 60, "hac_lf"
    ),
    Case("web_agentic", LookSchedule(n0=10, k=10, max_looks=3), 30, "hac_lf"),
    Case("llm_serving", LookSchedule(n0=10, k=6, max_looks=3), 22, "hac_lf"),
    Case(
        "gpu_kernels",
        LookSchedule(n0=20, k=20, max_looks=6),
        120,
        "t_log_ratio_batch_means",
    ),
)


@pytest.fixture(params=CASES, ids=lambda c: c.name)
def case(request: pytest.FixtureRequest) -> Case:
    return request.param


def rounds(
    case: Case, speedup: float, sd: float, seed: int
) -> tuple[FloatArray, FloatArray, NDArray[np.bool_]]:
    """Baseline and candidate times with balanced order, ``(1, n)``."""
    rng = np.random.default_rng(seed)
    n = case.max_rounds
    coin = rng.random(n // 2) < 0.5
    first = np.empty(n, dtype=np.bool_)
    first[0::2] = coin
    first[1::2] = ~coin
    noise = sd * rng.standard_normal(n)
    candidate = np.ones(n)
    baseline = candidate * speedup * np.exp(noise)
    return baseline[None, :], candidate[None, :], first[None, :]


def decide(
    case: Case,
    speedup: float,
    sd: float,
    *,
    calibrated: bool = True,
    keep_blocked: bool = False,
    resolution: Resolution | None = None,
    seed: int = 20261023,
) -> SequentialResult:
    b, c, first = rounds(case, speedup, sd, seed)
    return run_looks(
        b,
        c,
        direction="lower_is_better",
        schedule=case.looks,
        mode="one_host",
        min_effect=BAR - 1.0,
        calibrated=calibrated,
        resolution=resolution,
        exhausted=np.ones(1, dtype=np.bool_),
        procedure=procedure_for_schedule(case.looks, case.max_rounds),
        keep_blocked=keep_blocked,
        candidate_first=first,
    )


def test_the_case_table_matches_the_selection(case: Case) -> None:
    assert (
        procedure_for_schedule(case.looks, case.max_rounds) == case.procedure
    )


@pytest.mark.parametrize("speedup", [1.08, 1.04])
def test_row_interval_above_the_bar_is_rule_2_keep(
    case: Case, speedup: float
) -> None:
    got = decide(case, speedup, 0.002)
    assert got.procedure == case.procedure
    assert got.outcome[0] == "KEEP"
    assert int(got.final_rule[0]) == 2
    last = int(got.looks[0]) - 1
    assert got.lower[0, last] > BAR
    assert not got.regression[0].any()
    assert got.withheld[0, last] == ""


def test_row_interval_below_the_bar_is_rule_3_reject(case: Case) -> None:
    got = decide(case, 1.01, 0.002)
    assert got.outcome[0] == "REJECT"
    assert int(got.final_rule[0]) == 3
    last = int(got.looks[0]) - 1
    assert got.upper[0, last] <= BAR
    assert not got.regression[0].any()


def test_row_a_slower_candidate_is_a_flagged_rule_3_reject(
    case: Case,
) -> None:
    got = decide(case, 0.94, 0.002)
    assert got.outcome[0] == "REJECT"
    assert int(got.final_rule[0]) == 3
    last = int(got.looks[0]) - 1
    assert got.upper[0, last] < 1.0
    assert bool(got.regression[0, last])


def test_row_an_interval_straddling_the_bar_is_rule_5_inconclusive(
    case: Case,
) -> None:
    got = decide(case, 1.05, 0.15)
    assert got.outcome[0] == "INCONCLUSIVE"
    assert int(got.final_rule[0]) == 5
    assert int(got.looks[0]) == case.looks.max_looks
    last = int(got.looks[0]) - 1
    assert got.lower[0, last] <= BAR < got.upper[0, last]


def test_row_a_value_at_its_resolution_is_rule_1_inconclusive(
    case: Case,
) -> None:
    n = case.max_rounds
    coarse = Resolution(
        baseline_step=np.full((1, n), 5.0),
        candidate_step=np.full((1, n), 5.0),
        check_continuity=False,
    )
    got = decide(case, 1.08, 0.002, resolution=coarse)
    assert got.outcome[0] == "INCONCLUSIVE"
    assert int(got.final_rule[0]) == 1


def test_an_uncalibrated_procedure_never_keeps_or_rejects_on_speed(
    case: Case,
) -> None:
    for speedup in (1.08, 1.01, 0.94):
        got = decide(case, speedup, 0.002, calibrated=False)
        assert got.outcome[0] == "INCONCLUSIVE"
        assert int(got.final_rule[0]) == 5


def test_a_blocked_keep_is_inconclusive_not_a_keep(case: Case) -> None:
    got = decide(case, 1.08, 0.002, keep_blocked=True)
    assert got.outcome[0] == "INCONCLUSIVE"
    assert bool(got.held[0].any())


def test_hac_lf_records_the_lag1_numbers_and_the_gate_does_not(
    case: Case,
) -> None:
    got = decide(case, 1.08, 0.002)
    assert got.lag1 is not None
    assert got.rho_limit is not None
    shown = ~np.isnan(got.rho_limit)
    if case.procedure == "hac_lf":
        assert shown.any()
        assert np.all(got.rho_limit[shown] >= 0.3)
    else:
        assert not shown.any()
