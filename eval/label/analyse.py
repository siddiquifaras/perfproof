"""Turn one machine's pairs into the analyses the rule reads.

The bootstrap (`boot`) and pyperf (`pyperf_stats`) each look at the
same wall times; `decide.machine_label` combines them with the halves
check. Instruction counts, where every execution has one, get the same
bootstrap and are reported only.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

import boot
import decide

FloatArray = boot.FloatArray


def arms(
    pairs: list[dict[str, Any]], key: str
) -> tuple[FloatArray, FloatArray]:
    """Return arm A's and arm B's values of one field, in pair order.

    Parameters
    ----------
    pairs : list of dict
        Pair records from the log.
    key : str
        ``wall_s``, ``cpu_s`` or ``instructions``.

    Returns
    -------
    a, b : ndarray of float64, shape (n,)
        The values.
    """
    a = np.array([p["a"][key] for p in pairs], dtype=np.float64)
    b = np.array([p["b"][key] for p in pairs], dtype=np.float64)
    return a, b


def halves(a: FloatArray, b: FloatArray) -> tuple[float, float]:
    """Return the geometric-mean speedup of each half of the pairs.

    Parameters
    ----------
    a, b : ndarray of float64, shape (n,)
        Paired times, ``n >= 2``.

    Returns
    -------
    tuple of float
        The two halves' ``exp(mean(ln(a / b)))``.
    """
    h = a.shape[0] // 2
    return (
        boot.geometric_mean_ratio(a[:h], b[:h]),
        boot.geometric_mean_ratio(a[h:], b[h:]),
    )


def analyse(
    pairs: list[dict[str, Any]], seed: int, out_dir: Path, stem: str
) -> dict[str, Any]:
    """Run both analyses and the rule on finished pairs.

    Parameters
    ----------
    pairs : list of dict
        Pair records, every execution exited 0, at least 2.
    seed : int
        The run's seed, also the bootstrap's.
    out_dir : Path
        Where pyperf's JSON files go.
    stem : str
        Their file name prefix.

    Returns
    -------
    dict
        ``bootstrap``, ``pyperf``, ``halves``, ``machine_label`` and
        ``instructions`` (an interval, or why there is none).
    """
    # pyperf comes from eval/label/requirements.txt, not the project's
    # environment, so only the analysis needs it installed.
    import pyperf_stats

    a, b = arms(pairs, "wall_s")
    ci = boot.interval(a, b, seed)
    pp = pyperf_stats.compare(a.tolist(), b.tolist(), out_dir, stem)
    two = halves(a, b)
    label = decide.machine_label(
        ci.lower, ci.upper, pp.ratio, pp.significant, two
    )
    counts = [p[arm]["instructions"] for p in pairs for arm in ("a", "b")]
    if all(isinstance(c, int) and c > 0 for c in counts):
        ia, ib = arms(pairs, "instructions")
        instructions: dict[str, Any] = asdict(boot.interval(ia, ib, seed))
    else:
        missing = sum(1 for c in counts if not isinstance(c, int))
        instructions = {
            "interval": None,
            "reason": f"{missing} of {len(counts)} executions have no count",
        }
    ca, cb = arms(pairs, "cpu_s")
    return {
        "metric": "wall_s",
        "bar": decide.BAR,
        "bootstrap": asdict(ci),
        "pyperf": asdict(pp),
        "halves": list(two),
        "machine_label": asdict(label),
        "instructions": instructions,
        "summary": {
            "a_median_s": float(np.median(a)),
            "b_median_s": float(np.median(b)),
            "a_cpu_median_s": float(np.median(ca)),
            "b_cpu_median_s": float(np.median(cb)),
        },
    }
