"""The decision rule in eval/label/README.md, row by row."""

import math

import pytest

import decide
from decide import BAR

BELOW = math.nextafter(BAR, 0.0)
ABOVE = math.nextafter(BAR, 2.0)
MIRROR = 1 / BAR


def test_bar_is_three_percent_in_float64() -> None:
    assert BAR == 1 + 0.03
    assert decide.MIN_PAIRS == 200


@pytest.mark.parametrize(
    ("lower", "upper", "expected"),
    [
        (ABOVE, 1.2, "improvement"),
        # Strict, as in docs/method.md: a lower bound on the bar is not
        # an improvement.
        (BAR, 1.2, "indeterminate"),
        (0.9, BAR, "below-bar"),
        (0.9, ABOVE, "indeterminate"),
        (0.99, 1.01, "below-bar"),
        (0.9, math.nextafter(MIRROR, 0.0), "regression"),
        (0.9, MIRROR, "below-bar"),
        (0.95, 1.10, "indeterminate"),
    ],
)
def test_bootstrap_category(lower: float, upper: float, expected: str) -> None:
    assert decide.bootstrap_category(lower, upper) == expected


@pytest.mark.parametrize(
    ("ratio", "significant", "expected"),
    [
        (ABOVE, True, "improvement"),
        (ABOVE, False, "indeterminate"),
        (BAR, True, "below-bar"),
        (BAR, False, "below-bar"),
        (1.0, False, "below-bar"),
        (math.nextafter(MIRROR, 0.0), True, "regression"),
        (math.nextafter(MIRROR, 0.0), False, "below-bar"),
        (MIRROR, True, "below-bar"),
    ],
)
def test_pyperf_category(
    ratio: float, significant: bool, expected: str
) -> None:
    assert decide.pyperf_category(ratio, significant) == expected


@pytest.mark.parametrize(
    ("category", "halves", "expected"),
    [
        ("improvement", (1.05, 1.06), True),
        ("improvement", (1.05, BAR), False),
        ("regression", (0.9, 0.95), True),
        ("regression", (0.9, MIRROR), False),
        ("below-bar", (1.0, BAR), True),
        ("below-bar", (1.0, ABOVE), False),
        ("indeterminate", (1.0, 1.0), False),
    ],
)
def test_reproduces(
    category: decide.Category, halves: tuple[float, float], expected: bool
) -> None:
    assert decide.reproduces(category, halves) is expected


def test_machine_label_agreeing_and_reproduced() -> None:
    got = decide.machine_label(1.05, 1.08, 1.06, True, (1.06, 1.07))
    assert (got.label, got.reason) == ("improvement", "")
    got = decide.machine_label(0.99, 1.01, 1.0, False, (1.0, 1.0))
    assert (got.label, got.reason) == ("below-bar", "")
    got = decide.machine_label(0.85, 0.9, 0.87, True, (0.86, 0.88))
    assert (got.label, got.reason) == ("regression", "")


def test_machine_label_unstable_reasons_in_order() -> None:
    # Indeterminate wins over disagreement and reproduction.
    got = decide.machine_label(1.0, 1.1, 1.06, True, (0.9, 1.2))
    assert (got.label, got.reason) == ("unstable", "indeterminate")
    got = decide.machine_label(1.05, 1.08, 1.06, False, (1.06, 1.07))
    assert (got.label, got.reason) == ("unstable", "indeterminate")
    # Both decided but different.
    got = decide.machine_label(1.04, 1.06, 1.02, True, (1.05, 1.05))
    assert (got.label, got.reason) == ("unstable", "analyses-disagree")
    # Agreeing, but one half on the other side of the bar.
    got = decide.machine_label(1.04, 1.08, 1.06, True, (1.02, 1.09))
    assert (got.label, got.reason) == ("unstable", "not-reproduced")


def test_combine_performance_agree() -> None:
    for label in ("improvement", "below-bar", "regression"):
        got = decide.combine_performance(("m1", label, ""), ("m2", label, ""))
        assert got == (label, "")


def test_combine_performance_disagree() -> None:
    label, reason = decide.combine_performance(
        ("mac", "improvement", ""), ("vm", "below-bar", "")
    )
    assert label == "unstable"
    assert reason == "machines disagree; mac: improvement; vm: below-bar"
    label, reason = decide.combine_performance(
        ("mac", "improvement", ""), ("vm", "unstable", "not-reproduced")
    )
    assert label == "unstable"
    assert reason.startswith("one machine unstable;")
    assert "vm: unstable (not-reproduced)" in reason
    label, reason = decide.combine_performance(
        ("mac", "unstable", "indeterminate"),
        ("vm", "unstable", "indeterminate"),
    )
    assert label == "unstable"
    assert reason.startswith("both unstable;")


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("pass", "pass", "pass"),
        ("pass", "fail", "fail"),
        ("fail", "unknown", "fail"),
        ("pass", "unknown", "unknown"),
        ("unknown", "unknown", "unknown"),
    ],
)
def test_combine_correctness(
    first: decide.Correctness,
    second: decide.Correctness,
    expected: str,
) -> None:
    assert decide.combine_correctness(first, second) == expected


def test_correctness_label() -> None:
    label = decide.correctness_label
    assert label({"t::a"}, {"t::a"}, True, 0)[0] == "pass"
    assert label(set(), set(), True, 0)[0] == "pass"
    new = label({"t::a", "t::b"}, {"t::a"}, True, 0)
    assert new == ("fail", "newly failing on the reference tree: t::b")
    assert label(set(), set(), True, 1)[0] == "fail"
    # Failing tests and no task-tree results to compare with.
    assert label({"t::a"}, None, True, 0)[0] == "unknown"
    assert label(None, set(), False, 0)[0] == "unknown"
    assert label(set(), set(), False, 0)[0] == "unknown"
    assert label(set(), set(), True, None)[0] == "unknown"
    # An oracle failure is a failure even when the suite is unreadable.
    assert label(None, None, False, 2)[0] == "fail"
