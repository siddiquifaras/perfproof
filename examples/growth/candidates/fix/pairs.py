"""Count the pairs of readings that add up to a target.

A sensor batch is a sequence of integer readings. Calibration needs the
number of index pairs ``i < j`` whose readings sum to a target value.
"""

from collections import Counter
from collections.abc import Sequence


def build_readings(n: int) -> list[int]:
    """Make the readings of a batch of ``n`` sensors.

    Parameters
    ----------
    n : int
        Number of readings, at least 0.

    Returns
    -------
    list of int
        ``n`` readings from 0 to ``2 n - 1``, the same for every call
        with the same ``n``.
    """
    modulus = 2 * n if n else 1
    return [(i * 7919 + 13) % modulus for i in range(n)]


def count_pairs(values: Sequence[int], target: int) -> int:
    """Count index pairs ``i < j`` whose readings sum to ``target``.

    Parameters
    ----------
    values : sequence of int
        The readings.
    target : int
        The sum to look for.

    Returns
    -------
    int
        Number of qualifying pairs.

    Notes
    -----
    Runs in O(n) time for n readings: each reading is paired with the
    earlier readings that complete it.
    """
    seen: Counter[int] = Counter()
    count = 0
    for value in values:
        count += seen[target - value]
        seen[value] += 1
    return count


def checksum(values: Sequence[int], passes: int = 40) -> int:
    """Fold the readings into one number with a fixed linear workload.

    Parameters
    ----------
    values : sequence of int
        The readings.
    passes : int, optional
        Number of mixing passes over the readings.

    Returns
    -------
    int
        Sum of the mixed readings.

    Notes
    -----
    Runs in O(passes n) time for n readings.
    """
    mixed = list(values)
    for _ in range(passes):
        mixed = [(v * 31 + 7) % 65521 for v in mixed]
    return sum(mixed)


def render(count: int, n: int, check: int) -> str:
    """Format the calibration line for a batch.

    Parameters
    ----------
    count : int
        Number of qualifying pairs.
    n : int
        Number of readings in the batch.
    check : int
        The batch's `checksum`.

    Returns
    -------
    str
        One line of text.
    """
    return f"{count} of {n * (n - 1) // 2} pairs match ({check})"
