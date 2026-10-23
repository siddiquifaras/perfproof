"""Noise at the source: how much each control removes, from raw logs.

Usage::

    uv run python tests/golden/golden_noise.py \\
        --data eval/golden/data --noise eval/golden/noise \\
        --out eval/golden/results

Two inputs. (1) The ``S = 1.00`` cell of the golden campaign, in which
nothing is injected: for every comparison the log ratio ``d`` of the
two arms under four metrics (reported wall time, CPU time, instructions,
cycles), its standard deviation, its order-centred lag-1 and how much of
the wall-time ``d`` the effective frequency (cycles per task-clock
second) and the host load explain. (2) The A/A recordings of
``studies/golden/noise.py``: interleaved against blocked order, pinned
against free, same metrics.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import golden_replay as gr
import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]
METRICS = ("wall", "cpu", "instructions", "cycles")


def lag1(u: FloatArray) -> float:
    """Lag-1 autocorrelation of a centred series (0 if constant)."""
    den = float(np.sum(u * u))
    return float(np.sum(u[:-1] * u[1:]) / den) if den > 0 else 0.0


def order_centre(d: FloatArray, first: NDArray[np.bool_]) -> FloatArray:
    """Subtract the mean of ``d`` over rounds with the same order."""
    u = d.copy()
    for flag in (True, False):
        mask = first == flag
        if mask.any():
            u[mask] -= d[mask].mean()
    return u


def column(rows: list[dict[str, str]], name: str) -> FloatArray:
    """Float column, NaN where empty."""
    return np.array(
        [
            float(r[name]) if r[name] not in ("", "None") else np.nan
            for r in rows
        ]
    )


def golden_null(path: Path) -> list[str]:
    """Tables for the ``1.00`` cell of one workload's golden data."""
    rows = [r for r in gr.open_text(path) if r["cell"] == "1.00"]
    if not rows:
        return []
    reps = sorted({int(r["rep"]) for r in rows})
    out: list[str] = [f"### {rows[0]['workload']}", ""]
    out += [
        "| Metric | SD of d (%) | Order-centred lag-1 | Mean d (%) |",
        "|---|---|---|---|",
    ]
    series: dict[str, list[FloatArray]] = {m: [] for m in METRICS}
    firsts: list[NDArray[np.bool_]] = []
    explain: list[tuple[FloatArray, FloatArray, FloatArray]] = []
    for rep in reps:
        mine = [r for r in rows if int(r["rep"]) == rep]
        a = [r for r in mine if r["arm"] == "A"]
        b = [r for r in mine if r["arm"] == "B"]
        a.sort(key=lambda r: int(r["round"]))
        b.sort(key=lambda r: int(r["round"]))
        firsts.append(np.array([int(r["pos"]) == 0 for r in b]))
        wall_a, wall_b = column(a, "work_s"), column(b, "work_s")
        cpu_a = column(a, "user_s") + column(a, "sys_s")
        cpu_b = column(b, "user_s") + column(b, "sys_s")
        ins_a, ins_b = column(a, "instructions"), column(b, "instructions")
        cyc_a, cyc_b = column(a, "cycles"), column(b, "cycles")
        series["wall"].append(np.log(wall_a / wall_b))
        series["cpu"].append(np.log(cpu_a / cpu_b))
        series["instructions"].append(np.log(ins_a / ins_b))
        series["cycles"].append(np.log(cyc_a / cyc_b))
        freq_a = cyc_a / (column(a, "task_clock_ms") * 1e6)
        freq_b = cyc_b / (column(b, "task_clock_ms") * 1e6)
        explain.append(
            (
                np.log(wall_a / wall_b),
                np.log(freq_a / freq_b),
                column(a, "steal_ticks") - column(b, "steal_ticks"),
            )
        )
    for m in METRICS:
        d = np.concatenate(series[m])
        if not np.isfinite(d).all():
            out.append(f"| {m} | unavailable | | |")
            continue
        r1 = float(
            np.mean(
                [
                    lag1(order_centre(s, f))
                    for s, f in zip(series[m], firsts, strict=True)
                ]
            )
        )
        out.append(
            f"| {m} | {100 * d.std():.4f} | {r1:+.3f} | "
            f"{100 * d.mean():+.4f} |"
        )
    wall = np.concatenate([e[0] for e in explain])
    freq = np.concatenate([e[1] for e in explain])
    steal = np.concatenate([e[2] for e in explain])
    if np.isfinite(freq).all():
        x = np.column_stack([np.ones_like(freq), freq, steal])
        beta, *_ = np.linalg.lstsq(x, wall, rcond=None)
        resid = wall - x @ beta
        out += [
            "",
            f"Wall-time d regressed on the frequency ratio and the steal "
            f"difference: R^2 = {1 - resid.var() / wall.var():.3f}, SD "
            f"{100 * wall.std():.4f}% to {100 * resid.std():.4f}%.",
        ]
    out.append("")
    return out


def recorded_conditions(noise: Path) -> list[str]:
    """Tables for the A/A recordings of ``noise.py``."""
    out: list[str] = [
        "| Workload | Order | Pinned | Pass | Metric | SD of d (%) "
        "| Lag-1 | Mean d (%) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for path in sorted(noise.glob("*.csv")):
        rows = list(gr.open_text(path))
        pairs = sorted({int(r["idx"]) for r in rows})
        a = {int(r["idx"]): r for r in rows if r["arm"] == "A"}
        b = {int(r["idx"]): r for r in rows if r["arm"] == "B"}
        first = np.array([rows.index(b[i]) < rows.index(a[i]) for i in pairs])
        for m in METRICS:

            def value(r: dict[str, str], metric: str = m) -> float:
                if metric == "wall":
                    return float(r["wall_s"])
                if metric == "cpu":
                    return float(r["user_s"]) + float(r["sys_s"])
                return float(r[metric])

            d = np.array([np.log(value(a[i]) / value(b[i])) for i in pairs])
            u = (
                order_centre(d, first)
                if rows[0]["order"] == "interleaved"
                else d - d.mean()
            )
            out.append(
                f"| {rows[0]['workload']} | {rows[0]['order']} | "
                f"{rows[0]['pin'] or 'no'} | {path.stem[-1]} | {m} | "
                f"{100 * d.std():.4f} | {lag1(u):+.3f} | "
                f"{100 * d.mean():+.4f} |"
            )
    return out


def run(argv: Sequence[str]) -> int:
    """Write ``noise.md``; return the exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--noise", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    lines = ["# Noise at the source", "", "## Golden null cell (S = 1.00)", ""]
    for workload in ("python", "native", "node", "java"):
        for suffix in (".csv.gz", ".csv"):
            path = args.data / f"{workload}{suffix}"
            if path.exists():
                lines += golden_null(path)
                break
    lines += ["## A/A recordings by condition", ""]
    lines += recorded_conditions(args.noise)
    (args.out / "noise.md").write_text("\n".join(lines) + "\n")
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
