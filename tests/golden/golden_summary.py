"""The summary tables of the golden-set report.

Made from the cell rows and recorded cells that `golden_report`
builds: the injection check, admissibility against the wrong-KEEP
limit, where ``hac_lf`` keeps fewer than the gated primary, and the
rounds, time and cost to a verdict.
"""

from pathlib import Path

import golden_replay as gr
import numpy as np
from golden_stats import BAR, LIMIT

Row = dict[str, object]
MIN_N = 200
"""Comparisons per cell from which a cell is judged under the amended
rule; the registered rule judges every cell."""


def _int(row: Row, key: str) -> int:
    return int(str(row[key]))


def _float(row: Row, key: str) -> float:
    return float(str(row[key]))


def cells_above(rows: list[Row], method: str, min_n: int) -> list[Row]:
    """Return the wrong-KEEP cells of ``method`` above the 2.5% limit.

    Parameters
    ----------
    rows : list of dict
        The cell rows of ``cells.csv``.
    method : str
        A method name.
    min_n : int
        Only cells with at least this many comparisons count; 0 is the
        rule as registered (every cell), `MIN_N` the amended one.

    Returns
    -------
    list of dict
        Wrong-KEEP rows of ``method`` with ``n >= min_n`` and a CP95
        upper bound above `LIMIT`.
    """
    return [
        r
        for r in rows
        if r["method"] == method
        and r["wrong_kind"] == "KEEP"
        and _int(r, "n") >= min_n
        and _float(r, "cp_upper") > LIMIT
    ]


def _injection(cells: dict[str, dict[str, gr.Cell]]) -> list[str]:
    lines = ["## Injection check (measured, per cell)", ""]
    lines += [
        "| Workload | Cell | Comparisons | Achieved spin (%) "
        "| Expected mean d (%) | Measured mean d (%) | SE (%) |",
        "|---|---|---|---|---|---|---|",
    ]
    for workload, by_label in cells.items():
        for label, cell in by_label.items():
            d = np.log(cell.base / cell.cand)
            per = d.mean(axis=1)
            se = float(per.std(ddof=1) / np.sqrt(per.size))
            achieved = float(np.mean(np.log1p(cell.spin_ratio)))
            lines.append(
                f"| {workload} | {label} | {per.size} | "
                f"{100 * achieved:.4f} | "
                f"{100 * np.log(cell.nominal):.4f} | "
                f"{100 * d.mean():.4f} | {100 * se:.4f} |"
            )
    return lines


def _admissibility(rows: list[Row]) -> list[str]:
    lines = ["", "## Admissibility (wrong-KEEP CP95 upper bound <= 2.5%)", ""]
    lines += [
        '**As registered** (`docs/method.md`, "Choosing"): every cell. '
        "A cell of 100 comparisons (S = 1.02) cannot show 2.5% even with "
        "no error (bound 2.95%), so under the rule as registered no "
        "method is admissible. **Amended after the data**, proposed by the "
        "task author and accepted by the owner on 2026-10-23: judge only "
        "the cells of 200 (S = 1.00 and 1.03). Both are shown. Every "
        "method had 0 wrong-KEEPs in the "
        "S = 1.02 cells.",
        "",
        "| Method | Cells | Cells of 200 | Wrong-KEEPs in cells of 200 "
        "| Worst cell of 200 | Worst CP95 | Cells above 2.5%, as registered "
        "(all cells) | Cells above 2.5%, amended (cells of 200) "
        "| Wrong-KEEPs in S=1.02 cells |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    wrong = [r for r in rows if r["wrong_kind"] == "KEEP"]
    for method in gr.METHODS:
        mine = [r for r in wrong if r["method"] == method]
        big = [r for r in mine if _int(r, "n") >= MIN_N]
        small = [r for r in mine if _int(r, "n") < MIN_N]
        worst = max(big, key=lambda r: _float(r, "cp_upper"))
        lines.append(
            f"| {method} | {len(mine)} | {len(big)} | "
            f"{sum(_int(r, 'wrong') for r in big)} | "
            f"{worst['workload']}/{worst['schedule']}/{worst['speedup']} "
            f"({worst['wrong']}/{worst['n']}) | "
            f"{_float(worst, 'cp_upper'):.4f} | "
            f"{len(cells_above(rows, method, 0))} | "
            f"{len(cells_above(rows, method, MIN_N))} | "
            f"{sum(_int(r, 'wrong') for r in small)} |"
        )
    lines += ["", "Cells of 200 above 2.5% (amended rule):", ""]
    lines += [
        "| Method | Workload | Schedule | S | Wrong-KEEP | CP95 |",
        "|---|---|---|---|---|---|",
    ]
    for method in gr.METHODS:
        lines.extend(
            f"| {method} | {r['workload']} | {r['schedule']} | "
            f"{r['speedup']} | {r['wrong']}/{r['n']} | {r['cp_upper']} |"
            for r in cells_above(rows, method, MIN_N)
        )
    return lines


def _losses(rows: list[Row]) -> list[str]:
    lines = ["", "## Where `hac_lf` keeps fewer than the gated primary", ""]
    lines += [
        "Power cells (S = 1.05, 1.10); KEEP counts out of 100.",
        "",
        "| Workload | Schedule | S | gated | hac_lf | Difference |",
        "|---|---|---|---|---|---|",
    ]
    index = {
        (
            str(r["workload"]),
            str(r["schedule"]),
            str(r["speedup"]),
            str(r["method"]),
        ): r
        for r in rows
    }
    for (w, s, lab, m), g in index.items():
        if m != "gated" or float(lab) <= BAR + 1e-12:
            continue
        h = index[(w, s, lab, "hac_lf")]
        diff = _int(h, "keep") - _int(g, "keep")
        if diff < 0:
            lines.append(
                f"| {w} | {s} | {lab} | {g['keep']} | {h['keep']} | {diff} |"
            )
    return lines


def _costs(rows: list[Row]) -> list[str]:
    lines = ["", "## Rounds and time to verdict, mean over workloads", ""]
    lines += [
        "| Schedule | S | Method | Rounds | Seconds of executions "
        "| Statistics ms per verdict |",
        "|---|---|---|---|---|---|",
    ]
    for schedule in gr.SCHEDULES:
        for lab in ("1.00", "1.05", "1.10"):
            for method in gr.METHODS:
                sel = [
                    r
                    for r in rows
                    if r["schedule"] == schedule
                    and r["speedup"] == lab
                    and r["method"] == method
                ]
                if not sel:
                    continue
                rounds = np.mean([_float(r, "rounds_mean") for r in sel])
                secs = np.mean([_float(r, "seconds_to_verdict") for r in sel])
                ms = np.mean([_float(r, "stats_ms") for r in sel])
                lines.append(
                    f"| {schedule} | {lab} | {method} | {rounds:.1f} | "
                    f"{secs:.1f} | {ms:.2f} |"
                )
    return lines


def write_summary(
    out: Path, rows: list[Row], cells: dict[str, dict[str, gr.Cell]]
) -> None:
    """Write ``summary.md`` into ``out``.

    Parameters
    ----------
    out : pathlib.Path
        Existing results directory.
    rows : list of dict
        The cell rows of ``cells.csv``.
    cells : dict
        Recorded cells by workload and label.
    """
    lines = ["# Golden set: summary", ""]
    lines += _injection(cells)
    lines += _admissibility(rows)
    lines += _losses(rows)
    lines += _costs(rows)
    (out / "summary.md").write_text("\n".join(lines) + "\n")
