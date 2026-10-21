"""The challenger procedures of T1.11, as interval functions.

Each `Challenger` turns the rounds of some replications into bounds on
the speedup ``S = exp(E[d])`` at a look: ``baseline`` and ``candidate``
times shaped ``(R, n)``, the order flags, the host mode and the
per-look confidence the schedule gives (``1 - alpha / (2 K)``). The
sequential looks, the Bonferroni split, the decision rule and the
stopping are the harness's (`montecarlo.drive_looks`), unchanged: only
the interval differs. A look with fewer than `inference.MIN_N`
observations has no interval, as in the primary.

The arithmetic is in `inference` and the critical values in `fixedb`.
"""

from dataclasses import dataclass
from typing import Final

import numpy as np
from fixedb import SPEC_BY_NAME, critical, lf_critical
from inference import (
    MIN_N,
    BoolArray,
    FloatArray,
    Spec,
    adjusted_fit,
    batch_means_auto,
    estimate,
    lag1,
    studentised_se,
)
from numpy.typing import NDArray

Bounds = tuple[FloatArray, FloatArray]


@dataclass(frozen=True, slots=True)
class Challenger:
    """One challenger procedure.

    Attributes
    ----------
    name : str
        Procedure name in cell results.
    spec : inference.Spec or None
        The studentised procedure (HAC, stationary bootstrap, or their
        least favourable versions); ``None`` for batch means.
    scale : float
        Batch-length scale of `inference.auto_batch_length`, batch
        means only.
    estimator : {"mean", "huber"}
        Batch means only: the centre.
    widen : float
        Factor on ``alpha / (2 K)`` for the least favourable critical
        value: 1 is the per-look level of the schedule.
    guard : bool
        Whether a KEEP or performance REJECT is withheld, named
        ``imbalanced_conditions``, when the recorded conditions are far
        from balanced between the arms (`IMBALANCE_T`) and the
        covariate does not show a link to ``d`` that would correct it
        (`LINK_T`); see `withheld`.
    """

    name: str
    spec: Spec | None = None
    scale: float = 1.0
    estimator: str = "mean"
    widen: float = 1.0
    guard: bool = False

    def bounds(
        self,
        baseline: FloatArray,
        candidate: FloatArray,
        first: BoolArray,
        cov: FloatArray,
        mode: str,
        confidence: float,
    ) -> Bounds:
        """Bounds on ``S`` at the look the arrays end at.

        NaN where fewer than `MIN_N` observations make no interval.
        """
        rows, n = baseline.shape
        if n < MIN_N:
            return np.full(rows, np.nan), np.full(rows, np.nan)
        d = np.log(baseline / candidate)
        if self.spec is None:
            lo, hi = batch_means_auto(
                d,
                first,
                confidence=confidence,
                scale=self.scale,
                estimator="huber" if self.estimator == "huber" else "mean",
            )
            return np.exp(lo), np.exp(hi)
        spec = self.spec
        if spec.estimator in ("adjusted", "huber_adjusted"):
            fit = adjusted_fit(
                d,
                first,
                cov,
                spec.b,
                robust=spec.estimator == "huber_adjusted",
            )
            theta, se = fit.theta, fit.se
        else:
            theta, u = estimate(d, first, spec.estimator)
            se = studentised_se(u, spec)
        if spec.lf:
            level = 1.0 - (1.0 - confidence) * self.widen
            crit = lf_critical(spec.name, mode, n, level, lag1(d, first))
        else:
            crit = np.float64(critical(spec.name, mode, n, confidence))
        half = crit * se
        return np.exp(theta - half), np.exp(theta + half)

    def withheld(
        self,
        baseline: FloatArray,
        candidate: FloatArray,
        first: BoolArray,
        cov: FloatArray,
        b_ratio: float = 0.10,
    ) -> NDArray[np.bool_]:
        """Rows whose KEEP or performance REJECT the guard withholds.

        True where the covariate's imbalance between the arms has
        ``|t|`` above `IMBALANCE_T` and its coefficient on ``d`` has
        ``|t|`` below `LINK_T`: the conditions are lopsided and the
        data do not show that the recorded covariate explains ``d``, so
        no adjustment can be trusted to remove what they did.
        """
        rows, n = baseline.shape
        if not self.guard or n < MIN_N:
            return np.zeros(rows, dtype=np.bool_)
        d = np.log(baseline / candidate)
        fit = adjusted_fit(d, first, cov, b_ratio)
        with np.errstate(invalid="ignore", divide="ignore"):
            link = np.where(fit.slope_se > 0, fit.slope / fit.slope_se, 0.0)
        return (np.abs(fit.imbalance) > IMBALANCE_T) & (np.abs(link) < LINK_T)


IMBALANCE_T: Final = 3.0
"""``|t|`` of the covariate's mean difference above which the recorded
conditions count as lopsided between the arms (two-sided 0.3% under
balance)."""
LINK_T: Final = 3.0
"""``|t|`` of the covariate's coefficient below which the data do not
show that the covariate explains the log ratio."""


def _spec(name: str, widen: float = 1.0, *, guard: bool = False) -> Challenger:
    label = name if widen == 1.0 else f"{name}_x{widen:g}"
    if guard:
        label += "_guard"
    return Challenger(label, spec=SPEC_BY_NAME[name], widen=widen, guard=guard)


CHALLENGER_LIST: Final = (
    _spec("hac_b10"),
    _spec("hac_b20"),
    _spec("hac_b30"),
    _spec("sb_pw"),
    _spec("huber_hac_b20"),
    _spec("huber_hac_b30"),
    _spec("lf_iid"),
    _spec("lf_hac_b10"),
    _spec("lf_hac_b20"),
    _spec("lf_huber_iid"),
    _spec("lf_huber_hac_b10"),
    _spec("lf_hac_b10", 2.0),
    _spec("lf_huber_hac_b10", 2.0),
    _spec("lf_adj_hac_b10"),
    _spec("lf_adj_hac_b10", guard=True),
    Challenger("bm_auto_c1", scale=1.0),
    Challenger("bm_auto_c2", scale=2.0),
    Challenger("huber_bm_auto_c2", scale=2.0, estimator="huber"),
)
WAVE_2: Final = (_spec("lf_huber_adj_hac_b10"),)
"""Added after the first wave of runs, which fixed ``montecarlo``'s
``all`` procedure list; run by name."""
CHALLENGERS: Final = {c.name: c for c in (*CHALLENGER_LIST, *WAVE_2)}
