"""How a benchmark's metric grows with its input size.

`measure_sizes` runs the benchmark at ``n``, ``2n``, ``4n`` and so on
and fits ``metric = c * size ** exponent`` to every measurement with
`perfproof.bench.estimates.fit_exponent`. An exponent near 1 is linear
growth, near 2 quadratic; the interval says how well the runs pin it
down. The fit is an estimate from a few runs on whatever the machine
was doing, not a calibrated result, and it says nothing about whether
any change is better.

How size reaches the benchmark
------------------------------
perfproof cannot guess which input a benchmark scales, so the caller
names the knob: a command-line flag (``--n``), which is appended to the
spec's command as two arguments ``--n <size>``, or an environment
variable. ``base`` is the size the multipliers apply to; the sizes
measured are ``base * factor`` for each integer ``factor``.

What to read in the result
--------------------------
``local_exponents`` are the slopes between neighbouring sizes. A
constant cost that does not scale (interpreter start-up in a ``wall``
metric, a fixed setup) pulls the small sizes' exponents toward 0; a
sequence that rises with size means the growth is not one power.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from perfproof.bench.conditions import (
    Condition,
    MeasureError,
    run_conditions,
)
from perfproof.bench.estimates import ExponentFit, fit_exponent
from perfproof.schema import BenchmarkSpec

__all__ = ["SizeKnob", "SizesResult", "measure_sizes", "parse_knob"]

_NAME: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_MIN_SIZES: Final = 3


@dataclass(frozen=True, slots=True)
class SizeKnob:
    """The one input that sets the size.

    Attributes
    ----------
    kind : {"arg", "env"}
        A command-line flag, appended as ``flag size``, or an
        environment variable.
    name : str
        The flag (``--n``) or the variable (``ITEMS``).
    base : int
        The size a multiplier of 1 means; at least 1.
    """

    kind: Literal["arg", "env"]
    name: str
    base: int


@dataclass(frozen=True, slots=True)
class SizesResult:
    """What a scaling run showed.

    Attributes
    ----------
    knob : SizeKnob
        What was scaled.
    factors : tuple of int
        The multipliers measured.
    sizes : tuple of int
        ``base * factor`` for each.
    rounds : int
        Interleaved rounds; each has two executions at each size.
    fit : perfproof.bench.estimates.ExponentFit
        The fitted exponent and its interval.
    metric_kind : str
        The spec's metric kind, ``wall``, ``cpu``, ``reported`` or
        ``counter``.
    unit : str
        Unit of the medians.
    notes : tuple of str
        Anything the reader needs to weigh the exponent.
    directory : pathlib.Path
        Where the runs' logs are.
    """

    knob: SizeKnob
    factors: tuple[int, ...]
    sizes: tuple[int, ...]
    rounds: int
    fit: ExponentFit
    metric_kind: str
    unit: str
    notes: tuple[str, ...]
    directory: Path


def parse_knob(kind: Literal["arg", "env"], text: str) -> SizeKnob:
    """Parse ``NAME=BASE`` for ``--size-arg`` or ``--size-env``.

    Parameters
    ----------
    kind : {"arg", "env"}
        Which option the text came from.
    text : str
        ``NAME=BASE``, with ``BASE`` a decimal integer of at least 1.

    Returns
    -------
    SizeKnob
        The parsed knob.

    Raises
    ------
    MeasureError
        ``invalid_arguments`` if the text is not of that form, or an
        environment variable's name is not an identifier.
    """
    name, sep, base_text = text.rpartition("=")
    if not (sep and name and base_text.isascii() and base_text.isdigit()):
        msg = f"expected NAME=BASE with BASE an integer, got {text!r}"
        raise MeasureError("invalid_arguments", msg)
    base = int(base_text)
    if base < 1:
        msg = f"BASE must be at least 1, got {base}"
        raise MeasureError("invalid_arguments", msg)
    if kind == "env" and not _NAME.fullmatch(name):
        msg = f"{name!r} is not an environment variable name"
        raise MeasureError("invalid_arguments", msg)
    if kind == "arg" and not name.startswith("-"):
        msg = f"a size flag starts with '-', got {name!r}"
        raise MeasureError("invalid_arguments", msg)
    return SizeKnob(kind, name, base)


def _condition(knob: SizeKnob, factor: int) -> Condition:
    """Return the condition that runs the benchmark at one size."""
    size = str(knob.base * factor)
    key = f"x{factor}"
    if knob.kind == "arg":
        return Condition(key, argv_extra=(knob.name, size))
    return Condition(key, env={knob.name: size})


def measure_sizes(
    spec: BenchmarkSpec,
    root: Path,
    knob: SizeKnob,
    factors: Sequence[int],
    *,
    rounds: int,
    work_dir: Path,
    seed: int,
    process_env: Mapping[str, str],
    timeout_seconds: float | None = None,
) -> SizesResult:
    """Fit how the benchmark's metric grows with its input size.

    Parameters
    ----------
    spec : perfproof.schema.BenchmarkSpec
        The benchmark.
    root : pathlib.Path
        Root of the tree to run in.
    knob : SizeKnob
        The input that sets the size.
    factors : sequence of int
        Multipliers of ``knob.base``, at least three distinct, each at
        least 1.
    rounds : int
        Interleaved rounds; each gives one observation at each size, the
        mean of two executions. At least 2, so the fit has a residual.
    work_dir : pathlib.Path
        New directory for the runs' logs.
    seed : int
        Seed for the order of sizes within rounds.
    process_env : Mapping of str to str
        perfproof's own environment, usually ``os.environ``.
    timeout_seconds : float, optional
        Time limit for each execution; the spec's when omitted.

    Returns
    -------
    SizesResult
        The fit and the sizes it rests on.

    Raises
    ------
    MeasureError
        ``invalid_arguments`` for fewer than three distinct sizes or a
        multiplier below 1, plus whatever
        `perfproof.bench.conditions.run_conditions` raises.

    Notes
    -----
    Runs ``rounds * len(factors) * 2`` executions plus warmups, so the
    time grows with the largest size. One value per round and size, the
    mean of its two executions, is fitted.
    """
    distinct = sorted(set(factors))
    if len(distinct) < _MIN_SIZES or distinct[0] < 1:
        msg = (
            f"need at least {_MIN_SIZES} distinct integer multipliers of "
            f"1 or more, got {list(factors)}"
        )
        raise MeasureError("invalid_arguments", msg)
    if rounds < 2:
        msg = "a fit with an interval needs at least 2 rounds"
        raise MeasureError("invalid_arguments", msg)
    observed = run_conditions(
        spec,
        root,
        [_condition(knob, f) for f in distinct],
        rounds=rounds,
        work_dir=work_dir,
        seed=seed,
        process_env=process_env,
        timeout_seconds=timeout_seconds,
    )
    sizes: list[float] = []
    values: list[float] = []
    for o in observed:
        size = knob.base * int(o.key.removeprefix("x"))
        # The two executions of a round share its machine state, so they
        # are one observation, not two independent ones.
        sizes.append(float(size))
        values.append(sum(o.values) / len(o.values))
    fit = fit_exponent(sizes, values)
    notes: list[str] = []
    if spec.metric.kind == "wall":
        notes.append(
            "a wall metric includes process start-up, a cost that does not "
            "grow with the input; it pulls the exponent of small sizes "
            "toward 0, as the local exponents show"
        )
    return SizesResult(
        knob=knob,
        factors=tuple(distinct),
        sizes=tuple(knob.base * f for f in distinct),
        rounds=rounds,
        fit=fit,
        metric_kind=spec.metric.kind,
        unit=spec.metric.unit,
        notes=tuple(notes),
        directory=work_dir,
    )
