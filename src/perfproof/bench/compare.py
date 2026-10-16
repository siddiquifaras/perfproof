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
   correctness check, runtime probe). A failure refuses the run before
   anything executes.
3. The `lock` gate against the manifest frozen with the spec.
4. Specs the thin path cannot run are refused with `CompareStoppedError`
   (see below), still before anything executes.
5. The `runtime` gate, after probing both trees' interpreters (the
   probe may run ``uv run`` in a tree), and the `procedure` and
   `same_host` gates on what is known before any round.
6. One output-producing execution of the benchmark in each tree, then
   the `correctness` gate (tests three times on the baseline, outputs
   compared). The `coverage` gate is ``not_available``: it is not
   built yet (T2.14), and that blocks KEEP. Differential checks
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
blamed, the baseline runs once more: the runner runs executions in
pairs, so this recheck is one warmup pair with the baseline tree in
both slots. If the baseline fails too, the failure is shared (a
program missing on the host, a noexec mount) and the comparison stops
as a broken baseline. A baseline failure (including a benchmark
program the baseline tree can't resolve, checked before the runtime
gate so it is never blamed on the candidate), a
`perfproof.bench.correctness.BaselineBrokenError`, and a fault in
perfproof's own process (a `perfproof.bench.runner.RunnerError` with
no arm, or an `OSError` from the correctness command) stop the
comparison with no verdict: `CompareStoppedError` is raised and no
record is written. Logs stay in the run directory.

Before calibration
------------------
`CALIBRATED` is False until the procedure passes calibration (T1.6,
T1.7). Rules 2 and 3 then never fire: every comparison that passes
its gates runs to the end of its budget, or to its ``K``-th look, and
ends INCONCLUSIVE (``insufficient_evidence``) with the diagnostic
``procedure_uncalibrated`` and its interval recorded. Only a gate
REJECT can end it otherwise, and the resolution stop can end it early
as INCONCLUSIVE.

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

What the thin path does not do yet
----------------------------------
- Guardrails (T2.15): every applicable guardrail is ``unmeasurable``,
  with that reason; one marked not applicable keeps the spec's reason.
- Balanced order: the runner draws a fair coin per round
  (`perfproof.bench.runner.round_orders`), not the balanced order
  pairs of "Observations" in ``docs/method.md``; a warning reports an
  imbalance in the rounds a look used.
- A reset command (``spec.reset``): the runner runs executions in
  pairs and offers no hook between them, so a reset cannot run before
  every execution. Such a spec is refused.
- Cross-host comparisons (``allow_cross_host``) are refused.
"""

import functools
import itertools
import json
import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final, cast

import numpy as np
from numpy.typing import NDArray

from perfproof import __version__
from perfproof.bench.correctness import (
    OUTPUT_TAIL_BYTES,
    BaselineBrokenError,
    clear_outputs,
    correctness_gate,
)
from perfproof.bench.lock import (
    environment_warnings,
    lock_gate,
    probe_environment,
    procedure_gate,
    resolve_executable,
    runtime_gate,
)
from perfproof.bench.runner import (
    Execution,
    ExecutionError,
    Round,
    Runner,
    RunnerError,
)
from perfproof.bench.side_effects import (
    benchmark_environment,
    side_effects_gate,
)
from perfproof.bench.spec import normalize_launch_path
from perfproof.machine import fingerprint, machine_warnings
from perfproof.report import write
from perfproof.schema import (
    DEFAULT_GUARDRAILS,
    Arm,
    BenchmarkSpec,
    CommandOutput,
    Diagnostics,
    GateName,
    GateResult,
    GuardrailResult,
    Interval,
    LockManifest,
    MachineFingerprint,
    Procedure,
    RunRecord,
    RunWarning,
    Sample,
    TreeEnvironment,
    Verdict,
)
from perfproof.stats.decision import GATE_PRECEDENCE, decide
from perfproof.stats.intervals import t_interval
from perfproof.stats.sequential import (
    Look,
    Resolution,
    decide_look,
    lag1_autocorrelation,
    look_confidence,
    plan_looks,
)

__all__ = [
    "CALIBRATED",
    "PROCEDURE",
    "SAMPLES_NAME",
    "CompareResult",
    "CompareStoppedError",
    "compare",
]

CALIBRATED: Final = False
"""Whether the interval procedure has passed calibration. False until
the M1 exit (T1.6 and T1.7): no KEEP and no performance REJECT."""
PROCEDURE: Final[Procedure] = "t_log_ratio"
"""The primary interval procedure of ``docs/method.md``."""
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
_GUARDRAILS_NOT_BUILT: Final = (
    "guardrails are not measured yet (plan task T2.15)"
)


class CompareStoppedError(Exception):
    """The comparison stopped, or was refused, with no verdict.

    Parameters
    ----------
    code : str
        Stable snake_case identifier: ``broken_baseline`` (the baseline
        failed, alone or together with the candidate),
        ``harness_fault`` (perfproof's own process or the host failed),
        ``unsupported_spec`` (the thin path can't run this spec) or
        ``undeclared_guardrails`` (the spec omits a default guardrail,
        so no verdict but a gate REJECT could be recorded) or
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
class _Measured:
    """One round with the resolution of each of its two values."""

    round: Round
    steps: Mapping[Arm, float]


@dataclass(frozen=True, slots=True)
class _Ended:
    """How the rounds ended: by a look, or with none taken."""

    look: Look | None
    used: int
    looks: int
    confidence: float


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
        If ``run_dir`` already exists.
    """
    run_dir.mkdir(parents=True)
    started = datetime.now(UTC)
    machine = fingerprint()
    env = benchmark_environment(spec, process_env)
    trees: dict[Arm, Path] = {
        "baseline": baseline_root,
        "candidate": candidate_root,
    }
    run = _Run(spec, lock, trees, run_dir, env, machine, started)
    run.baseline_commit = baseline_commit
    run.order_seed = order_seed
    run.wall_step = _wall_step()
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
        self.baseline_commit = ""
        self.order_seed = 0
        self.wall_step = 0.0
        self.gates: dict[GateName, GateResult] = {}
        self.warnings: list[RunWarning] = list(machine_warnings(machine))
        reason = "not probed: no command ran in this tree"
        self.environments: dict[Arm, TreeEnvironment] = {
            arm: _unprobed(arm, env, reason) for arm in _ARMS
        }
        self.measured: list[_Measured] = []
        self.looks = 0

    def stop(self, code: str, message: str) -> CompareStoppedError:
        """Build the no-verdict stop for this run."""
        return CompareStoppedError(code, message, self.run_dir)

    def execute(self) -> CompareResult:
        """Run the gates and rounds and write the record."""
        try:
            return self._execute()
        except _CandidateFailedError as failed:
            self.gates["correctness"] = failed.gate
            return self._finish_gate()

    def _execute(self) -> CompareResult:
        spec, trees = self.spec, self.trees
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
        self._refuse_unstartable_baseline()
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
        self.gates["runtime"] = runtime_gate(
            spec,
            self.environments["baseline"],
            self.environments["candidate"],
            baseline_root=trees["baseline"],
            candidate_root=trees["candidate"],
        )
        self._recheck_unknown_baseline_runtime()
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
        self.gates["coverage"] = GateResult(
            gate="coverage",
            status="not_available",
            detail="the coverage gate is not built yet (plan task T2.14)",
        )
        ended = self._rounds(rounds_runner)
        samples = [s for m in self.measured for s in m.round.samples]
        self.gates["procedure"] = self._procedure(executables, samples)
        self.gates["same_host"] = _same_host_gate(self.machine, fingerprint())
        if self._failed():
            return self._finish_gate()
        return self._finish_look(ended)

    def _failed(self) -> bool:
        return any(g.status == "failed" for g in self.gates.values())

    def _refuse_unsupported(self) -> None:
        """Refuse a spec the thin path can't run, before it executes."""
        spec = self.spec
        if spec.reset is not None:
            msg = (
                "the spec declares a reset command, which must run before "
                "every execution; the runner runs executions in pairs with "
                "no hook between them, so compare can't run it yet"
            )
            raise self.stop("unsupported_spec", msg)
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
        declared = {g.metric for g in spec.guardrails}
        missing = sorted(
            set(DEFAULT_GUARDRAILS[None]) - declared - {spec.objective}
        )
        if missing:
            msg = (
                f"the spec does not declare the default guardrails "
                f"{', '.join(missing)}; the target class is unknown, so "
                "each must be declared, applied or not applicable with a "
                "reason, before a verdict other than a gate REJECT can be "
                "recorded. Define a new spec with them and an objective."
            )
            raise self.stop("undeclared_guardrails", msg)

    def _refuse_unstartable_baseline(self) -> None:
        """Stop as a broken baseline when its program can't be found.

        Per "Failed executions" in ``docs/method.md``, a baseline that
        can't be started is a broken experiment, never a REJECT of the
        candidate. The runtime gate would fail closed on the unknown
        executable and blame the candidate, so this comes first; it
        covers a program missing from both trees too. A program missing
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
            raise self.stop("broken_baseline", msg)

    def _recheck_unknown_baseline_runtime(self) -> None:
        """Run the baseline before an unknown runtime fails the gate.

        The runtime gate fails closed when it can't identify the
        baseline's runtime, which is also what a bad interpreter line
        or a missing interpreter behind ``env`` looks like. Those are a
        broken baseline, per "Failed executions" in ``docs/method.md``,
        so the baseline runs once; if it fails, the comparison stops
        with no verdict. If it runs, the REJECT stands: an unidentified
        runtime fails closed.
        """
        runtime = self.gates["runtime"]
        if runtime.status != "failed":
            return
        if "the baseline runtime is unknown" not in runtime.detail:
            return
        self._recheck_baseline("runtime", runtime.detail)

    def _runner(
        self, spec: BenchmarkSpec, trees: Mapping[Arm, Path], logs: str
    ) -> Runner:
        """Build a runner whose logs go to ``logs`` in the run dir."""
        try:
            return Runner(
                spec,
                trees,
                self.run_dir / "logs" / logs,
                order_seed=self.order_seed,
                base_env=self.env,
            )
        except ValueError as exc:
            raise self.stop("unsupported_spec", str(exc)) from exc
        except RunnerError as exc:
            raise self.stop("unsupported_spec", str(exc)) from exc

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
            self.gates["correctness"] = correctness_gate(
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
            gate = _execution_gate(exc, self.spec.command, what)
            raise _CandidateFailedError(gate) from exc
        except RunnerError as exc:
            msg = f"{what} stopped on a fault blamed on neither tree: {exc}"
            raise self.stop("harness_fault", msg) from exc

    def _recheck_baseline(self, label: str, why: str) -> None:
        """Run the baseline again before the candidate is blamed.

        ``why`` says what would be blamed on the candidate; the stop
        message quotes it.
        """
        baseline = self.trees["baseline"]
        checker = self._runner(
            self.spec.model_copy(update={"warmup": 1}),
            {"baseline": baseline, "candidate": baseline},
            f"recheck-{label}",
        )
        try:
            checker.warmup()
        except ExecutionError as exc:
            msg = (
                f"{why}; the baseline failed when run ({exc.reason}), so "
                f"the failure is the baseline's or shared, not the "
                f"candidate's. Logs: {exc.stdout.parent}"
            )
            raise self.stop("broken_baseline", msg) from exc
        except RunnerError as exc:
            msg = f"the baseline recheck stopped on a harness fault: {exc}"
            raise self.stop("harness_fault", msg) from exc

    def _rounds(self, runner: Runner) -> _Ended:
        """Run warmup and rounds until a look ends the comparison."""
        spec = self.spec
        self._guard(runner.warmup, "a warmup execution")
        steps = _Steps(spec, self.wall_step)
        confidence = look_confidence(spec.alpha, spec.looks.max_looks)
        last: Look | None = None
        used = 0
        longest = 0.0
        begun = time.monotonic()
        for index in range(spec.budget.max_rounds):
            start = time.monotonic()
            done = self._guard(
                functools.partial(runner.run_round, index), f"round {index}"
            )
            self.measured.append(_Measured(done, steps.of(done, self.stop)))
            now = time.monotonic()
            longest = max(longest, now - start)
            n = index + 1
            exhausted = n == spec.budget.max_rounds or (
                now - begun + longest > spec.budget.max_seconds
            )
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
                if str(last.action[0]) != "CONTINUE":
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
            procedure=PROCEDURE,
        )

    def _keep_blocked(self) -> bool:
        """Whether a guardrail can't be shown compliant yet."""
        return any(g.applicable for g in self.spec.guardrails)

    def _values(
        self, used: int, field: Callable[[Sample], float]
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """Return both arms' values in the first ``used`` rounds."""
        out: dict[Arm, list[float]] = {arm: [] for arm in _ARMS}
        for m in self.measured[:used]:
            for s in m.round.samples:
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
            procedure=PROCEDURE,
            calibration="uncalibrated",
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
            elif rule != 5:
                msg = (
                    f"the last look gave rule {rule}; an uncalibrated "
                    "comparison can only end on rule 1 (resolution) or 5"
                )
                raise self.stop("internal_fault", msg)
        verdict = Verdict(
            outcome="INCONCLUSIVE",
            reason="insufficient_evidence",
            rule=5,
            estimate=estimate,
            interval=interval,
            rounds=len(self.measured),
            looks=ended.looks,
            procedure=PROCEDURE,
            calibration="uncalibrated",
            diagnostics=diagnostics,
        )
        guardrails = tuple(
            GuardrailResult(
                metric=g.metric,
                status="unmeasurable" if g.applicable else "not_applicable",
                reason=_GUARDRAILS_NOT_BUILT if g.applicable else g.reason,
            )
            for g in self.spec.guardrails
        )
        return self._write(verdict, self._listed_gates(), guardrails)

    def _statistics(
        self, ended: _Ended
    ) -> tuple[Diagnostics, Interval | None, float | None]:
        """Diagnostics, interval and estimate of the deciding look."""
        look, used = ended.look, ended.used
        n = used if look is not None else len(self.measured)
        if n < 2:
            return Diagnostics(procedure_uncalibrated=True), None, None
        spec = self.spec
        b, c = self._values(n, lambda s: s.value)
        ratios = b / c if spec.metric.direction == "lower_is_better" else c / b
        d = np.log(ratios)
        first = np.array(
            [m.round.samples[0].version == "candidate" for m in self.measured]
        )[:n]
        lag1 = float(lag1_autocorrelation(d, first))
        self._order_warning(first)
        self._shape_warnings(d, lag1)
        confidence = ended.confidence
        interval: Interval | None = None
        estimate: float | None = None
        if look is not None and int(look.rule[0]) == 5:
            interval = Interval(
                lower=float(look.lower[0]),
                upper=float(look.upper[0]),
                confidence=confidence,
            )
            estimate = float(
                t_interval(
                    b,
                    c,
                    direction=spec.metric.direction,
                    confidence=confidence,
                ).estimate
            )
        wall, disagree = self._wall(n, confidence, interval)
        diagnostics = Diagnostics(
            procedure_uncalibrated=True,
            metric_disagreement=disagree,
            wall_interval=wall,
            lag1_autocorrelation=None if math.isnan(lag1) else lag1,
            cv_baseline=float(np.std(b, ddof=1) / np.mean(b)),
            cv_candidate=float(np.std(c, ddof=1) / np.mean(c)),
            sd_log_ratio=float(np.std(d, ddof=1)),
        )
        return diagnostics, interval, estimate

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
            procedure=PROCEDURE,
        )
        if int(look.rule[0]) == 1:
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

    def _order_warning(self, candidate_first: NDArray[np.bool_]) -> None:
        firsts = int(np.count_nonzero(candidate_first))
        others = int(candidate_first.size) - firsts
        if firsts != others:
            self.warnings.append(
                RunWarning(
                    code="order_not_balanced",
                    message=(
                        f"the candidate ran first in {firsts} of the "
                        f"{candidate_first.size} rounds used; the runner "
                        "draws a fair coin per round, not balanced order "
                        "pairs"
                    ),
                )
            )

    def _shape_warnings(self, d: NDArray[np.float64], lag1: float) -> None:
        """Warn on lag-1 autocorrelation above 0.3 and skew beyond 2."""
        if not math.isnan(lag1) and lag1 > _AUTOCORRELATION_WARN:
            self.warnings.append(
                RunWarning(
                    code="autocorrelated_rounds",
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

    def __init__(self, spec: BenchmarkSpec, wall_step: float) -> None:
        self.kind = spec.metric.kind
        self.continuity = self.kind == "reported"
        self.fixed: float | None = {
            "wall": wall_step,
            "cpu": _CPU_STEP_SECONDS,
            "counter": 1.0,
            "reported": None,
        }[self.kind]

    def of(
        self, done: Round, stop: Callable[[str, str], CompareStoppedError]
    ) -> dict[Arm, float]:
        """Return each arm's step in one finished round."""
        steps: dict[Arm, float] = {}
        for ex in done.executions:
            if self.fixed is not None:
                steps[ex.arm] = self.fixed
                continue
            step = _printed_step(ex)
            if step is None:
                msg = (
                    f"the PERFPROOF value of {ex.label} {ex.arm} could not be "
                    f"read back from {ex.stdout} to find its resolution"
                )
                raise stop("harness_fault", msg)
            steps[ex.arm] = step
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

    Fails closed: a host identity that could not be probed fails the
    gate, as does a different identity at the end (on Linux a reboot
    changes it).
    """
    if first.host_id is None:
        reason = first.missing.get("host_id", "no reason recorded")
        return GateResult(
            gate="same_host",
            status="failed",
            detail=f"the host identity is unknown: {reason}",
        )
    if last is None:
        return GateResult(
            gate="same_host",
            status="passed",
            detail=f"host {first.host_id} before the first round",
        )
    if last.host_id != first.host_id:
        found = last.host_id or last.missing.get("host_id", "unknown")
        return GateResult(
            gate="same_host",
            status="failed",
            detail=(
                f"the host was {first.host_id} before the first round and "
                f"{found} after the last"
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
    token = _reported_token(ex.stdout)
    if token is None:
        return None
    match = _NUMBER.fullmatch(token)
    if match is None or float(token) != ex.value:
        return None
    digits = len(match.group(1) or "")
    exponent = int(match.group(2) or "0")
    exact = Decimal(1).scaleb(exponent - digits)
    step = float(exact)
    if Decimal(step) < exact:
        step = math.nextafter(step, math.inf)
    return step


def _reported_token(path: Path) -> str | None:
    """Return the literal ``value`` of the first ``PERFPROOF`` line."""
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
                return _literal_value(body.decode("utf-8"))
    except (OSError, UnicodeDecodeError):
        return None


def _literal_value(text: str) -> str | None:
    """Return a JSON object's ``value`` as the literal text it was."""

    def keep(literal: str) -> str:
        return literal

    try:
        parsed: object = json.loads(text, parse_float=keep, parse_int=keep)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    value = cast("dict[str, object]", parsed).get("value")
    return value if isinstance(value, str) else None


def _save_samples(path: Path, measured: Sequence[_Measured]) -> None:
    """Write every measured sample to ``samples.npz``.

    One entry per sample, in the order run: ``round``, ``position``,
    ``candidate`` (1 for the candidate), ``session``, ``value``,
    ``step`` (its resolution), ``wall_seconds``, ``cpu_seconds`` and
    ``max_rss_bytes`` (NaN where unknown), and ``command_hash``.
    Numeric and fixed-width text arrays only, so it loads with
    ``allow_pickle=False``.
    """
    rows = [(m, s) for m in measured for s in m.round.samples]

    def column(values: list[float]) -> NDArray[np.float64]:
        return np.array(values, dtype=np.float64)

    np.savez(
        path,
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
