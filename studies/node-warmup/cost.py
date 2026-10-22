"""Cost of an execution of the Node warm-up rule, from the curve.

Each batch of 500 iterations (two requests each) took the value in
curve.jsonl in microseconds per iteration. Seconds for WARM iterations
discarded and MEASURE measured = sum over the batches they cover, per
process, then the mean over the 12 processes. Start-up (process and
module loading) is NOT included: it was not measured here.
"""

import json
from pathlib import Path

import numpy as np
from _out import emit

D = Path(__file__).resolve().parents[2] / "tests/fixtures/node/warmup"
CURVE = np.array(
    [json.loads(x) for x in (D / "curve.jsonl").read_text().splitlines()]
)
SEC = CURVE * 500 / 1e6  # seconds per batch
STEADY = CURVE[:, 40:].mean() * 1e-6


def seconds(lo: int, hi: int) -> float:
    """Return the mean over processes of the time of [lo, hi)."""
    total = 0.0
    for start in range(lo, hi, 500):
        b = start // 500
        total += SEC[:, b].mean() if b < 60 else 500 * STEADY
    return total


def stretched(lo: int, hi: int, f: float) -> float:
    """Return the time of [lo, hi) on a tree that settles f times later.

    The committed mean curve is stretched along the iteration axis; the
    first batch (start-up) is not.
    """
    mean = CURVE.mean(axis=0) * 1e-6 * 500
    total = 0.0
    for start in range(lo, hi, 500):
        b = start // 500
        i = 0 if b == 0 else max(1, int(b // f))
        total += mean[i] if i < 60 else 500 * STEADY
    return total


def main() -> None:
    """Print the cost of a few WARM values."""
    emit("batches of 500 iterations, 12 processes, mean seconds")
    emit("beyond the 30,000 recorded iterations the steady value is used")
    for warm in (24000, 44000, 88000, 132000):
        w = seconds(0, warm)
        m = seconds(warm, warm + 10000)
        emit(
            f"WARM {warm:6d}: warm-up {w:5.2f} s, "
            f"measured 10000: {m:5.2f} s, total {w + m:5.2f} s"
        )
    emit("a tree that settles f times later (stretched curve):")
    for f in (1.0, 2.0, 3.0):
        warm = int(44000 * f)
        w = stretched(0, warm, f) + stretched(warm, warm + 10000, f)
        emit(f"  f={f:.0f}, WARM {warm}: {w:5.2f} s per execution")
    unknown = seconds(0, 640000) + seconds(640000, 650000)
    emit(
        f"unknown path, WARM 640000: {unknown:5.1f} s per execution, "
        f"{60 * 2 * unknown:6.0f} s for 60 rounds (default max_seconds 1800)"
    )
    short = seconds(0, 160000) + seconds(160000, 170000)
    emit(
        f"unknown path, WARM 160000: {short:5.1f} s per execution, "
        f"{60 * 2 * short:6.0f} s for 60 rounds"
    )
    emit("recording one process (no measured window), seconds:")
    for batches in (320, 640, 1280):
        n = batches * 500
        sec = seconds(0, n)
        emit(f"  {batches:5d} batches ({n:7d} iterations): {sec:6.1f} s")
    emit(f"steady {STEADY * 1e6:.1f} us/iteration (batches 40-59)")


if __name__ == "__main__":
    main()
