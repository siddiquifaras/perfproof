"""Unknown rate and cost of the tail estimate, by tolerance and noise.

Choice A of PREREGISTRATION.md: on the sharp pair (1, 1), how often the
tail windows never agree, per pair of trees, by noise, with the cost of
choosing and of an execution at each margin (start-up not included).

Usage: python estimator_unknown.py R seed margins [mode ...]
"""

import sys

import model as m
import numpy as np
from _out import emit

NOISES = (0.1, 0.25, 0.5, 1.0, 1.5, 2.0)


def tree_cost(shares: list[float]) -> float:
    """Return seconds to record one tree, with the retries."""
    return m.PROCESSES * (
        m.seconds_of_batches(0, m.LENGTHS[0])
        + (1 - shares[0]) * m.seconds_of_batches(0, m.LENGTHS[1])
        + (1 - shares[1]) * m.seconds_of_batches(0, m.LENGTHS[2])
    )


def main() -> None:
    """Print one row per tolerance mode and noise."""
    runs, seed = int(sys.argv[1]), int(sys.argv[2])
    margins = tuple(float(x) for x in sys.argv[3].split(","))
    emit(
        f"seed {seed}, R = {runs} pairs per row, sharp pair (1, 1), "
        f"{m.PROCESSES} processes per tree"
    )
    emit(
        "mode   noise | median tolerance of a 320-batch recording | "
        "a tree's first recording agrees | pair unknown after retries | "
        "N median batches (5th, 95th), known trees | "
        "choosing both trees s | per margin: median WARM, mean "
        "execution s, choosing + 122 executions s"
    )
    for mode in sys.argv[4:] or ("fixed", "scaled"):
        for noise in NOISES:
            rng = np.random.default_rng([seed, round(100 * noise)])
            arm = m.Arm(m.Pools(noise))
            n_a, k_a, sh_a = m.settle(rng, runs, arm, mode)
            n_b, k_b, sh_b = m.settle(rng, runs, arm, mode)
            known = k_a & k_b
            tol = np.median(
                m.tolerance(
                    m.windows(arm.record(rng, 1000, m.LENGTHS[0]), mode), mode
                )
            )
            n = np.concatenate([n_a[k_a], n_b[k_b]])
            choose = tree_cost(sh_a) + tree_cost(sh_b)
            per = []
            for margin in margins:
                warm, _ = m.warm_of(n_a, k_a, n_b, k_b, margin)
                secs = np.array(
                    [
                        m.seconds_of_batches(0, int(w) // m.BATCH + m.NB)
                        for w in warm
                    ]
                )
                total = choose + m.EXECUTIONS * secs.mean()
                per.append(
                    f"m={margin:g}: {np.median(warm):.0f}, "
                    f"{secs.mean():.2f} s, {total:.0f} s"
                )
            emit(
                f"{mode:<6} {noise:5.2f} | {100 * tol:5.1f}% | "
                f"{100 * sh_a[0]:6.1f}% | "
                f"{100 * (1 - known.mean()):6.2f}% | "
                f"{np.median(n):.0f} ({np.percentile(n, 5):.0f}, "
                f"{np.percentile(n, 95):.0f}) | {choose:.0f} | "
                + " | ".join(per)
            )


if __name__ == "__main__":
    main()
