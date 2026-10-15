"""Tests for `perfproof.stats.simulate`.

How the tolerances are set
--------------------------
Every effect is checked by comparing a sample statistic with the value
its parameter implies, over ``R`` independent replications. Two kinds
of standard error are used:

- **Rates** (tail mass, bimodal fraction, outlier rate, the order
  coin) are counts of independent Bernoulli events, so their standard
  error under the parameter is exactly ``sqrt(p (1 - p) / N)``.
- **Everything else** (CV, drift slope, variance step, lag-1
  autocorrelation, order effect, offset spread, mean log ratio) is
  computed once per group of replications. Replications are
  independent and identically distributed by construction, so the
  ``G`` group values are too, and their spread gives the Monte Carlo
  standard error of the mean of the group values. Groups hold
  thousands of observations, so the finite-sample bias of ratio and
  standard-deviation estimators, of order ``1 / (group size)``, is a
  small fraction of that standard error.

A check passes when the estimate is within ``Z = 5`` standard errors
of the target. Under a correct simulator the estimate is close to
normal (a mean of 100 i.i.d. group values, or a binomial count in the
thousands), so a correct simulator fails one check with probability
about ``6e-7``; across the roughly 120 checks here the family-wise
chance is below ``1e-4``. The seed is fixed, so the outcome is
deterministic.

A tolerance of five standard errors is meaningful only if the standard
error is small, so every check also asserts that ``Z * se`` is below a
stated cap (``max_tol``), chosen per statistic and given next to it. A
simulator that inflated the variance cannot pass by widening its own
tolerance.
"""

import math
import tracemalloc
from collections.abc import Callable
from dataclasses import fields, replace
from typing import cast

import numpy as np
import pytest
from numpy.typing import NDArray
from scipy import stats  # pyright: ignore[reportMissingTypeStubs]

from perfproof.stats import simulate as sim_module
from perfproof.stats.simulate import (
    LOGNORMAL_CVS,
    HostMode,
    NoiseScenario,
    Simulated,
    development_scenarios,
    simulate,
)

R = 20_000
N = 50
G = 100
Z = 5.0
SEED = 20261016
MODES: tuple[HostMode, ...] = ("one_host", "cross_host")

FloatArray = NDArray[np.float64]


def log_sd(cv: float) -> float:
    """Log-scale sd of a log-normal variable with this CV."""
    return math.sqrt(math.log1p(cv * cv))


def run(
    scenario: NoiseScenario,
    *,
    mode: HostMode = "one_host",
    speedup: float = 1.0,
    n: int = N,
    replications: int = R,
    seed: int = SEED,
) -> Simulated:
    return simulate(
        scenario,
        speedup=speedup,
        n=n,
        replications=replications,
        mode=mode,
        rng=np.random.default_rng(seed),
    )


def log_ratio(sample: Simulated) -> FloatArray:
    """``d = log(baseline / candidate)``, shape ``(R, n)``."""
    x = sample.log_times
    return x[..., 0] - x[..., 1]


def groups(x: FloatArray) -> FloatArray:
    """Split the leading replication axis into ``G`` groups."""
    return x.reshape(G, x.shape[0] // G, *x.shape[1:])


def assert_mc(
    per_group: FloatArray, target: float, max_tol: float
) -> tuple[float, float]:
    """Mean of group values within ``Z`` standard errors of target."""
    assert per_group.shape == (G,)
    est = float(per_group.mean())
    se = float(per_group.std(ddof=1)) / math.sqrt(G)
    assert Z * se <= max_tol, f"se {se:.3g} too large for cap {max_tol}"
    assert abs(est - target) <= Z * se, (
        f"estimate {est:.6g}, target {target:.6g}, se {se:.3g}"
    )
    return est, se


def assert_rate(count: int, total: int, p: float, max_rel: float) -> None:
    """Binomial count within ``Z`` exact standard errors of ``p``."""
    se = math.sqrt(p * (1 - p) / total)
    assert Z * se <= max_rel * p, f"se {se:.3g} too large for p {p}"
    est = count / total
    assert abs(est - p) <= Z * se, (
        f"rate {est:.6g}, target {p:.6g}, se {se:.3g}"
    )


def t_two_sided_tail(threshold: float, df: float | None) -> float:
    """``P(|X| > threshold)`` for X standardized to unit variance."""
    if df is None:
        sf = cast(
            "Callable[[float], float]",
            stats.norm.sf,  # pyright: ignore[reportUnknownMemberType]
        )
        return 2 * float(sf(threshold))
    sf_t = cast(
        "Callable[[float, float], float]",
        stats.t.sf,  # pyright: ignore[reportUnknownMemberType]
    )
    return 2 * float(sf_t(threshold * math.sqrt(df / (df - 2)), df))


class TestShapeAndDesign:
    @pytest.mark.parametrize("mode", MODES)
    def test_shapes_dtypes_and_finite_values(self, mode: HostMode) -> None:
        sample = run(NoiseScenario(cv=0.05), mode=mode, replications=40)
        assert sample.log_times.shape == (40, N, 2)
        assert sample.log_times.dtype == np.float64
        assert sample.candidate_first.shape == (40, N)
        assert sample.candidate_first.dtype == np.bool_
        assert np.all(np.isfinite(sample.log_times))

    def test_one_host_order_is_a_fair_coin_per_round(self) -> None:
        first = run(NoiseScenario(cv=0.05)).candidate_first
        assert_rate(int(first.sum()), first.size, 0.5, 0.01)
        # Independent rounds agree with their neighbour half the time.
        same = first[:, 1:] == first[:, :-1]
        assert_rate(int(same.sum()), same.size, 0.5, 0.01)

    def test_cross_host_baseline_session_always_first(self) -> None:
        sample = run(NoiseScenario(cv=0.05), mode="cross_host")
        assert not sample.candidate_first.any()

    def test_noiseless_log_times_are_exact(self) -> None:
        sample = run(NoiseScenario(cv=0.0), speedup=1.25, replications=3)
        np.testing.assert_array_equal(sample.log_times[..., 0], 0.0)
        np.testing.assert_array_equal(
            sample.log_times[..., 1], -math.log(1.25)
        )


class TestReproducibility:
    SCENARIO = NoiseScenario(
        cv=0.05,
        t_df=4.0,
        slow_fraction=0.1,
        slow_factor=1.3,
        outlier_rate=0.02,
        outlier_factor=3.0,
        drift=0.1,
        variance_step=2.0,
        ar1=0.5,
        order_effect=0.03,
        session_offset_cv=0.15,
    )

    @staticmethod
    def scenario(df: float | None, mode: HostMode) -> NoiseScenario:
        """Every effect the mode allows, normal or t innovations."""
        one_host = mode == "one_host"
        return replace(
            TestReproducibility.SCENARIO,
            t_df=df,
            drift=0.1 if one_host else 0.0,
            order_effect=0.03 if one_host else 0.0,
        )

    @pytest.mark.parametrize("df", [None, 4.0])
    @pytest.mark.parametrize("mode", MODES)
    def test_same_seed_same_arrays(
        self, df: float | None, mode: HostMode
    ) -> None:
        scenario = self.scenario(df, mode)
        a = run(scenario, mode=mode, replications=200)
        b = run(scenario, mode=mode, replications=200)
        np.testing.assert_array_equal(a.log_times, b.log_times)
        np.testing.assert_array_equal(a.candidate_first, b.candidate_first)

    @pytest.mark.parametrize("df", [None, 4.0])
    @pytest.mark.parametrize("mode", MODES)
    def test_different_seed_different_arrays(
        self, df: float | None, mode: HostMode
    ) -> None:
        scenario = self.scenario(df, mode)
        a = run(scenario, mode=mode, replications=200)
        b = run(scenario, mode=mode, replications=200, seed=SEED + 1)
        assert not np.array_equal(a.log_times, b.log_times)

    @pytest.mark.parametrize("df", [None, 4.0])
    @pytest.mark.parametrize("mode", MODES)
    def test_global_rng_is_untouched(
        self, df: float | None, mode: HostMode
    ) -> None:
        np.random.seed(7)  # noqa: NPY002
        before = np.random.get_state()  # noqa: NPY002
        run(self.scenario(df, mode), mode=mode, replications=50)
        after = np.random.get_state()  # noqa: NPY002
        np.testing.assert_array_equal(before[1], after[1])
        assert before[2:] == after[2:]

    def test_draws_come_only_from_the_given_generator(self) -> None:
        rng = np.random.default_rng(SEED)
        simulate(
            self.SCENARIO,
            speedup=1.0,
            n=N,
            replications=10,
            mode="one_host",
            rng=rng,
        )
        untouched = np.random.default_rng(SEED)
        assert rng.bit_generator.state != untouched.bit_generator.state

    def test_working_memory_is_bounded_by_the_chunk_budget(self) -> None:
        replications, n = 600, 10_000
        out_bytes = replications * n * (2 * 8 + 1)
        tracemalloc.start()
        try:
            sample = run(self.SCENARIO, n=n, replications=replications)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert sample.log_times.nbytes + sample.candidate_first.nbytes == (
            out_bytes
        )
        # Peak beyond the output stays within a small multiple of the
        # chunk budget; generating all at once would need several
        # output-sized temporaries (several times 96 MB here).
        assert peak - out_bytes <= 8 * sim_module.CHUNK_BYTES


class TestNoiseLevel:
    @pytest.mark.parametrize("cv", LOGNORMAL_CVS)
    @pytest.mark.parametrize("arm", [0, 1])
    def test_cv_of_run_times(self, cv: float, arm: int) -> None:
        log_times = run(NoiseScenario(cv=cv), speedup=1.1).log_times
        times = np.exp(log_times[..., arm])
        g = groups(times).reshape(G, -1)
        per_group = g.std(axis=1, ddof=1) / g.mean(axis=1)
        # Cap: 2% of the CV; at 10^4 runs per group the CV's relative
        # standard error is under 1%, so the cap has room.
        assert_mc(per_group, cv, 0.02 * cv)

    @pytest.mark.parametrize("df", [None, 10.0])
    def test_log_scale_sd_matches_cv_with_any_tails(
        self, df: float | None
    ) -> None:
        cv = 0.05
        x = run(NoiseScenario(cv=cv, t_df=df)).log_times[..., 0]
        per_group = np.sqrt((groups(x).reshape(G, -1) ** 2).mean(axis=1))
        # Mean is known (0), so the root mean square is the sd. Cap 1%.
        assert_mc(per_group, log_sd(cv), 0.01 * log_sd(cv))

    @pytest.mark.parametrize("df", [None, 2.5, 3.0, 10.0])
    @pytest.mark.parametrize("threshold", [1.0, 3.0])
    def test_tail_mass_of_standardized_log_noise(
        self, df: float | None, threshold: float
    ) -> None:
        cv = 0.05
        sample = run(NoiseScenario(cv=cv, t_df=df), replications=2 * R)
        z = sample.log_times / log_sd(cv)
        count = int((np.abs(z) > threshold).sum())
        p = t_two_sided_tail(threshold, df)
        # Cap: 10% of the tail mass; with 4e6 draws the binomial se is
        # at most 1.6% of p here.
        assert_rate(count, z.size, p, 0.10)

    def test_t3_tails_are_heavier_than_normal(self) -> None:
        assert t_two_sided_tail(3.0, 3.0) > 4 * t_two_sided_tail(3.0, None)

    @pytest.mark.parametrize("big", [1e300, 1e-300])
    @pytest.mark.parametrize("mode", MODES)
    def test_log_times_stay_finite_where_times_would_not(
        self, big: float, mode: HostMode
    ) -> None:
        # Rates of 1 and no noise make every value deterministic, and
        # the factors put every log time past what exp can represent in
        # float64 (above log(max) or below log(smallest subnormal)), as
        # heavy t tails occasionally do in the audit ranges. The log
        # times must stay finite and exact, with E[d] = log(S) intact.
        timeline = mode == "one_host" and big > 1
        scenario = NoiseScenario(
            cv=0.0,
            slow_fraction=1.0,
            slow_factor=big,
            outlier_rate=1.0,
            outlier_factor=big,
            drift=1e300 if timeline else 0.0,
            order_effect=0.05 if timeline else 0.0,
        )
        n, speedup = 20, 1.03
        sample = run(
            scenario, mode=mode, speedup=speedup, n=n, replications=50
        )
        x = sample.log_times
        assert np.all(np.isfinite(x))
        tiny = np.finfo(np.float64).smallest_subnormal
        if big > 1:
            assert float(x.min()) > math.log(np.finfo(np.float64).max)
        else:
            assert float(x.max()) < math.log(tiny)
        step = math.log1p(scenario.drift) / (2 * n - 1)
        order = math.log1p(scenario.order_effect)
        swing = np.where(sample.candidate_first, step - order, order - step)
        np.testing.assert_allclose(
            log_ratio(sample), math.log(speedup) + swing, rtol=0, atol=1e-9
        )


class TestMixtures:
    @pytest.mark.parametrize(
        ("fraction", "factor"), [(0.10, 1.30), (0.30, 1.10)]
    )
    def test_bimodal_fraction_and_slow_mode(
        self, fraction: float, factor: float
    ) -> None:
        cv = 0.002
        scenario = NoiseScenario(
            cv=cv, slow_fraction=fraction, slow_factor=factor
        )
        x = run(scenario).log_times
        # The cut sits halfway between the modes, at least 19 log-scale
        # sds from either, so a run is misclassified with probability
        # below 1e-80. Rate caps here are 5% of the rate.
        cut = math.log(factor) / 2
        assert cut / log_sd(cv) > 19
        slow = x > cut
        assert_rate(int(slow.sum()), x.size, fraction, 0.05)
        mean_slow = float(x[slow].mean())
        se = log_sd(cv) / math.sqrt(int(slow.sum()))
        assert abs(mean_slow - math.log(factor)) <= Z * se

    @pytest.mark.parametrize(
        ("rate", "factor"), [(1 / 50, 3.0), (0.05, 2.0), (0.01, 5.0)]
    )
    def test_outlier_rate_and_size(self, rate: float, factor: float) -> None:
        cv = 0.01
        scenario = NoiseScenario(
            cv=cv, outlier_rate=rate, outlier_factor=factor
        )
        x = run(scenario).log_times
        cut = math.log(factor) / 2
        assert cut / log_sd(cv) > 30
        out = x > cut
        assert_rate(int(out.sum()), x.size, rate, 0.05)
        se = log_sd(cv) / math.sqrt(int(out.sum()))
        assert abs(float(x[out].mean()) - math.log(factor)) <= Z * se

    def test_slow_and_outlier_draws_are_independent(self) -> None:
        cv = 0.002
        scenario = NoiseScenario(
            cv=cv,
            slow_fraction=0.2,
            slow_factor=1.2,
            outlier_rate=0.1,
            outlier_factor=3.0,
        )
        x = run(scenario).log_times
        both = x > math.log(1.2 * 3.0) - math.log(1.2) / 2
        assert_rate(int(both.sum()), x.size, 0.2 * 0.1, 0.05)


class TestTimeline:
    @pytest.mark.parametrize("drift", [0.10, 0.20, -0.05])
    def test_drift_slope_across_the_session(self, drift: float) -> None:
        sample = run(NoiseScenario(cv=0.05, drift=drift))
        x = sample.log_times
        second = np.stack(
            (sample.candidate_first, ~sample.candidate_first), axis=-1
        )
        pos = 2 * np.arange(N)[:, np.newaxis] + second
        xs = pos.reshape(R, -1).astype(np.float64)
        ys = x.reshape(R, -1)
        xc = xs - xs.mean(axis=1, keepdims=True)
        slope = (xc * ys).sum(axis=1) / (xc * xc).sum(axis=1)
        total = groups(slope * (2 * N - 1)).mean(axis=1)
        target = math.log1p(drift)
        # Cap: 2% of the total drift.
        assert_mc(total, target, 0.02 * abs(target))

    @pytest.mark.parametrize(
        ("factor", "at"), [(2.0, 0.5), (4.0, 0.3), (1.5, 0.82)]
    )
    @pytest.mark.parametrize("mode", MODES)
    def test_variance_step(
        self, factor: float, at: float, mode: HostMode
    ) -> None:
        cv = 0.05
        scenario = NoiseScenario(
            cv=cv, variance_step=factor, variance_step_at=at
        )
        d = log_ratio(run(scenario, mode=mode))
        k = round(at * N)
        assert k == at * N
        before = np.sqrt((groups(d[:, :k]) ** 2).mean(axis=(1, 2)))
        after = np.sqrt((groups(d[:, k:]) ** 2).mean(axis=(1, 2)))
        target = log_sd(cv * factor) / log_sd(cv)
        # Cap: 2% of the sd ratio.
        assert_mc(after / before, target, 0.02 * target)
        # The step lands exactly at round k: the rounds either side of
        # it have the sds of their regimes.
        last = np.sqrt((groups(d[:, k - 1]) ** 2).mean(axis=1))
        first = np.sqrt((groups(d[:, k]) ** 2).mean(axis=1))
        sd = math.sqrt(2) * log_sd(cv)
        assert_mc(last, sd, 0.03 * sd)
        assert_mc(first, sd * target, 0.03 * sd * target)

    def test_cv_after_the_step(self) -> None:
        cv, factor = 0.05, 2.0
        scenario = NoiseScenario(
            cv=cv, variance_step=factor, variance_step_at=0.5
        )
        times = np.exp(run(scenario).log_times[:, N // 2 :, 0])
        g = groups(times).reshape(G, -1)
        per_group = g.std(axis=1, ddof=1) / g.mean(axis=1)
        assert_mc(per_group, cv * factor, 0.02 * cv * factor)

    @pytest.mark.parametrize("rho", [0.3, 0.5, 0.7, -0.4])
    @pytest.mark.parametrize("mode", MODES)
    def test_ar1_autocorrelation_of_log_ratios(
        self, rho: float, mode: HostMode
    ) -> None:
        cv = 0.05
        d = log_ratio(run(NoiseScenario(cv=cv, ar1=rho), mode=mode))
        g = groups(d)
        var = (g**2).mean(axis=(1, 2))
        lag1 = (g[:, :, 1:] * g[:, :, :-1]).mean(axis=(1, 2)) / var
        lag2 = (g[:, :, 2:] * g[:, :, :-2]).mean(axis=(1, 2)) / var
        # The mean of d is known (0), so these are unbiased up to the
        # ratio; caps 0.01 absolute.
        assert_mc(lag1, rho, 0.01)
        assert_mc(lag2, rho * rho, 0.01)
        sd = math.sqrt(2) * log_sd(cv)
        assert_mc(np.sqrt(var), sd, 0.02 * sd)

    def test_no_autocorrelation_by_default(self) -> None:
        g = groups(log_ratio(run(NoiseScenario(cv=0.05))))
        var = (g**2).mean(axis=(1, 2))
        lag1 = (g[:, :, 1:] * g[:, :, :-1]).mean(axis=(1, 2)) / var
        assert_mc(lag1, 0.0, 0.01)


class TestOrderAndOffsets:
    @pytest.mark.parametrize("effect", [0.0, 0.03, 0.05])
    def test_order_effect_favours_the_second_run(self, effect: float) -> None:
        sample = run(NoiseScenario(cv=0.05, order_effect=effect))
        d = log_ratio(sample)
        first = sample.candidate_first
        gd, gf = groups(d), groups(first.astype(np.float64)) > 0.5
        per_group = np.array(
            [
                (gd[j][~gf[j]].mean() - gd[j][gf[j]].mean()) / 2
                for j in range(G)
            ]
        )
        # Candidate second gains log1p(effect), candidate first loses
        # it, so half the difference is the effect. Cap 0.001 absolute,
        # a tenth of the smallest step between calibration cells.
        assert_mc(per_group, math.log1p(effect), 0.001)

    def test_one_host_offset_is_shared_within_a_round(self) -> None:
        cv, offset = 0.05, 0.15
        sample = run(NoiseScenario(cv=cv, session_offset_cv=offset))
        x = sample.log_times[..., 0]
        spread = np.sqrt((groups(x).reshape(G, -1) ** 2).mean(axis=1))
        target = math.hypot(log_sd(cv), log_sd(offset))
        assert_mc(spread, target, 0.02 * target)
        d = log_ratio(sample)
        sd_d = np.sqrt((groups(d).reshape(G, -1) ** 2).mean(axis=1))
        sd = math.sqrt(2) * log_sd(cv)
        assert_mc(sd_d, sd, 0.02 * sd)
        # Shared by the two runs of a round, fresh in the next round.
        g = groups(sample.log_times)
        var = (g**2).mean(axis=(1, 2, 3))
        arms = (g[..., 0] * g[..., 1]).mean(axis=(1, 2)) / var
        rounds = (g[:, :, 1:, 0] * g[:, :, :-1, 0]).mean(axis=(1, 2)) / var
        share = log_sd(offset) ** 2 / target**2
        assert_mc(arms, share, 0.01)
        assert_mc(rounds, 0.0, 0.01)

    def test_cross_host_offset_is_drawn_per_session(self) -> None:
        cv, offset = 0.05, 0.15
        sample = run(
            NoiseScenario(cv=cv, session_offset_cv=offset), mode="cross_host"
        )
        d = log_ratio(sample)
        sd_d = np.sqrt((groups(d).reshape(G, -1) ** 2).mean(axis=1))
        target = math.sqrt(2) * math.hypot(log_sd(cv), log_sd(offset))
        assert_mc(sd_d, target, 0.02 * target)
        # Offsets are independent between sessions and between pairs.
        x = sample.log_times
        g = groups(x)
        var = (g**2).mean(axis=(1, 2, 3))
        arms = (g[..., 0] * g[..., 1]).mean(axis=(1, 2)) / var
        pairs = (g[:, :, 1:, 0] * g[:, :, :-1, 0]).mean(axis=(1, 2)) / var
        assert_mc(arms, 0.0, 0.01)
        assert_mc(pairs, 0.0, 0.01)


COMBINED_ONE_HOST = NoiseScenario(
    cv=0.05,
    t_df=3.0,
    slow_fraction=0.1,
    slow_factor=1.3,
    outlier_rate=0.02,
    outlier_factor=3.0,
    drift=0.2,
    variance_step=4.0,
    variance_step_at=0.37,
    ar1=0.7,
    order_effect=0.05,
    session_offset_cv=0.15,
)
COMBINED_CROSS_HOST = replace(COMBINED_ONE_HOST, drift=0.0, order_effect=0.0)


class TestTrueSpeedup:
    @pytest.mark.parametrize("speedup", [0.95, 1.0, 1.03, 1.04, 1.2])
    @pytest.mark.parametrize("mode", MODES)
    def test_geometric_mean_speedup_is_recovered(
        self, speedup: float, mode: HostMode
    ) -> None:
        scenario = (
            COMBINED_ONE_HOST if mode == "one_host" else COMBINED_CROSS_HOST
        )
        d = log_ratio(run(scenario, mode=mode, speedup=speedup))
        per_group = groups(d).reshape(G, -1).mean(axis=1)
        # Cap 0.004 on log(S), under half of log(1.04 / 1.03), the gap
        # between the calibration cells either side of the bar. Every
        # effect at once is the noisiest case; the presets below are
        # checked one effect at a time with far smaller errors.
        est, _ = assert_mc(per_group, math.log(speedup), 0.004)
        assert math.exp(est) == pytest.approx(speedup, rel=0.004)

    @pytest.mark.parametrize(
        "scenario",
        list(development_scenarios(0.05).values()),
        ids=list(development_scenarios(0.05)),
    )
    def test_every_development_scenario_recovers_the_speedup(
        self, scenario: NoiseScenario
    ) -> None:
        for mode in MODES:
            if mode == "cross_host" and scenario.one_host_only:
                continue
            d = log_ratio(run(scenario, mode=mode, speedup=1.03))
            per_group = groups(d).reshape(G, -1).mean(axis=1)
            assert_mc(per_group, math.log(1.03), 0.002)


class TestPresets:
    def test_development_scenarios_match_the_method(self) -> None:
        dev = development_scenarios(0.02)
        assert dev == {
            "lognormal_cv1": NoiseScenario(cv=0.01),
            "lognormal_cv5": NoiseScenario(cv=0.05),
            "lognormal_cv15": NoiseScenario(cv=0.15),
            "student_t_df3": NoiseScenario(cv=0.02, t_df=3.0),
            "bimodal": NoiseScenario(
                cv=0.02, slow_fraction=0.10, slow_factor=1.30
            ),
            "outliers": NoiseScenario(
                cv=0.02, outlier_rate=1 / 50, outlier_factor=3.0
            ),
            "drift": NoiseScenario(cv=0.02, drift=0.10),
            "variance_step": NoiseScenario(
                cv=0.02, variance_step=2.0, variance_step_at=0.5
            ),
            "ar1": NoiseScenario(cv=0.02, ar1=0.5),
            "order_effect": NoiseScenario(cv=0.02, order_effect=0.03),
            "session_offset": NoiseScenario(cv=0.02, session_offset_cv=0.15),
        }
        assert LOGNORMAL_CVS == (0.01, 0.05, 0.15)

    def test_only_drift_and_order_are_one_host_only(self) -> None:
        dev = development_scenarios(0.02)
        only = {name for name, s in dev.items() if s.one_host_only}
        assert only == {"drift", "order_effect"}

    @pytest.mark.parametrize("corner", ["low", "high"])
    @pytest.mark.parametrize("mode", MODES)
    def test_audit_range_corners_are_expressible(
        self, corner: str, mode: HostMode
    ) -> None:
        low = corner == "low"
        scenario = NoiseScenario(
            cv=0.005 if low else 0.20,
            t_df=10.0 if low else 2.5,
            outlier_rate=0.0 if low else 0.05,
            outlier_factor=2.0 if low else 5.0,
            drift=0.0 if low or mode == "cross_host" else 0.20,
            variance_step=1.0 if low else 4.0,
            variance_step_at=float(np.random.default_rng(SEED).random()),
            ar1=0.0 if low else 0.7,
            order_effect=0.0 if low or mode == "cross_host" else 0.05,
        )
        sample = run(scenario, mode=mode, replications=50)
        assert np.all(np.isfinite(sample.log_times))


class TestValidation:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("cv", -0.01),
            ("cv", math.nan),
            ("cv", math.inf),
            ("cv", True),
            ("cv", "0.05"),
            ("t_df", 2.0),
            ("t_df", 1.5),
            ("t_df", math.inf),
            ("slow_fraction", -0.1),
            ("slow_fraction", 1.1),
            ("slow_factor", 0.0),
            ("outlier_rate", 1.5),
            ("outlier_factor", -2.0),
            ("drift", -1.0),
            ("variance_step", 0.0),
            ("variance_step_at", -0.1),
            ("variance_step_at", 1.1),
            ("ar1", 1.0),
            ("ar1", -1.0),
            ("order_effect", -1.0),
            ("session_offset_cv", -0.1),
        ],
    )
    def test_scenario_refuses_out_of_range(
        self, field: str, value: object
    ) -> None:
        kwargs: dict[str, object] = {"cv": 0.05, field: value}
        with pytest.raises(ValueError, match=field):
            NoiseScenario(**kwargs)  # pyright: ignore[reportArgumentType]

    def test_every_field_is_validated(self) -> None:
        names = {f.name for f in fields(NoiseScenario)}
        assert names == {
            "cv",
            "t_df",
            "slow_fraction",
            "slow_factor",
            "outlier_rate",
            "outlier_factor",
            "drift",
            "variance_step",
            "variance_step_at",
            "ar1",
            "order_effect",
            "session_offset_cv",
        }

    @pytest.mark.parametrize(
        "scenario",
        [
            NoiseScenario(cv=0.05, drift=0.1),
            NoiseScenario(cv=0.05, order_effect=0.03),
        ],
    )
    def test_cross_host_refuses_within_host_effects(
        self, scenario: NoiseScenario
    ) -> None:
        assert scenario.one_host_only
        with pytest.raises(ValueError, match="cross_host"):
            run(scenario, mode="cross_host", replications=2)

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("speedup", 0.0),
            ("speedup", -1.0),
            ("speedup", math.nan),
            ("speedup", math.inf),
            ("n", 0),
            ("n", 2.0),
            ("n", True),
            ("replications", 0),
            ("mode", "same_host"),
            ("rng", 5),
            ("rng", np.random.RandomState(1)),
        ],
    )
    def test_simulate_refuses_bad_arguments(
        self, name: str, value: object
    ) -> None:
        kwargs: dict[str, object] = {
            "speedup": 1.0,
            "n": 10,
            "replications": 2,
            "mode": "one_host",
            "rng": np.random.default_rng(1),
            name: value,
        }
        with pytest.raises(ValueError, match=name):
            simulate(
                NoiseScenario(cv=0.05),
                **kwargs,  # pyright: ignore[reportArgumentType]
            )

    def test_simulate_refuses_a_non_scenario(self) -> None:
        with pytest.raises(ValueError, match="scenario"):
            simulate(
                {"cv": 0.05},  # pyright: ignore[reportArgumentType]
                speedup=1.0,
                n=10,
                replications=2,
                mode="one_host",
                rng=np.random.default_rng(1),
            )

    def test_single_round_works_in_both_modes(self) -> None:
        for mode in MODES:
            scenario = (
                COMBINED_ONE_HOST
                if mode == "one_host"
                else COMBINED_CROSS_HOST
            )
            sample = run(scenario, mode=mode, n=1, replications=5)
            assert sample.log_times.shape == (5, 1, 2)
            assert np.all(np.isfinite(sample.log_times))
