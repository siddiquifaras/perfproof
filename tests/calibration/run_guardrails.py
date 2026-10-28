"""Run the guardrail calibration, or a subset of its cells, in parallel.

Usage::

    uv run python tests/calibration/run_guardrails.py run \\
        --out scratch/guardrails/run.jsonl --workers 4
    uv run python tests/calibration/run_guardrails.py report \\
        scratch/guardrails/run.jsonl --csv results/guardrails.csv

``run`` writes one JSON line per cell, after a first line naming the run
(seed, ``R``, workers, platform). A cell already in the output file is
skipped, so an interrupted run resumes at the same seed with the same
counts; a different seed or ``R`` than the file's is refused. ``report``
prints the verdict of the acceptance rule for the cells and, with
``--csv``, writes the per-cell table ``docs/method.md`` records.
"""

import argparse
import csv
import fnmatch
import json
import multiprocessing
import os
import platform
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, cast

import guardrail_mc as g
import montecarlo as mc

MAX_WORKERS: Final = 4
COLUMNS: Final[tuple[str, ...]] = (
    "cell",
    "procedure",
    "replications",
    "truly_within",
    "messy",
    "alone_errors",
    "alone_cp_upper",
    "alone_passes",
    "alone_passed",
    "alone_breached",
    "alone_undecided",
    "verdict_errors",
    "verdict_cp_upper",
    "verdict_passes",
    "verdict_keep",
    "verdict_reject",
    "verdict_performance_reject",
    "verdict_inconclusive",
    "rounds_p95",
    "cov_n0_bounded",
    "cov_n0_miss_upper",
    "cov_n0_miss_lower",
    "cov_nmax_bounded",
    "cov_nmax_miss_upper",
    "cov_nmax_miss_lower",
    "coverage_passes",
)


def _work(args: tuple[str, int, int]) -> str:
    name, seed, replications = args
    result = g.run_cell(
        g.cell_by_name(name), seed=seed, replications=replications
    )
    return json.dumps(result, sort_keys=True)


def _read(path: Path) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Return the header and the finished cells' lines, by name."""
    if not path.exists():
        return None, {}
    header: dict[str, Any] | None = None
    done: dict[str, str] = {}
    for line in path.read_text().splitlines():
        record = cast("dict[str, Any]", json.loads(line))
        if "run" in record:
            header = record
        else:
            done[str(record["cell"])] = line
    return header, done


def run(argv: Sequence[str]) -> int:
    """Run the cells matching the patterns and append their results."""
    parser = argparse.ArgumentParser(prog="run_guardrails.py run")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    parser.add_argument("--cells", nargs="*", default=["*"])
    parser.add_argument("--replications", type=int, default=mc.REPLICATIONS)
    options = parser.parse_args(argv)
    if not 1 <= options.workers <= MAX_WORKERS:
        sys.stderr.write(f"--workers is 1 to {MAX_WORKERS}\n")
        return 2
    seed = g.calibration_seed()
    header, done = _read(options.out)
    if header is not None and (
        header["seed"] != seed
        or header["replications"] != options.replications
    ):
        sys.stderr.write("the output file is from another seed or R\n")
        return 2
    options.out.parent.mkdir(parents=True, exist_ok=True)
    wanted = [
        c.name
        for c in g.all_cells()
        if any(fnmatch.fnmatchcase(c.name, p) for p in options.cells)
        and c.name not in done
    ]
    with options.out.open("a") as out:
        if header is None:
            out.write(
                json.dumps(
                    {
                        "run": "guardrails",
                        "seed": seed,
                        "replications": options.replications,
                        "workers": options.workers,
                        "platform": platform.platform(),
                        "started": time.time(),
                    },
                    sort_keys=True,
                )
                + "\n"
            )
        begun = time.monotonic()
        jobs = [(name, seed, options.replications) for name in wanted]
        with multiprocessing.Pool(options.workers) as pool:
            for index, line in enumerate(pool.imap_unordered(_work, jobs), 1):
                out.write(line + "\n")
                out.flush()
                if index % 50 == 0 or index == len(jobs):
                    sys.stderr.write(
                        f"{index}/{len(jobs)} cells, "
                        f"{time.monotonic() - begun:.0f} s\n"
                    )
    return 0


def _row(result: dict[str, Any]) -> dict[str, Any]:
    alone = cast("dict[str, Any]", result["alone"])
    verdict = cast("dict[str, Any]", result["verdict"])
    row: dict[str, Any] = {
        "cell": result["cell"],
        "procedure": result["procedure"],
        "replications": result["replications"],
        "truly_within": int(result["truly_within"]),
        "messy": int(result["messy"]),
        "rounds_p95": result["rounds_p95"],
    }
    for prefix, found in (("alone", alone), ("verdict", verdict)):
        row[f"{prefix}_errors"] = found["errors"]
        row[f"{prefix}_cp_upper"] = f"{found['cp_upper']:.6f}"
        row[f"{prefix}_passes"] = int(found["passes"])
    for key in ("passed", "breached", "undecided"):
        row[f"alone_{key}"] = alone[key]
    for key in ("keep", "reject", "performance_reject", "inconclusive"):
        row[f"verdict_{key}"] = verdict[key]
    covered = cast("dict[str, dict[str, int]]", result["coverage"])
    holds = True
    for label in ("n0", "nmax"):
        for key in ("bounded", "miss_upper", "miss_lower"):
            row[f"cov_{label}_{key}"] = 0
    for label, at in zip(
        ("n0", "nmax"), sorted(covered, key=int), strict=False
    ):
        found = covered[at]
        row[f"cov_{label}_bounded"] = found["bounded"]
        row[f"cov_{label}_miss_upper"] = found["miss_upper"]
        row[f"cov_{label}_miss_lower"] = found["miss_lower"]
        for side in ("miss_upper", "miss_lower"):
            holds &= g.coverage_holds(
                found[side], int(result["replications"]), float(result["tail"])
            )
    row["coverage_passes"] = int(holds)
    return row


def report(argv: Sequence[str]) -> int:
    """Print the acceptance verdict of a finished run."""
    parser = argparse.ArgumentParser(prog="run_guardrails.py report")
    parser.add_argument("path", type=Path)
    parser.add_argument("--csv", type=Path)
    options = parser.parse_args(argv)
    header, done = _read(options.path)
    if header is None:
        sys.stderr.write("no header: nothing was run\n")
        return 2
    rows = [
        _row(cast("dict[str, Any]", json.loads(line)))
        for _, line in sorted(done.items())
    ]
    expected = {c.name for c in g.all_cells()}
    missing = expected - set(done)
    failing = [
        r
        for r in rows
        if not (
            r["alone_passes"] and r["verdict_passes"] and r["coverage_passes"]
        )
    ]
    messy = [r for r in failing if r["messy"]]
    sys.stdout.write(
        f"seed {header['seed']}, R {header['replications']}: {len(rows)} "
        f"cells of {len(expected)} ({len(missing)} missing), "
        f"{len(failing)} failing the rule, {len(messy)} of them messy "
        "limit cells (outliers or a slow mode in the candidate's values)\n"
    )
    for r in failing:
        sys.stdout.write(
            f"  {r['cell']}: alone {r['alone_errors']} "
            f"(CP {r['alone_cp_upper']}), verdict {r['verdict_errors']} "
            f"(CP {r['verdict_cp_upper']})\n"
        )
    worst = sorted(
        rows,
        key=lambda r: max(
            float(r["alone_cp_upper"]), float(r["verdict_cp_upper"])
        ),
        reverse=True,
    )[:10]
    sys.stdout.write("largest bounds:\n")
    for r in worst:
        sys.stdout.write(
            f"  {r['cell']}: alone {r['alone_errors']} / "
            f"{r['alone_cp_upper']}, verdict {r['verdict_errors']} / "
            f"{r['verdict_cp_upper']}\n"
        )
    if options.csv is not None:
        options.csv.parent.mkdir(parents=True, exist_ok=True)
        with options.csv.open("w", newline="") as f:
            writer: csv.DictWriter[str] = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
    return 0 if not failing and not missing else 1


def main(argv: Sequence[str]) -> int:
    """Dispatch ``run`` or ``report``."""
    if not argv or argv[0] not in ("run", "report"):
        sys.stderr.write(__doc__ or "")
        return 2
    return (run if argv[0] == "run" else report)(argv[1:])


if __name__ == "__main__":
    os.environ.setdefault("PYTHONHASHSEED", "0")
    raise SystemExit(main(sys.argv[1:]))
