"""Goals: known metrics, default guardrails and how a bar is named.

Nothing here fills in a goal the developer has not stated. The tables
say what perfproof already knows about a metric's name (its direction,
and its unit where the name fixes one) so that ``--metric wall`` need
not be followed by a direction; a metric whose unit the name does not
fix (``throughput``) stays unknown until the developer gives it.

**The default bar.** A goal's ``bar`` is ``None`` until the developer
states one, and is never filled. When a comparison finds ``None`` it
applies the benchmark spec's ``min_effect`` (``0.03`` unless the spec
says otherwise, so a bar of ``1.03``) and calls it the *default bar*;
a stated bar is the *goal bar*. `bar_label` is the one place that
builds those names, so a report cannot present the default as the
developer's choice.
"""

import math
from collections.abc import Mapping
from typing import Final

from perfproof.schema import (
    DEFAULT_GUARDRAILS,
    Direction,
    GoalBar,
    GuardrailSpec,
    TargetClass,
)
from perfproof.stats.guardrails import resolve_allowance

__all__ = [
    "ALIASES",
    "KNOWN_METRICS",
    "MetricInfo",
    "bar_label",
    "default_guardrails",
    "parse_bar",
    "parse_guardrail",
]

type MetricInfo = tuple[Direction, str | None]
"""Direction and unit of a metric; the unit is ``None`` when unknown."""

KNOWN_METRICS: Final[Mapping[str, MetricInfo]] = {
    "wall_time": ("lower_is_better", "s"),
    "cpu_time": ("lower_is_better", "s"),
    "startup_time": ("lower_is_better", "s"),
    "p50_latency": ("lower_is_better", "s"),
    "p99_latency": ("lower_is_better", "s"),
    "peak_memory": ("lower_is_better", "bytes"),
    "peak_gpu_memory": ("lower_is_better", "bytes"),
    "throughput": ("higher_is_better", None),
    "cost_per_token": ("lower_is_better", None),
}
"""Metric names of ``docs/discovery.md`` with what the name fixes."""

ALIASES: Final[Mapping[str, str]] = {
    "wall": "wall_time",
    "cpu": "cpu_time",
    "startup": "startup_time",
    "p50": "p50_latency",
    "p99": "p99_latency",
    "memory": "peak_memory",
    "gpu_memory": "peak_gpu_memory",
}
"""Short names accepted for a metric."""


def _guardrail(
    metric: str, allowance: float, source: str, reason: str
) -> GuardrailSpec:
    """Build a guardrail; ``source`` is the allowance's origin."""
    direction, unit = KNOWN_METRICS[metric]
    if unit is None:
        msg = f"the unit of {metric} is not fixed by its name"
        raise ValueError(msg)
    return GuardrailSpec.model_validate(
        {
            "metric": metric,
            "direction": direction,
            "unit": unit,
            "allowance": allowance,
            "allowance_source": source,
            "reason": reason,
        }
    )


def default_guardrails(
    target_class: TargetClass, primary: str
) -> tuple[GuardrailSpec, ...]:
    """Return the guardrails ``docs/method.md`` applies by default.

    Parameters
    ----------
    target_class : TargetClass
        Class of the workload's target.
    primary : str
        The goal's metric; a metric is the primary or a guardrail,
        never both, so it is left out.

    Returns
    -------
    tuple of GuardrailSpec
        The class's default guardrails, each with the allowance
        `perfproof.stats.guardrails.resolve_allowance` gives it (the
        provisional default, or the class's own) and where it came
        from.
    """
    found: list[GuardrailSpec] = []
    for metric in DEFAULT_GUARDRAILS[target_class]:
        if metric == primary:
            continue
        resolved = resolve_allowance(metric, target_class=target_class)
        if resolved is None:
            msg = f"no allowance is known for the default guardrail {metric}"
            raise ValueError(msg)
        allowance, source = resolved
        found.append(
            _guardrail(
                metric, allowance, source, f"default for {target_class}"
            )
        )
    return tuple(found)


def parse_guardrail(text: str) -> GuardrailSpec:
    """Parse ``METRIC=ALLOWANCE`` into a project-chosen guardrail.

    Parameters
    ----------
    text : str
        For example ``peak_memory=0.05`` (5%).

    Returns
    -------
    GuardrailSpec
        Applicable guardrail with source ``project``.

    Raises
    ------
    ValueError
        If the metric is not one whose unit its name fixes, or the
        allowance is not a number of at least 0.
    """
    metric, sep, raw = text.partition("=")
    metric = ALIASES.get(metric, metric)
    if not sep or metric not in KNOWN_METRICS:
        msg = f"expected METRIC=ALLOWANCE with a known metric, got {text!r}"
        raise ValueError(msg)
    allowance = float(raw)
    if allowance < 0 or math.isnan(allowance):
        msg = f"allowance must be at least 0, got {raw!r}"
        raise ValueError(msg)
    return _guardrail(metric, allowance, "project", "set by the developer")


def parse_bar(text: str) -> GoalBar:
    """Parse ``ratio:1.2`` or ``absolute:0.5`` into a bar.

    Parameters
    ----------
    text : str
        ``KIND:VALUE``.

    Returns
    -------
    GoalBar
        The bar.

    Raises
    ------
    ValueError
        If the text is not ``ratio`` or ``absolute`` followed by a
        number, or the bar is invalid (a ratio must exceed 1).
    """
    kind, sep, raw = text.partition(":")
    if not sep or kind not in ("ratio", "absolute"):
        msg = f"expected ratio:VALUE or absolute:VALUE, got {text!r}"
        raise ValueError(msg)
    return GoalBar.model_validate({"kind": kind, "value": float(raw)})


def bar_label(bar: GoalBar | None, min_effect: float) -> str:
    """Name the bar a comparison applies.

    Parameters
    ----------
    bar : GoalBar or None
        The goal's bar; ``None`` when the developer has not stated one.
    min_effect : float
        The benchmark spec's ``min_effect``.

    Returns
    -------
    str
        ``"goal bar (ratio 1.2)"`` for a stated bar; for ``None``,
        ``"default bar (min_effect 0.03)"`` naming the spec's value.
    """
    if bar is None:
        return f"default bar (min_effect {min_effect:g})"
    return f"goal bar ({bar.kind} {bar.value:g})"
