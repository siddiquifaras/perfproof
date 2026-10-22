"""Power of the old advice to compare the halves of a window.

The advice was "compare the first and second half of the window once by
hand when a verdict is surprising" (docs/method.md "Warm-up (Node)",
before it was dropped). Uses the same execution model as
node_ab_model.py. For an arm whose drop falls at f x 5,667 iterations:

* bias = E[window mean] / E[window mean with no transient] - 1;
* alarm = P(first-half mean / second-half mean > T) for ONE execution,
  and for a pooled check over m executions (z-test on the mean log
  ratio).

numpy only.
"""

import sys

import numpy as np
from _out import emit
from node_ab_model import POST, PRE, executions

seed = int(sys.argv[1])
rng = np.random.default_rng(seed)
N = 200000
JSD = 0.042


def stats(f):
    """Return the reported values and log half ratios for drop f."""
    val, v = executions(rng, N, 1.0, f, JSD)
    h1 = v[:, :10].mean(axis=1)
    h2 = v[:, 10:].mean(axis=1)
    return val, np.log(h1 / h2)


def main() -> None:
    """Print one row per drop position."""
    emit(
        f"seed {seed}; {N} simulated executions per row; JSD {JSD}; WARM 12000"
    )
    emit(
        f"mean batch values: pre-drop {PRE.mean():.2f} us, "
        f"post-drop {POST.mean():.2f} us"
    )
    base_val, base_lr = stats(1.0)
    steady_mean = base_val.mean()
    emit(
        "\nf     drop at   bias of window mean | single execution: "
        "P(h1/h2 > 1.10 / 1.20 / 1.30) | pooled over m=12 executions: "
        "P(mean log ratio > 2 SE)"
    )
    for f in (1.0, 1.5, 1.8, 2.0, 2.1, 2.2, 2.4, 2.6, 3.0):
        val, lr = stats(f)
        bias = val.mean() / steady_mean - 1
        p = [(np.exp(lr) > t).mean() for t in (1.10, 1.20, 1.30)]
        # Pooled: groups of 12 executions, one-sided z-test, own sd.
        g = lr[: (N // 12) * 12].reshape(-1, 12)
        z = g.mean(axis=1) / (g.std(axis=1, ddof=1) / np.sqrt(12))
        pp = (z > 2.0).mean()
        emit(
            f"{f:3.1f}  {int(f * 5667):6d}   {100 * bias:+6.1f}%"
            f"             |  {100 * p[0]:5.1f}% / {100 * p[1]:5.1f}% / "
            f"{100 * p[2]:5.1f}%  |  {100 * pp:5.1f}%"
        )
    emit(
        "\nnull (f=1.0) sd of log(h1/h2) for one execution: "
        f"{base_lr.std():.3f} (committed data at W=12000: 0.189)"
    )


if __name__ == "__main__":
    main()
