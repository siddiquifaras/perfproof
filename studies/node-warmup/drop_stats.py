"""Derive the model's drop position and process spread from the curve.

The drop of a process is the first batch, from batch 8 on, whose time
is below 0.6 x the median of that process's batches 1 to 11; its
position is that batch's index x 500 iterations. The process level is
the median of the process's batches 14 to 59, and the spread is the
population standard deviation of the log of the 12 levels.
"""

import json
from pathlib import Path

import numpy as np
from _out import emit

D = Path(__file__).resolve().parents[2] / "tests/fixtures/node/warmup"
CURVE = np.array(
    [json.loads(x) for x in (D / "curve.jsonl").read_text().splitlines()]
)


def main() -> None:
    """Print the per-process drops and the two summary numbers."""
    drops = []
    for row in CURVE:
        cut = 0.6 * np.median(row[1:12])
        first = 8 + int(np.argmax(row[8:] < cut))
        drops.append(first * 500)
    emit("drop positions (iterations): " + ", ".join(map(str, drops)))
    emit(
        f"mean {np.mean(drops):.0f}, sample sd {np.std(drops, ddof=1):.0f}, "
        f"population sd {np.std(drops):.0f}"
    )
    post = CURVE[:, 14:]
    means = post.mean(axis=1)
    within = (post.var(axis=1, ddof=1)).mean() / post.shape[1]
    excess = max(means.var(ddof=1) - within, 0.0) ** 0.5 / means.mean()
    emit(f"process means sd/mean: {means.std(ddof=1) / means.mean():.3f}")
    emit(f"spread in excess of batch noise (J): {excess:.3f}")


if __name__ == "__main__":
    main()
