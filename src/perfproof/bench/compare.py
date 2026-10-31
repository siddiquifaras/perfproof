"""Compare a candidate tree against a baseline: gates, rounds, verdict.

This is step 5 of the loop in ``docs/method.md``, the thin path of the
plan's T2.5: gates first, then interleaved rounds until a look ends
the comparison, then one `perfproof.schema.RunRecord` with
``record.json``, ``report.md`` and ``samples.npz`` in the run
directory. `compare` runs the whole thing in the foreground.

Order of work
-------------
Nothing executes in either tree until the gates that only read files
have passed:

1. The machine is fingerprinted once, before anything else runs.
2. `perfproof.bench.side_effects.side_effects_gate` on both trees,
   with `perfproof.bench.side_effects.benchmark_environment`, which is
   also the environment of every command that runs later (benchmark,
   reset, correctness check, runtime probe). A failure refuses the run
   before anything executes.
3. The `lock` gate against the manifest frozen with the spec.
4. Specs the thin path cannot run are refused with `CompareStoppedError`
   (see below), still before anything executes.
5. The `runtime` gate, after probing both trees' interpreters (the
   probe may run ``uv run`` in a tree), and the `procedure` and
   `same_host` gates on what is known before any round. A runtime
   perfproof can't identify, and a host identity it can't read, make
   their gates ``not_available``, never failed: "Unknown is not wrong"
   in ``docs/method.md``. A benchmark or test command that would fetch
   code from the network is the one refusal here, after setup (which
   may install what it needs).
6. One output-producing execution of the benchmark in each tree, then
   the `correctness` gate (tests three times on the baseline, outputs
   compared); a spec with no check leaves the gate ``not_available``.
   The `coverage` gate then reruns the correctness command under
   coverage (`perfproof.bench.coverage_gate`): changed lines no passing
   check ran make it ``not_available``, which blocks KEEP and is never
   a REJECT. The candidate's untracked files are listed before the
   first command runs in its tree. Differential checks
   (T2.13) are not built either; a warning says so.
7. Warmup, then rounds, with a look wherever
   `perfproof.stats.sequential.plan_looks` places one. Each look is
   decided by `perfproof.stats.sequential.decide_look` at
   `perfproof.stats.sequential.look_confidence`, so the resolution
   stop and the widening are never skipped.
8. The `procedure` and `same_host` gates again, on the finished
   samples and a second fingerprint.

A failed gate is decided by `perfproof.stats.decision.decide` (rule
1, with the gate precedence of ``docs/method.md``) and carries no
speedup estimate, interval or guardrail result.

Failed executions
-----------------
Per "Failed executions" in ``docs/method.md``: a candidate execution
that exits non-zero, is signalled, times out, gives no valid value or
can't be started from its tree fails the `correctness` gate, with its
exit status and the tail of its output. Before the candidate is
blamed, the baseline runs once more, one execution through
`perfproof.bench.runner.Runner.run_single`. If the baseline fails
too, the failure is shared (a program missing on the host, a noexec
mount) and the comparison stops as a broken baseline. A baseline
failure (including a benchmark program the baseline tree can't
resolve, checked before the runtime gate so it is never blamed on the
candidate), a
`perfproof.bench.correctness.BaselineBrokenError`, and a fault in
perfproof's own process (a `perfproof.bench.runner.RunnerError` with
no arm, or an `OSError` from the correctness command) stop the
comparison with no verdict: `CompareStoppedError` is raised and no
record is written. Logs stay in the run directory.

Reset
-----
The runner runs the spec's ``reset`` before every execution: the
output-producing pair, warmup, rounds and the baseline recheck. A
reset that fails fails the `side_effects` gate with its output, per
"Side effects" in ``docs/method.md``, and the run stops there with
that gate REJECT; the execution it preceded never runs.

Calibration
-----------
`CALIBRATED` is True: rules 2 and 3 decide. A comparison ends on the
first look that gives KEEP (rule 2), a performance REJECT (rule 3), a
guardrail REJECT (rule 2 with a breached guardrail) or the resolution
stop, and otherwise runs to the end of its budget, or to its ``K``-th
look, and ends INCONCLUSIVE (``insufficient_evidence``). The interval
procedure is chosen from the spec's look schedule before any round
(`perfproof.stats.sequential.procedure_for_schedule`, "The T1.11
decision" in ``docs/method.md``): ``hac_lf`` for the three default
schedules of the python_native, web_agentic and llm_serving
calibration schedules, the gated batch means for the gpu_kernels
schedule and for any other schedule. The verdict records which
(``Verdict.procedure``). The interval is recorded when the last look
has one: not below 12 rounds (two complete blocks, and ``hac_lf``'s
minimum), and not when a ratio or a bound falls outside the float
range, which records the warning ``interval_unrepresentable``. Under
the gated batch means the diagnostic gate withholds a look it would
have decided and records ``autocorrelated_rounds`` or
``outlier_rounds``; ``hac_lf`` has no gate and leaves both ``None``.
Outside "The calibrated domain" (``alpha`` 0.05, the three class
schedules, and a ``wall`` primary metric for ``node`` and ``jvm``; the
gpu_kernels schedule has no real traces yet) the
comparison runs and a REJECT is decided as inside, but a KEEP is
withheld with the warning ``schedule_uncalibrated``. A schedule that
can take more than one look at 120 rounds or more is withheld the same
way. The verdict is ``calibrated`` exactly when the spec's declared
``target_class`` is confirmed by the project model (and not stale), its
schedule is that class's own, and it is in the domain; otherwise it is
``uncalibrated`` with one named warning ("The calibrated domain" in
``docs/method.md``).

Resolution
----------
Each value's step ``q`` (``docs/method.md``, "Resolution"):

- ``wall``: the step of ``time.perf_counter_ns``, measured once per
  comparison as the smallest positive change between consecutive
  readings, plus 1 ns for the runner's integer nanoseconds, and never
  below the clock's declared resolution.
- ``cpu``: 2 microseconds, the step of the two ``wait4`` times that
  are added. A stated limit: on a kernel that accounts CPU time in
  coarser ticks the real step is larger, and the bound is then not
  exact.
- ``counter``: one count.
- ``reported``: the place of the last digit printed in the
  ``PERFPROOF`` line (``9`` has 1, ``9.40`` has 0.01, ``1.5e-3`` has
  1e-4), with the continuity check. The schema now has a declared
  ``MetricSpec.resolution``, but compare doesn't read it yet (T2.5).

The resolution stop is recorded as rule 5 INCONCLUSIVE
(``insufficient_evidence``) with a ``coarse_resolution`` warning. The
schema now has the ``coarse_resolution`` diagnostic and its rule 1
INCONCLUSIVE, but compare doesn't use them yet (T2.5).

Guardrails
----------
Per "Guardrails" in ``docs/method.md``. ``peak_memory`` is measured: the
largest single process's peak resident set size of each execution
(`perfproof.bench.runner`), as bytes. Every other applicable guardrail
(``p99_latency``, ``throughput``, a project's own metric) is reported:
the benchmark prints it as a key of the same ``PERFPROOF`` line as its
primary value, once per execution, in the guardrail's unit
(`perfproof.bench.runner.reported_guardrails`), and perfproof takes the
number as given; its resolution is the place of its last printed digit,
as the primary's is. At each look every applicable
guardrail with a value is evaluated by
`perfproof.stats.guardrails.evaluate_guardrail` on the rounds so far, at
confidence ``1 - alpha / (2 K G)`` and with the procedure the primary's
(`perfproof.stats.guardrails.guardrail_procedure`), with the resolution
widening at the page size. A round with no value (Linux, where a
benchmark peaking below the launch helper's own few megabytes can't be
told from it), or a round whose line left a reported guardrail out in
either arm, makes the guardrail ``unmeasurable`` with that reason:
perfproof never measures it on the rounds that are left. The statuses
decide a rule 2 look as `perfproof.stats.decision.guardrail_actions`
says: a breach is a REJECT (``guardrail``) and ends the comparison, an
``unmeasurable`` or ``not_declared`` guardrail withholds the KEEP for
good (the run ends at that look), and a ``not_established`` one holds it
while more rounds can narrow its interval, so the look continues. The
record keeps every measured guardrail's estimate and intervals, whether
or not it breached.

Withheld, not refused
----------------------
Per "Unknown is not wrong" in ``docs/method.md``, what perfproof cannot
verify never stops the comparison. It is measured and recorded, and a
KEEP is withheld (`keep_blocked`, `_Run._keep_blocked`): a gate
``not_available``, a default guardrail the spec doesn't declare
(``not_declared``), a ``counter`` metric whose ``perf`` is unusable
(wall time is measured instead, warning ``counter_unavailable``), a
``reported`` value whose resolution can't be read back (step 0, warning
``resolution_unknown``). A detached run's deadline ends the rounds
before it, as a budget end, with the warning ``time_limit_reached``.

What the thin path does not do yet
----------------------------------
- Cross-host comparisons (``allow_cross_host``) are refused.
"""

import functools
import itertools
import json
import math
import re
import resource
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, cast

import numpy as np
from numpy.typing import NDArray

from perfproof import __version__
from perfproof.bench.correctness import (
    OUTPUT_TAIL_BYTES,
    BaselineBrokenError,
    SuiteComparison,
    check_correctness,
    clear_outputs,
)
from perfproof.bench.coverage_gate import (
    ALL_JUDGED,
    CoverageGateError,
    coverage_gate,
    untracked_files,
)
from perfproof.bench.harness import install_harness
from perfproof.bench.journal import Journal, Resumed, RoundEntry
from perfproof.bench.lock import (
    environment_warnings,
    fetching_launch,
    lock_gate,
    probe_environment,
    procedure_gate,
    resolve_executable,
    runtime_gate,
)
from perfproof.bench.runner import (
    Execution,
    ExecutionError,
    ResetFailedError,
    Round,
    Runner,
    RunnerError,
    counter_unavailable_reason,
)
from perfproof.bench.side_effects import (
    benchmark_environment,
    side_effects_gate,
)
from perfproof.bench.spec import normalize_launch_path
from perfproof.bench.tree_setup import (
    SetupFailedError,
    SetupHostError,
    ignored_program_hint,
    run_setup,
)
from perfproof.machine import fingerprint, machine_warnings
from perfproof.project.errors import NotConfirmedError, ProjectError
from perfproof.project.guard import require_confirmed
from perfproof.project.staleness import find_stale
from perfproof.project.store import load_model
from perfproof.report import write
from perfproof.schema import (
    DEFAULT_GUARDRAILS,
    Arm,
    BenchmarkSpec,
    CalibrationStatus,
    CommandOutput,
    Diagnostics,
    Difference,
    GateName,
    GateResult,
    GoalEntry,
    GuardrailResult,
    GuardrailSpec,
    Interval,
    LockManifest,
    MachineFingerprint,
    MetricKind,
    MetricSpec,
    Outcome,
    Procedure,
    Reason,
    Rule,
    RunRecord,
    RunWarning,
    Sample,
    SetupSpec,
    Text,
    TreeEnvironment,
    Verdict,
    metric_in_calibrated_domain,
)
from perfproof.stats.decision import (
    GATE_PRECEDENCE,
    decide,
    guardrail_actions,
)
from perfproof.stats.guardrails import (
    GuardrailLook,
    check_count,
    evaluate_guardrail,
    guardrail_confidence,
    guardrail_procedure,
    guardrail_result,
)
from perfproof.stats.intervals import HAC_MIN_N, hac_lf_interval, t_interval
from perfproof.stats.sequential import (
    BATCH_BLOCK,
    CALIBRATION_SEED,
    DEFAULT_ALPHA,
    LAG1_MIN_BLOCKS,
    TRACE_BASIS,
    Look,
    Resolution,
    continuity_fails,
    decide_look,
    in_calibrated_domain,
    is_class_schedule,
    is_gpu_kernels_schedule,
    lag1_autocorrelation,
    look_confidence,
    plan_looks,
    procedure_for_schedule,
)

__all__ = [
    "CALIBRATED",
    "PEAK_MEMORY_MEASURE",
    "REPORTED_MEASURE",
    "SAMPLES_NAME",
    "CompareResult",
    "CompareStoppedError",
    "compare",
    "keep_blocked",
    "late_looks",
    "undeclared_guardrails",
]

CALIBRATED: Final = True
"""Whether the interval procedure has passed calibration. True since
2026-10-28 ("The calibrated domain" in ``docs/method.md``): rules 2 and
3 decide. Where a KEEP is withheld is the domain's, not this flag's."""
SAMPLES_NAME: Final = "samples.npz"
"""File name of the raw samples in a run directory."""

_ARMS: Final[tuple[Arm, Arm]] = ("baseline", "candidate")
_CPU_STEP_SECONDS: Final = 2e-6
_CLOCK_READS: Final = 2000
_PREFIX: Final = b"PERFPROOF "
_MAX_LINE: Final = 4096
_SKIP_CHUNK: Final = 1 << 16
_NUMBER: Final = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.([0-9]+))?(?:[eE]([+-]?[0-9]+))?"
)
_LAUNCH_EXIT: Final = 127
_AUTOCORRELATION_WARN: Final = 0.3
_SKEW_WARN: Final = 2.0
PEAK_MEMORY_MEASURE: Final = (
    "the largest single process's peak resident set size: ru_maxrss of "
    "the benchmark process as wait4 reports it, the maximum over the "
    "descendants it waited for, never a sum over a process tree"
)
"""What ``peak_memory`` is measured as, written into the record."""
REPORTED_MEASURE: Final = (
    "the {metric} value the benchmark itself printed in the PERFPROOF "
    "line of each execution, taken as given: perfproof does not compute it"
)
"""What a reported guardrail is measured as, written into the record."""
_NO_LOOK: Final = (
    "the comparison ended before its first look, so no interval was computed"
)
_NO_CHECK: Final = (
    "the spec declares no correctness check (no test command and no "
    "output to compare), so nothing was checked and a KEEP is withheld"
)
_DEADLINE_MARGIN_SECONDS: Final = 10.0
"""Seconds before a detached run's deadline at which no round that
could not finish is started: the kill grace of
`perfproof.bench.detach.DEFAULT_GRACE_SECONDS`, a chosen allowance."""
_FALLBACK_METRIC: Final = MetricSpec(
    kind="wall", direction="lower_is_better", unit="s"
)
"""The measure a ``counter`` metric falls back to when ``perf`` can't
count its event."""


class CompareStoppedError(Exception):
    """The comparison stopped, or was refused, with no verdict.

    Parameters
    ----------
    code : str
        Stable snake_case identifier: ``broken_baseline`` (the baseline
        failed, alone or together with the candidate),
        ``harness_fault`` (perfproof's own process or the host failed),
        ``unsupported_spec`` (the thin path can't run this spec, or a
        benchmark or test command would fetch code from the network) or
        ``internal_fault`` (perfproof reached a state its own rules
        exclude, such as a comparison ending on a look that says
        continue; a bug, reported without a traceback).
    message : str
        What happened and what to do about it.
    run_dir : Path
        The run directory, which keeps whatever logs were written.
    """

    def __init__(self, code: str, message: str, run_dir: Path) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.run_dir = run_dir


@dataclass(frozen=True, slots=True)
class CompareResult:
    """A finished comparison.

    Attributes
    ----------
    record : RunRecord
        The record, as written to ``record.json``.
    run_dir : Path
        The run directory.
    record_path, report_path : Path
        ``record.json`` and ``report.md`` in it.
    """

    record: RunRecord
    run_dir: Path
    record_path: Path
    report_path: Path


@dataclass(frozen=True, slots=True)
class _Ended:
    """How the rounds ended: by a look, or with none taken."""

    look: Look | None
    used: int
    looks: int
    confidence: float


_Columns = tuple[
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
]
"""A guardrail's baseline and candidate values, then their steps."""


@dataclass(frozen=True, slots=True)
class _GuardrailEvaluation:
    """One guardrail at the last look: its statistics, or why none."""

    look: GuardrailLook | None
    reason: str


def _no_source(metric: str, unit: str) -> str | None:
    """Say why ``metric`` has no value to measure, or ``None``.

    ``peak_memory`` is the runner's peak resident size, in bytes only.
    Any other guardrail is reported by the benchmark, in whatever unit
    it declares, and has a source here; whether a round has its value
    is checked at each look.
    """
    if metric == "peak_memory" and unit != "bytes":
        return (
            "peak_memory is measured in bytes, but the spec's unit is "
            f"{unit!r}, so it was not measured and a KEEP is withheld"
        )
    return None


class _CandidateFailedError(Exception):
    """A candidate execution failed; carries the correctness gate."""

    def __init__(self, gate: GateResult) -> None:
        super().__init__(gate.detail)
        self.gate = gate


def compare(
    spec: BenchmarkSpec,
    lock: LockManifest,
    *,
    baseline_root: Path,
    baseline_commit: str,
    candidate_root: Path,
    run_dir: Path,
    process_env: Mapping[str, str],
    order_seed: int,
    harness_root: Path | None = None,
    project_root: Path | None = None,
    journal: Journal | None = None,
    resume: Resumed | None = None,
    deadline: float | None = None,
) -> CompareResult:
    """Prove a candidate tree against a baseline tree in the foreground.

    Parameters
    ----------
    spec : BenchmarkSpec
        The frozen spec.
    lock : LockManifest
        The manifest frozen with ``spec``.
    baseline_root : Path
        Root of the baseline tree, a worktree at ``baseline_commit``.
    baseline_commit : str
        Full SHA of the baseline commit.
    candidate_root : Path
        Root of the candidate tree.
    run_dir : Path
        Run directory to create, ``.perfproof/runs/<run-id>/``; its
        name is the run id. It must not exist yet.
    process_env : Mapping of str to str
        perfproof's own environment, usually ``os.environ``. Only the
        base variables of
        `perfproof.bench.side_effects.benchmark_environment` reach any
        command.
    order_seed : int
        Seed of the in-round order, non-negative; recorded.
    harness_root : Path, optional
        Root of the main checkout, where the spec was defined. The
        harness files the spec lists under ``.perfproof/harness/`` are
        copied from it into both trees before anything runs, replacing
        whatever a user-supplied candidate directory holds there.
        ``None`` copies nothing.
    project_root : Path, optional
        Root of the main checkout, whose project model
        (``.perfproof/project.json``) must hold a ``confirmed`` target
        of the spec's ``target_class`` for a verdict to be calibrated.
        ``None``, no model, or an unreadable one fails closed: a KEEP
        is withheld with ``target_class_unverified``. It must also hold
        the benchmark's workload (the spec's name) confirmed, with its
        goal and every constraint, unchanged on disk, on this machine
        and with this spec (`require_confirmed`), or a KEEP is
        withheld with ``workload_not_confirmed``; the comparison is
        measured either way.
    journal : perfproof.bench.journal.Journal, optional
        Where a detached run keeps its progress; each completed round
        is appended to it. ``run_dir`` may then exist already.
    resume : perfproof.bench.journal.Resumed, optional
        Completed rounds of an interrupted run to continue from, with
        its first fingerprint and start time. Every gate is checked
        again; the rounds before are replayed through the looks, not
        run again, so the same ``order_seed`` gives the same orders.
    deadline : float, optional
        Unix time of a detached run's hard timeout. Before starting a
        round that, with the kill grace, could not finish by then, the
        rounds end as at a ``max_seconds`` budget end, and the record
        carries a ``time_limit_reached`` warning. ``None``: no limit.
        Positive and finite; anything else raises `ValueError`
        (`perfproof.bench.detach` stores 0.0 for "no deadline" and
        passes ``None``).

    Returns
    -------
    CompareResult
        The record and the paths written. Time is dominated by the
        executions; the work between rounds is O(n) in the rounds so
        far, at each look.

    Raises
    ------
    CompareStoppedError
        If the comparison stops with no verdict; see the module
        docstring.
    FileExistsError
        If ``run_dir`` already exists and ``journal`` is not given.
    ValueError
        If ``deadline`` is not positive and finite.
    """
    if deadline is not None and not (0 < deadline < math.inf):
        msg = (
            "deadline is a positive, finite Unix time, or None for no "
            f"limit, not {deadline!r}"
        )
        raise ValueError(msg)
    run_dir.mkdir(parents=True, exist_ok=journal is not None)
    if resume is None:
        started = datetime.now(UTC)
        machine = fingerprint()
        if journal is not None:
            journal.begin(machine, started)
    else:
        started, machine = resume.started, resume.machine
    env = benchmark_environment(spec, process_env)
    trees: dict[Arm, Path] = {
        "baseline": baseline_root,
        "candidate": candidate_root,
    }
    run = _Run(spec, lock, trees, run_dir, env, machine, started)
    run.journal = journal
    run.resume = resume
    if resume is not None:
        run.warnings.append(
            RunWarning(
                code="resumed",
                message=(
                    f"resumed (attempt {resume.attempt}) after "
                    f"{len(resume.rounds)} completed rounds; every gate was "
                    "checked again and those rounds were not run again"
                ),
            )
        )
    run.baseline_commit = baseline_commit
    run.order_seed = order_seed
    run.harness_root = harness_root
    run.class_problem = _class_problem(spec, project_root)
    run.workload_problem = _workload_problem(spec, project_root)
    run.wall_step = _wall_step()
    run.deadline = deadline
    return run.execute()


class _Run:
    """State of one comparison while it runs."""

    def __init__(
        self,
        spec: BenchmarkSpec,
        lock: LockManifest,
        trees: Mapping[Arm, Path],
        run_dir: Path,
        env: Mapping[str, str],
        machine: MachineFingerprint,
        started: datetime,
    ) -> None:
        self.spec = spec
        self.lock = lock
        self.trees = trees
        self.run_dir = run_dir
        self.env = env
        self.machine = machine
        self.started = started
        self.procedure: Procedure = procedure_for_schedule(
            spec.looks, spec.budget.max_rounds, spec.alpha
        )
        self.baseline_commit = ""
        self.order_seed = 0
        self.harness_root: Path | None = None
        # Fail closed: a class not confirmed by the project model
        # never makes a verdict calibrated.
        self.class_problem: str | None = "the project model was not read"
        self.workload_problem: str | None = "the project model was not read"
        self.wall_step = 0.0
        self.gates: dict[GateName, GateResult] = {}
        self.warnings: list[RunWarning] = list(machine_warnings(machine))
        reason = "not probed: no command ran in this tree"
        self.environments: dict[Arm, TreeEnvironment] = {
            arm: _unprobed(arm, env, reason) for arm in _ARMS
        }
        self.measured: list[RoundEntry] = []
        self.looks = 0
        self.journal: Journal | None = None
        self.resume: Resumed | None = None
        self.deadline: float | None = None
        self.time_limit_hit = False
        self.fallback_reason: str | None = None
        self.suite: SuiteComparison | None = None
        self.untracked: tuple[str, ...] | None = None
        self.untracked_error = ""
        checks = check_count(spec.guardrails)
        self.guardrail_procedure: Procedure = (
            guardrail_procedure(self.procedure, spec.looks.max_looks, checks)
            if checks
            else self.procedure
        )
        self.guardrail_confidence = (
            guardrail_confidence(spec.alpha, spec.looks.max_looks, checks)
            if checks
            else look_confidence(spec.alpha, spec.looks.max_looks)
        )
        self.guardrail_looks: dict[str, _GuardrailEvaluation] = {}

    def stop(self, code: str, message: str) -> CompareStoppedError:
        """Build the no-verdict stop for this run."""
        return CompareStoppedError(code, message, self.run_dir)

    def execute(self) -> CompareResult:
        """Run the gates and rounds and write the record."""
        if self.journal is not None:
            self.journal.phase("gates")
        try:
            return self._execute()
        except _CandidateFailedError as failed:
            self.gates[failed.gate.gate] = failed.gate
            return self._finish_gate()
        except ResetFailedError as failed:
            self.gates["side_effects"] = failed.gate
            return self._finish_gate()

    def _execute(self) -> CompareResult:
        spec, trees = self.spec, self.trees
        self._list_untracked()
        self._install_harness()
        self.gates["side_effects"] = _side_effects(spec, trees, self.env)
        if self._failed():
            return self._finish_gate()
        self.gates["lock"] = lock_gate(
            spec,
            self.lock,
            baseline_root=trees["baseline"],
            candidate_root=trees["candidate"],
        )
        if self._failed():
            return self._finish_gate()
        self._refuse_unsupported()
        self._setup()
        if self._failed():
            return self._finish_gate()
        self._refuse_unstartable_baseline()
        self._check_counter()
        rounds_runner = self._runner(spec, trees, "rounds")
        for arm in _ARMS:
            self.environments[arm] = probe_environment(
                arm, spec, trees[arm], self.env
            )
        self.warnings.extend(
            environment_warnings(
                self.environments["baseline"], self.environments["candidate"]
            )
        )
        self._warn_calibration()
        self._warn_workload()
        self.gates["runtime"] = self._test_launcher(
            runtime_gate(
                spec,
                self.environments["baseline"],
                self.environments["candidate"],
                baseline_root=trees["baseline"],
                candidate_root=trees["candidate"],
            )
        )
        executables = self._executables()
        self.gates["procedure"] = self._procedure(executables, ())
        self.gates["same_host"] = _same_host_gate(self.machine, None)
        if self._failed():
            return self._finish_gate()
        self._correctness()
        self.warnings.append(
            RunWarning(
                code="differential_checks_not_run",
                message=(
                    "differential checks are not built yet (plan task "
                    "T2.13); the correctness gate ran the tests and "
                    "compared the declared outputs only"
                ),
            )
        )
        if self._failed():
            return self._finish_gate()
        self._coverage()
        ended = self._rounds(rounds_runner)
        self._note_resolution_unknown()
        samples = [s for m in self.measured for s in m.samples]
        self.gates["procedure"] = self._procedure(executables, samples)
        self.gates["same_host"] = _same_host_gate(self.machine, fingerprint())
        if self._failed():
            return self._finish_gate()
        return self._finish_look(ended)

    def _failed(self) -> bool:
        return any(g.status == "failed" for g in self.gates.values())

    def _install_harness(self) -> None:
        """Copy perfproof's harness files into both trees."""
        if self.harness_root is None:
            return
        for arm in _ARMS:
            try:
                install_harness(self.spec, self.harness_root, self.trees[arm])
            except OSError as exc:
                msg = f"could not install the harness in the {arm} tree: {exc}"
                raise self.stop("harness_fault", msg) from exc

    def _setup(self) -> None:
        """Run the spec's setup in each tree, then lock it again.

        A baseline failure stops the run as a broken baseline. A
        candidate failure is run once more, because an idempotent setup
        on a half-prepared tree can pass where a transient fault failed
        it; if it fails again, the baseline's setup is run once more to
        rule out a shared fault, and only then is it a correctness
        REJECT recording both attempts. The lock gate is checked again
        afterwards, because setup runs the candidate's own build.
        """
        setup, spec = self.spec.setup, self.spec
        if setup is None:
            return
        logs = self.run_dir / "logs" / "setup"
        baseline = self.trees["baseline"]
        try:
            self._run_setup(setup, baseline, logs, "baseline")
        except SetupFailedError as exc:
            msg = f"the baseline failed setup: {exc.reason}; logs: {exc.logs}"
            raise self.stop("broken_baseline", msg) from exc
        candidate = self.trees["candidate"]
        try:
            self._run_setup(setup, candidate, logs, "candidate")
        except SetupFailedError as first:
            self._retry_setup(setup, candidate, logs, first)
        self.gates["lock"] = lock_gate(
            spec,
            self.lock,
            baseline_root=baseline,
            candidate_root=self.trees["candidate"],
        )

    def _run_setup(
        self, setup: SetupSpec, tree: Path, logs: Path, arm: str
    ) -> None:
        """Run setup in one tree; a host fault stops with no verdict."""
        try:
            run_setup(setup, tree, self.env, logs, arm)
        except SetupHostError as exc:
            msg = f"setup ({arm}) hit a fault blamed on neither tree: {exc}"
            raise self.stop("harness_fault", msg) from exc

    def _retry_setup(
        self,
        setup: SetupSpec,
        candidate: Path,
        logs: Path,
        first: SetupFailedError,
    ) -> None:
        """Rerun a failed candidate setup, then blame it or stop."""
        try:
            self._run_setup(setup, candidate, logs, "candidate-retry")
        except SetupFailedError as second:
            both = f"{first.reason}; again on a rerun: {second.reason}"
            baseline = self.trees["baseline"]
            try:
                self._run_setup(setup, baseline, logs, "recheck")
            except SetupFailedError as again:
                msg = (
                    f"the candidate failed setup twice ({both}); the "
                    f"baseline failed it too when rerun ({again.reason}), "
                    f"so the failure is shared, not the candidate's. "
                    f"Logs: {first.logs}"
                )
                raise self.stop("broken_baseline", msg) from again
            gate = GateResult(
                gate="correctness",
                status="failed",
                detail=f"the candidate failed setup twice: {both}",
                output=second.output,
            )
            raise _CandidateFailedError(gate) from second
        self.warnings.append(
            RunWarning(
                code="setup_retried",
                message=(
                    f"the candidate's setup failed once ({first.reason}) "
                    "and passed on a rerun; the first failure's logs are "
                    f"in {first.logs}"
                ),
            )
        )

    def _refuse_unsupported(self) -> None:
        """Refuse a spec the thin path can't run, before it executes."""
        spec = self.spec
        if spec.allow_cross_host:
            msg = "cross-host comparisons are not built yet"
            raise self.stop("unsupported_spec", msg)
        try:
            plan_looks(
                spec.looks, np.zeros(1, np.int64), False, mode="one_host"
            )
        except ValueError as exc:
            msg = f"the look schedule can't run on one host: {exc}"
            raise self.stop("unsupported_spec", msg) from exc
        if late_looks(spec) > 1:
            self.warnings.append(
                RunWarning(
                    code="schedule_uncalibrated",
                    message=(
                        f"the look schedule (n0 {spec.looks.n0}, k "
                        f"{spec.looks.k}, {spec.looks.max_looks} looks, up "
                        f"to {spec.budget.max_rounds} rounds) can take "
                        "more than one look at "
                        f"{LAG1_MIN_BLOCKS * BATCH_BLOCK} rounds or more, "
                        "outside the schedules the procedure was "
                        "calibrated on: the comparison runs, but this "
                        "verdict can never be a KEEP"
                    ),
                )
            )

    def _warn_calibration(self) -> None:
        """Record why the verdict cannot be calibrated, once.

        Called after the runtime probe, so the one place that decides
        the blocker sees the identified runtime families as well as the
        spec: a KEEP withheld by `_calibration_blocker` always carries
        its warning. The schedule-length warning of
        `_refuse_unsupported` stands in for it when a schedule takes
        late looks.
        """
        if late_looks(self.spec) > 1:
            return
        blocker = _calibration_blocker(
            self.spec, self.class_problem, self._families()
        )
        if blocker is not None:
            self.warnings.append(
                RunWarning(code=blocker[0], message=blocker[1])
            )

    def _warn_workload(self) -> None:
        """Record why the workload is not confirmed, once.

        A KEEP stands only on a workload the developer confirmed; the
        comparison is measured regardless (`_workload_problem`).
        """
        if self.workload_problem is not None:
            self.warnings.append(
                RunWarning(
                    code="workload_not_confirmed",
                    message=(
                        f"the project model does not back workload "
                        f"{self.spec.name}: {self.workload_problem}. The "
                        "comparison runs, but this verdict can never be a "
                        "KEEP; register and confirm the workload with "
                        "`perfproof project add-workload`, `project "
                        "confirm` and `project goal --confirm`"
                    ),
                )
            )

    def _test_launcher(self, gate: GateResult) -> GateResult:
        """Add a test launcher that may download code to the gate.

        The benchmark's launcher is judged by the probe; the test
        command is not probed, so a launcher in it that may fetch code
        at run time (`fetching_launch`) is recorded here. It makes a
        passed gate ``not_available``: measured, not kept. A failed or
        waived gate is left as it is.
        """
        command = self.spec.correctness.command
        if command is None or gate.status != "passed":
            return gate
        for arm in _ARMS:
            cwd = (self.trees[arm] / self.spec.correctness.cwd).absolute()
            reason = fetching_launch(
                command, self.env, cwd, tree=self.trees[arm]
            )
            if reason is not None:
                return GateResult(
                    gate="runtime",
                    status="not_available",
                    detail=(
                        "the test command's launcher may download code at "
                        f"run time, so the runtime was not verified: {reason}"
                    ),
                )
        return gate

    def _check_counter(self) -> None:
        """Fall back to wall time when ``perf`` can't count the event.

        "Metrics" in ``docs/method.md``: the comparison measures the
        command's wall time instead, records why, and withholds a KEEP.
        """
        metric = self.spec.metric
        if metric.kind == "counter" and metric.event is not None:
            reason = counter_unavailable_reason(metric.event, env=self.env)
            if reason is not None:
                self._fall_back(reason)
        self._journal_metric()

    def _journal_metric(self) -> None:
        """Journal the measured kind; refuse a resume that would mix.

        Whether ``perf`` works is decided afresh in every attempt, so a
        resume could continue wall-time rounds with counts, or the other
        way, and the interval would mix units. The kind the first
        attempt measured is journaled, and a resume that would measure
        another is not resumable.
        """
        kind = self._metric_kind()
        if self.journal is None:
            return
        resume = self.resume
        if resume is None or not resume.rounds:
            # No round is kept from an earlier attempt, so this
            # attempt decides the kind the journal will hold.
            self.journal.metric(kind)
        elif resume.metric_kind not in (None, kind):
            msg = (
                f"the first attempt measured the {resume.metric_kind} metric "
                f"and this one would measure {kind} (perf was usable in one "
                "and not the other), so the rounds already taken can't be "
                "continued; start a new compare"
            )
            raise self.stop("not_resumable", msg)

    def _fall_back(self, reason: str) -> None:
        """Record that the counter was measured as wall time."""
        metric = self.spec.metric
        self.fallback_reason = reason
        self.warnings.append(
            RunWarning(
                code="counter_unavailable",
                message=(
                    f"the counter {metric.event} can't be measured here "
                    f"({reason}); the command's wall time in seconds was "
                    "measured instead, and a KEEP is withheld"
                ),
            )
        )

    def _refuse_unstartable_baseline(self) -> None:
        """Stop as a broken baseline when its program can't be found.

        Per "Failed executions" in ``docs/method.md``, a baseline that
        can't be started is a broken experiment, never a REJECT of the
        candidate. It comes before anything runs and covers a program
        missing from both trees too. A program missing
        only from the candidate tree stays the candidate's failure.
        """
        spec = self.spec
        tree = self.trees["baseline"]
        if resolve_executable(spec, tree, self.env) is None:
            program = spec.command[0]
            where = (
                f"at {tree / spec.cwd / program}"
                if "/" in program
                else f"on PATH {self.env.get('PATH', '')!r}"
            )
            msg = (
                f"the baseline can't be started: {program!r} is not an "
                f"executable file {where}"
            )
            hint = ignored_program_hint(
                tree, spec.cwd, program, has_setup=spec.setup is not None
            )
            raise self.stop(
                "broken_baseline", msg if hint is None else f"{msg}. {hint}"
            )
        self._refuse_ignored_test_program()

    def _refuse_ignored_test_program(self) -> None:
        """Stop when the test program is a missing ignored path."""
        spec = self.spec
        tree = self.trees["baseline"]
        command = spec.correctness.command
        if command is None or "/" not in command[0]:
            return
        cwd = spec.correctness.cwd
        if (tree / cwd / command[0]).exists():
            return
        hint = ignored_program_hint(
            tree, cwd, command[0], has_setup=spec.setup is not None
        )
        if hint is not None:
            msg = f"the baseline's test command can't be started: {hint}"
            raise self.stop("broken_baseline", msg)

    def _runner(
        self, spec: BenchmarkSpec, trees: Mapping[Arm, Path], logs: str
    ) -> Runner:
        """Build a runner whose logs go to ``logs`` in the run dir."""
        if self.fallback_reason is not None:
            spec = spec.model_copy(update={"metric": _FALLBACK_METRIC})
        try:
            return Runner(
                spec,
                trees,
                self._log_root() / logs,
                order_seed=self.order_seed,
                base_env=self.env,
            )
        except ValueError as exc:
            raise self.stop("unsupported_spec", str(exc)) from exc
        except RunnerError as exc:
            raise self.stop("unsupported_spec", str(exc)) from exc

    def _log_root(self) -> Path:
        """Return the log directory of this attempt.

        A resumed attempt keeps its logs apart, since the runner never
        writes over a log.
        """
        logs = self.run_dir / "logs"
        if self.resume is None:
            return logs
        return logs / f"resume-{self.resume.attempt}"

    def _executables(self) -> dict[Arm, str]:
        """Each tree's resolved benchmark executable, as hashed."""
        found: dict[Arm, str] = {}
        for arm in _ARMS:
            tree = self.trees[arm]
            exe = resolve_executable(self.spec, tree, self.env)
            found[arm] = (
                self.spec.command[0]
                if exe is None
                else normalize_launch_path(exe, tree)
            )
        return found

    def _procedure(
        self, executables: Mapping[Arm, str], samples: Sequence[Sample]
    ) -> GateResult:
        same = dict.fromkeys(_ARMS, self.spec.spec_hash)
        version = dict.fromkeys(_ARMS, __version__)
        return procedure_gate(
            self.spec,
            samples,
            spec_hashes=same,
            perfproof_versions=version,
            executables=executables,
        )

    def _correctness(self) -> None:
        """Produce the declared outputs, then run the correctness gate.

        Raises `_CandidateFailedError` when a candidate execution fails.
        """
        spec, trees = self.spec, self.trees
        if spec.correctness.command is None and not spec.correctness.outputs:
            self.gates["correctness"] = GateResult(
                gate="correctness", status="not_available", detail=_NO_CHECK
            )
            return
        try:
            for arm in _ARMS:
                clear_outputs(spec.correctness.outputs, trees[arm])
        except OSError as exc:
            msg = f"could not clear the declared outputs: {exc}"
            raise self.stop("harness_fault", msg) from exc
        producer = self._runner(
            spec.model_copy(update={"warmup": 1}), trees, "outputs"
        )
        self._guard(producer.warmup, "the output-producing execution")
        try:
            self.gates["correctness"], self.suite = check_correctness(
                spec,
                baseline_root=trees["baseline"],
                candidate_root=trees["candidate"],
                env=self.env,
            )
        except BaselineBrokenError as exc:
            raise self.stop("broken_baseline", str(exc)) from exc
        except OSError as exc:
            msg = f"the correctness command could not run: {exc}"
            raise self.stop("harness_fault", msg) from exc

    def _list_untracked(self) -> None:
        """List the candidate's untracked files before anything runs.

        The coverage gate counts them as the candidate's changes, and
        the runs create files (caches, build output) it must not count.
        A failure is kept for the gate to report.
        """
        try:
            self.untracked = untracked_files(self.trees["candidate"])
        except CoverageGateError as exc:
            self.untracked_error = str(exc)

    def _coverage(self) -> None:
        """Run the coverage gate on the changed lines.

        Raises `CompareStoppedError` when perfproof's own process cannot
        start the run.
        """
        spec, trees = self.spec, self.trees
        if self.untracked is None:
            self.gates["coverage"] = GateResult(
                gate="coverage",
                status="not_available",
                detail=(
                    "the candidate's changed lines could not be listed: "
                    f"{self.untracked_error}"
                ),
            )
            return
        try:
            self.gates["coverage"] = coverage_gate(
                spec,
                baseline_root=trees["baseline"],
                candidate_root=trees["candidate"],
                env=self.env,
                tests=self.suite,
                judged_tests=(
                    None if spec.correctness.command is None else ALL_JUDGED
                ),
                untracked=self.untracked,
            )
        except CoverageGateError as exc:
            self.gates["coverage"] = GateResult(
                gate="coverage",
                status="not_available",
                detail=f"the changed lines could not be listed: {exc}",
            )
        except OSError as exc:
            msg = f"the coverage run could not start: {exc}"
            raise self.stop("harness_fault", msg) from exc

    def _guard[T](self, step: Callable[[], T], what: str) -> T:
        """Run ``step``, turning a failed execution into its outcome."""
        try:
            return step()
        except ExecutionError as exc:
            if exc.arm == "baseline":
                msg = f"the baseline failed {what}: {exc}"
                raise self.stop("broken_baseline", msg) from exc
            self._recheck_baseline(
                exc.label, f"the candidate failed: {exc.reason}"
            )
            if exc.cause == "launch" and not self.spec.allow_runtime_change:
                raise _CandidateFailedError(
                    _launch_gate(exc, self.spec.command, what)
                ) from exc
            gate = _execution_gate(exc, self.spec.command, what)
            raise _CandidateFailedError(gate) from exc
        except ResetFailedError:
            raise
        except RunnerError as exc:
            msg = f"{what} stopped on a fault blamed on neither tree: {exc}"
            raise self.stop("harness_fault", msg) from exc

    def _recheck_baseline(self, label: str, why: str) -> None:
        """Run the baseline again before the candidate is blamed.

        ``why`` says what would be blamed on the candidate; the stop
        message quotes it.
        """
        checker = self._runner(self.spec, self.trees, f"recheck-{label}")
        try:
            checker.run_single("baseline", "recheck")
        except ExecutionError as exc:
            msg = (
                f"{why}; the baseline failed when run ({exc.reason}), so "
                f"the failure is the baseline's or shared, not the "
                f"candidate's. Logs: {exc.stdout.parent}"
            )
            raise self.stop("broken_baseline", msg) from exc
        except ResetFailedError:
            raise
        except RunnerError as exc:
            msg = f"the baseline recheck stopped on a harness fault: {exc}"
            raise self.stop("harness_fault", msg) from exc

    def _rounds(self, runner: Runner) -> _Ended:
        """Run warmup and rounds until a look ends the comparison."""
        spec = self.spec
        self._guard(runner.warmup, "a warmup execution")
        steps = _Steps(spec, self.wall_step, self._metric_kind())
        confidence = look_confidence(spec.alpha, spec.looks.max_looks)
        last: Look | None = None
        used = 0
        longest = 0.0
        restored = () if self.resume is None else self.resume.rounds
        begun = time.monotonic() - (restored[-1].elapsed if restored else 0.0)
        for index in range(spec.budget.max_rounds):
            if index < len(restored):
                entry = restored[index]
            else:
                start = time.monotonic()
                done = self._guard(
                    functools.partial(runner.run_round, index),
                    f"round {index}",
                )
                made = steps.of(done)
                now = time.monotonic()
                entry = RoundEntry(
                    done.samples,
                    made,
                    now - begun,
                    now - start,
                    {ex.arm: _guardrail_steps(ex) for ex in done.executions},
                )
                if self.journal is not None:
                    self.journal.round_done(index, entry)
            self.measured.append(entry)
            longest = max(longest, entry.duration)
            n = index + 1
            exhausted = n == spec.budget.max_rounds or (
                entry.elapsed + longest > spec.budget.max_seconds
            )
            if (
                not exhausted
                and index >= len(restored)
                and self.deadline is not None
                and time.time() + longest + _DEADLINE_MARGIN_SECONDS
                >= self.deadline
            ):
                exhausted = True
                self.time_limit_hit = True
            plan = plan_looks(
                spec.looks,
                np.array([n], dtype=np.int64),
                np.array([exhausted]),
                mode="one_host",
            )
            taken = int(np.count_nonzero(plan.rounds[0]))
            again = (
                exhausted
                and taken == self.looks
                and last is not None
                and str(last.action[0]) == "CONTINUE"
            )
            if taken > self.looks or again:
                self.looks = taken
                used = int(plan.rounds[0, taken - 1])
                final = bool(plan.final[0, taken - 1])
                last = self._look(used, final, confidence, steps)
                if self.journal is not None and index >= len(restored):
                    self.journal.looks(self.looks)
                if str(last.action[0]) != "CONTINUE" or self._ends_rule_2(
                    last
                ):
                    # A held rule 2 whose blocker no round lifts ends
                    # here, INCONCLUSIVE, as "Decision rule" says, and a
                    # guardrail breach is its REJECT; a guardrail only
                    # not established yet goes on.
                    break
            if exhausted:
                break
        return _Ended(
            look=last, used=used, looks=self.looks, confidence=confidence
        )

    def _look(
        self, used: int, final: bool, confidence: float, steps: "_Steps"
    ) -> Look:
        """Decide one look on the first ``used`` rounds."""
        b, c = self._values(used, lambda s: s.value)
        qb, qc = self._steps(used)
        self.guardrail_looks = self._evaluate_guardrails(used, final)
        return decide_look(
            b[np.newaxis, :],
            c[np.newaxis, :],
            direction=self.spec.metric.direction,
            confidence=confidence,
            min_effect=self.spec.min_effect,
            calibrated=CALIBRATED,
            final=np.array([final]),
            resolution=Resolution(
                baseline_step=qb[np.newaxis, :],
                candidate_step=qc[np.newaxis, :],
                check_continuity=steps.continuity,
            ),
            keep_blocked=np.array([self._keep_blocked()]),
            procedure=self.procedure,
            candidate_first=self._candidate_first(used)[np.newaxis, :],
        )

    def _candidate_first(self, used: int) -> NDArray[np.bool_]:
        """Whether the candidate ran first in each of ``used`` rounds.

        Read from each sample's recorded ``position`` (0 ran first), not
        from the seeded plan, so it is what actually ran, including for
        rounds restored from a journal.
        """
        return np.array(
            [
                next(s for s in m.samples if s.position == 0).version
                == "candidate"
                for m in self.measured[:used]
            ],
            dtype=np.bool_,
        )

    def _families(self) -> frozenset[str]:
        """Runtime families the probe identified in either tree."""
        return frozenset(
            e.runtime_family
            for e in self.environments.values()
            if e.runtime_family is not None
        )

    def _fixed_blocker(self) -> bool:
        """Whether something no further round lifts withholds a KEEP.

        `keep_blocked` on the spec, and what this run found out: a gate
        ``not_available``, a counter that fell back to wall time, a
        value of unknown resolution, a guardrail that is
        ``unmeasurable``.
        """
        return (
            keep_blocked(self.spec)
            or _calibration_blocker(
                self.spec, self.class_problem, self._families()
            )
            is not None
            or self.fallback_reason is not None
            or self._resolution_unknown()
            or any(g.status == "not_available" for g in self.gates.values())
            or self._guardrails_with("unmeasurable")
        )

    def _guardrails_with(self, status: str) -> bool:
        """Whether a guardrail has ``status`` at this look."""
        return any(
            str(e.look.status[0]) == status
            for e in self.guardrail_looks.values()
            if e.look is not None
        ) or (
            status == "unmeasurable"
            and any(e.look is None for e in self.guardrail_looks.values())
        )

    def _calibration(
        self,
    ) -> tuple[CalibrationStatus, tuple[Text, ...]]:
        """Calibration of this run's verdicts: see `_calibration`."""
        return _calibration(self.spec, self.class_problem, self._families())

    def _keep_blocked(self) -> bool:
        """Whether a KEEP is withheld at this look, for any reason.

        A fixed blocker, or a guardrail breached or not established
        yet; the last two are the guardrail step's to separate
        (`_ends_rule_2`).
        """
        return (
            self._fixed_blocker()
            or self._guardrails_with("breached")
            or self._guardrails_with("not_established")
        )

    def _ends_rule_2(self, look: Look) -> bool:
        """Whether a rule 2 look that does not keep ends the comparison.

        By `perfproof.stats.decision.guardrail_actions`: a breach is a
        REJECT, and a blocker no round lifts is INCONCLUSIVE now; a
        guardrail only not established holds the KEEP back and the
        comparison goes on while budget remains.
        """
        if int(look.rule[0]) != 2:
            return False
        step = guardrail_actions(
            np.int8(2),
            breached=self._guardrails_with("breached"),
            fixed=self._fixed_blocker(),
            pending=self._guardrails_with("not_established"),
            budget_remains=True,
        )
        return str(step.action) in ("REJECT", "INCONCLUSIVE")

    def _evaluate_guardrails(
        self, used: int, final: bool
    ) -> "dict[str, _GuardrailEvaluation]":
        """Evaluate each applicable guardrail on the first rounds.

        One evaluation per guardrail, replacing the last look's. A
        guardrail with no value to measure, or a round missing its
        value, is ``unmeasurable`` with the reason, never measured on
        what is left.
        """
        evaluated: dict[str, _GuardrailEvaluation] = {}
        for g in self.spec.guardrails:
            if not g.applicable:
                continue
            data = self._guardrail_data(g, used)
            if isinstance(data, str):
                evaluated[g.metric] = _GuardrailEvaluation(None, data)
                continue
            b, c, qb, qc = data
            evaluated[g.metric] = _GuardrailEvaluation(
                evaluate_guardrail(
                    b[np.newaxis, :],
                    c[np.newaxis, :],
                    direction=g.direction,
                    allowance=g.allowance,
                    limits=tuple(float(x.value) for x in g.limits),
                    confidence=self.guardrail_confidence,
                    procedure=self.guardrail_procedure,
                    final=np.array([final]),
                    candidate_first=self._candidate_first(used)[np.newaxis, :],
                    baseline_step=qb[np.newaxis, :],
                    candidate_step=qc[np.newaxis, :],
                    quantile=(
                        0.5 if g.limit_quantile is None else g.limit_quantile
                    ),
                ),
                "",
            )
        return evaluated

    def _guardrail_data(self, g: GuardrailSpec, used: int) -> "_Columns | str":
        """Return a guardrail's values and steps per arm, or why not.

        ``(baseline, candidate, baseline steps, candidate steps)`` over
        the first ``used`` rounds, or the reason it is unmeasurable.
        """
        reason = _no_source(g.metric, g.unit)
        if reason is not None:
            return reason
        if g.metric == "peak_memory":
            rss = self._rss_values(used)
            if isinstance(rss, str):
                return rss
            page = np.full(used, float(resource.getpagesize()))
            return (rss[0], rss[1], page, page)
        return self._reported_values(g.metric, used)

    def _rss_values(
        self, used: int
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]] | str:
        """Return both arms' peak sizes, or why one is unknown."""
        samples = [s for m in self.measured[:used] for s in m.samples]
        unknown = sum(s.max_rss_bytes is None for s in samples)
        if unknown:
            return (
                f"peak resident size is unknown for {unknown} of "
                f"{len(samples)} executions: Linux counts the launching "
                "process's own peak for a child, and a benchmark whose "
                "peak does not exceed the launch helper's own few "
                "megabytes can't be told apart from it"
            )
        b, c = self._values(used, lambda s: float(s.max_rss_bytes or 0))
        return b, c

    def _reported_values(self, metric: str, used: int) -> "_Columns | str":
        """Return both arms' reported values and steps, or why not.

        A round whose line left ``metric`` out in either arm makes the
        guardrail unmeasurable, as an unknown peak size does: the
        rounds that remain would be a selection.
        """
        found: dict[Arm, list[tuple[float, float]]] = {
            arm: [] for arm in _ARMS
        }
        unknown = 0
        for m in self.measured[:used]:
            for arm in _ARMS:
                pair = m.reported.get(arm, {}).get(metric)
                if pair is None:
                    unknown += 1
                else:
                    found[arm].append(pair)
        if unknown:
            return (
                f"{metric} is missing from the PERFPROOF line of "
                f"{unknown} of {2 * used} executions, so it is unknown "
                "there and is not measured on the rounds that have it"
            )

        def column(arm: Arm, i: int) -> NDArray[np.float64]:
            return np.array([p[i] for p in found[arm]], dtype=np.float64)

        columns = (
            column("baseline", 0),
            column("candidate", 0),
            column("baseline", 1),
            column("candidate", 1),
        )
        coarse = [bool(continuity_fails(v)) for v in columns[:2]]
        if any(coarse):
            distinct = [len(set(v.tolist())) for v in columns[:2]]
            arms = " and ".join(
                f"the {arm} ({d} distinct)"
                for arm, d, bad in zip(_ARMS, distinct, coarse, strict=True)
                if bad
            )
            return (
                f"{metric} looks discrete in {arms} over {used} rounds, "
                "fewer distinct values than half the rounds, so its "
                "resolution can't be trusted and it is not judged on "
                "them; print more digits"
            )
        return columns

    def _metric_kind(self) -> MetricKind:
        """Return the kind of value produced: wall on a fallback."""
        if self.fallback_reason is not None:
            return _FALLBACK_METRIC.kind
        return self.spec.metric.kind

    def _resolution_unknown(self) -> bool:
        """Whether a ``reported`` value's resolution could not be read.

        A printed step is always positive, so a recorded step of 0 for
        a metric whose steps are read back marks one that could not be.
        That holds for the primary metric and for each reported
        guardrail value.
        """
        return (
            self.spec.metric.kind == "reported"
            and any(
                step == 0.0 for m in self.measured for step in m.steps.values()
            )
        ) or any(
            step == 0.0
            for m in self.measured
            for found in m.reported.values()
            for _, step in found.values()
        )

    def _note_resolution_unknown(self) -> None:
        """Warn once when a value's resolution was unknown."""
        if self._resolution_unknown():
            self.warnings.append(
                RunWarning(
                    code="resolution_unknown",
                    message=(
                        "the resolution of a reported value could not be "
                        "read back from the benchmark's output, so its "
                        "interval is not widened for it and a KEEP is "
                        "withheld; print a fixed number of digits or "
                        "declare a resolution"
                    ),
                )
            )

    def _values(
        self, used: int, field: Callable[[Sample], float]
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """Return both arms' values in the first ``used`` rounds."""
        out: dict[Arm, list[float]] = {arm: [] for arm in _ARMS}
        for m in self.measured[:used]:
            for s in m.samples:
                out[s.version].append(field(s))
        return (
            np.array(out["baseline"], dtype=np.float64),
            np.array(out["candidate"], dtype=np.float64),
        )

    def _steps(
        self, used: int
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """Resolution steps of the first ``used`` rounds, per arm."""
        rows = self.measured[:used]
        return (
            np.array([m.steps["baseline"] for m in rows], dtype=np.float64),
            np.array([m.steps["candidate"] for m in rows], dtype=np.float64),
        )

    def _finish_gate(self) -> CompareResult:
        """Record a gate REJECT, decided by rule 1."""
        listed = self._listed_gates()
        decision = decide(
            listed,
            None,
            min_effect=self.spec.min_effect,
            budget_remains=False,
            calibrated=CALIBRATED,
        )
        if decision.gate is None or decision.reason is None:
            msg = "a gate REJECT was recorded with no failed gate"
            raise self.stop("internal_fault", msg)
        verdict = Verdict(
            outcome="REJECT",
            reason=decision.reason,
            rule=1,
            gate=decision.gate,
            rounds=len(self.measured),
            looks=self.looks,
            procedure=self.procedure,
            target_class=self.spec.target_class,
            calibration=self._calibration()[0],
            calibration_conditions=self._calibration()[1],
            diagnostics=Diagnostics(
                procedure_uncalibrated=decision.procedure_uncalibrated
            ),
        )
        return self._write(verdict, listed, ())

    def _finish_look(self, ended: _Ended) -> CompareResult:
        """Record the verdict of the last look, or of no look."""
        look = ended.look
        diagnostics, interval, estimate = self._statistics(ended)
        if look is not None:
            rule = int(look.rule[0])
            if rule == 1:
                self.warnings.append(
                    RunWarning(
                        code="coarse_resolution",
                        message=(
                            "a value was no larger than its resolution, or "
                            "a reported metric looked discrete; print more "
                            "digits or do more work per execution"
                        ),
                    )
                )
            elif rule not in (2, 3, 5):
                msg = (
                    f"the last look gave rule {rule}; a comparison ends on "
                    "rule 1 (resolution), 2, 3 or 5"
                )
                raise self.stop("internal_fault", msg)
        verdict = self._look_verdict(ended, diagnostics, interval, estimate)
        if self.time_limit_hit:
            self.warnings.append(
                RunWarning(
                    code="time_limit_reached",
                    message=(
                        f"the run's time limit came before its budget; it "
                        f"ended after {len(self.measured)} rounds"
                        + (
                            f", and its final look used {ended.used} of "
                            "them (complete order pairs only)"
                            if ended.look is not None
                            else ", too few for any look"
                        )
                    ),
                )
            )
        guardrails = tuple(
            self._guardrail_result(g) for g in self.spec.guardrails
        ) + tuple(
            GuardrailResult(
                metric=metric,
                status="not_declared",
                reason=(
                    f"the spec does not declare {metric}, so nothing was "
                    "checked and a KEEP is withheld; declare it, with an "
                    "allowance, in a new spec"
                ),
            )
            for metric in undeclared_guardrails(self.spec)
        )
        return self._write(verdict, self._listed_gates(), guardrails)

    def _look_verdict(
        self,
        ended: _Ended,
        diagnostics: Diagnostics,
        interval: Interval | None,
        estimate: float | None,
    ) -> Verdict:
        """Build the verdict of the last look, or of no look.

        Rule 2 keeps unless a blocker holds it (`_keep_blocked`): a
        breached guardrail is then a REJECT with reason ``guardrail``,
        anything else INCONCLUSIVE. Rule 3 is the performance REJECT,
        with its regression flag. With no look, or rule 5, the
        comparison is INCONCLUSIVE.
        """
        look = ended.look
        rule = 5 if look is None else int(look.rule[0])
        reject = False
        reason: Reason = "insufficient_evidence"
        outcome: Outcome = "INCONCLUSIVE"
        if rule == 2:
            if self._guardrails_with("breached"):
                outcome, reason = "REJECT", "guardrail"
            elif not self._keep_blocked() and self.workload_problem is None:
                # An unconfirmed workload relabels a KEEP and changes
                # nothing before it: the looks and stopping are those
                # of a confirmed one.
                outcome, reason = "KEEP", "performance"
        elif rule == 3:
            outcome, reason, reject = "REJECT", "performance", True
        return Verdict(
            outcome=outcome,
            reason=reason,
            rule=cast("Rule", rule if rule in (2, 3) else 5),
            estimate=estimate,
            interval=interval,
            rounds=len(self.measured),
            looks=ended.looks,
            procedure=self.procedure,
            regression=reject
            and look is not None
            and bool(look.regression[0]),
            target_class=self.spec.target_class,
            calibration=self._calibration()[0],
            calibration_conditions=self._calibration()[1],
            diagnostics=diagnostics,
        )

    def _guardrail_result(self, g: GuardrailSpec) -> GuardrailResult:
        """Record one declared guardrail as the last look left it."""
        if not g.applicable:
            return GuardrailResult(
                metric=g.metric, status="not_applicable", reason=g.reason
            )
        measure = (
            PEAK_MEMORY_MEASURE
            if g.metric == "peak_memory"
            else REPORTED_MEASURE.format(metric=g.metric)
        )
        found = self.guardrail_looks.get(g.metric)
        if found is None or found.look is None:
            reason = _NO_LOOK if found is None else found.reason
            return GuardrailResult(
                metric=g.metric,
                status="unmeasurable",
                reason=reason,
                measure=measure,
            )
        recorded = guardrail_result(
            g, found.look, 0, self.guardrail_confidence
        )
        return recorded.model_copy(update={"measure": measure})

    def _statistics(
        self, ended: _Ended
    ) -> tuple[Diagnostics, Interval | None, float | None]:
        """Diagnostics, interval and estimate of the deciding look."""
        look, used = ended.look, ended.used
        n = used if look is not None else len(self.measured)
        if n < 2:
            return (
                Diagnostics(procedure_uncalibrated=not CALIBRATED),
                None,
                None,
            )
        spec = self.spec
        b, c = self._values(n, lambda s: s.value)
        with np.errstate(over="ignore", under="ignore"):
            ratios = (
                b / c if spec.metric.direction == "lower_is_better" else c / b
            )
        in_range = bool(
            np.all(np.isfinite(ratios) & (ratios >= np.finfo(np.float64).tiny))
        )
        first = self._candidate_first(n)
        d = np.log(ratios) if in_range else None
        # Identical log ratios have no autocorrelation, whatever residue
        # the mean leaves, as in the gate of `decide_look`.
        lag1 = (
            float(lag1_autocorrelation(d, first))
            if d is not None and np.ptp(d) > 0
            else math.nan
        )
        if d is not None:
            self._shape_warnings(d, lag1)
        confidence = ended.confidence
        interval: Interval | None = None
        estimate: float | None = None
        if (
            look is not None
            and int(look.rule[0]) in (2, 3, 5)
            and np.isfinite(look.lower[0])
            and np.isfinite(look.upper[0])
        ):
            interval = Interval(
                lower=float(look.lower[0]),
                upper=float(look.upper[0]),
                confidence=confidence,
            )
            estimate = self._estimate(b, c, first, confidence)
        if (
            interval is None
            and look is not None
            and int(look.rule[0]) == 5
            and n >= self._min_interval_rounds()
        ):
            self.warnings.append(
                RunWarning(
                    code="interval_unrepresentable",
                    message=(
                        "a ratio or an interval bound of the last look "
                        "fell outside the range of a float (underflowed to "
                        "0 or overflowed), so no interval is recorded and "
                        "the look decides nothing; the values are extreme "
                        "or spread over hundreds of orders of magnitude"
                    ),
                )
            )
        wall, disagree = self._wall(n, confidence, interval)
        diagnostics = Diagnostics(
            procedure_uncalibrated=not CALIBRATED,
            metric_disagreement=disagree,
            wall_interval=wall,
            lag1_autocorrelation=None if math.isnan(lag1) else lag1,
            autocorrelated_rounds=_gate_flag(
                look, "autocorrelated_rounds", self.procedure
            ),
            outlier_rounds=_gate_flag(look, "outlier_rounds", self.procedure),
            cv_baseline=_cv(b),
            cv_candidate=_cv(c),
            sd_log_ratio=None if d is None else float(np.std(d, ddof=1)),
        )
        return diagnostics, interval, estimate

    def _min_interval_rounds(self) -> int:
        """Fewest rounds at which the procedure has an interval."""
        if self.procedure == "hac_lf":
            return HAC_MIN_N
        return 2 * BATCH_BLOCK

    def _estimate(
        self,
        b: NDArray[np.float64],
        c: NDArray[np.float64],
        first: NDArray[np.bool_],
        confidence: float,
    ) -> float:
        """Point estimate of the run's procedure on the first rounds."""
        direction = self.spec.metric.direction
        if self.procedure == "hac_lf":
            return float(
                hac_lf_interval(
                    b[np.newaxis, :],
                    c[np.newaxis, :],
                    direction=direction,
                    confidence=confidence,
                    candidate_first=first[np.newaxis, :],
                    mode="one_host",
                ).interval.estimate[0]
            )
        return float(
            t_interval(
                b,
                c,
                direction=direction,
                confidence=confidence,
                block=BATCH_BLOCK,
            ).estimate
        )

    def _wall(
        self, n: int, confidence: float, primary: Interval | None
    ) -> tuple[Interval | None, bool | None]:
        """Compute the wall-clock interval of a ``reported`` metric.

        Returns the interval, widened by the wall clock's resolution,
        and ``metric_disagreement``: one interval shows the candidate
        faster (``L > 1``) and the other excludes that (``U <= 1``).
        Both are ``None`` when not computed.
        """
        if self.spec.metric.kind != "reported" or primary is None:
            return None, None
        b, c = self._values(n, lambda s: s.wall_seconds)
        if np.any(b <= 0) or np.any(c <= 0):
            return None, None
        step = self.wall_step
        look = decide_look(
            b[np.newaxis, :],
            c[np.newaxis, :],
            direction="lower_is_better",
            confidence=confidence,
            min_effect=self.spec.min_effect,
            calibrated=CALIBRATED,
            final=np.array([True]),
            resolution=Resolution(
                baseline_step=step, candidate_step=step, check_continuity=False
            ),
            procedure=self.procedure,
            candidate_first=self._candidate_first(n)[np.newaxis, :],
        )
        if int(look.rule[0]) == 1 or not (
            np.isfinite(look.lower[0]) and np.isfinite(look.upper[0])
        ):
            return None, None
        wall = Interval(
            lower=float(look.lower[0]),
            upper=float(look.upper[0]),
            confidence=confidence,
        )
        disagree = (primary.lower > 1 and wall.upper <= 1) or (
            wall.lower > 1 and primary.upper <= 1
        )
        return wall, disagree

    def _shape_warnings(self, d: NDArray[np.float64], lag1: float) -> None:
        """Warn on lag-1 autocorrelation above 0.3 and skew beyond 2."""
        if not math.isnan(lag1) and lag1 > _AUTOCORRELATION_WARN:
            self.warnings.append(
                RunWarning(
                    code="lag1_autocorrelation_high",
                    message=(
                        f"lag-1 autocorrelation of the log ratios is "
                        f"{lag1:.3f}, above {_AUTOCORRELATION_WARN}"
                    ),
                )
            )
        centred = d - np.mean(d)
        m2 = float(np.mean(centred**2))
        if d.size >= 3 and m2 > 0:
            skew = float(np.mean(centred**3)) / m2**1.5
            if abs(skew) > _SKEW_WARN:
                self.warnings.append(
                    RunWarning(
                        code="skewed_log_ratio",
                        message=(
                            f"skewness of the log ratios is {skew:.3f}; the "
                            "t interval under-covers on the skewed side"
                        ),
                    )
                )

    def _listed_gates(self) -> list[GateResult]:
        return [self.gates[g] for g in GATE_PRECEDENCE if g in self.gates]

    def _write(
        self,
        verdict: Verdict,
        gates: Sequence[GateResult],
        guardrails: tuple[GuardrailResult, ...],
    ) -> CompareResult:
        samples_path = None
        if self.measured:
            _save_samples(self.run_dir / SAMPLES_NAME, self.measured)
            samples_path = SAMPLES_NAME
        record = RunRecord(
            run_id=self.run_dir.name,
            perfproof_version=__version__,
            started_at=self.started,
            finished_at=max(datetime.now(UTC), self.started),
            spec=self.spec,
            baseline_commit=self.baseline_commit,
            order_seed=self.order_seed,
            verdict=verdict,
            gates=tuple(gates),
            guardrails=guardrails,
            machines=(self.machine,),
            environments=tuple(self.environments[arm] for arm in _ARMS),
            warnings=tuple(self.warnings),
            samples_path=samples_path,
            measured_metric=(
                None if self.fallback_reason is None else _FALLBACK_METRIC
            ),
        )
        record_path, report_path = write(record, self.run_dir)
        return CompareResult(
            record=record,
            run_dir=self.run_dir,
            record_path=record_path,
            report_path=report_path,
        )


class _Steps:
    """The resolution step of each value, per "Resolution"."""

    def __init__(
        self, spec: BenchmarkSpec, wall_step: float, kind: MetricKind
    ) -> None:
        self.kind = kind
        self.continuity = self.kind == "reported"
        self.fixed: float | None = {
            "wall": wall_step,
            "cpu": _CPU_STEP_SECONDS,
            "counter": 1.0,
            "reported": None,
        }[self.kind]

    def of(self, done: Round) -> dict[Arm, float]:
        """Return each arm's step in one finished round.

        A ``reported`` value whose printed digits can't be read back
        has an unknown resolution: its step is 0, which no readable
        value has, and `_Run` records ``resolution_unknown`` and
        withholds a KEEP.
        """
        steps: dict[Arm, float] = {}
        for ex in done.executions:
            if self.fixed is not None:
                steps[ex.arm] = self.fixed
                continue
            step = _printed_step(ex)
            steps[ex.arm] = 0.0 if step is None else step
        return steps


def _side_effects(
    spec: BenchmarkSpec, trees: Mapping[Arm, Path], env: Mapping[str, str]
) -> GateResult:
    """Run the side-effect guard on both trees; one gate for the run.

    The first tree that fails decides; otherwise a waiver from either
    tree, then the baseline's pass, stands for both.
    """
    results = [side_effects_gate(spec, trees[arm], env) for arm in _ARMS]
    for status in ("failed", "waived"):
        for arm, result in zip(_ARMS, results, strict=True):
            if result.status == status:
                return result.model_copy(
                    update={"detail": f"{arm} tree: {result.detail}"}
                )
    return results[0]


def _same_host_gate(
    first: MachineFingerprint, last: MachineFingerprint | None
) -> GateResult:
    """Check every round ran on the host fingerprinted at the start.

    A host identity that could not be read is ``not_available``, not
    a failure: nothing shows the host changed. A readable identity that
    differs at the end fails the gate (on Linux a reboot changes it).
    """
    if first.host_id is None:
        reason = first.missing.get("host_id", "no reason recorded")
        return GateResult(
            gate="same_host",
            status="not_available",
            detail=(
                f"the host identity could not be read ({reason}), so "
                "whether every round ran on one host was not verified"
            ),
        )
    if last is None:
        return GateResult(
            gate="same_host",
            status="passed",
            detail=f"host {first.host_id} before the first round",
        )
    if last.host_id is None:
        reason = last.missing.get("host_id", "no reason recorded")
        return GateResult(
            gate="same_host",
            status="not_available",
            detail=(
                f"the host identity could not be read after the last round "
                f"({reason}), so whether it stayed {first.host_id} was not "
                "verified"
            ),
        )
    if last.host_id != first.host_id:
        return GateResult(
            gate="same_host",
            status="failed",
            detail=(
                f"the host was {first.host_id} before the first round and "
                f"{last.host_id} after the last"
            ),
        )
    return GateResult(
        gate="same_host",
        status="passed",
        detail=f"every round ran on host {first.host_id}",
    )


def _unprobed(
    arm: Arm, env: Mapping[str, str], reason: str
) -> TreeEnvironment:
    """Record a tree whose runtime was not probed, with the reason."""
    return TreeEnvironment(
        version=arm,
        env_names=tuple(env),
        missing={
            "runtime_kind": reason,
            "runtime_executable": reason,
            "runtime_version": reason,
            "linked_libraries": reason,
            "dependency_locks": reason,
        },
    )


def _execution_gate(
    failure: ExecutionError, argv: tuple[str, ...], what: str
) -> GateResult:
    """Fail the correctness gate for a failed candidate execution.

    A process that could not be started has no exit status; it is
    recorded as 127, the shell's status for a command that can't run,
    and the detail says it never started.
    """
    out, cut_out = _tail(failure.stdout)
    err, cut_err = _tail(failure.stderr)
    code = _LAUNCH_EXIT if failure.exit_code is None else failure.exit_code
    return GateResult(
        gate="correctness",
        status="failed",
        detail=(
            f"the candidate failed {what} ({failure.label}): {failure.reason}"
        ),
        output=CommandOutput(
            command=argv,
            exit_code=code,
            timed_out=failure.cause == "timeout",
            stdout=out,
            stderr=err,
            truncated=cut_out or cut_err,
        ),
    )


def _launch_gate(
    failure: ExecutionError, argv: tuple[str, ...], what: str
) -> GateResult:
    """Fail the runtime gate for a candidate that could not be started.

    The baseline ran the program and the candidate's tree could not
    start it (a missing program or interpreter, a bad interpreter
    line), so the two arms do not run the same program: integrity, as
    "Integrity" in ``docs/method.md`` says. A program that started and
    then exited non-zero is the correctness gate's, not this one.
    """
    return GateResult(
        gate="runtime",
        status="failed",
        detail=(
            f"the arms do not run the same program: the baseline ran "
            f"{argv[0]!r} and the candidate could not start it from its "
            f"tree during {what} ({failure.label}): {failure.reason}"
        ),
        differences=(
            Difference(
                item="launch",
                baseline="started",
                candidate=failure.reason,
            ),
        ),
    )


def _tail(path: Path) -> tuple[str, bool]:
    """Return a log's last `OUTPUT_TAIL_BYTES` and if it was cut."""
    try:
        with path.open("rb") as f:
            size = f.seek(0, 2)
            f.seek(max(0, size - OUTPUT_TAIL_BYTES))
            data = f.read()
    except OSError:
        return "", False
    return data.decode("utf-8", errors="replace"), size > OUTPUT_TAIL_BYTES


def _cv(values: NDArray[np.float64]) -> float | None:
    """Coefficient of variation, ``None`` if not a finite float.

    Scaled by the largest value first, so a value near the float
    maximum cannot overflow the variance. The result is unchanged by
    the scale.
    """
    scaled = values / np.max(values)
    cv = float(np.std(scaled, ddof=1) / np.mean(scaled))
    return cv if math.isfinite(cv) else None


def _gate_flag(
    look: Look | None, name: str, procedure: Procedure
) -> bool | None:
    """Whether the diagnostic gate named ``name`` at ``look``.

    ``None`` when no look was taken, or the procedure is ``hac_lf``,
    which has no gate: the gate was not evaluated.
    """
    if look is None or procedure == "hac_lf":
        return None
    return str(look.withheld[0]) == name


def keep_blocked(spec: BenchmarkSpec) -> bool:
    """Whether the spec rules out a KEEP, whatever the interval shows.

    True when a guardrail can never be shown compliant here (applicable
    with no value to measure: ``peak_memory`` in a unit other than
    bytes; or a default one left undeclared), or when the look schedule
    is outside the calibrated ones (more than one look at 120
    observations or more, see `late_looks`), or when the schedule or
    ``alpha`` is outside the calibrated domain
    (`perfproof.stats.sequential.in_calibrated_domain`). A measured or
    reported guardrail does not block here: whether it passes is
    decided at each look from its values, and a reported one missing
    from a round is
    ``unmeasurable`` then. Rule 2 then ends INCONCLUSIVE at that look;
    REJECTs are unaffected.

    Parameters
    ----------
    spec : perfproof.schema.BenchmarkSpec
        The spec being compared.

    Returns
    -------
    bool
        Whether rule 2 must not keep.
    """
    return (
        any(
            g.applicable and _no_source(g.metric, g.unit) is not None
            for g in spec.guardrails
        )
        or bool(undeclared_guardrails(spec))
        or late_looks(spec) > 1
        or _calibration_blocker(spec) is not None
    )


def _class_problem(spec: BenchmarkSpec, root: Path | None) -> str | None:
    """Why the project model does not confirm the spec's class, if so.

    ``None`` only when ``root`` (the main checkout, where the project
    model lives) holds a readable model with a ``confirmed`` target
    entry of the spec's class that no invalidation trigger has fired
    for: the check `perfproof project check` makes
    (`perfproof.project.staleness.find_stale`), run read-only, so the
    model is never rewritten here. Otherwise the reason: no root, no
    model, an unreadable one, no such target, a target not confirmed
    (``proposed``, ``stale``, ``retired``), or one whose evidence
    changed since it was confirmed.
    """
    cls = spec.target_class
    if cls is None:
        return "no target class is declared"
    if root is None:
        return "no project root was given"
    try:
        model = load_model(root)
    except (ProjectError, OSError) as exc:
        return f"the project model is unreadable ({exc})"
    if model is None:
        return "there is no project model"
    own = [t for t in model.targets if t.target_class == cls]
    if not own:
        return f"the model has no {cls} target"
    confirmed = [t for t in own if t.status == "confirmed"]
    if not confirmed:
        states = ", ".join(sorted({t.status for t in own}))
        return f"its {cls} target is {states}, not confirmed"
    ids = {t.id for t in confirmed}
    findings, unknown = find_stale(root, model, host_id=None, only=ids)
    changed = {f.entry_id: f for f in findings}
    unchecked = {i for i, _ in unknown}
    if all(i in changed or i in unchecked for i in ids):
        first = next((changed[i] for i in sorted(ids) if i in changed), None)
        if first is not None:
            return (
                f"its {cls} target is stale: {first.trigger} ({first.detail})"
            )
        return f"its {cls} target could not be checked for staleness"
    return None


def _workload_problem(spec: BenchmarkSpec, root: Path | None) -> str | None:
    """Why the project model does not back this benchmark's workload.

    ``None`` only when `perfproof.project.guard.require_confirmed`
    accepts the workload named like the spec for this spec's hash (the
    workload, its target, its goal and every live constraint
    ``confirmed``, none changed on disk since, none bound to another
    machine) and `_alignment_problems` finds none. Otherwise every
    problem found, in one string: no root, no model, an unreadable one,
    a missing or retired workload, an entry not confirmed or stale, a
    changed spec, a workload not bound to a spec, a spec of another
    class than the workload's target, a goal that disagrees with the
    spec's metric. Read-only.
    """
    if root is None:
        return "no project root was given"
    problems: list[str] = []
    try:
        require_confirmed(root, spec.name, spec_hash=spec.spec_hash)
    except NotConfirmedError as exc:
        problems = [
            f"{p.entry_id} ({p.kind}) {p.reason}: {p.detail}"
            for p in exc.problems
        ]
    except (ProjectError, OSError) as exc:
        return f"the project model is unreadable ({exc})"
    problems.extend(_alignment_problems(spec, root))
    return "; ".join(problems) or None


def _alignment_problems(spec: BenchmarkSpec, root: Path) -> list[str]:
    """Name where the workload, target and goal disagree with the spec.

    Each check runs on whatever entries exist, so a missing or
    unconfirmed one does not hide the others: a confirmed workload with
    no ``spec_hash`` was confirmed before its benchmark was defined (a
    bound hash that differs is `require_confirmed`'s ``spec_changed``);
    the spec's ``target_class`` must be the workload's target's class; a
    confirmed goal's metric, direction and unit must be the spec's.
    """
    try:
        model = load_model(root)
    except (ProjectError, OSError):
        return []
    if model is None:
        return []
    workload = next(
        (
            w
            for w in model.workloads
            if w.id == spec.name and w.status != "retired"
        ),
        None,
    )
    if workload is None:
        return []
    found: list[str] = []
    if workload.status == "confirmed" and workload.spec_hash is None:
        found.append(
            f"{workload.id} (workload) confirmed before its benchmark was "
            f"defined: confirm again with `perfproof project confirm "
            f"{workload.id} [--spec-hash HASH]`"
        )
    target = next(
        (t for t in model.targets if t.id == workload.target_id), None
    )
    if target is not None and spec.target_class != target.target_class:
        found.append(
            f"{workload.id} (workload) runs on target {target.id} of class "
            f"{target.target_class}, but the spec declares "
            f"{spec.target_class}"
        )
    goal = next(
        (
            g
            for g in model.goals
            if g.workload_id == workload.id and g.status == "confirmed"
        ),
        None,
    )
    mismatch = None if goal is None else _goal_mismatch(spec, goal)
    if mismatch is not None:
        found.append(mismatch)
    return found


_DEFAULT_OBJECTIVE: Final[Mapping[str, str]] = MappingProxyType(
    {"wall": "wall_time", "cpu": "cpu_time"}
)


def _goal_mismatch(spec: BenchmarkSpec, goal: GoalEntry) -> str | None:
    """Name each way the spec's metric contradicts the confirmed goal.

    The goal says what faster means (metric name, direction, unit); a
    KEEP on a spec that measures something else would claim the wrong
    thing. ``None`` when all three agree. A spec that names no
    objective is known only for ``wall`` and ``cpu``; for another
    kind its name is unknown and counts as a mismatch.
    """
    objective = spec.objective or _DEFAULT_OBJECTIVE.get(spec.metric.kind)
    found: list[str] = []
    if objective != goal.metric:
        found.append(
            f"metric is {objective or 'not named'} in the spec, "
            f"{goal.metric} in the confirmed goal"
        )
    if spec.metric.direction != goal.direction:
        found.append(
            f"direction is {spec.metric.direction} in the spec, "
            f"{goal.direction} in the confirmed goal"
        )
    if spec.metric.unit != goal.unit:
        found.append(
            f"unit is {spec.metric.unit} in the spec, {goal.unit} in the "
            "confirmed goal"
        )
    if not found:
        return None
    return f"{goal.id} (goal) does not match the spec: " + "; ".join(found)


def _calibration_blocker(
    spec: BenchmarkSpec,
    class_problem: str | None = None,
    families: frozenset[str] = frozenset(),
) -> tuple[str, str] | None:
    """Why the spec's verdict cannot be calibrated, as code and text.

    ``None`` when it can: ``alpha`` and schedule in "The calibrated
    domain" of ``docs/method.md``, a declared target class, and that
    class's own schedule, for ``node`` and ``jvm`` a ``wall`` primary
    metric, and (``class_problem`` is None) a project
    model that confirms the class. Checked in that order, so the first
    reason is the one named; `keep_blocked` passes the default None
    because the model is read once per run, in `_class_problem`. Each
    code is in `perfproof.schema.KEEP_BLOCKING_WARNINGS`.
    """
    looks, rounds = spec.looks, spec.budget.max_rounds
    shape = (
        f"(n0 {looks.n0}, k {looks.k}, {looks.max_looks} looks, up to "
        f"{rounds} rounds)"
    )
    if is_gpu_kernels_schedule(looks, rounds) and spec.alpha == DEFAULT_ALPHA:
        return (
            "schedule_uncalibrated",
            f"the gpu_kernels schedule {shape} has no real GPU traces yet, "
            "so it is not calibrated: the comparison runs, but this "
            "verdict can never be a KEEP until they exist",
        )
    if not in_calibrated_domain(looks, rounds, spec.alpha):
        return (
            "schedule_uncalibrated",
            f"alpha {spec.alpha} with the look schedule {shape} is outside "
            "the calibrated domain (alpha 0.05 and the python_native "
            "(python_cpu, native, node, jvm), web_agentic (web_service, "
            "agentic) and llm_serving schedules): the comparison runs, "
            "but this verdict can never be a KEEP",
        )
    if spec.target_class is None:
        return (
            "target_class_unknown",
            "the spec declares no target_class, so no class's calibration "
            "can be claimed: the comparison runs, but this verdict can "
            "never be a KEEP; declare the class in a new spec",
        )
    if not is_class_schedule(spec.target_class, looks, rounds):
        return (
            "schedule_not_class_own",
            f"the look schedule {shape} is not the default schedule of "
            f"the declared target class {spec.target_class}, the one "
            "calibrated for it: the comparison runs, but this verdict can "
            "never be a KEEP",
        )
    if not metric_in_calibrated_domain(
        spec.target_class, spec.metric.kind, families
    ):
        return (
            "metric_not_calibrated",
            "KEEP is calibrated for Node and Java code only on "
            "whole-command (wall) timing; this run measured "
            f"{spec.metric.kind}. The comparison runs and the estimate and "
            "interval stand, but this verdict can never be a KEEP",
        )
    if class_problem is not None:
        return (
            "target_class_unverified",
            f"the project model does not confirm the declared target "
            f"class {spec.target_class}: {class_problem}. The comparison "
            "runs, but this verdict can never be a KEEP; confirm or "
            "refresh the target with `perfproof project`",
        )
    return None


def _calibration(
    spec: BenchmarkSpec,
    class_problem: str | None = None,
    families: frozenset[str] = frozenset(),
) -> tuple[CalibrationStatus, tuple[Text, ...]]:
    """Calibration status of the spec's verdicts, and its conditions.

    Calibrated exactly when `CALIBRATED`, the spec declares a target
    class, its schedule is that class's own, and ``alpha`` and schedule
    are in the calibrated domain; the conditions then name what the
    calibration rests on. Otherwise ``uncalibrated`` and no conditions.
    """
    cls = spec.target_class
    if (
        not CALIBRATED
        or cls is None
        or _calibration_blocker(spec, class_problem, families) is not None
    ):
        return "uncalibrated", ()
    looks, rounds = spec.looks, spec.budget.max_rounds
    procedure = procedure_for_schedule(looks, rounds, spec.alpha)
    return (
        "calibrated",
        (
            f"interval procedure {procedure}",
            f"look schedule (n0 {looks.n0}, k {looks.k}, {looks.max_looks} "
            f"looks, {rounds} rounds), the default of {cls}",
            f"alpha {spec.alpha}",
            "one host",
            "real A/A traces of the class under its own schedule "
            f"({TRACE_BASIS[cls]}), run of record seed "
            f"{CALIBRATION_SEED}",
        ),
    )


def undeclared_guardrails(spec: BenchmarkSpec) -> tuple[str, ...]:
    """List the default guardrails the spec leaves out.

    The defaults are those of `perfproof.schema.DEFAULT_GUARDRAILS` for
    the spec's ``target_class`` (every default when it declares none),
    less the ones the spec declares and the primary objective. Each is
    recorded ``not_declared`` instead of stopping the comparison.

    Parameters
    ----------
    spec : perfproof.schema.BenchmarkSpec
        The spec being compared.

    Returns
    -------
    tuple of str
        Metric names, in the order of the defaults.
    """
    declared = {g.metric for g in spec.guardrails}
    return tuple(
        m
        for m in DEFAULT_GUARDRAILS[spec.target_class]
        if m not in declared and m != spec.objective
    )


def late_looks(spec: BenchmarkSpec) -> int:
    """Count the looks the budget can take at 120 observations or more.

    "Stopping" in ``docs/method.md``: the scheduled looks at
    ``n0 + j k`` for ``j < K`` from 120 up to ``max_rounds``, plus one
    when exactly one is that long, it is not the ``K``-th, and the
    budget runs at least 2 rounds past it, since a budget that ends
    there takes a final look that replaces the remaining ones.

    Parameters
    ----------
    spec : perfproof.schema.BenchmarkSpec
        The spec whose look schedule and budget are counted, in rounds
        (one host).

    Returns
    -------
    int
        The looks at 120 observations or more. Exact for whether it is
        above 1, which is what the warning and the KEEP block use; a
        lower bound on the true count above that.
    """
    sched = spec.looks
    late = [
        n
        for j in range(sched.max_looks)
        if LAG1_MIN_BLOCKS * BATCH_BLOCK
        <= (n := sched.n0 + j * sched.k)
        <= spec.budget.max_rounds
    ]
    if len(late) != 1:
        return len(late)
    j = (late[0] - sched.n0) // sched.k
    cut = j + 1 < sched.max_looks and spec.budget.max_rounds >= late[0] + 2
    return 1 + int(cut)


def _wall_step() -> float:
    """Measure the step of the runner's wall clock, in seconds.

    The smallest positive change between consecutive
    ``perf_counter_ns`` readings, never below the declared resolution,
    plus 1 ns: the runner keeps whole nanoseconds, so a converted tick
    can read up to 1 ns short. O(`_CLOCK_READS`).
    """
    reads = [time.perf_counter_ns() for _ in range(_CLOCK_READS)]
    deltas = [b - a for a, b in itertools.pairwise(reads) if b > a]
    measured = min(deltas) if deltas else 1
    declared = time.get_clock_info("perf_counter").resolution
    return max(measured * 1e-9, declared) + 1e-9


def _printed_step(ex: Execution) -> float | None:
    """Return the place of the last printed digit of a reported value.

    Reads the one ``PERFPROOF`` line the runner already parsed, a
    bounded line at a time, and takes the literal number from it.
    Returns ``None`` if the line or its number can't be read back, or
    the number is not the value the runner recorded.
    """
    return _step_of(_reported_literals(ex.stdout), "value", ex.value)


def _guardrail_steps(ex: Execution) -> dict[str, tuple[float, float]]:
    """Return each reported guardrail value with its resolution step.

    The step is the place of the value's last printed digit, read back
    from the same ``PERFPROOF`` line as the primary's. One whose digits
    can't be read back gets step 0, which no readable value has: it is
    measured, not widened, and `_Run` records ``resolution_unknown``
    and withholds a KEEP, as for the primary.
    """
    if not ex.reported:
        return {}
    literals = _reported_literals(ex.stdout)
    out: dict[str, tuple[float, float]] = {}
    for name, value in ex.reported.items():
        step = _step_of(literals, name, value)
        out[name] = (value, 0.0 if step is None else step)
    return out


def _step_of(
    literals: Mapping[str, str] | None, key: str, value: float
) -> float | None:
    """Return the step of ``key``'s printed literal, or ``None``.

    ``None`` when the line is gone, the key is missing, or the literal
    is not the number the runner recorded.
    """
    token = None if literals is None else literals.get(key)
    if token is None:
        return None
    match = _NUMBER.fullmatch(token)
    if match is None or float(token) != value:
        return None
    digits = len(match.group(1) or "")
    exponent = int(match.group(2) or "0")
    exact = Decimal(1).scaleb(exponent - digits)
    step = float(exact)
    if Decimal(step) < exact:
        step = math.nextafter(step, math.inf)
    return step


def _reported_literals(path: Path) -> dict[str, str] | None:
    """Return each key's literal number from the ``PERFPROOF`` line."""
    try:
        with path.open("rb") as f:
            while True:
                head = f.readline(_MAX_LINE + 1)
                if not head:
                    return None
                if not head.startswith(_PREFIX):
                    while len(head) > _MAX_LINE and not head.endswith(b"\n"):
                        head = f.readline(_SKIP_CHUNK)
                    continue
                body = head.removeprefix(_PREFIX).strip()
                return _literal_values(body.decode("utf-8"))
    except (OSError, UnicodeDecodeError):
        return None


def _literal_values(text: str) -> dict[str, str] | None:
    """Return a JSON object's numbers as the literal text they were."""

    def keep(literal: str) -> str:
        return literal

    try:
        parsed: object = json.loads(text, parse_float=keep, parse_int=keep)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    return {
        k: v
        for k, v in cast("dict[str, object]", parsed).items()
        if isinstance(v, str)
    }


def _save_samples(path: Path, measured: Sequence[RoundEntry]) -> None:
    """Write every measured sample to ``samples.npz``.

    One entry per sample, in the order run: ``round``, ``position``,
    ``candidate`` (1 for the candidate), ``session``, ``value``,
    ``step`` (its resolution), ``wall_seconds``, ``cpu_seconds`` and
    ``max_rss_bytes`` (NaN where unknown), ``command_hash``, and one
    ``reported_<metric>`` column per reported guardrail (NaN where the
    execution's line left it out). Numeric and fixed-width text arrays
    only, so it loads with ``allow_pickle=False``.
    """
    rows = [(m, s) for m in measured for s in m.samples]

    def column(values: list[float]) -> NDArray[np.float64]:
        return np.array(values, dtype=np.float64)

    names = sorted(
        {k for m in measured for r in m.reported.values() for k in r}
    )
    reported = {
        f"reported_{name}": column(
            [
                m.reported.get(s.version, {}).get(name, (math.nan, 0.0))[0]
                for m, s in rows
            ]
        )
        for name in names
    }
    np.savez(
        path,
        **cast("dict[str, Any]", reported),
        round=np.array([s.round for _, s in rows], dtype=np.int64),
        position=np.array([s.position for _, s in rows], dtype=np.int64),
        candidate=np.array(
            [s.version == "candidate" for _, s in rows], dtype=np.int8
        ),
        session=np.array([s.session for _, s in rows], dtype=np.int64),
        value=column([s.value for _, s in rows]),
        step=column([m.steps[s.version] for m, s in rows]),
        wall_seconds=column([s.wall_seconds for _, s in rows]),
        cpu_seconds=column(
            [
                math.nan if s.cpu_seconds is None else s.cpu_seconds
                for _, s in rows
            ]
        ),
        max_rss_bytes=column(
            [
                math.nan if s.max_rss_bytes is None else float(s.max_rss_bytes)
                for _, s in rows
            ]
        ),
        command_hash=np.array([s.command_hash for _, s in rows], dtype="<U64"),
    )
