"""Run the full calibration, or a subset of its cells, in parallel.

Usage::

    uv run python tests/calibration/run_calibration.py run \\
        --out scratch/calibration/run.jsonl --workers 4
    uv run python tests/calibration/run_calibration.py report \\
        scratch/calibration/run.jsonl --procedure t

``run`` writes one JSON line per cell, after a first line describing
the run (seed, ``R``, workers, platform, load average). A cell already
in the output file is skipped, so an interrupted run resumes at the
same seed with the same counts; a different seed or ``R`` than the
file's is refused. ``report`` prints the per-cell table for one
procedure, the form ``docs/method.md`` records.

The machine this runs on is shared, so ``--workers`` is at most 4.
"""

import argparse
import csv
import fnmatch
import hashlib
import json
import multiprocessing
import os
import platform
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, cast

import montecarlo as mc
import numpy as np

MAX_WORKERS: Final = 4


def _work(args: tuple[str, int, tuple[str, ...], int]) -> str:
    name, seed, procedures, replications = args
    load = os.getloadavg()
    result = mc.run_cell(
        mc.cell_by_name(name),
        seed=seed,
        procedures=procedures,
        replications=replications,
    )
    result["load_before"] = load
    result["load_after"] = os.getloadavg()
    return json.dumps(result, sort_keys=True)


def _cost(name: str) -> int:
    """Rough relative cost of a cell, to start the slow ones first."""
    cell = mc.cell_by_name(name)
    looks = cell.schedule.looks
    return cell.schedule.max_rounds * looks.max_looks


def run(argv: Sequence[str]) -> int:
    """Run the cells matching the patterns and append their results."""
    parser = argparse.ArgumentParser(prog="run_calibration.py run")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    parser.add_argument("--cells", nargs="*", default=["*"])
    parser.add_argument("--procedures", nargs="*", default=list(mc.PROCEDURES))
    parser.add_argument("--replications", type=int, default=mc.REPLICATIONS)
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= MAX_WORKERS:
        parser.error(f"--workers must be in [1, {MAX_WORKERS}]")
    seed = mc.calibration_seed()
    names = [
        cell.name
        for cell in mc.all_cells()
        if any(fnmatch.fnmatchcase(cell.name, p) for p in args.cells)
    ]
    out: Path = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "seed": seed,
        "replications": args.replications,
        "procedures": args.procedures,
    }
    done: set[str] = set()
    if out.exists():
        lines = out.read_text().splitlines()
        first = json.loads(lines[0])["meta"]
        for key, value in meta.items():
            if first[key] != value:
                msg = f"{out} was run with {key}={first[key]!r}, not {value!r}"
                raise SystemExit(msg)
        done = {json.loads(line)["cell"] for line in lines[1:]}
    else:
        header = {
            **meta,
            "workers": args.workers,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "load_at_start": os.getloadavg(),
            "cpus": os.cpu_count(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "harness_sha256": hashlib.sha256(
                Path(mc.__file__).read_bytes()
            ).hexdigest(),
        }
        out.write_text(json.dumps({"meta": header}, sort_keys=True) + "\n")
    todo = sorted((n for n in names if n not in done), key=_cost, reverse=True)
    sys.stdout.write(
        f"seed {seed}: {len(todo)} cells to run, {len(done)} done\n"
    )
    begun = time.monotonic()
    jobs = [(n, seed, tuple(args.procedures), args.replications) for n in todo]
    context = multiprocessing.get_context("spawn")
    with context.Pool(args.workers) as pool, out.open("a") as sink:
        for count, line in enumerate(
            pool.imap_unordered(_work, jobs), start=1
        ):
            sink.write(line + "\n")
            sink.flush()
            cell = json.loads(line)
            sys.stdout.write(
                f"[{count}/{len(jobs)}] {cell['cell']} "
                f"{cell['wall_seconds']:.1f}s "
                f"load {cell['load_after'][0]:.1f}\n"
            )
            sys.stdout.flush()
    sys.stdout.write(
        f"wall {time.monotonic() - begun:.0f}s, "
        f"load {os.getloadavg()[0]:.1f}\n"
    )
    return 0


def load(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read a run file: its header and its cells in canonical order."""
    lines = path.read_text().splitlines()
    meta = cast("dict[str, Any]", json.loads(lines[0])["meta"])
    cells = {
        c["cell"]: c
        for c in (cast("dict[str, Any]", json.loads(x)) for x in lines[1:])
    }
    order = [c.name for c in mc.all_cells()]
    return meta, [cells[n] for n in order if n in cells]


def report(argv: Sequence[str]) -> int:
    """Print the per-cell table of one procedure, and its failures."""
    parser = argparse.ArgumentParser(prog="run_calibration.py report")
    parser.add_argument("path", type=Path)
    parser.add_argument("--procedure", default="t")
    args = parser.parse_args(argv)
    meta, cells = load(args.path)
    proc: str = args.procedure
    write = sys.stdout.write
    write(f"seed {meta['seed']}, procedure {proc}\n\n")
    head = "| Schedule | Scenario | Host | " + " | ".join(
        f"{s:.2f}" for s in mc.SPEEDUPS
    )
    write(head + " |\n")
    write("|---" * (3 + len(mc.SPEEDUPS)) + "|\n")
    rows: dict[tuple[str, str, str], dict[float, str]] = {}
    failing: list[str] = []
    for cell in cells:
        found = cell["procedures"].get(proc)
        if found is None:
            continue
        key = (cell["schedule"], cell["scenario"], cell["mode"])
        mark = "" if found["passes"] else " **FAIL**"
        rows.setdefault(key, {})[cell["speedup"]] = (
            f"{found['errors']} ({100 * found['cp_upper']:.2f}%){mark}"
        )
        if not found["passes"]:
            failing.append(cell["cell"])
    for (schedule, scenario, mode), by_speedup in rows.items():
        values = " | ".join(by_speedup.get(s, "") for s in mc.SPEEDUPS)
        write(f"| {schedule} | {scenario} | {mode} | {values} |\n")
    write(f"\n{len(failing)} failing cells\n")
    for name in failing:
        write(f"  {name}\n")
    return 0


CSV_COLUMNS: Final = (
    "cell",
    "procedure",
    "replications",
    "keep",
    "reject",
    "inconclusive",
    "errors",
    "cp_upper",
    "passes",
    "conditional_error_rate",
    "rounds_mean",
    "rounds_p95",
    "n0",
    "n0_miss_above",
    "n0_miss_below",
    "n_max",
    "n_max_miss_above",
    "n_max_miss_below",
    "cut_by_time",
)


def table(argv: Sequence[str]) -> int:
    """Write every cell and procedure as one CSV row."""
    parser = argparse.ArgumentParser(prog="run_calibration.py csv")
    parser.add_argument("path", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    _, cells = load(args.path)
    with args.out.open("w", newline="") as sink:
        writer = csv.writer(sink, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)
        for cell in cells:
            sched = mc.SCHEDULE_BY_NAME[cell["schedule"]]
            ends = (str(sched.looks.n0), str(sched.max_rounds))
            for proc, s in cell["procedures"].items():
                cov = s["coverage"]
                fixed: list[object] = []
                for n in ends:
                    got = cov.get(n)
                    fixed += (
                        [n, got["miss_above"], got["miss_below"]]
                        if got
                        else [n, "", ""]
                    )
                cond = s["conditional_error_rate"]
                writer.writerow(
                    [
                        cell["cell"],
                        proc,
                        s["replications"],
                        s["keep"],
                        s["reject"],
                        s["inconclusive"],
                        s["errors"],
                        f"{s['cp_upper']:.6f}",
                        int(s["passes"]),
                        "" if cond is None else f"{cond:.6f}",
                        f"{s['rounds_mean']:.3f}",
                        s["rounds_p95"],
                        *fixed,
                        cell["cut_by_time"],
                    ]
                )
    return 0


def main() -> int:
    """Dispatch to ``run``, ``report`` or ``csv``."""
    commands = {"run": run, "report": report, "csv": table}
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        sys.stderr.write(__doc__ or "")
        return 2
    return commands[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
