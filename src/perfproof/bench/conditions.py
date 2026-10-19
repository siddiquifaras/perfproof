"""Run one benchmark under several conditions, interleaved.

The discovery measurements (`measure --sizes`, ``--stub`` and ``--set``)
all ask the same question of one benchmark: how does its metric differ
between a few variants of the same run (another input size, a function
replayed, a setting changed). Each variant is a `Condition`: extra
arguments appended to the spec's command and extra environment
variables, on top of the spec's own.

`run_conditions` measures them with `perfproof.bench.runner.Runner`, so
a value is read exactly as a comparison reads it: wall time, CPU time or
the benchmark's own ``PERFPROOF`` line, in a fresh process each time. In
round ``r`` every condition runs once through the runner, in an order
drawn per round from a recorded seed, so a drift in machine load lands
on all conditions alike. The runner executes a baseline and a candidate
per round; here both are the same tree and the same condition, so each
round gives two executions of each condition, and both count.

Nothing here decides anything. The values are inputs to an estimate or
a bound, and every caller labels them as such.

What is checked first
---------------------
Before anything runs, the ``side_effects`` gate must pass or be waived
for the spec and for every condition, as for every run of a benchmark.
A spec with a ``reset`` is refused: `Runner` does not run it, so a
second execution would start from the first one's state.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import numpy as np

from perfproof.bench.runner import ExecutionError, Runner, RunnerError
from perfproof.bench.side_effects import (
    benchmark_environment,
    side_effects_gate,
)
from perfproof.schema import BenchmarkSpec

__all__ = [
    "EXECUTIONS_PER_ROUND",
    "Condition",
    "MeasureError",
    "Observation",
    "check_runnable",
    "run_conditions",
]

EXECUTIONS_PER_ROUND: Final = 2
"""Executions of each condition per round: the runner's pair."""


class MeasureError(Exception):
    """A discovery measurement could not run, or its workload failed.

    Parameters
    ----------
    code : str
        Stable snake_case identifier, such as ``workload_failed``.
    message : str
        What went wrong.
    details : Mapping of str to object, optional
        Structured context, such as the failing run's log paths.
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: Mapping[str, object] = {} if details is None else details


@dataclass(frozen=True, slots=True)
class Condition:
    """One variant of the benchmark's run.

    Attributes
    ----------
    key : str
        Name of the condition in results and in log directories.
    argv_extra : tuple of str
        Arguments appended to the spec's command.
    env : Mapping of str to str
        Environment variables set for the run, over the spec's own.
    """

    key: str
    argv_extra: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True, slots=True)
class Observation:
    """What one round measured for one condition.

    Attributes
    ----------
    key : str
        The condition's key.
    round : int
        Round index, from 0.
    values : tuple of float
        The spec's metric for each of the round's two executions, in
        the metric's unit.
    wall_seconds, cpu_seconds : tuple of float
        Wall and CPU time of each execution, in seconds.
    directory : pathlib.Path
        This condition's log directory for the round.
    """

    key: str
    round: int
    values: tuple[float, ...]
    wall_seconds: tuple[float, ...]
    cpu_seconds: tuple[float, ...]
    directory: Path


def check_runnable(
    spec: BenchmarkSpec,
    root: Path,
    process_env: Mapping[str, str],
    conditions: Sequence["Condition"] = (),
) -> None:
    """Refuse a spec that cannot be run repeatedly as it is.

    Parameters
    ----------
    spec : perfproof.schema.BenchmarkSpec
        The benchmark.
    root : pathlib.Path
        Root of the tree it runs in.
    process_env : Mapping of str to str
        perfproof's own environment, usually ``os.environ``.
    conditions : sequence of Condition, optional
        Variants to check as well: a setting or argument that points
        the benchmark at a service must pass the gate too.

    Raises
    ------
    MeasureError
        ``side_effects`` if the gate fails for the spec or a variant;
        ``unsupported_spec`` if the spec has a reset command.
    """
    for variant in (spec, *(_variant(spec, c, {}, None) for c in conditions)):
        env = benchmark_environment(variant, process_env)
        gate = side_effects_gate(variant, root, env)
        if gate.status == "failed":
            msg = f"the side_effects gate refused the run: {gate.detail}"
            raise MeasureError("side_effects", msg)
    if spec.reset is not None:
        msg = (
            "this spec has a reset command, which measure does not run "
            "between executions; measure it with compare instead"
        )
        raise MeasureError("unsupported_spec", msg)


def _variant(
    spec: BenchmarkSpec,
    condition: Condition,
    extra_env: Mapping[str, str],
    timeout_seconds: float | None,
) -> BenchmarkSpec:
    """Return the spec with a condition's command and environment."""
    update: dict[str, object] = {
        "command": (*spec.command, *condition.argv_extra),
        "env": {**spec.env, **condition.env, **extra_env},
    }
    if timeout_seconds is not None:
        update["timeout_seconds"] = timeout_seconds
    return spec.model_copy(update=update)


def run_conditions(
    spec: BenchmarkSpec,
    root: Path,
    conditions: Sequence[Condition],
    *,
    rounds: int,
    work_dir: Path,
    seed: int,
    process_env: Mapping[str, str],
    round_env: Callable[[str, str], Mapping[str, str]] | None = None,
    timeout_seconds: float | None = None,
) -> list[Observation]:
    """Measure every condition in ``rounds`` interleaved rounds.

    Parameters
    ----------
    spec : perfproof.schema.BenchmarkSpec
        The benchmark; its warmup count applies to each condition.
    root : pathlib.Path
        Root of the tree to run in.
    conditions : sequence of Condition
        The variants, with distinct keys; at least one.
    rounds : int
        Rounds to run, at least 1 and at most the spec's round budget.
    work_dir : pathlib.Path
        Directory for the runs' logs; created, never overwritten.
    seed : int
        Seed for the per-round order of conditions; non-negative.
    process_env : Mapping of str to str
        perfproof's own environment, usually ``os.environ``.
    round_env : callable, optional
        ``round_env(key, label)`` returns extra environment variables
        for one condition's run, where ``label`` is ``"warmup"`` or
        ``"round-<r>"``; used to give each run its own output
        directory.
    timeout_seconds : float, optional
        Time limit for each execution; the spec's when omitted.

    Returns
    -------
    list of Observation
        One per condition per round, in the order measured.

    Raises
    ------
    MeasureError
        ``invalid_arguments`` for a bad ``rounds`` or duplicate keys;
        ``workload_failed`` when an execution fails (its logs are in
        the details, and ``condition`` names the condition);
        ``runner_failed`` for a fault in perfproof's own process.
    ValueError
        If a tree or the seed is invalid, as `Runner` raises.

    Notes
    -----
    Time is that of ``rounds * len(conditions) * 2`` executions plus
    the warmups. perfproof does nothing between executions beyond
    reading their output.
    """
    keys = [c.key for c in conditions]
    if not conditions or len(set(keys)) != len(keys):
        msg = "conditions must be a non-empty list with distinct keys"
        raise MeasureError("invalid_arguments", msg)
    if not 1 <= rounds <= spec.budget.max_rounds:
        msg = (
            f"rounds must be from 1 to the spec's budget of "
            f"{spec.budget.max_rounds}, got {rounds}"
        )
        raise MeasureError("invalid_arguments", msg)
    check_runnable(spec, root, process_env, conditions)
    environment = benchmark_environment(spec, process_env)
    by_key = {c.key: c for c in conditions}
    rng = np.random.default_rng(seed)

    def runner(key: str, label: str) -> Runner:
        extra: Mapping[str, str] = (
            {} if round_env is None else round_env(key, label)
        )
        return Runner(
            _variant(spec, by_key[key], extra, timeout_seconds),
            {"baseline": root, "candidate": root},
            work_dir / "logs" / key / label,
            order_seed=seed,
            base_env=environment,
        )

    observations: list[Observation] = []
    current = keys[0]
    try:
        if spec.warmup:
            for current in keys:
                runner(current, "warmup").warmup()
        for r in range(rounds):
            for index in rng.permutation(len(keys)):
                current = keys[int(index)]
                label = f"round-{r}"
                done = runner(current, label).run_round(0)
                observations.append(
                    Observation(
                        key=current,
                        round=r,
                        values=tuple(e.value for e in done.executions),
                        wall_seconds=tuple(
                            e.wall_seconds for e in done.executions
                        ),
                        cpu_seconds=tuple(
                            e.cpu_seconds for e in done.executions
                        ),
                        directory=work_dir / "logs" / current / label,
                    )
                )
    except ExecutionError as exc:
        raise MeasureError(
            "workload_failed",
            f"under condition {current!r}: {exc.reason}",
            {
                "condition": current,
                "cause": exc.cause,
                "stdout": str(exc.stdout),
                "stderr": str(exc.stderr),
            },
        ) from exc
    except RunnerError as exc:
        raise MeasureError(
            "runner_failed", str(exc), {"condition": current}
        ) from exc
    return observations
