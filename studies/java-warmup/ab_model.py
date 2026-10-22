"""Model: what the Java warm-up rule does to a comparison of two arms.

Synthetic clock, not JVM data. Each execution is a fresh JVM whose
iteration times are ``T * J * (1 + a exp(-k / tau)) * LN(0, 0.02)``,
with ``J = LN(0, 0.05)`` one JVM's level (an SD of about 5% matches the
15-19% max/min spread of 10 JVMs in ``docs/method.md``). The execution
is run through the stop rule of "Warm-up (Java)" and reports its value;
the reported values of 60 alternating rounds go through the production
look procedure (``perfproof.stats.sequential.run_looks``, calibrated,
batch means, ``n0=10, k=10, K=6``).

The two arms differ only in how fast they settle (``tau``), so a
comparison with ``S`` = 1.00 is a no-change comparison and any KEEP is
a false one; with ``S`` = 1.06 a REJECT is a false one.

Run::

    uv run python studies/java-warmup/ab_model.py <R> <seed> <rule> \
        [<floor> [<rows>]]

``R`` is the number of comparisons per row, ``seed`` seeds the random
numbers, and ``rule`` is ``old`` (5% drift tolerance, an exit at the
cap) or ``new`` (1% tolerance; at the cap the median of the last 20
iterations is reported). ``floor`` is the fewest warm-up iterations
before the rule may stop (default 20). ``rows`` is ``key`` (three of
the eight default rows) or ``bar`` (a true ratio of 1.03, on the bar:
a control with both arms fast, then the baseline settling with tau 60,
75, 90 and 120 against a candidate with tau 3), or omitted for all
eight default rows.
"""

import sys

import numpy as np
from numpy.typing import NDArray
from scipy.stats import beta  # pyright: ignore[reportMissingTypeStubs]

from perfproof.schema import LookSchedule
from perfproof.stats.sequential import run_looks

BLOCK = 10
MEASURED = 20
ROUNDS = 60
SIGMA_ITERATION = 0.02
SIGMA_JVM = 0.05
_config = {"columns": 140, "floor": 20}
"""Iterations generated per execution (the model's cap on warm-up),
and the fewest warm-up iterations before the stop rule may fire."""
BAR = 1.03

Floats = NDArray[np.float64]
Ints = NDArray[np.int64]


def upper_median(x: Floats) -> Floats:
    """Median as the programs take it: ``sorted[len // 2]``."""
    return np.sort(x, axis=-1)[..., x.shape[-1] // 2]


def executions(
    rng: np.random.Generator,
    reps: int,
    level: float,
    transient: tuple[float, float],
    tolerance: float,
    *,
    cap_reports: bool,
) -> tuple[Floats, Ints, float]:
    """Return the reported values of ``reps`` fresh JVMs, one stop rule.

    Parameters
    ----------
    rng : numpy.random.Generator
        Source of randomness.
    reps : int
        Number of executions.
    level : float
        Asymptotic iteration time ``T`` of the arm.
    transient : tuple of float
        ``(a, tau)`` of the settling curve.
    tolerance : float
        Relative drift between two blocks of 10 iterations below which
        the execution counts as stable.
    cap_reports : bool
        When ``True`` an execution that has not settled by the last
        generated iteration reports the median of its last 20
        iterations; when ``False`` it fails (the old rule), which
        raises here.

    Returns
    -------
    tuple
        Values, iterations until stable (the number generated for a
        capped one), and the share of executions that hit the cap.
    """
    a, tau = transient
    k = np.arange(_config["columns"])
    t = (1.0 + a * np.exp(-k / tau))[None, :] * np.exp(
        SIGMA_ITERATION * rng.standard_normal((reps, _config["columns"]))
    )
    n_stop = np.full(reps, -1, dtype=np.int64)
    for n in range(max(2 * BLOCK, _config["floor"]), _config["columns"] + 1):
        last = upper_median(t[:, n - BLOCK : n])
        prior = upper_median(t[:, n - 2 * BLOCK : n - BLOCK])
        stable = np.abs(last - prior) < tolerance * last
        n_stop[stable & (n_stop < 0)] = n
    capped = n_stop < 0
    if capped.any() and not cap_reports:
        msg = "an execution did not settle"
        raise RuntimeError(msg)
    start = np.where(capped, _config["columns"] - MEASURED, n_stop)
    start = np.where(
        capped, start, np.minimum(start, _config["columns"] - MEASURED)
    )
    window = np.take_along_axis(
        t, start[:, None] + np.arange(MEASURED)[None, :], axis=1
    )
    value = np.sort(window, axis=1)[:, MEASURED // 2]
    jvm = np.exp(SIGMA_JVM * rng.standard_normal(reps))
    n_report = np.where(capped, _config["columns"], n_stop)
    return level * jvm * value, n_report, float(capped.mean())


def scenario(
    rng: np.random.Generator,
    comparisons: int,
    ratio: float,
    base: tuple[float, float],
    cand: tuple[float, float],
    label: str,
    tolerance: float,
    *,
    cap_reports: bool,
) -> None:
    """Run ``comparisons`` comparisons and print one table row."""
    b = np.empty((comparisons, ROUNDS))
    c = np.empty((comparisons, ROUNDS))
    stops_b: list[float] = []
    stops_c: list[float] = []
    capped = 0.0
    chunks = 0
    for lo in range(0, comparisons, 100):
        hi = min(comparisons, lo + 100)
        n = (hi - lo) * ROUNDS
        vb, sb, cb = executions(
            rng, n, 1.0, base, tolerance, cap_reports=cap_reports
        )
        vc, sc, cc = executions(
            rng, n, 1.0 / ratio, cand, tolerance, cap_reports=cap_reports
        )
        b[lo:hi] = vb.reshape(hi - lo, ROUNDS)
        c[lo:hi] = vc.reshape(hi - lo, ROUNDS)
        stops_b.append(float(np.median(sb)))
        stops_c.append(float(np.median(sc)))
        capped += (cb + cc) / 2
        chunks += 1
    first = np.zeros((comparisons, ROUNDS), dtype=bool)
    flip = rng.random((comparisons, ROUNDS // 2)) < 0.5
    first[:, 0::2] = flip
    first[:, 1::2] = ~flip
    result = run_looks(
        b,
        c,
        direction="lower_is_better",
        schedule=LookSchedule(n0=10, k=10, max_looks=6),
        mode="one_host",
        min_effect=0.03,
        calibrated=True,
        resolution=None,
        exhausted=np.ones(comparisons, dtype=bool),
        candidate_first=first,
    )
    keep = int((result.outcome == "KEEP").sum())
    reject = int((result.outcome == "REJECT").sum())
    errors, kind = (
        (keep, "false KEEP") if ratio <= BAR else (reject, "false REJECT")
    )
    upper = (
        float(beta.ppf(0.95, errors + 1, comparisons - errors))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        if errors < comparisons
        else 1.0
    )
    bias = float(np.exp(np.mean(np.log(b) - np.log(c))) / ratio - 1)
    sys.stdout.write(
        f"{label:<46} S={ratio:4.2f} | KEEP {keep:4d} REJECT {reject:4d}"
        f" INCONC {comparisons - keep - reject:4d} | {kind}:"
        f" {errors}/{comparisons} = {100 * errors / comparisons:5.1f}%"
        f" (CP95 upper {100 * upper:5.1f}%) | bias of geo-mean ratio"
        f" {100 * bias:+5.1f}% | median iterations b/c"
        f" {np.mean(stops_b):.0f}/{np.mean(stops_c):.0f}"
        f" | capped {100 * capped / chunks:4.1f}%\n"
    )


def main() -> None:
    """Print the table for one rule."""
    comparisons = int(sys.argv[1])
    seed = int(sys.argv[2])
    rule = sys.argv[3]
    if len(sys.argv) > 4:
        _config["floor"] = int(sys.argv[4])
        _config["columns"] = max(140, _config["floor"] + 120)
    which = sys.argv[5] if len(sys.argv) > 5 else "all"
    tolerance = {"old": 0.05, "new": 0.01}[rule]
    cap_reports = rule == "new"
    rng = np.random.default_rng(seed)
    none, fast, slow = (0.0, 1.0), (0.15, 3.0), (0.15, 60.0)
    sys.stdout.write(
        f"rule {rule}: tolerance {tolerance}, floor {_config['floor']},"
        f" seed {seed},"
        f" {comparisons} comparisons per row, {ROUNDS} rounds\n"
    )
    rows = [
        (1.00, none, none, "both settle at once"),
        (1.00, fast, fast, "both fast transient (tau 3)"),
        (1.00, slow, slow, "both slow transient (tau 60)"),
        (1.00, slow, fast, "baseline tau 60, candidate tau 3"),
        (1.00, fast, slow, "baseline tau 3, candidate tau 60"),
        (1.06, fast, fast, "both fast, candidate truly 6% faster"),
        (1.06, fast, slow, "baseline tau 3, candidate tau 60, 6% faster"),
        (1.06, slow, fast, "baseline tau 60, candidate tau 3, 6% faster"),
    ]
    if which == "key":
        rows = [rows[0], rows[3], rows[6]]
    elif which == "bar":
        rows = [(1.03, fast, fast, "control: both fast, ratio on the bar")]
        rows += [
            (
                1.03,
                (0.15, float(t)),
                fast,
                f"baseline tau {t}, candidate tau 3",
            )
            for t in (60, 75, 90, 120)
        ]
    for ratio, base, cand, label in rows:
        scenario(
            rng,
            comparisons,
            ratio,
            base,
            cand,
            label,
            tolerance,
            cap_reports=cap_reports,
        )


if __name__ == "__main__":
    main()
