"""Summarise warm-up curves: iteration times of fresh processes.

Each input file is one process: an optional ``#`` header line, then one
iteration time in nanoseconds per line. For each file the steady time is
the median of its last ``--steady`` iterations, and the table gives the
first iteration over steady, the median of iterations 1 to 10 over
steady, and the first iteration after which every window of
30 iterations has a median within 10% and within 20% of steady
(the settle iteration). Across files it gives the median, minimum and
maximum.

Usage: ``python studies/warmup_analyze.py <dir> <prefix>...`` reads
``<dir>/<prefix>-*.txt`` for each prefix.
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path


def read(path: Path) -> tuple[str, list[int]]:
    """Return a file's header line and its iteration times."""
    header = ""
    times: list[int] = []
    for line in path.read_text().splitlines():
        if line.startswith("#"):
            header = line
        elif line.strip():
            times.append(int(line))
    return header, times


def settle(times: list[int], steady: float, window: int, tol: float) -> int:
    """Return the first start after which all windows are in range."""
    last_bad = -1
    for start in range(len(times) - window + 1):
        median = statistics.median(times[start : start + window])
        if abs(median / steady - 1) > tol:
            last_bad = start
    return last_bad + 1


def main(argv: list[str]) -> int:
    """Print the table for each prefix named in ``argv``."""
    directory = Path(argv[1])
    steady_n, window = 500, 30
    for prefix in argv[2:]:
        files = sorted(directory.glob(f"{prefix}-*.txt"))
        if not files:
            sys.stdout.write(f"{prefix}: no files\n")
            continue
        rows: list[tuple[float, float, int, int, float]] = []
        header = ""
        for path in files:
            header, times = read(path)
            steady = statistics.median(times[-steady_n:])
            rows.append(
                (
                    times[0] / steady,
                    statistics.median(times[:10]) / steady,
                    settle(times, steady, window, 0.10),
                    settle(times, steady, window, 0.20),
                    steady / 1e6,
                )
            )
        sys.stdout.write(f"{prefix}: {len(files)} processes; {header}\n")
        names = (
            "first iteration / steady",
            "iterations 1-10 median / steady",
            f"settle iteration, window {window}, within 10%",
            f"settle iteration, window {window}, within 20%",
            "steady ms per iteration",
        )
        for i, name in enumerate(names):
            column = [r[i] for r in rows]
            sys.stdout.write(
                f"  {name}: median {statistics.median(column):.3g}, "
                f"min {min(column):.3g}, max {max(column):.3g}\n"
            )
        blocks = [
            [
                statistics.median(read(path)[1][b * window : (b + 1) * window])
                / statistics.median(read(path)[1][-steady_n:])
                for path in files
            ]
            for b in range(10)
        ]
        sys.stdout.write(
            "  median over processes of the block median over steady, "
            f"blocks of {window} iterations from the first: "
            + " ".join(f"{statistics.median(b):.2f}" for b in blocks)
            + "\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
