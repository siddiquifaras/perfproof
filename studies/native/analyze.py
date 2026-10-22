"""Summarise the native warm-up runs of ``run.sh``.

Reads ``blocks.txt`` (per process, 60 lines ``block <n> <ns per
decompression>``) and writes the numbers quoted in ``docs/method.md``
under "Warm-up (C and C++)".

Command that produced the committed output::

    uv run python studies/native/analyze.py \
        studies/native/runs-warmup > studies/native/results.txt
"""

import statistics
import sys
from pathlib import Path

STEADY_FROM = 10
"""Blocks before this index are not counted as steady state."""
QUIET_CV = 10.0
"""A process whose steady blocks vary by at most this, in percent, ran
on a machine quiet enough to read a first-block effect from."""


def read_blocks(path: Path) -> list[list[float]]:
    """Return, per process, each block's ns per decompression."""
    processes: list[list[float]] = []
    for row in path.read_text().splitlines():
        words = row.split()
        if words[0] == "process":
            processes.append([])
        else:
            processes[-1].append(float(words[2]))
    return processes


def cv(values: list[float]) -> float:
    """Return the coefficient of variation, in percent."""
    return 100 * statistics.pstdev(values) / statistics.fmean(values)


def main(directory: Path) -> None:
    """Write the summary of the runs in ``directory``."""
    out = sys.stdout.write
    processes = read_blocks(directory / "blocks.txt")
    out((directory / "environment.txt").read_text())
    out(
        f"{len(processes)} fresh processes, {len(processes[0])} blocks of "
        "300 decompressions each\n\n"
    )
    steady = [statistics.median(b[STEADY_FROM:]) for b in processes]
    out("Per process (ns per decompression):\n")
    out("process  first block  steady median  first/steady  steady CV%\n")
    firsts: list[float] = []
    for i, blocks in enumerate(processes, start=1):
        firsts.append(blocks[0] / steady[i - 1])
        out(
            f"{i:7d}  {blocks[0]:11.1f}  {steady[i - 1]:13.1f}  "
            f"{blocks[0] / steady[i - 1]:12.2f}  "
            f"{cv(blocks[STEADY_FROM:]):10.1f}\n"
        )
    out(
        f"\nfirst block / steady median: min {min(firsts):.2f}, "
        f"median {statistics.median(firsts):.2f}, max {max(firsts):.2f}\n"
    )
    quiet = [
        first
        for first, blocks in zip(firsts, processes, strict=True)
        if cv(blocks[STEADY_FROM:]) <= QUIET_CV
    ]
    out(
        f"processes whose steady CV is at most {QUIET_CV:g}% "
        f"({len(quiet)} of {len(processes)}): first/steady min "
        f"{min(quiet):.2f}, median {statistics.median(quiet):.2f}, "
        f"max {max(quiet):.2f}\n"
    )
    out(
        f"steady medians of the {len(processes)} identical processes: "
        f"min {min(steady):.1f}, max {max(steady):.1f}, "
        f"max/min {max(steady) / min(steady):.3f}\n\n"
    )
    out("Pooled over processes, median block time / that process's steady:\n")
    for index in (0, 1, 2, 3, 4, 5, 9, 10, 30, 59):
        ratios = [b[index] / steady[i] for i, b in enumerate(processes)]
        out(f"  block {index:2d}: {statistics.median(ratios):.3f}\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
