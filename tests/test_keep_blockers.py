"""Every site that decides what withholds a KEEP agrees, from one table.

The sites are `perfproof.stats.decision.decide`, `bench.compare._Run`
(`_keep_blocked`, `_fixed_blocker`) and
`RunRecord._check_keep_blockers`.
"""

from types import SimpleNamespace
from typing import Any, Final, cast, get_args

import pytest

from perfproof.bench import compare
from perfproof.bench.compare import _Run  # pyright: ignore[reportPrivateUsage]
from perfproof.schema import (
    KEEP_BLOCKING_WARNINGS,
    GateResult,
    GateStatus,
    GuardrailStatus,
    RunRecord,
)
from perfproof.stats.decision import GATE_PRECEDENCE, decide

GATE_WITHHOLDS: Final = {
    "passed": False,
    "failed": False,  # a REJECT by rule 1, which keeps nothing either
    "waived": False,
    "not_available": True,
}
GUARDRAIL_WITHHOLDS: Final = {
    "passed": False,
    "breached": True,
    "not_established": True,
    "unmeasurable": True,
    "not_declared": True,
    "not_applicable": False,
}
# Withheld for a reason no further round lifts, as opposed to pending.
GUARDRAIL_FIXED: Final = {"unmeasurable", "not_declared"}
WARNING_CODES: Final = {
    "counter_unavailable",
    "resolution_unknown",
    "schedule_uncalibrated",
    "schedule_not_class_own",
    "metric_not_calibrated",
    "target_class_unknown",
    "target_class_unverified",
    "workload_not_confirmed",
}


def _gates(status: str) -> list[GateResult]:
    return [
        GateResult.model_validate(
            {
                "gate": g,
                "status": status if g == "lock" else "passed",
                "detail": "x" if g == "lock" and status != "passed" else "",
            }
        )
        for g in GATE_PRECEDENCE
    ]


def _decide(status: str, guardrail: str | None) -> str:
    named = None if guardrail is None else {"peak_memory": guardrail}
    return decide(
        _gates(status),
        (1.5, 1.6),
        min_effect=0.05,
        budget_remains=True,
        calibrated=True,
        guardrails=cast("Any", named),
    ).outcome


def _schema_refuses_keep(
    gate: str = "passed", guardrail: str = "passed", warning: str = "x"
) -> bool:
    fake = SimpleNamespace(
        verdict=SimpleNamespace(outcome="KEEP", reason=None, rule=2),
        guardrails=[SimpleNamespace(metric="peak_memory", status=guardrail)],
        warnings=[SimpleNamespace(code=warning)],
    )
    try:
        RunRecord._check_keep_blockers(  # pyright: ignore[reportPrivateUsage]
            cast("Any", fake), {"lock": gate}
        )
    except ValueError:
        return True
    return False


def _compare_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    gate: str = "passed",
    guardrail: str | None = None,
    calibration: tuple[str, str] | None = None,
    fallback: str | None = None,
    resolution_unknown: bool = False,
) -> Any:
    def no_spec_block(_spec: object) -> bool:
        return False

    def calibration_blocker(
        *_a: object, **_k: object
    ) -> tuple[str, str] | None:
        return calibration

    monkeypatch.setattr(compare, "keep_blocked", no_spec_block)
    monkeypatch.setattr(compare, "_calibration_blocker", calibration_blocker)
    looks = {}
    if guardrail is not None:
        looks["peak_memory"] = SimpleNamespace(
            look=SimpleNamespace(status=[guardrail])
        )
    run: Any = SimpleNamespace(
        spec=None,
        class_problem=None,
        fallback_reason=fallback,
        gates={"lock": SimpleNamespace(status=gate)},
        guardrail_looks=looks,
        _families=lambda: frozenset[str](),
        _resolution_unknown=lambda: resolution_unknown,
    )
    for name in ("_guardrails_with", "_fixed_blocker", "_keep_blocked"):
        setattr(run, name, getattr(_Run, name).__get__(run))
    return run


def test_the_tables_cover_every_status() -> None:
    assert set(GATE_WITHHOLDS) == set(get_args(GateStatus))
    assert set(GUARDRAIL_WITHHOLDS) == set(get_args(GuardrailStatus))
    assert KEEP_BLOCKING_WARNINGS == WARNING_CODES


@pytest.mark.parametrize("status", sorted(GATE_WITHHOLDS))
def test_every_site_agrees_on_a_gate_status(
    status: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    want = GATE_WITHHOLDS[status]
    if status != "waived":  # not waivable on one host, which decide tests
        kept = _decide(status, "passed") == "KEEP"
        assert kept is (status == "passed")
    assert _schema_refuses_keep(gate=status) is want
    run = _compare_run(monkeypatch, gate=status)
    assert run._keep_blocked() is want


@pytest.mark.parametrize("status", sorted(GUARDRAIL_WITHHOLDS))
def test_every_site_agrees_on_a_guardrail_status(
    status: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    want = GUARDRAIL_WITHHOLDS[status]
    assert (_decide("passed", status) != "KEEP") is want
    assert _schema_refuses_keep(guardrail=status) is want
    # not_declared is never a look status: compare reads it from the
    # spec through `keep_blocked(spec)`.
    if status != "not_declared":
        run = _compare_run(monkeypatch, guardrail=status)
        assert run._keep_blocked() is want
        assert run._fixed_blocker() is (status in GUARDRAIL_FIXED)
    # Fixed reasons end INCONCLUSIVE now; a pending one continues.
    if want and status != "breached":
        expect = "INCONCLUSIVE" if status in GUARDRAIL_FIXED else "CONTINUE"
        assert _decide("passed", status) == expect


def test_a_missing_guardrail_map_is_a_fixed_blocker() -> None:
    assert _decide("passed", None) == "INCONCLUSIVE"


@pytest.mark.parametrize("code", sorted(WARNING_CODES | {"high_load"}))
def test_every_site_agrees_on_a_warning_code(
    code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    want = code in WARNING_CODES
    assert _schema_refuses_keep(warning=code) is want
    # compare raises these warnings from the conditions it checks; the
    # workload one is checked apart from `_fixed_blocker`.
    conditions: dict[str, dict[str, Any]] = {
        "counter_unavailable": {"fallback": "wall time"},
        "resolution_unknown": {"resolution_unknown": True},
    }
    if code in conditions:
        run = _compare_run(monkeypatch, **conditions[code])
        assert run._fixed_blocker() is True
    elif code not in ("high_load", "workload_not_confirmed"):
        run = _compare_run(monkeypatch, calibration=(code, "x"))
        assert run._fixed_blocker() is True
