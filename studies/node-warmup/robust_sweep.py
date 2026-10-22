"""Pass rule of PREREGISTRATION.md, round 6: pairs with outliers.

The sharp pairs and exponential tails of ``rule_sweep.py``, at margin 8,
for one estimator mode, with no outliers and with outliers: each batch
multiplied by m with probability q, in the recordings and the
executions of both trees, for q of 0.5%, 1%, 2% and m of 10, 30.

``SUMMARY`` lines give, per noise and outlier setting, the worst CP95
upper bound over all rows of each family (every comparison). Rows are
false KEEP at S = 1.00 and 1.03 and false REJECT at 1.06.

Usage: python robust_sweep.py R seed mode noise [noise ...]
"""

import sys

import model as m
import numpy as np
from _out import emit
from rule_sweep import claimed

OUTLIERS: tuple[tuple[float, float] | None, ...] = (
    None,
    *((q, mult) for q in (0.005, 0.01, 0.02) for mult in (10.0, 30.0)),
)


def label(ol: tuple[float, float] | None) -> str:
    """Return a short label of an outlier setting."""
    return "none" if ol is None else f"q={100 * ol[0]:g}% x{ol[1]:g}"


def main() -> None:
    """Run each outlier setting at each noise; print rows, summaries."""
    runs, seed, mode = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    noises = [float(x) for x in sys.argv[4:]]
    emit(
        f"seed {seed}, R = {runs} per row, tolerance {mode}, margin 8, "
        f"{m.PROCESSES} processes per tree"
    )
    for noise in noises:
        pl = m.Pools(noise)
        emit(f"== noise {noise:g}")
        for ol in OUTLIERS:
            rng = np.random.default_rng(
                [
                    seed,
                    round(1000 * noise),
                    round(1000 * (ol or (0, 0))[0]),
                    int((ol or (0, 0))[1]),
                ]
            )
            worst: dict[tuple[str, str], tuple[float, float]] = {}
            unknown = []
            for fam, tag, a, b in claimed(pl, ol):
                rows, known, _ = m.scenario(rng, tag, a, b, runs, (8.0,), mode)
                unknown.append(1 - known)
                emit(
                    f"-- {label(ol)} {fam}: {tag} | N known in "
                    f"{100 * known:.1f}% of pairs"
                )
                for row in rows:
                    emit(row.line())
                    key = (fam, row.kind)
                    old = worst.get(key, (0.0, 0.0))
                    worst[key] = (
                        max(old[0], row.ub),
                        max(old[1], row.ub_known),
                    )
            for (fam, kind), (ub, ubk) in sorted(worst.items()):
                emit(
                    f"SUMMARY noise {noise:g} outliers {label(ol)} {fam} "
                    f"{kind} all {100 * ub:.2f}% known {100 * ubk:.2f}%"
                )
            emit(
                f"UNKNOWN noise {noise:g} outliers {label(ol)} mean pair "
                f"unknown {100 * float(np.mean(unknown)):.2f}%"
            )


if __name__ == "__main__":
    main()
