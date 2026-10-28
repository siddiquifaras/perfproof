"""Tests of the procedure chosen by look schedule (T1.11 split)."""

import itertools

import pytest

from perfproof.schema import (
    CALIBRATION_PENDING,
    CLASS_SCHEDULES,
    LookSchedule,
    TargetClass,
)
from perfproof.stats.sequential import (
    TRACE_BASIS,
    in_calibrated_domain,
    is_class_schedule,
    is_gpu_kernels_schedule,
    procedure_for_schedule,
)

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


def test_the_class_schedule_map_is_exactly_the_documented_one() -> None:
    # "Defaults per target class" in docs/method.md, row by row. A map
    # edit that gives a class another class's schedule (gpu_kernel the
    # python one, say) would make its KEEPs calibrated: pin all six.
    assert dict(CLASS_SCHEDULES) == {
        "python_cpu": (10, 10, 6, 60),
        "native": (10, 10, 6, 60),
        "web_service": (10, 10, 3, 30),
        "agentic": (10, 10, 3, 30),
        "llm_serving": (10, 6, 3, 22),
        "gpu_kernel": (20, 20, 6, 120),
    }
    assert {"gpu_kernel"} == CALIBRATION_PENDING
    assert set(TRACE_BASIS) == set(CLASS_SCHEDULES) - CALIBRATION_PENDING
    # The calibrated domain is the pending-free classes' schedules.
    assert {
        s for c, s in CLASS_SCHEDULES.items() if c not in CALIBRATION_PENDING
    } == set(HAC)


@pytest.mark.parametrize("target_class", sorted(CLASS_SCHEDULES))
def test_a_class_is_in_the_domain_exactly_on_its_own_schedule(
    target_class: TargetClass,
) -> None:
    n0, k, looks, rounds = CLASS_SCHEDULES[target_class]
    assert is_class_schedule(target_class, schedule(n0, k, looks), rounds)
    assert in_calibrated_domain(schedule(n0, k, looks), rounds) is (
        target_class not in CALIBRATION_PENDING
    )
    for other in CLASS_SCHEDULES.values():
        same = other == CLASS_SCHEDULES[target_class]
        found = is_class_schedule(target_class, schedule(*other[:3]), other[3])
        assert found is same


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


@pytest.mark.parametrize(("n0", "k", "looks", "max_rounds"), HAC)
def test_the_three_class_schedules_are_in_the_calibrated_domain(
    n0: int, k: int, looks: int, max_rounds: int
) -> None:
    assert in_calibrated_domain(schedule(n0, k, looks), max_rounds)


def test_the_gpu_kernels_schedule_is_outside_the_domain_without_traces() -> (
    None
):
    # Inside until the owner's decision of 2026-10-28: gpu_kernels
    # has no real traces, so its real-trace acceptance unit is empty and
    # its KEEP is withheld until they exist ("The calibrated domain").
    gpu = schedule(20, 20, 6)
    assert not in_calibrated_domain(gpu, 120)
    assert is_gpu_kernels_schedule(gpu, 120)
    assert not is_gpu_kernels_schedule(schedule(20, 20, 6), 118)
    assert not is_gpu_kernels_schedule(schedule(10, 10, 6), 60)


@pytest.mark.parametrize("alpha", [0.01, 0.049999, 0.1, 0.2])
@pytest.mark.parametrize(
    ("n0", "k", "looks", "max_rounds"), [*HAC, (20, 20, 6, 120)]
)
def test_any_alpha_but_the_default_is_outside_the_domain(
    n0: int, k: int, looks: int, max_rounds: int, alpha: float
) -> None:
    assert not in_calibrated_domain(schedule(n0, k, looks), max_rounds, alpha)


@pytest.mark.parametrize(
    ("n0", "k", "looks", "max_rounds"),
    [
        (12, 10, 6, 60),
        (10, 10, 6, 62),
        (10, 10, 5, 60),
        (20, 20, 6, 118),
        (20, 20, 6, 121),
        (20, 10, 6, 120),
        (10, 10, 4, 30),
    ],
)
def test_a_custom_schedule_is_outside_the_domain(
    n0: int, k: int, looks: int, max_rounds: int
) -> None:
    assert not in_calibrated_domain(schedule(n0, k, looks), max_rounds)


def test_the_domain_is_exactly_the_three_schedules_at_the_default_alpha() -> (
    None
):
    inside = {
        (n0, k, looks, max_rounds, alpha)
        for n0 in (10, 12, 20)
        for k in (2, 5, 6, 10, 12, 20)
        for looks in range(1, 9)
        for max_rounds in range(1, 141)
        for alpha in (0.05, 0.1)
        if in_calibrated_domain(schedule(n0, k, looks), max_rounds, alpha)
    }
    assert inside == {(*key, 0.05) for key in HAC}
