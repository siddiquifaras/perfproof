"""Run every procedure on block-bootstrap replicates of real A/A traces.

Usage::

    uv run python tests/calibration/run_real.py run \\
        --out scratch/real/run.csv --workers 4
    uv run python tests/calibration/run_real.py report \\
        scratch/real/run.csv

Per trace, block rule (``pw`` = Politis-White, or a fixed even length),
schedule and true speedup (`SPEEDUPS`) one cell of ``R = 4000``
replicates, each a complete comparison on the schedule's looks run
through ``montecarlo._run_chunk``: the production primary, the
harness's ungated interval and every challenger, on the same rounds
(no time budget: every replicate collects ``max_rounds``). See
`real_traces` for how a replicate and its known speedup are built. One
host only. Every cell draws from its own seed sequence keyed by `SEED`
and the cell's name.
"""

import argparse
import csv
import multiprocessing
import sys
import time
import zlib
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Final

import montecarlo as mc
import numpy as np
import real_traces as rt

SEED: Final = 8737169782817125482
"""Drawn before the real-trace runs."""
SPEEDUPS: Final = (0.80, 0.90, 1.00, 1.03, 1.05, 1.10, 1.20)
COLUMNS: Final = (
    "cell",
    "trace",
    "rule",
    "block",
    "schedule",
    "speedup",
    "procedure",
    "replications",
    "keep",
    "reject",
    "inconclusive",
    "errors",
    "cp_upper",
    "passes",
    "rounds_mean",
)
PROCEDURES: Final = (
    *mc.ALL_PROCEDURES,
    *mc.WAVE_2_NAMES,
    *mc.WAVE_3_NAMES,
)


@cache
def _traces() -> dict[str, rt.Trace]:
    return {t.name: t for t in rt.load_all()}


def block_rules(trace: rt.Trace) -> dict[str, int]:
    """The block lengths run on ``trace``, by rule name."""
    rules = {"pw": rt.politis_white_block(trace)}
    rules |= {f"fixed{b}": b for b in rt.FIXED_BLOCKS}
    return {
        k: v for k, v in rules.items() if v <= trace.pairs - trace.pairs % 2
    }


def _cell_name(trace: str, rule: str, schedule: str, speedup: float) -> str:
    return f"{trace}/{rule}/{schedule}/{speedup:.2f}"


def run_one(
    args: tuple[str, str, str, float, int, int],
) -> list[dict[str, object]]:
    """Run one cell and return its rows, one per procedure."""
    trace_name, rule, schedule_name, speedup, replications, seed = args
    trace = _traces()[trace_name]
    block = block_rules(trace)[rule]
    sched = mc.SCHEDULE_BY_NAME[schedule_name]
    name = _cell_name(trace_name, rule, schedule_name, speedup)
    rng = np.random.default_rng(
        np.random.SeedSequence(seed, spawn_key=(zlib.crc32(name.encode()),))
    )
    cell = mc.Cell(sched, f"{trace_name}/{rule}", "one_host", speedup)
    run = mc._CellRun(  # pyright: ignore[reportPrivateUsage]
        cell=cell,
        confidence=mc.look_confidence(mc.DEFAULT_ALPHA, sched.looks.max_looks),
        cross_n0=None,
        fixed_n=(sched.looks.n0, sched.max_rounds),
        tallies={p: mc.Tally() for p in PROCEDURES},
    )
    log_times, first, covariate = rt.replicates(
        trace, block, speedup, replications, sched.max_rounds, rng
    )
    rounds = np.full(replications, sched.max_rounds, dtype=np.int64)
    mc._run_chunk(run, log_times, first, covariate, rounds)  # pyright: ignore[reportPrivateUsage]
    rows: list[dict[str, object]] = []
    for procedure, tally in run.tallies.items():
        s = tally.summary(cell)
        rows.append(
            {
                "cell": name,
                "trace": trace_name,
                "rule": rule,
                "block": block,
                "schedule": schedule_name,
                "speedup": f"{speedup:.2f}",
                "procedure": procedure,
                "replications": s["replications"],
                "keep": s["keep"],
                "reject": s["reject"],
                "inconclusive": s["inconclusive"],
                "errors": s["errors"],
                "cp_upper": f"{s['cp_upper']:.6f}",
                "passes": int(bool(s["passes"])),
                "rounds_mean": f"{s['rounds_mean']:.3f}",
            }
        )
    return rows


def run(argv: Sequence[str]) -> int:
    """Run every cell and write the CSV."""
    parser = argparse.ArgumentParser(prog="run_real.py run")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--replications", type=int, default=mc.REPLICATIONS)
    parser.add_argument("--traces", nargs="*", default=None)
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 4:
        parser.error("--workers must be in [1, 4]")
    jobs: list[tuple[str, str, str, float, int, int]] = []
    for trace in rt.load_all():
        if args.traces and trace.name not in args.traces:
            continue
        for rule in block_rules(trace):
            jobs.extend(
                (
                    trace.name,
                    rule,
                    sched.name,
                    speedup,
                    args.replications,
                    SEED,
                )
                for sched in mc.SCHEDULES
                for speedup in SPEEDUPS
            )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    begun = time.monotonic()
    context = multiprocessing.get_context("spawn")
    with (
        context.Pool(args.workers) as pool,
        args.out.open("w", newline="") as sink,
    ):
        writer = csv.writer(sink, lineterminator="\n")
        writer.writerow(COLUMNS)
        for count, rows in enumerate(pool.imap_unordered(run_one, jobs), 1):
            writer.writerows([[r[c] for c in COLUMNS] for r in rows])
            sink.flush()
            sys.stdout.write(f"[{count}/{len(jobs)}] {rows[0]['cell']}\n")
            sys.stdout.flush()
    sys.stdout.write(f"wall {time.monotonic() - begun:.0f}s\n")
    return 0


def report(argv: Sequence[str]) -> int:
    """Print false-KEEP cells (1.00 and 1.03) per procedure and rule."""
    parser = argparse.ArgumentParser(prog="run_real.py report")
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    with args.path.open(newline="") as source:
        rows = list(csv.DictReader(source))
    write = sys.stdout.write
    write(
        "| Procedure | Rule | Cells (1.00, 1.03) | Failing "
        "| Worst false KEEPs |\n"
    )
    write("|---|---|---|---|---|\n")
    for rule in ("pw", "fixed6", "fixed20"):
        for proc in PROCEDURES:
            mine = [
                r
                for r in rows
                if r["procedure"] == proc
                and r["rule"] == rule
                and float(r["speedup"]) >= 1.0
                and float(r["speedup"]) <= mc.BAR
            ]
            if not mine:
                continue
            failing = [r for r in mine if r["passes"] != "1"]
            worst = max(mine, key=lambda r: int(r["errors"]))
            write(
                f"| {proc} | {rule} | {len(mine)} | {len(failing)} | "
                f"{worst['errors']} {worst['cell']} |\n"
            )
    return 0


def power(argv: Sequence[str]) -> int:
    """Print KEEP and REJECT rates and mean rounds, per trace.

    Per trace and schedule (Politis-White blocks), for each procedure
    named: KEEP % at 1.05, 1.10 and 1.20, REJECT % at 0.90 and 0.80,
    mean rounds at 1.10; then the mean over traces. Use ``--rule`` for a
    fixed block rule, ``--exclude`` to leave out traces (picolm has few
    pairs).
    """
    parser = argparse.ArgumentParser(prog="run_real.py power")
    parser.add_argument("path", type=Path)
    parser.add_argument("--procedures", nargs="+", required=True)
    parser.add_argument("--rule", default="pw")
    parser.add_argument("--exclude", nargs="*", default=[])
    parser.add_argument("--mean-only", action="store_true")
    args = parser.parse_args(argv)
    with args.path.open(newline="") as source:
        rows = [
            r
            for r in csv.DictReader(source)
            if r["rule"] == args.rule and r["trace"] not in args.exclude
        ]
    cols = (
        ("keep", 1.05),
        ("keep", 1.10),
        ("keep", 1.20),
        ("reject", 0.90),
        ("reject", 0.80),
        ("rounds_mean", 1.10),
    )
    index = {
        (r["trace"], r["schedule"], r["procedure"], float(r["speedup"])): r
        for r in rows
    }
    traces = sorted({r["trace"] for r in rows})
    write = sys.stdout.write
    head = (
        "| Trace | Procedure | KEEP 1.05 | KEEP 1.10 | KEEP 1.20 "
        "| REJECT 0.90 | REJECT 0.80 | rounds 1.10 |\n"
    )
    for sched in (s.name for s in mc.SCHEDULES):
        write(f"\n### {sched} ({args.rule} blocks)\n\n{head}")
        write("|---" * 8 + "|\n")
        for trace in ("MEAN",) if args.mean_only else (*traces, "MEAN"):
            for proc in args.procedures:
                vals: list[str] = []
                for kind, speed in cols:
                    found = [
                        index[t, sched, proc, speed]
                        for t in traces
                        if trace in (t, "MEAN")
                    ]
                    if kind == "rounds_mean":
                        x = [float(r[kind]) for r in found]
                        vals.append(f"{sum(x) / len(x):.1f}")
                    else:
                        x = [
                            int(r[kind]) / int(r["replications"])
                            for r in found
                        ]
                        vals.append(f"{100 * sum(x) / len(x):.1f}")
                write(f"| {trace} | {proc} | " + " | ".join(vals) + " |\n")
    return 0


def falsekeep(argv: Sequence[str]) -> int:
    """Print false-KEEP counts at 1.00 and 1.03 per trace and schedule.

    One row per trace and schedule, one column per procedure, each
    ``count at 1.00 / count at 1.03`` of 4000 (a count above 83 fails
    the acceptance rule, marked ``*``).
    """
    parser = argparse.ArgumentParser(prog="run_real.py falsekeep")
    parser.add_argument("path", type=Path)
    parser.add_argument("--procedures", nargs="+", required=True)
    parser.add_argument("--rule", default="pw")
    args = parser.parse_args(argv)
    with args.path.open(newline="") as source:
        rows = [r for r in csv.DictReader(source) if r["rule"] == args.rule]
    index = {
        (r["trace"], r["schedule"], r["procedure"], float(r["speedup"])): r
        for r in rows
    }
    write = sys.stdout.write
    write("| Trace | Schedule | " + " | ".join(args.procedures) + " |\n")
    write("|---" * (2 + len(args.procedures)) + "|\n")
    for trace in sorted({r["trace"] for r in rows}):
        for sched in (s.name for s in mc.SCHEDULES):
            cells: list[str] = []
            for proc in args.procedures:
                a = index[trace, sched, proc, 1.00]
                b = index[trace, sched, proc, 1.03]
                mark = "" if a["passes"] == b["passes"] == "1" else "*"
                cells.append(f"{a['keep']} / {b['keep']}{mark}")
            write(f"| {trace} | {sched} | " + " | ".join(cells) + " |\n")
    return 0


def main() -> int:
    """Dispatch to ``run``, ``report``, ``power`` or ``falsekeep``."""
    commands = {
        "run": run,
        "report": report,
        "power": power,
        "falsekeep": falsekeep,
    }
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        sys.stderr.write(__doc__ or "")
        return 2
    return commands[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
