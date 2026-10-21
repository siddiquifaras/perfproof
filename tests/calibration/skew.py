"""One-version skew probes for T1.11, outside the audit ranges.

``docs/method.md`` ("Assumptions") states that whether the primary
covers when one version is occasionally much slower is unknown, and
``NoiseScenario`` cannot express it: its outliers hit both arms alike,
so ``d = log(b / c)`` stays symmetric. A robust centre (Huber, a
trimmed mean) targets the centre of ``d``'s distribution, which is
``E[d]``, the estimand ``S = exp(E[d])``, only while ``d`` is
symmetric. These scenarios break the symmetry on purpose, to show what
each challenger does when it is not there. They are probes, not part of
any acceptance count.

Each adds ``log(factor)`` to one arm's log time in a fraction ``rate``
of the rounds, after the base scenario is drawn, and the cell's true
speedup is defined by the mean, ``S = exp(E[d])``: the speedup passed
to the simulator is raised (candidate skew) or lowered (baseline skew)
by ``factor ** rate`` so that ``E[d]`` is ``log(S)`` of the cell.
"""

import math
from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from perfproof.stats.simulate import NoiseScenario


@dataclass(frozen=True, slots=True)
class Skew:
    """An asymmetric slow mode in one arm.

    Attributes
    ----------
    base : perfproof.stats.simulate.NoiseScenario
        The symmetric noise underneath.
    arm : int
        0 for the baseline, 1 for the candidate.
    rate : float
        Probability that a round's run of that arm is slow.
    factor : float
        How many times as long a slow run takes.
    """

    base: NoiseScenario
    arm: int
    rate: float
    factor: float

    @property
    def mean_shift(self) -> float:
        """Change of ``E[d]`` the slow mode causes."""
        sign = -1.0 if self.arm == 1 else 1.0
        return sign * self.rate * math.log(self.factor)

    def simulator_speedup(self, speedup: float) -> float:
        """Speedup to simulate so that ``exp(E[d])`` is ``speedup``."""
        return speedup * math.exp(-self.mean_shift)

    def apply(
        self, log_times: NDArray[np.float64], rng: np.random.Generator
    ) -> None:
        """Add the slow mode to ``log_times`` ``(R, n, 2)`` in place."""
        slow = rng.random(log_times.shape[:2]) < self.rate
        log_times[..., self.arm] += math.log(self.factor) * slow


SKEW: Final[dict[str, Skew]] = {
    "SKEW-cand-5pct-x2": Skew(NoiseScenario(cv=0.05), 1, 0.05, 2.0),
    "SKEW-base-5pct-x2": Skew(NoiseScenario(cv=0.05), 0, 0.05, 2.0),
    "SKEW-cand-10pct-x1.5": Skew(NoiseScenario(cv=0.05), 1, 0.10, 1.5),
}
