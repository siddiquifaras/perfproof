"""Rolling window sums over a series of event counts.

A series holds one non-negative count per time step, such as requests
per second. The sum of each window of consecutive steps is the load
over that window.
"""

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
    Runs in O(n) time with a running total.
    """
    if width < 1:
        msg = f"width must be at least 1, got {width}"
        raise ValueError(msg)
    n = len(values)
    if width > n:
        return np.zeros(0, dtype=np.int64)
    out = np.empty(n - width + 1, dtype=np.int64)
    total = int(values[:width].sum())
    out[0] = total
    for i in range(width, n):
        total += int(values[i]) - int(values[i - width])
        out[i - width + 1] = total
    return out
