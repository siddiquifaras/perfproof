"""Round-3 audit model of the Java warm-up rule (synthetic clock).

Not JVM data. Written by the stats auditor and kept as written, so the
committed outputs match the committed code (hence the lint exemptions
above). The stop rule of ``docs/method.md`` "Warm-up (Java)" as the
workloads implement it (check from iteration ``floor``, last 10 against
the 10 before within ``tol``, cap 600 with the median of the last 20
reported, exit 0), plus two alternatives, run through production
``run_looks(calibrated=True)``. Execution:
``t_k = T J (1 + a exp(-k/tau)) LN(0, sigma_it)``, ``J = LN(0, 0.05)``.
Experiment, seed and comparisons per row come from argv.

Commands that produced the committed outputs::

    uv run python studies/java-warmup/audit_model.py G7 462910583 4000 \
        > studies/java-warmup/audit-out-G7.txt
    uv run python studies/java-warmup/audit_model.py G8 462910583 4000 \
        > studies/java-warmup/audit-out-G8.txt

G7 holds the amplitude rows and G8 the noise rows, both at a true ratio
of 1.03.
"""

import sys

import numpy as np
from scipy.stats import beta

from perfproof.schema import LookSchedule
from perfproof.stats.sequential import run_looks

BLOCK, MEAS, ROUNDS, SIG_JVM, CAP = 10, 20, 60, 0.05, 600
MIN_EFFECT = 0.03


def emit(text):
    """Write one line and flush it."""
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def umed(x):
    """Median as the programs take it: ``sorted[len // 2]``."""
    return np.sort(x, axis=-1)[..., x.shape[-1] // 2]


def rule_fixed(t, floor, tol=0.01, cap=CAP):
    """Floor, then last 10 against the 10 before within ``tol``."""
    reps = t.shape[0]
    n_stop = np.full(reps, -1)
    for n in range(max(floor, 2 * BLOCK), cap + 1):
        last, prior = (
            umed(t[:, n - BLOCK : n]),
            umed(t[:, n - 2 * BLOCK : n - BLOCK]),
        )
        n_stop[(np.abs(last - prior) < tol * last) & (n_stop < 0)] = n
        if (n_stop >= 0).all():
            break
    return n_stop


def rule_longlag(t, block=20, lag=100, tol=0.01, cap=CAP):
    """Alternative: a long-lag drift check.

    The last ``block`` iterations and the ``block`` that ended ``lag``
    iterations earlier, within ``tol``.
    """
    reps = t.shape[0]
    n_stop = np.full(reps, -1)
    for n in range(lag + block, cap + 1):
        last = umed(t[:, n - block : n])
        early = umed(t[:, n - lag - block : n - lag])
        n_stop[(np.abs(last - early) < tol * last) & (n_stop < 0)] = n
        if (n_stop >= 0).all():
            break
    return n_stop


def rule_combo(
    t, floor=200, tol=0.01, block=20, lag=100, tol_lag=0.01, cap=CAP
):
    """Floor AND the 10-vs-10 drift check AND the long-lag check."""
    reps = t.shape[0]
    n_stop = np.full(reps, -1)
    for n in range(max(floor, lag + block, 2 * BLOCK), cap + 1):
        last, prior = (
            umed(t[:, n - BLOCK : n]),
            umed(t[:, n - 2 * BLOCK : n - BLOCK]),
        )
        l20 = umed(t[:, n - block : n])
        e20 = umed(t[:, n - lag - block : n - lag])
        ok = (np.abs(last - prior) < tol * last) & (
            np.abs(l20 - e20) < tol_lag * l20
        )
        n_stop[ok & (n_stop < 0)] = n
        if (n_stop >= 0).all():
            break
    return n_stop


def executions(rng, reps, level, a, tau, sig_it, rule):
    """Return values, iterations used and the capped mask."""
    k = np.arange(CAP + MEAS)
    t = (1.0 + a * np.exp(-k / tau))[None, :] * np.exp(
        sig_it * rng.standard_normal((reps, CAP + MEAS))
    )
    n_stop = rule(t)
    stable = n_stop >= 0
    start = np.where(stable, n_stop, CAP - MEAS)
    w = np.take_along_axis(
        t, start[:, None] + np.arange(MEAS)[None, :], axis=1
    )
    value = np.sort(w, axis=1)[:, MEAS // 2]
    jvm = np.exp(SIG_JVM * rng.standard_normal(reps))
    n_eff = np.where(stable, n_stop, CAP)
    return level * jvm * value, n_eff, ~stable


def scenario(rng, runs, ratio, base, cand, sig_it, rule, label):
    """Run ``runs`` comparisons and print one row."""
    b = np.empty((runs, ROUNDS))
    c = np.empty((runs, ROUNDS))
    nb = np.empty((runs, ROUNDS))
    nc = np.empty((runs, ROUNDS))
    capd = 0.0
    ch = 0
    for lo in range(0, runs, 100):
        hi = min(runs, lo + 100)
        n = (hi - lo) * ROUNDS
        vb, sb, cb = executions(rng, n, 1.0, *base, sig_it, rule)
        vc, sc, cc = executions(rng, n, 1.0 / ratio, *cand, sig_it, rule)
        b[lo:hi] = vb.reshape(hi - lo, ROUNDS)
        c[lo:hi] = vc.reshape(hi - lo, ROUNDS)
        nb[lo:hi] = sb.reshape(hi - lo, ROUNDS)
        nc[lo:hi] = sc.reshape(hi - lo, ROUNDS)
        capd += (cb.mean() + cc.mean()) / 2
        ch += 1
    first = np.zeros((runs, ROUNDS), dtype=bool)
    flip = rng.random((runs, ROUNDS // 2)) < 0.5
    first[:, 0::2] = flip
    first[:, 1::2] = ~flip
    res = run_looks(
        b,
        c,
        direction="lower_is_better",
        schedule=LookSchedule(n0=10, k=10, max_looks=6),
        mode="one_host",
        min_effect=MIN_EFFECT,
        calibrated=True,
        resolution=None,
        exhausted=np.ones(runs, dtype=bool),
        candidate_first=first,
    )
    keep = int((res.outcome == "KEEP").sum())
    rej = int((res.outcome == "REJECT").sum())
    errs, kind = (
        (keep, "fKEEP") if ratio <= 1 + MIN_EFFECT + 1e-12 else (rej, "fREJ ")
    )
    ub = beta.ppf(0.95, errs + 1, runs - errs) if errs < runs else 1.0
    bias = float(np.exp(np.mean(np.log(b) - np.log(c))) / ratio - 1)
    # the skill's cross-check: do the arms' reported counts differ?
    mb, mc = np.median(nb, axis=1), np.median(nc, axis=1)
    diff10 = float(np.mean(np.abs(mb - mc) >= 10))
    diff1 = float(np.mean(mb != mc))
    emit(
        f"{label:<44} S={ratio:4.2f} {kind} {errs:4d}/{runs}"
        f" = {100 * errs / runs:5.1f}%"
        f" (CP95 up {100 * ub:5.1f}%)"
        f" | bias {100 * bias:+5.1f}%"
        f" | mean n b/c {nb.mean():5.0f}/{nc.mean():5.0f}"
        f" | arms' counts differ >=1: {100 * diff1:4.1f}%"
        f" >=10: {100 * diff10:4.1f}%"
        f" | capped {100 * capd / ch:4.1f}%",
    )


def main():
    """Run the experiment named in argv."""
    exp, seed = sys.argv[1], int(sys.argv[2])
    runs = int(sys.argv[3]) if len(sys.argv) > 3 else 2000
    rng = np.random.default_rng(seed)

    def fixed(f):
        return lambda t: rule_fixed(t, f)

    emit(
        f"experiment {exp}, seed {seed}, R={runs}, 60 rounds,"
        " run_looks(calibrated=True)"
    )

    def fast(a):
        return (a, 3.0)

    if exp == "G1":  # slower settling, floor 200, a = 0.15, noise 2%
        for tau in (60, 90, 120, 160, 200, 300):
            scenario(
                rng,
                runs,
                1.00,
                (0.15, tau),
                fast(0.15),
                0.02,
                fixed(200),
                f"floor200 a.15 base tau {tau}, cand tau 3",
            )
            scenario(
                rng,
                runs,
                1.06,
                fast(0.15),
                (0.15, tau),
                0.02,
                fixed(200),
                f"floor200 a.15 base tau 3, cand tau {tau}",
            )
    elif exp == "G2":  # larger amplitude
        for a in (0.5, 1.0):
            for tau in (30, 60, 90):
                scenario(
                    rng,
                    runs,
                    1.00,
                    (a, tau),
                    fast(a),
                    0.02,
                    fixed(200),
                    f"floor200 a{a} base tau {tau}, cand tau 3",
                )
    elif exp == "G3":  # noisier iterations
        for sig in (0.05, 0.10, 0.20):
            for tau in (60, 120):
                scenario(
                    rng,
                    runs,
                    1.00,
                    (0.15, tau),
                    fast(0.15),
                    sig,
                    fixed(200),
                    f"floor200 sig{sig} base tau {tau}, cand 3",
                )
                scenario(
                    rng,
                    runs,
                    1.06,
                    fast(0.15),
                    (0.15, tau),
                    sig,
                    fixed(200),
                    f"floor200 sig{sig} base 3, cand tau {tau}",
                )
    elif exp == "E5":  # rule comparison: cost and false KEEP
        rules = {
            "fixed200": fixed(200),
            "fixed120": fixed(120),
            "longlag20/100": rule_longlag,
        }
        for sig in (0.02, 0.10, 0.20):
            for tau in (3, 20, 60, 120, 200):
                for name, rl in rules.items():
                    scenario(
                        rng,
                        runs,
                        1.00,
                        (0.15, tau),
                        fast(0.15),
                        sig,
                        rl,
                        f"{name} sig{sig} base tau {tau}, cand 3",
                    )
    elif (
        exp == "G3b"
    ):  # the iteration CVs of 25-130% recorded in the committed manifests
        for sig in (0.4, 0.8, 1.2):
            for tau in (60, 120):
                scenario(
                    rng,
                    runs,
                    1.00,
                    (0.15, tau),
                    fast(0.15),
                    sig,
                    fixed(200),
                    f"floor200 sig{sig} base tau {tau}, cand 3",
                )
                scenario(
                    rng,
                    runs,
                    1.06,
                    fast(0.15),
                    (0.15, tau),
                    sig,
                    fixed(200),
                    f"floor200 sig{sig} base 3, cand tau {tau}",
                )
    elif exp == "G4":  # the bar: floor 200 against min_effect 1% and 10%
        global MIN_EFFECT
        for me, taus in (
            (0.01, (30, 60, 90, 120)),
            (0.10, (90, 120, 160, 200)),
        ):
            MIN_EFFECT = me
            for tau in taus:
                scenario(
                    rng,
                    runs,
                    1 + me,
                    (0.15, tau),
                    fast(0.15),
                    0.02,
                    fixed(200),
                    f"min_effect {me}: base tau {tau}, cand 3, S at the bar",
                )
        MIN_EFFECT = 0.03
    elif exp == "G5":
        # the acceptance rule's cells nearest the default bar: S=1.03
        # (false KEEP) and S=1.04 (false REJECT)
        for tau in (60,):
            scenario(
                rng,
                runs,
                1.03,
                (0.15, tau),
                (0.15, tau),
                0.02,
                fixed(200),
                f"control both tau {tau} (no bias), floor200",
            )
        for tau in (30, 60, 75, 90, 120):
            scenario(
                rng,
                runs,
                1.03,
                (0.15, tau),
                fast(0.15),
                0.02,
                fixed(200),
                f"floor200 base tau {tau}, cand 3",
            )
            scenario(
                rng,
                runs,
                1.04,
                fast(0.15),
                (0.15, tau),
                0.02,
                fixed(200),
                f"floor200 base 3, cand tau {tau}",
            )
    elif (
        exp == "G6"
    ):  # near the bar, how far does a higher floor push the covered tau
        for fl in (200, 300, 400):
            for tau in (60, 90, 120, 160):
                scenario(
                    rng,
                    runs,
                    1.03,
                    (0.15, tau),
                    fast(0.15),
                    0.02,
                    fixed(fl),
                    f"floor{fl} base tau {tau}, cand 3",
                )
    elif exp == "G7":
        # amplitude dependence on the bar: predicted bias
        # a*exp(-210/tau) at 0.26%, 0.7%, 1.5%
        for a, taus in ((0.5, (40, 49, 60)), (1.0, (34, 42, 50))):
            for tau in taus:
                scenario(
                    rng,
                    runs,
                    1.03,
                    (a, tau),
                    (a, 3.0),
                    0.02,
                    fixed(200),
                    f"floor200 a{a} base tau {tau}, cand 3"
                    f" (pred bias {100 * a * np.exp(-210 / tau):.2f}%)",
                )
    elif exp == "G8":
        # the near-bar cells under noisier iterations (the manifests'
        # tails show CV 3% to 129%)
        for sig in (0.10, 0.30):
            scenario(
                rng,
                runs,
                1.03,
                (0.15, 3.0),
                (0.15, 3.0),
                sig,
                fixed(200),
                f"control both tau 3 sigma {sig}",
            )
            for tau in (60, 75, 90, 120):
                scenario(
                    rng,
                    runs,
                    1.03,
                    (0.15, tau),
                    fast(0.15),
                    sig,
                    fixed(200),
                    f"floor200 sigma {sig} base tau {tau}, cand 3",
                )
    elif (
        exp == "E6"
    ):  # floor 200 vs floor 200 + long-lag condition vs flat floor 300
        rules = {
            "fixed200": fixed(200),
            "fixed300": fixed(300),
            "combo200+lag": rule_combo,
        }
        for sig in (0.02, 0.10, 0.20):
            for tau in (3, 120, 200, 300):
                for name, rl in rules.items():
                    scenario(
                        rng,
                        runs,
                        1.00,
                        (0.15, tau),
                        fast(0.15),
                        sig,
                        rl,
                        f"{name} sig{sig} base tau {tau}, cand 3",
                    )
    else:
        raise SystemExit("unknown experiment")


if __name__ == "__main__":
    main()
