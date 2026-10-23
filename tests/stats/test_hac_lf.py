"""Tests of the ``hac_lf`` interval, its table and its look driver."""

import itertools
import math
from decimal import Decimal, localcontext

import numpy as np
import pytest
from numpy.typing import NDArray

from perfproof.schema import LookSchedule
from perfproof.stats import intervals, lf_tables
from perfproof.stats.intervals import (
    HAC_MIN_N,
    RHO_FLOOR,
    hac_bandwidth,
    hac_lf_interval,
    lf_critical,
)
from perfproof.stats.sequential import (
    decide_look,
    look_confidence,
    run_looks,
)

FloatArray = NDArray[np.float64]
CONF = look_confidence(0.05, 6)


def _data(
    rows: int, n: int, *, seed: int = 0, shift: float = 0.0, rho: float = 0.0
) -> tuple[FloatArray, FloatArray, NDArray[np.bool_]]:
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((rows, n))
    x = np.empty_like(z)
    x[:, 0] = z[:, 0]
    for i in range(1, n):
        x[:, i] = rho * x[:, i - 1] + math.sqrt(1 - rho * rho) * z[:, i]
    base = np.exp(0.05 * rng.standard_normal((rows, n)))
    cand = base * np.exp(-0.05 * x * math.sqrt(2) - shift)
    first = np.zeros((rows, n), dtype=np.bool_)
    first[:, 0::2] = rng.random((rows, 1)) < 0.5
    first[:, 1::2] = ~first[:, 0::2]
    return base, cand, first


def _run(
    b: FloatArray,
    c: FloatArray,
    first: NDArray[np.bool_],
    *,
    mode: str = "one_host",
    ucb_z: float = intervals.UCB_Z,
    rho_floor: float = RHO_FLOOR,
) -> intervals.HacInterval:
    return hac_lf_interval(
        b,
        c,
        direction="lower_is_better",
        confidence=CONF,
        candidate_first=first,
        mode=mode,
        ucb_z=ucb_z,
        rho_floor=rho_floor,
    )


def test_the_tabulated_confidences_are_the_look_confidences() -> None:
    levels = intervals._level_confidences()  # pyright: ignore[reportPrivateUsage]
    for k, conf in zip(lf_tables.LEVEL_KS, levels, strict=True):
        assert conf == look_confidence(0.05, k)


def test_bandwidth_is_the_ceiling_of_the_double_product() -> None:
    assert hac_bandwidth(12) == 2
    assert hac_bandwidth(20) == 2
    assert hac_bandwidth(30) == math.ceil(0.1 * 30)
    assert hac_bandwidth(60) == math.ceil(0.1 * 60)
    assert hac_bandwidth(11) == 2
    assert hac_bandwidth(3) == 2


def test_table_is_monotone_in_lag1_limit_and_look_length() -> None:
    for mode in ("one_host", "cross_host"):
        values = np.array(lf_tables.VALUES[mode])
        # nodes x limits x bins x levels
        assert values.shape == (
            len(lf_tables.NODES),
            len(lf_tables.RHO_MAXES),
            len(lf_tables.EDGES) + 1,
            len(lf_tables.LEVEL_KS),
        )
        assert np.all(np.diff(values, axis=2) >= 0), "not nondecreasing in r"
        assert np.all(np.diff(values, axis=1) >= 0), "not nondecreasing in rho"
        assert np.all(np.diff(values, axis=0) <= 0), "not nonincreasing in n"
        assert np.all(np.diff(values, axis=3) >= 0), "not nondecreasing in K"
        assert np.all(values > 0)


def test_a_look_at_an_untabulated_length_gets_at_least_the_next_node() -> None:
    r = np.linspace(-0.9, 0.99, 40)
    limit = np.full(r.shape, 0.5)
    nodes = lf_tables.NODES
    for mode in ("one_host", "cross_host"):
        for n in range(HAC_MIN_N, nodes[-1] + 20):
            here = lf_critical(
                mode=mode, n=n, confidence=CONF, lag1_up=r, rho_limit=limit
            )
            above = [m for m in nodes if m > n]
            if above:
                there = lf_critical(
                    mode=mode,
                    n=above[0],
                    confidence=CONF,
                    lag1_up=r,
                    rho_limit=limit,
                )
                assert np.all(here >= there - 1e-12)


def test_interpolation_never_gives_less_than_the_bin_value() -> None:
    values = np.array(lf_tables.VALUES["one_host"])
    edges = np.array(lf_tables.EDGES)
    node = lf_tables.NODES.index(30)
    level = lf_tables.LEVEL_KS.index(6)
    for b in range(len(edges) + 1):
        lo = -1.0 if b == 0 else float(edges[b - 1])
        hi = 1.0 if b == len(edges) else float(edges[b])
        r = np.linspace(lo + 1e-9, hi - 1e-9, 25)
        got = lf_critical(
            mode="one_host",
            n=30,
            confidence=CONF,
            lag1_up=r,
            rho_limit=np.full(r.shape, 0.3),
        )
        assert np.all(got >= values[node, 0, b, level] - 1e-12)


def test_a_larger_limit_or_a_larger_confidence_never_narrows() -> None:
    r = np.linspace(-0.5, 0.9, 30)
    got = [
        lf_critical(
            mode="one_host",
            n=24,
            confidence=CONF,
            lag1_up=r,
            rho_limit=np.full(r.shape, limit),
        )
        for limit in (0.3, 0.5, 0.9)
    ]
    assert np.all(got[1] >= got[0])
    assert np.all(got[2] >= got[1])
    wide = lf_critical(
        mode="one_host",
        n=24,
        confidence=look_confidence(0.05, 12),
        lag1_up=r,
        rho_limit=np.full(r.shape, 0.5),
    )
    assert np.all(wide >= got[1])
    # K = 5 has no table of its own and uses K = 6.
    five = lf_critical(
        mode="one_host",
        n=24,
        confidence=look_confidence(0.05, 5),
        lag1_up=r,
        rho_limit=np.full(r.shape, 0.5),
    )
    assert np.all(five == got[1])


def test_a_confidence_above_the_last_level_is_refused() -> None:
    with pytest.raises(ValueError, match="tabulated"):
        lf_critical(
            mode="one_host",
            n=24,
            confidence=look_confidence(0.05, 13),
            lag1_up=np.zeros(1),
            rho_limit=np.full(1, 0.3),
        )


def test_inputs_are_checked() -> None:
    b, c, first = _data(3, 20)
    with pytest.raises(ValueError, match="at least 12"):
        _run(b[:, :10], c[:, :10], first[:, :10])
    with pytest.raises(ValueError, match="boolean"):
        hac_lf_interval(
            b,
            c,
            direction="lower_is_better",
            confidence=CONF,
            candidate_first=first.astype(int),
            mode="one_host",
        )
    with pytest.raises(ValueError, match="mode"):
        _run(b, c, first, mode="both")
    with pytest.raises(ValueError, match="ucb_z"):
        _run(b, c, first, ucb_z=-1.0)
    with pytest.raises(ValueError, match="rho_floor"):
        _run(b, c, first, rho_floor=0.99)


def test_swapping_the_arms_inverts_the_interval() -> None:
    b, c, first = _data(40, 30, seed=1, shift=0.04)
    a = _run(b, c, first)
    s = _run(c, b, first)
    np.testing.assert_allclose(
        1 / s.interval.upper, a.interval.lower, rtol=1e-9
    )
    np.testing.assert_allclose(
        1 / s.interval.lower, a.interval.upper, rtol=1e-9
    )
    np.testing.assert_allclose(a.lag1, s.lag1, atol=1e-12)


def test_scaling_both_arms_leaves_the_interval_unchanged() -> None:
    b, c, first = _data(30, 24, seed=2, shift=0.03)
    a = _run(b, c, first)
    s = _run(7.0 * b, 7.0 * c, first)
    np.testing.assert_allclose(s.interval.lower, a.interval.lower, rtol=1e-9)
    np.testing.assert_allclose(s.interval.upper, a.interval.upper, rtol=1e-9)


def test_scaling_the_candidate_scales_the_interval_inversely() -> None:
    b, c, first = _data(30, 24, seed=3, shift=0.03)
    a = _run(b, c, first)
    s = _run(b, 2.0 * c, first)
    np.testing.assert_allclose(
        s.interval.lower, a.interval.lower / 2, rtol=1e-9
    )
    np.testing.assert_allclose(
        s.interval.upper, a.interval.upper / 2, rtol=1e-9
    )


def test_identical_ratios_give_that_ratio_exactly() -> None:
    b = np.full((2, 16), 3.0)
    c = np.full((2, 16), 2.0)
    first = np.zeros((2, 16), dtype=np.bool_)
    made = hac_lf_interval(
        b,
        c,
        direction="lower_is_better",
        confidence=CONF,
        candidate_first=first,
        mode="cross_host",
    )
    assert np.all(made.interval.lower == 1.5)
    assert np.all(made.interval.upper == 1.5)
    assert np.all(made.interval.estimate == 1.5)


def test_hand_checkable_variance_and_estimate() -> None:
    # d = 0.1, 0.3 alternating on one host with both orders: order
    # centring leaves nothing but the order effect, so u = 0 and the
    # interval is the point 0.2 in the mean, widened only by rounding.
    n = 12
    d = np.tile([0.1, 0.3], n // 2)
    b = np.exp(d)[None, :]
    c = np.ones((1, n))
    first = np.zeros((1, n), dtype=np.bool_)
    first[0, 1::2] = True
    made = hac_lf_interval(
        b,
        c,
        direction="lower_is_better",
        confidence=CONF,
        candidate_first=first,
        mode="one_host",
    )
    assert made.interval.estimate[0] == pytest.approx(math.exp(0.2))
    assert made.interval.lower[0] <= math.exp(0.2) <= made.interval.upper[0]
    assert made.interval.upper[0] / made.interval.lower[0] - 1 < 1e-12
    assert made.lag1[0] == 0.0


def _decimal_reference(
    ratios: FloatArray,
    first: NDArray[np.bool_],
    confidence: float,
    ucb_z: float,
    mode: str,
) -> tuple[Decimal, Decimal]:
    """Exact bounds of the procedure with the table's critical value."""
    n = ratios.shape[0]
    with localcontext() as ctx:
        ctx.prec = 80
        d = [Decimal(float(r)).ln() for r in ratios]
        idx_f = [i for i in range(n) if first[i]]
        idx_s = [i for i in range(n) if not first[i]]
        u = [Decimal(0)] * n
        for group in (idx_f, idx_s):
            if group:
                mean = sum((d[i] for i in group), Decimal(0)) / len(group)
                for i in group:
                    u[i] = d[i] - mean
        s2 = sum((x * x for x in u), Decimal(0))
        top = sum((u[i] * u[i + 1] for i in range(n - 1)), Decimal(0))
        r = top / s2 if s2 > 0 else Decimal(0)
        m = hac_bandwidth(n)
        lrv = Decimal(0)
        for j in range(m):
            gamma = sum((u[i] * u[i + j] for i in range(n - j)), Decimal(0))
            w = Decimal(1) - Decimal(j) / Decimal(m)
            lrv += (w if j == 0 else 2 * w) * gamma / n
        var = lrv / n
        mean_d = sum(d, Decimal(0)) / n
        spread = ((1 - r * r) / n).sqrt()
        ucb = float(r + Decimal(ucb_z) * spread)
        limits = np.asarray(lf_tables.RHO_MAXES)
        wanted = max(RHO_FLOOR, ucb)
        limit = float(
            limits[
                min(
                    np.searchsorted(limits, wanted, side="left"),
                    limits.size - 1,
                )
            ]
        )
        crit = float(
            lf_critical(
                mode=mode,
                n=n,
                confidence=confidence,
                lag1_up=np.array([float(r)]),
                rho_limit=np.array([limit]),
            )[0]
        )
        half = Decimal(crit) * var.sqrt()
        return (mean_d - half).exp(), (mean_d + half).exp()


@pytest.mark.parametrize("scale", [1e-9, 1e-3, 1.0, 3.0])
def test_bounds_are_never_more_favourable_than_exact_arithmetic(
    scale: float,
) -> None:
    rng = np.random.default_rng(11)
    rows = 8
    n = 14
    d = scale * rng.standard_normal((rows, n)) + 0.01
    ratios = np.exp(d)
    first = np.zeros((rows, n), dtype=np.bool_)
    first[:, 0::2] = True
    ones = np.ones_like(ratios)
    made = hac_lf_interval(
        ratios,
        ones,
        direction="lower_is_better",
        confidence=CONF,
        candidate_first=first,
        mode="one_host",
    )
    for i in range(rows):
        lo, hi = _decimal_reference(
            ratios[i], first[i], CONF, intervals.UCB_Z, "one_host"
        )
        assert Decimal(float(made.interval.lower[i])) <= lo
        assert Decimal(float(made.interval.upper[i])) >= hi


def test_bounds_stay_conservative_on_extreme_inputs() -> None:
    n = 12
    first = np.zeros((1, n), dtype=np.bool_)
    first[0, 0::2] = True
    cases = [
        1.0 + 1e-12 * np.arange(n, dtype=np.float64),
        np.exp(np.linspace(-200.0, 200.0, n)),
        np.where(np.arange(n) % 2 == 0, 1e-100, 1e100),
    ]
    for ratios in cases:
        made = hac_lf_interval(
            ratios[None, :],
            np.ones((1, n)),
            direction="lower_is_better",
            confidence=CONF,
            candidate_first=first,
            mode="one_host",
        )
        lo, hi = _decimal_reference(
            ratios, first[0], CONF, intervals.UCB_Z, "one_host"
        )
        assert Decimal(float(made.interval.lower[0])) <= lo
        assert Decimal(float(made.interval.upper[0])) >= hi


def test_independent_noise_keeps_the_floor_and_dependent_noise_widens() -> (
    None
):
    b, c, first = _data(400, 120, seed=4, rho=0.0)
    iid = hac_lf_interval(
        b,
        c,
        direction="lower_is_better",
        confidence=CONF,
        candidate_first=first,
        mode="one_host",
    )
    assert np.mean(iid.rho_limit == RHO_FLOOR) > 0.9
    b, c, first = _data(400, 120, seed=5, rho=0.8)
    dep = hac_lf_interval(
        b,
        c,
        direction="lower_is_better",
        confidence=CONF,
        candidate_first=first,
        mode="one_host",
    )
    assert np.mean(dep.rho_limit >= 0.7) > 0.9
    assert np.all(dep.ucb >= dep.lag1)
    width_iid = np.median(iid.interval.upper / iid.interval.lower)
    width_dep = np.median(dep.interval.upper / dep.interval.lower)
    assert width_dep > width_iid


def test_a_larger_z_never_narrows_the_interval() -> None:
    b, c, first = _data(200, 40, seed=6, rho=0.3)
    low = _run(b, c, first, ucb_z=0.5)
    high = _run(b, c, first, ucb_z=3.0)
    assert np.all(high.interval.upper >= low.interval.upper)
    assert np.all(high.interval.lower <= low.interval.lower)


def test_decide_look_records_the_dependence_and_withholds_nothing() -> None:
    b, c, first = _data(60, 30, seed=7, shift=0.12)
    look = decide_look(
        b,
        c,
        direction="lower_is_better",
        confidence=CONF,
        min_effect=0.03,
        calibrated=True,
        final=False,
        resolution=None,
        procedure="hac_lf",
        candidate_first=first,
    )
    assert look.lag1 is not None
    assert look.ucb is not None
    assert look.rho_limit is not None
    assert np.all(np.isfinite(look.lag1))
    assert np.all(look.rho_limit >= RHO_FLOOR)
    assert np.all(look.withheld == "")
    assert np.all(np.isin(look.rule, (2, 3, 4)))


def test_hac_lf_has_no_interval_below_twelve_observations() -> None:
    b, c, first = _data(5, 10, seed=8)
    look = decide_look(
        b,
        c,
        direction="lower_is_better",
        confidence=CONF,
        min_effect=0.03,
        calibrated=True,
        final=np.array([False, False, True, True, False]),
        resolution=None,
        procedure="hac_lf",
        candidate_first=first,
    )
    assert np.all(np.isnan(look.lower))
    assert np.all(np.isnan(look.lag1 if look.lag1 is not None else 1.0))
    assert list(look.rule) == [4, 4, 5, 5, 4]


def test_cross_host_looks_take_no_order_flags() -> None:
    b, c, _ = _data(10, 20, seed=9, shift=0.1)
    look = decide_look(
        b,
        c,
        direction="lower_is_better",
        confidence=CONF,
        min_effect=0.03,
        calibrated=True,
        final=False,
        resolution=None,
        n0=10,
        procedure="hac_lf",
        candidate_first=None,
    )
    assert look.lag1 is not None
    assert np.all(np.isfinite(look.lag1))


def test_run_looks_with_hac_lf_records_every_look() -> None:
    b, c, first = _data(50, 60, seed=10, shift=0.15)
    result = run_looks(
        b,
        c,
        direction="lower_is_better",
        schedule=LookSchedule(n0=10, k=10, max_looks=6),
        mode="one_host",
        min_effect=0.03,
        calibrated=True,
        resolution=None,
        exhausted=True,
        procedure="hac_lf",
        candidate_first=first,
    )
    assert result.procedure == "hac_lf"
    assert result.lag1 is not None
    assert result.rho_limit is not None
    taken = result.rounds > 0
    # the first look at 10 rounds has no interval; later looks do.
    assert np.all(np.isnan(result.lag1[:, 0]))
    assert np.all(np.isfinite(result.lag1[taken & (result.rounds >= 20)]))
    assert np.all(result.withheld == "")
    assert set(np.unique(result.outcome)) <= {"KEEP", "REJECT", "INCONCLUSIVE"}


def test_the_rho_limit_is_rounded_up_exactly() -> None:
    got = intervals._limit_index(  # pyright: ignore[reportPrivateUsage]
        np.array([0.0, 0.3, 0.3 + 5e-13, 0.31, 0.9, 0.9 + 1e-3])
    )
    assert list(got) == [0, 0, 1, 1, 6, 6]


def test_a_look_past_the_last_tabulated_length_uses_the_last_node() -> None:
    r = np.linspace(-0.5, 0.9, 20)
    limit = np.full(r.shape, 0.4)
    last = lf_tables.NODES[-1]
    at = lf_critical(
        mode="one_host", n=last, confidence=CONF, lag1_up=r, rho_limit=limit
    )
    for n in (last + 2, 200, 1000):
        past = lf_critical(
            mode="one_host", n=n, confidence=CONF, lag1_up=r, rho_limit=limit
        )
        assert np.all(past == at)


def test_bad_self_check_arguments_fail_before_any_look() -> None:
    b, c, first = _data(4, 20, seed=12)
    with pytest.raises(ValueError, match="ucb_z"):
        run_looks(
            b,
            c,
            direction="lower_is_better",
            schedule=LookSchedule(n0=10, k=10, max_looks=3),
            mode="one_host",
            min_effect=0.03,
            calibrated=True,
            resolution=None,
            exhausted=True,
            procedure="hac_lf",
            candidate_first=first,
            ucb_z=-1.0,
        )
    with pytest.raises(ValueError, match="rho_floor"):
        decide_look(
            b,
            c,
            direction="lower_is_better",
            confidence=CONF,
            min_effect=0.03,
            calibrated=True,
            final=False,
            resolution=None,
            procedure="hac_lf",
            candidate_first=first,
            rho_floor=0.95,
        )
    # integers are numbers too, and are accepted
    _run(b, c, first, ucb_z=1, rho_floor=0.3)  # pyright: ignore[reportArgumentType]


def test_row_chunks_cover_every_row_once_within_the_budget() -> None:
    chunk = intervals._row_chunks  # pyright: ignore[reportPrivateUsage]
    budget = intervals._HAC_CHUNK_BYTES  # pyright: ignore[reportPrivateUsage]
    per = intervals._HAC_TEMPORARIES  # pyright: ignore[reportPrivateUsage]
    for rows, n in [(0, 12), (1, 12), (4000, 60), (100_000, 120), (7, 5000)]:
        spans = list(chunk(rows, n))
        assert spans, "at least one range, even for no rows"
        assert spans[0][0] == 0
        assert spans[-1][1] == rows
        for (_, stop), (start, _) in itertools.pairwise(spans):
            assert start == stop
        for start, stop in spans:
            assert stop - start <= max(1, budget // (per * 8 * n))
            if rows >= 1:
                assert stop > start


def test_chunking_changes_no_value(monkeypatch: pytest.MonkeyPatch) -> None:
    b, c, first = _data(37, 24, seed=5, shift=0.04, rho=0.4)
    whole = _run(b, c, first)
    # A budget of a few rows' worth of temporaries forces many chunks.
    monkeypatch.setattr(intervals, "_HAC_CHUNK_BYTES", 5 * 16 * 8 * 24)
    chunks = list(
        intervals._row_chunks(37, 24)  # pyright: ignore[reportPrivateUsage]
    )
    assert len(chunks) > 5
    split = _run(b, c, first)
    for name in ("estimate", "lower", "upper"):
        np.testing.assert_array_equal(
            getattr(split.interval, name), getattr(whole.interval, name)
        )
    for name in ("lag1", "ucb", "rho_limit"):
        np.testing.assert_array_equal(
            getattr(split, name), getattr(whole, name)
        )


def test_no_rows_give_empty_results() -> None:
    b, c, first = _data(0, 12)
    got = _run(b, c, first)
    assert got.interval.lower.shape == (0,)
    assert got.rho_limit.shape == (0,)
