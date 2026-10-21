"""Scenarios the T1.6 audit of PR #33 exposed, now in development.

``docs/method.md`` ("Audit scenarios") says a failed audit's scenarios
join the development scenarios and the next audit draws new ones. The
auditor's held-out runs on the batch-means interval of blocks of 6
rounds found two conditions, both inside the audit ranges, where it
fails the acceptance rule:

- AR(1) with rho 0.6 to 0.7 and little other noise, on the schedule
  with the most rounds (GPU kernels), and
- 4 to 5% outliers of 2.8 to 4.5 times with a run-to-run CV under 1%,
  on schedules with three blocks or fewer (LLM serving, 22 rounds).

The scenarios below are the auditor's, copied from its scenario files
with their exact parameters. ``A<draw seed>-<index>`` is a draw from
the audit ranges that failed (nine). ``RHO0.6`` and ``RHO0.7`` are
AR(1) at that rho with a CV of 5%: the development scenario ``ar1``
at the two values of rho where blocks of 6 failed. ``A00-*`` are
ablations of the first failing draw, ``A20261052-00``: its outlier
part alone, and outliers of 5% and 1% at 5 times with a CV of 1%. The
auditor's scenarios that were not failures, and its adversarial ones
outside the audit ranges (a level shift in one arm, sticky slow
modes, one-version skew), are not here.

One host, each runs as written. Cross host the simulator refuses drift
and the order effect, so a scenario that has them runs there without
them, with a per-session offset of `montecarlo.CROSS_HOST_OFFSET_CV`,
as the auditor ran them.
"""

from typing import Final

from perfproof.stats.simulate import NoiseScenario

EXPOSED: Final[dict[str, NoiseScenario]] = {
    "A20261052-00": NoiseScenario(
        cv=0.006643224078688728,
        t_df=6.812282053936064,
        outlier_rate=0.04925239898991419,
        outlier_factor=3.994343445524475,
        drift=0.18495494099048979,
        variance_step=1.256034134885914,
        variance_step_at=0.5239226485737208,
        ar1=0.00392794083534107,
        order_effect=0.04049485021274967,
    ),
    "A20261052-04": NoiseScenario(
        cv=0.1512015144071317,
        t_df=6.353901843686194,
        variance_step=2.74100968843051,
        variance_step_at=0.8842703852180627,
        ar1=0.6782983793510124,
    ),
    "A20261052-14": NoiseScenario(
        cv=0.11015699558291724,
        drift=0.12797003472386234,
        variance_step=1.2487553875932957,
        variance_step_at=0.4878748450552495,
        ar1=0.6091644997313546,
    ),
    "A20261053-04": NoiseScenario(
        cv=0.006951930118243849,
        t_df=6.247904226935675,
        outlier_rate=0.043733071423485204,
        outlier_factor=4.263104673900089,
        ar1=0.5983760960102645,
    ),
    "A20261054-08": NoiseScenario(
        cv=0.005380525596588283,
        outlier_rate=0.04287248808886973,
        outlier_factor=4.309869179497351,
        ar1=0.19691889691348233,
        order_effect=0.023972076348190593,
    ),
    "A20261054-13": NoiseScenario(
        cv=0.005223179249588948,
        drift=0.13897955973171974,
        ar1=0.6080567083443381,
        order_effect=0.024557701742076943,
    ),
    "A20261055-17": NoiseScenario(
        cv=0.005660923482802736,
        t_df=3.3758130764080536,
        outlier_rate=0.041905383412599584,
        outlier_factor=2.7685372608489898,
        variance_step=3.139805553389392,
        variance_step_at=0.571068298798805,
    ),
    "A20261056-02": NoiseScenario(
        cv=0.00535109349085587,
        variance_step=1.312638837240126,
        variance_step_at=0.5628446055321178,
        ar1=0.6173621614930589,
        order_effect=0.02811269292315462,
    ),
    "A20261056-15": NoiseScenario(
        cv=0.04666012153376578,
        variance_step=2.110342820522554,
        variance_step_at=0.42074588330516055,
        ar1=0.6657182692214588,
        order_effect=0.028213980084217712,
    ),
    "RHO0.6": NoiseScenario(cv=0.05, ar1=0.6),
    "RHO0.7": NoiseScenario(cv=0.05, ar1=0.7),
    "A00-outliers-only": NoiseScenario(
        cv=0.006643224078688728,
        outlier_rate=0.04925239898991419,
        outlier_factor=3.994343445524475,
    ),
    "A00-outliers-5pct-x5-cv1": NoiseScenario(
        cv=0.01, outlier_rate=0.05, outlier_factor=5.0
    ),
    "A00-outliers-1pct-x5-cv1": NoiseScenario(
        cv=0.01, outlier_rate=0.01, outlier_factor=5.0
    ),
}
