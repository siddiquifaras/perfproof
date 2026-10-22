"""Model: choosing WARM when N is estimated from noisy processes.

Synthetic, built from the committed Hono curve (sharp drop) and from
exponential tails 1 + a exp(-k/tau). Two rules for each tree's N, both
estimated from 12 processes, then WARM = 3 x 500 x the larger N of the
two trees, then 60 rounds through production `run_looks` (calibrated,
K=6, bar 3%):

* ``doublings`` (the rule of f318948, as the stats audit read it): the
  smallest n whose 20-batch windows starting at n, 2n and 4n agree
  within 5%; N is the first window within 5% of the window at n.
* ``tail`` (the rule of an earlier revision, 5%, with no cover and
  a fixed tolerance; the rule now in docs/method.md is model.py's):
  the last three disjoint 20-batch windows of the recording agree
  within 5%, their mean is the reference, and N is the smallest s
  whose window and the next two disjoint windows are all within 5% of
  it. When the tail does not agree the recording is redone with
  fresh processes at twice the length (320, 640, 1280 batches of 500
  iterations); if the longest still fails, N is unknown and WARM is
  the longest recording's end.

The stats audit's scripts were the starting point; the execution
model is node_ab_model.py's.

Usage: python estimate_model.py R seed
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
PRE, POST, FIRST = CURVE[:, 1:12].ravel(), CURVE[:, 14:].ravel(), CURVE[:, 0]
NOISE = POST / POST.mean()
K0_MEAN, K0_SD = 5667.0, 749.0
BATCH, NB, BAR, TOL, JSD = 500, 20, 1.03, 0.05, 0.042
MARGIN = 3
LENGTHS = (320, 640, 1280)
PROCESSES = 12


def sharp_curves(rng, c, f, nrec):
    """Draw c recordings of 12 processes dropping at f x 5,667."""
    k0 = np.maximum(
        rng.normal(f * K0_MEAN, f * K0_SD, size=(c, PROCESSES)), 1500.0
    )
    phi = np.clip((k0[..., None] - BATCH * np.arange(nrec)) / BATCH, 0.0, 1.0)
    shape = (c, PROCESSES, nrec)
    v = phi * rng.choice(PRE, size=shape) + (1 - phi) * rng.choice(
        POST, size=shape
    )
    v[..., 0] = rng.choice(FIRST, size=(c, PROCESSES))
    return v * np.exp(JSD * rng.standard_normal((c, PROCESSES)))[..., None]


def excess(a, tau, lo):
    """Return the expected excess of the batches starting at lo."""
    return a * tau / BATCH * (np.exp(-lo / tau) - np.exp(-(lo + BATCH) / tau))


def exp_curves(rng, c, a, tau, nrec):
    """Draw c recordings of 12 processes with an exponential tail."""
    ex = excess(a, tau, BATCH * np.arange(nrec))
    shape = (c, PROCESSES, nrec)
    v = (1.0 + ex)[None, None, :] * rng.choice(NOISE, size=shape)
    v[..., 0] = 6.2 * rng.choice(NOISE, size=(c, PROCESSES))
    return v * np.exp(JSD * rng.standard_normal((c, PROCESSES)))[..., None]


def window_means(v):
    """Return the mean over processes of each 20-batch window mean."""
    cs = np.concatenate(
        [np.zeros_like(v[..., :1]), np.cumsum(v, axis=2)], axis=2
    )
    return ((cs[..., NB:] - cs[..., :-NB]) / NB).mean(axis=1)


def n_doublings(m):
    """Return N (batches) and whether the doublings rule found one."""
    c, s = m.shape
    n = np.arange(1, (s - 1) // 4 + 1)
    a, b, d = m[:, n], m[:, 2 * n], m[:, 4 * n]
    ok = np.maximum(np.maximum(a, b), d) <= (1 + TOL) * np.minimum(
        np.minimum(a, b), d
    )
    first = np.argmax(ok, axis=1)
    nref = n[first]
    ref = m[np.arange(c), nref]
    within = (np.abs(m / ref[:, None] - 1) <= TOL) & (
        np.arange(s)[None, :] <= nref[:, None]
    )
    return np.argmax(within, axis=1), ok.any(axis=1) & within.any(axis=1)


def n_tail(m):
    """Return N (batches) and whether the tail rule found one."""
    s = m.shape[1]
    tails = m[:, [s - 1, s - 1 - NB, s - 1 - 2 * NB]]
    agree = tails.max(axis=1) <= (1 + TOL) * tails.min(axis=1)
    ref = tails.mean(axis=1)
    within = np.abs(m / ref[:, None] - 1) <= TOL
    # Stays: this window and the next two disjoint ones are all within.
    stays = within[:, : -2 * NB] & within[:, NB:-NB] & within[:, 2 * NB :]
    return np.argmax(stays, axis=1), agree & stays.any(axis=1)


def settle(rng, runs, make, rule, lengths):
    """Estimate N for `runs` trees; return N (batches) and found flags.

    ``make(rng, c, nrec)`` draws recordings. Each length in ``lengths``
    is tried in turn on the trees still unresolved, with fresh draws.
    Unresolved trees get N = the longest length.
    """
    n = np.full(runs, lengths[-1])
    done = np.zeros(runs, dtype=bool)
    for nrec in lengths:
        todo = np.flatnonzero(~done)
        for lo in range(0, len(todo), 100):
            idx = todo[lo : lo + 100]
            got, found = rule(window_means(make(rng, len(idx), nrec)))
            n[idx[found]] = got[found]
            done[idx[found]] = True
    return n, done


def run_ab(rng, warm, s, level_b, level_c, execution):
    """Run one comparison per element of ``warm``; return counts."""
    runs, rounds = len(warm), 60
    b = np.empty((runs, rounds))
    c = np.empty((runs, rounds))
    for lo in range(0, runs, 200):
        hi = min(runs, lo + 200)
        w = np.repeat(warm[lo:hi], rounds).astype(float)
        b[lo:hi] = execution(rng, w, level_b, True).reshape(hi - lo, rounds)
        c[lo:hi] = execution(rng, w, level_c / s, False).reshape(
            hi - lo, rounds
        )
    first = np.zeros((runs, rounds), dtype=bool)
    flip = rng.random((runs, rounds // 2)) < 0.5
    first[:, 0::2] = flip
    first[:, 1::2] = ~flip
    res = run_looks(
        b,
        c,
        direction="lower_is_better",
        schedule=LookSchedule(n0=10, k=10, max_looks=6),
        mode="one_host",
        min_effect=0.03,
        calibrated=True,
        resolution=None,
        exhausted=np.ones(runs, dtype=bool),
        candidate_first=first,
    )
    return int((res.outcome == "KEEP").sum()), int(
        (res.outcome == "REJECT").sum()
    )


def sharp_execution(fb, fc):
    """Return an execution drawer for arms dropping at fb and fc."""

    def draw(rng, warm, level, is_base):
        f = fb if is_base else fc
        n = len(warm)
        k0 = np.maximum(rng.normal(f * K0_MEAN, f * K0_SD, size=n), 1500.0)
        starts = warm[:, None] + BATCH * np.arange(NB)[None, :]
        phi = np.clip((k0[:, None] - starts) / BATCH, 0.0, 1.0)
        v = phi * rng.choice(PRE, size=(n, NB)) + (1 - phi) * rng.choice(
            POST, size=(n, NB)
        )
        return level * np.exp(JSD * rng.standard_normal(n)) * v.mean(axis=1)

    return draw


def exp_execution(a, tau_b, tau_c):
    """Return an execution drawer for exponential arms."""

    def draw(rng, warm, level, is_base):
        tau = tau_b if is_base else tau_c
        lo = warm[:, None] + BATCH * np.arange(NB)[None, :]
        v = (1.0 + excess(a, tau, lo)) * rng.choice(NOISE, size=lo.shape)
        return (
            level
            * np.exp(JSD * rng.standard_normal(len(warm)))
            * v.mean(axis=1)
        )

    return draw


def report(rng, warm, s, execution, tag):
    """Run the comparisons and print one row."""
    runs = len(warm)
    keep, rej = run_ab(rng, warm, s, 1.0, 1.0, execution)
    errs, kind = (keep, "false KEEP  ") if s <= BAR else (rej, "false REJECT")
    ub = beta.ppf(0.95, errs + 1, runs - errs) if errs < runs else 1.0
    emit(
        f"   {tag:<34} S={s:4.2f} | KEEP {keep:5d} REJECT {rej:5d} | "
        f"{kind} {errs:5d}/{runs} = {100 * errs / runs:5.2f}% "
        f"(CP95 upper {100 * ub:5.2f}%)"
    )


def warm_for(nb, db, nc, dc, lengths):
    """Return WARM per comparison and the share with N known."""
    known = db & dc
    warm = MARGIN * BATCH * np.maximum(nb, nc)
    return np.where(known, warm, BATCH * lengths[-1]), known.mean()


def sharp_section(rng, runs):
    """Print N and the A/B errors for the sharp drop, both rules."""
    emit("== N per tree, sharp drop, 12 processes (tail rule, retries)")
    for f in (1.0, 2.0, 3.0):
        d = f * K0_MEAN

        def make(r, c, nrec, f=f):
            return sharp_curves(r, c, f, nrec)

        first = [
            np.mean(n_tail(window_means(make(rng, 100, length)))[1])
            for length in LENGTHS
            for _ in range(10)
        ]
        n, done = settle(rng, runs, make, n_tail, LENGTHS)
        ni = BATCH * n[done]
        emit(
            f"   f={f:.0f}: tail agrees on one recording "
            f"{100 * np.mean(first):.1f}%; after retries "
            f"{100 * done.mean():.1f}%; N median {np.median(ni):.0f} "
            f"(5th {np.percentile(ni, 5):.0f}, 95th "
            f"{np.percentile(ni, 95):.0f}); N/drop median "
            f"{np.median(ni) / d:.2f}"
        )
    pairs = ((1, 1), (3, 1), (1, 3), (2, 1), (1, 2), (3, 3))
    for label, rule, lengths in (
        ("BEFORE: doublings rule, one recording of 320", n_doublings, (320,)),
        ("AFTER: tail rule, retries 320/640/1280", n_tail, LENGTHS),
    ):
        emit(f"\n== {label}; WARM = 3 x larger N")
        for fb, fc in pairs:
            nb, db = settle(
                rng,
                runs,
                lambda r, c, nrec, f=fb: sharp_curves(r, c, f, nrec),
                rule,
                lengths,
            )
            nc, dc = settle(
                rng,
                runs,
                lambda r, c, nrec, f=fc: sharp_curves(r, c, f, nrec),
                rule,
                lengths,
            )
            warm, known = warm_for(nb, db, nc, dc, lengths)
            d = K0_MEAN * max(fb, fc)
            emit(
                f"-- f_base={fb} f_cand={fc}: N known for "
                f"{100 * known:.1f}%; WARM/later drop median "
                f"{np.median(warm) / d:.2f}, 5th pct "
                f"{np.percentile(warm, 5) / d:.2f}"
            )
            ex = sharp_execution(float(fb), float(fc))
            for s in (1.00, 1.03, 1.06):
                report(rng, warm, s, ex, f"{label[:6]} ({fb},{fc})")


def exp_section(rng, runs):
    """Print the false KEEP on exponential tails, both rules."""
    for label, rule, lengths in (
        ("BEFORE: doublings rule, one recording of 800", n_doublings, (800,)),
        ("AFTER: tail rule, retries 320/640/1280", n_tail, LENGTHS),
    ):
        emit(f"\n== exponential tails, {label}; candidate tau 300")
        emit(
            "a     tau_b  | N known | N median | baseline excess at WARM "
            "(median, 95th) | false KEEP S=1.00 | S=1.03"
        )
        for a in (0.5, 0.75):
            for tau in (2000, 5000, 10000, 20000, 40000):
                nb, db = settle(
                    rng,
                    runs,
                    lambda r, c, nrec, t=float(tau), a=a: exp_curves(
                        r, c, a, t, nrec
                    ),
                    rule,
                    lengths,
                )
                nc, dc = settle(
                    rng,
                    runs,
                    lambda r, c, nrec, a=a: exp_curves(r, c, a, 300.0, nrec),
                    rule,
                    lengths,
                )
                warm, known = warm_for(nb, db, nc, dc, lengths)
                exc = excess(
                    a, float(tau), warm[:, None] + BATCH * np.arange(NB)
                )
                exc = exc.mean(axis=1)
                ex = exp_execution(a, float(tau), 300.0)
                kb, _ = run_ab(rng, warm, 1.00, 1.0, 1.0, ex)
                kc, _ = run_ab(rng, warm, 1.03, 1.0, 1.0, ex)
                ubb = beta.ppf(0.95, kb + 1, runs - kb)
                ubc = beta.ppf(0.95, kc + 1, runs - kc)
                emit(
                    f"{a:4.2f} {tau:6d} | {100 * known:5.1f}% | "
                    f"{np.median(BATCH * nb):8.0f} | "
                    f"{100 * np.median(exc):5.2f}%, "
                    f"{100 * np.percentile(exc, 95):5.2f}% | "
                    f"{kb:4d}/{runs} = {100 * kb / runs:5.1f}% "
                    f"(<= {100 * ubb:5.1f}%) | "
                    f"{kc:4d}/{runs} = {100 * kc / runs:5.1f}% "
                    f"(<= {100 * ubc:5.1f}%)"
                )


def main() -> None:
    """Run both sections."""
    runs, seed = int(sys.argv[1]), int(sys.argv[2])
    rng = np.random.default_rng(seed)
    emit(
        f"seed {seed}, R = {runs} per row, JSD {JSD}, 12 processes per "
        "tree, 60 rounds, K=6, margin 3"
    )
    sharp_section(rng, runs)
    exp_section(rng, runs)


if __name__ == "__main__":
    main()
