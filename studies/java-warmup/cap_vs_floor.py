"""Round-3 audit model: share of executions that hit the 600 cap.

Not JVM data. Written by the stats auditor and kept as written (hence
the lint exemptions above). Flat noisy iteration series
``t = LN(0, sigma)``; the committed manifests' tails show a CV of 25 to
129% for iterations of 1.2 to 16 ms. Rule as in the workloads: check
from iteration ``floor``, last 10 against the 10 before within 1%, cap
600.

Command that produced the committed output::

    uv run python studies/java-warmup/cap_vs_floor.py 462910583 \
        > studies/java-warmup/audit-out-cap-vs-floor.txt
"""

import sys

import numpy as np

seed = int(sys.argv[1])
rng = np.random.default_rng(seed)
N, CAP = 20000, 600


def umed(x):
    """Median as the programs take it: ``sorted[len // 2]``."""
    return np.sort(x, axis=-1)[..., x.shape[-1] // 2]


sys.stdout.write(
    f"seed {seed}, {N} executions per cell, flat series t = LN(0, sigma)"
    + "\n"
)
sys.stdout.write(
    f"{'sigma':>6} {'CV~%':>6} | "
    + " ".join(f"floor {f:>3}" for f in (200, 300, 400, 500))
    + "\n"
)
for sigma in (0.3, 0.6, 0.8, 1.0, 1.2):
    t = np.exp(sigma * rng.standard_normal((N, CAP)))
    cv = np.sqrt(np.exp(sigma**2) - 1) * 100
    row = []
    for floor in (200, 300, 400, 500):
        stopped = np.zeros(N, dtype=bool)
        for n in range(floor, CAP + 1):
            last, prior = umed(t[:, n - 10 : n]), umed(t[:, n - 20 : n - 10])
            stopped |= np.abs(last - prior) < 0.01 * last
            if stopped.all():
                break
        row.append(100 * (1 - stopped.mean()))
    sys.stdout.write(
        f"{sigma:6.1f} {cv:6.0f} | "
        + " ".join(f"{r:9.1f}%" for r in row)
        + "\n"
    )
