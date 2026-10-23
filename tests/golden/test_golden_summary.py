"""Tests of the admissibility filter of the golden-set summary."""

from golden_stats import LIMIT, cp_upper
from golden_summary import MIN_N, cells_above


def _row(
    method: str, n: int, wrong: int, kind: str = "KEEP"
) -> dict[str, object]:
    return {
        "method": method,
        "n": n,
        "wrong": wrong,
        "wrong_kind": kind,
        "cp_upper": f"{cp_upper(wrong, n):.5f}",
    }


def test_a_cell_of_100_with_no_error_fails_the_registered_rule() -> None:
    assert cp_upper(0, 100) > LIMIT
    assert cp_upper(0, MIN_N) < LIMIT
    rows = [_row("m", 100, 0), _row("m", 200, 0)]
    assert cells_above(rows, "m", 0) == [rows[0]]
    assert cells_above(rows, "m", MIN_N) == []


def test_filter_keeps_only_the_method_and_wrong_keep_cells() -> None:
    rows = [
        _row("a", 200, 2),
        _row("b", 200, 2),
        _row("a", 100, 5, kind="REJECT"),
    ]
    assert cells_above(rows, "a", MIN_N) == [rows[0]]
    assert cells_above(rows, "a", 0) == [rows[0]]


def test_one_error_in_200_passes_and_two_fail() -> None:
    assert cp_upper(1, 200) < LIMIT < cp_upper(2, 200)
    rows = [_row("m", 200, 1), _row("m", 200, 2)]
    assert cells_above(rows, "m", MIN_N) == [rows[1]]
