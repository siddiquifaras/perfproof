"""Compare procedures run on the same cells: acceptance and power.

Reads results CSVs (``run_calibration.py csv``) and prints Markdown:

``acceptance``
    Per procedure and file, the cells run, the cells failing the
    acceptance rule of ``docs/method.md`` (one-sided 95%
    Clopper-Pearson upper bound above 2.5%, the ``passes`` column), the
    largest false-KEEP and false-REJECT counts with their cells. A cell
    counts as a failure from the raw count: ``errors`` against the
    largest passing count at ``R = 4000``, recomputed here.

``power``
    Per schedule, host mode and scenario group, KEEP % at true
    speedups 1.03, 1.05, 1.10 and 1.20 (1.03 is the false-KEEP rate)
    and REJECT % at 0.90 and 0.80, per procedure, as the mean over the
    group's scenarios of each cell's rate. Speedups absent from the
    files print as ``-``.

Usage::

    uv run python tests/calibration/compare.py acceptance FILE...
    uv run python tests/calibration/compare.py power FILE... \\
        --procedures batch_means_6 hac_b20
"""

import argparse
import csv
import gzip
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import montecarlo as mc
from exposed import EXPOSED
from heldout import HELDOUT
from heldout import NAME_KEY as HELDOUT_KEY

KEEP_AT: Final = (1.03, 1.05, 1.10, 1.20)
REJECT_AT: Final = (0.90, 0.80)
GROUPS: Final = {
    "gaussian": ("lognormal_cv1", "lognormal_cv5", "lognormal_cv15"),
    "bimodal (10% of runs 30% slow)": ("bimodal",),
    "student_t_df3": ("student_t_df3",),
    "outliers (2% of 3x)": ("outliers",),
    "ar1 (rho 0.5)": ("ar1",),
    "other development": (
        "drift",
        "variance_step",
        "order_effect",
        "session_offset",
    ),
    "shared throttling (mean of 4)": (
        "COND-shared-throttle1",
        "COND-shared-throttle3",
        "COND-shared-thermal-bursts",
        "COND-shared-mixed-lowcv",
    ),
    "arm episodes, random arm": ("COND-armluck",),
    "arm episodes, 80% on baseline": ("COND-armbias-base",),
    "arm episodes, 80% on candidate": ("COND-armbias-cand",),
    "arm episodes, 80% baseline, poor covariate": (
        "COND-armbias-base-poorcov",
    ),
    "arm episodes, 80% baseline, no covariate": ("COND-armbias-base-nocov",),
    "skew, candidate 5% x2": ("SKEW-cand-5pct-x2",),
    "skew, baseline 5% x2": ("SKEW-base-5pct-x2",),
    "skew, candidate 10% x1.5": ("SKEW-cand-10pct-x1.5",),
    "held-out (mean of 30)": tuple(
        f"H{HELDOUT_KEY}-{i:02d}" for i in range(30)
    ),
}


def _read(path: Path) -> list[dict[str, str]]:
    """Rows of a results CSV, plain or gzipped."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", newline="") as source:
        return list(csv.DictReader(source))


def _grid(scenario: str) -> str:
    if scenario in EXPOSED:
        return "exposed"
    if scenario in HELDOUT:
        return "heldout"
    if scenario.startswith(("COND-", "SKEW-")):
        return "probe"
    return "development"


def acceptance(paths: Sequence[Path], procedures: Sequence[str]) -> None:
    """Print the acceptance summary of every procedure in every file."""
    write = sys.stdout.write
    write(
        "| File | Procedure | Grid | Cells | Failing | Worst false KEEP "
        "| Worst false REJECT |\n|---|---|---|---|---|---|---|\n"
    )
    for path in paths:
        rows = _read(path)
        names = sorted({r["procedure"] for r in rows})
        for proc in procedures or names:
            for grid in ("development", "exposed", "heldout", "probe"):
                mine = [
                    r
                    for r in rows
                    if r["procedure"] == proc
                    and _grid(r["cell"].split("/")[1]) == grid
                    and float(r["cell"].split("/")[3]) >= 0.95
                ]
                if not mine:
                    continue
                failing = [
                    r
                    for r in mine
                    if not mc.passes(int(r["errors"]), int(r["replications"]))
                ]
                keep = [
                    r for r in mine if float(r["cell"].split("/")[3]) <= mc.BAR
                ]
                rej = [
                    r for r in mine if float(r["cell"].split("/")[3]) > mc.BAR
                ]
                wk = max(keep, key=lambda r: int(r["errors"]), default=None)
                wr = max(rej, key=lambda r: int(r["errors"]), default=None)
                write(
                    f"| {path.stem} | {proc} | {grid} | {len(mine)} | "
                    f"{len(failing)} | "
                    f"{wk['errors'] + ' ' + wk['cell'] if wk else '-'} | "
                    f"{wr['errors'] + ' ' + wr['cell'] if wr else '-'} |\n"
                )


def power(paths: Sequence[Path], procedures: Sequence[str]) -> None:
    """Print mean KEEP and REJECT rates by schedule, host and group."""
    write = sys.stdout.write
    rates: dict[tuple[str, ...], list[float]] = defaultdict(list)
    for path in paths:
        for r in _read(path):
            schedule, scenario, mode, speed = r["cell"].split("/")
            total = int(r["replications"])
            for label, members in GROUPS.items():
                if scenario in members:
                    key = (r["procedure"], schedule, mode, label, speed)
                    rates[(*key, "keep")].append(int(r["keep"]) / total)
                    rates[(*key, "reject")].append(int(r["reject"]) / total)
    headers = [f"KEEP {s:.2f}" for s in KEEP_AT] + [
        f"REJECT {s:.2f}" for s in REJECT_AT
    ]
    for schedule in (s.name for s in mc.SCHEDULES):
        for mode in mc.MODES:
            write(f"\n### {schedule}, {mode} (percent)\n\n")
            write("| Group | Procedure | " + " | ".join(headers) + " |\n")
            write("|---" * (2 + len(headers)) + "|\n")
            for label in GROUPS:
                for proc in procedures:
                    cells: list[str] = []
                    for speed, kind in [(s, "keep") for s in KEEP_AT] + [
                        (s, "reject") for s in REJECT_AT
                    ]:
                        key = (
                            proc,
                            schedule,
                            mode,
                            label,
                            f"{speed:.2f}",
                            kind,
                        )
                        got = rates.get(key)
                        cells.append(
                            "-"
                            if not got
                            else f"{100 * sum(got) / len(got):.1f}"
                        )
                    write(
                        f"| {label} | {proc} | " + " | ".join(cells) + " |\n"
                    )


def main() -> int:
    """Dispatch to ``acceptance`` or ``power``."""
    parser = argparse.ArgumentParser(prog="compare.py")
    parser.add_argument("command", choices=("acceptance", "power"))
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--procedures", nargs="*", default=[])
    args = parser.parse_args()
    if args.command == "acceptance":
        acceptance(args.files, args.procedures)
    else:
        power(args.files, args.procedures or list(mc.ALL_PROCEDURES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
