"""Unit tests of T1.11's challenger arithmetic and tables."""

import csv
import math
from dataclasses import fields

import fixedb
import heldout
import inference as inf
import montecarlo as mc
import numpy as np
import pytest
from challengers import CHALLENGER_LIST
from numpy.typing import NDArray
from scipy import stats  # pyright: ignore[reportMissingTypeStubs]

from perfproof.stats.simulate import NoiseScenario

FloatArray = NDArray[np.float64]


def _flags(*first: bool) -> NDArray[np.bool_]:
    return np.array([first], dtype=np.bool_)


def test_order_centre_subtracts_each_orders_mean() -> None:
    d = np.array([[1.0, 3.0, 2.0, 6.0]])
    centred = inf.order_centre(d, _flags(True, False, True, False))
    np.testing.assert_allclose(centred, [[-0.5, -1.5, 0.5, 1.5]])


def test_order_centre_with_one_order_is_the_plain_centring() -> None:
    d = np.array([[1.0, 2.0, 6.0]])
    centred = inf.order_centre(d, _flags(False, False, False))
    np.testing.assert_allclose(centred, [[-2.0, -1.0, 3.0]])


def test_bartlett_variance_of_an_alternating_series_by_hand() -> None:
    # u = 1, -1, 1, -1; n = 4, b = 0.5 so M = 2: gamma_0 = 1 and
    # gamma_1 = (-1 - 1 - 1) / 4 = -3/4, long-run variance
    # gamma_0 + 2 (1 - 1/2) gamma_1 = 1/4, variance of the mean 1/16.
    u = np.array([[1.0, -1.0, 1.0, -1.0]])
    np.testing.assert_allclose(inf.bartlett_variance(u, 0.5), [1 / 16])


def test_bandwidth_is_at_least_two_and_below_n() -> None:
    assert inf.bandwidth(12, 0.1) == 2
    assert inf.bandwidth(30, 0.2) == 6
    assert inf.bandwidth(30, 0.3) == 9
    assert inf.bandwidth(12, 1.0) == 11


def test_huber_equals_the_mean_on_symmetric_clean_data() -> None:
    rng = np.random.default_rng(1)
    d = rng.standard_normal((200, 40))
    first = np.zeros(d.shape, dtype=np.bool_)
    theta, _ = inf.huber(d, first)
    # 95% efficient: close to the mean, not equal to it.
    assert np.abs(theta - d.mean(axis=1)).max() < 0.25


def test_huber_ignores_a_gross_outlier_the_mean_follows() -> None:
    rng = np.random.default_rng(2)
    d = 0.01 * rng.standard_normal((1, 30))
    first = np.zeros(d.shape, dtype=np.bool_)
    clean, _ = inf.huber(d, first)
    d[0, 5] = 50.0
    dirty, _ = inf.huber(d, first)
    assert abs(dirty[0] - clean[0]) < 0.01
    assert d.mean() > 1.0


def test_huber_of_identical_values_is_that_value() -> None:
    d = np.full((1, 12), 0.25)
    theta, influence = inf.huber(d, np.zeros(d.shape, dtype=np.bool_))
    assert theta[0] == pytest.approx(0.25)
    np.testing.assert_allclose(influence, 0.0, atol=1e-12)


def test_huber_is_per_order_so_an_order_effect_cancels() -> None:
    rng = np.random.default_rng(3)
    base = 0.02 * rng.standard_normal((50, 24))
    first = np.zeros(base.shape, dtype=np.bool_)
    first[:, 0::2] = True
    shifted = base + np.where(first, 0.05, -0.05)
    theta, _ = inf.huber(shifted, first)
    plain, _ = inf.huber(base, np.zeros(base.shape, dtype=np.bool_))
    assert np.abs(theta - plain).max() < 0.01


def test_sb_variance_is_positive_and_near_the_iid_value() -> None:
    rng = np.random.default_rng(4)
    z = rng.standard_normal((4000, 60))
    first = np.zeros(z.shape, dtype=np.bool_)
    v = inf.sb_variance(inf.order_centre(z, first))
    assert np.all(v > 0)
    assert abs(v.mean() * 60 - 1.0) < 0.1


def test_politis_white_length_is_within_its_cap_and_grows_with_rho() -> None:
    rng = np.random.default_rng(5)
    n = 60
    cap = math.ceil(min(3 * math.sqrt(n), n / 3))
    means: list[float] = []
    for rho in (0.0, 0.7):
        z = rng.standard_normal((2000, n))
        x = np.empty_like(z)
        x[:, 0] = z[:, 0]
        for i in range(1, n):
            x[:, i] = rho * x[:, i - 1] + math.sqrt(1 - rho**2) * z[:, i]
        b = inf.politis_white_length(
            inf.order_centre(x, np.zeros_like(x, bool))
        )
        assert b.min() >= 1.0
        assert b.max() <= cap
        means.append(float(b.mean()))
    assert means[1] > means[0] + 1.0


def test_auto_batch_length_is_even_bounded_and_nondecreasing() -> None:
    rho = np.linspace(-0.5, 0.95, 60)
    for n in (12, 30, 120):
        length = inf.auto_batch_length(rho, n, 1.0)
        assert np.all(length % 2 == 0)
        assert length.min() >= 2
        assert n // length.max() >= inf.MIN_BLOCKS or length.max() == 2
        assert np.all(np.diff(length) >= 0)


def test_batch_means_auto_on_whole_blocks_matches_the_t_interval() -> None:
    rng = np.random.default_rng(6)
    d = rng.standard_normal((1, 24))
    first = np.zeros(d.shape, dtype=np.bool_)
    # rho_hat clipped at 0 gives blocks of 2: B = 12 means of pairs.
    lo, hi = inf.batch_means_auto(
        d, first, confidence=0.99, scale=0.0, estimator="mean"
    )
    pairs = d.reshape(12, 2).mean(axis=1)
    crit = float(stats.t.ppf(0.995, 11))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    half = crit * pairs.std(ddof=1) / math.sqrt(12)
    assert lo[0] == pytest.approx(pairs.mean() - half)
    assert hi[0] == pytest.approx(pairs.mean() + half)


def test_heldout_scenarios_are_in_the_audit_ranges_and_recorded() -> None:
    assert len(heldout.HELDOUT) == heldout.COUNT >= 24
    for s in heldout.HELDOUT.values():
        assert 0.005 <= s.cv <= 0.2
        assert s.t_df is None or 2.5 <= s.t_df <= 10
        assert 0 <= s.outlier_rate <= 0.05
        assert s.outlier_rate == 0 or 2 <= s.outlier_factor <= 5
        assert 0 <= s.drift <= 0.2
        assert 1 <= s.variance_step <= 4
        assert 0 <= s.ar1 <= 0.7
        assert 0 <= s.order_effect <= 0.05
    with heldout.CSV_PATH.open(newline="") as source:
        rows = list(csv.DictReader(source))
    assert [r["scenario"] for r in rows] == list(heldout.HELDOUT)
    for row in rows:
        s = heldout.HELDOUT[row["scenario"]]
        for f in fields(NoiseScenario):
            assert row[f.name] == repr(getattr(s, f.name))
        assert row["seed"] == str(heldout.SEED)
    assert heldout.draw(heldout.SEED, heldout.COUNT) == heldout.HELDOUT


def test_grids_have_the_cells_the_report_needs() -> None:
    main = mc.cells_for("main")
    held = mc.cells_for("heldout")
    power = mc.cells_for("power")
    assert len(main) == 1344
    assert len(held) == len(heldout.HELDOUT) * 2 * 4 * len(mc.SPEEDUPS)
    assert len(power) == (11 + 9) * 4 * len(mc.POWER_SPEEDUPS)
    names = {c.name for c in (*main, *held, *power)}
    assert len(names) == len(main) + len(held) + len(power)
    for cell in (*held[:5], *power[:5]):
        assert mc.cell_by_name(cell.name) == cell


def test_the_iid_table_covers_every_look_length() -> None:
    table = fixedb.load()
    for s in fixedb.SPECS:
        for conf in fixedb.LEVELS:
            for n in range(fixedb.N_MIN, fixedb.N_MAX + 1):
                assert (s.name, "cross_host", n, round(conf, 10)) in table
                if n % 2 == 0:
                    assert (s.name, "one_host", n, round(conf, 10)) in table


def test_iid_critical_values_exceed_the_normal_and_fall_with_n() -> None:
    conf = fixedb.LEVELS[1]
    normal = float(stats.norm.ppf(0.5 + conf / 2))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    for s in fixedb.SPECS:
        values = [
            fixedb.critical(s.name, "one_host", n, conf)
            for n in (12, 24, 60, 120)
        ]
        assert all(v > normal for v in values)
        assert values[0] > values[-1]


def test_the_lf_table_is_nondecreasing_in_the_lag1_and_above_iid() -> None:
    for s in fixedb.LF_SPECS:
        for mode in ("one_host", "cross_host"):
            for n in fixedb.LF_N_NODES:
                for conf in fixedb.LEVELS:
                    values = fixedb.load_lf()[s.name, mode, n, round(conf, 10)]
                    assert values.shape == (len(fixedb.LF_EDGES) + 1,)
                    assert np.all(np.diff(values) >= 0)
    grid = np.linspace(-0.9, 0.99, 25)
    got = fixedb.lf_critical("lf_iid", "one_host", 25, fixedb.LEVELS[0], grid)
    assert np.all(np.diff(got) >= 0)


@pytest.mark.parametrize("challenger", CHALLENGER_LIST, ids=lambda c: c.name)
def test_challenger_bounds_invert_when_baseline_and_candidate_swap(
    challenger: object,
) -> None:
    from challengers import Challenger

    assert isinstance(challenger, Challenger)
    rng = np.random.default_rng(7)
    n = 24
    base = np.exp(0.05 * rng.standard_normal((40, n)))
    cand = np.exp(0.05 * rng.standard_normal((40, n)) - 0.04)
    first = np.zeros((40, n), dtype=np.bool_)
    first[:, 0::2] = True
    conf = fixedb.LEVELS[2]
    cov = rng.standard_normal(base.shape)
    lo, hi = challenger.bounds(base, cand, first, cov, "one_host", conf)
    lo2, hi2 = challenger.bounds(cand, base, first, -cov, "one_host", conf)
    np.testing.assert_allclose(1.0 / hi2, lo, rtol=1e-9)
    np.testing.assert_allclose(1.0 / lo2, hi, rtol=1e-9)
    lo3, hi3 = challenger.bounds(
        3.0 * base, 3.0 * cand, first, cov, "one_host", conf
    )
    np.testing.assert_allclose(lo3, lo, rtol=1e-9)
    np.testing.assert_allclose(hi3, hi, rtol=1e-9)


@pytest.mark.parametrize("challenger", CHALLENGER_LIST, ids=lambda c: c.name)
def test_challenger_has_no_interval_below_twelve_observations(
    challenger: object,
) -> None:
    from challengers import Challenger

    assert isinstance(challenger, Challenger)
    x = np.ones((3, 10))
    first = np.zeros((3, 10), dtype=np.bool_)
    lo, hi = challenger.bounds(x, x, first, x, "cross_host", fixedb.LEVELS[0])
    assert np.all(np.isnan(lo))
    assert np.all(np.isnan(hi))


def test_adjusted_fit_recovers_the_intercept_and_slope() -> None:
    rng = np.random.default_rng(8)
    rows, n = 400, 40
    x = rng.standard_normal((rows, n))
    first = np.zeros((rows, n), dtype=np.bool_)
    first[:, 0::2] = True
    d = 0.03 - 0.2 * x + 0.01 * rng.standard_normal((rows, n))
    d += np.where(first, 0.02, -0.02)
    fit = inf.adjusted_fit(d, first, x, 0.2)
    assert abs(fit.theta.mean() - 0.03) < 0.001
    assert abs(fit.slope.mean() + 0.2) < 0.002
    assert np.mean(np.abs(fit.theta - 0.03) < 3 * fit.se) > 0.95


def test_adjusted_fit_survives_a_covariate_that_never_varies() -> None:
    rng = np.random.default_rng(9)
    d = rng.standard_normal((5, 20))
    first = np.zeros(d.shape, dtype=np.bool_)
    fit = inf.adjusted_fit(d, first, np.zeros_like(d), 0.2)
    assert np.all(np.isfinite(fit.theta))
    assert np.all(np.isfinite(fit.se))
    np.testing.assert_allclose(fit.theta, d.mean(axis=1), atol=1e-12)


def test_shared_conditions_cancel_and_arm_conditions_bias() -> None:
    from conditions import COND

    from perfproof.stats.simulate import simulate

    rng = np.random.default_rng(10)
    mean_d: dict[str, float] = {}
    for name in ("COND-shared-throttle3", "COND-armbias-base"):
        cond = COND[name]
        drawn = simulate(
            cond.base,
            speedup=1.0,
            n=30,
            replications=600,
            mode="one_host",
            rng=rng,
        )
        x = cond.apply(drawn.log_times, drawn.candidate_first, rng)
        assert x.shape == (600, 30)
        d = drawn.log_times[..., 0] - drawn.log_times[..., 1]
        mean_d[name] = float(d.mean())
    assert abs(mean_d["COND-shared-throttle3"]) < 0.005
    assert mean_d["COND-armbias-base"] > 0.03


def test_sb_closed_form_matches_resampling() -> None:
    rng = np.random.default_rng(5)
    n, p, boots = 16, 0.3, 200_000
    x = np.cumsum(rng.standard_normal(n)) * 0.3 + rng.standard_normal(n)
    x -= x.mean()
    start = rng.integers(0, n, (boots, n))
    new = rng.random((boots, n)) < p
    new[:, 0] = True
    idx = np.empty((boots, n), dtype=np.int64)
    idx[:, 0] = start[:, 0]
    for i in range(1, n):
        idx[:, i] = np.where(new[:, i], start[:, i], (idx[:, i - 1] + 1) % n)
    resampled = float(x[idx].mean(axis=1).var())
    closed = inf.sb_variance_at(x[None, :], np.array([p]))[0]
    assert closed == pytest.approx(resampled, rel=0.02)
