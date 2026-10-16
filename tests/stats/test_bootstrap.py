import decimal
import math
import statistics
import tracemalloc
from collections.abc import Callable
from fractions import Fraction
from typing import Any, cast, get_args

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from numpy.typing import NDArray
from scipy import stats  # pyright: ignore[reportMissingTypeStubs]

from perfproof.schema import Direction
from perfproof.stats.bootstrap import (
    DEFAULT_MAX_BYTES,
    BootstrapIntervals,
    RatioInterval,
    _acceleration_bounds,  # pyright: ignore[reportPrivateUsage]
    _bca_tails,  # pyright: ignore[reportPrivateUsage]
    _median_of_sorted,  # pyright: ignore[reportPrivateUsage]
    bootstrap_median_ratio,
)
from perfproof.stats.decision import interval_rules

U = 2.0**-53
UF = Fraction(1, 2**53)
DIRECTIONS: tuple[Direction, ...] = get_args(Direction)
F64 = NDArray[np.float64]


def run(
    baseline: Any,
    candidate: Any,
    direction: Direction = "lower_is_better",
    seed: int = 0,
    **kwargs: Any,
) -> BootstrapIntervals:
    return bootstrap_median_ratio(
        baseline,
        candidate,
        direction,
        rng=np.random.default_rng(seed),
        **kwargs,
    )


def intervals(result: BootstrapIntervals) -> tuple[RatioInterval, ...]:
    return (result.percentile, result.bca)


def assert_identical(a: BootstrapIntervals, b: BootstrapIntervals) -> None:
    for x, y in zip(intervals(a), intervals(b), strict=True):
        for name in ("estimate", "lower", "upper"):
            np.testing.assert_array_equal(
                getattr(x, name), getattr(y, name), strict=True
            )


def exact_median(values: list[Fraction]) -> Fraction:
    return statistics.median(sorted(values))


def draw_indices(
    rng: np.random.Generator, n_b: int, n_c: int, resamples: int
) -> NDArray[np.int64]:
    """One row's resample indices, in the layout the module documents.

    One ``(B, n_b + n_c)`` draw, baseline columns first.
    """
    low = np.array([0] * n_b + [n_b] * n_c)
    high = np.array([n_b] * n_b + [n_b + n_c] * n_c)
    return rng.integers(low, high, size=(resamples, n_b + n_c))


def exact_replicates(
    baseline: list[float],
    candidate: list[float],
    resamples: int,
    seed: int | np.random.Generator,
) -> list[Fraction]:
    """Replicate ratios ``median(c*) / median(b*)`` in exact arithmetic.

    ``seed`` is a seed for a fresh generator, or a generator to draw
    from as the module would.
    """
    n_b, n_c = len(baseline), len(candidate)
    rng = (
        seed
        if isinstance(seed, np.random.Generator)
        else np.random.default_rng(seed)
    )
    idx = draw_indices(rng, n_b, n_c, resamples)
    x = [Fraction(v) for v in [*baseline, *candidate]]
    out: list[Fraction] = []
    for row in idx.tolist():
        m_b = exact_median([x[i] for i in row[:n_b]])
        m_c = exact_median([x[i] for i in row[n_b:]])
        out.append(m_c / m_b)
    return sorted(out)


positive = st.floats(
    min_value=1e-3, max_value=1e3, allow_nan=False, allow_infinity=False
)
tied = st.sampled_from([1.0, 1.5, 2.0, 3.0, 7.0])
values = st.one_of(positive, tied)
arm = st.lists(values, min_size=2, max_size=9)


@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize(
    "min_effect", [0.0, 0.03, 0.118, 0.124, 0.4730308035801494]
)
@pytest.mark.parametrize("n", [2, 3, 48])
def test_rounds_all_at_the_bar_give_the_bar_exactly(
    direction: Direction, min_effect: float, n: int
) -> None:
    bar = 1.0 + min_effect
    fast, slow = (
        ([bar] * n, [1.0] * n)
        if direction == "higher_is_better"
        else ([1.0] * n, [bar] * n)
    )
    result = run(slow, fast, direction, resamples=999)
    for iv in intervals(result):
        assert float(iv.estimate) == bar
        assert float(iv.lower) == bar
        assert float(iv.upper) == bar
        rules = interval_rules(
            iv.lower, iv.upper, min_effect=min_effect, budget_remains=False
        )
        assert int(rules.rule) == 3


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_constant_arms_give_one_division_with_no_drift(
    direction: Direction,
) -> None:
    result = run([3.0] * 7, [7.0] * 5, direction, resamples=999)
    expected = 7.0 / 3.0 if direction == "higher_is_better" else 3.0 / 7.0
    for iv in intervals(result):
        assert float(iv.estimate) == expected
        assert float(iv.lower) == expected
        assert float(iv.upper) == expected


def test_identical_constant_arms_give_exactly_one() -> None:
    result = run([0.1] * 10, [0.1] * 10, resamples=999)
    for iv in intervals(result):
        assert (float(iv.lower), float(iv.upper)) == (1.0, 1.0)


def test_constant_row_is_exact_inside_a_batch() -> None:
    g = np.random.default_rng(3)
    base = g.lognormal(size=(3, 12))
    cand = g.lognormal(size=(3, 12))
    base[1] = 2.5
    cand[1] = 1.25
    result = run(base, cand, resamples=999)
    for iv in intervals(result):
        assert (float(iv.lower[1]), float(iv.upper[1])) == (2.0, 2.0)
        assert iv.lower[0] < iv.upper[0]
        assert iv.lower[2] < iv.upper[2]


def test_hand_checkable_two_point_arms() -> None:
    # Baseline [1, 3] resamples to medians 1, 2, 3 with probabilities
    # 1/4, 1/2, 1/4; the candidate is constant 2. Each extreme holds a
    # quarter of the mass, so the 95% percentile bounds are 2/3 and 2.
    result = run([1.0, 3.0], [2.0, 2.0], "higher_is_better")
    pct = result.percentile
    assert float(pct.estimate) == 1.0
    assert float(pct.lower) == (2.0 / 3.0) * (1.0 - 5 * U)
    assert float(pct.upper) == 2.0 * (1.0 + 6 * U)
    flipped = run([1.0, 3.0], [2.0, 2.0], "lower_is_better").percentile
    assert float(flipped.lower) == (1.0 / 2.0) * (1.0 - 5 * U)
    assert float(flipped.upper) == (1.0 / (2.0 / 3.0)) * (1.0 + 6 * U)


def test_percentile_index_rule_at_the_smallest_resample_count() -> None:
    # B = 39 at 95%: j = floor(40 * 0.025) = 1, the extreme replicates.
    base = [1.0, 2.0, 4.0, 8.0, 16.0]
    cand = [3.0, 5.0, 9.0]
    reps = exact_replicates(base, cand, 39, seed=11)
    pct = run(base, cand, "higher_is_better", seed=11, resamples=39)
    assert Fraction(float(pct.percentile.lower)) <= reps[0]
    assert Fraction(float(pct.percentile.upper)) >= reps[-1]
    assert Fraction(float(pct.percentile.lower)) > reps[0] * (1 - 11 * UF)
    assert Fraction(float(pct.percentile.upper)) < reps[-1] * (1 + 12 * UF)


@settings(max_examples=150, deadline=None)
@given(
    base=arm,
    cand=arm,
    seed=st.integers(0, 2**32 - 1),
    direction=st.sampled_from(DIRECTIONS),
)
def test_bounds_are_never_more_favourable_than_exact(
    base: list[float], cand: list[float], seed: int, direction: Direction
) -> None:
    # B = 199 at 90%: j = floor(200 (1 - 0.9) / 2) = 9, not 10, because
    # the float64 nearest 0.9 is above 9 / 10. The derived widening
    # keeps each bound on the conservative side of the exact order
    # statistic, and within (1 + gamma_5)(1 + 6u) - 1 < 12u of it.
    b, j = 199, 9
    reps = exact_replicates(base, cand, b, seed)
    if direction == "lower_is_better":
        reps = sorted(1 / r for r in reps)
    exact_lo, exact_hi = reps[j - 1], reps[b - j]
    result = run(base, cand, direction, seed, resamples=b, confidence=0.9)
    lo = Fraction(float(result.percentile.lower))
    hi = Fraction(float(result.percentile.upper))
    constant = len(set(base)) == 1 and len(set(cand)) == 1
    if constant:
        assert lo == hi == Fraction(float(result.percentile.estimate))
        return
    assert lo <= exact_lo
    assert hi >= exact_hi
    assert lo >= exact_lo * (1 - 11 * UF)
    assert hi <= exact_hi * (1 + 12 * UF)
    for bound in (result.bca.lower, result.bca.upper):
        if math.isnan(float(bound)):
            continue
        widened = Fraction(float(bound))
        assert any(
            r * (1 - 11 * UF) <= widened <= r * (1 + 12 * UF) for r in reps
        )


@settings(max_examples=100, deadline=None)
@given(base=arm, cand=arm, seed=st.integers(0, 2**32 - 1))
def test_direction_flip_inverts_the_interval(
    base: list[float], cand: list[float], seed: int
) -> None:
    # Both directions select the same replicate y. One gives
    # fl(y (1 + 6u)), the other fl(fl(1 / y)(1 - 5u)); their float
    # product is (1 + 6u)(1 - 5u)(1 + theta_4), within 6u of 1. The
    # estimates are fl(m_c / m_b) and fl(m_b / m_c): within gamma_3.
    hi = run(base, cand, "higher_is_better", seed, resamples=499)
    lo = run(base, cand, "lower_is_better", seed, resamples=499)
    assert abs(float(hi.percentile.estimate * lo.percentile.estimate) - 1) <= (
        3.0000001 * U
    )
    for h, low in zip(intervals(hi), intervals(lo), strict=True):
        assert np.isnan(h.lower) == np.isnan(low.upper)
        if np.isnan(h.lower):
            continue
        assert abs(float(h.lower * low.upper) - 1) <= 6 * U
        assert abs(float(h.upper * low.lower) - 1) <= 6 * U


@settings(max_examples=100, deadline=None)
@given(
    base=arm,
    cand=arm,
    seed=st.integers(0, 2**32 - 1),
    k=st.integers(-30, 30),
    direction=st.sampled_from(DIRECTIONS),
)
def test_scaling_both_arms_by_a_power_of_two_changes_nothing(
    base: list[float],
    cand: list[float],
    seed: int,
    k: int,
    direction: Direction,
) -> None:
    c = 2.0**k
    plain = run(base, cand, direction, seed, resamples=499)
    scaled = run(
        np.multiply(base, c),
        np.multiply(cand, c),
        direction,
        seed,
        resamples=499,
    )
    assert_identical(plain, scaled)


@settings(max_examples=100, deadline=None)
@given(
    base=arm,
    cand=arm,
    seed=st.integers(0, 2**32 - 1),
    c=st.floats(min_value=1e-3, max_value=1e3),
    direction=st.sampled_from(DIRECTIONS),
)
def test_scaling_both_arms_leaves_the_percentile_interval(
    base: list[float],
    cand: list[float],
    seed: int,
    c: float,
    direction: Direction,
) -> None:
    # Scaling rounds each input once, which moves each exact replicate
    # by at most gamma_2. Each computed bound lies within gamma_5 of
    # its exact order statistic after the same widening factor, and
    # the test's quotient rounds once: 2 + 5 + 5 + 1 = 13u to first
    # order, so 16u covers the second-order terms.
    plain = run(base, cand, direction, seed, resamples=499).percentile
    scaled = run(
        np.multiply(base, c),
        np.multiply(cand, c),
        direction,
        seed,
        resamples=499,
    ).percentile
    for name in ("estimate", "lower", "upper"):
        ratio = float(getattr(scaled, name) / getattr(plain, name))
        assert abs(ratio - 1) <= 16 * U


@settings(max_examples=100, deadline=None)
@given(
    base=arm,
    cand=arm,
    seed=st.integers(0, 2**32 - 1),
    k=st.integers(-30, 30),
)
def test_scaling_the_candidate_scales_lower_is_better_by_its_inverse(
    base: list[float], cand: list[float], seed: int, k: int
) -> None:
    c = 2.0**k
    plain = run(base, cand, "lower_is_better", seed, resamples=499)
    scaled = run(
        base, np.multiply(cand, c), "lower_is_better", seed, resamples=499
    )
    for p, s in zip(intervals(plain), intervals(scaled), strict=True):
        for name in ("estimate", "lower", "upper"):
            np.testing.assert_array_equal(
                getattr(s, name), getattr(p, name) / c, strict=True
            )


@settings(max_examples=100, deadline=None)
@given(
    base=arm,
    cand=arm,
    seed=st.integers(0, 2**32 - 1),
    c=st.floats(min_value=1e-3, max_value=1e3),
)
def test_scaling_the_candidate_by_any_factor_scales_the_percentile(
    base: list[float], cand: list[float], seed: int, c: float
) -> None:
    # As for scaling both arms, with the test's own division by c as
    # one more rounding.
    plain = run(base, cand, "lower_is_better", seed, resamples=499)
    scaled = run(
        base, np.multiply(cand, c), "lower_is_better", seed, resamples=499
    )
    p, s = plain.percentile, scaled.percentile
    for name in ("estimate", "lower", "upper"):
        ratio = float(getattr(s, name) * c / getattr(p, name))
        assert abs(ratio - 1) <= 16 * U


def mc_tolerance(a: F64, b: F64, ranks: float, resamples: int) -> float:
    """Allowed gap between the means of two sets of log bounds.

    Four standard errors of the difference of the means, plus the
    shift from the two procedures picking order statistics up to
    ``ranks`` apart: one rank moves a quantile by about ``sd /
    sqrt(B t (1 - t))``, bounded here with ``t = 0.005``, the smallest
    tail mass either interval uses on the data below.
    """
    sd_a, sd_b = float(np.std(a, ddof=1)), float(np.std(b, ddof=1))
    se = math.sqrt(sd_a**2 / a.size + sd_b**2 / b.size)
    spacing = max(sd_a, sd_b) / math.sqrt(resamples * 0.005 * 0.995)
    return 4.0 * se + ranks * spacing + 7 * U


def test_swapping_arms_inverts_the_interval_in_distribution() -> None:
    # Swapping changes which arm draws which indices, so the intervals
    # agree in distribution, not draw by draw.
    rows, b = 40, 4000
    g = np.random.default_rng(8)
    base = np.broadcast_to(g.lognormal(0.0, 0.3, 25), (rows, 25))
    cand = np.broadcast_to(g.lognormal(0.1, 0.3, 20), (rows, 20))
    ab = run(base, cand, "higher_is_better", 1, resamples=b)
    ba = run(cand, base, "higher_is_better", 2, resamples=b)
    for x, y in zip(intervals(ab), intervals(ba), strict=True):
        np.testing.assert_array_equal(x.estimate * y.estimate, 1.0)
        for mine, swapped in ((x.lower, y.upper), (x.upper, y.lower)):
            a, s = np.log(mine), -np.log(swapped)
            assert abs(a.mean() - s.mean()) <= mc_tolerance(a, s, 0, b)


def log_median_ratio(baseline: F64, candidate: F64, axis: int = -1) -> F64:
    return np.log(
        np.median(candidate, axis=axis) / np.median(baseline, axis=axis)
    )


def test_agrees_with_scipy_bootstrap_within_monte_carlo_error() -> None:
    # scipy.stats.bootstrap resamples each sample independently, like
    # this module, and its BCa uses the same two-sample jackknife. Its
    # bounds interpolate between order statistics where this module
    # picks one at floor((B + 1) t), at most 2 ranks away. Each side
    # runs 30 independent seeds; the means of the log bounds must agree
    # within `mc_tolerance`. The data are skewed enough that BCa and
    # percentile differ by many times that tolerance, which the last
    # assertion checks so that the agreement means something.
    seeds, b = 30, 10_000
    g = np.random.default_rng(20261016)
    base = g.lognormal(0.0, 0.35, 40)
    cand = g.lognormal(0.1, 0.35, 30)
    mine = run(
        np.broadcast_to(base, (seeds, 40)),
        np.broadcast_to(cand, (seeds, 30)),
        "higher_is_better",
        seed=1,
        resamples=b,
    )
    bootstrap = cast(
        Callable[..., Any],
        stats.bootstrap,  # pyright: ignore[reportUnknownMemberType]
    )
    theirs: dict[str, tuple[F64, F64]] = {}
    for method in ("percentile", "BCa"):
        lo: list[float] = []
        hi: list[float] = []
        for s in range(seeds):
            ci = bootstrap(
                (base, cand),
                log_median_ratio,
                n_resamples=b,
                method=method,
                rng=np.random.default_rng(1000 + s),
            ).confidence_interval
            lo.append(float(ci.low))
            hi.append(float(ci.high))
        theirs[method] = (np.array(lo), np.array(hi))
    pairs = (
        (mine.percentile, theirs["percentile"]),
        (mine.bca, theirs["BCa"]),
    )
    for iv, (t_lo, t_hi) in pairs:
        for m, t in ((np.log(iv.lower), t_lo), (np.log(iv.upper), t_hi)):
            assert abs(m.mean() - t.mean()) <= mc_tolerance(m, t, 2, b)
    p_lo, _ = theirs["percentile"]
    bca_lo = np.log(mine.bca.lower)
    assert abs(bca_lo.mean() - p_lo.mean()) > 5 * mc_tolerance(
        bca_lo, p_lo, 2, b
    )


def to_decimal(value: Fraction) -> decimal.Decimal:
    return decimal.Decimal(value.numerator) / decimal.Decimal(
        value.denominator
    )


def exact_acceleration(
    base: list[float], cand: list[float]
) -> decimal.Decimal | None:
    """The two-sample jackknife acceleration to 60 significant digits.

    The jackknife values ``log(r_(i) / r)`` come from exact rational
    medians and ``Decimal.ln``, so ulp-level spreads are resolved.
    ``None`` where neither arm's jackknife has spread.
    """
    with decimal.localcontext(decimal.Context(prec=60)):
        num = den = decimal.Decimal(0)
        for arm in (0, 1):
            sample = (base, cand)[arm]
            n = len(sample)
            whole = exact_median([Fraction(v) for v in sample])
            thetas: list[decimal.Decimal] = []
            for i in range(n):
                kept = exact_median(
                    [Fraction(v) for k, v in enumerate(sample) if k != i]
                )
                ratio = whole / kept if arm == 0 else kept / whole
                thetas.append(to_decimal(ratio).ln())
            mean = sum(thetas, decimal.Decimal(0)) / n
            devs = [(n - 1) * (mean - t) for t in thetas]
            num += sum((d**3 for d in devs), decimal.Decimal(0)) / n**3
            den += sum((d**2 for d in devs), decimal.Decimal(0)) / n**2
        if den == 0:
            return None
        return num / (6 * den * den.sqrt())


def independent_bca(
    base: list[float],
    cand: list[float],
    reps: list[Fraction],
    confidence: float,
    sign: float = 1.0,
) -> tuple[Fraction | None, Fraction | None] | None:
    """BCa order statistics from the textbook formulas, written apart.

    ``reps`` are the exact sorted replicate ratios ``c / b``. The
    counts behind ``z0`` are exact, and so is the acceleration to 60
    digits (`exact_acceleration`); only the normal quantile and CDF
    are evaluated in float64, whose error moves a level by far less
    than the module's own margins. ``sign = -1`` flips the
    acceleration, to show the test can tell. ``None`` where BCa is
    undefined (``z0`` infinite or no jackknife spread); a bound is
    ``None`` where its ``j`` falls outside ``[1, B]``.
    """
    b = len(reps)
    est = exact_median([Fraction(v) for v in cand]) / exact_median(
        [Fraction(v) for v in base]
    )
    share = (sum(r < est for r in reps) + sum(r <= est for r in reps)) / (
        2 * b
    )
    accel = exact_acceleration(base, cand)
    if share in (0, 1) or accel is None:
        return None
    normal = statistics.NormalDist()
    z0 = normal.inv_cdf(share)
    a = sign * float(accel)
    z = normal.inv_cdf((1 - confidence) / 2)
    t_lo = normal.cdf(z0 + (z0 + z) / (1 - a * (z0 + z)))
    t_hi = 1 - normal.cdf(z0 + (z0 - z) / (1 - a * (z0 - z)))
    j_lo = math.floor((b + 1) * t_lo)
    j_hi = math.floor((b + 1) * t_hi)
    return (
        reps[j_lo - 1] if 1 <= j_lo <= b else None,
        reps[b - j_hi] if 1 <= j_hi <= b else None,
    )


def assert_bca_conservative(
    base: list[float],
    cand: list[float],
    reps: list[Fraction],
    confidence: float,
    direction: Direction,
    result: RatioInterval,
) -> None:
    """A finite BCa interval from the module contains the exact one.

    NaN is always allowed: it claims nothing. A finite interval needs
    the exact interval to exist, and must not be narrower on either
    side. ``reps`` are the canonical exact replicates.
    """
    lower, upper = float(result.lower), float(result.upper)
    assert math.isnan(lower) == math.isnan(upper)
    if math.isnan(lower):
        return
    expected = independent_bca(base, cand, reps, confidence)
    assert expected is not None
    lo, hi = expected
    assert lo is not None
    assert hi is not None
    assert lo <= hi
    if direction == "lower_is_better":
        lo, hi = 1 / hi, 1 / lo
    assert Fraction(lower) <= lo
    assert Fraction(upper) >= hi


def test_bca_matches_an_independent_derivation() -> None:
    g = np.random.default_rng(12)
    cases = [
        (
            [1.0, 1.1, 1.2, 5.0, 9.0, 1.05, 1.3],
            [0.9, 1.0, 4.0, 7.5, 0.95],
        ),
        (
            g.lognormal(0.0, 1.0, 15).tolist(),
            g.lognormal(0.0, 1.0, 12).tolist(),
        ),
        (
            g.lognormal(0.0, 0.3, 40).tolist(),
            g.lognormal(0.1, 0.3, 30).tolist(),
        ),
    ]
    b, seed, confidence = 1999, 5, 0.95
    sign_matters = False
    for base, cand in cases:
        reps = exact_replicates(base, cand, b, seed)
        expected = independent_bca(base, cand, reps, confidence)
        assert expected is not None
        lo, hi = expected
        assert lo is not None
        assert hi is not None
        result = run(base, cand, "higher_is_better", seed, resamples=b)
        got_lo = Fraction(float(result.bca.lower))
        got_hi = Fraction(float(result.bca.upper))
        assert lo * (1 - 11 * UF) <= got_lo <= lo
        assert hi <= got_hi <= hi * (1 + 12 * UF)
        flipped = independent_bca(base, cand, reps, confidence, sign=-1.0)
        sign_matters |= flipped != (lo, hi)
    assert sign_matters


def test_bca_is_nan_where_its_order_statistic_does_not_exist() -> None:
    # At 99.9% with B = 4000 the BCa upper level here needs j < 1, an
    # order statistic beyond the largest replicate. Clipping it would
    # return the percentile interval as if it were BCa's own.
    base = [
        0.6696730219731747,
        0.5157260848230018,
        0.8832201304874078,
        1.2339527307455274,
        1.7647751107205916,
    ]
    cand = [
        1.0563855255388388,
        0.7585673729054255,
        0.6754405207298267,
        1.454079254256084,
        2.2645850051852783,
    ]
    result = run(
        base, cand, "higher_is_better", 0, resamples=4000, confidence=0.999
    )
    assert np.isnan(result.bca.lower)
    assert np.isnan(result.bca.upper)
    assert result.percentile.lower < result.percentile.upper


def ulps_around(center: float) -> st.SearchStrategy[list[float]]:
    """Arms whose values sit within a few ulps of ``center``."""
    step = math.ulp(center)
    return st.lists(
        st.integers(-3, 3).map(lambda k: center + k * step),
        min_size=2,
        max_size=10,
    )


near_ties = st.tuples(
    ulps_around(1.0),
    st.sampled_from([1.0, 1.035, 1.14, 2.143529201646492]).flatmap(
        ulps_around
    ),
)


@settings(max_examples=80, deadline=None)
@given(
    arms=st.one_of(st.tuples(arm, arm), near_ties),
    seed=st.integers(0, 2**32 - 1),
    confidence=st.sampled_from([0.95, 0.99, 0.998, 0.999]),
    direction=st.sampled_from(DIRECTIONS),
)
def test_bca_is_never_more_favourable_than_exact(
    arms: tuple[list[float], list[float]],
    seed: int,
    confidence: float,
    direction: Direction,
) -> None:
    # Covers ulp-level near ties, where rounded replicates and a rounded
    # estimate disagree with exact arithmetic about which side they are
    # on, and levels whose order statistic does not exist at this B,
    # which must give NaN rather than the extreme replicate.
    base, cand = arms
    if len(set(base)) == 1 and len(set(cand)) == 1:
        return
    b = 1999
    reps = exact_replicates(base, cand, b, seed)
    result = run(
        base, cand, direction, seed, resamples=b, confidence=confidence
    ).bca
    assert_bca_conservative(base, cand, reps, confidence, direction, result)


def ulp_core(center: float) -> st.SearchStrategy[list[float]]:
    """Odd or even arms whose middle values are a few ulps apart.

    The jackknife of the median then moves by ulps only, so the
    computed acceleration is rounding noise; the outer values are
    spread so the resamples still vary.
    """

    def build(n: int, ks: list[int], spread: list[float]) -> list[float]:
        k = n // 2 - 1
        out = [center * (1 - s) for s in spread[:k]]
        out += [center + j * math.ulp(center) for j in ks[: n - 2 * k]]
        out += [center * (1 + s) for s in spread[k : 2 * k]]
        return out

    return st.integers(3, 13).flatmap(
        lambda n: st.builds(
            build,
            st.just(n),
            st.lists(st.integers(-4, 4), min_size=n, max_size=n),
            st.lists(st.floats(1e-3, 1e-2), min_size=n, max_size=n),
        )
    )


Z99 = statistics.NormalDist().inv_cdf(0.005)
Z95 = statistics.NormalDist().inv_cdf(0.025)


def tails_at(
    z0_lo: float, z0_hi: float, a_lo: float, a_hi: float, z: float
) -> tuple[float, float]:
    t_lo, t_hi = _bca_tails(
        np.array([z0_lo]),
        np.array([z0_hi]),
        np.array([a_lo]),
        np.array([a_hi]),
        z,
    )
    return float(t_lo[0]), float(t_hi[0])


def test_bca_lower_mass_rises_and_upper_mass_falls_in_z0_and_a() -> None:
    # Why the lower bound is taken at (z0_lo, a_lo) and the upper at
    # (z0_hi, a_hi): those are the corners with the smaller mass on
    # each side, so both bounds move outward.
    def lo(z0: float, a: float) -> float:
        return tails_at(z0, z0, a, a, Z95)[0]

    def hi(z0: float, a: float) -> float:
        return tails_at(z0, z0, a, a, Z95)[1]

    assert lo(0.0, 0.05) < lo(0.1, 0.05)
    assert lo(0.0, 0.0) < lo(0.0, 0.05)
    assert hi(0.0, 0.05) > hi(0.1, 0.05)
    assert hi(0.0, 0.0) > hi(0.0, 0.05)
    t_lo, t_hi = tails_at(0.0, 0.1, 0.0, 0.05, Z95)
    assert t_lo == lo(0.0, 0.0)
    assert t_hi == hi(0.1, 0.05)


def test_bca_tails_are_nan_where_one_minus_a_w_reaches_zero() -> None:
    # At (z0, a) = (-3.5, -1/6) and 99%, D = 1 - a (z0 + z) is about
    # -0.0126. Past D = 0 the lower argument is about +477 and the lower
    # mass about 1, which without the guard would put the lower bound on
    # the largest replicate, an order statistic that is in range.
    d = 1 - (-1 / 6) * (-3.5 + Z99)
    assert -0.02 < d < 0
    guarded = tails_at(-3.5, -1.0, -1 / 6, 1 / 6, Z99)
    assert math.isnan(guarded[0])
    assert math.isnan(guarded[1])
    t_lo, t_hi = tails_at(-3.5, -1.0, -0.1, 1 / 6, Z99)
    assert 0 < t_lo < 0.5
    assert 0 < t_hi < 0.5


@settings(max_examples=150, deadline=None)
@given(
    arms=st.one_of(
        st.tuples(arm, arm),
        near_ties,
        st.tuples(ulp_core(1.0), ulp_core(1.14)),
    )
)
def test_acceleration_interval_contains_the_exact_value(
    arms: tuple[list[float], list[float]],
) -> None:
    base, cand = arms
    sorted_b = np.sort(np.array([base]), axis=-1)
    sorted_c = np.sort(np.array([cand]), axis=-1)
    m_b, _ = _median_of_sorted(sorted_b)
    m_c, _ = _median_of_sorted(sorted_c)
    a_lo, a_hi = _acceleration_bounds(sorted_b, sorted_c, m_b, m_c)
    exact = exact_acceleration(base, cand)
    if exact is None:
        assert np.isnan(a_lo[0])
        assert np.isnan(a_hi[0])
        return
    assert abs(exact) <= decimal.Decimal(1) / 6
    assert decimal.Decimal(float(a_lo[0])) <= exact
    assert exact <= decimal.Decimal(float(a_hi[0]))


AUDIT_NEAR_TIES = [
    pytest.param(
        "higher_is_better",
        0.035,
        443736741019,
        389,
        [
            "0x1.fffffffffffffp-1",
            "0x1.0000000000001p+0",
            "0x1.0000000000000p+0",
            "0x1.0000000000001p+0",
            "0x1.0000000000001p+0",
            "0x1.0000000000001p+0",
            "0x1.0000000000001p+0",
            "0x1.fffffffffffffp-1",
            "0x1.0000000000000p+0",
            "0x1.fffffffffffffp-1",
        ],
        [
            "0x1.093996fa82e87p+0",
            "0x1.08f5c28f5c290p+0",
            "0x1.093996fa82e87p+0",
            "0x1.08b1ee2435697p+0",
            "0x1.093996fa82e87p+0",
            "0x1.08b1ee2435697p+0",
            "0x1.093996fa82e87p+0",
            "0x1.093996fa82e87p+0",
            "0x1.093996fa82e87p+0",
            "0x1.093996fa82e87p+0",
        ],
        id="higher-0.035",
    ),
    pytest.param(
        "higher_is_better",
        1.143529201646492,
        443736741019,
        2059,
        [
            "0x1.0000000000001p+0",
            "0x1.0000000000000p+0",
            "0x1.0000000000001p+0",
            "0x1.fffffffffffffp-1",
            "0x1.fffffffffffffp-1",
            "0x1.0000000000000p+0",
            "0x1.0000000000000p+0",
            "0x1.0000000000001p+0",
            "0x1.0000000000001p+0",
            "0x1.0000000000001p+0",
        ],
        [
            "0x1.125f2a358bdccp+1",
            "0x1.125f2a358bdccp+1",
            "0x1.12a5676f74b4bp+1",
            "0x1.12a5676f74b4bp+1",
            "0x1.12a5676f74b4bp+1",
            "0x1.12a5676f74b4bp+1",
            "0x1.125f2a358bdcbp+1",
            "0x1.12a5676f74b4bp+1",
            "0x1.12a5676f74b4bp+1",
            "0x1.125f2a358bdcap+1",
        ],
        id="higher-1.1435",
    ),
    pytest.param(
        "lower_is_better",
        0.14,
        443736741020,
        1419,
        [
            "0x1.2421c044284e0p+0",
            "0x1.2421c044284e0p+0",
            "0x1.2421c044284e0p+0",
            "0x1.23d70a3d70a3ep+0",
            "0x1.23d70a3d70a3fp+0",
            "0x1.2421c044284e0p+0",
            "0x1.23d70a3d70a3dp+0",
            "0x1.23d70a3d70a3ep+0",
            "0x1.2421c044284e0p+0",
            "0x1.2421c044284e0p+0",
        ],
        [
            "0x1.0000000000001p+0",
            "0x1.0000000000001p+0",
            "0x1.0000000000000p+0",
            "0x1.fffffffffffffp-1",
            "0x1.0000000000001p+0",
            "0x1.0000000000001p+0",
            "0x1.0000000000000p+0",
            "0x1.0000000000001p+0",
            "0x1.0000000000000p+0",
            "0x1.fffffffffffffp-1",
        ],
        id="lower-0.14",
    ),
]


@pytest.mark.parametrize(
    ("direction", "min_effect", "seed", "row", "base_hex", "cand_hex"),
    AUDIT_NEAR_TIES,
)
def test_bca_near_ties_from_the_audit_are_not_false_keeps(
    direction: Direction,
    min_effect: float,
    seed: int,
    row: int,
    base_hex: list[str],
    cand_hex: list[str],
) -> None:
    # Rows of the auditor's tiny-noise sweep (B = 1999, 99%), each the
    # row-th of a batched call from that seed. f5abc09 gave L above the
    # bar where the exact BCa lower bound is at or below it: a false
    # KEEP from rounding alone.
    base = [float.fromhex(h) for h in base_hex]
    cand = [float.fromhex(h) for h in cand_hex]
    b, confidence = 1999, 0.99
    rng = np.random.default_rng(seed)
    for _ in range(row):
        draw_indices(rng, len(base), len(cand), b)
    state = rng.bit_generator.state
    result = bootstrap_median_ratio(
        base,
        cand,
        direction,
        rng=rng,
        resamples=b,
        confidence=confidence,
    ).bca
    replay = np.random.default_rng()
    replay.bit_generator.state = state
    reps = exact_replicates(base, cand, b, replay)
    exact = independent_bca(base, cand, reps, confidence)
    assert exact is not None
    exact_lo = exact[0] if direction == "higher_is_better" else exact[1]
    assert exact_lo is not None
    if direction == "lower_is_better":
        exact_lo = 1 / exact_lo
    bar = 1.0 + min_effect
    assert exact_lo <= Fraction(bar)
    lower = float(result.lower)
    assert math.isnan(lower) or lower <= bar
    assert_bca_conservative(base, cand, reps, confidence, direction, result)


AUDIT_QUANTIZED = [
    pytest.param(
        32529814263,
        27,
        [
            "0x1.0083126e978d5p+0",
            "0x1.0000000000000p+0",
            "0x1.0000000000000p+0",
            "0x1.fef9db22d0e56p-1",
            "0x1.0000000000000p+0",
            "0x1.ff7ced916872bp-1",
            "0x1.fef9db22d0e56p-1",
            "0x1.fef9db22d0e56p-1",
            "0x1.fef9db22d0e56p-1",
            "0x1.0147ae147ae15p+0",
        ],
        [
            "0x1.0872b020c49bap+0",
            "0x1.06e978d4fdf3bp+0",
            "0x1.076c8b4395810p+0",
            "0x1.076c8b4395810p+0",
            "0x1.07ae147ae147bp+0",
            "0x1.07ef9db22d0e5p+0",
            "0x1.06e978d4fdf3bp+0",
            "0x1.072b020c49ba6p+0",
            "0x1.083126e978d50p+0",
            "0x1.072b020c49ba6p+0",
        ],
        id="seed263-row27",
    ),
    pytest.param(
        32529814269,
        49,
        [
            "0x1.0000000000000p+0",
            "0x1.00c49ba5e3540p+0",
            "0x1.0000000000000p+0",
            "0x1.fc6a7ef9db22dp-1",
            "0x1.fe76c8b439581p-1",
            "0x1.0147ae147ae15p+0",
            "0x1.03d70a3d70a3ep+0",
            "0x1.03d70a3d70a3ep+0",
            "0x1.fb645a1cac083p-1",
            "0x1.f7ced916872b0p-1",
        ],
        [
            "0x1.072b020c49ba6p+0",
            "0x1.03d70a3d70a3ep+0",
            "0x1.09fbe76c8b439p+0",
            "0x1.0a7ef9db22d0ep+0",
            "0x1.049ba5e353f7dp+0",
            "0x1.0353f7ced9169p+0",
            "0x1.051eb851eb852p+0",
            "0x1.05e353f7ced92p+0",
            "0x1.051eb851eb852p+0",
            "0x1.0a7ef9db22d0ep+0",
        ],
        id="seed269-row49",
    ),
    pytest.param(
        32529814269,
        121,
        [
            "0x1.010624dd2f1aap+0",
            "0x1.0000000000000p+0",
            "0x1.ff7ced916872bp-1",
            "0x1.f7ced916872b0p-1",
            "0x1.0189374bc6a7fp+0",
            "0x1.028f5c28f5c29p+0",
            "0x1.00c49ba5e3540p+0",
            "0x1.028f5c28f5c29p+0",
            "0x1.f645a1cac0831p-1",
            "0x1.f3b645a1cac08p-1",
        ],
        [
            "0x1.0624dd2f1a9fcp+0",
            "0x1.06e978d4fdf3bp+0",
            "0x1.0872b020c49bap+0",
            "0x1.0c083126e978dp+0",
            "0x1.049ba5e353f7dp+0",
            "0x1.0666666666666p+0",
            "0x1.076c8b4395810p+0",
            "0x1.09ba5e353f7cfp+0",
            "0x1.0666666666666p+0",
            "0x1.0353f7ced9169p+0",
        ],
        id="seed269-row121",
    ),
    pytest.param(
        32529814273,
        5,
        [
            "0x1.fe76c8b439581p-1",
            "0x1.028f5c28f5c29p+0",
            "0x1.fe76c8b439581p-1",
            "0x1.020c49ba5e354p+0",
            "0x1.fced916872b02p-1",
            "0x1.fc6a7ef9db22dp-1",
            "0x1.09ba5e353f7cfp+0",
            "0x1.ff7ced916872bp-1",
            "0x1.00c49ba5e3540p+0",
            "0x1.fa5e353f7ced9p-1",
            "0x1.004189374bc6bp+0",
            "0x1.010624dd2f1aap+0",
            "0x1.f333333333333p-1",
            "0x1.fef9db22d0e56p-1",
            "0x1.024dd2f1a9fbfp+0",
            "0x1.fd70a3d70a3d7p-1",
            "0x1.00c49ba5e3540p+0",
            "0x1.f7ced916872b0p-1",
            "0x1.0147ae147ae15p+0",
            "0x1.03d70a3d70a3ep+0",
            "0x1.0000000000000p+0",
            "0x1.f333333333333p-1",
            "0x1.01cac083126eap+0",
            "0x1.f851eb851eb85p-1",
            "0x1.0666666666666p+0",
            "0x1.0000000000000p+0",
            "0x1.fc6a7ef9db22dp-1",
            "0x1.0189374bc6a7fp+0",
            "0x1.f7ced916872b0p-1",
            "0x1.f74bc6a7ef9dbp-1",
        ],
        [
            "0x1.08f5c28f5c28fp+0",
            "0x1.06a7ef9db22d1p+0",
            "0x1.05604189374bdp+0",
            "0x1.05a1cac083127p+0",
            "0x1.051eb851eb852p+0",
            "0x1.0ac083126e979p+0",
            "0x1.05e353f7ced92p+0",
            "0x1.07ae147ae147bp+0",
            "0x1.07ae147ae147bp+0",
            "0x1.0666666666666p+0",
            "0x1.09fbe76c8b439p+0",
            "0x1.0353f7ced9169p+0",
            "0x1.0c8b439581062p+0",
            "0x1.0d0e560418937p+0",
            "0x1.09374bc6a7efap+0",
            "0x1.07ae147ae147bp+0",
            "0x1.0e5604189374cp+0",
            "0x1.0e5604189374cp+0",
            "0x1.0b020c49ba5e3p+0",
            "0x1.05a1cac083127p+0",
            "0x1.0978d4fdf3b64p+0",
            "0x1.0ac083126e979p+0",
            "0x1.05a1cac083127p+0",
            "0x1.07ae147ae147bp+0",
            "0x1.0c8b439581062p+0",
            "0x1.010624dd2f1aap+0",
            "0x1.0872b020c49bap+0",
            "0x1.07ef9db22d0e5p+0",
            "0x1.083126e978d50p+0",
            "0x1.0624dd2f1a9fcp+0",
        ],
        id="seed273-row5",
    ),
    pytest.param(
        32529814273,
        12,
        [
            "0x1.f6c8b43958106p-1",
            "0x1.fe76c8b439581p-1",
            "0x1.fd70a3d70a3d7p-1",
            "0x1.0083126e978d5p+0",
            "0x1.028f5c28f5c29p+0",
            "0x1.f95810624dd2fp-1",
            "0x1.fced916872b02p-1",
            "0x1.010624dd2f1aap+0",
            "0x1.00c49ba5e3540p+0",
            "0x1.f9db22d0e5604p-1",
            "0x1.0872b020c49bap+0",
            "0x1.03d70a3d70a3ep+0",
            "0x1.05604189374bdp+0",
            "0x1.04189374bc6a8p+0",
            "0x1.ff7ced916872bp-1",
            "0x1.fb645a1cac083p-1",
            "0x1.fef9db22d0e56p-1",
            "0x1.f95810624dd2fp-1",
            "0x1.02d0e56041894p+0",
            "0x1.0189374bc6a7fp+0",
            "0x1.fd70a3d70a3d7p-1",
            "0x1.03126e978d4fep+0",
            "0x1.fdf3b645a1cacp-1",
            "0x1.04dd2f1a9fbe8p+0",
            "0x1.004189374bc6bp+0",
            "0x1.0147ae147ae15p+0",
            "0x1.051eb851eb852p+0",
            "0x1.fef9db22d0e56p-1",
            "0x1.028f5c28f5c29p+0",
            "0x1.fdf3b645a1cacp-1",
        ],
        [
            "0x1.04dd2f1a9fbe8p+0",
            "0x1.0b4395810624ep+0",
            "0x1.0a7ef9db22d0ep+0",
            "0x1.0624dd2f1a9fcp+0",
            "0x1.09374bc6a7efap+0",
            "0x1.09374bc6a7efap+0",
            "0x1.0a3d70a3d70a4p+0",
            "0x1.04dd2f1a9fbe8p+0",
            "0x1.0353f7ced9169p+0",
            "0x1.06a7ef9db22d1p+0",
            "0x1.0624dd2f1a9fcp+0",
            "0x1.06e978d4fdf3bp+0",
            "0x1.076c8b4395810p+0",
            "0x1.0d0e560418937p+0",
            "0x1.09374bc6a7efap+0",
            "0x1.051eb851eb852p+0",
            "0x1.05e353f7ced92p+0",
            "0x1.0b4395810624ep+0",
            "0x1.076c8b4395810p+0",
            "0x1.04189374bc6a8p+0",
            "0x1.02d0e56041894p+0",
            "0x1.09374bc6a7efap+0",
            "0x1.076c8b4395810p+0",
            "0x1.0c083126e978dp+0",
            "0x1.083126e978d50p+0",
            "0x1.09fbe76c8b439p+0",
            "0x1.051eb851eb852p+0",
            "0x1.04189374bc6a8p+0",
            "0x1.03d70a3d70a3ep+0",
            "0x1.0a7ef9db22d0ep+0",
        ],
        id="seed273-row12",
    ),
    pytest.param(
        32529814273,
        105,
        [
            "0x1.fd70a3d70a3d7p-1",
            "0x1.004189374bc6bp+0",
            "0x1.004189374bc6bp+0",
            "0x1.049ba5e353f7dp+0",
            "0x1.0147ae147ae15p+0",
            "0x1.ff7ced916872bp-1",
            "0x1.fced916872b02p-1",
            "0x1.0000000000000p+0",
            "0x1.0353f7ced9169p+0",
            "0x1.024dd2f1a9fbfp+0",
            "0x1.03126e978d4fep+0",
            "0x1.fc6a7ef9db22dp-1",
            "0x1.028f5c28f5c29p+0",
            "0x1.fdf3b645a1cacp-1",
            "0x1.f95810624dd2fp-1",
            "0x1.fd70a3d70a3d7p-1",
            "0x1.f8d4fdf3b645ap-1",
            "0x1.fced916872b02p-1",
            "0x1.0189374bc6a7fp+0",
            "0x1.fced916872b02p-1",
            "0x1.fef9db22d0e56p-1",
            "0x1.fa5e353f7ced9p-1",
            "0x1.fb645a1cac083p-1",
            "0x1.f7ced916872b0p-1",
            "0x1.024dd2f1a9fbfp+0",
            "0x1.f6c8b43958106p-1",
            "0x1.0395810624dd3p+0",
            "0x1.0083126e978d5p+0",
            "0x1.010624dd2f1aap+0",
            "0x1.f7ced916872b0p-1",
        ],
        [
            "0x1.09374bc6a7efap+0",
            "0x1.049ba5e353f7dp+0",
            "0x1.05e353f7ced92p+0",
            "0x1.06e978d4fdf3bp+0",
            "0x1.076c8b4395810p+0",
            "0x1.0353f7ced9169p+0",
            "0x1.03126e978d4fep+0",
            "0x1.0624dd2f1a9fcp+0",
            "0x1.06a7ef9db22d1p+0",
            "0x1.07ae147ae147bp+0",
            "0x1.06e978d4fdf3bp+0",
            "0x1.0b020c49ba5e3p+0",
            "0x1.09ba5e353f7cfp+0",
            "0x1.01cac083126eap+0",
            "0x1.072b020c49ba6p+0",
            "0x1.09fbe76c8b439p+0",
            "0x1.0a3d70a3d70a4p+0",
            "0x1.05604189374bdp+0",
            "0x1.07ef9db22d0e5p+0",
            "0x1.049ba5e353f7dp+0",
            "0x1.0189374bc6a7fp+0",
            "0x1.076c8b4395810p+0",
            "0x1.0978d4fdf3b64p+0",
            "0x1.06a7ef9db22d1p+0",
            "0x1.02d0e56041894p+0",
            "0x1.09ba5e353f7cfp+0",
            "0x1.076c8b4395810p+0",
            "0x1.0c49ba5e353f8p+0",
            "0x1.028f5c28f5c29p+0",
            "0x1.072b020c49ba6p+0",
        ],
        id="seed273-row105",
    ),
]


@pytest.mark.parametrize(
    ("seed", "row", "base_hex", "cand_hex"), AUDIT_QUANTIZED
)
def test_bca_on_quantized_data_from_the_audit_contains_exact(
    seed: int, row: int, base_hex: list[str], cand_hex: list[str]
) -> None:
    # Rows of the auditor's timer-quantized sweep (B = 1999, 99%,
    # lower is better) where f5abc09 and b7e5af3 gave a BCa interval
    # narrower than the exact one on at least one side.
    base = [float.fromhex(h) for h in base_hex]
    cand = [float.fromhex(h) for h in cand_hex]
    b, confidence = 1999, 0.99
    rng = np.random.default_rng(seed)
    for _ in range(row):
        draw_indices(rng, len(base), len(cand), b)
    replay = np.random.default_rng()
    replay.bit_generator.state = rng.bit_generator.state
    result = bootstrap_median_ratio(
        base,
        cand,
        "lower_is_better",
        rng=rng,
        resamples=b,
        confidence=confidence,
    ).bca
    reps = exact_replicates(base, cand, b, replay)
    assert_bca_conservative(
        base, cand, reps, confidence, "lower_is_better", result
    )


BAND_ROWS = [
    pytest.param(
        "higher_is_better",
        1232,
        2,
        [
            "0x1.0000000000001p+0",
            "0x1.004189374bc6bp+0",
            "0x1.0000000000001p+0",
            "0x1.ff7ced916872bp-1",
            "0x1.ff7ced916872dp-1",
            "0x1.0000000000000p+0",
            "0x1.ff7ced916872ap-1",
            "0x1.ff7ced916872ap-1",
            "0x1.ff7ced916872bp-1",
            "0x1.004189374bc69p+0",
        ],
        [
            "0x1.238c5436b8f9bp+0",
            "0x1.238c5436b8f9bp+0",
            "0x1.2421c044284dfp+0",
            "0x1.23d70a3d70a3dp+0",
            "0x1.23d70a3d70a3ep+0",
            "0x1.23d70a3d70a3ep+0",
            "0x1.238c5436b8f9bp+0",
            "0x1.238c5436b8f9bp+0",
            "0x1.2421c044284dfp+0",
            "0x1.2421c044284dfp+0",
        ],
        id="higher-seed1232-row2",
    ),
    pytest.param(
        "lower_is_better",
        1232,
        2,
        [
            "0x1.0000000000001p+0",
            "0x1.004189374bc6bp+0",
            "0x1.0000000000001p+0",
            "0x1.ff7ced916872bp-1",
            "0x1.ff7ced916872dp-1",
            "0x1.0000000000000p+0",
            "0x1.ff7ced916872ap-1",
            "0x1.ff7ced916872ap-1",
            "0x1.ff7ced916872bp-1",
            "0x1.004189374bc69p+0",
        ],
        [
            "0x1.238c5436b8f9bp+0",
            "0x1.238c5436b8f9bp+0",
            "0x1.2421c044284dfp+0",
            "0x1.23d70a3d70a3dp+0",
            "0x1.23d70a3d70a3ep+0",
            "0x1.23d70a3d70a3ep+0",
            "0x1.238c5436b8f9bp+0",
            "0x1.238c5436b8f9bp+0",
            "0x1.2421c044284dfp+0",
            "0x1.2421c044284dfp+0",
        ],
        id="lower-seed1232-row2",
    ),
    pytest.param(
        "higher_is_better",
        2235,
        88,
        [
            "0x1.ff7ced916872ap-1",
            "0x1.0000000000001p+0",
            "0x1.ff7ced916872dp-1",
            "0x1.ff7ced916872bp-1",
            "0x1.ff7ced916872bp-1",
            "0x1.004189374bc6bp+0",
            "0x1.ff7ced916872bp-1",
            "0x1.004189374bc69p+0",
            "0x1.0000000000000p+0",
            "0x1.004189374bc69p+0",
        ],
        [
            "0x1.125f2a358bdcbp+1",
            "0x1.125f2a358bdccp+1",
            "0x1.1218ecfba304bp+1",
            "0x1.125f2a358bdccp+1",
            "0x1.1218ecfba304bp+1",
            "0x1.125f2a358bdcbp+1",
            "0x1.12a5676f74b4bp+1",
            "0x1.125f2a358bdcbp+1",
            "0x1.125f2a358bdcap+1",
            "0x1.1218ecfba304bp+1",
        ],
        id="higher-seed2235-row88",
    ),
    pytest.param(
        "lower_is_better",
        2235,
        88,
        [
            "0x1.ff7ced916872ap-1",
            "0x1.0000000000001p+0",
            "0x1.ff7ced916872dp-1",
            "0x1.ff7ced916872bp-1",
            "0x1.ff7ced916872bp-1",
            "0x1.004189374bc6bp+0",
            "0x1.ff7ced916872bp-1",
            "0x1.004189374bc69p+0",
            "0x1.0000000000000p+0",
            "0x1.004189374bc69p+0",
        ],
        [
            "0x1.125f2a358bdcbp+1",
            "0x1.125f2a358bdccp+1",
            "0x1.1218ecfba304bp+1",
            "0x1.125f2a358bdccp+1",
            "0x1.1218ecfba304bp+1",
            "0x1.125f2a358bdcbp+1",
            "0x1.12a5676f74b4bp+1",
            "0x1.125f2a358bdcbp+1",
            "0x1.125f2a358bdcap+1",
            "0x1.1218ecfba304bp+1",
        ],
        id="lower-seed2235-row88",
    ),
]


@pytest.mark.parametrize(
    ("direction", "seed", "row", "base_hex", "cand_hex"), BAND_ROWS
)
def test_bca_counts_replicates_near_the_estimate_both_ways(
    direction: Direction,
    seed: int,
    row: int,
    base_hex: list[str],
    cand_hex: list[str],
) -> None:
    # Found by searching ulp-level clusters mixed with 0.1% spread
    # (B = 1999, 99%) for rows where counting replicates against the
    # rounded estimate without the 8u band gives a BCa interval
    # narrower than the exact one.
    base = [float.fromhex(h) for h in base_hex]
    cand = [float.fromhex(h) for h in cand_hex]
    b, confidence = 1999, 0.99
    rng = np.random.default_rng(seed)
    for _ in range(row):
        draw_indices(rng, len(base), len(cand), b)
    replay = np.random.default_rng()
    replay.bit_generator.state = rng.bit_generator.state
    result = bootstrap_median_ratio(
        base, cand, direction, rng=rng, resamples=b, confidence=confidence
    ).bca
    assert np.isfinite(result.lower)
    reps = exact_replicates(base, cand, b, replay)
    assert_bca_conservative(base, cand, reps, confidence, direction, result)


def test_bca_is_nan_when_the_jackknife_has_no_spread() -> None:
    # Leaving out any one value of [1, 2, 2, 2, 3] leaves median 2, so
    # the acceleration is 0 / 0; the resamples still vary.
    result = run(
        [1.0, 2.0, 2.0, 2.0, 3.0], [5.0, 6.0, 6.0, 6.0, 7.0], resamples=999
    )
    assert np.isnan(result.bca.lower)
    assert np.isnan(result.bca.upper)
    assert result.percentile.lower < result.percentile.upper
    assert float(result.bca.estimate) == 2.0 / 6.0


@pytest.mark.parametrize(
    "max_bytes",
    [
        200_000,  # one row at a time, B chunked
        300_000,
        1_000_000,  # several rows per block
        DEFAULT_MAX_BYTES,  # every row at once
    ],
)
def test_result_does_not_depend_on_the_byte_budget(max_bytes: int) -> None:
    g = np.random.default_rng(4)
    base = g.lognormal(size=(2, 3, 11))
    cand = g.lognormal(size=(2, 3, 8))
    reference = run(base, cand, "higher_is_better", 9, resamples=3000)
    chunked = run(
        base,
        cand,
        "higher_is_better",
        9,
        resamples=3000,
        max_bytes=max_bytes,
    )
    assert_identical(reference, chunked)
    assert reference.percentile.lower.shape == (2, 3)


def test_batched_call_equals_consecutive_calls() -> None:
    g = np.random.default_rng(5)
    base = g.lognormal(size=(4, 10))
    cand = g.lognormal(size=(4, 13))
    batched = run(base, cand, "lower_is_better", 21, resamples=2000)
    rng = np.random.default_rng(21)
    for i in range(4):
        row = bootstrap_median_ratio(
            base[i], cand[i], "lower_is_better", rng=rng, resamples=2000
        )
        for whole, one in zip(intervals(batched), intervals(row), strict=True):
            assert whole.lower[i] == one.lower
            assert whole.upper[i] == one.upper
            assert whole.estimate[i] == one.estimate


def test_same_seed_same_result_and_global_rng_untouched() -> None:
    g = np.random.default_rng(6)
    base, cand = g.lognormal(size=15), g.lognormal(size=15)
    state = np.random.get_state()  # noqa: NPY002
    first = run(base, cand, seed=3, resamples=2000)
    second = run(base, cand, seed=3, resamples=2000)
    after = np.random.get_state()  # noqa: NPY002
    assert_identical(first, second)
    assert state[0] == after[0]
    np.testing.assert_array_equal(state[1], after[1])
    other = run(base, cand, seed=4, resamples=2000)
    assert other.percentile.lower != first.percentile.lower


@pytest.mark.parametrize(
    ("rows", "n", "resamples", "max_bytes"),
    [
        (1, 200, 100_000, 8 * 2**20),
        (1, 200, 100_000, DEFAULT_MAX_BYTES),
        (50, 20, 2000, 2 * 2**20),
        (3, 41, 20_000, 2 * 2**20),
    ],
)
def test_peak_memory_stays_under_the_budget(
    rows: int, n: int, resamples: int, max_bytes: int
) -> None:
    g = np.random.default_rng(7)
    base = g.lognormal(size=(rows, n))
    cand = g.lognormal(size=(rows, n))
    tracemalloc.start()
    try:
        run(base, cand, resamples=resamples, max_bytes=max_bytes)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak <= max_bytes


GOOD = [1.0, 2.0, 3.0]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"baseline": [1.0]}, "at least 2 observations"),
        ({"candidate": 2.0}, "at least one axis"),
        ({"baseline": ["a", "b"]}, "real numeric"),
        ({"baseline": [True, False]}, "real numeric"),
        ({"baseline": [1.0, math.nan]}, "nan"),
        ({"candidate": [1.0, math.inf]}, "inf"),
        ({"candidate": [1.0, 0.0]}, "0.0"),
        ({"candidate": [1.0, -2.0]}, "-2.0"),
        ({"baseline": [1.0, 2.0**501]}, "2\\*\\*500"),
        ({"baseline": [1.0, 2.0**-501]}, "2\\*\\*-500"),
        ({"baseline": [1, 2**60]}, "2\\*\\*53"),
        ({"baseline": [1, 2**53 + 1]}, "2\\*\\*53"),
        ({"baseline": [[1.0, 2.0]] * 2}, "batch shapes differ"),
        ({"direction": "faster"}, "unknown direction"),
        ({"rng": 0}, "Generator"),
        ({"confidence": 1.0}, "confidence"),
        ({"confidence": 0.0}, "confidence"),
        ({"confidence": math.nan}, "confidence"),
        ({"confidence": True}, "confidence"),
        ({"resamples": 0}, "resamples"),
        ({"resamples": 2.5}, "resamples"),
        ({"resamples": 38}, "at least 39"),
        ({"max_bytes": 1000}, "max_bytes"),
        ({"max_bytes": 0}, "max_bytes"),
    ],
)
def test_invalid_inputs_raise(kwargs: dict[str, Any], match: str) -> None:
    args: dict[str, Any] = {
        "baseline": GOOD,
        "candidate": GOOD,
        "direction": "lower_is_better",
        "rng": np.random.default_rng(0),
        "resamples": 100,
    }
    args.update(kwargs)
    with pytest.raises(ValueError, match=match):
        bootstrap_median_ratio(
            args.pop("baseline"),
            args.pop("candidate"),
            args.pop("direction"),
            **args,
        )


@pytest.mark.skipif(
    np.dtype(np.longdouble) == np.dtype(np.float64),
    reason="longdouble is float64 on this platform, so it is exact",
)
def test_longdouble_inputs_are_refused() -> None:
    with pytest.raises(ValueError, match="float64 or narrower"):
        run(np.array([1.0, 2.0], dtype=np.longdouble), GOOD)


def test_float32_and_integer_inputs_widen_exactly() -> None:
    as_float = run([1.0, 2.0, 4.0], [3.0, 5.0], resamples=999)
    as_int = run([1, 2, 4], [3, 5], resamples=999)
    as_f32 = run(
        np.array([1, 2, 4], dtype=np.float32),
        np.array([3, 5], dtype=np.float32),
        resamples=999,
    )
    assert_identical(as_float, as_int)
    assert_identical(as_float, as_f32)
