"""Model: how large a margin on the drop keeps both arms settled.

Uses node_ab_model's execution model (built from the committed curve in
tests/fixtures/node/warmup) and production run_looks. The rule under
test (docs/method.md "Warm-up (Node)"): measure the drop of BOTH trees,
WARM = margin x the later drop, MEASURE = 10,000 iterations. An arm's
drop is at f x 5,667 iterations (f = 1 is the committed Hono curve).
Assumes the drop is estimated without error, and a sharp drop like the
committed one; a slow tail is in estimate_model.py.

Usage: python margin_model.py R seed jsd rounds K
"""

import sys

import node_ab_model as m
import numpy as np
from _out import emit


def main() -> None:
    """Print false KEEP and REJECT rates per margin and drop pair."""
    runs, seed, jsd = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])
    rounds, looks = int(sys.argv[4]), int(sys.argv[5])
    rng = np.random.default_rng(seed)
    emit(
        f"seed {seed}, R = {runs} per row, {rounds} rounds, K = {looks}, "
        f"JSD {jsd}, MEASURE {m.NB * m.BATCH}; "
        "WARM = margin x 5667 x max(f_base, f_cand)"
    )
    for margin in (2, 3, 4):
        emit(f"== margin {margin}x")
        pairs = ((1, 1), (1, 3), (3, 1), (3, 3), (2, 2), (1, 2), (2, 1))
        for fb, fc in pairs:
            warm = int(margin * 5667 * max(fb, fc))
            for s in (1.00, 1.03, 1.06):
                m.scenario(
                    rng,
                    runs,
                    rounds,
                    looks,
                    s,
                    float(fb),
                    float(fc),
                    f"m={margin} f_base={fb} f_cand={fc} WARM={warm}",
                    jsd,
                    warm,
                )


if __name__ == "__main__":
    main()
