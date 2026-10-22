"""The tail rule applied to the committed 30,000-iteration recording.

The tail check needs three disjoint 20-batch windows (30,000
iterations) after the drop, and the committed recording has 60 batches
in all, so the rule cannot pass on it: the first of the three windows
contains the drop. The rule's answer on this curve is therefore
"unknown" at any tolerance below the spread printed here.
"""

import json
from pathlib import Path

import numpy as np
from _out import emit

D = Path(__file__).resolve().parents[2] / "tests/fixtures/node/warmup"
CURVE = np.array(
    [json.loads(x) for x in (D / "curve.jsonl").read_text().splitlines()]
)
NB = 20


def main() -> None:
    """Print the window means and whether the tail agrees."""
    cs = np.concatenate([np.zeros((12, 1)), np.cumsum(CURVE, axis=1)], axis=1)
    m = ((cs[:, NB:] - cs[:, :-NB]) / NB).mean(axis=0)
    s = len(m)
    emit(f"{CURVE.shape[1]} batches, {s} windows of {NB} batches")
    tails = m[[s - 1, s - 1 - NB, s - 1 - 2 * NB]]
    emit(
        "tail windows start at batches "
        f"{s - 1}, {s - 1 - NB}, {s - 1 - 2 * NB}: "
        + ", ".join(f"{x:.1f}" for x in tails)
        + " us"
    )
    spread = tails.max() / tails.min() - 1
    emit(
        f"spread {100 * spread:.1f}%: the tail windows agree only at a "
        "tolerance of at least that"
    )


if __name__ == "__main__":
    main()
