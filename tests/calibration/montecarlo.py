"""Monte Carlo calibration of the sequential procedure.

"Calibration" in ``docs/method.md`` fixes what is run and how a cell
passes; this module runs it. A cell is one look schedule (a target
class's defaults), one development scenario, one host mode and one
true speedup. Each of its ``R`` replications is one complete
comparison: rounds drawn by `perfproof.stats.simulate.simulate` with
balanced order on one host, the round and time budget of the schedule,
the looks `perfproof.stats.sequential.plan_looks` places, and every
look decided by `perfproof.stats.decision.interval_rules`.

Two procedures run in every cell: the t primary of ``docs/method.md``,
through `perfproof.stats.sequential.run_looks`, and batch means in
blocks of `BATCH_BLOCK` rounds, the fallback with the block size that
the page's N1 decision fixes. The other challengers (Hodges-Lehmann,
the bootstrap of the ratio of medians with and without BCa, and blocks
of 4, 5 and 10 rounds) lost everywhere, were deleted after the recorded
runs, and are in the history at commit 8406d80 with their counts in
``tests/calibration/results/``.

Choices the method leaves open, fixed here before the run:

- Scenarios other than the log-normal rows take a run-to-run CV of
  `BASE_CV`, as T1.5's measurements did.
- Cross host, every scenario gets a per-session offset of
  `CROSS_HOST_OFFSET_CV`, the 15% of the method's offset row. Drift and
  the order effect have no cross-host cell: the simulator refuses them,
  because a fresh host inherits no heat or cache from the last one
  (``docs/method.md``, "Comparisons across machines").
- The time budget: one execution takes ``t0 exp(log time)`` seconds,
  with ``t0 = max_seconds / (2 (max_rounds + 1))``, so the budget holds
  ``max_rounds`` typical rounds plus the one round of headroom the
  runner's look-ahead needs. The budget is exhausted after round ``n``
  when the elapsed time plus the longest round so far exceeds
  ``max_seconds``, as `perfproof.bench.compare` decides it, or at
  ``max_rounds``. Times are accumulated in log space
  (``np.logaddexp.accumulate``) because heavy tails put single runs
  far beyond the float64 range of their sum's terms.
- Every procedure in a cell sees the same simulated rounds.

Randomness comes from one seed, ``PERFPROOF_CALIBRATION_SEED`` when set
and `DEFAULT_SEED` otherwise. Each cell draws from its own
`numpy.random.SeedSequence` keyed by the CRC-32 of its name, so a cell
gives the same counts whichever other cells run with it.

Replications are processed in chunks of at most `CHUNK_BYTES` of
simulated log times, a size that depends only on the schedule, so the
draws, and with them the counts, do not depend on the machine.
"""

import math
import os
import time
import zlib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from typing import Final, Literal

import numpy as np
from numpy.typing import NDArray
from scipy import stats  # pyright: ignore[reportMissingTypeStubs]

from perfproof.schema import LookSchedule
from perfproof.stats.decision import interval_rules
from perfproof.stats.intervals import t_interval
from perfproof.stats.sequential import (
    DEFAULT_ALPHA,
    LookPlan,
    look_confidence,
    plan_looks,
    run_looks,
)
from perfproof.stats.simulate import (
    HostMode,
    NoiseScenario,
    development_scenarios,
    simulate,
)

FloatArray = NDArray[np.float64]

SEED_ENV: Final = "PERFPROOF_CALIBRATION_SEED"
DEFAULT_SEED: Final = 844029934949031648
"""Seed of the calibration of record, drawn before it ran. The run at
seed 1652377851162723444 chose the batch-means block size; this one
confirms it, whole, as ``docs/method.md`` requires after a change."""
SPEEDUPS: Final = (0.95, 1.00, 1.03, 1.04, 1.05, 1.10, 1.20)
MIN_EFFECT: Final = 0.03
BAR: Final = 1.0 + MIN_EFFECT
LIMIT: Final = 0.025
"""Largest error rate the acceptance rule allows."""
REPLICATIONS: Final = 4000
BASE_CV: Final = 0.05
CROSS_HOST_OFFSET_CV: Final = 0.15
CHUNK_BYTES: Final = 32 * 2**20
"""Most bytes of simulated log times held per chunk of replications."""
MODES: Final[tuple[HostMode, ...]] = ("one_host", "cross_host")

Outcome = Literal["KEEP", "REJECT", "INCONCLUSIVE"]
_ACTION_BY_RULE: Final = np.array(
    ["", "INCONCLUSIVE", "KEEP", "REJECT", "CONTINUE", "INCONCLUSIVE"]
)


@dataclass(frozen=True, slots=True)
class TargetSchedule:
    """A target class's default look schedule and budget.

    Attributes
    ----------
    name : str
        Short name used in cell names.
    classes : tuple of str
        The target classes of ``docs/method.md`` that use it.
    looks : perfproof.schema.LookSchedule
        ``n0``, ``k`` and ``K``.
    max_rounds : int
        Round budget.
    max_seconds : float
        Time budget, in seconds.
    """

    name: str
    classes: tuple[str, ...]
    looks: LookSchedule
    max_rounds: int
    max_seconds: float


SCHEDULES: Final = (
    TargetSchedule(
        "python_native",
        ("Python, CPU-bound", "Native"),
        LookSchedule(n0=10, k=10, max_looks=6),
        60,
        1800.0,
    ),
    TargetSchedule(
        "web_agentic",
        ("Web services", "Agentic apps"),
        LookSchedule(n0=10, k=10, max_looks=3),
        30,
        3600.0,
    ),
    TargetSchedule(
        "llm_serving",
        ("LLM serving",),
        LookSchedule(n0=10, k=6, max_looks=3),
        22,
        3600.0,
    ),
    TargetSchedule(
        "gpu_kernels",
        ("GPU kernels",),
        LookSchedule(n0=20, k=20, max_looks=6),
        120,
        1800.0,
    ),
)
SCHEDULE_BY_NAME: Final = {s.name: s for s in SCHEDULES}

BATCH_BLOCK: Final = 6
"""Rounds per batch-means block: three order pairs (N1)."""
PRIMARY: Final = "t"
FALLBACK: Final = f"batch_means_{BATCH_BLOCK}"
PROCEDURES: Final = (PRIMARY, FALLBACK)


def scenarios(mode: HostMode) -> dict[str, NoiseScenario]:
    """Return the development scenarios run in ``mode``.

    One host, every development scenario at `BASE_CV`. Cross host,
    those the simulator allows there, each with a per-session offset
    of `CROSS_HOST_OFFSET_CV`.
    """
    found = development_scenarios(BASE_CV)
    if mode == "one_host":
        return found
    return {
        name: replace(s, session_offset_cv=CROSS_HOST_OFFSET_CV)
        for name, s in found.items()
        if not s.one_host_only
    }


@dataclass(frozen=True, slots=True)
class Cell:
    """One calibration cell.

    Attributes
    ----------
    schedule : TargetSchedule
        Look schedule and budget.
    scenario : str
        Development scenario name.
    mode : {"one_host", "cross_host"}
        Host mode.
    speedup : float
        True geometric-mean speedup ``S``.
    """

    schedule: TargetSchedule
    scenario: str
    mode: HostMode
    speedup: float

    @property
    def name(self) -> str:
        """Stable name, also the key of the cell's seed."""
        return (
            f"{self.schedule.name}/{self.scenario}/{self.mode}/"
            f"{self.speedup:.2f}"
        )

    @property
    def error(self) -> Outcome:
        """The outcome that is an error for this cell's true speedup."""
        return "KEEP" if self.speedup <= BAR else "REJECT"


def all_cells() -> list[Cell]:
    """Every cell, in a fixed order."""
    return [
        Cell(schedule, name, mode, speedup)
        for schedule in SCHEDULES
        for mode in MODES
        for name in scenarios(mode)
        for speedup in SPEEDUPS
    ]


def cell_by_name(name: str) -> Cell:
    """Return the cell with ``name``, as `Cell.name` spells it."""
    schedule, scenario, mode, speedup = name.split("/")
    if mode not in MODES:
        msg = f"unknown mode {mode!r} in cell {name!r}"
        raise ValueError(msg)
    host: HostMode = "one_host" if mode == "one_host" else "cross_host"
    cell = Cell(SCHEDULE_BY_NAME[schedule], scenario, host, float(speedup))
    if cell.name != name or scenario not in scenarios(host):
        msg = f"no such cell {name!r}"
        raise ValueError(msg)
    return cell


def calibration_seed() -> int:
    """Return ``PERFPROOF_CALIBRATION_SEED``, else the default seed."""
    given = os.environ.get(SEED_ENV)
    if given is None or not given.strip():
        return DEFAULT_SEED
    value = int(given)
    if value < 0:
        msg = f"{SEED_ENV} must be a non-negative integer, got {given!r}"
        raise ValueError(msg)
    return value


def clopper_pearson_upper(count: int, total: int) -> float:
    """One-sided 95% Clopper-Pearson upper bound on a binomial rate.

    The ``0.95`` quantile of ``Beta(count + 1, total - count)``, and 1
    when every trial counted.
    """
    if not 0 <= count <= total or total < 1:
        msg = f"need 0 <= count <= total and total >= 1, got {count}/{total}"
        raise ValueError(msg)
    if count == total:
        return 1.0
    bound = np.asarray(
        stats.beta.ppf(0.95, count + 1, total - count),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        dtype=np.float64,
    )
    return float(bound)


def passes(count: int, total: int) -> bool:
    """Whether a cell passes: its upper bound is at most `LIMIT`."""
    return clopper_pearson_upper(count, total) <= LIMIT


def collected_rounds(
    log_times: FloatArray, schedule: TargetSchedule
) -> NDArray[np.int64]:
    """Rounds each replication collects before its budget runs out.

    See the module docstring for the time model. ``log_times`` is
    shaped ``(R, max_rounds, 2)``. Returns the rounds, shape ``(R,)``,
    each in ``[1, max_rounds]``; the budget is exhausted after them in
    every replication.
    """
    m = schedule.max_rounds
    if log_times.shape[1] != m:
        msg = f"need {m} rounds per replication, got {log_times.shape[1]}"
        raise ValueError(msg)
    log_t0 = math.log(schedule.max_seconds / (2 * (m + 1)))
    log_round = log_t0 + np.logaddexp(log_times[..., 0], log_times[..., 1])
    elapsed = np.logaddexp.accumulate(log_round, axis=1)
    longest = np.maximum.accumulate(log_round, axis=1)
    over = np.logaddexp(elapsed, longest) > math.log(schedule.max_seconds)
    first = np.argmax(over, axis=1) + 1
    return np.where(over[:, -1], first, m).astype(np.int64)


IntervalFn = Callable[[NDArray[np.intp], int], tuple[FloatArray, FloatArray]]
"""Bounds for the given rows at ``n`` observations; NaN where there is
no interval."""


@dataclass(frozen=True, slots=True)
class Ended:
    """How each replication of one procedure ended.

    Attributes
    ----------
    outcome : numpy.ndarray of str, shape (R,)
        KEEP, REJECT or INCONCLUSIVE.
    rounds : numpy.ndarray of int64, shape (R,)
        Rounds at the last look taken; 0 with no look.
    """

    outcome: NDArray[np.str_]
    rounds: NDArray[np.int64]


def drive_looks(
    plan: LookPlan, interval_at: IntervalFn, *, n0: int | None
) -> Ended:
    """Run the looks of ``plan`` for a procedure given as bounds.

    The same loop as `perfproof.stats.sequential.run_looks` with
    ``resolution=None`` (simulated values are exact): each look's
    bounds go to `perfproof.stats.decision.interval_rules`, with budget
    remaining except at the plan's final look, and a replication stops
    at its first look whose action is not continue. A look with no
    interval (NaN bounds: too few blocks) neither keeps nor rejects: it
    continues, and is INCONCLUSIVE at its last look. ``n0`` is given
    cross host only.
    """
    rows_total, looks = plan.rounds.shape
    running = np.ones(rows_total, dtype=np.bool_)
    outcome = np.full(rows_total, "INCONCLUSIVE", dtype="<U12")
    used = np.zeros(rows_total, dtype=np.int64)
    for j in range(looks):
        lengths = plan.rounds[:, j]
        here = running & (lengths > 0)
        if not here.any():
            break
        for n in np.unique(lengths[here]).tolist():
            r = np.flatnonzero(here & (lengths == n))
            lo, hi = interval_at(r, n)
            final = plan.final[r, j]
            rule = np.where(final, 5, 4).astype(np.int8)
            defined = np.isfinite(lo) & np.isfinite(hi) & (lo > 0)
            if defined.any():
                pairs = None if n0 is None else np.full(defined.sum(), n)
                decided = interval_rules(
                    lo[defined],
                    hi[defined],
                    min_effect=MIN_EFFECT,
                    budget_remains=~final[defined],
                    calibrated=True,
                    session_pairs=pairs,
                    n0=n0,
                )
                rule[defined] = decided.rule
            action = _ACTION_BY_RULE[rule]
            outcome[r] = np.where(action == "CONTINUE", "INCONCLUSIVE", action)
            used[r] = n
            running[r] = action == "CONTINUE"
        running &= here
    return Ended(outcome.astype(np.str_), used)


def _nan_bounds(rows: int) -> tuple[FloatArray, FloatArray]:
    return np.full(rows, np.nan), np.full(rows, np.nan)


def batch_means_bounds(
    b: FloatArray, c: FloatArray, block: int, confidence: float
) -> tuple[FloatArray, FloatArray]:
    """Batch-means t bounds with ``block`` rounds per block.

    NaN where fewer than two complete blocks fit, so the look decides
    nothing.
    """
    if b.shape[1] // block < 2:
        return _nan_bounds(b.shape[0])
    found = t_interval(
        b,
        c,
        direction="lower_is_better",
        confidence=confidence,
        block=block,
    )
    return found.lower, found.upper


def _counts() -> dict[int, int]:
    return {}


@dataclass(slots=True)
class Tally:
    """Outcome counts and diagnostics of one procedure in one cell."""

    replications: int = 0
    keep: int = 0
    reject: int = 0
    inconclusive: int = 0
    rounds: list[NDArray[np.int64]] = field(
        default_factory=lambda: list[NDArray[np.int64]]()
    )
    miss_above: dict[int, int] = field(default_factory=_counts)
    miss_below: dict[int, int] = field(default_factory=_counts)
    covered_of: dict[int, int] = field(default_factory=_counts)

    def add(self, ended: Ended) -> None:
        """Count one chunk's outcomes."""
        self.replications += ended.outcome.size
        self.keep += int(np.count_nonzero(ended.outcome == "KEEP"))
        self.reject += int(np.count_nonzero(ended.outcome == "REJECT"))
        self.inconclusive += int(
            np.count_nonzero(ended.outcome == "INCONCLUSIVE")
        )
        self.rounds.append(ended.rounds)

    def add_coverage(
        self, n: int, lower: FloatArray, upper: FloatArray, speedup: float
    ) -> None:
        """Count fixed-``n`` intervals missing ``S`` on either side."""
        self.miss_above[n] = self.miss_above.get(n, 0) + int(
            np.count_nonzero(lower > speedup)
        )
        self.miss_below[n] = self.miss_below.get(n, 0) + int(
            np.count_nonzero(upper < speedup)
        )
        self.covered_of[n] = self.covered_of.get(n, 0) + lower.size

    def summary(self, cell: Cell) -> dict[str, object]:
        """Counts, the acceptance verdict and the diagnostics."""
        total = self.replications
        errors = self.keep if cell.error == "KEEP" else self.reject
        decided = self.keep + self.reject
        rounds = np.concatenate(self.rounds)
        bound = clopper_pearson_upper(errors, total)
        return {
            "replications": total,
            "keep": self.keep,
            "reject": self.reject,
            "inconclusive": self.inconclusive,
            "error": cell.error,
            "errors": errors,
            "error_rate": errors / total,
            "cp_upper": bound,
            "passes": bound <= LIMIT,
            "conditional_error_rate": (errors / decided if decided else None),
            "keep_rate": self.keep / total,
            "inconclusive_rate": self.inconclusive / total,
            "rounds_mean": float(np.mean(rounds)),
            "rounds_p95": int(
                np.percentile(rounds, 95, method="inverted_cdf")
            ),
            "coverage": {
                str(n): {
                    "of": self.covered_of[n],
                    "miss_above": self.miss_above[n],
                    "miss_below": self.miss_below[n],
                }
                for n in sorted(self.covered_of)
            },
        }


def _chunks(total: int, n: int) -> Iterator[tuple[int, int]]:
    """Replication ranges of at most `CHUNK_BYTES` of log times."""
    size = max(1, CHUNK_BYTES // (16 * n))
    for start in range(0, total, size):
        yield start, min(start + size, total)


@dataclass(frozen=True, slots=True)
class _CellRun:
    """What every chunk of one cell's replications shares."""

    cell: Cell
    confidence: float
    cross_n0: int | None
    fixed_n: tuple[int, ...]
    tallies: dict[str, Tally]


def run_cell(
    cell: Cell,
    *,
    seed: int,
    procedures: Sequence[str] = PROCEDURES,
    replications: int = REPLICATIONS,
) -> dict[str, object]:
    """Run one cell and return its results as plain data."""
    unknown = set(procedures) - set(PROCEDURES)
    if unknown:
        msg = f"unknown procedures {sorted(unknown)}"
        raise ValueError(msg)
    begun = time.monotonic()
    sched = cell.schedule
    scenario = scenarios(cell.mode)[cell.scenario]
    root = np.random.SeedSequence(
        seed, spawn_key=(zlib.crc32(cell.name.encode()),)
    )
    (sim_seq,) = root.spawn(1)
    sim_rng = np.random.default_rng(sim_seq)
    run = _CellRun(
        cell=cell,
        confidence=look_confidence(DEFAULT_ALPHA, sched.looks.max_looks),
        cross_n0=sched.looks.n0 if cell.mode == "cross_host" else None,
        fixed_n=(sched.looks.n0, sched.max_rounds),
        tallies={name: Tally() for name in procedures},
    )
    cut_by_time = 0
    for start, stop in _chunks(replications, sched.max_rounds):
        drawn = simulate(
            scenario,
            speedup=cell.speedup,
            n=sched.max_rounds,
            replications=stop - start,
            mode=cell.mode,
            rng=sim_rng,
        )
        rounds = collected_rounds(drawn.log_times, sched)
        cut_by_time += int(np.count_nonzero(rounds < sched.max_rounds))
        _run_chunk(run, drawn.log_times, rounds)
    return {
        "cell": cell.name,
        "schedule": sched.name,
        "scenario": cell.scenario,
        "mode": cell.mode,
        "speedup": cell.speedup,
        "seed": seed,
        "replications": replications,
        "confidence": run.confidence,
        "cut_by_time": cut_by_time,
        "wall_seconds": time.monotonic() - begun,
        "procedures": {
            name: tally.summary(cell) for name, tally in run.tallies.items()
        },
    }


def _run_chunk(
    run: _CellRun,
    log_times: FloatArray,
    rounds: NDArray[np.int64],
) -> None:
    """Run every procedure on one chunk of replications."""
    cell, tallies, conf = run.cell, run.tallies, run.confidence
    sched = cell.schedule
    values = np.exp(log_times)
    if not (np.all(np.isfinite(values)) and np.all(values > 0)):
        msg = f"{cell.name}: simulated times leave the float64 range"
        raise ValueError(msg)
    b = np.ascontiguousarray(values[..., 0])
    c = np.ascontiguousarray(values[..., 1])
    exhausted = np.ones(b.shape[0], dtype=np.bool_)
    if PRIMARY in tallies:
        result = run_looks(
            b,
            c,
            direction="lower_is_better",
            schedule=sched.looks,
            mode=cell.mode,
            min_effect=MIN_EFFECT,
            calibrated=True,
            resolution=None,
            exhausted=exhausted,
            rounds=rounds,
            procedure="t_log_ratio",
        )
        if np.any(result.outcome == "CONTINUE"):
            msg = f"{cell.name}: {PRIMARY} ended with CONTINUE"
            raise ValueError(msg)
        tallies[PRIMARY].add(Ended(result.outcome, result.rounds_used))
        for n in run.fixed_n:
            found = t_interval(
                b[:, :n],
                c[:, :n],
                direction="lower_is_better",
                confidence=conf,
            )
            tallies[PRIMARY].add_coverage(
                n, found.lower, found.upper, cell.speedup
            )
    if FALLBACK in tallies:
        plan = plan_looks(sched.looks, rounds, exhausted, mode=cell.mode)

        def blocked(
            rows: NDArray[np.intp], n: int
        ) -> tuple[FloatArray, FloatArray]:
            return batch_means_bounds(
                b[rows, :n], c[rows, :n], BATCH_BLOCK, conf
            )

        tallies[FALLBACK].add(drive_looks(plan, blocked, n0=run.cross_n0))
        for n in run.fixed_n:
            lo, hi = batch_means_bounds(b[:, :n], c[:, :n], BATCH_BLOCK, conf)
            if np.all(np.isfinite(lo)):
                tallies[FALLBACK].add_coverage(n, lo, hi, cell.speedup)
