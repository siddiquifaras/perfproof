"""Record A/A traces for GPU-kernel workloads on a rented GPU host.

``gpu_collect.py`` records the same trace format as ``collect.py`` (it
calls ``collect.execute`` for every run, so a row carries the same
fields), for a command that times a GPU kernel with CUDA events and
prints ``PERFPROOF {"value": <seconds>, ...}``. Rounds are pairs, A and
B are two copies of one tree, the order of each pair comes from
``collect.round_orders`` and one warm-up pair (pair -1) is recorded and
excluded from statistics. What it adds for a paid, remote, interruptible
host:

* a health check that runs first, prints what it found and exits 2
  before any paid minute is spent on a broken setup;
* GPU details in the header (model, driver, CUDA, clocks, power limit,
  MPS, compute mode) and a GPU sample after every pair (clocks,
  temperature, power, throttle reasons);
* safe resume: a torn tail or an incomplete pair is dropped, a
  duplicate pair is dropped, the order seed is checked, and a resumed
  segment starts with its own warm-up pair so a cold cache after a
  restart never lands in the statistics;
* a time budget that stops at a pair boundary (exit 76, resumable), an
  optional background load process, and an optional sync hook run
  between pairs so the files reach another machine before the pod can
  disappear.

``gpu_collect.py plan`` turns the health files of the first probe into
pair counts, and ``gpu_collect.py verify`` checks finished traces.
Every external call has a timeout.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import importlib
import json
import math
import os
import re
import shlex
import shutil
import signal
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import IO, Any

import collect

ABORT_EXIT = collect.ABORT_EXIT
HEALTH_EXIT = 2
USAGE_EXIT = 3
BUDGET_EXIT = 76
SMI_TIMEOUT = 20.0
SYNC_TIMEOUT = 120.0
RUN_TIMEOUT = 180.0
PROBES = 5
MIN_FREE_GB = 2.0
MIN_PAIRS = 300
MAX_PAIRS = 600
PLAN_MARGIN = 1.10
BUDGET_SLACK = 1.25
RAISE_STEP = 50
CLOCK_FIELDS = (
    "clocks.sm",
    "clocks.mem",
    "temperature.gpu",
    "power.draw",
    "utilization.gpu",
)
THROTTLE_FIELDS = (
    "clocks_event_reasons.active",
    "clocks_throttle_reasons.active",
)
STATIC_FIELDS = (
    "name",
    "driver_version",
    "uuid",
    "memory.total",
    "pci.bus_id",
    "vbios_version",
    "clocks.max.sm",
    "clocks.max.mem",
    "clocks.applications.graphics",
    "power.limit",
    "persistence_mode",
    "compute_mode",
    "pstate",
    "ecc.mode.current",
)
ABSENT = {"[N/A]", "[Not Supported]", "N/A", "", "[Unknown Error]"}
CUDA_VERSION = re.compile(r"CUDA (?:UMD )?Version:\s*([\d.]+)")
# (name, target pairs, optional). The order is the priority: workloads
# are dropped from the bottom up when time runs short, matmul and sgemm
# never (see GPU.md).
PLAN_ORDER: tuple[tuple[str, int, bool], ...] = (
    ("matmul", 300, False),
    ("sgemm", 500, False),
    ("membound", 300, False),
    ("attention", 300, False),
    ("matmul_loaded", 300, True),
)
DROP_ORDER = ("matmul_loaded", "attention", "membound")
RAISE_ORDER = ("matmul", "membound", "attention", "sgemm")


class GpuError(RuntimeError):
    """``nvidia-smi`` is missing, timed out or rejected a query."""


def _smi(args: list[str], timeout: float = SMI_TIMEOUT) -> str:
    """Run ``nvidia-smi`` with ``args`` and return stdout.

    Raises
    ------
    GpuError
        If it is not installed, times out or exits non-zero.
    """
    try:
        done = subprocess.run(
            ["nvidia-smi", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        msg = f"nvidia-smi {' '.join(args)}: {exc}"
        raise GpuError(msg) from None
    if done.returncode != 0:
        msg = f"nvidia-smi {' '.join(args)}: {done.stderr.strip()[-200:]}"
        raise GpuError(msg)
    return done.stdout


def _number(text: str | None) -> float | None:
    """Parse a float, or None."""
    if text is None:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def query_gpu(fields: Sequence[str]) -> list[dict[str, str | None]]:
    """Query ``fields`` of every GPU; an absent value is None.

    Parameters
    ----------
    fields : sequence of str
        ``nvidia-smi --query-gpu`` field names.

    Returns
    -------
    list of dict
        One dict per GPU, field name to the raw text (units stripped by
        ``nounits``).

    Raises
    ------
    GpuError
        If ``nvidia-smi`` fails, or a line has a different number of
        values than fields.
    """
    out = _smi(
        [f"--query-gpu={','.join(fields)}", "--format=csv,noheader,nounits"]
    )
    gpus: list[dict[str, str | None]] = []
    for line in out.strip().splitlines():
        values = [v.strip() for v in line.split(",")]
        if len(values) != len(fields):
            msg = f"expected {len(fields)} values, got {line!r}"
            raise GpuError(msg)
        gpus.append(
            {
                k: (None if v in ABSENT else v)
                for k, v in zip(fields, values, strict=True)
            }
        )
    return gpus


def pick_throttle_field() -> str | None:
    """Return the throttle-reasons field this driver accepts, or None.

    Drivers from R550 call it ``clocks_event_reasons.active``; older
    ones ``clocks_throttle_reasons.active``.
    """
    for field in THROTTLE_FIELDS:
        try:
            query_gpu([field])
        except GpuError:
            continue
        return field
    return None


def compute_apps() -> list[str]:
    """Return the compute processes ``nvidia-smi`` lists on the GPU."""
    out = _smi(
        [
            "--query-compute-apps=pid,process_name,used_memory",
            "--format=csv,noheader,nounits",
        ]
    )
    return [x.strip() for x in out.strip().splitlines() if x.strip()]


def cuda_driver_version() -> str | None:
    """Return the highest CUDA version the driver supports, or None."""
    match = CUDA_VERSION.search(_smi([]))
    return match.group(1) if match else None


def mps_state() -> dict[str, Any]:
    """Say whether CUDA MPS looks active; None where unknown.

    The daemon is looked for by name with ``pgrep``; the pipe directory
    is the client-side environment variable. Either alone does not prove
    the benchmark ran through MPS, so both are recorded.
    """
    try:
        done = subprocess.run(
            ["pgrep", "-x", "nvidia-cuda-mps-control"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        daemon: bool | None = done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        daemon = None
    return {
        "daemon_running": daemon,
        "pipe_dir_env": os.environ.get("CUDA_MPS_PIPE_DIRECTORY"),
    }


def gpu_header() -> dict[str, Any]:
    """Describe the GPU for the trace header.

    Each static field is queried on its own so one field a driver does
    not know leaves that field None (with a reason) instead of losing
    the rest.
    """
    static: dict[str, str | None] = {}
    missing: dict[str, str] = {}
    for field in STATIC_FIELDS:
        try:
            static[field] = query_gpu([field])[0][field]
        except (GpuError, IndexError) as exc:
            static[field] = None
            missing[field] = str(exc)
    try:
        cuda = cuda_driver_version()
    except GpuError:
        cuda = None
    return {
        "static": static,
        "unreadable": missing,
        "cuda_driver_supports": cuda,
        "mps": mps_state(),
        "clocks_locked": "not changed by this script; the clocks the"
        " host runs at are what is recorded",
    }


SAMPLE_COLUMNS = {
    "clocks.sm": "gpu_sm_mhz",
    "clocks.mem": "gpu_mem_mhz",
    "temperature.gpu": "gpu_temp_c",
    "power.draw": "gpu_power_w",
    "utilization.gpu": "gpu_util_pct",
}


def usable_sample_fields() -> tuple[list[str], dict[str, str]]:
    """Return the per-pair fields this driver accepts, and the rest.

    Each of ``CLOCK_FIELDS`` is tried alone, so one name a driver does
    not know costs that column (recorded as null) and not the whole
    per-pair sample.

    Returns
    -------
    accepted, rejected : list of str, dict
        Accepted field names, and rejected name to the error text.
    """
    accepted: list[str] = []
    rejected: dict[str, str] = {}
    for field in CLOCK_FIELDS:
        try:
            query_gpu([field])
        except GpuError as exc:
            rejected[field] = str(exc)[:200]
        else:
            accepted.append(field)
    return accepted, rejected


def sample_gpu(
    throttle: str | None, fields: Sequence[str] = CLOCK_FIELDS
) -> dict[str, Any]:
    """Sample clocks, temperature, power and throttle reasons once.

    Returns the per-pair row fields; a column whose field is not in
    ``fields`` is None. A failed query gives None for all of them and
    ``gpu_sample_error`` set, never a stale value.
    """
    names = (*fields, *((throttle,) if throttle else ()))
    row: dict[str, Any] = dict.fromkeys(SAMPLE_COLUMNS.values())
    row["gpu_throttle"] = None
    row["gpu_sample_error"] = None
    try:
        got = query_gpu(names)[0]
    except (GpuError, IndexError) as exc:
        row["gpu_sample_error"] = str(exc)[:200]
        return row
    for field in fields:
        row[SAMPLE_COLUMNS[field]] = _number(got[field])
    if throttle:
        row["gpu_throttle"] = got[throttle]
    return row


def read_rows(path: Path) -> tuple[list[dict[str, Any]], bool]:
    """Read a trace file, tolerating one torn last line.

    Returns
    -------
    rows, torn : list of dict, bool
        The parsed rows and whether the last line was cut off.

    Raises
    ------
    ValueError
        If a line other than the last is not valid JSON: that is
        corruption, not an interrupted write.
    """
    rows: list[dict[str, Any]] = []
    lines = path.read_text().split("\n")
    torn = bool(lines[-1])
    if lines and lines[-1] == "":
        lines.pop()
    for i, line in enumerate(lines):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            if i != len(lines) - 1:
                msg = f"{path}: line {i + 1} is not JSON (corrupt)"
                raise ValueError(msg) from None
            torn = True
    return rows, torn


def repair_trace(path: Path, orders: list[str]) -> dict[str, Any]:
    """Make an interrupted trace safe to append to; return what changed.

    Keeps, in file order, the warm-up pairs and the whole pairs (both
    arms present once) that form the run 0, 1, 2, ... without a gap and
    whose first arm follows ``orders``. Drops a torn last line, an
    incomplete pair, a repeated ``(segment, pair, arm)`` and a pair
    after a gap. The file is rewritten atomically only if something was
    dropped. A trace with no whole pair is deleted.

    Parameters
    ----------
    path : pathlib.Path
        The ``.jsonl`` trace.
    orders : list of str
        ``collect.round_orders`` for the trace's seed.

    Returns
    -------
    dict
        ``kept_pairs``, ``dropped_rows``, ``torn_tail``.

    Raises
    ------
    ValueError
        If the file is corrupt in the middle, or a pair's first arm
        does not match ``orders`` (the seed is not the recorded one).
    """
    if not path.exists():
        return {"kept_pairs": 0, "dropped_rows": 0, "torn_tail": False}
    rows, torn = read_rows(path)
    groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for row in rows:
        key = (int(row.get("segment", 0)), int(row["pair"]))
        groups.setdefault(key, []).append(row)
    kept: list[dict[str, Any]] = []
    next_pair = 0
    for (_, pair), group in groups.items():
        whole = sorted(r["pos"] for r in group) == [0, 1] and {
            r["arm"] for r in group
        } == {"A", "B"}
        if not whole:
            continue
        if pair == -1:
            kept.extend(group)
            continue
        if pair != next_pair:
            continue
        first = next(r["arm"] for r in group if r["pos"] == 0)
        if first != orders[pair]:
            msg = (
                f"{path}: pair {pair} ran {first} first, the seed says"
                f" {orders[pair]}; wrong seed for this file"
            )
            raise ValueError(msg)
        kept.extend(group)
        next_pair += 1
    dropped = len(rows) - len(kept)
    if next_pair == 0:
        path.unlink()
    elif dropped or torn:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in kept)
        )
        os.replace(tmp, path)
    return {
        "kept_pairs": next_pair,
        "dropped_rows": dropped,
        "torn_tail": torn,
    }


def write_header(path: Path, header: dict[str, Any]) -> None:
    """Write ``header`` as JSON atomically."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(header, indent=2) + "\n")
    os.replace(tmp, path)


def plan_pairs(
    pair_s: dict[str, float], budget_s: float
) -> tuple[dict[str, int], list[str]]:
    """Choose pair counts that fit ``budget_s`` seconds of recording.

    The rule (GPU.md, "Sizing rule"): cost of a pair is the measured
    per-pair seconds times ``PLAN_MARGIN``. Try the target counts of
    ``PLAN_ORDER`` for the required workloads; else every required
    workload at ``MIN_PAIRS``; else drop ``attention``, then
    ``membound``; ``matmul`` and ``sgemm`` are never dropped (an empty
    plan is returned if even they do not fit). Then, if the optional
    loaded workload fits at ``MIN_PAIRS`` in what is left, add it, and
    spend the rest in steps of ``RAISE_STEP`` pairs round-robin over
    ``RAISE_ORDER`` up to ``MAX_PAIRS``. Counts are even, because the
    calibration's blocks keep an order pair whole.

    Parameters
    ----------
    pair_s : dict
        Workload name to measured seconds per pair (both runs plus
        sampling); a workload not present is skipped.
    budget_s : float
        Seconds available for recording.

    Returns
    -------
    plan, notes : dict of int, list of str
        Pairs per workload (0 = not recorded) and why anything was
        reduced or dropped.
    """
    cost = {k: v * PLAN_MARGIN for k, v in pair_s.items()}
    notes: list[str] = []
    missing = [n for n in ("matmul", "sgemm") if n not in cost]
    if missing:
        notes.append(f"no healthy probe for {', '.join(missing)}")
        return {n: 0 for n, _, _ in PLAN_ORDER}, notes
    required = [
        (n, t) for n, t, optional in PLAN_ORDER if not optional and n in cost
    ]
    plan = {n: 0 for n, _, _ in PLAN_ORDER}

    def total(p: dict[str, int]) -> float:
        return sum(p[n] * cost[n] for n in cost)

    plan |= dict(required)
    if total(plan) > budget_s:
        notes.append(f"targets over budget; every workload at {MIN_PAIRS}")
        for n, _ in required:
            plan[n] = MIN_PAIRS
    for name in DROP_ORDER[1:]:
        if total(plan) <= budget_s:
            break
        if plan[name]:
            notes.append(f"dropped {name}: over budget at {MIN_PAIRS} pairs")
            plan[name] = 0
    if total(plan) > budget_s:
        notes.append("matmul and sgemm at the minimum do not fit")
        return dict.fromkeys(plan, 0), notes
    loaded = DROP_ORDER[0]
    if loaded in cost and (total(plan) + MIN_PAIRS * cost[loaded] <= budget_s):
        plan[loaded] = MIN_PAIRS
    elif loaded in cost:
        notes.append(f"{loaded} not recorded: {MIN_PAIRS} pairs do not fit")
    progress = True
    while progress:
        progress = False
        for name in RAISE_ORDER:
            if name not in cost or not plan[name] or plan[name] >= MAX_PAIRS:
                continue
            step = min(RAISE_STEP, MAX_PAIRS - plan[name])
            if total(plan) + step * cost[name] <= budget_s:
                plan[name] += step
                progress = True
    for name in plan:
        plan[name] -= plan[name] % 2
    return plan, notes


def verify_trace(path: Path, expect_pairs: int | None = None) -> list[str]:
    """Check a finished trace; return the problems found (empty is OK).

    Checks: header present with a GPU block; one warm-up pair at least;
    pairs numbered 0..n-1 without gap or repeat; every pair whole; the
    first arm of each pair following the header's seed; ``reported_s``
    present, positive and finite on every row; at least ``expect_pairs``
    pairs when given.
    """
    problems: list[str] = []
    header_path = path.with_suffix(".header.json")
    header: dict[str, Any] = {}
    if header_path.exists():
        header = json.loads(header_path.read_text())
    else:
        problems.append("no header file")
    if header and not header.get("gpu") and not header.get("standin"):
        problems.append("header has no gpu block")
    try:
        rows, torn = read_rows(path)
    except ValueError as exc:
        return [*problems, str(exc)]
    if torn:
        problems.append("torn last line")
    seen: set[tuple[int, int, str]] = set()
    pairs: dict[int, int] = {}
    for row in rows:
        key = (int(row.get("segment", 0)), int(row["pair"]), row["arm"])
        if key in seen:
            problems.append(f"duplicate row {key}")
        seen.add(key)
        value = row.get("reported_s")
        if (
            value is None
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or value <= 0
        ):
            problems.append(f"pair {row['pair']}: bad reported_s {value!r}")
        if not row["warmup"]:
            pairs[row["pair"]] = pairs.get(row["pair"], 0) + 1
    if not any(r["warmup"] for r in rows):
        problems.append("no warm-up pair")
    n = len(pairs)
    if sorted(pairs) != list(range(n)):
        problems.append("pair numbers are not 0..n-1")
    if any(c != 2 for c in pairs.values()):
        problems.append("a pair does not have exactly two runs")
    if "order_seed" in header and n:
        orders = collect.round_orders(header["order_seed"], n)
        firsts = {
            r["pair"]: r["arm"]
            for r in rows
            if not r["warmup"] and not r["pos"]
        }
        if any(firsts.get(i) != orders[i] for i in range(n)):
            problems.append("first arms do not follow the order seed")
    if expect_pairs is not None and n < expect_pairs:
        problems.append(f"{n} pairs, expected {expect_pairs}")
    return problems


def _tail_of(command: list[str], cwd: Path, timeout: float) -> str:
    """Run ``command`` once more and return the last 80 output lines."""
    try:
        done = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"(could not rerun: {exc})"
    lines = (done.stdout + done.stderr).splitlines()
    return "\n".join(lines[-80:])


def start_background(
    argv: list[str], log: Path, settle_s: float
) -> subprocess.Popen[bytes]:
    """Start the background load in its own session and check it lives.

    Raises
    ------
    RuntimeError
        If it has exited after ``settle_s`` seconds.
    """
    with log.open("ab") as sink:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=sink,
            start_new_session=True,
        )
    time.sleep(settle_s)
    if proc.poll() is not None:
        msg = f"background {argv} exited {proc.returncode}; see {log}"
        raise RuntimeError(msg)
    return proc


def stop_background(proc: subprocess.Popen[bytes] | None) -> None:
    """Stop the background load's process group, escalating to KILL."""
    if proc is None or proc.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)


def health_check(
    args: argparse.Namespace,
    command: list[str],
    dir_a: Path,
    background: list[str] | None,
) -> tuple[dict[str, Any], proc_type]:
    """Run the pre-flight checks, print them and return the report.

    Checks, each printed ``OK`` or ``FAIL``: free disk; one GPU visible
    and no other compute process on it (skipped with ``--no-gpu``); the
    background load starts and stays up; ``PROBES`` runs of the command
    succeed, each printing a ``reported_s`` value. It then prints the
    median and spread of the probes and the projected seconds for the
    requested pairs.

    Returns
    -------
    report, proc : dict, Popen or None
        The report (``ok`` false on any failure) and the background
        process left running, if any.
    """
    checks: list[tuple[str, bool, str]] = []
    report: dict[str, Any] = {"workload": args.target, "label": args.label}
    proc: proc_type = None
    throttle: str | None = None
    free_gb = shutil.disk_usage(args.out.parent).free / 1e9
    checks.append(("disk", free_gb >= MIN_FREE_GB, f"{free_gb:.1f} GB free"))
    if not args.no_gpu:
        try:
            gpus = query_gpu(("name", "driver_version", "memory.total"))
            checks.append(
                ("one gpu", len(gpus) == 1, f"{len(gpus)} visible: {gpus}")
            )
            apps = compute_apps()
            checks.append(
                ("gpu idle", not apps, f"{len(apps)} compute processes {apps}")
            )
            throttle = pick_throttle_field()
            checks.append(
                (
                    "throttle field",
                    throttle is not None,
                    str(throttle),
                )
            )
            report["gpu"] = gpus[0] if gpus else None
            accepted, rejected = usable_sample_fields()
            report["sample_fields"] = accepted
            report["sample_fields_rejected"] = rejected
            checks.append(
                (
                    "per-pair sample fields",
                    bool(accepted),
                    f"{accepted}; rejected {sorted(rejected)}",
                )
            )
        except GpuError as exc:
            checks.append(("nvidia-smi", False, str(exc)))
    report["throttle_field"] = throttle
    if background and all(ok for _, ok, _ in checks):
        try:
            proc = start_background(
                background,
                args.out.with_suffix(".background.log"),
                args.background_settle_s,
            )
            checks.append(("background load", True, shlex.join(background)))
        except RuntimeError as exc:
            checks.append(("background load", False, str(exc)))
    walls: list[float] = []
    values: list[float] = []
    if all(ok for _, ok, _ in checks):
        for i in range(args.probes):
            try:
                m = collect.execute(command, dir_a, False)
            except RuntimeError as exc:
                checks.append((f"probe {i}", False, str(exc)))
                report["failure_tail"] = _tail_of(
                    command, dir_a, args.run_timeout
                )
                break
            if m["reported_s"] is None:
                checks.append((f"probe {i}", False, "no PERFPROOF value"))
                break
            walls.append(m["wall_s"])
            values.append(m["reported_s"])
        else:
            checks.append(("probes", True, f"{len(walls)} runs"))
    t0 = time.perf_counter()
    if not args.no_gpu:
        sample_gpu(throttle, report.get("sample_fields") or CLOCK_FIELDS)
    sample_s = time.perf_counter() - t0
    if walls:
        median_wall = statistics.median(walls)
        median_value = statistics.median(values)
        pair_s = 2 * median_wall + sample_s
        report |= {
            "probe_wall_s": walls,
            "probe_reported_s": values,
            "median_run_wall_s": median_wall,
            "median_reported_s": median_value,
            "startup_s_estimate": median_wall - median_value,
            "reported_spread": (max(values) - min(values)) / median_value,
            "sample_s": sample_s,
            "pair_s": pair_s,
            "projected_s": pair_s * args.rounds,
        }
        slow = median_wall > args.run_timeout / 4
        checks.append(
            (
                "run time vs timeout",
                not slow,
                f"median run {median_wall:.1f} s, timeout {args.run_timeout}",
            )
        )
    report["checks"] = [
        {"check": n, "ok": ok, "detail": d} for n, ok, d in checks
    ]
    report["ok"] = all(ok for _, ok, _ in checks)
    for name, ok, detail in checks:
        sys.stdout.write(
            f"health {'OK  ' if ok else 'FAIL'} {name}: {detail}\n"
        )
    if walls:
        sys.stdout.write(
            f"health {args.label}: run {report['median_run_wall_s']:.2f} s"
            f" (kernel {report['median_reported_s']:.3f} s, start-up"
            f" {report['startup_s_estimate']:.2f} s), pair"
            f" {report['pair_s']:.2f} s, {args.rounds} pairs ="
            f" {report['projected_s'] / 60:.0f} min\n"
        )
    if report.get("failure_tail"):
        sys.stdout.write(
            f"last output of the command:\n{report['failure_tail']}\n"
        )
    return report, proc


proc_type = subprocess.Popen[bytes] | None


def run_sync(
    sync_cmd: list[str], files: list[Path], timeout: float
) -> str | None:
    """Run the sync hook; return an error text, or None on success.

    ``{files}`` in the argv is replaced by the file paths. A failure is
    reported, never raised: losing a sync must not stop the recording.
    """
    argv: list[str] = []
    for part in sync_cmd:
        if part == "{files}":
            argv.extend(str(f) for f in files)
        else:
            argv.append(part)
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)[:200]
    if done.returncode != 0:
        return f"exit {done.returncode}: {done.stderr.strip()[-200:]}"
    return None


def _parser() -> argparse.ArgumentParser:
    """Build the record command's parser."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--dir-a", type=Path, required=True)
    p.add_argument("--dir-b", type=Path, required=True)
    p.add_argument("--cmd", required=True, help="shlex-split argv")
    p.add_argument("--rounds", type=int, required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--note", default="")
    p.add_argument("--tool", action="append", default=[])
    p.add_argument("--null-reason", action="append", default=[])
    p.add_argument("--run-timeout", type=float, default=RUN_TIMEOUT)
    p.add_argument("--probes", type=int, default=PROBES)
    p.add_argument("--health-only", action="store_true")
    p.add_argument(
        "--no-gpu",
        action="store_true",
        help="CPU stand-in: no nvidia-smi calls, GPU fields are null",
    )
    p.add_argument(
        "--time-budget-s",
        type=float,
        help="stop at a pair boundary after this long; exit 76, resumable",
    )
    p.add_argument(
        "--allow-new-host",
        action="store_true",
        help="resume although the GPU's UUID differs from the header's",
    )
    p.add_argument("--background", help="shlex-split load process to run")
    p.add_argument("--background-settle-s", type=float, default=5.0)
    p.add_argument("--sync-cmd", help="shlex-split; {files} = trace files")
    p.add_argument("--sync-every", type=int, default=25)
    p.add_argument("--sync-timeout", type=float, default=SYNC_TIMEOUT)
    return p


def _segment_header(
    args: argparse.Namespace,
    command: list[str],
    dirs: dict[str, Path],
    existing: dict[str, Any] | None,
    repair: dict[str, Any],
    done_pairs: int,
    gpu: dict[str, Any] | None,
    health: dict[str, Any],
) -> dict[str, Any]:
    """Build the header for this segment, carrying the first one's."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    if existing is not None:
        header = existing
        header.pop("finished_utc", None)
        header.pop("stopped", None)
        header.pop("aborted", None)
        header["rounds_requested"] = args.rounds
        header["resumed_from_pair"] = done_pairs
        header["resumes"].append(
            {"at_utc": now, "from_pair": done_pairs, "repair": repair}
        )
        return header
    nulls: dict[str, str] = dict.fromkeys(
        ("instructions", "cycles", "max_rss_bytes"),
        "no perf or /usr/bin/time wrapper on a rented GPU host",
    )
    nulls["cpu_speed_limit"] = "pmset exists only on macOS hosts"
    nulls["power_source"] = "pmset exists only on macOS hosts"
    nulls["gpu_*"] = "null when nvidia-smi could not be read" + (
        "; --no-gpu stand-in run" if args.no_gpu else ""
    )
    for item in args.null_reason:
        key, _, why = item.partition("=")
        nulls[key] = why
    return {
        "label": args.label,
        "target": args.target,
        "note": args.note,
        "command": command,
        "dirs": {k: str(v) for k, v in dirs.items()},
        "rounds_requested": args.rounds,
        "order_seed": args.seed,
        "order_rule": "perfproof.bench.runner.round_orders; A=baseline",
        "warmup": "one pair (pair -1, warmup=true) per segment, excluded"
        " from stats; a resumed segment records its own",
        "recorder": "gpu_collect.py over collect.execute",
        "tools": collect.tool_versions(args.tool),
        "machine": collect.machine_info(),
        "gpu": gpu,
        "standin": args.no_gpu,
        "background": args.background,
        "health": {
            k: health.get(k)
            for k in (
                "median_run_wall_s",
                "median_reported_s",
                "startup_s_estimate",
                "reported_spread",
                "pair_s",
            )
        },
        "idle_wait": {
            "waited_seconds": 0.0,
            "load1_at_start": os.getloadavg()[0],
        },
        "started_utc": now,
        "resumed_from_pair": done_pairs,
        "resumes": [],
        "sync_failures": 0,
        "fields": {
            "wall_s": "perf_counter around spawn..exit of the command",
            "user_s_sys_s": "RUSAGE_CHILDREN delta across the run",
            "reported_s": "seconds between two CUDA events around a fixed"
            " number of kernel launches, as printed by the command",
            "metrics": "the full PERFPROOF JSON the command printed;"
            " peak_gpu_mem_bytes is the allocator's peak in the process",
            "load1": "os.getloadavg()[0] at the start of the pair; inside"
            " a container this is the host's, not the container's",
            "gpu_*": "one nvidia-smi sample taken right after the second"
            " run of a pair: SM and memory clock (MHz), temperature (C),"
            " power draw (W), utilisation (%) and the active throttle"
            " reasons bitmask. After the run the clocks may already have"
            " dropped, so a low clock is weak evidence; a throttle bit is"
            " not",
            "segment": "0 for the first process, +1 per resume",
        },
        "null_fields": nulls,
    }


def acquire_lock(path: Path) -> IO[str] | None:
    """Take an exclusive, non-blocking lock on ``path``; None if held.

    The lock lives as long as the returned file object and is dropped
    by the OS if the process dies, so a crashed recorder never blocks
    its own resume.
    """
    handle = path.open("a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def record(args: argparse.Namespace) -> int:
    """Lock the output, then health-check and record or resume it.

    Two recorders on one trace interleave rows and break pairs, and a
    health probe run beside a recording disturbs its timings, so the
    lock is taken for ``--health-only`` too.
    """
    args.out.parent.mkdir(parents=True, exist_ok=True)
    lock_path = args.out.with_suffix(".lock")
    lock = acquire_lock(lock_path)
    if lock is None:
        sys.stdout.write(
            f"{args.label}: another recorder holds {lock_path}; this one"
            " exits without touching the trace\n"
        )
        return USAGE_EXIT
    try:
        return _record_locked(args)
    finally:
        lock.close()


def _record_locked(args: argparse.Namespace) -> int:
    """Health-check, then record or resume the trace."""
    command = shlex.split(args.cmd)
    background = shlex.split(args.background) if args.background else None
    collect.TIMEOUT = args.run_timeout
    dirs = {"A": args.dir_a, "B": args.dir_b}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    jsonl = args.out.with_suffix(".jsonl")
    header_path = args.out.with_suffix(".header.json")
    on_disk = jsonl.read_text().count("\n") + 1 if jsonl.exists() else 0
    orders = collect.round_orders(args.seed, max(args.rounds, on_disk))
    existing: dict[str, Any] | None = None
    repair: dict[str, Any] = {}
    done_pairs = 0
    if not args.health_only and header_path.exists() and jsonl.exists():
        old: dict[str, Any] = json.loads(header_path.read_text())
        existing = old
        same: bool = (
            old["label"] == args.label
            and old["order_seed"] == args.seed
            and old["command"] == command
        )
        if not same:
            sys.stdout.write(
                f"{args.label}: {header_path} was recorded with another"
                " label, seed or command; refusing to append\n"
            )
            return USAGE_EXIT
    if not args.health_only:
        try:
            repair = repair_trace(jsonl, orders)
        except ValueError as exc:
            sys.stdout.write(f"{args.label}: cannot resume: {exc}\n")
            return USAGE_EXIT
        done_pairs = repair["kept_pairs"]
        if done_pairs == 0:
            existing = None
    if done_pairs:
        sys.stdout.write(
            f"{args.label}: resuming at pair {done_pairs}; {repair}\n"
        )
    health, bg = health_check(args, command, dirs["A"], background)
    health_path = args.out.with_suffix(".health.json")
    write_header(health_path, health)

    def on_signal(_signo: int, _frame: FrameType | None) -> None:
        _STOP["signal"] = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGHUP, on_signal)
    try:
        if not health["ok"]:
            stop_background(bg)
            sys.stdout.write(f"{args.label}: health check FAILED\n")
            return HEALTH_EXIT
        if args.health_only:
            return 0
        if done_pairs >= args.rounds:
            sys.stdout.write(f"{args.label}: already {done_pairs} pairs\n")
            return 0
        return _record_pairs(
            args, command, dirs, orders, existing, repair, done_pairs,
            health, bg,
        )  # fmt: skip
    finally:
        stop_background(bg)


def _record_pairs(
    args: argparse.Namespace,
    command: list[str],
    dirs: dict[str, Path],
    orders: list[str],
    existing: dict[str, Any] | None,
    repair: dict[str, Any],
    done_pairs: int,
    health: dict[str, Any],
    bg: proc_type,
) -> int:
    """Append the warm-up and the remaining pairs."""
    jsonl = args.out.with_suffix(".jsonl")
    header_path = args.out.with_suffix(".header.json")
    throttle = health.get("throttle_field")
    sample_fields = health.get("sample_fields") or list(CLOCK_FIELDS)
    gpu = None if args.no_gpu else gpu_header()
    if existing is not None and gpu is not None and not args.allow_new_host:
        old_gpu: dict[str, Any] = existing.get("gpu") or {}
        old_static: dict[str, Any] = old_gpu.get("static") or {}
        new_static: dict[str, Any] = gpu["static"]
        old = old_static.get("uuid")
        new = new_static.get("uuid")
        if old != new:
            sys.stdout.write(
                f"{args.label}: this GPU ({new}) is not the one the trace"
                f" was started on ({old}); a trace must stay on one GPU."
                " Pass --allow-new-host to continue anyway\n"
            )
            return USAGE_EXIT
    header = _segment_header(
        args, command, dirs, existing, repair, done_pairs, gpu, health
    )
    segment = len(header["resumes"])
    write_header(header_path, header)
    sampler: Callable[[], dict[str, Any]] = (
        (lambda: dict.fromkeys(_GPU_NULL))
        if args.no_gpu
        else (lambda: sample_gpu(throttle, sample_fields))
    )
    sync = shlex.split(args.sync_cmd) if args.sync_cmd else None
    files = [jsonl, header_path]
    started = time.monotonic()
    pair_cost = health.get("pair_s") or 0.0

    def do_pair(pair: int, warmup: bool, order: str) -> None:
        load1 = os.getloadavg()[0]
        rows: list[dict[str, Any]] = []
        for pos, arm in enumerate((order, "B" if order == "A" else "A")):
            m = collect.execute(command, dirs[arm], False)
            rows.append(
                {"pair": pair, "warmup": warmup, "pos": pos, "arm": arm}
                | m
                | {"load1": load1}
            )
        extra = sampler() | {
            "cpu_speed_limit": None,
            "power_source": None,
            "segment": segment,
        }
        with jsonl.open("a") as f:
            for row in rows:
                f.write(json.dumps(row | extra, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def sync_now() -> None:
        if sync is None:
            return
        err = run_sync(sync, files, args.sync_timeout)
        if err:
            header["sync_failures"] += 1
            write_header(header_path, header)
            sys.stdout.write(f"{args.label}: sync failed: {err}\n")

    do_pair(-1, True, "A")
    for pair in range(done_pairs, args.rounds):
        stopped = None
        if stop_flag_set():
            stopped = "signal"
        elif (
            args.time_budget_s is not None
            and time.monotonic() - started + pair_cost * BUDGET_SLACK
            > args.time_budget_s
        ):
            stopped = "time budget"
        elif bg is not None and bg.poll() is not None:
            header["aborted"] = f"pair {pair}: background load exited"
            write_header(header_path, header)
            sys.stdout.write(f"{args.label}: ABORT {header['aborted']}\n")
            sync_now()
            return ABORT_EXIT
        if stopped:
            header["stopped"] = {"reason": stopped, "next_pair": pair}
            write_header(header_path, header)
            sync_now()
            sys.stdout.write(
                f"{args.label}: stopped ({stopped}) at pair {pair}\n"
            )
            return BUDGET_EXIT
        t0 = time.monotonic()
        do_pair(pair, False, orders[pair])
        pair_cost = max(pair_cost * 0.9, time.monotonic() - t0)
        if (pair + 1) % 50 == 0:
            sys.stdout.write(f"{args.label}: {pair + 1} pairs\n")
            sys.stdout.flush()
        if sync is not None and (pair + 1) % args.sync_every == 0:
            sync_now()
    header["finished_utc"] = datetime.now(UTC).isoformat(timespec="seconds")
    write_header(header_path, header)
    sync_now()
    return 0


_GPU_NULL = (
    "gpu_sm_mhz",
    "gpu_mem_mhz",
    "gpu_temp_c",
    "gpu_power_w",
    "gpu_util_pct",
    "gpu_throttle",
    "gpu_sample_error",
)
_STOP = {"signal": False}


def stop_flag_set() -> bool:
    """Return whether SIGTERM or SIGHUP arrived."""
    return _STOP["signal"]


def plan_main(argv: list[str]) -> int:
    """``plan``: print pair counts from health files as env lines."""
    p = argparse.ArgumentParser(prog="gpu_collect.py plan")
    p.add_argument("--budget-s", type=float, required=True)
    p.add_argument("health", nargs="+", type=Path)
    args = p.parse_args(argv)
    pair_s: dict[str, float] = {}
    for path in args.health:
        report = json.loads(path.read_text())
        if not report.get("ok") or "pair_s" not in report:
            sys.stdout.write(f"# {path}: health not OK, workload skipped\n")
            continue
        pair_s[report["workload"]] = float(report["pair_s"])
    plan, notes = plan_pairs(pair_s, args.budget_s)
    for note in notes:
        sys.stdout.write(f"# {note}\n")
    spent = 0.0
    for name, n in plan.items():
        cost = n * pair_s.get(name, 0.0) * PLAN_MARGIN
        spent += cost
        sys.stdout.write(
            f"# {name}: {n} pairs, {pair_s.get(name, 0.0):.1f} s/pair,"
            f" {cost / 60:.0f} min with margin\n"
        )
        sys.stdout.write(f"PAIRS_{name}={n}\n")
        sys.stdout.write(f"PAIRSEC_{name}={pair_s.get(name, 0.0):.2f}\n")
    sys.stdout.write(
        f"# total {spent / 60:.0f} min of {args.budget_s / 60:.0f} min\n"
    )
    return 0 if any(plan.values()) else HEALTH_EXIT


def verify_main(argv: list[str]) -> int:
    """``verify``: check finished traces; exit 1 on a problem."""
    p = argparse.ArgumentParser(prog="gpu_collect.py verify")
    p.add_argument("traces", nargs="+", type=Path)
    p.add_argument("--min-pairs", type=int, default=MIN_PAIRS)
    p.add_argument(
        "--calibration-loader",
        type=Path,
        help="directory holding real_traces.py: also load each trace"
        " with its load_trace, the reader the calibration uses",
    )
    args = p.parse_args(argv)
    bad = 0
    loader: Any = None
    if args.calibration_loader:
        sys.path.insert(0, str(args.calibration_loader))
        loader = importlib.import_module("real_traces")
    for path in args.traces:
        problems = verify_trace(path, args.min_pairs)
        pairs = "?"
        if loader is not None and not problems:
            trace = loader.load_trace(path)
            pairs = str(trace.pairs)
        elif not problems:
            pairs = str(
                len({r["pair"] for r in read_rows(path)[0] if not r["warmup"]})
            )
        sys.stdout.write(
            f"{'OK  ' if not problems else 'FAIL'} {path.name}: {pairs}"
            f" pairs {'; '.join(problems)}\n"
        )
        bad += bool(problems)
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    """Dispatch ``plan``, ``verify`` or record a trace."""
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "plan":
        return plan_main(argv[1:])
    if argv and argv[0] == "verify":
        return verify_main(argv[1:])
    _STOP["signal"] = False
    args = _parser().parse_args(argv)
    return record(args)


if __name__ == "__main__":
    sys.exit(main())
