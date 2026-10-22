"""Exponential tails between and beyond the sweep's grid, at margin 8.

``rule_sweep.py`` runs a in 0.5 and 0.75 and tau of 2,000, 5,000,
10,000, 20,000 and 40,000. This runs those again with a finer grid
between them, a ridge at tau 2,500 to 4,000, and amplitudes below the
claimed range, at noise 0.5 to 2.0, and prints the cost of the claimed
tails: WARM and the execution time at its median and 95th percentile
(committed-curve cost model, start-up excluded).

Sets: ``claimed`` (the sweep's grid), ``fine`` (a in 0.5, 0.6, 0.75 and
tau between and beyond it), ``lowa`` (a in 0.3 and 0.4, outside the
claimed range). Each cell is false KEEP at S = 1.00 and 1.03 and false
REJECT at 1.06. ``SUMMARY`` lines count cells whose CP95 upper bound is
over 2.5% and give the worst.

Usage: python exp_grid.py R seed margin noise[,noise ...] [mode]
"""

import sys

import model as m
import numpy as np
from _out import emit

CLAIMED = [
    (a, tau) for a in (0.5, 0.75) for tau in (2000, 5000, 10000, 20000, 40000)
]
FINE = [
    (a, tau)
    for a in (0.5, 0.6, 0.75)
    for tau in (*range(2500, 4501, 500), 5500, 6000, 7000, 8000, 15000, 30000)
]
LOWA = [(a, tau) for a in (0.3, 0.4) for tau in (2000, 5000, 10000, 20000)]


def main() -> None:
    """Run the three sets at each noise; print cells and summaries."""
    runs, seed, margin = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])
    noises = [float(x) for x in sys.argv[4].split(",")]
    mode = sys.argv[5] if len(sys.argv) > 5 else "scaled"
    emit(
        f"seed {seed}, R = {runs} per row, margin {margin:g}, tolerance {mode}"
    )
    for noise in noises:
        rng = np.random.default_rng([seed, round(1000 * noise)])
        pl = m.Pools(noise)
        emit(f"== noise {noise:g}")
        for name, grid in (
            ("claimed", CLAIMED),
            ("fine", FINE),
            ("lowa", LOWA),
        ):
            over = 0
            worst = 0.0
            for a, tau in grid:
                x = m.Arm(pl, exp=(a, float(tau)))
                y = m.Arm(pl, exp=(a, 300.0))
                n_x, k_x, _ = m.settle(rng, runs, x, mode)
                n_y, k_y, _ = m.settle(rng, runs, y, mode)
                warm, known = m.warm_of(n_x, k_x, n_y, k_y, margin)
                secs = [
                    m.seconds_of_batches(0, int(w) // m.BATCH + m.NB)
                    for w in np.percentile(warm, [50, 95])
                ]
                cells = []
                for s, base, cand in (
                    (1.00, x, y),
                    (1.03, x, y),
                    (1.06, y, x),
                ):
                    kind = "KEEP" if s <= m.BAR else "REJECT"
                    oc = m.outcomes(rng, warm, s, base, cand)
                    e = int((oc == kind).sum())
                    ub = m.cp95(e, runs)
                    over += ub > m.LIMIT
                    worst = max(worst, ub)
                    cells.append(
                        f"S={s:.2f} {kind[0]} {e:4d}/{runs} "
                        f"{100 * e / runs:5.2f}% ({100 * ub:5.2f})"
                        f"{'*' if ub > m.LIMIT else ' '}"
                    )
                emit(
                    f"{name:<7} exp a={a} tau={tau:<6d} known "
                    f"{100 * known.mean():5.1f}% WARM median "
                    f"{np.percentile(warm, 50):7.0f} ({secs[0]:5.2f} s) "
                    f"p95 {np.percentile(warm, 95):7.0f} ({secs[1]:5.2f} s)"
                    f" | " + " | ".join(cells)
                )
            emit(
                f"SUMMARY noise {noise:g} {name}: {len(grid)} pairs, "
                f"{3 * len(grid)} cells, {over} over 2.5% CP95, worst CP95 "
                f"{100 * worst:.2f}%"
            )


if __name__ == "__main__":
    main()
