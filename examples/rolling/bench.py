"""Benchmark `rolling.rolling_sum` on a fixed batch of synthetic series.

Run as ``python bench.py [--output PATH]`` from this directory. It
times the window sums of every series with `time.perf_counter` and
prints ``PERFPROOF {"value": <seconds>}`` on stdout, so the same
command serves a ``reported`` metric (the calls alone) and a ``wall``
metric (the whole process). With ``--output`` it also saves the sums as
one ``.npy`` array of shape ``(SERIES, SAMPLES - WIDTH + 1)``, outside
the timed region, for output comparison.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from rolling import rolling_sum

SERIES = 20
"""Number of series, one per sensor."""
SAMPLES = 100_000
"""Length of each series, in time steps."""
WIDTH = 3_000
"""Window width, in time steps."""
SEED = 20261016
"""Seed of the generator that draws the series."""


def make_series() -> list[NDArray[np.int64]]:
    """Draw the benchmark series.

    Returns
    -------
    list of numpy.ndarray of int64, each of shape (SAMPLES,)
        ``SERIES`` series of counts drawn uniformly from 0 to 999, the
        same on every run.
    """
    rng = np.random.default_rng(SEED)
    return [
        rng.integers(0, 1000, SAMPLES, dtype=np.int64) for _ in range(SERIES)
    ]


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark once and report its time.

    Parameters
    ----------
    argv : list of str, optional
        Command-line arguments; ``sys.argv[1:]`` when omitted.

    Returns
    -------
    int
        Exit status, 0 on success.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="save the sums here")
    args = parser.parse_args(argv)
    batch = make_series()
    start = time.perf_counter()
    sums = [rolling_sum(series, WIDTH) for series in batch]
    elapsed = time.perf_counter() - start
    if args.output is not None:
        output: Path = args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        np.save(output, np.stack(sums))
    sys.stdout.write(f"PERFPROOF {json.dumps({'value': elapsed})}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
