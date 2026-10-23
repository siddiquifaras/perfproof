"""Does the committed ``hac_lf`` table cover AR(1) noise off its grid?

The table is built on a grid of ``rho`` and of look lengths
(`perfproof.stats.lf_tables`). Here fresh draws with an independent
seed, at ``rho`` between the grid points and at look lengths between
the tabulated ones, check that for the limit a look would use, the
unconditional share of intervals that miss the true mean is within
Monte Carlo error of the budget ``1 - confidence`` or below it. The
tolerance is 20% of the budget on top of the budget (about three
standard errors at this ``R``).
"""

import math

import numpy as np
import pytest
from fixedb import ar1_normal, order_pattern
from inference import Spec, lag1, pivot

from perfproof.stats import lf_tables
from perfproof.stats.intervals import lf_critical
from perfproof.stats.sequential import look_confidence

pytestmark = pytest.mark.calibration

SPEC = Spec("hac_lf", "mean", "bartlett", lf_tables.HAC_B)
DRAWS = 400_000
CHUNK = 100_000


def test_the_table_records_its_provenance() -> None:
    assert lf_tables.SEED == 7974144670174977917
    assert lf_tables.DRAWS == 4_000_000
    assert lf_tables.HAC_B == 0.1


@pytest.mark.parametrize("mode", ["one_host", "cross_host"])
@pytest.mark.parametrize(
    ("n", "rho", "limit"),
    [
        (22, 0.12, 0.3),
        (46, 0.28, 0.3),
        (100, 0.0, 0.3),
        (24, 0.38, 0.4),
        (46, 0.55, 0.6),
        (90, 0.22, 0.3),
    ],
)
def test_unconditional_miss_is_within_budget_off_the_grid(
    mode: str, n: int, rho: float, limit: float
) -> None:
    k = 6
    conf = look_confidence(0.05, k)
    budget = 1.0 - conf
    rng = np.random.default_rng(
        [n, int(rho * 1000), 20261022, 1 if mode == "one_host" else 2]
    )
    misses = 0
    done = 0
    while done < DRAWS:
        rows = min(CHUNK, DRAWS - done)
        x = ar1_normal(rng, rows, n, rho)
        first = order_pattern(rng, rows, n, mode)
        t = np.abs(pivot(x, first, SPEC))
        crit = lf_critical(
            mode=mode,
            n=n,
            confidence=conf,
            lag1_up=lag1(x, first),
            rho_limit=np.full(rows, limit),
        )
        misses += int(np.count_nonzero(t > crit))
        done += rows
    rate = misses / DRAWS
    se = math.sqrt(budget * (1 - budget) / DRAWS)
    assert rate <= budget + max(0.2 * budget, 3 * se), (rate, budget)
