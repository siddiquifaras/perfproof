"""Model of the Node warm-up rule: N per tree, WARM, then the compare.

The rule (see PREREGISTRATION.md and docs/method.md "Warm-up (Node)"):

* each tree is recorded, 12 processes of R batches of 500 iterations
  (R = 320, then 640, then 1,280 when the tail windows disagree);
* ``W(s)`` is the mean over processes of the mean of the 20 batches
  from batch ``s``; the tail windows start at R - 20, R - 40, R - 60,
  agree when the largest is within ``tol`` of the smallest, and their
  mean is the reference;
* ``N`` is the smallest ``s`` whose windows ``W(s)``, ``W(s + 20)`` and
  ``W(s + 40)`` are within ``tol`` of the reference;
* ``WARM`` = margin x the larger ``N`` of the two trees, in iterations;
  when a tail never agrees, ``WARM`` is the longest recording, 640,000.

There is no check beyond those three windows and no verdict from noisy
windows. Arms are synthetic, built from the committed Hono curve
(``tests/fixtures/node/warmup/curve.jsonl``): a sharp drop, or an
exponential tail, plus optional extra shapes (second plateau, drift,
hump) added to the steady value. Noise 1.0 is the committed curve;
smaller is quieter, larger is noisier.
"""

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from numpy.typing import NDArray
from scipy.stats import beta  # type: ignore[import-untyped]

from perfproof.schema import LookSchedule
from perfproof.stats.sequential import run_looks

Array = NDArray[np.float64]
Shape = Callable[[Array], Array]

H = Path(__file__).resolve().parents[2] / "tests/fixtures/node/warmup"
CURVE = np.array(
    [json.loads(row) for row in (H / "curve.jsonl").read_text().splitlines()]
)
PRE0, POST0, FIRST = CURVE[:, 1:12].ravel(), CURVE[:, 14:].ravel(), CURVE[:, 0]
K0_MEAN, K0_SD = 5667.0, 749.0
BATCH, NB, BAR, JSD = 500, 20, 1.03, 0.042
# Processes recorded per tree; round 8 of PREREGISTRATION.md varies it.
PROCESSES = int(os.environ.get("NODE_WARMUP_PROCESSES", "12"))
LENGTHS = (320, 640, 1280)
UNKNOWN_WARM = BATCH * LENGTHS[-1]
LIMIT = 0.025
BASE_TOL = 0.02
MAX_TOL = 0.04
ROBUST_CAPS = {
    "robust": MAX_TOL,
    "robust-cap15": 0.15,
    "robust-nocap": float("inf"),
}
SE_FACTOR = 3.0
MEAN_BATCH_US = CURVE.mean(axis=0)
STEADY_US = CURVE[:, 40:].mean()
EXECUTIONS = 122
ROUNDS = 60


class Pools:
    """Batch pools with the committed deviations scaled by ``scale``."""

    def __init__(self, scale: float) -> None:
        """Scale the committed batch deviations around their means."""
        self.pre = np.maximum(PRE0.mean() + scale * (PRE0 - PRE0.mean()), 1.0)
        self.post = np.maximum(
            POST0.mean() + scale * (POST0 - POST0.mean()), 1.0
        )
        self.noise = self.post / self.post.mean()


def plateau(p: float, k1: float) -> Shape:
    """Return an excess of ``p`` until iteration ``k1``."""
    return lambda k: p * (k < k1)


def drift(d: float, kd: float) -> Shape:
    """Return an excess falling linearly from ``d`` to 0 at ``kd``."""
    return lambda k: d * np.maximum(0.0, 1.0 - k / kd)


def hump(h: float, k0: float, k1: float) -> Shape:
    """Return an excess of ``h`` from iteration ``k0`` to ``k1``."""
    return lambda k: h * ((k >= k0) & (k < k1))


@dataclass
class Arm:
    """A tree with steady value 1 whose start-up drops at f x 5,667.

    Parameters
    ----------
    pools : Pools
        Batch pools at the noise scale of the machine.
    f : float
        Scale on the drop position (1 is the committed curve).
    sd : float
        Multiplier on the drop-position spread.
    shapes : list of (str, callable)
        Labelled excesses added to the steady value, as a function of
        the iteration at the middle of each batch.
    exp : tuple of float, optional
        ``(a, tau)``: an exponential tail ``1 + a exp(-k / tau)``
        instead of the sharp drop.
    jsd : float
        Spread of the process effect on the log scale.
    outlier : tuple of float, optional
        ``(q, m)``: each batch is multiplied by ``m`` with probability
        ``q``, independently, in recordings (not the first batch) and
        in executions.
    """

    pools: Pools
    f: float = 1.0
    sd: float = 1.0
    shapes: list[tuple[str, Shape]] = field(default_factory=list)
    exp: tuple[float, float] | None = None
    jsd: float = JSD
    outlier: tuple[float, float] | None = None

    def label(self) -> str:
        """Return a short description of the arm."""
        if self.exp:
            base = f"exp a={self.exp[0]} tau={self.exp[1]:.0f}"
        else:
            base = f"f={self.f:g}"
            if self.sd != 1.0:
                base += f" sd x{self.sd:g}"
        extra = "+".join(label for label, _ in self.shapes)
        return f"{base} {extra}".strip()

    def excess(self, k: Array) -> Array:
        """Return the multiplier from the extra shapes at k."""
        out = np.ones_like(k)
        for _, e in self.shapes:
            out = out + e(k)
        return out

    def outliers(
        self, rng: np.random.Generator, v: Array, skip_first: bool
    ) -> Array:
        """Multiply random batches by ``m``, as ``outlier`` says."""
        if self.outlier is None:
            return v
        q, mult = self.outlier
        hit = rng.random(v.shape) < q
        if skip_first:
            hit[..., 0] = False
        return np.where(hit, v * mult, v)

    def record(self, rng: np.random.Generator, c: int, nrec: int) -> Array:
        """Draw c recordings of 12 processes; shape (c, 12, nrec)."""
        pl = self.pools
        lo = BATCH * np.arange(nrec, dtype=float)
        shape = (c, PROCESSES, nrec)
        if self.exp:
            a, tau = self.exp
            ex = (
                a
                * tau
                / BATCH
                * (np.exp(-lo / tau) - np.exp(-(lo + BATCH) / tau))
            )
            v = (1.0 + ex)[None, None, :] * rng.choice(pl.noise, size=shape)
            v[..., 0] = 6.2 * rng.choice(pl.noise, size=(c, PROCESSES))
        else:
            k0 = np.maximum(
                rng.normal(
                    self.f * K0_MEAN,
                    self.sd * self.f * K0_SD,
                    size=(c, PROCESSES),
                ),
                1500.0,
            )
            phi = np.clip((k0[..., None] - lo) / BATCH, 0.0, 1.0)
            v = (
                phi * rng.choice(pl.pre, size=shape)
                + (1 - phi)
                * rng.choice(pl.post, size=shape)
                * self.excess(lo + BATCH / 2)[None, None, :]
            )
            v[..., 0] = rng.choice(FIRST, size=(c, PROCESSES))
        v = self.outliers(rng, v, skip_first=True)
        return (
            v
            * np.exp(self.jsd * rng.standard_normal((c, PROCESSES)))[..., None]
        )

    def execute(
        self, rng: np.random.Generator, warm: Array, level: float
    ) -> Array:
        """Draw one execution's value per element of ``warm``.

        The value is the mean of the 20 batches after ``warm``
        iterations, times ``level`` and a process effect.
        """
        pl = self.pools
        n = len(warm)
        lo = warm[:, None] + BATCH * np.arange(NB)[None, :]
        if self.exp:
            a, tau = self.exp
            ex = (
                a
                * tau
                / BATCH
                * (np.exp(-lo / tau) - np.exp(-(lo + BATCH) / tau))
            )
            v = (1.0 + ex) * rng.choice(pl.noise, size=lo.shape)
        else:
            k0 = np.maximum(
                rng.normal(self.f * K0_MEAN, self.sd * self.f * K0_SD, size=n),
                1500.0,
            )
            phi = np.clip((k0[:, None] - lo) / BATCH, 0.0, 1.0)
            v = phi * rng.choice(pl.pre, size=(n, NB)) + (
                1 - phi
            ) * rng.choice(pl.post, size=(n, NB)) * self.excess(lo + BATCH / 2)
        v = self.outliers(rng, v, skip_first=False)
        return (
            level * np.exp(self.jsd * rng.standard_normal(n)) * v.mean(axis=1)
        )


def window_means(v: Array) -> Array:
    """Return per-process 20-batch window means, (c, 12, windows)."""
    cs = np.concatenate(
        [np.zeros_like(v[..., :1]), np.cumsum(v, axis=2)], axis=2
    )
    return (cs[..., NB:] - cs[..., :-NB]) / NB


def window_medians(v: Array) -> Array:
    """Return per-process 20-batch window medians, (c, 12, windows)."""
    return np.median(sliding_window_view(v, NB, axis=2), axis=3)


def windows(v: Array, mode: str) -> Array:
    """Return the per-process window values the mode estimates from."""
    return window_medians(v) if mode in ROBUST_CAPS else window_means(v)


def tolerance(per_process: Array, mode: str) -> Array:
    """Return the tolerance per recording.

    ``fixed`` is 2%. ``scaled`` is the larger of 2% and 3 x se, the
    relative standard error of a tail window mean, from the spread over
    the 12 processes of each process's three tail windows divided by its
    own mean. ``robust`` takes the deviations of those ratios from
    their median over the processes, the median absolute deviation
    times 1.4826 over sqrt(12) as ``se``, and caps the tolerance at
    ``MAX_TOL``, 4%. ``robust-cap15`` caps it at 15% and
    ``robust-nocap`` does not cap it (``ROBUST_CAPS``).
    """
    c = per_process.shape[0]
    if mode == "fixed":
        return np.full(c, BASE_TOL)
    s = per_process.shape[2]
    x = per_process[:, :, [s - 1, s - 1 - NB, s - 1 - 2 * NB]]
    x = x / x.mean(axis=2, keepdims=True)
    if mode in ROBUST_CAPS:
        dev = np.abs(x - np.median(x, axis=1, keepdims=True))
        se = 1.4826 * np.median(dev, axis=(1, 2)) / np.sqrt(PROCESSES)
        return np.clip(SE_FACTOR * se, BASE_TOL, ROBUST_CAPS[mode])
    resid = x - x.mean(axis=1, keepdims=True)
    var = (resid**2).sum(axis=(1, 2)) / ((PROCESSES - 1) * 2)
    se = np.sqrt(var / PROCESSES)
    return np.maximum(BASE_TOL, SE_FACTOR * se)


def estimate_n(per_process: Array, mode: str) -> tuple[Array, Array]:
    """Return N (batches) and whether the tail windows agreed.

    ``N`` is only meaningful where the second array is true.
    """
    m = per_process.mean(axis=1)
    tol = tolerance(per_process, mode)
    s = m.shape[1]
    tails = m[:, [s - 1, s - 1 - NB, s - 1 - 2 * NB]]
    agree = tails.max(axis=1) <= (1 + tol) * tails.min(axis=1)
    ref = tails.mean(axis=1)
    ok = np.abs(m / ref[:, None] - 1) <= tol[:, None]
    three = ok[:, : s - 2 * NB] & ok[:, NB : s - NB] & ok[:, 2 * NB :]
    return np.argmax(three, axis=1), agree


def settle(
    rng: np.random.Generator, runs: int, arm: Arm, mode: str
) -> tuple[Array, NDArray[np.bool_], list[float]]:
    """Estimate N for ``runs`` recordings of ``arm`` with the retries.

    Returns N in batches, the known flags and the share known after
    each recording length.
    """
    n = np.full(runs, LENGTHS[-1], dtype=float)
    known = np.zeros(runs, dtype=bool)
    shares: list[float] = []
    for nrec in LENGTHS:
        todo = np.flatnonzero(~known)
        step = 20 if mode in ROBUST_CAPS else 100
        for lo in range(0, len(todo), step):
            idx = todo[lo : lo + step]
            got, agree = estimate_n(
                windows(arm.record(rng, len(idx), nrec), mode), mode
            )
            n[idx[agree]] = got[agree]
            known[idx[agree]] = True
        shares.append(float(known.mean()))
    return n, known, shares


def warm_of(
    n_a: Array,
    k_a: NDArray[np.bool_],
    n_b: Array,
    k_b: NDArray[np.bool_],
    margin: float,
) -> tuple[Array, NDArray[np.bool_]]:
    """Return WARM (iterations) per comparison and the known flags."""
    known = k_a & k_b
    longest = (
        margin
        * BATCH
        * np.maximum(np.where(k_a, n_a, 0.0), np.where(k_b, n_b, 0.0))
    )
    warm = np.where(known, longest, np.maximum(longest, UNKNOWN_WARM))
    return warm, known


def outcomes(
    rng: np.random.Generator,
    warm: Array,
    s: float,
    base: Arm,
    cand: Arm,
) -> NDArray[np.str_]:
    """Run a 60-round comparison per element of ``warm``.

    The candidate's true ratio is ``s`` (baseline over candidate).
    Production ``run_looks`` decides each comparison.
    """
    runs = len(warm)
    b = np.empty((runs, ROUNDS))
    c = np.empty((runs, ROUNDS))
    for lo in range(0, runs, 200):
        hi = min(runs, lo + 200)
        w = np.repeat(warm[lo:hi], ROUNDS).astype(float)
        b[lo:hi] = base.execute(rng, w, 1.0).reshape(hi - lo, ROUNDS)
        c[lo:hi] = cand.execute(rng, w, 1.0 / s).reshape(hi - lo, ROUNDS)
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
        min_effect=0.03,
        calibrated=True,
        resolution=None,
        exhausted=np.ones(runs, dtype=bool),
        candidate_first=first,
    )
    return np.asarray(res.outcome)


def cp95(errors: int, runs: int) -> float:
    """Return the 95% binomial upper bound on errors / runs."""
    if errors >= runs:
        return 1.0
    return float(beta.ppf(0.95, errors + 1, runs - errors))


@dataclass
class Row:
    """One cell: errors of one kind in one scenario at one margin."""

    tag: str
    margin: float
    s: float
    kind: str
    runs: int
    errors: int
    known_errors: int
    known: float

    @property
    def ub(self) -> float:
        """Upper bound over every comparison."""
        return cp95(self.errors, self.runs)

    @property
    def ub_known(self) -> float:
        """Upper bound over the comparisons where N was found."""
        return cp95(self.known_errors, self.runs)

    def line(self) -> str:
        """Return the cell as one line."""
        return (
            f"   {self.tag:<52} m={self.margin:g} S={self.s:4.2f} "
            f"false {self.kind:<6} all {self.errors:5d}/{self.runs} = "
            f"{100 * self.errors / self.runs:5.2f}% (CP95 "
            f"{100 * self.ub:5.2f}%) | known-N only {self.known_errors:5d} "
            f"= {100 * self.known_errors / self.runs:5.2f}% (CP95 "
            f"{100 * self.ub_known:5.2f}%)"
        )


def scenario(
    rng: np.random.Generator,
    tag: str,
    a: Arm,
    b: Arm,
    runs: int,
    margins: tuple[float, ...],
    mode: str,
) -> tuple[list[Row], float, float]:
    """Run tree A against tree B at every margin.

    A is the baseline at S = 1.00 and 1.03 (false KEEP) and the
    candidate at S = 1.06 (false REJECT). Returns the rows, the share
    of pairs with N known and the median WARM at the first margin.
    """
    n_a, k_a, _ = settle(rng, runs, a, mode)
    n_b, k_b, _ = settle(rng, runs, b, mode)
    rows: list[Row] = []
    shares = 0.0
    med = 0.0
    for margin in margins:
        warm, known = warm_of(n_a, k_a, n_b, k_b, margin)
        if margin == margins[0]:
            shares, med = float(known.mean()), float(np.median(warm))
        for s, base, cand in (
            (1.00, a, b),
            (1.03, a, b),
            (1.06, b, a),
        ):
            oc = outcomes(rng, warm, s, base, cand)
            hit = oc == ("KEEP" if s <= BAR else "REJECT")
            rows.append(
                Row(
                    tag,
                    margin,
                    s,
                    "KEEP" if s <= BAR else "REJECT",
                    runs,
                    int(hit.sum()),
                    int((hit & known).sum()),
                    float(known.mean()),
                )
            )
    return rows, shares, med


def seconds_of_batches(lo: int, hi: int) -> float:
    """Return the committed curve's seconds for batches [lo, hi)."""
    us = [
        MEAN_BATCH_US[b] if b < len(MEAN_BATCH_US) else STEADY_US
        for b in range(lo, hi)
    ]
    return float(np.sum(us)) * 1e-6 * BATCH
