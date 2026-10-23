"""Share of looks whose rho limit rose above the floor, per trace.

Reads a real-trace results CSV (``run_real.py run``, plain or gzipped)
and, for ``hac_lf`` at true speedup 1.00 (the null, so every look is
one a comparison of equal versions takes) and Politis-White blocks,
prints per trace and schedule the percentage of looks with an interval
whose critical value used a limit above the floor
(``limit_above_floor`` over ``limit_looks``).

``limit_looks`` counts every look a replication took, including the
look at ``n0`` observations of a schedule whose ``n0`` is below
`perfproof.stats.intervals.HAC_MIN_N`, which has no interval and no
limit. Each replication takes exactly one such look, so those cells
are corrected by the replications of the cell.

Usage::

    uv run python tests/calibration/fallback_share.py FILE
"""

import csv
import gzip
import sys
from collections import defaultdict
from pathlib import Path

import montecarlo as mc

from perfproof.stats.intervals import HAC_MIN_N

SCHEDULES = ("python_native", "web_agentic", "llm_serving", "gpu_kernels")


def main() -> int:
    """Print the table."""
    path = Path(sys.argv[1])
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", newline="") as source:
        rows = list(csv.DictReader(source))
    share: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        if (
            r["procedure"] != "hac_lf"
            or r["rule"] != "pw"
            or r["speedup"] != "1.00"
        ):
            continue
        looks = int(r["limit_looks"])
        if mc.SCHEDULE_BY_NAME[r["schedule"]].looks.n0 < HAC_MIN_N:
            looks -= int(r["replications"])
        key = (r["trace"], r["schedule"])
        share[key][0] += int(r["limit_above_floor"])
        share[key][1] += looks
    write = sys.stdout.write
    write("| Trace | " + " | ".join(SCHEDULES) + " |\n")
    write("|---" * (1 + len(SCHEDULES)) + "|\n")
    total: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for trace in sorted({k[0] for k in share}):
        cells: list[str] = []
        for s in SCHEDULES:
            above, looks = share[trace, s]
            total[s][0] += above
            total[s][1] += looks
            cells.append(f"{100 * above / looks:.1f}%")
        write(f"| {trace} | " + " | ".join(cells) + " |\n")
    write(
        "| ALL | "
        + " | ".join(
            f"{100 * total[s][0] / total[s][1]:.1f}%" for s in SCHEDULES
        )
        + " |\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
