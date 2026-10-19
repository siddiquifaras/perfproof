"""Benchmark the calibration pipeline of `pairs` on one batch.

Run as ``python bench.py [--n N] [--output PATH]`` from this directory.
The timed region builds the readings, folds them into a checksum,
counts the matching pairs and renders the line, and
``PERFPROOF {"value": <seconds>}`` on stdout reports it, so the metric
is ``reported``. ``--n`` is the batch size, the one input-size knob;
``measure --sizes`` scales it. With ``--output`` the rendered line is
written outside the timed region.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from pairs import build_readings, checksum, count_pairs, render

DEFAULT_N = 1_000
"""Readings in the batch when ``--n`` is not given."""


def main(argv: list[str] | None = None) -> int:
    """Run the pipeline once and report its time.

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
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument("--output", type=Path, help="save the line here")
    args = parser.parse_args(argv)
    n: int = args.n
    start = time.perf_counter()
    values = build_readings(n)
    check = checksum(values)
    count = count_pairs(values, n)
    line = render(count, n, check)
    elapsed = time.perf_counter() - start
    if args.output is not None:
        output: Path = args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(line + "\n", encoding="utf-8")
    sys.stdout.write(f"PERFPROOF {json.dumps({'value': elapsed})}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
