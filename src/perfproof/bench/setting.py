"""Measure the effect of changing one setting.

`measure_setting` runs the benchmark as it is and with one environment
variable set to another value, interleaved, and reports each side's
metric and their ratio (the unchanged run's metric divided by the
changed run's) with an interval. It is a cheap test, an estimate from
a few runs, and never a verdict: whether a setting is kept is decided
by `compare` on a candidate that carries it, and many settings tried
this way are chosen among by `confirm`.

Only environment variables are settings here. A setting that lives in a
config file or a flag is changed by writing a candidate and measuring
that.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np

from perfproof.bench.conditions import (
    Condition,
    MeasureError,
    Observation,
    run_conditions,
)
from perfproof.bench.estimates import Estimate, geometric_mean
from perfproof.schema import BenchmarkSpec

__all__ = ["SettingResult", "measure_setting", "parse_setting"]

_NAME: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_MIN_ROUNDS: Final = 3


@dataclass(frozen=True, slots=True)
class SettingResult:
    """What changing one setting showed.

    Attributes
    ----------
    name, value : str
        The environment variable and the value it was set to.
    declared : str or None
        The value the spec declares for it; ``None`` when the spec does
        not set it (its value in the unchanged run is then whatever the
        program defaults to, which perfproof does not know).
    rounds : int
        Interleaved rounds; each has two executions of each side.
    unchanged, changed : Estimate
        The metric of each side.
    ratio : Estimate
        Unchanged over changed: the geometric mean of the rounds'
        ratios.
    metric_kind, unit : str
        The spec's metric kind and unit.
    directory : pathlib.Path
        Where the runs' logs are.
    """

    name: str
    value: str
    declared: str | None
    rounds: int
    unchanged: Estimate
    changed: Estimate
    ratio: Estimate
    metric_kind: str
    unit: str
    directory: Path


def parse_setting(text: str) -> tuple[str, str]:
    """Parse ``NAME=VALUE`` for ``--set``.

    Parameters
    ----------
    text : str
        An environment variable name, ``=`` and a value, possibly empty.

    Returns
    -------
    tuple of (str, str)
        The name and the value.

    Raises
    ------
    MeasureError
        ``invalid_arguments`` if there is no ``=`` or the name is not a
        valid variable name or is one perfproof reserves.
    """
    name, sep, value = text.partition("=")
    if not (sep and _NAME.fullmatch(name)):
        msg = f"expected NAME=VALUE with NAME an identifier, got {text!r}"
        raise MeasureError("invalid_arguments", msg)
    if name.startswith("PERFPROOF_") or name == "PYTHONPATH":
        msg = f"{name} is reserved for perfproof's own measurements"
        raise MeasureError("invalid_arguments", msg)
    return name, value


def measure_setting(
    spec: BenchmarkSpec,
    root: Path,
    name: str,
    value: str,
    *,
    rounds: int,
    work_dir: Path,
    seed: int,
    process_env: Mapping[str, str],
    timeout_seconds: float | None = None,
) -> SettingResult:
    """Measure the benchmark with and without one setting changed.

    Parameters
    ----------
    spec : perfproof.schema.BenchmarkSpec
        The benchmark.
    root : pathlib.Path
        Root of the tree to run in.
    name, value : str
        The environment variable and the value to set it to, as
        `parse_setting` returns them.
    rounds : int
        Interleaved rounds, at least 3 so an interval exists.
    work_dir : pathlib.Path
        New directory for the runs' logs.
    seed : int
        Seed for the order within rounds.
    process_env : Mapping of str to str
        perfproof's own environment, usually ``os.environ``.
    timeout_seconds : float, optional
        Time limit for each execution; the spec's when omitted.

    Returns
    -------
    SettingResult
        Both sides' metric and their ratio.

    Raises
    ------
    MeasureError
        ``invalid_arguments`` for fewer than 3 rounds, plus whatever
        `perfproof.bench.conditions.run_conditions` raises. A setting
        that points the benchmark at a service fails the
        ``side_effects`` gate like any other run.

    Notes
    -----
    Runs ``rounds * 2 * 2`` executions plus warmups.
    """
    if rounds < _MIN_ROUNDS:
        msg = f"an interval needs at least {_MIN_ROUNDS} rounds"
        raise MeasureError("invalid_arguments", msg)
    observed = run_conditions(
        spec,
        root,
        (Condition("unchanged"), Condition("changed", env={name: value})),
        rounds=rounds,
        work_dir=work_dir,
        seed=seed,
        process_env=process_env,
        timeout_seconds=timeout_seconds,
    )
    by_key: dict[tuple[str, int], Observation] = {
        (o.key, o.round): o for o in observed
    }
    old = [
        float(np.mean(by_key["unchanged", r].values)) for r in range(rounds)
    ]
    new = [float(np.mean(by_key["changed", r].values)) for r in range(rounds)]
    return SettingResult(
        name=name,
        value=value,
        declared=spec.env.get(name),
        rounds=rounds,
        unchanged=geometric_mean(old),
        changed=geometric_mean(new),
        ratio=geometric_mean([a / b for a, b in zip(old, new, strict=True)]),
        metric_kind=spec.metric.kind,
        unit=spec.metric.unit,
        directory=work_dir,
    )
