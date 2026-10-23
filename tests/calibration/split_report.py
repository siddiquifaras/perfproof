"""Results of the split procedure of T1.11, from the committed CSVs.

The split decided by Faras (``docs/method.md``, "The T1.11 decision")
uses ``hac_lf`` on every schedule but ``gpu_kernels`` and the gated
primary on ``gpu_kernels``. Both ran on the same cells, so the split's
count for a cell is the count of the procedure its schedule names, and
no new simulation is needed. This prints three tables:

- acceptance per grid and seed (failing cells, worst false KEEP and
  false REJECT counts), under the rule of ``docs/method.md``;
- the real traces under the three block rules;
- dominance against the gated primary, per schedule and threshold: the
  smallest difference in percentage points and the number of cells worse
  than 3 standard errors of two proportions (conservative, as in
  ``dominance.py``).

Usage::

    uv run python tests/calibration/split_report.py
"""

import csv
import gzip
import math
import sys
from pathlib import Path
from typing import Final

import montecarlo as mc
from compare import _grid  # pyright: ignore[reportPrivateUsage]

RESULTS: Final = Path(__file__).parent / "results"
SEEDS: Final = (
    ("record", "3598798392042499920"),
    ("fresh", "2483202639896524218"),
)
REAL_SEED: Final = "7257639517108691388"
PRIMARY: Final = "batch_means_6"
CHALLENGER: Final = "hac_lf"
GATED: Final = "gpu_kernels"
Z: Final = 3.0
KEEP_AT: Final = (1.05, 1.10, 1.20)
REJECT_AT: Final = (0.90, 0.80)
SCHEDULE_NAMES: Final = tuple(s.name for s in mc.SCHEDULES)


def _read(path: Path) -> list[dict[str, str]]:
    with gzip.open(path, "rt", newline="") as source:
        return list(csv.DictReader(source))


def _split(
    rows: list[dict[str, str]], schedule_of: str
) -> list[dict[str, str]]:
    """Rows of the split: each schedule's own procedure, relabelled."""
    keep: list[dict[str, str]] = []
    for r in rows:
        want = PRIMARY if schedule_of_row(r) == GATED else CHALLENGER
        if r["procedure"] == want:
            keep.append({**r, "procedure": schedule_of})
    return keep


def schedule_of_row(row: dict[str, str]) -> str:
    """Return the schedule of a synthetic or real-trace row."""
    if "schedule" in row:
        return row["schedule"]
    return row["cell"].split("/")[0]


def _acceptance() -> None:
    write = sys.stdout.write
    write(
        "| Seed | Grid | Cells | Failing | Worst false KEEP "
        "| Worst false REJECT |\n|---|---|---|---|---|---|\n"
    )
    for label, seed in SEEDS:
        for name in ("main", "heldout", "cond", "skew"):
            path = RESULTS / f"t111p2b-{name}-seed-{seed}.csv.gz"
            rows = _split(_read(path), "split")
            for grid in ("development", "exposed", "heldout", "probe"):
                mine = [
                    r
                    for r in rows
                    if _grid(r["cell"].split("/")[1]) == grid
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
                kind = "cond" if name == "cond" else name
                write(
                    f"| {label} ({seed[:4]}) | {kind}/{grid} | {len(mine)} "
                    f"| {len(failing)} | "
                    f"{wk['errors'] + ' ' + wk['cell'] if wk else '-'} | "
                    f"{wr['errors'] + ' ' + wr['cell'] if wr else '-'} |\n"
                )


def _skew() -> None:
    """Failing skew-probe cells per schedule: split against primary."""
    write = sys.stdout.write
    write("| Seed | Schedule | Primary | Split |\n|---|---|---|---|\n")
    for label, seed in SEEDS:
        rows = _read(RESULTS / f"t111p2b-skew-seed-{seed}.csv.gz")
        both = {PRIMARY: [r for r in rows if r["procedure"] == PRIMARY]}
        both["split"] = _split(rows, "split")
        for schedule in SCHEDULE_NAMES:
            counts: list[str] = []
            for proc in (PRIMARY, "split"):
                bad = [
                    r
                    for r in both[proc]
                    if r["cell"].split("/")[0] == schedule
                    and float(r["cell"].split("/")[3]) >= 0.95
                    and not mc.passes(int(r["errors"]), int(r["replications"]))
                ]
                counts.append(str(len(bad)))
            write(
                f"| {label} ({seed[:4]}) | {schedule} | {counts[0]} "
                f"| {counts[1]} |\n"
            )


def _real() -> None:
    write = sys.stdout.write
    rows = _read(RESULTS / f"t111p2b-real-traces-seed-{REAL_SEED}.csv.gz")
    both = {
        PRIMARY: [r for r in rows if r["procedure"] == PRIMARY],
        CHALLENGER: [r for r in rows if r["procedure"] == CHALLENGER],
        "split": _split(rows, "split"),
    }
    write(
        "| Procedure | Rule | Cells (1.00, 1.03) | Failing "
        "| Worst false KEEPs |\n|---|---|---|---|---|\n"
    )
    for rule in ("pw", "fixed6", "fixed20"):
        for proc in ("split", PRIMARY, CHALLENGER):
            mine = [
                r
                for r in both[proc]
                if r["rule"] == rule and 1.0 <= float(r["speedup"]) <= mc.BAR
            ]
            failing = [r for r in mine if r["passes"] != "1"]
            worst = max(mine, key=lambda r: int(r["errors"]))
            write(
                f"| {proc} | {rule} | {len(mine)} | {len(failing)} | "
                f"{worst['errors']} {worst['cell']} |\n"
            )


def _counts(
    rows: list[dict[str, str]], key: str
) -> dict[tuple[str, float], dict[str, tuple[int, int, int]]]:
    """Counts by (cell without speedup, speedup) and procedure."""
    cells: dict[tuple[str, float], dict[str, tuple[int, int, int]]] = {}
    for r in rows:
        if key == "synthetic":
            parts = r["cell"].split("/")
            name, speed = "/".join(parts[:-1]), float(parts[-1])
        else:
            name, speed = r["cell"].rsplit("/", 1)[0], float(r["speedup"])
        cells.setdefault((name, speed), {})[r["procedure"]] = (
            int(r["keep"]),
            int(r["reject"]),
            int(r["replications"]),
        )
    return cells


def _dominance_rows(title: str, rows: list[dict[str, str]], key: str) -> None:
    write = sys.stdout.write
    cells = _counts(rows, key)
    write(f"\n{title}\n\n")
    write(
        "| Schedule | "
        + " | ".join(f"KEEP {s:.2f}" for s in KEEP_AT)
        + " | "
        + " | ".join(f"REJECT {s:.2f}" for s in REJECT_AT)
        + " |\n"
    )
    write("|---" * (1 + len(KEEP_AT) + len(REJECT_AT)) + "|\n")
    for schedule in SCHEDULE_NAMES:
        out: list[str] = []
        for kind, speeds in (("keep", KEEP_AT), ("reject", REJECT_AT)):
            for speed in speeds:
                diffs: list[float] = []
                bad = 0
                for (name, s), by in cells.items():
                    if abs(s - speed) > 1e-9 or "split" not in by:
                        continue
                    if (
                        name.split("/")[0] != schedule
                        if key == "synthetic"
                        else name.split("/")[2] != schedule
                    ):
                        continue
                    idx = 0 if kind == "keep" else 1
                    total = by["split"][2]
                    p1 = by["split"][idx] / total
                    p0 = by[PRIMARY][idx] / total
                    se = math.sqrt(
                        (p1 * (1 - p1) + p0 * (1 - p0)) / total + 1e-12
                    )
                    diffs.append(100 * (p1 - p0))
                    bad += (p1 - p0) / se < -Z
                out.append(
                    f"{min(diffs):+.1f}, {bad} of {len(diffs)}"
                    if diffs
                    else "-"
                )
        write(f"| {schedule} | " + " | ".join(out) + " |\n")


def _dominance() -> None:
    write = sys.stdout.write
    write(
        "Each entry: the smallest difference of the split to the gated "
        "primary in points, then the cells worse than 3 standard errors "
        "of the cells at that speedup.\n"
    )
    for label, seed in SEEDS:
        rows: list[dict[str, str]] = []
        for name in ("main", "power", "heldout"):
            path = RESULTS / f"t111p2b-{name}-seed-{seed}.csv.gz"
            file_rows = _read(path)
            rows += [r for r in file_rows if r["procedure"] == PRIMARY]
            rows += _split(file_rows, "split")
        _dominance_rows(
            f"Synthetic, {label} seed {seed} (development, exposed, "
            "held-out, power cells; both host modes)",
            rows,
            "synthetic",
        )
    real = _read(RESULTS / f"t111p2b-real-traces-seed-{REAL_SEED}.csv.gz")
    for rule in ("pw", "fixed6", "fixed20"):
        mine = [
            r for r in real if r["rule"] == rule and r["procedure"] == PRIMARY
        ] + _split([r for r in real if r["rule"] == rule], "split")
        _dominance_rows(
            f"Real traces, block rule {rule}, seed {REAL_SEED}", mine, "real"
        )


def main() -> int:
    """Print the three tables."""
    sys.stdout.write("### Acceptance, synthetic grids\n\n")
    _acceptance()
    sys.stdout.write("\n### Failing skew-probe cells by schedule\n\n")
    _skew()
    sys.stdout.write("\n### Acceptance, real traces\n\n")
    _real()
    sys.stdout.write("\n### Dominance against the gated primary\n")
    _dominance()
    return 0


if __name__ == "__main__":
    sys.exit(main())
