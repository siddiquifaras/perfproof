"""Summarise the Go warm-up runs of ``run.sh``.

Reads ``blocks.txt`` (per process, 60 lines ``block <n> <ns per
request> <garbage collections in the block>``) and ``startup.txt``
(lines ``go-run <ns>`` and ``binary <ns>``) and writes the numbers
quoted in ``docs/method.md`` under "Warm-up (Go)".

Command that produced the committed output::

    uv run python studies/go/analyze.py \
        studies/go/runs > studies/go/results.txt
"""

import statistics
import sys
from pathlib import Path

STEADY_FROM = 10
"""Blocks before this index are not counted as steady state."""


def read_blocks(path: Path) -> list[list[tuple[float, int]]]:
    """Return, per process, each block as ``(ns per request, GCs)``."""
    processes: list[list[tuple[float, int]]] = []
    for row in path.read_text().splitlines():
        words = row.split()
        if words[0] == "process":
            processes.append([])
        else:
            processes[-1].append((float(words[2]), int(words[3])))
    return processes


def cv(values: list[float]) -> float:
    """Return the coefficient of variation, in percent."""
    return 100 * statistics.pstdev(values) / statistics.fmean(values)


def main(directory: Path) -> None:
    """Write the summary of the runs in ``directory``."""
    out = sys.stdout.write
    processes = read_blocks(directory / "blocks.txt")
    out(f"{directory / 'go-version.txt'}: ")
    out((directory / "go-version.txt").read_text())
    out(
        f"{len(processes)} fresh processes, "
        f"{len(processes[0])} blocks of 100000 requests each\n\n"
    )
    steady = [
        statistics.median(t for t, _ in blocks[STEADY_FROM:])
        for blocks in processes
    ]
    out("Per process (ns per request):\n")
    out("process  first block  steady median  first/steady  steady CV%\n")
    firsts: list[float] = []
    for i, blocks in enumerate(processes, start=1):
        times = [t for t, _ in blocks]
        firsts.append(times[0] / steady[i - 1])
        out(
            f"{i:7d}  {times[0]:11.1f}  {steady[i - 1]:13.1f}  "
            f"{times[0] / steady[i - 1]:12.2f}  "
            f"{cv(times[STEADY_FROM:]):10.1f}\n"
        )
    out(
        f"\nfirst block / steady median: min {min(firsts):.2f}, "
        f"median {statistics.median(firsts):.2f}, max {max(firsts):.2f}\n"
    )
    out(
        "steady medians of the 12 identical processes: "
        f"min {min(steady):.1f}, max {max(steady):.1f}, "
        f"max/min {max(steady) / min(steady):.3f}\n\n"
    )
    out("Pooled over processes, median block time / that process's steady:\n")
    for index in (0, 1, 2, 3, 4, 5, 9, 10, 30, 59):
        ratios = [
            blocks[index][0] / steady[i] for i, blocks in enumerate(processes)
        ]
        out(f"  block {index:2d}: {statistics.median(ratios):.3f}\n")
    out("\nGarbage collections in a steady block, pooled:\n")
    by_gc: dict[int, list[float]] = {}
    for i, blocks in enumerate(processes):
        for t, gcs in blocks[STEADY_FROM:]:
            by_gc.setdefault(gcs, []).append(t / steady[i])
    for gcs in sorted(by_gc):
        ratios = by_gc[gcs]
        out(
            f"  {gcs} GC: {len(ratios):4d} blocks, median "
            f"{statistics.median(ratios):.3f} of steady\n"
        )
    first_gcs = [blocks[0][1] for blocks in processes]
    out(f"  GCs in the first block, per process: {first_gcs}\n")
    totals = [sum(gcs for _, gcs in blocks) for blocks in processes]
    out(
        f"  GCs over all {len(processes[0])} blocks, per process: "
        f"min {min(totals)}, median {statistics.median(totals):g}, "
        f"max {max(totals)}\n\n"
    )
    startup: dict[str, list[float]] = {"go-run": [], "binary": []}
    for row in (directory / "startup.txt").read_text().splitlines():
        name, value = row.split()
        startup[name].append(float(value) / 1e6)
    out("Empty program, wall time per start (ms):\n")
    for name, values in startup.items():
        out(
            f"  {name:7s} n={len(values)} median "
            f"{statistics.median(values):.2f}, min {min(values):.2f}, "
            f"max {max(values):.2f}\n"
        )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
