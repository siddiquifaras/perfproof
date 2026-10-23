"""Controlled background CPU load for the loaded trace passes.

``burner.py STEADY BURSTY SEED`` starts ``STEADY`` processes that spin
at ``nice 0`` until killed, and ``BURSTY`` processes that alternate
spinning and sleeping, each phase 20 to 60 s long, drawn from a seed per
process. It prints one line per phase change of the bursty processes and
stops all of them on SIGTERM or SIGINT. It touches nothing else.
"""

from __future__ import annotations

import os
import random
import signal
import sys
import time
from datetime import UTC, datetime
from types import FrameType


def spin(seconds: float) -> None:
    """Busy-loop for ``seconds`` of wall time."""
    end = time.monotonic() + seconds
    x = 0
    while time.monotonic() < end:
        for _ in range(100_000):
            x += 1


def steady() -> None:
    """Spin forever."""
    while True:
        spin(3600.0)


def bursty(seed: int) -> None:
    """Alternate 20-60 s of spinning with 20-60 s of sleeping."""
    rng = random.Random(seed)  # noqa: S311
    on = True
    while True:
        length = rng.uniform(20.0, 60.0)
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        sys.stdout.write(
            f"{stamp} bursty {seed} {'on' if on else 'off'} {length:.0f}s\n"
        )
        sys.stdout.flush()
        if on:
            spin(length)
        else:
            time.sleep(length)
        on = not on


def main(argv: list[str]) -> int:
    """Start the load and wait for a termination signal."""
    n_steady, n_bursty, seed = (int(a) for a in argv[1:4])
    children: list[int] = []
    for _ in range(n_steady):
        pid = os.fork()
        if pid == 0:
            steady()
        children.append(pid)
    for i in range(n_bursty):
        pid = os.fork()
        if pid == 0:
            bursty(seed + i)
        children.append(pid)

    def stop(signum: int, frame: FrameType | None) -> None:
        for pid in children:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except OSError:
                continue
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    sys.stdout.write(f"burner pids {children}\n")
    sys.stdout.flush()
    while True:
        signal.pause()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
