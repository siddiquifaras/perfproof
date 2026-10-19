"""The pair counter against a brute-force reference."""

import itertools

import pytest

from pairs import build_readings, checksum, count_pairs, render


@pytest.mark.parametrize("n", [0, 1, 2, 7, 60])
def test_matches_brute_force(n: int) -> None:
    values = build_readings(n)
    expected = sum(
        1 for a, b in itertools.combinations(values, 2) if a + b == n
    )
    assert count_pairs(values, n) == expected


def test_repeated_values_pair_every_time() -> None:
    assert count_pairs([2, 2, 2, 2], 4) == 6


def test_render_names_both_counts_and_the_checksum() -> None:
    assert render(3, 5, 99) == "3 of 10 pairs match (99)"


def test_checksum_is_the_sum_of_the_mixed_readings() -> None:
    assert checksum([1, 2], passes=1) == (31 + 7) + (62 + 7)
    assert checksum([5], passes=0) == 5
