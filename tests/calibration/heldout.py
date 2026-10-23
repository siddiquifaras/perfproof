"""Held-out scenarios for T1.11, drawn from the audit ranges.

``docs/method.md`` ("Audit scenarios") gives the ranges the audit draws
new scenarios from, combining several violations in one scenario. This
module draws `COUNT` of them from `SEED`, a seed drawn before any
challenger ran, so that no candidate of T1.11 was built against them:

=============================  ==================================
Parameter                      Draw
=============================  ==================================
CV                             log-uniform on 0.5% to 20%
Student-t degrees of freedom   uniform on 2.5 to 10, in half
Outlier rate and size          uniform on 0 to 5% and 2x to 5x,
                               in half
Drift across the session       uniform on 0 to 20%, in half
Variance step                  uniform on 1x to 4x, at a uniform
                               point in 0.1 to 0.9, in half
AR(1) rho                      uniform on 0 to 0.7, in half
Order effect                   uniform on 0 to 5%, in half
=============================  ==================================

"In half" means the effect is present with probability 1/2 and absent
otherwise (a scenario with every effect absent is plain log-normal
noise). They run as the exposed scenarios do: one host as drawn, cross
host without drift and the order effect (the simulator refuses them)
and with the per-session offset of the harness.

The draw is deterministic, ``write_csv`` records it in
``results/heldout_scenarios.csv``, and a test holds the two equal.
Once a candidate is judged against these scenarios they are exposed:
the next audit draws new ones.
"""

import csv
import math
from dataclasses import fields
from pathlib import Path
from typing import Final

import numpy as np

from perfproof.stats.simulate import NoiseScenario

SEED: Final = 4371735374463635800
COUNT: Final = 30
CSV_PATH: Final = Path(__file__).parent / "results" / "heldout_scenarios.csv"
NAME_KEY: Final = SEED % 10**7


def draw(seed: int, count: int) -> dict[str, NoiseScenario]:
    """Draw ``count`` scenarios from the audit ranges (``H<k>-<i>``).

    Parameters
    ----------
    seed : int
        Seed of the generator.
    count : int
        How many scenarios.
    """
    rng = np.random.default_rng(seed)
    found: dict[str, NoiseScenario] = {}
    for i in range(count):
        cv = math.exp(rng.uniform(math.log(0.005), math.log(0.2)))
        t_df = float(rng.uniform(2.5, 10.0)) if rng.random() < 0.5 else None
        has_outliers = rng.random() < 0.5
        rate = float(rng.uniform(0.0, 0.05)) if has_outliers else 0.0
        factor = float(rng.uniform(2.0, 5.0)) if has_outliers else 1.0
        drift = float(rng.uniform(0.0, 0.2)) if rng.random() < 0.5 else 0.0
        has_step = rng.random() < 0.5
        step = float(rng.uniform(1.0, 4.0)) if has_step else 1.0
        at = float(rng.uniform(0.1, 0.9)) if has_step else 0.5
        ar1 = float(rng.uniform(0.0, 0.7)) if rng.random() < 0.5 else 0.0
        order = float(rng.uniform(0.0, 0.05)) if rng.random() < 0.5 else 0.0
        found[f"H{NAME_KEY}-{i:02d}"] = NoiseScenario(
            cv=cv,
            t_df=t_df,
            outlier_rate=rate,
            outlier_factor=factor,
            drift=drift,
            variance_step=step,
            variance_step_at=at,
            ar1=ar1,
            order_effect=order,
        )
    return found


HELDOUT: Final = draw(SEED, COUNT)


def write_csv(path: Path = CSV_PATH) -> None:
    """Write every scenario's parameters, one row each."""
    names = [f.name for f in fields(NoiseScenario)]
    with path.open("w", newline="") as sink:
        writer = csv.writer(sink, lineterminator="\n")
        writer.writerow(["scenario", "seed", *names])
        for name, s in HELDOUT.items():
            writer.writerow(
                [name, SEED, *(repr(getattr(s, n)) for n in names)]
            )
