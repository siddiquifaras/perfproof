import functools
import math
import time
import tracemalloc
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from typing import Any, Literal, cast

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from numpy.typing import NDArray
from pydantic import BaseModel
from scipy import special, stats  # pyright: ignore[reportMissingTypeStubs]

from perfproof.schema import (
    Arm,
    BenchmarkSpec,
    Budget,
    CorrectnessSpec,
    Diagnostics,
    Direction,
    GateName,
    GateResult,
    GuardrailResult,
    GuardrailSpec,
    Interval,
    LookSchedule,
    MachineFingerprint,
    MetricSpec,
    RunRecord,
    TreeEnvironment,
    Verdict,
)
from perfproof.stats.decision import decide
from perfproof.stats.intervals import (
    LIBM_ULPS,
    MAX_HL_ROUNDS,
    WILCOXON_EXACT_MAX_N,
    PairedInterval,
    _centred_squares_upper,  # pyright: ignore[reportPrivateUsage]
    _up,  # pyright: ignore[reportPrivateUsage]
    batch_means,
    hodges_lehmann_interval,
    normal_critical,
    paired_ratios,
    t_critical,
    t_interval,
)

FloatArray = NDArray[np.float64]
Kind = Literal["t", "batch", "hl"]
KINDS: tuple[Kind, ...] = ("t", "batch", "hl")
BLOCK = 5
FOUND_FALSE_KEEP = 0.4730308035801494
NAMED_MIN_EFFECTS = (0.0, 0.03, 0.118, 0.124, FOUND_FALSE_KEEP)
PREC = 60

isf_t = cast(
    "Callable[[float, int], float]",
    stats.t.isf,  # pyright: ignore[reportUnknownMemberType]
)
sem = cast(
    "Callable[[FloatArray], float]",
    stats.sem,  # pyright: ignore[reportUnknownMemberType]
)
stdtr = cast(
    "Callable[[int, float], float]",
    special.stdtr,  # pyright: ignore[reportUnknownMemberType]
)
ndtri = cast(
    "Callable[[float], float]",
    special.ndtri,  # pyright: ignore[reportUnknownMemberType]
)


def step(x: float, k: int) -> float:
    """Return ``x`` moved ``k`` float64 ulps (negative is down)."""
    for _ in range(abs(k)):
        x = math.nextafter(x, math.inf if k > 0 else -math.inf)
    return x


def interval(
    kind: Kind,
    baseline: Any,
    candidate: Any,
    confidence: float,
    direction: Direction = "lower_is_better",
) -> PairedInterval:
    if kind == "hl":
        return hodges_lehmann_interval(
            baseline, candidate, direction=direction, confidence=confidence
        )
    return t_interval(
        baseline,
        candidate,
        direction=direction,
        confidence=confidence,
        block=BLOCK if kind == "batch" else 1,
    )


def bounds(res: PairedInterval) -> tuple[FloatArray, FloatArray, FloatArray]:
    return res.estimate, res.lower, res.upper


# Exact reference: the procedures in 60-digit decimal arithmetic on the
# float64 ratios. Written apart from the module, from the same textbook
# formulas, and cross-checked against SciPy below.


@functools.cache
def exact_log(x: float) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = PREC
        return Decimal(x).ln()


def exact_exp(x: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = PREC
        return x.exp()


def ref_atan(y: Decimal) -> Decimal:
    """Arctangent for ``y >= 0`` by halving, then the Taylor series."""
    halvings = 0
    while y > Decimal("0.001"):
        y = y / (1 + (1 + y * y).sqrt())
        halvings += 1
    total = Decimal(0)
    power = y
    k = 0
    while True:
        add = power / (2 * k + 1)
        if add < Decimal(10) ** -75:
            break
        total += add if k % 2 == 0 else -add
        power *= y * y
        k += 1
    return total * 2**halvings


@functools.cache
def ref_pi() -> Decimal:
    with localcontext() as ctx:
        ctx.prec = PREC
        return 16 * ref_atan(Decimal(1) / 5) - 4 * ref_atan(Decimal(1) / 239)


def ref_t_mass(df: int, t: Decimal) -> Decimal:
    """P(|T| <= t), Abramowitz and Stegun 26.7.3 and 26.7.4."""
    with localcontext() as ctx:
        ctx.prec = PREC
        cos2 = df / (df + t * t)
        sin = t / (df + t * t).sqrt()
        if df % 2 == 0:
            coef = [Decimal(1)]
            for j in range(1, df // 2):
                coef.append(coef[-1] * (2 * j - 1) / (2 * j))
            return sin * sum(
                (c * cos2**j for j, c in enumerate(coef)), Decimal(0)
            )
        theta = ref_atan(t / Decimal(df).sqrt())
        coef = [Decimal(1)]
        for j in range(1, (df - 1) // 2):
            coef.append(coef[-1] * (2 * j) / (2 * j + 1))
        series = sum((c * cos2**j for j, c in enumerate(coef)), Decimal(0))
        inner = theta if df == 1 else theta + sin * cos2.sqrt() * series
        return 2 * inner / ref_pi()


def ref_normal_mass(z: Decimal) -> Decimal:
    """P(|Z| <= z) from erf's Taylor series, alternating form."""
    with localcontext() as ctx:
        ctx.prec = PREC + 20
        total = Decimal(0)
        k = 0
        term = z
        while True:
            add = term / (2 * k + 1)
            if abs(add) < Decimal(10) ** -80 and k > z * z:
                break
            total += add
            k += 1
            term = -term * z * z / (2 * k)
        return 2 * total / (2 * ref_pi()).sqrt()


def bisect(mass: Callable[[Decimal], Decimal], target: Decimal) -> Decimal:
    lo, hi = Decimal(0), Decimal(1)
    while mass(hi) < target:
        hi *= 2
    with localcontext() as ctx:
        ctx.prec = PREC
        for _ in range(170):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if mass(mid) < target else (lo, mid)
    return hi


@functools.cache
def ref_t_quantile(df: int, confidence: float) -> Decimal:
    return bisect(lambda t: ref_t_mass(df, t), Decimal(confidence))


@functools.cache
def ref_normal_quantile(confidence: float) -> Decimal:
    return bisect(ref_normal_mass, Decimal(confidence))


@functools.cache
def ref_signed_rank_counts(n: int) -> tuple[int, ...]:
    """Subsets of {1..n} by sum, by repeated convolution."""
    counts = [1]
    for i in range(1, n + 1):
        shifted = [0] * i + counts
        counts = [
            (counts[s] if s < len(counts) else 0) + shifted[s]
            for s in range(len(shifted))
        ]
    return tuple(counts)


def ref_critical(ratios: Sequence[float], confidence: float) -> int:
    """Signed-rank critical count: exact, or normal in Decimal."""
    n = len(ratios)
    alpha_half = (1 - Fraction(confidence)) / 2
    if n <= WILCOXON_EXACT_MAX_N:
        counts = ref_signed_rank_counts(n)
        cumulative, crit = 0, -1
        for c, k in enumerate(counts):
            cumulative += k
            if Fraction(cumulative, 2**n) > alpha_half:
                break
            crit = c
        return crit
    ties = sum(t**3 - t for t in Counter(ratios).values())
    with localcontext() as ctx:
        ctx.prec = PREC
        var = Decimal(n * (n + 1) * (2 * n + 1)) / 24 - Decimal(ties) / 48
        x = Decimal(n * (n + 1)) / 4 - ref_normal_quantile(confidence) * (
            var.sqrt()
        )
        x -= Decimal("0.5")
        return int(x.to_integral_value(rounding="ROUND_CEILING")) - 1


@dataclass(frozen=True)
class Exact:
    """An exact bound, compared with floats without rounding error.

    ``square`` holds the bound's exact square where it is rational: a
    Hodges-Lehmann bound ``exp((log a + log b) / 2)`` is
    ``sqrt(a b)``, and identical rounds give the ratio itself. A t
    bound is transcendental and held to 60 digits in ``value``; a
    comparison closer than 1e-45 would be undecided and fails.
    """

    value: Decimal | None = None
    square: Fraction | None = None

    def approx(self) -> float:
        """A float within a few ulps of the bound."""
        if self.square is not None:
            return math.sqrt(float(self.square))
        assert self.value is not None
        return float(self.value)

    def cmp(self, x: float) -> int:
        """Sign of ``exact - x``."""
        if self.square is not None:
            fx = Fraction(x) ** 2
            return (self.square > fx) - (self.square < fx)
        assert self.value is not None
        diff = self.value - Decimal(x)
        assert abs(diff) > Decimal(10) ** -45 * Decimal(x)
        return 1 if diff > 0 else -1


def exact_bounds(
    kind: Kind, ratios: Sequence[float], confidence: float
) -> tuple[Exact, Exact]:
    """The exact interval of ``kind`` on these float64 ratios."""
    if kind == "batch":
        ratios = ratios[: len(ratios) // BLOCK * BLOCK]
    if len(set(ratios)) == 1:
        point = Exact(square=Fraction(ratios[0]) ** 2)
        return point, point
    if kind == "hl":
        return exact_hl(ratios, confidence)
    logs = [exact_log(r) for r in ratios]
    with localcontext() as ctx:
        ctx.prec = PREC
        y = logs
        if kind == "batch":
            y = [
                sum(logs[i : i + BLOCK], Decimal(0)) / BLOCK
                for i in range(0, len(logs), BLOCK)
            ]
        m = len(y)
        mean = sum(y, Decimal(0)) / m
        sd = (sum(((v - mean) ** 2 for v in y), Decimal(0)) / (m - 1)).sqrt()
        half = ref_t_quantile(m - 1, confidence) * sd / Decimal(m).sqrt()
        return (
            Exact(value=exact_exp(mean - half)),
            Exact(value=exact_exp(mean + half)),
        )


def exact_hl(
    ratios: Sequence[float], confidence: float
) -> tuple[Exact, Exact]:
    """Order statistics of the exact Walsh averages, as ``a b``.

    ``(log a + log b) / 2`` orders like ``a b``, so the products of
    the ratio pairs, in exact rationals, stand for the Walsh averages.
    """
    crit = ref_critical(ratios, confidence)
    assert crit >= 0
    tally = sorted(Counter(Fraction(r) for r in ratios).items())
    walsh: Counter[Fraction] = Counter()
    for i, (a, ca) in enumerate(tally):
        walsh[a * a] += ca * (ca + 1) // 2
        for b, cb in tally[i + 1 :]:
            walsh[a * b] += ca * cb
    ordered = sorted(walsh.items())
    total = len(ratios) * (len(ratios) + 1) // 2

    def order_stat(k: int) -> Exact:
        seen = 0
        for value, count in ordered:
            seen += count
            if seen > k:
                return Exact(square=value)
        raise AssertionError(k)

    return order_stat(crit), order_stat(total - 1 - crit)


# Records, as a caller would build them

T0 = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
UNPROBED = "not probed in this test"
EVERY_GATE: tuple[GateName, ...] = (
    "side_effects",
    "lock",
    "procedure",
    "runtime",
    "same_host",
    "correctness",
    "coverage",
)
PASSED = tuple(GateResult(gate=g, status="passed") for g in EVERY_GATE)
MEMORY_NA = "the intervals are judged here, not the memory they cost"
MEMORY_SPEC = GuardrailSpec(
    metric="peak_memory",
    direction="lower_is_better",
    unit="bytes",
    applicable=False,
    reason=MEMORY_NA,
)
MEMORY_RESULT = GuardrailResult(
    metric="peak_memory", status="not_applicable", reason=MEMORY_NA
)


def unprobed(model: type[BaseModel], *keep: str) -> dict[str, str]:
    skip = {"captured_at", "version", "session", "env_values", "missing"}
    return {
        name: UNPROBED
        for name in model.model_fields
        if name not in skip and name not in keep
    }


def environment(version: Arm) -> TreeEnvironment:
    return TreeEnvironment(version=version, missing=unprobed(TreeEnvironment))


MACHINE = MachineFingerprint(
    captured_at=T0,
    host_id="bench-host",
    missing=unprobed(MachineFingerprint, "host_id"),
)
ENVIRONMENTS = (environment("baseline"), environment("candidate"))


def record(
    res: PairedInterval, row: int, min_effect: float, rule: int
) -> RunRecord:
    """Decide one row of ``res`` and build the record it would write."""
    lower = float(res.lower.reshape(-1)[row])
    upper = float(res.upper.reshape(-1)[row])
    d = decide(
        PASSED,
        (lower, upper),
        min_effect=min_effect,
        budget_remains=False,
        calibrated=True,
    )
    assert d.rule == rule
    assert d.reason is not None
    assert d.outcome != "CONTINUE"
    assert d.rule != 4
    v = Verdict(
        outcome=d.outcome,
        reason=d.reason,
        rule=d.rule,
        estimate=float(res.estimate.reshape(-1)[row]),
        interval=Interval(lower=lower, upper=upper, confidence=res.confidence),
        rounds=res.rounds,
        looks=1,
        procedure=res.procedure,
        regression=d.regression,
        target_class="python_cpu",
        calibration="calibrated",
        diagnostics=Diagnostics(
            procedure_uncalibrated=d.procedure_uncalibrated
        ),
    )
    return RunRecord(
        run_id="20261016-120000-bench",
        perfproof_version="0.1.0",
        started_at=T0,
        finished_at=T0 + timedelta(minutes=3),
        spec=BenchmarkSpec(
            objective="wall_time",
            guardrails=(MEMORY_SPEC,),
            name="bench",
            command=("python", "bench.py"),
            metric=MetricSpec(
                kind="wall", direction="lower_is_better", unit="s"
            ),
            correctness=CorrectnessSpec(
                command=("pytest",), timeout_seconds=60.0
            ),
            warmup=0,
            budget=Budget(max_rounds=1000, max_seconds=600.0),
            looks=LookSchedule(n0=10, k=10, max_looks=5),
            min_effect=min_effect,
            timeout_seconds=30.0,
            harness=("bench.py",),
        ),
        baseline_commit="0123456789abcdef0123456789abcdef01234567",
        order_seed=1,
        verdict=v,
        gates=PASSED,
        guardrails=(MEMORY_RESULT,),
        machines=(MACHINE,),
        environments=ENVIRONMENTS,
        samples_path="samples.npz",
    )


# Full path: observations, interval, decide, RunRecord


@dataclass(frozen=True)
class Case:
    min_effect: float
    ratios: tuple[float, ...]
    confidence: float
    label: str


def sweep_min_effects() -> list[float]:
    """300 bars beyond the named ones: a grid and random draws."""
    rng = np.random.default_rng(20261016)
    grid = np.linspace(0.001, 0.5, 150)
    drawn = rng.uniform(0.0, 2.0, 150)
    return [float(x) for x in (*grid, *drawn)]


def regression_cases() -> list[Case]:
    """Rounds at, an ulp off, and in tiny noise around each bar."""
    rng = np.random.default_rng(20261016)
    cases: list[Case] = []
    named = set(NAMED_MIN_EFFECTS)
    for min_effect in (*NAMED_MIN_EFFECTS, *sweep_min_effects()):
        bar = 1 + min_effect
        named_bar = min_effect in named
        confidences = (0.95, 0.99, 0.999) if named_bar else (0.99,)
        for n in (10, 48, 200) if named_bar else (10, 48):
            rows: list[tuple[str, list[float]]] = [
                (f"all at bar {k:+d} ulp", [step(bar, k)] * n)
                for k in (-1, 0, 1)
            ]
            for label, lo, hi in (
                ("ulp jitter -1..1", -1, 2),
                ("ulp jitter 0..1", 0, 2),
                ("ulp jitter -1..0", -1, 1),
            ):
                ks = rng.integers(lo, hi, n)
                rows.append((label, [step(bar, int(k)) for k in ks]))
            for cv in (1e-15, 1e-12):
                noise = rng.normal(0.0, cv, n)
                rows.append((f"cv {cv:g}", list(bar * np.exp(noise))))
            cases.extend(
                Case(min_effect, tuple(r), conf, f"n={n} {label}")
                for label, r in rows
                for conf in confidences
            )
        if named_bar:
            events = round(1000 * bar)
            cases.append(
                Case(min_effect, (events / 1000,) * 10, 0.99, "counter")
            )
    return cases


def test_regression_set_covers_the_plan() -> None:
    cases = regression_cases()
    bars = {c.min_effect for c in cases}
    assert len(bars) >= 305
    assert set(NAMED_MIN_EFFECTS) <= bars
    reproducer = [
        c
        for c in cases
        if c.min_effect == FOUND_FALSE_KEEP
        and len(c.ratios) == 48
        and set(c.ratios) == {1 + FOUND_FALSE_KEEP}
    ]
    assert reproducer


@dataclass(frozen=True)
class Tally:
    keeps: int
    rejects: int
    false_keeps: list[str]
    false_rejects: list[str]
    tighter: list[str]


def run_full_path(
    kind: Kind,
    cases: list[Case],
    compute: Callable[[Kind, FloatArray, float], PairedInterval],
) -> Tally:
    """Push ``cases`` through ``compute``, decide, record, and judge.

    A false KEEP is a KEEP whose exact interval has ``L <= bar``; a
    false REJECT is a performance REJECT whose exact interval has
    ``U > bar``. For identical rounds the exact interval is the ratio
    itself, so this is the error table of ``docs/method.md`` with
    ``S`` the ratio.
    """
    groups: dict[tuple[int, float], list[Case]] = {}
    for case in cases:
        if kind == "hl" and ref_critical(case.ratios, case.confidence) < 0:
            continue
        groups.setdefault((len(case.ratios), case.confidence), []).append(case)
    keeps = rejects = 0
    false_keeps: list[str] = []
    false_rejects: list[str] = []
    tighter: list[str] = []
    for (_, confidence), group in groups.items():
        ratios = np.array([c.ratios for c in group])
        res = compute(kind, ratios, confidence)
        for row, case in enumerate(group):
            bar = 1 + case.min_effect
            lower = float(res.lower[row])
            upper = float(res.upper[row])
            exact_lo, exact_hi = exact_bounds(
                kind, case.ratios, case.confidence
            )
            name = f"{case.label} min_effect={case.min_effect!r}"
            name += f" conf={confidence}"
            if exact_lo.cmp(lower) < 0 or exact_hi.cmp(upper) > 0:
                tighter.append(name)
            rule = 2 if lower > bar else 3 if upper <= bar else 5
            rec = record(res, row, case.min_effect, rule)
            stored = RunRecord.model_validate_json(rec.model_dump_json())
            assert stored.verdict.interval == rec.verdict.interval
            if rule == 2:
                keeps += 1
                if exact_lo.cmp(bar) <= 0:
                    false_keeps.append(name)
            elif rule == 3:
                rejects += 1
                if exact_hi.cmp(bar) > 0:
                    false_rejects.append(name)
    return Tally(keeps, rejects, false_keeps, false_rejects, tighter)


def under_test(kind: Kind, ratios: FloatArray, conf: float) -> PairedInterval:
    return interval(kind, ratios, np.ones_like(ratios), conf)


@pytest.mark.parametrize("kind", KINDS)
def test_full_path_gives_no_false_keep_or_reject(kind: Kind) -> None:
    # Before this module, the reproducer (48 rounds at 1 + 0.473...)
    # gave L = U = bar + 1 ulp on macOS arm64 and so a KEEP: np.mean of
    # 48 equal logs rounds one ulp up. Identical rounds now give the
    # ratio itself, and every other bound is widened by its derived
    # error bound.
    tally = run_full_path(kind, regression_cases(), under_test)
    assert tally.false_keeps == []
    assert tally.false_rejects == []
    assert tally.tighter == []
    assert tally.keeps > 0
    assert tally.rejects > 0


@pytest.mark.parametrize("kind", KINDS)
def test_reproducer_is_exactly_the_bar_and_a_reject(kind: Kind) -> None:
    bar = 1 + FOUND_FALSE_KEEP
    res = interval(kind, np.full(48, bar), np.ones(48), 0.99)
    for value in bounds(res):
        assert float(value).hex() == bar.hex()
    d = decide(
        PASSED,
        (float(res.lower), float(res.upper)),
        min_effect=FOUND_FALSE_KEEP,
        budget_remains=False,
        calibrated=True,
    )
    assert (d.outcome, d.rule) == ("REJECT", 3)


# Agreement with SciPy on fixed inputs


def log_data(n: int, seed: int) -> tuple[FloatArray, FloatArray]:
    rng = np.random.default_rng(seed)
    baseline = np.exp(rng.normal(0.0, 0.05, n)) * 2.0
    candidate = baseline / np.exp(rng.normal(0.04, 0.03, n))
    return baseline, candidate


def rel(a: float, b: float) -> float:
    return abs(a - b) / abs(b)


def scipy_t_interval(y: FloatArray, confidence: float) -> tuple[float, float]:
    """``mean -/+ t.isf(alpha / 2) * sem``, exponentiated.

    ``ttest_1samp(...).confidence_interval`` takes the quantile at
    ``(1 + confidence) / 2``, which rounds; at ``df = 1`` and 0.999 that
    moves a log bound near 19.4 by 2e-12. ``1 - confidence`` is exact,
    so the tail form is the accurate SciPy computation.
    """
    q = isf_t((1 - confidence) / 2, y.size - 1)
    centre = float(np.mean(y))
    half = q * sem(y)
    return math.exp(centre - half), math.exp(centre + half)


@pytest.mark.parametrize("n", [2, 3, 5, 10, 40, 200])
@pytest.mark.parametrize("confidence", [0.5, 0.9, 0.95, 0.99, 0.999])
@pytest.mark.parametrize("direction", ["lower_is_better", "higher_is_better"])
def test_t_interval_matches_scipy(
    n: int, confidence: float, direction: Direction
) -> None:
    baseline, candidate = log_data(n, seed=n)
    res = t_interval(
        baseline, candidate, direction=direction, confidence=confidence
    )
    d = np.log(paired_ratios(baseline, candidate, direction))
    low, high = scipy_t_interval(d, confidence)
    gmean = float(stats.gmean(np.exp(d)))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    assert rel(float(res.lower), low) < 1e-12
    assert rel(float(res.upper), high) < 1e-12
    assert rel(float(res.estimate), gmean) < 1e-12
    assert res.procedure == "t_log_ratio"
    assert (res.rounds, res.observations) == (n, n)


@pytest.mark.parametrize(("n", "block"), [(10, 5), (12, 5), (40, 5), (97, 4)])
def test_batch_means_interval_matches_scipy(n: int, block: int) -> None:
    baseline, candidate = log_data(n, seed=100 + n)
    res = t_interval(
        baseline,
        candidate,
        direction="lower_is_better",
        confidence=0.99,
        block=block,
    )
    m = n // block
    d = np.log(baseline / candidate)[: m * block]
    y = d.reshape(m, block).mean(axis=1)
    low, high = scipy_t_interval(y, 0.99)
    assert rel(float(res.lower), low) < 1e-12
    assert rel(float(res.upper), high) < 1e-12
    assert rel(float(res.estimate), math.exp(float(np.mean(y)))) < 1e-12
    assert res.procedure == "t_log_ratio_batch_means"
    assert (res.rounds, res.observations) == (m * block, m)


def wilcoxon_p(shifted: FloatArray) -> float:
    exact = shifted.size <= WILCOXON_EXACT_MAX_N
    result: Any = stats.wilcoxon(  # pyright: ignore[reportUnknownMemberType]
        shifted,
        method="exact" if exact else "approx",
        correction=not exact,
    )
    return float(result.pvalue)


@pytest.mark.parametrize(
    ("n", "decimals"),
    [
        (10, None),
        (20, None),
        (50, None),
        (51, None),
        (80, None),
        (150, None),
        (150, 2),
        (400, 2),
    ],
)
@pytest.mark.parametrize("confidence", [0.9, 0.99])
def test_hodges_lehmann_inverts_scipy_wilcoxon(
    n: int, decimals: int | None, confidence: float
) -> None:
    rng = np.random.default_rng(n)
    logs = rng.normal(0.04, 0.03, n)
    if decimals is not None:
        logs = np.round(logs, decimals)
    # exp of equal floats gives equal ratios: real ties.
    baseline = np.exp(logs)
    candidate = np.ones(n)
    res = hodges_lehmann_interval(
        baseline,
        candidate,
        direction="lower_is_better",
        confidence=confidence,
    )
    d = np.log(baseline)
    i, j = np.triu_indices(n)
    full = np.sort((d[i] + d[j]) / 2)
    mid = full.size // 2
    median = (
        (full[mid - 1] + full[mid]) / 2 if full.size % 2 == 0 else full[mid]
    )
    assert rel(float(res.estimate), math.exp(median)) < 1e-12
    # Walsh averages a few ulps apart (sums of tied logs in another
    # order) are one value; shifts are taken in the gaps between them.
    distinct = full[np.concatenate([[True], np.diff(full) > 1e-12])]
    alpha = 1 - confidence
    for bound, inside in ((res.lower, 1), (res.upper, -1)):
        k = int(np.argmin(np.abs(distinct - math.log(float(bound)))))
        assert rel(float(bound), math.exp(distinct[k])) < 1e-12
        # Just inside the interval the test does not reject; just
        # outside it does.
        theta_in = (distinct[k] + distinct[k + inside]) / 2
        theta_out = (distinct[k] + distinct[k - inside]) / 2
        assert wilcoxon_p(d - theta_in) > alpha
        assert wilcoxon_p(d - theta_out) <= alpha


@pytest.mark.parametrize("df", [1, 2, 3, 4, 9, 39, 47, 199])
@pytest.mark.parametrize("confidence", [0.5, 0.95, 0.99, 0.999, 0.99999])
def test_t_critical_is_certified_and_matches_scipy(
    df: int, confidence: float
) -> None:
    q = t_critical(df, confidence)
    assert rel(q, isf_t((1 - confidence) / 2, df)) < 1e-12
    assert Decimal(q) >= ref_t_quantile(df, confidence)


@pytest.mark.parametrize("confidence", [0.5, 0.95, 0.99, 0.999, 0.99999])
def test_normal_critical_is_certified_and_matches_scipy(
    confidence: float,
) -> None:
    z = normal_critical(confidence)
    assert rel(z, -ndtri((1 - confidence) / 2)) < 1e-12
    assert Decimal(z) >= ref_normal_quantile(confidence)


@pytest.mark.parametrize("df", [1, 2, 5, 40])
def test_reference_t_mass_agrees_with_scipy(df: int) -> None:
    for t in (0.1, 1.0, 2.5, 7.0):
        ref = float(ref_t_mass(df, Decimal(t)))
        assert abs(ref - (2 * stdtr(df, t) - 1)) < 1e-14


def test_reference_normal_mass_agrees_with_math_erf() -> None:
    for z in (0.1, 1.0, 2.5, 5.0):
        ref = float(ref_normal_mass(Decimal(z)))
        assert abs(ref - math.erf(z / math.sqrt(2))) < 1e-15


def test_libm_log_and_exp_are_within_the_assumed_ulps() -> None:
    rng = np.random.default_rng(7)
    xs = np.concatenate(
        [
            np.exp(rng.uniform(-5, 5, 1500)),
            1 + rng.uniform(-0.5, 0.5, 1500),
            np.array([step(1.0, k) for k in range(-50, 51)]),
        ]
    )
    worst = 0.0
    for x, got in zip(xs, np.log(xs), strict=True):
        exact = exact_log(float(x))
        if exact != 0:
            ulp = Decimal(math.ulp(float(exact)))
            worst = max(worst, float(abs(Decimal(float(got)) - exact) / ulp))
    a = rng.uniform(-20, 20, 3000)
    for x, got in zip(a, np.exp(a), strict=True):
        exact = exact_exp(Decimal(float(x)))
        ulp = Decimal(math.ulp(float(exact)))
        worst = max(worst, float(abs(Decimal(float(got)) - exact) / ulp))
    assert worst <= LIBM_ULPS


# Hand-checkable cases


def test_paired_ratios_orientation() -> None:
    b = np.array([2.0, 3.0])
    c = np.array([1.0, 4.0])
    lower = paired_ratios(b, c, "lower_is_better")
    higher = paired_ratios(b, c, "higher_is_better")
    assert lower.tolist() == [2.0, 0.75]
    assert higher.tolist() == [0.5, 4.0 / 3.0]


def test_two_round_t_interval_by_hand() -> None:
    ratios = np.array([1.02, 1.08])
    res = t_interval(
        ratios, np.ones(2), direction="lower_is_better", confidence=0.95
    )
    d1, d2 = math.log(1.02), math.log(1.08)
    q = 12.706204736174698
    half = q * abs(d2 - d1) / math.sqrt(2) / math.sqrt(2)
    centre = (d1 + d2) / 2
    assert rel(float(res.lower), math.exp(centre - half)) < 1e-12
    assert rel(float(res.upper), math.exp(centre + half)) < 1e-12
    assert rel(float(res.estimate), math.sqrt(1.02 * 1.08)) < 1e-12


def test_batch_means_by_hand() -> None:
    values = np.array([[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]])
    assert batch_means(values, 3).tolist() == [[2.0, 5.0]]
    assert batch_means(values, 1).tolist() == values.tolist()
    assert batch_means(values, 7).tolist() == [[4.0]]


@pytest.mark.parametrize(
    ("bounds_", "outcome_at_end", "regression"),
    [
        ((1.05, 1.11), "KEEP", False),
        ((1.035, 1.045), "KEEP", False),
        ((1.02, 1.08), "INCONCLUSIVE", False),
        ((0.98, 1.21), "INCONCLUSIVE", False),
        ((0.99, 1.02), "REJECT", False),
        ((0.91, 0.97), "REJECT", True),
    ],
)
def test_method_table_rows_from_observations(
    bounds_: tuple[float, float], outcome_at_end: str, regression: bool
) -> None:
    # Rounds whose t interval is the table's: d = centre + spread * z
    # with z standardised, so mean and sd are set exactly.
    n, confidence = 40, 0.99
    z = np.random.default_rng(1).normal(size=n)
    z = (z - z.mean()) / z.std(ddof=1)
    lo, hi = math.log(bounds_[0]), math.log(bounds_[1])
    half = (hi - lo) / 2
    spread = half * math.sqrt(n) / isf_t((1 - confidence) / 2, n - 1)
    ratios = np.exp((lo + hi) / 2 + spread * z)
    res = t_interval(
        ratios, np.ones(n), direction="lower_is_better", confidence=confidence
    )
    assert rel(float(res.lower), bounds_[0]) < 1e-9
    assert rel(float(res.upper), bounds_[1]) < 1e-9
    d = decide(
        PASSED,
        (float(res.lower), float(res.upper)),
        min_effect=0.03,
        budget_remains=False,
        calibrated=True,
    )
    assert d.outcome == outcome_at_end
    assert d.regression is regression


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("k", [-1, 0, 1])
@pytest.mark.parametrize("min_effect", [0.0, 0.03, 0.118, 0.124])
def test_identical_rounds_give_the_ratio_exactly(
    kind: Kind, k: int, min_effect: float
) -> None:
    value = step(1 + min_effect, k)
    res = interval(kind, np.full((3, 50), value), np.ones((3, 50)), 0.99)
    for array in bounds(res):
        assert array.shape == (3,)
        assert {float(v).hex() for v in array} == {value.hex()}


def test_identical_rows_are_exact_beside_noisy_rows() -> None:
    rng = np.random.default_rng(3)
    ratios = np.vstack([np.full(20, 1.03), np.exp(rng.normal(0, 0.01, 20))])
    res = t_interval(
        ratios,
        np.ones_like(ratios),
        direction="lower_is_better",
        confidence=0.99,
    )
    assert float(res.lower[0]) == float(res.upper[0]) == 1.03
    assert float(res.lower[1]) < float(res.estimate[1]) < float(res.upper[1])


def test_ten_thousand_replications_in_one_call() -> None:
    rng = np.random.default_rng(10_000)
    baseline = np.exp(rng.normal(0.03, 0.05, (10_000, 40)))
    candidate = np.ones((10_000, 40))
    start = time.perf_counter()
    res = t_interval(
        baseline, candidate, direction="lower_is_better", confidence=0.99
    )
    elapsed = time.perf_counter() - start
    assert res.lower.shape == (10_000,)
    assert elapsed < 5.0
    for row in (0, 4_321, 9_999):
        one = t_interval(
            baseline[row],
            candidate[row],
            direction="lower_is_better",
            confidence=0.99,
        )
        assert float(one.lower) == float(res.lower[row])
        assert float(one.upper) == float(res.upper[row])
    hl = hodges_lehmann_interval(
        baseline, candidate, direction="lower_is_better", confidence=0.99
    )
    assert hl.lower.shape == (10_000,)


def test_hodges_lehmann_chunks_agree_with_one_row_at_a_time() -> None:
    rng = np.random.default_rng(11)
    baseline = np.exp(rng.normal(0.03, 0.05, (7, 300)))
    candidate = np.ones_like(baseline)
    batch = hodges_lehmann_interval(
        baseline, candidate, direction="lower_is_better", confidence=0.99
    )
    for row in range(7):
        one = hodges_lehmann_interval(
            baseline[row],
            candidate[row],
            direction="lower_is_better",
            confidence=0.99,
        )
        assert float(one.lower) == float(batch.lower[row])
        assert float(one.upper) == float(batch.upper[row])
        assert float(one.estimate) == float(batch.estimate[row])


WALSH_BUDGET = 64 * 2**20


def test_max_hl_rounds_is_the_largest_n_that_fits() -> None:
    def one_row(n: int) -> int:
        return 32 * (n * (n + 1) // 2) + 64 * n + 512

    assert one_row(MAX_HL_ROUNDS) <= WALSH_BUDGET
    assert one_row(MAX_HL_ROUNDS + 1) > WALSH_BUDGET


@pytest.mark.parametrize(
    ("shape", "ties", "order"),
    [
        ((1, MAX_HL_ROUNDS), False, "C"),
        # Odd Walsh counts over two or more chunks: the median was a
        # view that kept a chunk's Walsh buffer alive into the next.
        ((14, 1001), False, "C"),
        ((2, MAX_HL_ROUNDS), False, "C"),
        ((4, 1449), False, "C"),
        ((3, 1500), False, "C"),
        ((40, 500), False, "C"),
        ((10_000, 40), False, "C"),
        ((20_000, 200), False, "C"),
        ((100_000, 60), False, "C"),
        ((100_000, 60), True, "F"),
    ],
)
def test_hodges_lehmann_peak_memory_is_within_the_documented_bound(
    shape: tuple[int, int], ties: bool, order: str
) -> None:
    # The docstring's bound for float64 input: the 64 MiB chunk budget,
    # 8 R n for the ratios and 3 R n of validation masks, and 24 R of
    # results.
    rng = np.random.default_rng(13)
    logs = rng.normal(0.03, 0.05, shape)
    if ties:
        logs = np.round(logs, 2)
    baseline = np.exp(logs)
    candidate = np.ones(shape)
    if order == "F":
        baseline = np.asfortranarray(baseline)
        candidate = np.asfortranarray(candidate)
    rows, n = shape
    tracemalloc.start()
    try:
        hodges_lehmann_interval(
            baseline, candidate, direction="lower_is_better", confidence=0.99
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak <= WALSH_BUDGET + 11 * rows * n + 24 * rows


@pytest.mark.parametrize("order", ["C", "F"])
def test_validation_memory_is_within_the_documented_bound(order: str) -> None:
    # Ratios (8 bytes) plus boolean masks (at most 3 bytes) per element.
    # A Fortran input once held its ratios, their mask and a C copy at
    # once: 17 bytes per element. 4 KiB covers array headers; Linux
    # measured 568 bytes of them on top of 11 bytes per element.
    rng = np.random.default_rng(17)
    baseline = np.exp(rng.normal(0.03, 0.05, (2000, 500)))
    candidate = np.ones((2000, 500))
    if order == "F":
        baseline = np.asfortranarray(baseline)
        candidate = np.asfortranarray(candidate)
    tracemalloc.start()
    try:
        ratios = paired_ratios(baseline, candidate, "lower_is_better")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert ratios.flags["C_CONTIGUOUS"]
    assert peak <= 11 * baseline.size + 4096


def chunk_rows(n: int) -> int:
    """Rows per chunk, from the per-row cost under `MAX_HL_ROUNDS`."""
    count = n * (n + 1) // 2
    per_row = 16 * count + 64 * n + 512
    return (WALSH_BUDGET - 16 * count) // per_row


def test_hodges_lehmann_rows_match_alone_across_chunk_boundaries() -> None:
    # Three real chunks at n = 301 (normal branch). The row kind cycles
    # with the chunk, so a row and the rows at its position in other
    # chunks differ in kind: untied, tied, identical. Computing the
    # critical index or the identical-row check from another chunk's
    # rows changes some row's bounds.
    n = 301
    k = chunk_rows(n)
    rows = 2 * k + 6
    rng = np.random.default_rng(18)
    ratios = np.empty((rows, n))
    for j in range(rows):
        kind = (j + j // k) % 3
        logs = rng.normal(0.03, 0.05, n)
        if kind == 0:
            ratios[j] = np.exp(logs)
        elif kind == 1:
            ratios[j] = np.exp(np.round(logs, 2))
        else:
            ratios[j] = step(1.03, j % 3 - 1)
    candidate = np.ones_like(ratios)
    batch = hodges_lehmann_interval(
        ratios, candidate, direction="lower_is_better", confidence=0.99
    )
    assert rows > 2 * k
    for j in range(rows):
        alone = hodges_lehmann_interval(
            ratios[j],
            candidate[j],
            direction="lower_is_better",
            confidence=0.99,
        )
        for got, want in zip(bounds(batch), bounds(alone), strict=True):
            assert float(got[j]).hex() == float(want).hex(), j


@pytest.mark.parametrize("n", [MAX_HL_ROUNDS + 1, 3_000, 6_000, 100_000])
def test_hodges_lehmann_refuses_rows_too_big_before_allocating(
    n: int,
) -> None:
    baseline = np.ones(n)
    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match=f"at most {MAX_HL_ROUNDS}"):
            hodges_lehmann_interval(
                baseline,
                baseline,
                direction="lower_is_better",
                confidence=0.99,
            )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * baseline.nbytes + 2**20


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("shape", [(300, 48), (4, 5, 60)])
def test_memory_layout_does_not_change_a_bit(
    kind: Kind, shape: tuple[int, ...]
) -> None:
    # A Fortran-ordered input once summed rows in another order and
    # moved some t bounds by an ulp.
    rng = np.random.default_rng(14)
    baseline = np.exp(rng.normal(0.03, 0.05, shape))
    candidate = np.exp(rng.normal(0.0, 0.05, shape))
    c_order = interval(kind, baseline, candidate, 0.99)
    f_order = interval(
        kind, np.asfortranarray(baseline), np.asfortranarray(candidate), 0.99
    )
    for got, want in zip(bounds(f_order), bounds(c_order), strict=True):
        assert got.tobytes() == want.tobytes()


MAX_ULP_STEPS = 16


def nearest_floats(exact: Exact) -> tuple[float, float]:
    """``(below, at_or_above)``: the floats either side of the bound.

    The search starts from the bound's own float approximation, a few
    ulps away at most, and fails after `MAX_ULP_STEPS` steps instead
    of walking far.
    """
    x = exact.approx()
    for _ in range(MAX_ULP_STEPS):
        if exact.cmp(x) <= 0:
            break
        x = math.nextafter(x, math.inf)
    else:
        raise AssertionError("no float at or above the bound nearby")
    for _ in range(MAX_ULP_STEPS):
        below = math.nextafter(x, -math.inf)
        if exact.cmp(below) > 0:
            return below, x
        x = below
    raise AssertionError("no float below the bound nearby")


@pytest.mark.parametrize("log_size", [100.0, 600.0])
@pytest.mark.parametrize("side", ["lower", "upper"])
def test_hodges_lehmann_eps_covers_cancelling_walsh_averages(
    log_size: float, side: str
) -> None:
    # 20 rounds with logs near -D and +D. The deciding bound is a
    # cross-pair Walsh average near log 1.03, whose log errors scale
    # with D, not with the result, so only eps covers them; the exp
    # widening scales with the result. The ratios get random mantissas:
    # the exact log of fl(exp(x)) sits within 1e-16 of x, which log
    # would round to with almost no error.
    rng = np.random.default_rng(int(log_size) + (side == "lower"))
    few, many = 3, 17
    minus_n, plus_n = (few, many) if side == "lower" else (many, few)
    logs = np.concatenate(
        [
            -log_size + rng.uniform(-0.1, 0.1, (100, minus_n)),
            log_size + rng.uniform(-0.05, 0.25, (100, plus_n)),
        ],
        axis=1,
    )
    ratios = np.exp(logs) * rng.uniform(1.0, 1.001, logs.shape)
    res = hodges_lehmann_interval(
        ratios,
        np.ones_like(ratios),
        direction="lower_is_better",
        confidence=0.99,
    )
    decided = 0
    for row in range(ratios.shape[0]):
        lower, upper = float(res.lower[row]), float(res.upper[row])
        exact_lo, exact_hi = exact_bounds(
            "hl", [float(r) for r in ratios[row]], 0.99
        )
        assert exact_lo.cmp(lower) >= 0
        assert exact_hi.cmp(upper) <= 0
        # The bar one ulp from the exact bound, on the side where a
        # bound tighter than exact decides wrongly.
        if side == "lower":
            bar = nearest_floats(exact_lo)[1]
        else:
            bar = nearest_floats(exact_hi)[0]
        if not 1.0 < bar < 2.0:
            continue
        min_effect = bar - 1.0
        assert 1.0 + min_effect == bar
        d = decide(
            PASSED,
            (lower, upper),
            min_effect=min_effect,
            budget_remains=False,
            calibrated=True,
        )
        assert d.rule != (2 if side == "lower" else 3)
        decided += 1
    assert decided >= 50


def test_centred_squares_bound_is_not_below_exact() -> None:
    rng = np.random.default_rng(15)
    y = np.concatenate(
        [
            rng.normal(0.03, 0.02, (400, 40)),
            rng.normal(100.0, 1e-9, (400, 40)),
            rng.normal(-0.5, 1.0, (400, 7)).repeat(6, axis=1)[:, :40],
        ]
    )
    mean = np.sum(y, axis=-1) / y.shape[-1]
    bound = _centred_squares_upper(y, mean)
    for row in range(y.shape[0]):
        centre = Fraction(float(mean[row]))
        exact = sum(
            ((Fraction(float(v)) - centre) ** 2 for v in y[row]), Fraction(0)
        )
        assert Fraction(float(bound[row])) >= exact


def test_up_is_not_below_the_exact_value_of_its_chain() -> None:
    # x = fl(fl(fl(a * b) + c) / d): three roundings of nonnegative
    # exact inputs; about half the chains round below the exact value.
    rng = np.random.default_rng(16)
    a, b, c, d = (rng.uniform(0.1, 10.0, 4000) for _ in range(4))
    x = (a * b + c) / d
    bound = _up(x, 3)
    below = 0
    for i in range(x.size):
        exact = (
            Fraction(float(a[i])) * Fraction(float(b[i]))
            + Fraction(float(c[i]))
        ) / Fraction(float(d[i]))
        below += Fraction(float(x[i])) < exact
        assert Fraction(float(bound[i])) >= exact
    assert below > 0


def test_leading_batch_axes_keep_their_shape() -> None:
    rng = np.random.default_rng(12)
    baseline = np.exp(rng.normal(0.0, 0.05, (2, 3, 12)))
    candidate = np.ones_like(baseline)
    for kind in KINDS:
        res = interval(kind, baseline, candidate, 0.9)
        for array in bounds(res):
            assert array.shape == (2, 3)


# Invalid input


GOOD = np.ones(12)


@pytest.mark.parametrize(
    ("call", "named"),
    [
        (lambda: paired_ratios(GOOD, np.ones(11), "lower_is_better"), "shape"),
        (lambda: paired_ratios(1.0, 1.0, "lower_is_better"), "rounds axis"),
        (
            lambda: paired_ratios(
                np.ones((3, 0)), np.ones((3, 0)), "lower_is_better"
            ),
            "at least one round",
        ),
        (
            lambda: t_interval(
                np.ones(0),
                np.ones(0),
                direction="lower_is_better",
                confidence=0.9,
            ),
            "at least one round",
        ),
        (
            lambda: hodges_lehmann_interval(
                np.ones(0),
                np.ones(0),
                direction="lower_is_better",
                confidence=0.9,
            ),
            "at least one round",
        ),
        (
            lambda: paired_ratios(
                np.full(12, -(2**60)), GOOD, "lower_is_better"
            ),
            "2\\*\\*53",
        ),
        (
            lambda: paired_ratios(
                np.full(12, 2**60, dtype=np.uint64), GOOD, "lower_is_better"
            ),
            "2\\*\\*53",
        ),
        (
            lambda: paired_ratios(GOOD, GOOD, cast("Direction", "faster")),
            "'faster'",
        ),
        (
            lambda: paired_ratios(GOOD * 0, GOOD, "lower_is_better"),
            "0.0",
        ),
        (
            lambda: paired_ratios(GOOD * np.nan, GOOD, "lower_is_better"),
            "nan",
        ),
        (
            lambda: paired_ratios(GOOD * -1, GOOD, "lower_is_better"),
            "-1.0",
        ),
        (
            lambda: paired_ratios(GOOD > 0, GOOD, "lower_is_better"),
            "bool",
        ),
        (
            lambda: paired_ratios(np.full(12, 2**60), GOOD, "lower_is_better"),
            "2\\*\\*53",
        ),
        (
            lambda: paired_ratios(
                GOOD * 1e300, GOOD * 1e-300, "lower_is_better"
            ),
            "normal float64 range",
        ),
        (
            lambda: t_interval(
                GOOD, GOOD, direction="lower_is_better", confidence=1.0
            ),
            "1.0",
        ),
        (
            lambda: t_interval(
                GOOD,
                GOOD,
                direction="lower_is_better",
                confidence=cast("float", 1),
            ),
            "got 1",
        ),
        (
            lambda: t_interval(
                GOOD, GOOD, direction="lower_is_better", confidence=math.nan
            ),
            "nan",
        ),
        (
            lambda: t_interval(
                GOOD[:1],
                GOOD[:1],
                direction="lower_is_better",
                confidence=0.9,
            ),
            "at least 2 observations",
        ),
        (
            lambda: t_interval(
                GOOD,
                GOOD,
                direction="lower_is_better",
                confidence=0.9,
                block=7,
            ),
            "blocks of 7",
        ),
        (
            lambda: t_interval(
                GOOD,
                GOOD,
                direction="lower_is_better",
                confidence=0.9,
                block=cast("int", True),
            ),
            "True",
        ),
        (
            lambda: t_interval(
                GOOD,
                GOOD,
                direction="lower_is_better",
                confidence=0.9,
                block=0,
            ),
            "got 0",
        ),
        (
            lambda: hodges_lehmann_interval(
                GOOD[:5],
                GOOD[:5],
                direction="lower_is_better",
                confidence=0.99,
            ),
            "too few",
        ),
        (lambda: batch_means(np.arange(4), 2), "int64"),
        (lambda: batch_means(np.ones(4), 5), "got 5"),
        (lambda: t_critical(0, 0.9), "got 0"),
        (lambda: normal_critical(0.0), "0.0"),
    ],
)
def test_invalid_input_raises(call: Callable[[], object], named: str) -> None:
    with pytest.raises(ValueError, match=named):
        call()


@pytest.mark.parametrize(
    ("value", "refused"),
    [
        (2**53, False),
        (2**53 + 1, True),
        (-(2**53) - 1, True),
    ],
)
def test_integer_limit_is_exactly_two_to_the_53(
    value: int, refused: bool
) -> None:
    baseline = np.full(12, value, dtype=np.int64)
    candidate = np.ones(12, dtype=np.int64)
    if refused:
        with pytest.raises(ValueError, match="2\\*\\*53"):
            paired_ratios(baseline, candidate, "lower_is_better")
    else:
        ratios = paired_ratios(baseline, candidate, "lower_is_better")
        assert ratios.tolist() == [float(2**53)] * 12


def test_integer_measurements_are_exact() -> None:
    res = t_interval(
        np.full(10, 1030, dtype=np.int64),
        np.full(10, 1000, dtype=np.int64),
        direction="lower_is_better",
        confidence=0.99,
    )
    assert float(res.lower) == float(res.upper) == 1030 / 1000


# Properties


pool_values = st.sampled_from(
    [step(1.03, k) for k in range(-2, 3)] + [1.0, 0.97, 1.5]
)
wide_values = st.floats(0.05, 20.0, allow_subnormal=False)


@st.composite
def measurements(
    draw: st.DrawFn, min_n: int = 10, max_n: int = 60
) -> tuple[FloatArray, FloatArray]:
    n = draw(st.integers(min_n, max_n))
    value = draw(st.sampled_from([pool_values, wide_values]))
    baseline = draw(st.lists(value, min_size=n, max_size=n))
    if draw(st.booleans()):
        candidate = [1.0] * n
    else:
        candidate = draw(st.lists(wide_values, min_size=n, max_size=n))
    return np.array(baseline), np.array(candidate)


SETTINGS = settings(max_examples=150, derandomize=True, deadline=None)


@SETTINGS
@given(measurements(), st.sampled_from(KINDS), st.sampled_from([0.9, 0.99]))
def test_bounds_are_never_tighter_than_exact(
    data: tuple[FloatArray, FloatArray], kind: Kind, confidence: float
) -> None:
    baseline, candidate = data
    res = interval(kind, baseline, candidate, confidence)
    ratios = paired_ratios(baseline, candidate, "lower_is_better")
    exact_lo, exact_hi = exact_bounds(
        kind, [float(r) for r in ratios], confidence
    )
    assert exact_lo.cmp(float(res.lower)) >= 0
    assert exact_hi.cmp(float(res.upper)) <= 0
    assert 0 < float(res.lower) <= float(res.estimate) <= float(res.upper)


@SETTINGS
@given(measurements(), st.sampled_from(KINDS))
def test_swapping_arms_inverts_the_interval(
    data: tuple[FloatArray, FloatArray], kind: Kind
) -> None:
    baseline, candidate = data
    res = interval(kind, baseline, candidate, 0.95)
    swapped = interval(kind, candidate, baseline, 0.95)
    assert rel(float(swapped.lower), 1 / float(res.upper)) < 1e-9
    assert rel(float(swapped.upper), 1 / float(res.lower)) < 1e-9
    assert rel(float(swapped.estimate), 1 / float(res.estimate)) < 1e-9


@SETTINGS
@given(
    measurements(),
    st.sampled_from(KINDS),
    st.floats(0.01, 100.0),
    st.integers(-20, 20),
)
def test_scaling_both_arms_leaves_the_interval(
    data: tuple[FloatArray, FloatArray], kind: Kind, factor: float, power: int
) -> None:
    baseline, candidate = data
    res = interval(kind, baseline, candidate, 0.95)
    scaled = interval(kind, baseline * factor, candidate * factor, 0.95)
    for got, want in zip(bounds(scaled), bounds(res), strict=True):
        assert rel(float(got), float(want)) < 1e-9
    exact = interval(kind, baseline * 2.0**power, candidate * 2.0**power, 0.95)
    for got, want in zip(bounds(exact), bounds(res), strict=True):
        assert float(got) == float(want)


@SETTINGS
@given(measurements(), st.sampled_from(KINDS), st.floats(0.1, 10.0))
def test_scaling_the_candidate_scales_the_interval_inversely(
    data: tuple[FloatArray, FloatArray], kind: Kind, factor: float
) -> None:
    baseline, candidate = data
    res = interval(kind, baseline, candidate, 0.95)
    scaled = interval(kind, baseline, candidate * factor, 0.95)
    for got, want in zip(bounds(scaled), bounds(res), strict=True):
        assert rel(float(got), float(want) / factor) < 1e-9


@SETTINGS
@given(measurements(), st.sampled_from(KINDS))
def test_higher_is_better_is_the_mirror_of_lower(
    data: tuple[FloatArray, FloatArray], kind: Kind
) -> None:
    baseline, candidate = data
    lower = interval(kind, baseline, candidate, 0.95, "lower_is_better")
    higher = interval(kind, candidate, baseline, 0.95, "higher_is_better")
    for got, want in zip(bounds(higher), bounds(lower), strict=True):
        assert float(got) == float(want)


@SETTINGS
@given(
    st.lists(measurements(min_n=12, max_n=12), min_size=1, max_size=4),
    st.sampled_from(KINDS),
)
def test_a_batch_row_equals_the_same_row_alone(
    rows: list[tuple[FloatArray, FloatArray]], kind: Kind
) -> None:
    baseline = np.stack([b for b, _ in rows])
    candidate = np.stack([c for _, c in rows])
    batch = interval(kind, baseline, candidate, 0.9)
    for i, (b, c) in enumerate(rows):
        one = interval(kind, b, c, 0.9)
        for got, want in zip(bounds(batch), bounds(one), strict=True):
            assert float(got[i]) == float(want)


@SETTINGS
@given(measurements(), st.sampled_from(KINDS))
def test_higher_confidence_never_narrows(
    data: tuple[FloatArray, FloatArray], kind: Kind
) -> None:
    baseline, candidate = data
    narrow = interval(kind, baseline, candidate, 0.9)
    wide = interval(kind, baseline, candidate, 0.99)
    assert float(wide.lower) <= float(narrow.lower)
    assert float(wide.upper) >= float(narrow.upper)
