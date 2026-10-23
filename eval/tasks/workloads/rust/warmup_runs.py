"""Time a built binary run after run, to see what a first run costs.

A compiled Rust program has no JIT: every execution is a fresh process
running the same machine code. What can still make the first execution
slower is outside the program (the file cache, the loader, the CPU's
caches and clock, and on macOS the system's first look at a new
executable), so this runs one binary many times, in several fresh copies
of it, and prints every wall time.

Usage::

    uv run python eval/tasks/workloads/rust/warmup_runs.py \
        <binary> <runs per copy> <copies> -- <arguments of the binary>

Each copy is a new file (a new inode, as a fresh build is), run
``runs`` times in a row with its stdout discarded. The output is one
line per run, ``copy <c> run <r> <milliseconds>``, preceded by the
machine and the load, and is committed as ``warmup-runs.txt``.
"""

import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TIMEOUT_S = 120


def run_once(binary: Path, arguments: list[str]) -> float:
    """Return the wall time of one execution in milliseconds."""
    start = time.perf_counter_ns()
    subprocess.run(
        [str(binary), *arguments],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
        timeout=TIMEOUT_S,
    )
    return (time.perf_counter_ns() - start) / 1e6


def main(argv: list[str]) -> int:
    """Run the series and write it to stdout."""
    if len(argv) < 4 or "--" not in argv:
        sys.stderr.write(__doc__ or "")
        return 2
    split = argv.index("--")
    binary, runs, copies = Path(argv[0]), int(argv[1]), int(argv[2])
    arguments = argv[split + 1 :]
    out = sys.stdout
    out.write(f"machine {platform.platform()} {platform.machine()}\n")
    out.write(f"cpus {os.cpu_count()} load {os.getloadavg()}\n")
    out.write(f"binary {binary.name} arguments {' '.join(arguments)}\n")
    firsts: list[float] = []
    steadies: list[float] = []
    with tempfile.TemporaryDirectory() as scratch:
        for copy in range(1, copies + 1):
            fresh = Path(scratch) / f"copy{copy}"
            shutil.copy2(binary, fresh)
            times = [run_once(fresh, arguments) for _ in range(runs)]
            for number, wall in enumerate(times, start=1):
                out.write(f"copy {copy} run {number} {wall:.3f}\n")
            firsts.append(times[0])
            steadies.append(statistics.median(times[runs // 2 :]))
    out.write(f"first runs (ms) {[round(t, 3) for t in firsts]}\n")
    out.write(
        f"median of the second half of each copy (ms) "
        f"{[round(t, 3) for t in steadies]}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
