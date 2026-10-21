"""Busy, warm, throttling machines: shared and arm-correlated noise.

Real engineers measure on a service they cannot stop or a laptop that
heats up. The `NoiseScenario` effects do not cover that: throttling
episodes, thermal drift and background load act on whichever execution
is running at that moment. These scenarios add them to a base scenario,
one host only (a fresh host inherits none of a session's state), after
the base noise is drawn, on the execution positions the simulator's
drift uses: the two runs of round ``i`` are executions ``2 i`` and
``2 i + 1`` of the session, in the round's order.

Two families, and a recorded covariate for each execution.

- **Shared**: a throttle episode (speed 0.7 to 0.85 over 5 to 40% of the
  session, one to three of them), a saturating thermal rise and short
  background-load bursts of one to three executions. The field is the
  same for both arms, so the pair's log ratio sees only its difference
  between neighbouring executions: it cancels, except at an episode's
  edges and where a burst lands on one run of a round (at random on
  either arm, so symmetric). The true speedup is the cell's speedup.
- **Arm-correlated**: episodes that slow one arm's runs only, as an
  aliasing background job does. With ``p_baseline = 0.5`` each episode
  falls on a random arm (bad luck, unbiased); at 0.8 or 0.2 it falls
  mostly on one arm, which biases ``d`` by a few percent. The true
  speedup is again the cell's, the clean one: the interference is a
  nuisance, so a procedure that follows it can make a false KEEP (the
  baseline is the slowed arm) or a false REJECT (the candidate is).

The recorded covariate of an execution is its true total log slowdown in
units of 0.1 plus Gaussian noise of ``covariate_noise``; ``None`` makes
it pure noise, a covariate that tells nothing. Scenarios without
conditions get that uninformative covariate too, so a procedure that
adjusts for it is exercised on every cell. A procedure sees the
covariate as ``x``, the candidate's recorded condition minus the
baseline's in each round.
"""

import math
from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from perfproof.stats.simulate import NoiseScenario

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
SIGNAL_UNIT: Final = 0.1
"""Log slowdown that is one unit of the recorded covariate."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Conditions:
    """Time-structured conditions on top of a base scenario.

    Attributes
    ----------
    base : perfproof.stats.simulate.NoiseScenario
        Noise underneath; the simulator draws it first.
    throttle_episodes : tuple of int
        Fewest and most shared throttle episodes per replication.
    throttle_speed, throttle_length : tuple of float
        Ranges of an episode's speed (as a fraction) and length (as a
        fraction of the session's executions).
    thermal : float
        Total log slowdown of the saturating thermal rise.
    burst_rate : float
        Probability that a burst starts at an execution.
    burst_size : tuple of float
        Range of a burst's log slowdown (log-uniform).
    arm_episodes : tuple of int
        Fewest and most arm-specific episodes.
    arm_length, arm_slowdown : tuple of float
        Ranges of an arm episode's length (fraction of executions) and
        log slowdown.
    p_baseline : float
        Probability that an arm episode falls on the baseline.
    covariate_noise : float or None
        Noise sd of the recorded covariate in units of its signal;
        ``None`` for pure noise.
    """

    base: NoiseScenario
    throttle_episodes: tuple[int, int] = (0, 0)
    throttle_speed: tuple[float, float] = (0.70, 0.85)
    throttle_length: tuple[float, float] = (0.05, 0.40)
    thermal: float = 0.0
    burst_rate: float = 0.0
    burst_size: tuple[float, float] = (0.1, 0.5)
    arm_episodes: tuple[int, int] = (0, 0)
    arm_length: tuple[float, float] = (0.05, 0.25)
    arm_slowdown: tuple[float, float] = (0.10, 0.30)
    p_baseline: float = 0.5
    covariate_noise: float | None = 0.3

    def apply(
        self,
        log_times: FloatArray,
        first: NDArray[np.bool_],
        rng: np.random.Generator,
    ) -> FloatArray:
        """Add the conditions to ``log_times`` in place.

        Parameters
        ----------
        log_times : numpy.ndarray, shape (R, n, 2)
            Simulated log times (baseline, candidate), modified.
        first : numpy.ndarray of bool, shape (R, n)
            True where the candidate ran first in its round.
        rng : numpy.random.Generator
            Source of the conditions and of the covariate's noise.

        Returns
        -------
        numpy.ndarray, shape (R, n)
            The covariate difference ``x``: candidate's minus
            baseline's recorded condition in each round.
        """
        rows, n, _ = log_times.shape
        total = 2 * n
        slot = np.arange(total, dtype=np.int64).reshape(1, total)
        shared = np.zeros((rows, total))
        self._throttle(shared, slot, rng)
        shared += self.thermal * (1.0 - np.exp(-slot / (0.3 * total)))
        shared += self._bursts(rows, total, rng)
        own = np.zeros((rows, total, 2))
        self._arm_episodes(own, slot, rng)
        second = np.stack((first, ~first), axis=-1)
        position = 2 * np.arange(n, dtype=np.int64).reshape(
            1, n, 1
        ) + second.astype(np.int64)
        recorded: list[FloatArray] = []
        for arm in (0, 1):
            where = position[:, :, arm]
            slowdown = np.take_along_axis(shared, where, axis=1)
            slowdown += np.take_along_axis(own[:, :, arm], where, axis=1)
            log_times[:, :, arm] += slowdown
            if self.covariate_noise is None:
                signal = np.zeros_like(slowdown)
                noise = 1.0
            else:
                signal = slowdown / SIGNAL_UNIT
                noise = self.covariate_noise
            recorded.append(signal + noise * rng.standard_normal(signal.shape))
        return recorded[1] - recorded[0]

    def _throttle(
        self, field: FloatArray, slot: IntArray, rng: np.random.Generator
    ) -> None:
        rows, total = field.shape
        low, high = self.throttle_episodes
        count = rng.integers(low, high + 1, size=rows)
        for j in range(high):
            length = rng.uniform(*self.throttle_length, rows) * total
            start = rng.uniform(0.0, 1.0, rows) * (total - length)
            speed = rng.uniform(*self.throttle_speed, rows)
            inside = (slot >= start[:, None]) & (
                slot < (start + length)[:, None]
            )
            field += (count > j)[:, None] * inside * (-np.log(speed))[:, None]

    def _bursts(
        self, rows: int, total: int, rng: np.random.Generator
    ) -> FloatArray:
        out = np.zeros((rows, total))
        if self.burst_rate <= 0:
            return out
        begin = rng.random((rows, total)) < self.burst_rate
        low, high = self.burst_size
        size = np.exp(
            rng.uniform(math.log(low), math.log(high), (rows, total))
        )
        duration = rng.integers(1, 4, (rows, total))
        for shift in range(3):
            hit = np.where(begin & (duration > shift), size, 0.0)
            out[:, shift:] += hit[:, : total - shift]
        return out

    def _arm_episodes(
        self, own: FloatArray, slot: IntArray, rng: np.random.Generator
    ) -> None:
        rows, total, _ = own.shape
        low, high = self.arm_episodes
        count = rng.integers(low, high + 1, size=rows)
        for j in range(high):
            length = rng.uniform(*self.arm_length, rows) * total
            start = rng.uniform(0.0, 1.0, rows) * (total - length)
            slow = rng.uniform(*self.arm_slowdown, rows)
            draw = rng.random(size=(rows,))
            on_baseline: NDArray[np.bool_] = draw < self.p_baseline
            inside = (slot >= start[:, None]) & (
                slot < (start + length)[:, None]
            )
            hit = (count > j)[:, None] * inside * slow[:, None]
            own[:, :, 0] += hit * on_baseline[:, None]
            own[:, :, 1] += hit * (~on_baseline)[:, None]


_CV5: Final = NoiseScenario(cv=0.05)
_CV2: Final = NoiseScenario(cv=0.02)
COND: Final[dict[str, Conditions]] = {
    "COND-shared-throttle1": Conditions(base=_CV5, throttle_episodes=(1, 1)),
    "COND-shared-throttle3": Conditions(base=_CV5, throttle_episodes=(1, 3)),
    "COND-shared-thermal-bursts": Conditions(
        base=_CV5, thermal=0.08, burst_rate=0.04
    ),
    "COND-shared-mixed-lowcv": Conditions(
        base=NoiseScenario(cv=0.01),
        throttle_episodes=(1, 3),
        thermal=0.05,
        burst_rate=0.03,
    ),
    "COND-armluck": Conditions(base=_CV2, arm_episodes=(2, 4), p_baseline=0.5),
    "COND-armbias-base": Conditions(
        base=_CV2, arm_episodes=(2, 4), p_baseline=0.8
    ),
    "COND-armbias-cand": Conditions(
        base=_CV2, arm_episodes=(2, 4), p_baseline=0.2
    ),
    "COND-armbias-base-poorcov": Conditions(
        base=_CV2,
        arm_episodes=(2, 4),
        p_baseline=0.8,
        covariate_noise=1.5,
    ),
    "COND-armbias-base-nocov": Conditions(
        base=_CV2,
        arm_episodes=(2, 4),
        p_baseline=0.8,
        covariate_noise=None,
    ),
}
