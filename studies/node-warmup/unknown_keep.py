"""Round 9 of PREREGISTRATION.md: unknown pairs may KEEP.

The rule: when a tree's warm-up is unknown, ``WARM`` is the longest
recording and the comparison proceeds, so perfproof's verdict stands.
The model already ran every pair that way (``model.warm_of``); this
reports it. Per noise and outlier setting, over the sharp and
exponential pairs of ``rule_sweep.claimed`` at margin 8 with the
``robust`` estimator:

* ``UNKNOWN`` lines: the errors among the pairs that ended unknown,
  pooled over the grid, per kind (false KEEP at S = 1.00 and 1.03,
  false REJECT at 1.06) with the CP95 upper bound of the pool;
* ``END-TO-END`` lines: all pairs combined, pooled, and the worst
  grid row's CP95 (the figure the 2.5% limit applies to).

``warm`` is ``WARM`` in iterations for a pair with an unknown tree.

Usage: python unknown_keep.py R seed warm noise [noise ...]
"""

import sys

import model as m
import numpy as np
from _out import emit
from rule_sweep import claimed

SETTINGS: tuple[tuple[float, float] | None, ...] = (
    None,
    (0.02, 10.0),
    (0.02, 30.0),
)


def label(ol: tuple[float, float] | None) -> str:
    """Return a short label of an outlier setting."""
    return "none" if ol is None else f"q={100 * ol[0]:g}% x{ol[1]:g}"


def main() -> None:
    """Run each setting at each noise; print pooled and worst rates."""
    runs, seed, warm = (int(x) for x in sys.argv[1:4])
    noises = [float(x) for x in sys.argv[4:]]
    m.UNKNOWN_WARM = warm
    emit(
        f"seed {seed}, R = {runs} per row, tolerance robust, margin 8, "
        f"WARM {warm} when a tree is unknown, "
        f"{m.PROCESSES} processes per tree"
    )
    for noise in noises:
        pl = m.Pools(noise)
        for ol in SETTINGS:
            rng = np.random.default_rng(
                [
                    seed,
                    round(1000 * noise),
                    round(1000 * (ol or (0, 0))[0]),
                    int((ol or (0, 0))[1]),
                ]
            )
            # key: (kind, s) -> [unknown errors, unknown pairs, errors,
            # pairs, worst row CP95 over all pairs]
            tot: dict[tuple[str, float], list[float]] = {}
            for _, tag, a, b in claimed(pl, ol):
                rows, _, _ = m.scenario(rng, tag, a, b, runs, (8.0,), "robust")
                for row in rows:
                    n_unk = round(runs * (1 - row.known))
                    cell = tot.setdefault(
                        (row.kind, row.s), [0.0, 0.0, 0.0, 0.0, 0.0]
                    )
                    cell[0] += row.errors - row.known_errors
                    cell[1] += n_unk
                    cell[2] += row.errors
                    cell[3] += row.runs
                    cell[4] = max(cell[4], row.ub)
            for (kind, s), c in sorted(tot.items()):
                ue, un, e, n, worst = (
                    int(c[0]),
                    int(c[1]),
                    int(c[2]),
                    int(c[3]),
                    c[4],
                )
                urate = (
                    f"{100 * ue / un:5.2f}% "
                    f"(CP95 {100 * m.cp95(ue, un):5.2f}%)"
                    if un
                    else "n/a"
                )
                emit(
                    f"UNKNOWN noise {noise:g} outliers {label(ol)} false "
                    f"{kind} S={s:4.2f}: {ue}/{un} = {urate} | "
                    f"unknown pairs {100 * un / n:.1f}% of {n}"
                )
                emit(
                    f"END-TO-END noise {noise:g} outliers {label(ol)} false "
                    f"{kind} S={s:4.2f}: pooled {e}/{n} = "
                    f"{100 * e / n:5.2f}% (CP95 {100 * m.cp95(e, n):5.2f}%) "
                    f"| worst row CP95 {100 * worst:5.2f}%"
                )


if __name__ == "__main__":
    main()
