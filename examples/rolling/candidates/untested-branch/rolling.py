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
    Runs in O(n) time as differences of one prefix-sum array.
    """
    if width < 1:
        msg = f"width must be at least 1, got {width}"
        raise ValueError(msg)
    if width == 1:
        return values
    prefix = np.zeros(len(values) + 1, dtype=np.int64)
    np.cumsum(values, out=prefix[1:])
    return prefix[width:] - prefix[:-width]
