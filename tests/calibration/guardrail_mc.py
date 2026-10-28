"""Monte Carlo calibration of guardrails (``docs/method.md``).

"Guardrail calibration" in ``docs/method.md`` fixes what is run and
how a cell passes; this module runs it. A cell is one look schedule, one
scenario (the development and exposed scenarios of the primary's main
grid, one host, and the page-quantised memory scenarios below), one
configuration and one true value. Each of its ``R`` replications is one
complete comparison on rounds drawn by
`perfproof.stats.simulate.simulate` (or `pages`) with balanced order,
the round and time budget of the schedule, and the looks
`perfproof.stats.sequential.plan_looks` places, with the production
functions at every look: `perfproof.stats.guardrails.evaluate_guardrail`
for the guardrail and `perfproof.stats.sequential.decide_look` for the
primary.

Two processes run on the same replications, and each cell is judged on
both:

- **Guardrail alone.** The comparison stops at the first look at which
  the guardrail is ``passed`` or ``breached``. A *false pass* is
  ``passed`` when the guardrail is truly beyond, a *false breach*
  ``breached`` when it is truly within. This is the event the union
  bound of "Error control" covers, whatever the primary does.
- **The verdict.** A second, independent draw of paired rounds has a
  true primary speedup of `PRIMARY_SPEEDUP`, well above the bar, so the
  primary keeps at the first look it can. Each look's rule and the
  guardrail's status go through
  `perfproof.stats.decision.guardrail_actions`, and the comparison stops
  at the first look that does not continue. A *false KEEP* is a KEEP
  while the guardrail is truly beyond, a *false guardrail REJECT* a
  REJECT (``guardrail``) while it is truly within.

Configurations (the number of checks ``G`` sets the confidence
``1 - alpha / (2 K G)`` and, with ``K``, whether ``hac_lf`` reaches it):

- ``rel1``: an allowance of `ALLOWANCE` and nothing else, ``G = 1``. The
  baseline sits at `LEVEL`; the candidate's true geometric-mean ratio is
  the cell's.
- ``rel3``: the same guardrail in a spec with three checks, ``G = 3``,
  which sends the python_native schedule (``K = 6``) to the gated batch
  means.
- ``limit``: the allowance with true ratio 1 (within) and one absolute
  limit, ``G = 2``. The cell's value is the candidate's true value over
  the limit.

Randomness comes from one seed, ``PERFPROOF_CALIBRATION_SEED`` when set
and `DEFAULT_SEED` otherwise, each cell drawing from its own
`numpy.random.SeedSequence` keyed by the CRC-32 of its name, so a cell
gives the same counts whichever other cells run with it.
"""

import math
import os
import time
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from typing import Final, Literal

import montecarlo as mc
import numpy as np
from numpy.typing import NDArray
from scipy import stats  # pyright: ignore[reportMissingTypeStubs]

from perfproof.stats.decision import guardrail_actions
from perfproof.stats.guardrails import (
    evaluate_guardrail,
    guardrail_confidence,
    guardrail_procedure,
    limit_bounds,
)
from perfproof.stats.sequential import (
    DEFAULT_ALPHA,
    decide_look,
    look_confidence,
    plan_looks,
    procedure_for_schedule,
)
from perfproof.stats.simulate import NoiseScenario, simulate

FloatArray = NDArray[np.float64]
Config = Literal["rel1", "rel3", "limit"]

SEED_ENV: Final = mc.SEED_ENV
DEFAULT_SEED: Final = 7282691080722525374
"""Set below, before any run of record, from the OS entropy pool."""
ALLOWANCE: Final = 0.10
"""The default allowance of peak memory."""
BAR: Final = 1.0 + ALLOWANCE
LEVEL: Final = 1e8
"""The baseline's typical value, in the metric's unit (100 MB)."""
PRIMARY_SPEEDUP: Final = 1.20
CHECKS: Final[dict[Config, int]] = {"rel1": 1, "rel3": 3, "limit": 2}
REL_RATIOS: Final = (1.00, 1.05, 1.10, 1.11, 1.20, 1.50)
"""True worsening ratios of the relative cells: the first three are
within the allowance (1.10 is exactly on it, where compliance is still
true), the last three beyond it (1.11 is the nearest, about one
standard error beyond at 5% noise and 60 rounds)."""
REL3_RATIOS: Final = (1.10, 1.11)
"""The two cells nearest the bar, for the three-check configuration."""
LIMIT_RATIOS: Final = (0.90, 0.97, 1.00, 1.01, 1.03, 1.10)
"""True candidate value over the limit: the first three within (1.00 is
exactly on it), the last three beyond."""
PAGE: Final = 4096.0
"""The resolution step of the page scenarios, in bytes."""


@dataclass(frozen=True, slots=True)
class Pages:
    """A scenario of page-quantised memory, in bytes.

    Each round's value is ``round(level exp(sigma z))`` pages of `PAGE`
    bytes, the baseline's level drawn once per replication from
    ``levels`` (a fixed level when the range is one value), the
    candidate's times the true ratio. The estimand is the geometric
    mean, so the true ratio is exact before the rounding and the
    resolution widening has to cover it.

    Attributes
    ----------
    levels : tuple of int
        Smallest and largest baseline level, in pages.
    sigma : float
        Log-scale sd of the run-to-run noise, at least 0.
    """

    levels: tuple[int, int]
    sigma: float


PAGES: Final[dict[str, Pages]] = {
    "pages_exact": Pages((10_000, 40_000), 0.0),
    "pages_tight": Pages((40_000, 40_000), 0.0005),
    "pages_noisy": Pages((5_000, 5_000), 0.02),
}
"""Deterministic memory (every round alike but for the rounding), memory
that jitters by tens of pages, and memory that jitters by 2%."""
LIMIT_PAGES: Final = ("pages_tight", "pages_noisy")
"""Page scenarios with a fixed level, so a limit can be fixed."""


@dataclass(frozen=True, slots=True)
class Cell:
    """One guardrail calibration cell.

    Attributes
    ----------
    schedule : montecarlo.TargetSchedule
        Look schedule and budget.
    scenario : str
        A one-host scenario of `montecarlo.scenarios` or of `PAGES`.
    config : {"rel1", "rel3", "limit"}
        What the guardrail checks.
    ratio : float
        The true worsening ratio (relative configurations) or the true
        candidate value over the limit (``limit``).
    """

    schedule: mc.TargetSchedule
    scenario: str
    config: Config
    ratio: float

    @property
    def name(self) -> str:
        """Stable name, also the key of the cell's seed."""
        return (
            f"{self.schedule.name}/{self.scenario}/{self.config}/"
            f"{self.ratio:.2f}"
        )

    @property
    def truly_within(self) -> bool:
        """Whether the guardrail is truly within, at or inside it."""
        bar = 1.0 if self.config == "limit" else BAR
        return self.ratio <= bar

    @property
    def messy(self) -> bool:
        """Whether this is a limit cell whose own values are messy.

        A scenario with a slow mode or outliers, the cells that test
        that the rank bound is not moved by them. Every cell is judged
        by the acceptance rule, messy or not; the flag reports them
        apart.
        """
        if self.config != "limit" or self.scenario in PAGES:
            return False
        return self.scenario not in _symmetric(mc.scenarios("one_host"))


def _stationary(scenarios: dict[str, NoiseScenario]) -> list[str]:
    """Names of the scenarios whose candidate mean does not move.

    A drift moves each round's own expectation across the session, so
    the candidate's true geometric mean at a look is not one number; a
    limit is not checked there.
    """
    return [n for n, s in scenarios.items() if s.drift == 0.0]


def _symmetric(scenarios: dict[str, NoiseScenario]) -> list[str]:
    """Names of the stationary scenarios without rare large values."""
    return [
        n
        for n, s in scenarios.items()
        if s.drift == 0.0 and s.slow_fraction == 0.0 and s.outlier_rate == 0.0
    ]


def scenario_names(config: Config) -> list[str]:
    """Return the scenarios a configuration runs, in a fixed order."""
    found = mc.scenarios("one_host")
    if config == "limit":
        return [*_stationary(found), *LIMIT_PAGES]
    return [*found, *PAGES]


def all_cells() -> list[Cell]:
    """Every cell, in a fixed order."""
    cells: list[Cell] = []
    configs: tuple[tuple[Config, tuple[float, ...]], ...] = (
        ("rel1", REL_RATIOS),
        ("rel3", REL3_RATIOS),
        ("limit", LIMIT_RATIOS),
    )
    for schedule in mc.SCHEDULES:
        for config, ratios in configs:
            cells.extend(
                Cell(schedule, scenario, config, ratio)
                for scenario in scenario_names(config)
                for ratio in ratios
            )
    return cells


def cell_by_name(name: str) -> Cell:
    """Return the cell with ``name``, as `Cell.name` spells it."""
    schedule, scenario, config, ratio = name.split("/")
    kinds: dict[str, Config] = {k: k for k in CHECKS}
    if config not in kinds:
        msg = f"unknown configuration in cell {name!r}"
        raise ValueError(msg)
    cell = Cell(
        mc.SCHEDULE_BY_NAME[schedule],
        scenario,
        kinds[config],
        float(ratio),
    )
    if cell.name != name or scenario not in scenario_names(cell.config):
        msg = f"no such cell {name!r}"
        raise ValueError(msg)
    return cell


@cache
def candidate_log_median(name: str) -> float:
    """Return the median of the candidate's log time in a scenario.

    At ratio 1, over a session, to the precision of 2.4 million draws
    from a fixed generator (about 1e-4 of a value at the noisiest
    scenario), the truth the limit cells are judged against: the cap's
    statistic is the median, and a slow mode or outliers move it by
    their rank, not by their size. The drift is excluded (see
    `_stationary`).
    """
    scenario = mc.scenarios("one_host")[name]
    drawn = simulate(
        scenario,
        speedup=1.0,
        n=60,
        replications=40_000,
        mode="one_host",
        rng=np.random.default_rng(20261024),
    )
    return float(np.median(drawn.log_times[..., 1]))


def limit_value(cell: Cell) -> float:
    """Return the limit of a ``limit`` cell, in the metric's unit.

    The candidate's true median divided by the cell's ratio, so the
    ratio is the true candidate median over the limit.
    """
    if cell.scenario in PAGES:
        low, high = PAGES[cell.scenario].levels
        if low != high:
            msg = f"{cell.scenario} has no fixed level for a limit"
            raise ValueError(msg)
        return low * PAGE / cell.ratio
    return LEVEL * math.exp(candidate_log_median(cell.scenario)) / cell.ratio


def pages(
    name: str,
    ratio: float,
    rows: int,
    n: int,
    rng: np.random.Generator,
) -> tuple[FloatArray, FloatArray, NDArray[np.bool_]]:
    """Draw page-quantised paired values of scenario ``name``.

    Returns the baseline and candidate values in bytes, shaped
    ``(rows, n)``, and the in-round order of balanced pairs.
    """
    spec = PAGES[name]
    low, high = spec.levels
    level = rng.integers(low, high + 1, size=(rows, 1)).astype(np.float64)
    noise = spec.sigma * rng.standard_normal((rows, n, 2))
    base = np.maximum(np.rint(level * np.exp(noise[..., 0])), 1.0)
    cand = np.maximum(np.rint(level * ratio * np.exp(noise[..., 1])), 1.0)
    first = np.zeros((rows, n), dtype=np.bool_)
    coins = rng.random((rows, (n + 1) // 2)) < 0.5
    first[:, 0::2] = coins
    first[:, 1::2] = ~coins[:, : n // 2]
    return base * PAGE, cand * PAGE, first


@dataclass(slots=True)
class _Coverage:
    """How often a look's bounds on the median missed the true median.

    Counted over all replications at one look length: ``bounded`` of
    them had a bound, ``miss_upper`` an upper bound below the truth and
    ``miss_lower`` a lower bound above it. A replication with no bound
    claims nothing, so it misses nothing.
    """

    bounded: int = 0
    miss_upper: int = 0
    miss_lower: int = 0


@dataclass(slots=True)
class _Counts:
    """Outcome counts of one process in one cell."""

    passed: int = 0
    breached: int = 0
    undecided: int = 0
    keep: int = 0
    reject: int = 0
    performance_reject: int = 0
    inconclusive: int = 0


def _chunks(rows: int, n: int) -> Iterator[tuple[int, int]]:
    size = max(1, mc.CHUNK_BYTES // (16 * n))
    for start in range(0, rows, size):
        yield start, min(start + size, rows)


@dataclass(frozen=True, slots=True)
class _Plan:
    """What every chunk of one cell shares."""

    cell: Cell
    procedure: str
    primary_procedure: str
    confidence: float
    primary_confidence: float
    limits: tuple[float, ...]
    allowance: float


def run_cell(
    cell: Cell,
    *,
    seed: int,
    replications: int = mc.REPLICATIONS,
) -> dict[str, object]:
    """Run one cell and return its counts and verdicts as plain data."""
    begun = time.monotonic()
    sched = cell.schedule
    primary = procedure_for_schedule(sched.looks, sched.max_rounds)
    checks = CHECKS[cell.config]
    plan = _Plan(
        cell=cell,
        procedure=guardrail_procedure(primary, sched.looks.max_looks, checks),
        primary_procedure=primary,
        confidence=guardrail_confidence(
            DEFAULT_ALPHA, sched.looks.max_looks, checks
        ),
        primary_confidence=look_confidence(
            DEFAULT_ALPHA, sched.looks.max_looks
        ),
        limits=(limit_value(cell),) if cell.config == "limit" else (),
        allowance=ALLOWANCE,
    )
    root = np.random.SeedSequence(
        seed, spawn_key=(zlib.crc32(cell.name.encode()),)
    )
    (sim_seq,) = root.spawn(1)
    rng = np.random.default_rng(sim_seq)
    alone, verdict = _Counts(), _Counts()
    rounds_used: list[NDArray[np.int64]] = []
    coverage: dict[int, _Coverage] = {}
    for start, stop in _chunks(replications, sched.max_rounds):
        used = _run_chunk(plan, stop - start, rng, alone, verdict, coverage)
        rounds_used.append(used)
    errors_alone = alone.breached if cell.truly_within else alone.passed
    errors_verdict = verdict.reject if cell.truly_within else verdict.keep
    used_all = np.concatenate(rounds_used)
    return {
        "cell": cell.name,
        "schedule": sched.name,
        "scenario": cell.scenario,
        "config": cell.config,
        "ratio": cell.ratio,
        "seed": seed,
        "replications": replications,
        "procedure": plan.procedure,
        "confidence": plan.confidence,
        "truly_within": cell.truly_within,
        "messy": cell.messy,
        "alone": _summary(alone, errors_alone, replications),
        "verdict": _summary(verdict, errors_verdict, replications),
        "rounds_p95": int(np.percentile(used_all, 95, method="inverted_cdf")),
        "coverage": {
            str(at): {
                "bounded": c.bounded,
                "miss_upper": c.miss_upper,
                "miss_lower": c.miss_lower,
            }
            for at, c in sorted(coverage.items())
        },
        "tail": (1.0 - plan.confidence) / 2.0,
        "wall_seconds": time.monotonic() - begun,
    }


def coverage_holds(misses: int, replications: int, tail: float) -> bool:
    """Whether misses do not show a bound wrong more often than stated.

    The one-sided 95% Clopper-Pearson lower bound on the miss rate is at
    most the bound's own error, ``tail``: the evidence has to show the
    rate is above the stated one, not near it, for the claim to fail.
    """
    if misses == 0:
        return True
    low = np.asarray(
        stats.beta.ppf(0.05, misses, replications - misses + 1),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        dtype=np.float64,
    )
    return float(low) <= tail


def _summary(
    counts: _Counts, errors: int, replications: int
) -> dict[str, object]:
    bound = mc.clopper_pearson_upper(errors, replications)
    return {
        "passed": counts.passed,
        "breached": counts.breached,
        "undecided": counts.undecided,
        "keep": counts.keep,
        "reject": counts.reject,
        "performance_reject": counts.performance_reject,
        "inconclusive": counts.inconclusive,
        "errors": errors,
        "error_rate": errors / replications,
        "cp_upper": bound,
        "passes": bound <= mc.LIMIT,
    }


def _run_chunk(
    plan: _Plan,
    rows: int,
    rng: np.random.Generator,
    alone: _Counts,
    verdict: _Counts,
    coverage: dict[int, _Coverage],
) -> NDArray[np.int64]:
    """Run ``rows`` replications through every look, counting."""
    cell = plan.cell
    sched = cell.schedule
    n = sched.max_rounds
    scenario = (
        None
        if cell.scenario in PAGES
        else mc.scenarios("one_host")[cell.scenario]
    )
    steps = None
    if scenario is None:
        gb, gc, gfirst = pages(
            cell.scenario,
            1.0 if cell.config == "limit" else cell.ratio,
            rows,
            n,
            rng,
        )
        steps = np.full((rows, n), PAGE)
        noise = mc.scenarios("one_host")["lognormal_cv5"]
    else:
        ratio = 1.0 if cell.config == "limit" else cell.ratio
        drawn = simulate(
            scenario,
            speedup=1.0 / ratio,
            n=n,
            replications=rows,
            mode="one_host",
            rng=rng,
        )
        values = LEVEL * np.exp(drawn.log_times)
        gb = np.ascontiguousarray(values[..., 0])
        gc = np.ascontiguousarray(values[..., 1])
        gfirst = drawn.candidate_first
        noise = scenario
    drawn_primary = simulate(
        noise,
        speedup=PRIMARY_SPEEDUP,
        n=n,
        replications=rows,
        mode="one_host",
        rng=rng,
    )
    pvalues = np.exp(drawn_primary.log_times)
    pb = np.ascontiguousarray(pvalues[..., 0])
    pc = np.ascontiguousarray(pvalues[..., 1])
    pfirst = drawn_primary.candidate_first
    collected = mc.collected_rounds(drawn_primary.log_times, sched)
    looks = plan_looks(
        sched.looks,
        collected,
        np.ones(rows, dtype=np.bool_),
        mode="one_host",
    )
    if cell.config == "limit":
        truth = plan.limits[0] * cell.ratio
        tail = (1.0 - plan.confidence) / 2.0
        for at in (sched.looks.n0, n):
            found = limit_bounds(
                gc[:, :at],
                quantile=0.5,
                tail=tail,
                candidate_first=gfirst[:, :at],
            )
            counted = coverage.setdefault(at, _Coverage())
            counted.bounded += int(np.count_nonzero(np.isfinite(found.upper)))
            counted.miss_upper += int(np.count_nonzero(found.upper < truth))
            counted.miss_lower += int(np.count_nonzero(found.lower > truth))
    open_alone = np.ones(rows, dtype=np.bool_)
    open_verdict = np.ones(rows, dtype=np.bool_)
    used = np.zeros(rows, dtype=np.int64)
    for j in range(sched.looks.max_looks):
        lengths = looks.rounds[:, j]
        for length in np.unique(lengths[(open_alone | open_verdict)]).tolist():
            if length == 0:
                continue
            r = np.flatnonzero(
                (open_alone | open_verdict) & (lengths == length)
            )
            final = looks.final[r, j]
            g = evaluate_guardrail(
                gb[r, :length],
                gc[r, :length],
                direction="lower_is_better",
                allowance=plan.allowance,
                limits=plan.limits,
                confidence=plan.confidence,
                procedure=plan.procedure,  # pyright: ignore[reportArgumentType]
                final=final,
                candidate_first=gfirst[r, :length],
                baseline_step=None if steps is None else steps[r, :length],
                candidate_step=None if steps is None else steps[r, :length],
            )
            status = g.status
            decided = (status == "passed") | (status == "breached")
            first_here = open_alone[r] & decided
            alone.passed += int(
                np.count_nonzero(first_here & (status == "passed"))
            )
            alone.breached += int(
                np.count_nonzero(first_here & (status == "breached"))
            )
            open_alone[r] &= ~decided
            used[r] = np.where(open_alone[r] | first_here, length, used[r])
            asked = open_verdict[r]
            if asked.any():
                look = decide_look(
                    pb[r, :length],
                    pc[r, :length],
                    direction="lower_is_better",
                    confidence=plan.primary_confidence,
                    min_effect=mc.MIN_EFFECT,
                    calibrated=True,
                    final=final,
                    resolution=None,
                    procedure=plan.primary_procedure,  # pyright: ignore[reportArgumentType]
                    candidate_first=pfirst[r, :length],
                )
                step = guardrail_actions(
                    look.rule,
                    breached=status == "breached",
                    fixed=np.zeros(r.size, dtype=np.bool_),
                    pending=status == "not_established",
                    budget_remains=~final,
                )
                action = step.action
                stop = asked & (action != "CONTINUE")
                refused = stop & step.guardrail_reject
                verdict.keep += int(
                    np.count_nonzero(stop & (action == "KEEP"))
                )
                verdict.reject += int(np.count_nonzero(refused))
                verdict.performance_reject += int(
                    np.count_nonzero(
                        stop & (action == "REJECT") & ~step.guardrail_reject
                    )
                )
                verdict.inconclusive += int(
                    np.count_nonzero(stop & (action == "INCONCLUSIVE"))
                )
                open_verdict[r] &= ~stop
    alone.undecided += int(np.count_nonzero(open_alone))
    verdict.undecided += int(np.count_nonzero(open_verdict))
    return used


def calibration_seed() -> int:
    """Return ``PERFPROOF_CALIBRATION_SEED``, else the record."""
    given = os.environ.get(SEED_ENV)
    if given is None or not given.strip():
        return DEFAULT_SEED
    value = int(given)
    if value < 0:
        msg = f"{SEED_ENV} must be a non-negative integer, got {given!r}"
        raise ValueError(msg)
    return value
