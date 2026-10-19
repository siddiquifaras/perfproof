"""Bound a function's cost by replaying it from captured calls.

"What if this were free?" is the cheapest test of a hypothesis that a
function is where the time goes. `stub_function` answers it for one
Python function of a benchmark without changing any code:

1. **Capture.** The benchmark runs once, unmeasured, with
   `perfproof.bench.stub_hook` loaded into its interpreter in
   ``record`` mode. Every call of the function is written down: the
   pickled arguments, and the pickled result or exception.
2. **Replay.** The benchmark runs in interleaved rounds, with and
   without the hook in ``replay`` mode. In replay mode a call whose
   arguments were captured returns its captured result without running
   the function; any other call runs the real function and is timed.
3. **Subtract.** The replayed runs still pay for finding and
   unpickling each result, and for any call that ran for real. The
   hook times both, so what is left, per round, is the metric of the
   benchmark with the function's cost taken out::

       remaining = stub_run - lookup_and_unpickle - real_calls
                   [- hook_install, for wall and cpu metrics]

   The hook's cost includes what no timestamp inside the wrapper can
   see (entry, argument packing, the depth check, the counter updates).
   At install the hook calls an empty function through a wrapper built
   by the same code and takes the loop's time per call less what the
   wrapper timed itself; that, times the calls, is added to the hook's
   cost. Calls in forked children are counted too, through per-process
   slots in memory shared at install and summed by the parent at exit.

   ``cap`` is ``baseline / remaining``, the geometric mean over the
   rounds with a ``LEVEL`` interval, and ``cap_bound`` is the upper end
   of that interval: Amdahl's law says no change to that function
   alone can beat it, so that is the bound to quote, never a measured
   effect of any change. The cap is withheld (``cap_withheld`` is
   ``uninformative``) when the time left is not above zero in every
   round, is not below the baseline, is smaller than the hook's own
   cost, or when forked processes could not be counted; then only
   ``cap_uncorrected`` (``baseline / stub_run``, nothing subtracted) is
   kept, and named a lower limit on the cap only when it is above 1.

What this does not capture
--------------------------
- Only Python functions, resolved as ``module:qualname`` in the module
  the benchmark imports. A function defined in ``__main__``, a
  generator or a coroutine function is refused.
- A call that changed its own arguments, whose arguments or result do
  not pickle, or that was never captured runs for real in the replay;
  it is counted, and its time is subtracted like the rest. When the
  function's work is not in its return value (a method that fills a
  list passed in), the replay's results differ from a real run's and
  the remaining time may differ from what a real removal would leave.
  The result reports the counts so the reader can judge.
- A call with a side effect (a file written, a path removed, a socket
  connected, a process started) is replayed without it, and the result
  names the effects: whatever later code reads back may differ.
- The remaining time is a difference of two noisy measurements; on a
  loaded machine it is an estimate.
- Forked children are counted. A process started with ``spawn`` or
  ``forkserver`` loads its own hook and is counted only if it exits
  normally, which was not exercised here. A process that exits without
  running ``atexit`` handlers (a killed worker) writes no counters, so
  its calls are neither counted nor subtracted; if the root process
  does so, the result says the hook did not report and gives no cap.
"""

import json
import os
import shutil
import signal
import subprocess
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

import numpy as np

from perfproof.bench.conditions import (
    EXECUTIONS_PER_ROUND,
    Condition,
    MeasureError,
    Observation,
    check_runnable,
    run_conditions,
)
from perfproof.bench.estimates import Estimate, geometric_mean
from perfproof.bench.lock import resolve_executable
from perfproof.bench.side_effects import benchmark_environment
from perfproof.schema import BenchmarkSpec

__all__ = [
    "CAPTURE_TIMEOUT_FACTOR",
    "HOOK_SOURCE",
    "StubResult",
    "hook_env",
    "stub_function",
    "stub_result",
    "write_config",
    "write_hook",
]

HOOK_SOURCE: Final = Path(__file__).with_name("stub_hook.py")
"""The file loaded into the benchmark's interpreter."""
CAPTURE_TIMEOUT_FACTOR: Final = 4.0
"""The capture run may take this many times the spec's timeout, since
it pickles every call."""

_SITECUSTOMIZE: Final = '''\
"""perfproof stub hook: loads only when PERFPROOF_STUB_CONFIG is set."""


def _perfproof_stub():
    import time

    started = time.perf_counter_ns()
    import importlib.machinery
    import importlib.util
    import os
    import sys
    import traceback

    here = os.path.dirname(os.path.abspath(__file__))
    config = os.environ.get("PERFPROOF_STUB_CONFIG")
    if config:
        try:
            spec = importlib.util.spec_from_file_location(
                "_perfproof_stub_hook", {source!r}
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules["_perfproof_stub_hook"] = module
            spec.loader.exec_module(module)
            module.install(config, started)
        except BaseException:
            error = config + ".error.%d.txt" % os.getpid()
            with open(error, "w") as f:
                traceback.print_exc(file=f)
    rest = [p for p in sys.path if os.path.abspath(p or ".") != here]
    found = importlib.machinery.PathFinder.find_spec("sitecustomize", rest)
    if found is not None and found.loader is not None:
        found.loader.exec_module(importlib.util.module_from_spec(found))


_perfproof_stub()
'''


@dataclass(frozen=True, slots=True)
class StubResult:
    """What replaying one function from captured calls showed.

    Attributes
    ----------
    function : str
        The target, ``module:qualname``.
    metric_kind : str
        ``wall``, ``cpu`` or ``reported``.
    unit : str
        Unit of the times below; ``s``.
    rounds : int
        Interleaved rounds measured; each has two executions of each
        condition.
    baseline : Estimate
        The metric of the benchmark as it is.
    stub_run : Estimate
        The metric with the function replayed, nothing subtracted.
    remaining : Estimate or None
        The metric with the function's cost taken out; ``None`` when a
        round's remainder was not above zero.
    cap : Estimate or None
        ``baseline / remaining``, the point estimate and interval;
        ``None`` when the cap is withheld.
    cap_bound : float or None
        The upper end of ``cap``'s interval: the number to use as the
        bound, since the point estimate can undercount.
    cap_withheld : str or None
        ``uninformative`` when no cap is given: what is left cannot be
        told from zero, or its subtracted hook cost is as large as it,
        or forked processes were not counted.
    cap_lower_limit : float or None
        When the cap is withheld and ``cap_uncorrected`` is above 1,
        that value, a lower limit on the cap; ``None`` otherwise, since
        a limit at or below 1 says nothing.
    cap_uncorrected : Estimate
        ``baseline / stub_run`` with nothing taken out. It understates
        the cap, but is not itself called a limit unless above 1.
    removed_share : float or None
        ``1 - remaining / baseline`` from the point estimates.
    recorded_calls : int
        Distinct calls the capture run recorded (identical arguments
        count once).
    calls_per_run : float
        Calls of the function per execution of a replay run, counted in
        every process, forked children included.
    processes_per_run : float
        Processes that ran the hook per execution, forked children
        included.
    replayed_per_run, real_per_run : float
        Of those, calls served from the capture and calls run for real.
    real_seconds_per_run, overhead_seconds_per_run : float
        Time per execution spent in real calls, and in finding and
        unpickling results (timer cost included).
    install_seconds_per_run : float
        Time per execution spent installing the hook; taken out only of
        ``wall`` and ``cpu`` metrics.
    capture_mutating_calls : int
        Captured calls that changed their own arguments.
    capture_unpicklable : int
        Calls whose arguments or result could not be pickled.
    effects : tuple of str
        Side effects seen in captured calls, which a replay skips.
    notes : tuple of str
        Anything the reader needs to weigh the number.
    directory : pathlib.Path
        Where the capture, the counters and the logs are.
    """

    function: str
    metric_kind: str
    unit: str
    rounds: int
    baseline: Estimate
    stub_run: Estimate
    remaining: Estimate | None
    cap: Estimate | None
    cap_bound: float | None
    cap_withheld: str | None
    cap_uncorrected: Estimate
    cap_lower_limit: float | None
    removed_share: float | None
    recorded_calls: int
    calls_per_run: float
    processes_per_run: float
    replayed_per_run: float
    real_per_run: float
    real_seconds_per_run: float
    overhead_seconds_per_run: float
    install_seconds_per_run: float
    capture_mutating_calls: int
    capture_unpicklable: int
    effects: tuple[str, ...]
    notes: tuple[str, ...]
    directory: Path


def write_hook(directory: Path) -> Path:
    """Write the ``sitecustomize.py`` that loads the hook.

    Parameters
    ----------
    directory : pathlib.Path
        Existing directory to create ``hook/`` in.

    Returns
    -------
    pathlib.Path
        The directory to put on ``PYTHONPATH``. The generated file does
        nothing unless ``PERFPROOF_STUB_CONFIG`` names a configuration,
        and it chains to any ``sitecustomize`` it shadows.

    Raises
    ------
    OSError
        If ``hook/`` exists or cannot be written.
    """
    hook = directory / "hook"
    hook.mkdir(parents=True)
    (hook / "sitecustomize.py").write_text(
        _SITECUSTOMIZE.format(source=str(HOOK_SOURCE.resolve())),
        encoding="utf-8",
    )
    return hook


def hook_env(spec: BenchmarkSpec, hook: Path, config: Path) -> dict[str, str]:
    """Name the environment variables that load the hook.

    Parameters
    ----------
    spec : perfproof.schema.BenchmarkSpec
        The benchmark; a ``PYTHONPATH`` it declares is kept after the
        hook's directory.
    hook : pathlib.Path
        What `write_hook` returned.
    config : pathlib.Path
        A file `write_config` wrote.

    Returns
    -------
    dict of str to str
        ``PYTHONPATH`` and ``PERFPROOF_STUB_CONFIG``.
    """
    existing = spec.env.get("PYTHONPATH")
    path = str(hook) if not existing else f"{hook}{os.pathsep}{existing}"
    return {"PYTHONPATH": path, "PERFPROOF_STUB_CONFIG": str(config)}


def write_config(
    path: Path,
    *,
    mode: str,
    module: str,
    qualname: str,
    calls: Path,
    stats: Path,
    clock: str,
    untimed_ns: int | None = None,
) -> None:
    """Write one hook configuration.

    Parameters
    ----------
    path : pathlib.Path
        The file to write.
    mode : {"record", "replay"}
        What the hook does with the function.
    module, qualname : str
        The target, as ``module:qualname`` splits.
    calls : pathlib.Path
        Directory the captured calls are written to or read from.
    stats : pathlib.Path
        Directory the process counters go to; created.
    clock : {"wall", "cpu"}
        Which clock the hook times itself with.
    untimed_ns : int, optional
        A fixed per-call cost to use instead of calibrating, for tests
        that need to know what the hook was told.

    Raises
    ------
    OSError
        If the file or directory cannot be written.
    """
    stats.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "mode": mode,
                "module": module,
                "qualname": qualname,
                "dir": str(calls),
                "stats_dir": str(stats),
                "clock": clock,
                **({} if untimed_ns is None else {"untimed_ns": untimed_ns}),
            }
        ),
        encoding="utf-8",
    )


def _capture(
    spec: BenchmarkSpec,
    root: Path,
    env: Mapping[str, str],
    hook_env: Mapping[str, str],
    directory: Path,
    timeout: float,
) -> None:
    """Run the benchmark once with the hook recording.

    Raises
    ------
    MeasureError
        ``capture_failed`` if the benchmark cannot start, exits
        non-zero or outlives the time limit.
    """
    cwd = (root / spec.cwd).absolute()
    executable = resolve_executable(spec, root, env)
    if executable is None:
        msg = f"{spec.command[0]!r} is not an executable file in {cwd}"
        raise MeasureError("workload_failed", msg)
    argv = [str(executable), *spec.command[1:]]
    out, err = directory / "capture.stdout", directory / "capture.stderr"
    with out.open("wb") as o, err.open("wb") as e:
        try:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env={**env, **hook_env},
                stdin=subprocess.DEVNULL,
                stdout=o,
                stderr=e,
                start_new_session=True,
            )
        except OSError as exc:
            msg = f"the benchmark could not start under capture: {exc}"
            raise MeasureError("capture_failed", msg) from exc
        try:
            code = proc.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            code, timed_out = proc.wait(), True
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
    if timed_out or code != 0:
        why = (
            f"timed out after {timeout:g} s" if timed_out else f"exited {code}"
        )
        msg = f"the benchmark {why} under capture; stderr is {err}"
        raise MeasureError(
            "capture_failed", msg, {"stdout": str(out), "stderr": str(err)}
        )


def _read_stats(directory: Path) -> list[dict[str, object]]:
    """Read every counter file in a directory."""
    stats: list[dict[str, object]] = []
    for path in sorted(directory.glob("stats-*.json")):
        loaded = cast("object", json.loads(path.read_text(encoding="utf-8")))
        if isinstance(loaded, dict):
            stats.append(cast("dict[str, object]", loaded))
    return stats


def _total(stats: list[dict[str, object]], field: str) -> float:
    """Sum one numeric counter over processes."""
    total = 0.0
    for entry in stats:
        value = entry.get(field, 0)
        if isinstance(value, int | float):
            total += float(value)
    return total


def _capture_summary(
    capture: Path, function: str
) -> tuple[int, int, int, tuple[str, ...], list[str]]:
    """Check the capture hooked the function; summarise what it saw.

    Returns
    -------
    distinct, mutating, unpicklable : int
        Distinct recorded calls, calls that changed their arguments,
        calls that could not be pickled.
    effects : tuple of str
        Side effects seen.
    notes : list of str
        What the reader should know.

    Raises
    ------
    MeasureError
        ``hook_not_loaded`` when no process reported,
        ``target_not_found`` when none could wrap the function, and
        ``no_calls`` when it was wrapped and never called.
    """
    for error in sorted(capture.glob("config.json.error.*.txt")):
        last = error.read_text(encoding="utf-8").strip().splitlines()[-1:]
        msg = f"the capture hook failed to start: {' '.join(last)}"
        raise MeasureError("hook_not_loaded", msg, {"log": str(error)})
    stats = _read_stats(capture / "stats")
    if not stats:
        msg = (
            "no benchmark process reported to the hook; the benchmark is "
            "not a Python program that honours PYTHONPATH (a run with -I "
            "or -E ignores it), or it exited without running exit handlers"
        )
        raise MeasureError("hook_not_loaded", msg)
    statuses = sorted({str(s.get("status")) for s in stats})
    if "wrapped" not in statuses:
        msg = f"{function} was not found to wrap: {'; '.join(statuses)}"
        raise MeasureError("target_not_found", msg, {"status": statuses})
    seen = any(capture.glob("events-*.jsonl")) and any(
        p.stat().st_size for p in capture.glob("events-*.jsonl")
    )
    calls = _total(stats, "calls")
    if calls == 0 and not seen:
        msg = (
            f"{function} was wrapped but never called while the benchmark ran"
        )
        raise MeasureError("no_calls", msg)
    distinct = sum(
        1
        for path in capture.glob("events-*.jsonl")
        for _ in path.read_bytes().splitlines()
    )
    effects = sorted(
        {
            str(name)
            for s in stats
            for name in cast("list[object]", s.get("effects") or [])
        }
    )
    mutating = int(_total(stats, "mutating_calls"))
    unpicklable = int(
        _total(stats, "unpicklable_args") + _total(stats, "unpicklable_result")
    )
    notes: list[str] = []
    if effects:
        notes.append(
            "captured calls had side effects ("
            + ", ".join(effects)
            + "), which a replay skips; code that reads them back may "
            "behave differently"
        )
    if mutating:
        notes.append(
            f"{mutating} captured call(s) changed their own arguments; "
            "those calls run for real in the replay and their time is "
            "subtracted"
        )
    if unpicklable:
        notes.append(
            f"{unpicklable} call(s) could not be pickled and run for real "
            "in the replay, their time subtracted"
        )
    return distinct, mutating, unpicklable, tuple(effects), notes


def stub_function(
    spec: BenchmarkSpec,
    root: Path,
    target: str,
    *,
    rounds: int,
    work_dir: Path,
    seed: int,
    process_env: Mapping[str, str],
    timeout_seconds: float | None = None,
) -> StubResult:
    """Bound one function's cost by replaying it from captured calls.

    Parameters
    ----------
    spec : perfproof.schema.BenchmarkSpec
        The benchmark. Its metric must be ``wall``, ``cpu`` or a
        ``reported`` metric in seconds.
    root : pathlib.Path
        Root of the tree to run in.
    target : str
        ``module:qualname`` of the function, as the benchmark imports
        its module.
    rounds : int
        Interleaved rounds to measure, at least 3 so an interval
        exists; at most the spec's budget.
    work_dir : pathlib.Path
        Empty or new directory for the capture, counters and logs.
    seed : int
        Seed for the order of conditions within rounds.
    process_env : Mapping of str to str
        perfproof's own environment, usually ``os.environ``.
    timeout_seconds : float, optional
        Time limit for each measured execution; the spec's when
        omitted. The capture run gets `CAPTURE_TIMEOUT_FACTOR` times
        the spec's.

    Returns
    -------
    StubResult
        The cap and what it rests on.

    Raises
    ------
    MeasureError
        ``invalid_target``, ``invalid_arguments``,
        ``unsupported_metric``, ``unsupported_spec``, ``side_effects``,
        ``capture_failed``, ``hook_not_loaded``, ``target_not_found``,
        ``no_calls``,
        ``workload_failed`` (also when the benchmark fails only with the
        function replayed), or ``runner_failed``.

    Notes
    -----
    Runs the benchmark once for the capture, then ``rounds * 2 * 2``
    times (two conditions, two executions each) plus warmups.
    """
    module, sep, qualname = target.partition(":")
    names = (*module.split("."), *qualname.split("."))
    if not (sep and all(n.isidentifier() for n in names)):
        msg = f"target must be module:qualname, got {target!r}"
        raise MeasureError("invalid_target", msg)
    kind = spec.metric.kind
    if kind not in ("wall", "cpu", "reported") or spec.metric.unit != "s":
        msg = (
            f"the {kind} metric in {spec.metric.unit!r} cannot be replayed "
            "against a time; use a wall, cpu or reported metric in seconds"
        )
        raise MeasureError("unsupported_metric", msg)
    if rounds < 3:
        msg = "an interval needs at least 3 rounds"
        raise MeasureError("invalid_arguments", msg)
    check_runnable(spec, root, process_env)
    work_dir.mkdir(parents=True, exist_ok=True)
    clock = "cpu" if kind == "cpu" else "wall"
    hook = write_hook(work_dir)
    capture = work_dir / "capture"
    capture.mkdir()
    config = capture / "config.json"
    write_config(
        config,
        mode="record",
        module=module,
        qualname=qualname,
        calls=capture,
        stats=capture / "stats",
        clock=clock,
    )
    env = benchmark_environment(spec, process_env)
    timeout = (
        timeout_seconds or spec.timeout_seconds
    ) * CAPTURE_TIMEOUT_FACTOR
    _capture(spec, root, env, hook_env(spec, hook, config), capture, timeout)
    capture_summary = _capture_summary(capture, target)

    def round_env(key: str, label: str) -> Mapping[str, str]:
        if key != "stub":
            return {}
        stats = work_dir / "replay" / label
        path = work_dir / "replay" / f"{label}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_config(
            path,
            mode="replay",
            module=module,
            qualname=qualname,
            calls=capture,
            stats=stats,
            clock=clock,
        )
        return hook_env(spec, hook, path)

    try:
        observed = run_conditions(
            spec,
            root,
            (Condition("baseline"), Condition("stub")),
            rounds=rounds,
            work_dir=work_dir / "runs",
            seed=seed,
            process_env=process_env,
            round_env=round_env,
            timeout_seconds=timeout_seconds,
        )
    except MeasureError as exc:
        if exc.code == "workload_failed" and exc.details.get("condition") == (
            "stub"
        ):
            msg = (
                f"{exc.message}; the benchmark runs unchanged but not with "
                f"{target} replayed, so something it does depends on what "
                "that function does besides returning"
            )
            raise MeasureError(exc.code, msg, exc.details) from exc
        raise
    result = stub_result(
        spec,
        target,
        kind,
        rounds,
        observed,
        work_dir,
        capture_summary,
    )
    shutil.rmtree(capture, ignore_errors=True)
    return result


def stub_result(
    spec: BenchmarkSpec,
    target: str,
    kind: str,
    rounds: int,
    observed: list[Observation],
    work_dir: Path,
    capture: tuple[int, int, int, tuple[str, ...], list[str]],
) -> StubResult:
    """Turn the interleaved observations and counters into the cap.

    Parameters
    ----------
    spec : perfproof.schema.BenchmarkSpec
        The benchmark; its metric unit labels the result.
    target : str
        ``module:qualname``.
    kind : str
        ``wall``, ``cpu`` or ``reported``; the hook's install time is
        part of its cost for the first two only.
    rounds : int
        Rounds measured.
    observed : list of Observation
        Baseline and ``stub`` observations, one each per round.
    work_dir : pathlib.Path
        Holds ``replay/round-<r>/stats-*.json``, the hook's counters.
    capture : tuple
        ``(distinct calls, mutating calls, unpicklable calls, effects,
        notes)`` from the capture run.

    Returns
    -------
    StubResult
        The cap, or why it is withheld.

    Raises
    ------
    MeasureError
        ``hook_not_loaded`` if a round has no counters.
    """
    distinct, mutating, unpicklable, effects, notes = capture
    by_key: dict[tuple[str, int], Observation] = {
        (o.key, o.round): o for o in observed
    }
    runs = float(EXECUTIONS_PER_ROUND)
    base: list[float] = []
    stub: list[float] = []
    free: list[float] = []
    totals: dict[str, float] = dict.fromkeys(
        (
            "calls",
            "replayed",
            "fallthrough",
            "real",
            "overhead",
            "install",
            "processes",
            "processes_lost",
        ),
        0.0,
    )
    for r in range(rounds):
        stats = _read_stats(work_dir / "replay" / f"round-{r}")
        if not stats:
            msg = (
                f"round {r}: no replay process reported its counters, so "
                "the hook's own cost is unknown and no cap is given"
            )
            raise MeasureError("hook_not_loaded", msg)
        base_run = by_key["baseline", r]
        stub_run = by_key["stub", r]
        b = float(np.mean(base_run.values))
        s = float(np.mean(stub_run.values))
        counters = {
            "calls": _total(stats, "calls") / runs,
            "replayed": _total(stats, "replayed") / runs,
            "fallthrough": _total(stats, "fallthrough") / runs,
            "real": _total(stats, "fallthrough_seconds") / runs,
            "overhead": _total(stats, "overhead_seconds") / runs,
            "install": _total(stats, "install_seconds") / runs,
            "processes": _total(stats, "processes") / runs,
            "processes_lost": _total(stats, "processes_lost") / runs,
        }
        for name, value in counters.items():
            totals[name] += value / rounds
        taken = counters["overhead"] + counters["real"]
        if kind != "reported":
            taken += counters["install"]
        base.append(b)
        stub.append(s)
        free.append(s - taken)
    baseline = geometric_mean(base)
    resolved = all(f > 0 for f in free)
    remaining = geometric_mean(free) if resolved else None
    hook_total = totals["overhead"]
    if kind != "reported":
        hook_total += totals["install"]
    notes = list(notes)
    why: str | None = None
    if remaining is None:
        why = (
            "the time left with the function removed was not above zero "
            "in every round"
        )
    elif remaining.value >= baseline.value:
        why = "the time left is not below the benchmark's own time"
    elif hook_total > remaining.value:
        why = (
            f"the hook's own cost taken out ({hook_total:.3g} s per run) "
            f"is larger than the time left ({remaining.value:.3g} s)"
        )
    if totals["processes_lost"]:
        why = (
            f"{totals['processes_lost']:.4g} forked process(es) per run "
            "could not be counted"
        )
    uncorrected = geometric_mean(
        [b / s for b, s in zip(base, stub, strict=True)]
    )
    cap = None
    if why is None:
        cap = geometric_mean([b / f for b, f in zip(base, free, strict=True)])
    else:
        notes.append(
            f"No cap can be given, because {why}: the time left cannot be "
            "told from zero at this resolution, so this measurement does "
            "not bound what changing the function could give."
        )
        if uncorrected.value > 1.0:
            notes.append(
                f"A lower limit on the cap is {uncorrected.value:.3g} "
                "(the baseline over the replayed run, nothing taken out)."
            )
    if totals["processes"] > 1:
        notes.append(
            f"{totals['processes']:.4g} processes per run called the "
            "function; hook time and real-call time are summed over them, "
            "which over-subtracts when they run in parallel (the cap is "
            "then larger, not smaller)"
        )
    if totals["fallthrough"]:
        notes.append(
            f"{totals['fallthrough']:.4g} of {totals['calls']:.4g} calls per "
            "run were not in the capture and ran for real; their time is "
            "subtracted"
        )
    if not totals["replayed"]:
        notes.append("no call was replayed from the capture")
    return StubResult(
        function=target,
        metric_kind=kind,
        unit=spec.metric.unit,
        rounds=rounds,
        baseline=baseline,
        stub_run=geometric_mean(stub),
        remaining=remaining if why is None else None,
        cap=cap,
        cap_bound=(
            None if cap is None else (cap.high if cap.high else cap.value)
        ),
        cap_withheld=None if why is None else "uninformative",
        cap_uncorrected=uncorrected,
        cap_lower_limit=(
            uncorrected.value
            if why is not None and uncorrected.value > 1.0
            else None
        ),
        removed_share=(
            None
            if remaining is None or why is not None
            else 1 - remaining.value / baseline.value
        ),
        recorded_calls=distinct,
        calls_per_run=totals["calls"],
        processes_per_run=totals["processes"],
        replayed_per_run=totals["replayed"],
        real_per_run=totals["fallthrough"],
        real_seconds_per_run=totals["real"],
        overhead_seconds_per_run=totals["overhead"],
        install_seconds_per_run=totals["install"],
        capture_mutating_calls=mutating,
        capture_unpicklable=unpicklable,
        effects=effects,
        notes=tuple(notes),
        directory=work_dir,
    )
