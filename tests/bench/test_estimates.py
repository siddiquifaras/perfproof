import numpy as np
import pytest

from perfproof.bench.estimates import fit_exponent, geometric_mean

SIZES = [1000.0, 2000.0, 4000.0, 8000.0]


@pytest.mark.parametrize("exponent", [0.0, 1.0, 1.5, 2.0, 3.0])
def test_an_exact_power_law_gives_its_exponent(exponent: float) -> None:
    n = np.repeat(SIZES, 3)
    values = 1e-9 * n**exponent * np.tile([0.999, 1.0, 1.001], 4)
    fit = fit_exponent(n.tolist(), values.tolist())
    assert fit.exponent == pytest.approx(exponent, abs=1e-3)
    assert fit.low <= exponent <= fit.high
    assert fit.r_squared > 0.99999 or exponent == 0.0


def test_the_interval_covers_the_truth_at_about_its_level() -> None:
    rng = np.random.default_rng(20261019)
    n = np.repeat(SIZES, 4)
    runs, covered = 2000, 0
    for _ in range(runs):
        noise = rng.lognormal(0.0, 0.1, n.size)
        fit = fit_exponent(n.tolist(), (3e-9 * n**2 * noise).tolist())
        covered += fit.low <= 2.0 <= fit.high
    rate = covered / runs
    # 0.95 with a Monte Carlo error of about 0.005 at 2000 runs.
    assert rate == pytest.approx(0.95, abs=0.025)


def test_a_constant_cost_pulls_small_sizes_toward_zero() -> None:
    n = np.repeat(SIZES, 3)
    fit = fit_exponent(n.tolist(), (0.05 + 1e-7 * n**2).tolist())
    assert fit.local_exponents[0] < fit.local_exponents[-1]
    assert fit.local_exponents[-1] > 1.5
    assert fit.exponent < 2.0


def test_medians_and_sizes_are_reported_per_distinct_size() -> None:
    fit = fit_exponent([1, 1, 2, 2, 4, 4], [1.0, 3.0, 2.0, 2.0, 4.0, 8.0])
    assert fit.sizes == (1.0, 2.0, 4.0)
    assert fit.medians == (2.0, 2.0, 6.0)
    assert fit.n == 6
    assert len(fit.local_exponents) == 2


@pytest.mark.parametrize(
    ("sizes", "values", "message"),
    [
        ([1, 2], [1, 2], "3 distinct sizes"),
        ([1, 2, 4, 4], [1, 2, 4], "same length"),
        ([1, 2, 4, 4], [1, 2, 0, 4], "positive"),
        ([1, 2, 4, 4], [1, 2, float("nan"), 4], "finite"),
        ([], [], "positive and finite"),
        ([0, 2, 4, 4], [1, 2, 4, 4], "positive"),
    ],
)
def test_fit_refuses_what_it_cannot_fit(
    sizes: list[float], values: list[float], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        fit_exponent(sizes, values)


def test_geometric_mean_and_its_interval() -> None:
    estimate = geometric_mean([2.0, 8.0])
    assert estimate.value == pytest.approx(4.0)
    assert estimate.n == 2
    assert estimate.low is not None
    assert estimate.high is not None
    assert estimate.low < 4.0 < estimate.high


def test_geometric_mean_interval_is_symmetric_on_the_log_scale() -> None:
    estimate = geometric_mean([1.0, 2.0, 4.0, 8.0])
    assert estimate.low is not None
    assert estimate.high is not None
    assert np.log(estimate.value / estimate.low) == pytest.approx(
        np.log(estimate.high / estimate.value)
    )


def test_one_value_has_no_interval() -> None:
    estimate = geometric_mean([3.0])
    assert estimate.value == pytest.approx(3.0)
    assert (estimate.low, estimate.high) == (None, None)


@pytest.mark.parametrize(
    "values", [[], [1.0, 0.0], [1.0, -1.0], [float("inf")]]
)
def test_geometric_mean_refuses_bad_values(values: list[float]) -> None:
    with pytest.raises(ValueError, match="positive"):
        geometric_mean(values)
