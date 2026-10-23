"""Does a procedure keep at least the gated primary's power everywhere?

T1.11 phase 2 has a hard constraint: no worse than the current gated
primary in KEEP power at true speedups 1.05, 1.10 and 1.20 and REJECT
power at 0.90 and 0.80, in every cell, within Monte Carlo error. This
reads results CSVs (the harness's, ``run_calibration.py csv``, or the
real-trace one, ``run_real.py run``) and, for each procedure named,
reports across all cells with that speedup:

- the smallest difference to the primary in percentage points,
- the number of cells where the procedure is worse beyond ``z`` (default
  3) standard errors of the difference of two proportions of ``R``
  replications each (the two procedures see the same rounds, so the
  true error of the difference is smaller: this is conservative),
- the worst cells.

Usage::

    uv run python tests/calibration/dominance.py FILE... \\
        --procedures hac_lf --against batch_means_6
"""

import argparse
import csv
import gzip
import math
import sys
from pathlib import Path
from typing import Final

KEEP_AT: Final = (1.05, 1.10, 1.20)
REJECT_AT: Final = (0.90, 0.80)


def _rows(path: Path) -> list[dict[str, str]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", newline="") as source:
        return list(csv.DictReader(source))


def _key(row: dict[str, str]) -> tuple[str, float]:
    """Cell without speedup, and the speedup, from either CSV layout."""
    parts = row["cell"].split("/")
    return "/".join(parts[:-1]), float(parts[-1])


def main() -> int:
    """Print the dominance report."""
    parser = argparse.ArgumentParser(prog="dominance.py")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--procedures", nargs="+", required=True)
    parser.add_argument("--against", default="batch_means_6")
    parser.add_argument("--z", type=float, default=3.0)
    parser.add_argument("--worst", type=int, default=3)
    args = parser.parse_args()
    cells: dict[tuple[str, float], dict[str, tuple[int, int, int]]] = {}
    for path in args.files:
        for r in _rows(path):
            name, speed = _key(r)
            cells.setdefault((path.stem + ":" + name, speed), {})[
                r["procedure"]
            ] = (int(r["keep"]), int(r["reject"]), int(r["replications"]))
    write = sys.stdout.write
    for proc in args.procedures:
        write(f"\n{proc} against {args.against}\n")
        for kind, speeds in (("keep", KEEP_AT), ("reject", REJECT_AT)):
            for speed in speeds:
                diffs: list[tuple[float, float, str]] = []
                for (name, s), by in cells.items():
                    if abs(s - speed) > 1e-9 or proc not in by:
                        continue
                    if args.against not in by:
                        continue
                    idx = 0 if kind == "keep" else 1
                    total = by[proc][2]
                    p1 = by[proc][idx] / total
                    p0 = by[args.against][idx] / total
                    se = math.sqrt(
                        (p1 * (1 - p1) + p0 * (1 - p0)) / total + 1e-12
                    )
                    diffs.append((100 * (p1 - p0), (p1 - p0) / se, name))
                if not diffs:
                    continue
                diffs.sort()
                bad = [d for d in diffs if d[1] < -args.z]
                write(
                    f"  {kind} at {speed:.2f}: {len(diffs)} cells, min "
                    f"{diffs[0][0]:+.1f} pts, {len(bad)} worse than "
                    f"{args.z:g} se; worst "
                    + "; ".join(
                        f"{d[2]} {d[0]:+.1f}" for d in diffs[: args.worst]
                    )
                    + "\n"
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
