"""Rolling window sums over a series of event counts.

A series holds one non-negative count per time step, such as requests
per second. The sum of each window of consecutive steps is the load
over that window.
"""

import itertools

import numpy as np
from numpy.typing import NDArray


def rolling_sum(values: NDArray[np.int64], width: int) -> NDArray[np.int64]:
    """Sum every window of ``width`` consecutive values.

    Parameters
    ----------
    values : numpy.ndarray of int64, shape (n,)
        Counts, one per time step. Every window sum must fit in int64.
    width : int
        Number of consecutive values in each window, at least 1.

    Returns
    -------
    numpy.ndarray of int64, shape (max(n - width + 1, 0),)
        Element ``i`` is ``values[i:i + width].sum()``. Empty when
        ``width`` exceeds ``n``.

    Raises
    ------
    ValueError
        If ``width`` is less than 1.

    Notes
    -----
    Runs in O(n) time from prefix sums, which are cached per distinct
    series so later calls on the same series reuse them.
    """
    if width < 1:
        msg = f"width must be at least 1, got {width}"
        raise ValueError(msg)
    n = len(values)
    if width > n:
        return np.zeros(0, dtype=np.int64)
    _, prefix = _cached(values)
    return np.array(
        [prefix[i + width] - prefix[i] for i in range(n - width + 1)],
        dtype=np.int64,
    )


_CACHE: dict[tuple[str, bytes], tuple[list[int], list[int]]] = {}


def _cached(values: NDArray[np.int64]) -> tuple[list[int], list[int]]:
    """Return the series as a list and its prefix sums, cached."""
    key = (values.dtype.str, values.tobytes())
    entry = _CACHE.get(key)
    if entry is None:
        counts: list[int] = values.tolist()
        prefix = list(itertools.accumulate(counts, initial=0))
        entry = _CACHE[key] = (counts, prefix)
    return entry
