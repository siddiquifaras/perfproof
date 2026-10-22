"""WARM of a lone tree that settles f times later, at margin 8.

``WARM`` is 8 x 500 x N for a sharp-drop tree whose drop is at f x
5,667 iterations, over trees with N known. Used to say where the
measured window falls against the humps of ``mixed_pair.py``.

Usage: python sharp_warm.py R seed margin noise[,noise ...]
"""

import sys

import model as m
import numpy as np
from _out import emit


def main() -> None:
    """Print percentiles of WARM per f and noise."""
    runs, seed, margin = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])
    noises = [float(x) for x in sys.argv[4].split(",")]
    emit(f"seed {seed}, R = {runs} trees per row, margin {margin:g}")
    emit("noise f | known | WARM 5th, median, 95th percentile")
    for noise in noises:
        rng = np.random.default_rng([seed, round(1000 * noise)])
        for f in (1.0, 2.0, 3.0):
            arm = m.Arm(m.Pools(noise), f=f)
            n, known, _ = m.settle(rng, runs, arm, "scaled")
            w = margin * m.BATCH * n[known]
            p = np.percentile(w, [5, 50, 95])
            emit(
                f"{noise:4.2f} {f:.0f} | {100 * known.mean():5.1f}% | "
                f"{p[0]:.0f}, {p[1]:.0f}, {p[2]:.0f}"
            )


if __name__ == "__main__":
    main()
