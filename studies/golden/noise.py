"""Record A/A noise under four conditions (noise-at-source study).

Two identical trees of a workload run as fresh processes, ``--pairs``
times each, under every combination of

* order: ``interleaved`` (the order pairs of perfproof's
  ``round_orders``: each pair runs in opposite orders in consecutive
  rounds, a coin per pair) or ``blocked`` (every ``A`` run, then every
  ``B`` run);
* pinning: none, or ``taskset -c 0``.

Each execution is wrapped in ``perf stat`` on Linux exactly as
``wrap.py`` does, and the row records spawn-to-exit wall time, user and
system CPU, instructions, cycles and task clock. Conditions run one
after another, so drift of the host between them is confounded with the
condition; two passes, the second in reverse condition order, balance a
linear drift, and the report compares the passes.

Usage::

    python noise.py --work DIR --out DIR --pairs 800
"""

import argparse
import csv
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import numpy as np
import wrap

HERE: Final = Path(__file__).resolve().parent
TIMEOUT_SECONDS: Final = 60
"""Longest one execution may take before the recorder gives up."""
COMMANDS: Final[dict[str, list[str]]] = {
    "python": ["python3", "bench.py"],
    "native": ["./bench"],
}
COLUMNS: Final = (
    "workload", "order", "pin", "idx", "arm", "wall_s", "user_s", "sys_s",
    "instructions", "cycles", "task_clock_ms",
)  # fmt: skip


def order_for(seed: int, pairs: int, kind: str) -> list[tuple[str, int]]:
    """Return the run list ``(arm, pair index)`` for a condition.

    Parameters
    ----------
    seed : int
        Order seed.
    pairs : int
        Pairs, even.
    kind : {"interleaved", "blocked"}
        The condition's order.

    Returns
    -------
    list of tuple
        ``2 * pairs`` entries. Interleaved: pair ``2j`` and ``2j + 1``
        run in opposite orders, a coin per pair of pairs
        (``perfproof.bench.runner.round_orders``). Blocked: all ``A``
        then all ``B``, pair ``i`` being the ``i``-th of each.
    """
    if kind == "blocked":
        return [("A", i) for i in range(pairs)] + [
            ("B", i) for i in range(pairs)
        ]
    raw = np.random.PCG64(seed).random_raw((pairs + 1) // 2)
    bits = (raw >> np.uint64(63)).tolist()
    runs: list[tuple[str, int]] = []
    for i in range(pairs):
        first = int(bits[i // 2]) ^ (i % 2)
        pair = ("A", "B") if first == 0 else ("B", "A")
        runs += [(arm, i) for arm in pair]
    return runs


def _alarm(signum: int, frame: object) -> None:
    msg = f"an execution exceeded {TIMEOUT_SECONDS} s"
    raise TimeoutError(msg)


def execute(cmd: list[str], cwd: Path, pin: str | None) -> list[object]:
    """Run one execution of ``cmd`` in ``cwd`` and measure it."""
    perf_file = f"/dev/shm/golden-noise-{os.getpid()}"  # noqa: S108
    launch = [
        "perf", "stat", "-x,", "-o", perf_file,
        "-e", wrap.PERF_EVENTS, "--", *cmd,
    ]  # fmt: skip
    if pin:
        launch = ["taskset", "-c", pin, *launch]
    os.chdir(cwd)
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(TIMEOUT_SECONDS)
    try:
        start, end, user, system = wrap.spawn(launch)
    finally:
        signal.alarm(0)
    counts = wrap.parse_perf(perf_file)
    Path(perf_file).unlink(missing_ok=True)
    return [
        f"{(end - start) / 1e9:.9f}", user, system,
        counts.get("instructions"), counts.get("cycles"),
        counts.get("task-clock"),
    ]  # fmt: skip


def prepare(workload: str, work: Path) -> dict[str, Path]:
    """Make the two identical trees of a workload; build if needed."""
    trees: dict[str, Path] = {}
    for arm in ("A", "B"):
        tree = work / "noise" / workload / arm
        if not tree.exists():
            tree.mkdir(parents=True)
            for src in (HERE / "targets" / workload).iterdir():
                shutil.copy(src, tree / src.name)
            if workload == "native":
                subprocess.run(
                    ["gcc", "-O2", "-o", "bench", "bench.c"],
                    cwd=tree,
                    check=True,
                    timeout=120,
                )
        trees[arm] = tree
    return trees


def main(argv: Sequence[str]) -> int:
    """Record every condition; return the exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pairs", type=int, default=800)
    parser.add_argument("--workloads", nargs="+", default=list(COMMANDS))
    args = parser.parse_args(argv)
    work = args.work.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    seed = 20261022
    conditions = [
        (kind, pin)
        for kind in ("interleaved", "blocked")
        for pin in (None, "0")
    ]
    for workload in args.workloads:
        trees = prepare(workload, work)
        for npass, ordered in enumerate((conditions, conditions[::-1])):
            for kind, pin in ordered:
                label = "pinned" if pin else "free"
                name = f"{workload}-{kind}-{label}-pass{npass + 1}"
                path = out / f"{name}.csv"
                if path.exists():
                    continue
                rows: list[list[object]] = []
                begun = time.monotonic()
                for arm, idx in order_for(seed + npass, args.pairs, kind):
                    rows.append(
                        [
                            workload,
                            kind,
                            pin or "",
                            idx,
                            arm,
                            *execute(COMMANDS[workload], trees[arm], pin),
                        ]
                    )
                with path.open("w", newline="") as sink:
                    writer = csv.writer(sink, lineterminator="\n")
                    writer.writerow(COLUMNS)
                    writer.writerows(rows)
                sys.stdout.write(
                    f"{name}: {len(rows)} runs in "
                    f"{time.monotonic() - begun:.0f}s\n"
                )
                sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
