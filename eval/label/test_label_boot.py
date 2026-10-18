"""The labeller's moving-block bootstrap."""

import math

import numpy as np
import pytest

import boot
import decide


@pytest.mark.parametrize(
    ("n", "expected"),
    [(1, 1), (2, 2), (8, 2), (9, 3), (64, 4), (65, 5), (200, 6), (216, 6)],
)
def test_block_length_is_the_ceiling_cube_root(n: int, expected: int) -> None:
    assert boot.block_length(n) == expected


def test_block_length_needs_a_ratio() -> None:
    with pytest.raises(ValueError, match="at least one"):
        boot.block_length(0)


def test_equal_ratios_give_that_ratio_exactly() -> None:
    a = np.full(200, 0.30)
    b = np.full(200, 0.10)
    ratio = 0.30 / 0.10
    ci = boot.interval(a, b, seed=1)
    assert ci.lower == ci.upper == ci.estimate == ratio


def test_one_block_of_everything_has_no_spread() -> None:
    r = np.random.default_rng(3).normal(size=10)
    means = boot.resampled_means(r, block=10, resamples=50, seed=4)
    assert np.allclose(means, r.mean(), rtol=0, atol=1e-15)


def test_chunking_does_not_change_the_draws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = np.random.default_rng(5).normal(size=200)
    whole = boot.resampled_means(r, 6, 1000, seed=6)
    monkeypatch.setattr(boot, "CHUNK_BYTES", 34 * 6 * 8 * 7)
    chunked = boot.resampled_means(r, 6, 1000, seed=6)
    assert np.array_equal(whole, chunked)


def test_interval_is_reproducible_and_brackets_the_estimate() -> None:
    rng = np.random.default_rng(7)
    a = np.exp(rng.normal(0.0, 0.05, 200))
    b = np.exp(rng.normal(-0.05, 0.05, 200))
    first = boot.interval(a, b, seed=8)
    again = boot.interval(a, b, seed=8)
    assert first == again
    assert first.lower < first.estimate < first.upper
    assert first.block == 6
    assert math.isclose(first.estimate, boot.geometric_mean_ratio(a, b))


@pytest.mark.parametrize(
    ("a", "b", "match"),
    [
        (np.ones(3), np.ones(4), "same length"),
        (np.ones(0), np.ones(0), "same length"),
        (np.array([1.0, np.nan]), np.ones(2), "finite"),
        (np.array([1.0, 0.0]), np.ones(2), "positive"),
    ],
)
def test_interval_rejects_bad_input(
    a: boot.FloatArray, b: boot.FloatArray, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        boot.interval(a, b, seed=0)


def _coverage(phi: float, reps: int, n: int, seed: int) -> tuple[float, float]:
    """Simulated coverage of the 95% interval for a known speedup.

    Log ratios are AR(1) with coefficient ``phi`` and marginal standard
    deviation 0.05 around ln(1.05); returns the coverage and its Monte
    Carlo standard error.
    """
    rng = np.random.default_rng(seed)
    true = 1.05
    hits = 0
    for rep in range(reps):
        e = rng.normal(0.0, 0.05 * math.sqrt(1 - phi**2), n)
        r = np.empty(n)
        r[0] = rng.normal(0.0, 0.05)
        for i in range(1, n):
            r[i] = phi * r[i - 1] + e[i]
        b = np.exp(rng.normal(0.0, 0.02, n))
        a = b * true * np.exp(r)
        ci = boot.interval(a, b, seed=seed + rep, resamples=2000)
        hits += ci.lower <= true <= ci.upper
    p = hits / reps
    return p, math.sqrt(p * (1 - p) / reps)


@pytest.mark.calibration
def test_coverage_independent_pairs() -> None:
    # Expected near 0.95; a percentile interval at n = 200 runs a
    # little short. 1000 replications give a Monte Carlo SE of about
    # 0.007, so 0.92 is about 4 SE under 0.95.
    p, _ = _coverage(phi=0.0, reps=1000, n=200, seed=21)
    assert p >= 0.92


@pytest.mark.calibration
def test_coverage_autocorrelated_pairs() -> None:
    # Drift between neighbouring pairs (AR(1), phi = 0.3) is what the
    # blocks are for. Short blocks under-cover somewhat, so the floor is
    # 0.90, about 7 SE under 0.95 at 1000 replications (SE about 0.007).
    # At 400 replications (seed 12) this measured 0.8975, about 2 SE
    # under the 0.928 a 1000-replication probe found, so the count was
    # raised; the floor was not moved.
    p, _ = _coverage(phi=0.3, reps=1000, n=200, seed=22)
    assert p >= 0.90


@pytest.mark.calibration
def test_false_improvement_at_the_bar() -> None:
    # A true speedup exactly at the bar must be called an improvement
    # by the bootstrap at most 2.5% of the time (the interval's upper
    # tail), plus Monte Carlo error: 400 replications, SE about 0.008
    # at 0.025, so the ceiling 0.05 is about 3 SE above.
    rng = np.random.default_rng(13)
    reps, n, wrong = 400, 200, 0
    for rep in range(reps):
        b = np.exp(rng.normal(0.0, 0.05, n))
        a = b * decide.BAR * np.exp(rng.normal(0.0, 0.05, n))
        ci = boot.interval(a, b, seed=100 + rep, resamples=2000)
        wrong += decide.bootstrap_category(ci.lower, ci.upper) == (
            "improvement"
        )
    assert wrong / reps <= 0.05
