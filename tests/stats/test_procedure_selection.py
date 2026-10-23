"""Tests of the procedure chosen by look schedule (T1.11 split)."""

import itertools

import pytest

from perfproof.schema import LookSchedule
from perfproof.stats.sequential import procedure_for_schedule

HAC = ((10, 10, 6, 60), (10, 10, 3, 30), (10, 6, 3, 22))
GATED = "t_log_ratio_batch_means"


def schedule(n0: int, k: int, looks: int) -> LookSchedule:
    return LookSchedule(n0=n0, k=k, max_looks=looks)


@pytest.mark.parametrize(("n0", "k", "looks", "max_rounds"), HAC)
def test_the_non_gpu_default_schedules_use_hac_lf(
    n0: int, k: int, looks: int, max_rounds: int
) -> None:
    assert (
        procedure_for_schedule(schedule(n0, k, looks), max_rounds) == "hac_lf"
    )


def test_the_gpu_kernels_schedule_keeps_the_gated_primary() -> None:
    assert procedure_for_schedule(schedule(20, 20, 6), 120) == GATED


@pytest.mark.parametrize(
    ("n0", "k", "looks", "max_rounds"),
    [
        (12, 10, 6, 60),
        (10, 8, 6, 60),
        (10, 10, 5, 60),
        (10, 10, 6, 58),
        (10, 10, 6, 62),
        (10, 10, 3, 31),
        (10, 6, 3, 24),
        (10, 2, 1, 10),
        (20, 20, 6, 60),
        (10, 10, 6, 120),
    ],
)
def test_a_schedule_off_the_defaults_falls_back_to_the_gated_primary(
    n0: int, k: int, looks: int, max_rounds: int
) -> None:
    assert procedure_for_schedule(schedule(n0, k, looks), max_rounds) == GATED


def test_only_the_exact_default_tuples_get_hac_lf() -> None:
    # Every neighbour of the grid around the defaults: all four fields
    # matter, and exactly the three default tuples give hac_lf.
    chosen = {
        (n0, k, looks, max_rounds)
        for n0, k, looks, max_rounds in itertools.product(
            (10, 12, 20), (2, 6, 10, 20), (1, 3, 5, 6), (22, 30, 60, 120)
        )
        if procedure_for_schedule(schedule(n0, k, looks), max_rounds)
        == "hac_lf"
    }
    assert chosen == set(HAC)


def test_the_choice_never_raises_on_a_schedule_that_cannot_run() -> None:
    # An odd n0 is refused later, by plan_looks; selection just says
    # gated, so an unknown schedule cannot stop a comparison here.
    assert procedure_for_schedule(schedule(11, 3, 2), 17) == GATED


@pytest.mark.parametrize("alpha", [0.04, 0.025, 0.01, 0.001, 0.06])
@pytest.mark.parametrize(("n0", "k", "looks", "max_rounds"), HAC)
def test_an_alpha_off_the_default_falls_back_to_the_gated_primary(
    n0: int, k: int, looks: int, max_rounds: int, alpha: float
) -> None:
    # A smaller alpha puts the per-look confidence above hac_lf's last
    # tabulated level, which would stop the run mid-comparison.
    chosen = procedure_for_schedule(schedule(n0, k, looks), max_rounds, alpha)
    assert chosen == GATED
