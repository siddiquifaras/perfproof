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
    _check_width(width)
    length = len(values)
    if width > length:
        return np.zeros(0, dtype=np.int64)
    sums = np.empty(length - width + 1, dtype=np.int64)
    running = 0
    for step in range(width):
        running += int(values[step])
    sums[0] = running
    for step in range(width, length):
        running += int(values[step]) - int(values[step - width])
        sums[step - width + 1] = running
    return sums


def _check_width(width: int) -> None:
    if width < 1:
        msg = f"width must be at least 1, got {width}"
        raise ValueError(msg)
