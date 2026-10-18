"""The labeller's decision rule, as written in ``eval/label/README.md``.

Pure functions: no measurement, no files. `machine_label` applies the
rule to one machine's analyses, `combine_performance` and
`combine_correctness` to two machines' labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

MIN_EFFECT = 0.03
BAR = 1 + MIN_EFFECT
MIN_PAIRS = 200

Category = Literal["improvement", "below-bar", "regression", "indeterminate"]
Label = Literal["improvement", "below-bar", "regression", "unstable"]
Correctness = Literal["pass", "fail", "unknown"]
DECIDED: tuple[Category, ...] = ("improvement", "below-bar", "regression")


def bootstrap_category(lower: float, upper: float) -> Category:
    """Categorise a bootstrap interval against the bar.

    Parameters
    ----------
    lower, upper : float
        The interval's bounds for the speedup of B over A.

    Returns
    -------
    {"improvement", "regression", "below-bar", "indeterminate"}
        The first that matches: ``lower > BAR``, ``upper < 1 / BAR``,
        ``upper <= BAR``, otherwise indeterminate.
    """
    if lower > BAR:
        return "improvement"
    if upper < 1 / BAR:
        return "regression"
    if upper <= BAR:
        return "below-bar"
    return "indeterminate"


def pyperf_category(ratio: float, significant: bool) -> Category:
    """Categorise pyperf's comparison against the bar.

    Parameters
    ----------
    ratio : float
        ``mean(a) / mean(b)``.
    significant : bool
        pyperf's t-test result.

    Returns
    -------
    {"improvement", "regression", "below-bar", "indeterminate"}
        The first that matches: significant and ``ratio > BAR``,
        significant and ``ratio < 1 / BAR``, ``ratio <= BAR``,
        otherwise indeterminate.
    """
    if significant and ratio > BAR:
        return "improvement"
    if significant and ratio < 1 / BAR:
        return "regression"
    if ratio <= BAR:
        return "below-bar"
    return "indeterminate"


def reproduces(category: Category, halves: tuple[float, float]) -> bool:
    """Whether both halves' estimates agree with a category.

    Parameters
    ----------
    category : str
        A decided category.
    halves : tuple of float
        Geometric-mean speedup of the first and second half of the
        pairs.

    Returns
    -------
    bool
        Both above ``BAR`` for an improvement, both below ``1 / BAR``
        for a regression, both at or below ``BAR`` for below-bar.
        False for ``indeterminate``.
    """
    if category == "improvement":
        return all(h > BAR for h in halves)
    if category == "regression":
        return all(h < 1 / BAR for h in halves)
    if category == "below-bar":
        return all(h <= BAR for h in halves)
    return False


@dataclass(frozen=True)
class MachineLabel:
    """One machine's performance label.

    Attributes
    ----------
    label : {"improvement", "below-bar", "regression", "unstable"}
        The label.
    reason : str
        Why, for ``unstable``; empty otherwise.
    bootstrap : str
        The bootstrap's category.
    pyperf : str
        pyperf's category.
    reproduced : bool
        Whether the halves agreed with the bootstrap's category.
    """

    label: Label
    reason: str
    bootstrap: Category
    pyperf: Category
    reproduced: bool


def machine_label(
    lower: float,
    upper: float,
    ratio: float,
    significant: bool,
    halves: tuple[float, float],
) -> MachineLabel:
    """Apply the rule for one machine.

    Parameters
    ----------
    lower, upper : float
        Bootstrap interval for the speedup of B over A.
    ratio : float
        pyperf's ratio of means, ``mean(a) / mean(b)``.
    significant : bool
        pyperf's t-test result.
    halves : tuple of float
        Geometric-mean speedup of each half of the pairs.

    Returns
    -------
    MachineLabel
        The label, with ``unstable`` reasons in the README's order:
        ``indeterminate``, ``analyses-disagree``, ``not-reproduced``.
    """
    boot = bootstrap_category(lower, upper)
    pp = pyperf_category(ratio, significant)
    again = reproduces(boot, halves)
    reason = ""
    if "indeterminate" in (boot, pp):
        reason = "indeterminate"
    elif boot != pp:
        reason = "analyses-disagree"
    elif not again:
        reason = "not-reproduced"
    if reason:
        return MachineLabel("unstable", reason, boot, pp, again)
    # No reason means boot is a decided category, so also a Label.
    return MachineLabel(cast(Label, boot), "", boot, pp, again)


def combine_performance(
    first: tuple[str, Label, str], second: tuple[str, Label, str]
) -> tuple[Label, str]:
    """Combine two machines' performance labels.

    Parameters
    ----------
    first, second : tuple of (str, str, str)
        Each machine's name, label and reason.

    Returns
    -------
    label : str
        The shared label when both are the same decided label, else
        ``unstable``.
    reason : str
        Empty when they agree; otherwise both machines' labels and
        reasons.
    """
    (m1, l1, r1), (m2, l2, r2) = first, second
    if l1 == l2 and l1 != "unstable":
        return l1, ""

    def say(machine: str, label: str, reason: str) -> str:
        return f"{machine}: {label}" + (f" ({reason})" if reason else "")

    agree = "machines disagree" if l1 != l2 else "both unstable"
    if "unstable" in (l1, l2) and l1 != l2:
        agree = "one machine unstable"
    return "unstable", f"{agree}; {say(m1, l1, r1)}; {say(m2, l2, r2)}"


def combine_correctness(
    first: Correctness, second: Correctness
) -> Correctness:
    """Combine two machines' correctness labels.

    Parameters
    ----------
    first, second : {"pass", "fail", "unknown"}
        Each machine's correctness label.

    Returns
    -------
    {"pass", "fail", "unknown"}
        ``fail`` if either fails, ``pass`` if both pass, else
        ``unknown``.
    """
    if "fail" in (first, second):
        return "fail"
    if first == second == "pass":
        return "pass"
    return "unknown"


def correctness_label(
    reference_failing: set[str] | None,
    task_failing: set[str] | None,
    suite_read: bool,
    oracle_exit: int | None,
) -> tuple[Correctness, str]:
    """Apply the correctness rule for one machine.

    Parameters
    ----------
    reference_failing : set of str or None
        Failed and errored test ids on the reference tree, or None when
        they could not be read.
    task_failing : set of str or None
        The same on the task tree.
    suite_read : bool
        Whether the reference tree's suite ran to completion and its
        results were read, with every non-zero exit explained by a
        failing id.
    oracle_exit : int or None
        The oracle's exit status on the reference tree, None when it
        did not run.

    Returns
    -------
    label : {"pass", "fail", "unknown"}
        As in the README's correctness rule.
    reason : str
        What decided it.
    """
    new: set[str] = set()
    if reference_failing is not None:
        new = reference_failing - (task_failing or set())
    if reference_failing is not None and task_failing is not None and new:
        shown = ", ".join(sorted(new)[:10])
        more = f" and {len(new) - 10} more" if len(new) > 10 else ""
        return "fail", f"newly failing on the reference tree: {shown}{more}"
    if oracle_exit is not None and oracle_exit != 0:
        return "fail", f"the oracle exits {oracle_exit} on the reference tree"
    if task_failing is None and reference_failing:
        return "unknown", (
            "the reference tree has failing tests and the task tree's "
            "results could not be read"
        )
    if not suite_read or reference_failing is None:
        return "unknown", "the reference tree's suite results were not read"
    if oracle_exit is None:
        return "unknown", "the oracle did not run on the reference tree"
    return "pass", "no newly failing test, and the oracle passes"
