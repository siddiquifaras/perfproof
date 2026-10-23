"""Make the golden-set tables from the recorded comparisons.

Usage::

    uv run python tests/golden/golden_report.py \\
        --data eval/golden/data --out eval/golden/results

Reads ``<workload>.csv.gz`` (or ``.csv``) per workload, replays every
comparison through the three methods on every schedule its rounds
cover (`golden_replay`), and writes Markdown tables and a CSV of cell
counts. Nothing here runs a benchmark.
"""

import argparse
import csv
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path

import golden_replay as gr
import numpy as np
from golden_stats import BAR, LIMIT, cp_upper, mcnemar
from golden_summary import MIN_N, write_summary
from numpy.typing import NDArray

WORKLOADS = ("python", "native", "node", "java")
PAIRS = (("gated", "hac_lf"), ("gated", "split"), ("hac_lf", "split"))


def find(data: Path, workload: str) -> Path | None:
    """Return the data file of a workload, if present."""
    for suffix in (".csv.gz", ".csv"):
        path = data / f"{workload}{suffix}"
        if path.exists():
            return path
    return None


def estimate(cell: gr.Cell, used: NDArray[np.int64]) -> NDArray[np.float64]:
    """Geometric-mean speedup on each comparison's rounds used."""
    d = np.log(cell.base / cell.cand)
    return np.array(
        [float(np.exp(d[i, : used[i]].mean())) for i in range(d.shape[0])]
    )


def run(argv: Sequence[str]) -> int:
    """Write the report; return the exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    cells: dict[str, dict[str, gr.Cell]] = {}
    for workload in WORKLOADS:
        path = find(args.data, workload)
        if path is not None:
            cells[workload] = gr.load_cells(path)
    rows: list[dict[str, object]] = []
    keeps: dict[tuple[str, str, str, str], NDArray[np.bool_]] = {}
    details: dict[tuple[str, str, str, str], gr.Replay] = {}
    for workload, by_label in cells.items():
        for label, cell in by_label.items():
            for schedule, (_, max_rounds) in gr.SCHEDULES.items():
                if cell.rounds < max_rounds:
                    continue
                for method in gr.METHODS:
                    rep = gr.replay(cell, schedule, method)
                    key = (workload, label, schedule, method)
                    details[key] = rep
                    keeps[key] = gr.is_outcome(rep, "KEEP")
                    n = len(rep.outcome)
                    k = int(np.count_nonzero(keeps[key]))
                    r = int(np.count_nonzero(gr.is_outcome(rep, "REJECT")))
                    wrong = k if cell.nominal <= BAR + 1e-12 else r
                    rows.append(
                        {
                            "workload": workload,
                            "schedule": schedule,
                            "speedup": label,
                            "method": method,
                            "n": n,
                            "keep": k,
                            "reject": r,
                            "inconclusive": n - k - r,
                            "wrong": wrong,
                            "wrong_kind": "KEEP"
                            if cell.nominal <= BAR + 1e-12
                            else "REJECT",
                            "cp_upper": f"{cp_upper(wrong, n):.5f}",
                            "rounds_mean": f"{rep.rounds_used.mean():.2f}",
                            "seconds_to_verdict": (
                                f"{_wall(cell, rep.rounds_used):.2f}"
                            ),
                            "stats_ms": f"{1e3 * rep.seconds:.3f}",
                        }
                    )
    with (args.out / "cells.csv").open("w", newline="") as sink:
        writer = csv.DictWriter(
            sink, fieldnames=list(rows[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    _write_tables(args.out, rows, cells, keeps, details)
    write_summary(args.out, rows, cells)
    return 0


def _at_most_bar(label: str) -> bool:
    """Whether a cell label is at or below the bar."""
    return float(label) <= BAR + 1e-12


def _above_bar(label: str) -> bool:
    """Whether a cell label is above the bar."""
    return float(label) > BAR + 1e-12


def _share(row: dict[str, object]) -> float:
    """KEEP share of a cell row."""
    return int(str(row["keep"])) / int(str(row["n"]))


def _wall(cell: gr.Cell, used: NDArray[np.int64]) -> float:
    """Mean seconds of recorded executions up to each verdict."""
    total = cell.base + cell.cand
    return float(
        np.mean([total[i, : used[i]].sum() for i in range(total.shape[0])])
    )


def _write_tables(
    out: Path,
    rows: list[dict[str, object]],
    cells: dict[str, dict[str, gr.Cell]],
    keeps: dict[tuple[str, str, str, str], NDArray[np.bool_]],
    details: dict[tuple[str, str, str, str], gr.Replay],
) -> None:
    """Write the Markdown tables."""
    lines: list[str] = ["# Golden set: replay results", ""]
    lines += ["## Wrong-KEEP, worst cell per method and schedule", ""]
    lines += [
        "| Schedule | Method | Cells | Worst wrong-KEEP (cell) "
        "| Worst CP95 upper | Cells above 2.5%, as registered (all) "
        "| Cells above 2.5%, amended (cells of 200 only) |",
        "|---|---|---|---|---|---|---|",
    ]
    for schedule in gr.SCHEDULES:
        for method in gr.METHODS:
            sel = [
                r
                for r in rows
                if r["schedule"] == schedule
                and r["method"] == method
                and r["wrong_kind"] == "KEEP"
            ]
            if not sel:
                continue
            worst = max(sel, key=lambda r: float(str(r["cp_upper"])))
            over = sum(float(str(r["cp_upper"])) > LIMIT for r in sel)
            over_big = sum(
                float(str(r["cp_upper"])) > LIMIT and int(str(r["n"])) >= MIN_N
                for r in sel
            )
            lines.append(
                f"| {schedule} | {method} | {len(sel)} | "
                f"{worst['wrong']}/{worst['n']} "
                f"({worst['workload']}/{worst['speedup']}) | "
                f"{float(str(worst['cp_upper'])):.4f} | {over} | {over_big} |"
            )
    lines += ["", "## Power (KEEP rate) at 1.05 and 1.10", ""]
    lines += [
        "| Workload | Schedule | Speedup | gated | hac_lf | split |",
        "|---|---|---|---|---|---|",
    ]
    for workload, by_label in cells.items():
        for schedule in gr.SCHEDULES:
            for label in ("1.05", "1.10"):
                got = {
                    str(r["method"]): r
                    for r in rows
                    if r["workload"] == workload
                    and r["schedule"] == schedule
                    and r["speedup"] == label
                }
                if len(got) != len(gr.METHODS) or label not in by_label:
                    continue
                cols = " | ".join(
                    f"{100 * _share(got[m]):.1f}" for m in gr.METHODS
                )
                lines.append(f"| {workload} | {schedule} | {label} | {cols} |")
    lines += ["", "## McNemar on KEEP, pooled over cells", ""]
    lines += [
        "| Schedule | Cells | A | B | KEEP only A | KEEP only B | p |",
        "|---|---|---|---|---|---|---|",
    ]
    for schedule in gr.SCHEDULES:
        scopes: list[tuple[str, Callable[[str], bool]]] = [
            ("S <= 1.03", _at_most_bar),
            ("S >= 1.05", _above_bar),
        ]
        for scope, pick in scopes:
            for a, b in PAIRS:
                ka: list[bool] = []
                kb: list[bool] = []
                for (w, label, s, m), arr in keeps.items():
                    if s == schedule and m == a and pick(label):
                        ka += arr.tolist()
                        kb += keeps[(w, label, s, b)].tolist()
                if not ka:
                    continue
                t = mcnemar(np.array(ka), np.array(kb))
                lines.append(
                    f"| {schedule} | {scope} | {a} | {b} | {t.only_a} | "
                    f"{t.only_b} | {t.p:.4g} |"
                )
    lines += ["", "## Disagreement classes (KEEP by one method only)", ""]
    lines += [
        "| Schedule | KEEP by | Other method's result | Count "
        "| Mean speedup estimate at the keeper's verdict |",
        "|---|---|---|---|---|",
    ]
    for schedule in gr.SCHEDULES:
        for a, b in PAIRS:
            for x, y in ((a, b), (b, a)):
                tally: Counter[str] = Counter()
                est: dict[str, list[float]] = {}
                for (w, label, s, m), arr in keeps.items():
                    if s != schedule or m != x:
                        continue
                    other = details[(w, label, s, y)]
                    mine = details[(w, label, s, x)]
                    pick = arr & ~keeps[(w, label, s, y)]
                    if not pick.any():
                        continue
                    e = estimate(cells[w][label], mine.rounds_used)
                    for i in np.flatnonzero(pick):
                        why = other.outcome[i] + (
                            f" ({other.withheld[i]})"
                            if other.withheld[i]
                            else ""
                        )
                        tally[why] += 1
                        est.setdefault(why, []).append(float(e[i]))
                for why, count in tally.most_common():
                    mean = float(np.mean(est[why]))
                    lines.append(
                        f"| {schedule} | {x} (vs {y}) | {why} | {count} "
                        f"| {mean:.4f} |"
                    )
    (out / "replay.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
