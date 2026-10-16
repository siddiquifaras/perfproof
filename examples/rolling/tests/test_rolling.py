import os

import numpy as np
import pytest
from numpy.typing import NDArray

from rolling import rolling_sum

FLAKY_SEED_VARIABLE = "ROLLING_FLAKY_SEED"


def reference(values: NDArray[np.int64], width: int) -> list[int]:
    return [
        int(values[i : i + width].sum())
        for i in range(len(values) - width + 1)
    ]


@pytest.mark.parametrize("n", [2, 10, 257])
@pytest.mark.parametrize("width", [2, 3, 8])
def test_matches_reference(n: int, width: int) -> None:
    rng = np.random.default_rng(1000 * n + width)
    values = rng.integers(0, 1000, n, dtype=np.int64)

    assert rolling_sum(values, width).tolist() == reference(values, width)


def test_one_window_when_width_equals_length() -> None:
    values = np.array([4, 0, 7, 1], dtype=np.int64)

    assert rolling_sum(values, 4).tolist() == [12]


def test_empty_when_width_exceeds_length() -> None:
    values = np.array([4, 0, 7], dtype=np.int64)

    sums = rolling_sum(values, 5)

    assert sums.shape == (0,)
    assert sums.dtype == np.int64


def test_empty_series() -> None:
    assert rolling_sum(np.zeros(0, dtype=np.int64), 3).shape == (0,)


@pytest.mark.parametrize("width", [0, -1])
def test_rejects_width_below_one(width: int) -> None:
    with pytest.raises(ValueError, match="width must be at least 1"):
        rolling_sum(np.arange(5, dtype=np.int64), width)


def test_large_counts_are_exact() -> None:
    values = np.full(6, 2**60, dtype=np.int64)

    assert rolling_sum(values, 3).tolist() == [3 * 2**60] * 4


def test_returns_int64_and_leaves_input_alone() -> None:
    values = np.arange(10, dtype=np.int64)
    before = values.copy()

    sums = rolling_sum(values, 4)

    assert sums.dtype == np.int64
    assert np.array_equal(values, before)


@pytest.mark.deliberately_flaky
def test_last_window_is_at_least_average() -> None:
    seed = os.environ.get(FLAKY_SEED_VARIABLE)
    rng = np.random.default_rng(None if seed is None else int(seed))
    values = rng.integers(0, 1000, 64, dtype=np.int64)

    assert rolling_sum(values, 8)[-1] >= 8 * 499.5
