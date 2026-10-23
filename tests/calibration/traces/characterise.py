"""Characterise a raw A/A trace written by ``collect.py``.

Descriptive only: it measures the noise a real run has (spread, tails,
dependence, level shifts, modes, drift) and how often the T1.8
diagnostics exceed their thresholds on windows of it (raw exceedance,
not what the gate withholds: the gate's minimum look lengths are not
applied). It changes no procedure and draws no calibration conclusion.

Conventions. ``A`` is the baseline tree and ``B`` the candidate tree;
the log ratio of a pair is ``d = log(A / B)``. The lag-1
autocorrelation of ``d`` is perfproof's own diagnostic
(``perfproof.stats.sequential.lag1_autocorrelation``, order means
removed), and "robust sd" is 1.4826 times the median absolute
deviation, as in the gate. The warm-up pair is dropped.

Usage::

    uv run --with ruptures python \\
        tests/calibration/traces/characterise.py \\
        data/*.jsonl.gz --json out.json

Without ``ruptures`` a binary-segmentation CUSUM on the mean is used
and the output says so.
"""

from __future__ import annotations

import argparse
import gzip
import importlib
import itertools
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from numpy.typing import NDArray

from perfproof.stats.sequential import (
    OUTLIER_Z,
    lag1_autocorrelation,
    outlier_share,
)

FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]

MAD_TO_SD = 1.482602218505602
LAG1_GATE = 0.1
OUTLIER_GATE = 0.03
WINDOWS = (30, 60, 120)
ACF_LAGS = 20
BOOTSTRAP = 2000
BLOCK = 25
MIN_SEGMENT = 30
QUANTILES = (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 0.999)


@dataclass(frozen=True)
class Trace:
    """Per-pair arrays of one trace, warm-up pair removed.

    Every array has one entry per pair, in pair order. ``wall``,
    ``instr`` and ``cpu`` are ``(n, 2)``: column 0 arm A, column 1 arm
    B. Missing measurements are NaN.
    """

    header: dict[str, Any]
    wall: FloatArray
    instr: FloatArray
    cpu: FloatArray
    candidate_first: BoolArray
    load1: FloatArray
    speed_limit: FloatArray
    on_ac: FloatArray
    start_s: FloatArray
    steal: FloatArray
    all_ticks: FloatArray
    migrations: FloatArray
    per_run: dict[str, FloatArray]
    metrics: dict[str, FloatArray]


def _nan(value: object) -> float:
    return float("nan") if value is None else float(str(value))


def load_trace(path: Path) -> Trace:
    """Read a trace and its header.

    Parameters
    ----------
    path : pathlib.Path
        ``<name>.jsonl`` or ``<name>.jsonl.gz``; the header is
        ``<name>.header.json`` beside it.

    Returns
    -------
    Trace
        Pairs with both executions present, warm-up excluded.
    """
    stem = path.name.removesuffix(".gz").removesuffix(".jsonl")
    header_path = path.with_name(f"{stem}.header.json")
    header: dict[str, Any] = json.loads(header_path.read_text())
    opener = gzip.open if path.suffix == ".gz" else open
    pairs: dict[int, list[dict[str, Any]]] = {}
    with opener(path, "rt") as f:
        for line in f:
            row: dict[str, Any] = json.loads(line)
            if not row["warmup"]:
                pairs.setdefault(int(row["pair"]), []).append(row)
    keys = sorted(k for k, rows in pairs.items() if len(rows) == 2)
    n = len(keys)
    wall = np.full((n, 2), np.nan)
    instr = np.full((n, 2), np.nan)
    cpu = np.full((n, 2), np.nan)
    first = np.zeros(n, dtype=bool)
    load = np.full(n, np.nan)
    speed = np.full(n, np.nan)
    ac = np.full(n, np.nan)
    start = np.full(n, np.nan)
    steal = np.zeros(n)
    ticks = np.zeros(n)
    migr = np.full(n, np.nan)
    per_run: dict[str, FloatArray] = {}
    metrics: dict[str, FloatArray] = {}
    for i, k in enumerate(keys):
        for row in pairs[k]:
            col = 0 if row["arm"] == "A" else 1
            wall[i, col] = row["wall_s"]
            instr[i, col] = _nan(row["instructions"])
            cpu[i, col] = row["user_s"] + row["sys_s"]
            if row["pos"] == 0:
                first[i] = row["arm"] == "B"
                load[i] = _nan(row["load1"])
                start[i] = row["mono_ns"] / 1e9
            if row.get("steal_ticks") is not None:
                steal[i] += row["steal_ticks"]
                ticks[i] += row["all_cpu_ticks"]
                migr[i] = _nan(row.get("cpu_migrations"))
            for key in (
                "loadavg_start",
                "loadavg_end",
                "cpu_speed_limit",
                "watched_cpu_s",
            ):
                if row.get(key) is not None:
                    per_run.setdefault(key, np.full((n, 2), np.nan))[
                        i, col
                    ] = row[key]
            reported: dict[str, float] = row.get("metrics") or {}
            for key, val in reported.items():
                if key != "value":
                    metrics.setdefault(key, np.full((n, 2), np.nan))[
                        i, col
                    ] = val
            speed[i] = _nan(row["cpu_speed_limit"])
            source = row["power_source"]
            if source is not None:
                ac[i] = 1.0 if source == "AC Power" else 0.0
    return Trace(
        header,
        wall,
        instr,
        cpu,
        first,
        load,
        speed,
        ac,
        start - start[0],
        steal,
        ticks,
        migr,
        per_run,
        metrics,
    )


def robust_sd(x: FloatArray) -> float:
    """Return 1.4826 times the median absolute deviation of ``x``."""
    return float(MAD_TO_SD * np.median(np.abs(x - np.median(x))))


def order_centred(d: FloatArray, first: BoolArray) -> FloatArray:
    """Subtract from each ``d`` the mean of its own order."""
    out = d.copy()
    for mask in (first, ~first):
        if mask.any():
            out[mask] -= d[mask].mean()
    return out


def acf(x: FloatArray, lags: int) -> FloatArray:
    """Autocorrelation of ``x`` at lags 0..``lags``, mean removed."""
    x = x - x.mean()
    n = x.size
    size = 1 << (2 * n - 1).bit_length()
    f = np.fft.rfft(x, size)
    cov = np.fft.irfft(f * np.conj(f), size)[: lags + 1]
    return cov / cov[0] if cov[0] > 0 else np.full(lags + 1, np.nan)


def effective_sample_size(x: FloatArray) -> float:
    """Effective sample size of the mean of ``x``.

    ``n / (2 * sum - 1)`` where the sum runs over Geyer's initial
    positive sequence: pair sums ``rho_(2m) + rho_(2m+1)`` from lag 0,
    stopping at the first negative pair. Negative dependence gives more
    than ``n`` (AR(1) with phi = -0.5 gives about 2.9 n).

    When the estimated integrated autocorrelation time ``2 * sum - 1``
    is not positive (strongly negative dependence, such as an
    alternating series), the result is NaN, not a negative size. Close
    to that boundary the estimate is unstable.
    """
    n = x.size
    rho = acf(x, min(n // 2, 500))
    total = 0.0
    for m in range(rho.size // 2):
        pair = rho[2 * m] + rho[2 * m + 1]
        if pair < 0:
            break
        total += pair
    tau = 2 * total - 1
    return float(n / tau) if tau > 0 else float("nan")


def lag1_bootstrap_ci(
    x: FloatArray, rng: np.random.Generator
) -> tuple[float, float]:
    """95% block-bootstrap interval for the lag-1 autocorrelation.

    Resamples blocks of ``BLOCK`` consecutive lag products
    ``x_i x_(i+1)`` and squares, so block joins do not bias it. ``x``
    is the order-centred series.
    """
    prod = x[:-1] * x[1:]
    sq = x[:-1] ** 2
    m = prod.size
    blocks = max(m // BLOCK, 1)
    starts = rng.integers(0, max(m - BLOCK, 0) + 1, size=(BOOTSTRAP, blocks))
    idx = (starts[:, :, None] + np.arange(BLOCK)).reshape(BOOTSTRAP, -1)
    idx = np.minimum(idx, m - 1)
    rho = prod[idx].sum(axis=1) / sq[idx].sum(axis=1)
    lo, hi = np.quantile(rho, [0.025, 0.975])
    return float(lo), float(hi)


def _tail_block(z_dev: FloatArray, scale: float) -> dict[str, float]:
    """Shares of ``z_dev`` beyond 3 and 6 robust sd, both and upper."""
    if scale == 0:
        return {"beyond3_abs": float("nan"), "beyond6_abs": float("nan")}
    return {
        "beyond3_abs": float(np.mean(np.abs(z_dev) > 3 * scale)),
        "beyond6_abs": float(np.mean(np.abs(z_dev) > 6 * scale)),
        "beyond3_upper": float(np.mean(z_dev > 3 * scale)),
        "beyond6_upper": float(np.mean(z_dev > 6 * scale)),
    }


def arm_stats(t: FloatArray) -> dict[str, Any]:
    """Spread and tails of one arm's measurements ``t`` (seconds)."""
    log_t = np.log(t)
    med = float(np.median(t))
    scale = robust_sd(log_t)
    quant = np.quantile(t, QUANTILES) / med
    return {
        "median": med,
        "cv": float(np.std(t, ddof=1) / np.mean(t)),
        "sd_log": float(np.std(log_t, ddof=1)),
        "robust_sd_log": scale,
        "quantiles_over_median": {
            f"{q:g}": float(v) for q, v in zip(QUANTILES, quant, strict=True)
        },
        "max_over_median": float(t.max() / med),
        "min_over_median": float(t.min() / med),
        "tails_log": _tail_block(log_t - np.median(log_t), scale),
    }


def pair_stats(
    m: FloatArray, first: BoolArray, rng: np.random.Generator
) -> dict[str, Any]:
    """Statistics of one measurement ``m`` of shape ``(n, 2)``.

    Returns the per-arm statistics, and for ``d = log(A / B)`` its sd,
    lag-1 autocorrelation with interval, ACF to lag 20, effective
    sample size and tails.
    """
    d = np.log(m[:, 0]) - np.log(m[:, 1])
    dc = order_centred(d, first)
    lag1 = float(lag1_autocorrelation(d, first))
    lo, hi = lag1_bootstrap_ci(dc, rng)
    ess = effective_sample_size(dc)
    scale = robust_sd(dc)
    return {
        "arm_A": arm_stats(m[:, 0]),
        "arm_B": arm_stats(m[:, 1]),
        "d": {
            "sd": float(np.std(d, ddof=1)),
            "robust_sd": scale,
            "mean_order_effect": float(d[first].mean() - d[~first].mean())
            if first.any() and (~first).any()
            else None,
            "lag1": lag1,
            "lag1_ci95_block_bootstrap": [lo, hi],
            "lag1_white_noise_band": 1.96 / float(np.sqrt(d.size)),
            "acf_to_lag20": [float(v) for v in acf(dc, ACF_LAGS)[1:]],
            "ess": ess,
            "ess_over_n": ess / d.size,
            "tails": _tail_block(dc, scale),
            "max_abs_over_robust_sd": float(np.abs(dc).max() / scale)
            if scale > 0
            else None,
        },
    }


def _binseg(y: FloatArray, penalty: float) -> list[int]:
    """Binary segmentation on the mean; split where gain > penalty."""
    found: list[int] = []

    def split(lo: int, hi: int) -> None:
        if hi - lo < 2 * MIN_SEGMENT:
            return
        seg = y[lo:hi]
        csum = np.cumsum(seg)
        k = np.arange(MIN_SEGMENT, seg.size - MIN_SEGMENT + 1)
        left = csum[k - 1] / k
        right = (csum[-1] - csum[k - 1]) / (seg.size - k)
        gain = k * (seg.size - k) / seg.size * (left - right) ** 2
        best = int(np.argmax(gain))
        if gain[best] > penalty:
            at = lo + int(k[best])
            found.append(at)
            split(lo, at)
            split(at, hi)

    split(0, y.size)
    return sorted(found)


def changepoints(y: FloatArray) -> dict[str, Any]:
    """Level shifts of a series ``y`` (log scale), with their size.

    Uses ``ruptures`` PELT (L2 cost, minimum segment 30) when it is
    importable, else binary segmentation with the same cost. The series
    is winsorised at 5 robust sd from the median first, so isolated
    spikes are not read as segments; the penalty is
    ``3 sigma^2 log n`` with sigma from the median absolute successive
    difference.

    Returns
    -------
    dict
        ``method``, ``penalty``, and ``segments``: start index, length
        and the segment median relative to the overall median, in %.
    """
    med = float(np.median(y))
    scale = robust_sd(y)
    w = np.clip(y, med - 5 * scale, med + 5 * scale) if scale > 0 else y
    sigma = float(
        MAD_TO_SD * np.median(np.abs(np.diff(w) - np.median(np.diff(w))))
    ) / np.sqrt(2)
    penalty = 3 * sigma**2 * float(np.log(y.size))
    method = "binary segmentation (CUSUM on the mean)"
    cuts: list[int]
    try:
        rpt: Any = importlib.import_module("ruptures")
    except ImportError:
        cuts = _binseg(w, penalty)
    else:
        algo: Any = rpt.Pelt(model="l2", min_size=MIN_SEGMENT, jump=1)
        cuts = [int(c) for c in algo.fit(w).predict(pen=penalty)][:-1]
        method = f"ruptures PELT (l2, version {rpt.__version__})"
    edges = [0, *cuts, y.size]
    segments = [
        {
            "start": a,
            "length": b - a,
            "median_vs_overall_pct": float(
                100 * (np.exp(np.median(y[a:b]) - med) - 1)
            ),
        }
        for a, b in itertools.pairwise(edges)
    ]
    return {
        "method": method,
        "penalty": penalty,
        "n_changepoints": len(cuts),
        "segments": segments,
    }


def two_component(x: FloatArray) -> dict[str, float]:
    """Compare 1- and 2-component Gaussian mixtures fitted to ``x``.

    EM on the values, started from a split at the median; reports the
    BIC difference (1-component minus 2-component, positive favours two
    modes), the smaller component's weight and the distance between
    the means in units of the pooled component sd. Also Sarle's
    bimodality coefficient (above 5/9 is suggestive). Not Hartigan's
    dip; a mixture also takes heavy tails as a second component, so a
    large BIC difference with a tiny weight or a small separation is a
    tail, not a mode.
    """
    n = x.size
    mu = np.array([x[x <= np.median(x)].mean(), x[x > np.median(x)].mean()])
    var = np.full(2, x.var() + 1e-300)
    w = np.array([0.5, 0.5])
    ll = -float("inf")
    new_ll = ll
    for _ in range(500):
        dens = (
            w
            / np.sqrt(2 * np.pi * var)
            * np.exp(-((x[:, None] - mu) ** 2) / (2 * var))
        ) + 1e-300
        total = dens.sum(axis=1)
        new_ll = float(np.log(total).sum())
        resp = dens / total[:, None]
        nk = resp.sum(axis=0) + 1e-12
        w = nk / n
        mu = (resp * x[:, None]).sum(axis=0) / nk
        var = (resp * (x[:, None] - mu) ** 2).sum(axis=0) / nk + 1e-300
        if abs(new_ll - ll) < 1e-9:
            break
        ll = new_ll
    ll1 = float(-0.5 * n * (np.log(2 * np.pi * x.var()) + 1))
    bic1 = -2 * ll1 + 2 * np.log(n)
    bic2 = -2 * new_ll + 5 * np.log(n)
    small = int(np.argmin(w))
    pooled = float(np.sqrt(np.sum(w * var)))
    c = (x - x.mean()) / x.std()
    skew = float(np.mean(c**3))
    kurt = float(np.mean(c**4)) - 3.0
    coefficient = (skew**2 + 1) / (
        kurt + 3 * (n - 1) ** 2 / ((n - 2) * (n - 3))
    )
    return {
        "delta_bic": float(bic1 - bic2),
        "small_weight": float(w[small]),
        "separation_sd": float(abs(mu[0] - mu[1]) / pooled),
        "bimodality_coefficient": float(coefficient),
    }


def ranks(x: FloatArray) -> FloatArray:
    """Average ranks of ``x`` (ties share their mean rank)."""
    order = np.argsort(x, kind="stable")
    r = np.empty(x.size)
    r[order] = np.arange(x.size, dtype=float)
    sorted_x = x[order]
    start = 0
    while start < x.size:
        stop = start
        while stop + 1 < x.size and sorted_x[stop + 1] == sorted_x[start]:
            stop += 1
        if stop > start:
            r[order[start : stop + 1]] = (start + stop) / 2
        start = stop + 1
    return r


def spearman(x: FloatArray, y: FloatArray) -> float | None:
    """Spearman correlation, or None when either side is constant."""
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 3 or np.ptp(x[ok]) == 0 or np.ptp(y[ok]) == 0:
        return None
    return float(np.corrcoef(ranks(x[ok]), ranks(y[ok]))[0, 1])


def drift_block(trace: Trace) -> dict[str, Any]:
    """Drift of pair-mean log time against index, load and heat."""
    m = np.log(trace.wall).mean(axis=1)
    n = m.size
    idx = np.arange(n, dtype=float)
    slope = float(np.polyfit(idx, m, 1)[0])
    d = np.log(trace.wall[:, 0]) - np.log(trace.wall[:, 1])
    head = min(100, n // 4)
    out: dict[str, Any] = {
        "linear_drift_over_trace_pct": float(100 * (np.exp(slope * n) - 1)),
        "median_first_vs_last_pct": float(
            100 * (np.exp(np.median(m[:head]) - np.median(m[n - head :])) - 1)
        ),
        "first_pairs_used": head,
        "spearman_logtime_vs_pair_index": spearman(idx, m),
        "spearman_logtime_vs_load1": spearman(trace.load1, m),
        "spearman_d_vs_load1": spearman(trace.load1, d),
        "spearman_abs_d_vs_load1": spearman(trace.load1, np.abs(d)),
        "load1_min_median_max": [
            float(v)
            for v in (
                np.nanmin(trace.load1),
                np.nanmedian(trace.load1),
                np.nanmax(trace.load1),
            )
        ],
    }
    high = trace.load1 > np.nanmedian(trace.load1)
    if high.any() and (~high).any():
        out["median_logtime_load_above_median_vs_below_pct"] = float(
            100 * (np.exp(np.median(m[high]) - np.median(m[~high])) - 1)
        )
    speed = trace.speed_limit
    if np.isfinite(speed).any():
        limited = speed < 100
        out["pairs_with_cpu_speed_limit_below_100"] = int(limited.sum())
        if limited.any() and (~limited).any():
            out["median_logtime_limited_vs_not_pct"] = float(
                100
                * (np.exp(np.median(m[limited]) - np.median(m[~limited])) - 1)
            )
    else:
        out["pairs_with_cpu_speed_limit_below_100"] = None
    if np.isfinite(trace.on_ac).any():
        out["pairs_not_on_ac_power"] = int((trace.on_ac == 0).sum())
    else:
        out["pairs_not_on_ac_power"] = None
    return out


def linux_block(trace: Trace) -> dict[str, Any] | None:
    """Steal time and migrations of a Linux trace, or None."""
    if not (trace.all_ticks > 0).any():
        return None
    m = np.log(trace.wall).mean(axis=1)
    share = trace.steal / np.maximum(trace.all_ticks, 1)
    return {
        "pairs_with_steal": int((trace.steal > 0).sum()),
        "steal_ticks_total": float(trace.steal.sum()),
        "steal_share_of_all_cpu_ticks": float(
            trace.steal.sum() / trace.all_ticks.sum()
        ),
        "spearman_logtime_vs_steal_share": spearman(share, m),
        "mean_cpu_migrations_per_run": float(np.nanmean(trace.migrations)),
        "spearman_logtime_vs_migrations": spearman(trace.migrations, m),
    }


def gate_trips(d: FloatArray, first: BoolArray) -> dict[str, Any]:
    """How often the T1.8 diagnostics exceed thresholds on windows.

    For each window length, every window of consecutive rounds starting
    at an even round (so order pairs stay whole) is judged as a look of
    that length would be, by perfproof's own `lag1_autocorrelation` and
    `outlier_share` with the window's own order flags. Reports the
    exceedance of lag-1 above 0.1 and outlier share above 0.03, either
    one, over all (overlapping) windows and over non-overlapping ones.
    The gate's minimum look lengths (120 rounds for lag-1, 60 for
    outliers) are not applied: this is the raw exceedance rate, a fact
    about the noise.
    """
    out: dict[str, Any] = {}
    for w in WINDOWS:
        if d.size < w:
            continue
        dw = sliding_window_view(d, w)[::2]
        fw = sliding_window_view(first, w)[::2]
        lag = np.asarray(lag1_autocorrelation(dw, fw))
        share = np.asarray(outlier_share(dw, fw))
        bad_lag = np.nan_to_num(lag, nan=0.0) > LAG1_GATE
        bad_out = share > OUTLIER_GATE
        either = bad_lag | bad_out
        step = max(w // 2, 1)
        non = slice(None, None, step)  # stride w in rounds, w // 2 rows
        out[str(w)] = {
            "windows_overlapping": int(either.size),
            "lag1_above_0.1": float(bad_lag.mean()),
            "outlier_share_above_0.03": float(bad_out.mean()),
            "either": float(either.mean()),
            "windows_non_overlapping": int(either[non].size),
            "either_non_overlapping": float(either[non].mean()),
            "median_lag1": float(np.nanmedian(lag)),
            "outlier_z": OUTLIER_Z,
        }
    return out


def covariate_balance(trace: Trace) -> dict[str, Any]:
    """Balance of per-run covariates between the arms of a pair.

    For each per-run covariate present (``loadavg_start``,
    ``loadavg_end``, ``cpu_speed_limit``, the watched server's CPU
    seconds), the mean in each arm, the mean within-pair difference
    A - B with its t statistic, and the Spearman correlation of that
    difference with ``d``. Also the correlation of ``d`` with the
    log ratio of the arms' own user+sys CPU seconds: near 1 means a
    pair's time difference is CPU work, near 0 waiting or contention.
    """
    d = np.log(trace.wall[:, 0]) - np.log(trace.wall[:, 1])
    out: dict[str, Any] = {}
    for key, arr in trace.per_run.items():
        diff = arr[:, 0] - arr[:, 1]
        ok = np.isfinite(diff)
        if ok.sum() < 3:
            continue
        sd = float(np.std(diff[ok], ddof=1))
        out[key] = {
            "mean_A": float(np.nanmean(arr[:, 0])),
            "mean_B": float(np.nanmean(arr[:, 1])),
            "mean_diff_A_minus_B": float(diff[ok].mean()),
            "t_of_diff": float(diff[ok].mean() / (sd / np.sqrt(ok.sum())))
            if sd > 0
            else None,
            "spearman_d_vs_diff": spearman(diff, d),
        }
    cpu = np.log(trace.cpu[:, 0]) - np.log(trace.cpu[:, 1])
    out["pearson_d_vs_log_cpu_ratio"] = float(np.corrcoef(d, cpu)[0, 1])
    return out


def metric_stats(trace: Trace, seed: int) -> dict[str, Any]:
    """``pair_stats`` of every reported metric that is positive."""
    rng = np.random.default_rng(seed)
    out: dict[str, Any] = {}
    for key, arr in trace.metrics.items():
        if not (np.isfinite(arr).all() and (arr > 0).all()):
            continue
        if np.ptp(np.log(arr[:, 0]) - np.log(arr[:, 1])) > 0:
            out[key] = pair_stats(arr, trace.candidate_first, rng)
    return out


def characterise(trace: Trace, seed: int = 0) -> dict[str, Any]:
    """Run every characterisation on one trace.

    Parameters
    ----------
    trace : Trace
        From `load_trace`.
    seed : int
        Seed of the bootstrap.

    Returns
    -------
    dict
        JSON-serialisable results.
    """
    rng = np.random.default_rng(seed)
    first = trace.candidate_first
    d = np.log(trace.wall[:, 0]) - np.log(trace.wall[:, 1])
    m = np.log(trace.wall).mean(axis=1)
    result: dict[str, Any] = {
        "label": trace.header["label"],
        "pairs": int(trace.wall.shape[0]),
        "executions": int(2 * trace.wall.shape[0]),
        "duration_s": float(trace.start_s[-1]),
        "null_fields": trace.header["null_fields"],
        "wall": pair_stats(trace.wall, first, rng),
        "changepoints_pair_mean_logtime": changepoints(m),
        "changepoints_d": changepoints(d),
        "bimodality_logtime_arm_A": two_component(np.log(trace.wall[:, 0])),
        "bimodality_logtime_arm_B": two_component(np.log(trace.wall[:, 1])),
        "drift": drift_block(trace),
        "diagnostic_exceedance_on_windows": gate_trips(d, first),
        "median_wall_s": float(np.median(trace.wall)),
    }
    if np.isfinite(trace.instr).all():
        result["median_instructions"] = float(np.median(trace.instr))
    result["covariate_balance"] = covariate_balance(trace)
    if trace.metrics:
        result["metrics"] = metric_stats(trace, seed)
    linux = linux_block(trace)
    if linux is not None:
        result["linux"] = linux
    cpu_ok = np.isfinite(trace.cpu).all() and (trace.cpu > 0).all()
    if cpu_ok:
        result["cpu_seconds"] = pair_stats(trace.cpu, first, rng)
    if np.isfinite(trace.instr).all():
        ins = pair_stats(trace.instr, first, rng)
        result["instructions"] = ins
        wall = result["wall"]
        result["instructions_vs_wall"] = {
            "sd_log_ratio_wall_over_instr_arm_A": wall["arm_A"]["sd_log"]
            / ins["arm_A"]["sd_log"],
            "sd_log_ratio_wall_over_instr_arm_B": wall["arm_B"]["sd_log"]
            / ins["arm_B"]["sd_log"],
            "robust_sd_log_ratio_wall_over_instr_arm_A": wall["arm_A"][
                "robust_sd_log"
            ]
            / ins["arm_A"]["robust_sd_log"]
            if ins["arm_A"]["robust_sd_log"] > 0
            else None,
            "sd_d_ratio_wall_over_instr": wall["d"]["sd"] / ins["d"]["sd"],
            "instr_changepoints_pair_mean": changepoints(
                np.log(trace.instr).mean(axis=1)
            )["n_changepoints"],
        }
    return result


def summary_row(r: dict[str, Any]) -> str:
    """One markdown table row of the headline numbers of a result."""
    w = r["wall"]
    d = w["d"]
    lo, hi = d["lag1_ci95_block_bootstrap"]
    g = r["diagnostic_exceedance_on_windows"]
    ratio = r.get("instructions_vs_wall", {}).get("sd_d_ratio_wall_over_instr")
    return (
        f"| {r['label']} | {r['pairs']} | {r['duration_s'] / 60:.0f} min "
        f"| {100 * w['arm_A']['cv']:.2f} / {100 * w['arm_B']['cv']:.2f} "
        f"| {d['lag1']:.3f} [{lo:.3f}, {hi:.3f}] "
        f"| {d['ess']:.0f} "
        f"| {w['arm_A']['max_over_median']:.2f} "
        f"| {100 * d['tails']['beyond3_abs']:.1f} / "
        f"{100 * d['tails']['beyond6_abs']:.1f} "
        f"| {r['changepoints_pair_mean_logtime']['n_changepoints']} "
        f"| {100 * g['30']['either']:.0f} / {100 * g['60']['either']:.0f} "
        f"| {'-' if ratio is None else f'{ratio:.1f}'} |"
    )


def loaded_rows(results: list[dict[str, Any]]) -> list[str]:
    """Markdown rows comparing each loaded trace with its quiet twin.

    A loaded label is the quiet one with ``loaded-`` (and a ``steady-``
    or ``bursty-`` kind) inserted after the platform, as in
    ``mac-loaded-rolling`` against ``mac-rolling``.
    """
    by_label = {r["label"]: r for r in results}
    rows: list[str] = []
    for r in results:
        parts = r["label"].split("-")
        if "loaded" not in parts:
            continue
        rest = [p for p in parts if p not in ("loaded", "steady", "bursty")]
        quiet = by_label.get("-".join(rest))
        if quiet is None:
            continue
        cells = [r["label"]]
        for key, med in (
            ("wall", "median_wall_s"),
            ("instructions", "median_instructions"),
        ):
            if key not in r or key not in quiet:
                cells += ["-", "-", "-"]
                continue
            q, k = quiet[key], r[key]
            cv_q = 100 * q["arm_A"]["cv"]
            cv_k = 100 * k["arm_A"]["cv"]
            cells += [
                f"{cv_q:.2f} -> {cv_k:.2f}",
                f"{100 * (r[med] / quiet[med] - 1):+.2f}",
                f"{q['d']['lag1']:.2f} -> {k['d']['lag1']:.2f}",
            ]
        rows.append("| " + " | ".join(cells) + " |")
    return rows


def main(argv: list[str] | None = None) -> int:
    """Characterise the traces named on the command line."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("traces", nargs="+", type=Path)
    p.add_argument("--json", type=Path, help="write all results here")
    args = p.parse_args(argv)
    results = [characterise(load_trace(path)) for path in args.traces]
    if args.json:
        args.json.write_text(json.dumps(results, indent=1) + "\n")
    sys.stdout.write(
        "| trace | pairs | duration | CV % A / B | lag-1 of d [95% CI] "
        "| ESS | max/median | beyond 3 / 6 rsd % "
        "| changepoints | diagnostic over limit 30 / 60 % "
        "| sd(d) wall / instr |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    for r in results:
        sys.stdout.write(summary_row(r) + "\n")
    metric = [
        f"| {r['label']} | {k} | {100 * v['arm_A']['cv']:.2f} "
        f"| {v['d']['lag1']:.3f} | {v['d']['ess']:.0f} "
        f"| {v['arm_A']['max_over_median']:.2f} |"
        for r in results
        for k, v in r.get("metrics", {}).items()
    ]
    if metric:
        sys.stdout.write(
            "\n| trace | metric | CV % (arm A) | lag-1 of d | ESS "
            "| max/median |\n|---|---|---|---|---|---|\n"
            + "\n".join(metric)
            + "\n"
        )
    rows = loaded_rows(results)
    if rows:
        sys.stdout.write(
            "\n| loaded trace | wall CV % A quiet -> loaded "
            "| wall median shift % | wall lag-1 "
            "| instr CV % | instr median shift % | instr lag-1 |\n"
            "|---|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
