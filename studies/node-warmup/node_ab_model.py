"""Model: what the look procedure decides under the Node rule.

Synthetic, built from the committed Hono curve; not Node data for any
other workload. It runs perfproof's `run_looks` (calibrated=True, batch
means, min_effect 0.03) when both arms use the rule of docs/method.md
"Warm-up (Node)" (a fixed WARM of iterations discarded, a fixed
MEASURE of 10,000 reported as one mean) and the arms differ only in
WHERE their one-time drop falls.

One execution is one fresh process:

* the drop happens at k0 ~ N(f * 5667, f * 749) iterations (mean and sd
  of the 12 committed processes; f scales the arm's settling point);
* the 20 batches of 500 iterations of the window [WARM, WARM + MEASURE)
  are drawn iid from the committed pools: batches 1..11 (before the
  drop, 132 values, mean 36.9 us) and batches 14..59 (after it, 552
  values, mean 21.2 us); a batch that straddles k0 is the proportional
  mix;
* reported value = arm level * J * mean of the 20 batches, J lognormal
  (sd JSD) = the process-level spread (data: 0.042; JSD 0.07 matches
  the reported-value spreads of 8.8% to 11.5% in the committed runs).

Assumptions NOT in the data: the plateau before the drop keeps the same
level wherever the drop falls; settling follows iterations (not
seconds).
"""

import json
import sys
from pathlib import Path

import numpy as np
from _out import emit
from scipy.stats import beta  # type: ignore[import-untyped]

from perfproof.schema import LookSchedule
from perfproof.stats.sequential import run_looks

H = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "node"
    / "warmup"
)
CURVE = np.array(
    [json.loads(row) for row in (H / "curve.jsonl").read_text().splitlines()]
)
PRE = CURVE[:, 1:12].ravel()
POST = CURVE[:, 14:].ravel()
K0_MEAN, K0_SD = 5667.0, 749.0
WARM, BATCH, NB = 12000, 500, 20
BAR = 1.03


def executions(rng, n, level, f, jsd, warm=WARM):
    """Draw n executions: reported values and their 20 batch values."""
    k0 = np.maximum(rng.normal(f * K0_MEAN, f * K0_SD, size=n), 1500.0)
    starts = warm + BATCH * np.arange(NB)
    phi = np.clip((k0[:, None] - starts[None, :]) / BATCH, 0.0, 1.0)
    pre = rng.choice(PRE, size=(n, NB))
    post = rng.choice(POST, size=(n, NB))
    v = phi * pre + (1.0 - phi) * post
    j = np.exp(jsd * rng.standard_normal(n))
    return level * j * v.mean(axis=1), v


def scenario(rng, runs, rounds, looks, s_true, fb, fc, label, jsd, warm=WARM):
    """Run `runs` comparisons of one scenario and print one row."""
    b = np.empty((runs, rounds))
    c = np.empty((runs, rounds))
    for lo in range(0, runs, 200):
        hi = min(runs, lo + 200)
        vb, _ = executions(rng, (hi - lo) * rounds, 1.0, fb, jsd, warm)
        vc, _ = executions(
            rng, (hi - lo) * rounds, 1.0 / s_true, fc, jsd, warm
        )
        b[lo:hi] = vb.reshape(hi - lo, rounds)
        c[lo:hi] = vc.reshape(hi - lo, rounds)
    first = np.zeros((runs, rounds), dtype=bool)
    flip = rng.random((runs, rounds // 2)) < 0.5
    first[:, 0::2] = flip
    first[:, 1::2] = ~flip
    res = run_looks(
        b,
        c,
        direction="lower_is_better",
        schedule=LookSchedule(n0=10, k=10, max_looks=looks),
        mode="one_host",
        min_effect=0.03,
        calibrated=True,
        resolution=None,
        exhausted=np.ones(runs, dtype=bool),
        candidate_first=first,
    )
    out = res.outcome
    keep, rej = int((out == "KEEP").sum()), int((out == "REJECT").sum())
    inc = runs - keep - rej
    if s_true <= BAR:
        errs, kind = keep, "false KEEP  "
    else:
        errs, kind = rej, "false REJECT"
    ub = beta.ppf(0.95, errs + 1, runs - errs) if errs < runs else 1.0
    bias = float(np.exp(np.mean(np.log(b) - np.log(c))) / s_true - 1)
    emit(
        f"{label:<52} S={s_true:4.2f} | KEEP {keep:5d} REJECT {rej:5d} "
        f"INCONC {inc:5d} "
        f"| {kind} {errs:5d}/{runs} = {100 * errs / runs:5.1f}% "
        f"(CP95 upper {100 * ub:5.1f}%) "
        f"| bias of geo-mean ratio {100 * bias:+6.1f}%"
    )


def main() -> None:
    """Print the control and the late-settling scenarios."""
    runs = int(sys.argv[1])
    seed = int(sys.argv[2])
    jsd = float(sys.argv[3])
    rounds, looks = int(sys.argv[4]), int(sys.argv[5])
    rng = np.random.default_rng(seed)
    emit(
        f"seed {seed}, R = {runs} comparisons per row, {rounds} rounds, "
        f"K = {looks}, JSD {jsd}, WARM {WARM}, MEASURE {NB * BATCH}; "
        "production run_looks(calibrated=True)"
    )

    def sc(*a, **k):
        """Run one scenario with this run's settings."""
        return scenario(rng, runs, rounds, looks, *a, jsd=jsd, **k)

    sc(1.00, 1.0, 1.0, "control: both arms drop at ~5.7k (f 1, 1)")
    sc(1.03, 1.0, 1.0, "control, ON THE BAR (f 1, 1)")
    sc(1.06, 1.0, 1.0, "control, truly 6% faster (f 1, 1)")
    sc(1.10, 1.0, 1.0, "control, truly 10% faster (f 1, 1)")
    emit(
        "-- candidate settles later than the baseline (counts chosen on "
        "the baseline, as the doc says); true gain 10%"
    )
    for f in (1.5, 1.8, 2.0, 2.1, 2.2, 2.5, 3.0):
        sc(
            1.10,
            1.0,
            f,
            f"candidate f={f} (drop at ~{int(f * 5667)}), truly 10% faster",
        )
    emit("-- same, true gain 6%")
    for f in (2.0, 2.2, 2.5):
        sc(
            1.06,
            1.0,
            f,
            f"candidate f={f} (drop at ~{int(f * 5667)}), truly 6% faster",
        )
    sc(1.00, 1.0, 3.0, "candidate f=3, no real change (REJECT is correct)")
    emit(
        "-- baseline settles later than WARM allows (counts NOT chosen "
        "on it): no real change"
    )
    for f in (1.5, 1.8, 2.0, 2.1, 2.2, 2.5, 3.0):
        sc(
            1.00,
            f,
            1.0,
            f"baseline f={f} (drop at ~{int(f * 5667)}), no real change",
        )
    emit("-- same, ON THE BAR (true ratio 1.03)")
    for f in (1.8, 2.0, 2.1, 2.2, 2.5, 3.0):
        sc(
            1.03,
            f,
            1.0,
            f"baseline f={f} (drop at ~{int(f * 5667)}), ON THE BAR",
        )


if __name__ == "__main__":
    main()
