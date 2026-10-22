"""Heavy-tailed batches against the scaled and the robust estimator.

Part 1, the estimator alone, sharp pair arm (f = 1), recordings of 320
batches at noise 0.25: no outlier, one batch 30 times the usual in one
process, one such batch in each of the 12 processes inside the tail
windows, and random outliers (each batch times m with probability q,
not the first batch). Prints how often the tail windows agree, the
median ``tol``, the median ``N`` and the share with ``N`` of 1 batch or
less.

Part 2, end to end at margin 8, noise 1.0: the sharp pairs (3, 1) and
(1, 1) and the exponential tail a = 0.5, tau = 5,000 against tau 300,
with outliers in the recordings and the executions of both trees, for
both estimators; false KEEP at S = 1.00 and 1.03, false REJECT at 1.06.

Usage: python outlier_probe.py R seed
"""

import sys

import model as m
import numpy as np
from _out import emit

MODES = ("scaled", "robust")


def report(label: str, v: m.Array) -> None:
    """Print the estimators' view of recordings ``v`` (c, 12, 320)."""
    for mode in MODES:
        pp = m.windows(v, mode)
        n, agree = m.estimate_n(pp, mode)
        tol = m.tolerance(pp, mode)
        emit(
            f"  {label:<40} {mode:<7} agree {100 * agree.mean():5.1f}% | "
            f"median tol {100 * np.median(tol):5.2f}% | median N "
            f"{np.median(n[agree]) if agree.any() else float('nan'):5.1f} "
            f"| N <= 1: {100 * np.mean(agree & (n <= 1)):5.1f}%"
        )


def pairs_of(
    pl: m.Pools, ol: tuple[float, float] | None
) -> tuple[tuple[str, m.Arm, m.Arm], ...]:
    """Return the three pairs of part 2 with outliers ``ol``."""
    return (
        (
            "sharp (3,1)",
            m.Arm(pl, f=3.0, outlier=ol),
            m.Arm(pl, f=1.0, outlier=ol),
        ),
        (
            "sharp (1,1)",
            m.Arm(pl, f=1.0, outlier=ol),
            m.Arm(pl, f=1.0, outlier=ol),
        ),
        (
            "exp a=0.5 tau=5000",
            m.Arm(pl, exp=(0.5, 5000.0), outlier=ol),
            m.Arm(pl, exp=(0.5, 300.0), outlier=ol),
        ),
    )


def main() -> None:
    """Run both parts and print them."""
    runs, seed = int(sys.argv[1]), int(sys.argv[2])
    emit(f"seed {seed}; part 1 uses {min(runs, 400)} recordings per row")
    emit("== 1. the estimator alone, noise 0.25, sharp f = 1, R = 320")
    rng = np.random.default_rng([seed, 1])
    arm = m.Arm(m.Pools(0.25))
    v = arm.record(rng, min(runs, 400), 320)
    report("no outlier", v)
    w = v.copy()
    w[:, 0, 305] *= 30
    report("one x30 batch, process 0, batch 305", w)
    w = v.copy()
    for p in range(m.PROCESSES):
        w[:, p, 260 + (p * 5) % 60] *= 30
    report("one x30 batch per process, batches 260-315", w)
    for q, mult in ((0.005, 10.0), (0.01, 30.0), (0.02, 30.0)):
        a = m.Arm(m.Pools(0.25), outlier=(q, mult))
        report(f"random q={100 * q:g}% x{mult:g}", a.record(rng, 400, 320))
    emit("== 2. end to end, margin 8, noise 1.0, 60 rounds, run_looks")
    pl = m.Pools(1.0)
    for mode in MODES:
        for q, mult in ((0.0, 1.0), (0.005, 10.0), (0.02, 10.0), (0.01, 30.0)):
            ol = None if q == 0 else (q, mult)

            pairs = pairs_of(pl, ol)
            rng = np.random.default_rng([seed, 2, round(100 * q), int(mult)])
            for tag, a, b in pairs:
                rows, known, _ = m.scenario(
                    rng,
                    f"{tag} q={100 * q:g}% x{mult:g}",
                    a,
                    b,
                    runs,
                    (8.0,),
                    mode,
                )
                emit(
                    f"-- {mode}: {tag}, q={100 * q:g}% x{mult:g}, N known "
                    f"in {100 * known:.1f}% of pairs"
                )
                for row in rows:
                    emit(row.line())


if __name__ == "__main__":
    main()
