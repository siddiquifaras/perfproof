"""Render a `RunRecord` as the readable ``report.md``.

The report is a pure function of the record. It reads no clock, no
environment, no locale and no file, so the same record renders to the
same bytes in any process and any time zone. Every timestamp is the
one the record holds, printed with the offset it was recorded with.

It computes nothing the record does not hold: no percentages, no
durations, no bar. The one comparison of recorded values, cross-host
session pairs against ``n0``, only picks which fixed explanation to
give, and is the same test `perfproof.schema.RunRecord` applies.
What it adds is fixed text, chosen by recorded fields only, saying
what a verdict, label or status means as ``docs/method.md`` defines
it. So the report can never claim more than was recorded, which is
failure 20 of ``docs/failure-modes.md``.

Numbers are formatted one fixed way and never rounded. Every recorded
number is printed exactly, as Python's shortest round-trip ``repr`` of
the stored float64, so nobody has to guess what decided: a rounded
bound could sit on the bar or on 1 and seem to contradict the verdict.

Free text from the record (details, messages, reasons, outputs) is
escaped so it cannot change the Markdown around it.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Final, cast, get_args

from pydantic import BaseModel

from perfproof.schema import (
    CommandOutput,
    DifferentialCheck,
    GateName,
    GateResult,
    GuardrailResult,
    GuardrailSpec,
    Interval,
    RunRecord,
    Verdict,
)

__all__ = ["render"]

_GATE_ORDER: Final[tuple[GateName, ...]] = get_args(GateName)
"""Gates in the order rule 1 of ``docs/method.md`` names them."""

_OPT_IN: Final[Mapping[GateName, tuple[str, str]]] = {
    "side_effects": (
        "allow_remote_services",
        "Remote services allowed: the benchmark may reach a service "
        "address whose host is not local.",
    ),
    "runtime": (
        "allow_runtime_change",
        "Runtime change allowed: the interpreter or runtime may differ "
        "between the trees.",
    ),
    "coverage": (
        "allow_unchecked_coverage",
        "Coverage not checked.",
    ),
}
"""Spec opt-in and verdict label of each waivable gate but `same_host`,
whose waiver is the cross-host label."""

_STATUS_TEXT: Final[Mapping[str, str]] = {
    "passed": "passed",
    "failed": "failed",
    "waived": "waived",
    "not_available": "not available",
    "breached": "breached",
    "not_established": "not established",
    "unmeasurable": "unmeasurable",
    "not_applicable": "not applicable",
}

_ESCAPED: Final = frozenset("\\`*_[]<>|~&")
"""ASCII punctuation that could start Markdown inside a line."""


def render(record: RunRecord) -> str:
    """Render ``record`` as the text of ``report.md``.

    Parameters
    ----------
    record : RunRecord
        A validated run record.

    Returns
    -------
    str
        Markdown, ending in one newline, with no trailing spaces. The
        same record always gives the same string.
    """
    sections = (
        _title(record),
        _verdict(record),
        _claim(record),
        _measured(record),
        _gates(record),
        _guardrails(record),
        _calibration(record),
        _warnings(record),
        _machines(record),
        _environments(record),
        _settings(record),
        _run(record),
        _files(record),
    )
    return "\n\n".join(sections) + "\n"


# Formatting. Each function maps one recorded value to fixed text.


def _num(value: float) -> str:
    """Print a float64 exactly, as its shortest round-trip form."""
    return repr(value)


def _flag(value: bool) -> str:
    return "yes" if value else "no"


def _plain(text: str) -> bool:
    """Whether ``text`` is one line with no control characters."""
    return not any(ord(c) < 32 or ord(c) == 127 for c in text)


def _longest_backtick_run(text: str) -> int:
    longest = run = 0
    for c in text:
        run = run + 1 if c == "`" else 0
        longest = max(longest, run)
    return longest


def _code(text: str) -> str:
    """Render ``text`` as an inline code span.

    Text with a line break, a control character or surrounding spaces
    is shown as its JSON string literal instead, so it stays on one
    line and every character remains visible.
    """
    if not _plain(text) or text != text.strip():
        text = json.dumps(text, ensure_ascii=False)
    fence = "`" * (_longest_backtick_run(text) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _text(text: str) -> str:
    """Render free text inline, with Markdown punctuation escaped."""
    if not text:
        return "(empty)"
    if not _plain(text) or text != text.strip():
        return _code(text)
    return "".join(f"\\{c}" if c in _ESCAPED else c for c in text)


def _argv(argv: Sequence[str]) -> str:
    """Render an argv list as its JSON array, in a code span."""
    return _code(json.dumps(list(argv), ensure_ascii=False))


def _time(value: datetime) -> str:
    """Print a recorded time with the offset it was recorded with."""
    return value.isoformat()


def _link(path: str) -> str:
    """Link a path relative to the run directory."""
    if not _plain(path):
        return _code(path)
    target = "".join(f"\\{c}" if c in "<>\\" else c for c in path)
    return f"[{_code(path)}](<{target}>)"


def _fenced(text: str) -> list[str]:
    """Render multi-line output as a fenced block, or say it's empty."""
    if not text:
        return ["(empty)"]
    if not _plain(text.replace("\n", "").replace("\t", "")):
        text = json.dumps(text, ensure_ascii=False)
    fence = "`" * max(3, _longest_backtick_run(text) + 1)
    body = text if text.endswith("\n") else text + "\n"
    return [f"{fence}text", *body.split("\n")[:-1], fence]


def _scalar(value: object) -> str:
    """Render one recorded value of a fingerprint or environment."""
    if isinstance(value, bool):
        return _flag(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _num(value)
    if isinstance(value, str):
        return _code(value)
    if isinstance(value, datetime):
        return _time(value)
    if isinstance(value, tuple):
        items = cast("tuple[object, ...]", value)
        return ", ".join(_scalar(v) for v in items) or "none"
    msg = f"no rendering for {type(value).__name__}"
    raise TypeError(msg)


def _interval(iv: Interval, unit: str | None = None) -> str:
    """Render an interval; on the ratio scale when ``unit`` is None."""
    scale = "" if unit is None else f" {_text(unit)}"
    return (
        f"{_num(iv.lower)} to {_num(iv.upper)}{scale} at confidence "
        f"{_num(iv.confidence)}"
    )


def _bullets(items: Iterable[str], indent: int = 0) -> list[str]:
    pad = " " * indent
    return [f"{pad}- {item}" for item in items]


# Sections, in the order the report gives them.


def _title(record: RunRecord) -> str:
    return (
        f"# perfproof report: {_code(record.spec.name)}, run "
        f"{_code(record.run_id)}"
    )


def _gate_status(record: RunRecord) -> dict[GateName, GateResult]:
    return {g.gate: g for g in record.gates}


def _headline(v: Verdict) -> str:
    line = f"**{v.outcome}**, reason `{v.reason}`, decided by rule {v.rule}"
    if v.gate is not None:
        line += f", gate `{v.gate}`"
    # Only rules 2 and 3 are decided by the interval. Under rule 5 it
    # decided nothing (it straddled the bar, too few cross-host pairs,
    # or an uncalibrated procedure), so it stays out of the headline;
    # Measured still shows it.
    if v.rule in (2, 3) and v.estimate is not None and v.interval is not None:
        line += (
            f": speedup {_num(v.estimate)}, interval "
            f"{_num(v.interval.lower)} to {_num(v.interval.upper)}"
        )
    return line + "."


def _meaning(record: RunRecord) -> str:
    """Say what the verdict means, chosen by recorded fields only."""
    v = record.verdict
    if v.rule == 1 and v.gate is None:
        return (
            "The resolution stop ended the comparison at a look: a value "
            "was no larger than its own resolution, or a `reported` metric "
            "with no declared resolution failed the continuity check "
            "(`coarse_resolution`). Neither a speedup above the bar nor its "
            "absence is shown. Nothing is kept. More printed digits, a "
            "declared resolution or more work per execution can resolve "
            "it."
        )
    if v.gate is not None:
        if v.reason == "integrity":
            first = (
                f"The experiment itself is not valid: the `{v.gate}` gate "
                "failed."
            )
        elif v.gate == "coverage":
            first = (
                "The candidate changed lines that no check ran: the "
                "`coverage` gate failed."
            )
        else:
            first = (
                "The candidate failed the `correctness` gate: it gave "
                "different results from the baseline, or it failed to run."
            )
        return (
            f"{first} Gates run before any timing, so this verdict carries "
            "no speedup estimate and says nothing about speed. A REJECT "
            "keeps nothing."
        )
    if v.rule == 2 and v.outcome == "KEEP":
        return (
            "The interval's lower bound is above the bar 1 + min_effect, so "
            "the candidate is faster than the baseline by more than "
            "min_effect on this benchmark's metric. Every gate passed or was "
            "waived by the spec's opt-in, and every guardrail passed or is "
            "not applicable."
        )
    if v.rule == 2 and v.outcome == "REJECT":
        return (
            "The interval's lower bound is above the bar 1 + min_effect, but "
            "a guardrail is shown worse than its allowance or an absolute "
            "limit. A REJECT keeps nothing. The speedup estimate stays to "
            "show what the speed cost."
        )
    if v.rule == 2:
        return (
            "The interval's lower bound is above the bar 1 + min_effect, but "
            "a KEEP needs every gate checked and every guardrail shown "
            "within its allowance and limits, and that was not shown. "
            "Nothing is kept."
        )
    if v.rule == 3:
        text = (
            "The interval's upper bound is at or below the bar "
            "1 + min_effect, so a speedup above the bar is ruled out. A "
            "REJECT keeps nothing. It says nothing about correctness beyond "
            "the gates having passed."
        )
        if v.regression:
            text += (
                " The upper bound is also below 1: the candidate is slower "
                "than the baseline, flagged as a regression."
            )
        return text
    withheld = _withheld(v)
    if withheld is not None:
        return withheld
    if v.diagnostics.procedure_uncalibrated:
        return (
            "The interval procedure had not passed calibration when this "
            "verdict was given, so rules 2 and 3 could not decide: the "
            "comparison ran to the end of its budget and is INCONCLUSIVE "
            "whatever its interval shows. Nothing is kept."
        )
    n0 = record.spec.looks.n0
    # The test RunRecord applies: below n0 cross-host pairs the interval
    # is exempt from the straddle check, so it need not straddle.
    if v.session_pairs is not None and v.session_pairs < n0:
        return (
            f"Only {v.session_pairs} cross-host session pairs were "
            f"completed, fewer than n0 = {n0}, and below n0 speed never "
            "decides, so the interval decided nothing whatever it shows. "
            "Neither a speedup above the bar nor its absence is shown. "
            "Nothing is kept."
        )
    if v.interval is None:
        return (
            "The budget ran out before any interval was computed. Neither "
            "a speedup above the bar 1 + min_effect nor its absence is "
            "shown. Nothing is kept."
        )
    return (
        "The budget ran out with the interval still straddling the bar "
        "1 + min_effect. Neither a speedup above the bar nor its absence is "
        "shown, however good the estimate looks. Nothing is kept."
    )


def _blockers(record: RunRecord) -> list[str]:
    """List what blocked a rule 2 KEEP, read from recorded statuses."""
    lines: list[str] = []
    gates = _gate_status(record)
    for name in _GATE_ORDER:
        gate = gates.get(name)
        if gate is not None and gate.status == "not_available":
            lines.append(
                f"Gate `{name}`: not available ({_text(gate.detail)})"
            )
    for result in record.guardrails:
        if result.status == "not_established":
            lines.append(
                f"Guardrail `{result.metric}`: not established (measured, "
                "but the data can't tell yet)"
            )
        elif result.status == "unmeasurable":
            lines.append(
                f"Guardrail `{result.metric}`: unmeasurable "
                f"({_text(result.reason)})"
            )
    return lines


def _labels(record: RunRecord) -> list[str]:
    """Labels the verdict carries, from recorded fields only."""
    v = record.verdict
    labels: list[str] = []
    if v.diagnostics.procedure_uncalibrated:
        labels.append(
            "Procedure not calibrated: the interval procedure had not "
            "passed calibration when this verdict was given."
        )
    if v.calibration == "uncalibrated":
        labels.append("Not calibrated for this target class.")
    if v.cross_host:
        labels.append(
            "Cross-host: decided under the alternating fresh-host session "
            f"design, with {v.session_pairs} session pairs. It rests on the "
            "assumption that the pool allocates hosts independently of "
            "which version runs, which perfproof cannot check. Calibration "
            "of this design cannot cover a pool that hands one version "
            "systematically different hosts. Gate `same_host` waived by "
            "`allow_cross_host`."
        )
    gates = _gate_status(record)
    for name in _GATE_ORDER:
        gate = gates.get(name)
        if gate is not None and gate.status == "waived" and name in _OPT_IN:
            opt_in, text = _OPT_IN[name]
            labels.append(f"{text} Gate `{name}` waived by `{opt_in}`.")
    return labels


def _verdict(record: RunRecord) -> str:
    v = record.verdict
    lines = ["## Verdict", "", _headline(v)]
    if v.diagnostics.metric_disagreement:
        lines += [
            "",
            "**Metric disagreement.** One of the spec-metric and "
            "wall-clock intervals shows the candidate faster and the other "
            "excludes that. The verdict comes from the spec's metric; "
            "both intervals are under Measured.",
        ]
    lines += ["", _meaning(record)]
    if v.rule == 2 and v.outcome == "INCONCLUSIVE":
        lines += ["", "Blocked by:", "", *_bullets(_blockers(record))]
    if v.reason == "guardrail":
        breached = [
            f"`{r.metric}`"
            for r in record.guardrails
            if r.status == "breached"
        ]
        lines += ["", f"Breached: {', '.join(breached)}."]
    labels = _labels(record)
    if labels:
        lines += ["", "Labels:", "", *_bullets(labels)]
    return "\n".join(lines)


def _claim(record: RunRecord) -> str:
    v, spec = record.verdict, record.spec
    metric = spec.metric
    scope = (
        f"Benchmark {_code(spec.name)} at spec hash `{spec.spec_hash}`, "
        f"baseline commit `{record.baseline_commit}`. Metric "
        f"`{metric.kind}` in {_code(metric.unit)}, "
        f"{metric.direction.replace('_', ' ')}"
    )
    if spec.objective is not None:
        scope += f", objective `{spec.objective}`"
    scope += f"; min_effect = {_num(spec.min_effect)} as stored."
    if v.gate is not None:
        claim = (
            f"The `{v.gate}` gate failed. No claim about speed."
            if v.reason == "integrity"
            else f"The candidate failed the `{v.gate}` gate. No claim about "
            "speed."
        )
    elif v.outcome == "KEEP":
        claim = (
            "The candidate is faster than the baseline by a ratio above "
            "1 + min_effect on this benchmark's metric, on the machines "
            "recorded under Machines, with the checks under Gates and "
            "Guardrails. The claim covers this benchmark command and its "
            "declared inputs only."
        )
    elif v.reason == "guardrail":
        claim = (
            "The candidate is faster than the baseline by a ratio above "
            "1 + min_effect, and a guardrail is shown worse than allowed. "
            "It is not kept."
        )
    elif v.outcome == "REJECT":
        claim = (
            "The candidate is not faster than the baseline by a ratio "
            "above 1 + min_effect on this benchmark's metric."
        )
        if v.regression:
            claim += " It is slower: the interval's upper bound is below 1."
    else:
        claim = (
            "No claim. The evidence recorded here does not settle whether "
            "the candidate should be kept."
        )
    if v.outcome != "INCONCLUSIVE" and _labels(record):
        claim += " The labels under Verdict qualify it."
    return "\n".join(
        ["## Claim", "", claim, "", scope, "", _provenance(record)]
    )


def _provenance(record: RunRecord) -> str:
    """Name the repository and candidate commit, or say neither is."""
    unrecorded: list[str] = []
    parts: list[str] = []
    if record.repository is None:
        unrecorded.append("the repository")
    else:
        parts.append(f"Repository {_code(record.repository)}.")
    if record.candidate_commit is None:
        unrecorded.append("the candidate's commit")
    else:
        dirty = (
            ", with changes not in that commit"
            if record.candidate_dirty
            else ", with no changes beyond it"
        )
        parts.append(f"Candidate commit `{record.candidate_commit}`{dirty}.")
    if unrecorded:
        parts.append(f"Not recorded: {' and '.join(unrecorded)}.")
    return " ".join(parts)


def _withheld(v: Verdict) -> str | None:
    """Say why the diagnostic gate withheld the last look, if it did."""
    d = v.diagnostics
    if d.autocorrelated_rounds:
        what = (
            "the lag-1 autocorrelation of the log ratios was above the "
            "gate's limit, and a look this short cannot show that the "
            "interval covers (`autocorrelated_rounds`)"
        )
    elif d.outlier_rounds:
        what = (
            "the share of outlier rounds was above the gate's limit, and a "
            "look this short cannot show that the interval covers "
            "(`outlier_rounds`)"
        )
    else:
        return None
    text = (
        "The diagnostic gate withheld the decision at the last look: "
        f"{what}. The interval is recorded but decides nothing. Nothing "
        "is kept."
    )
    if d.procedure_uncalibrated:
        return text + (
            " Two things stand in the way: the gate's reason, which more "
            "rounds or less dependence between rounds would lift, and the "
            "interval procedure not having passed calibration, which no "
            "number of rounds can change, so this comparison is "
            "INCONCLUSIVE until it has."
        )
    return text + (
        " More rounds, or less dependence between rounds, can resolve it."
    )


def _measured(record: RunRecord) -> str:
    v, spec = record.verdict, record.spec
    lines = ["## Measured", ""]
    if v.gate is not None:
        lines += [
            "Not measured: gates run before any timing, so this verdict "
            "carries no speedup estimate or interval.",
            "",
        ]
    items: list[str] = []
    if v.gate is None:
        if v.diagnostics.procedure_uncalibrated:
            # Before calibration the number backs no claim of speed.
            items.append(
                "Uncalibrated estimate: "
                + (
                    "not computed"
                    if v.estimate is None
                    else f"{_num(v.estimate)} (old time over new time); "
                    "not a verdict"
                )
            )
        else:
            items.append(
                "Speedup estimate: "
                + (
                    "not computed"
                    if v.estimate is None
                    else f"{_num(v.estimate)}; above 1 means the candidate "
                    "is faster"
                )
            )
        items.append(
            "Interval at the last look: "
            + ("not computed" if v.interval is None else _interval(v.interval))
        )
    items += [
        f"Rounds: {v.rounds}, of at most {spec.budget.max_rounds}",
        f"Looks: {v.looks}, of at most {spec.looks.max_looks}",
        f"Procedure: `{v.procedure}`",
        f"Regression: {_flag(v.regression)}",
        f"Cross host: {_flag(v.cross_host)}",
    ]
    if v.session_pairs is not None:
        items.append(f"Session pairs: {v.session_pairs}")
    lines += _bullets(items)
    d = v.diagnostics
    diagnostics = [
        f"procedure_uncalibrated: {_flag(d.procedure_uncalibrated)}",
        "metric_disagreement: "
        + (
            "not computed"
            if d.metric_disagreement is None
            else _flag(d.metric_disagreement)
        ),
        "wall_interval: "
        + (
            "not computed"
            if d.wall_interval is None
            else _interval(d.wall_interval)
        ),
        "coarse_resolution: "
        + (
            "not computed"
            if d.coarse_resolution is None
            else _flag(d.coarse_resolution)
        ),
    ]
    for name, flagged in (
        ("autocorrelated_rounds", d.autocorrelated_rounds),
        ("outlier_rounds", d.outlier_rounds),
    ):
        if flagged:
            diagnostics.append(f"{name}: {_flag(flagged)} (decision withheld)")
    for name, value in (
        ("lag1_autocorrelation", d.lag1_autocorrelation),
        ("cv_baseline", d.cv_baseline),
        ("cv_candidate", d.cv_candidate),
        ("sd_log_ratio", d.sd_log_ratio),
        ("resolution_widening", d.resolution_widening),
    ):
        shown = "not computed" if value is None else _num(value)
        diagnostics.append(f"{name}: {shown}")
    lines += ["", "### Diagnostics", "", *_bullets(diagnostics)]
    return "\n".join(lines)


def _differential(check: DifferentialCheck) -> list[str]:
    capped = (
        " (the capture limit, so a bound: any later calls were not captured)"
        if check.captured == check.capture_limit
        else ""
    )
    lines = _bullets(
        [
            f"Differential check {_code(check.function)}: "
            f"{check.captured} captured{capped}, {check.replayed} replayed, "
            f"{check.generated} generated, {check.mismatches} mismatched"
        ],
        2,
    )
    lines += _bullets(
        (
            f"Skipped at {s.stage}: {s.count} calls, {_text(s.reason)}"
            for s in check.skipped
        ),
        4,
    )
    lines += _bullets(
        (
            f"Difference {_code(d.item)}: baseline "
            f"{_side(d.baseline)}, candidate {_side(d.candidate)}"
            for d in check.differences
        ),
        4,
    )
    return lines


def _side(value: str | None) -> str:
    return "absent" if value is None else _code(value)


def _gate_lines(gate: GateResult) -> list[str]:
    lines = _bullets([f"`{gate.gate}`: {_STATUS_TEXT[gate.status]}"])
    if gate.detail:
        lines += _bullets([f"Detail: {_text(gate.detail)}"], 2)
    lines += _bullets(
        (
            f"Difference {_code(d.item)}: baseline {_side(d.baseline)}, "
            f"candidate {_side(d.candidate)}"
            for d in gate.differences
        ),
        2,
    )
    if gate.truncated:
        lines += _bullets(["More differences existed than are listed."], 2)
    lines += _bullets(
        (
            f"Quarantined flaky test {_code(q.test_id)}, left out of the "
            "judgement; baseline results "
            + ", ".join(f"`{r}`" for r in q.baseline_results)
            for q in gate.quarantined
        ),
        2,
    )
    for check in gate.differential:
        lines += _differential(check)
    lines += _bullets(
        (
            f"Unverified lines in {_code(u.path)}: "
            + ", ".join(str(n) for n in u.lines)
            for u in gate.unverified
        ),
        2,
    )
    if gate.output is not None:
        lines += _bullets(
            ["The failing command's output is under its own heading below."],
            2,
        )
    return lines


def _output(gate: GateName, out: CommandOutput) -> list[str]:
    return [
        "",
        f"### Output of the command that failed `{gate}`",
        "",
        *_bullets(
            [
                f"Command: {_argv(out.command)}",
                f"Exit code: {out.exit_code}",
                f"Timed out: {_flag(out.timed_out)}",
                f"Cut to its tail: {_flag(out.truncated)}",
            ]
        ),
        "",
        "stdout:",
        "",
        *_fenced(out.stdout),
        "",
        "stderr:",
        "",
        *_fenced(out.stderr),
    ]


def _gates(record: RunRecord) -> str:
    lines = ["## Gates", ""]
    gates = _gate_status(record)
    for name in _GATE_ORDER:
        gate = gates.get(name)
        if gate is None:
            lines += _bullets([f"`{name}`: not listed in this record"])
        else:
            lines += _gate_lines(gate)
    for name in _GATE_ORDER:
        gate = gates.get(name)
        if gate is not None and gate.output is not None:
            lines += _output(name, gate.output)
    return "\n".join(lines)


def _guardrail_lines(
    spec: GuardrailSpec, result: GuardrailResult
) -> list[str]:
    head = f"`{result.metric}`: {_STATUS_TEXT[result.status]}"
    items: list[str] = []
    if result.reason:
        items.append(f"Reason: {_text(result.reason)}")
    if spec.applicable:
        items.append(
            f"Unit {_code(spec.unit)}, {spec.direction.replace('_', ' ')}"
        )
        if spec.reason:
            items.append(f"Spec reason: {_text(spec.reason)}")
    if result.estimate is not None:
        items.append(
            f"Worsening ratio estimate: {_num(result.estimate)}; above 1 "
            "means the candidate is worse"
        )
    if spec.allowance is not None:
        items.append(
            f"Allowance: {_num(spec.allowance)} from "
            f"`{spec.allowance_source}`, refused beyond 1 + allowance"
        )
        items.append(
            "Worsening interval: "
            + (
                "not computed"
                if result.worsening is None
                else _interval(result.worsening)
            )
        )
    elif result.worsening is not None:
        items.append(f"Worsening interval: {_interval(result.worsening)}")
    kind = "ceiling" if spec.direction == "lower_is_better" else "floor"
    items += [
        f"Absolute limit, a {kind}: {_text(limit.label)}, "
        f"{_num(limit.value)} {_text(spec.unit)}"
        for limit in spec.limits
    ]
    if spec.limits or result.candidate is not None:
        items.append(
            "Candidate interval: "
            + (
                "not computed"
                if result.candidate is None
                else _interval(result.candidate, spec.unit)
            )
        )
    return [*_bullets([head]), *_bullets(items, 2)]


def _guardrails(record: RunRecord) -> str:
    lines = ["## Guardrails", ""]
    if record.verdict.gate is not None:
        lines.append(
            "None evaluated: a gate REJECT carries no guardrail results. "
            "The spec's guardrails are under Settings."
        )
        return "\n".join(lines)
    if not record.guardrails:
        lines.append("None declared in the spec.")
        return "\n".join(lines)
    specs = {g.metric: g for g in record.spec.guardrails}
    for result in record.guardrails:
        lines += _guardrail_lines(specs[result.metric], result)
    return "\n".join(lines)


def _calibration(record: RunRecord) -> str:
    v = record.verdict
    target = (
        "unknown, so every default guardrail had to be declared"
        if v.target_class is None
        else f"`{v.target_class}`"
    )
    items = [
        f"Interval procedure: `{v.procedure}`",
        "Procedure uncalibrated when this verdict was given: "
        + _flag(v.diagnostics.procedure_uncalibrated),
        f"Target class: {target}",
        f"Calibration for the target class: `{v.calibration}`",
    ]
    if v.calibration == "calibrated":
        items.append(
            "Calibrated under these conditions only, not in general: "
            + "; ".join(_text(c) for c in v.calibration_conditions)
        )
    else:
        items.append("Not calibrated for this target class.")
    return "\n".join(["## Calibration", "", *_bullets(items)])


def _warnings(record: RunRecord) -> str:
    lines = ["## Warnings", ""]
    if not record.warnings:
        lines.append("None recorded.")
    lines += _bullets(
        f"`{w.code}`: {_text(w.message)}" for w in record.warnings
    )
    return "\n".join(lines)


def _model_fields(
    model: BaseModel, missing: Mapping[str, str], skip: Iterable[str] = ()
) -> list[str]:
    """Render each field in declaration order; ``None`` as unknown."""
    omitted = {"missing", *skip}
    items: list[str] = []
    for name in type(model).model_fields:
        if name in omitted:
            continue
        value: object = getattr(model, name)
        if value is None and name not in missing:
            # The schema lets a field that doesn't apply, such as an
            # interpreter's linked libraries, be None with no reason.
            items.append(f"{name}: not applicable")
        elif value is None:
            items.append(f"{name}: unknown ({_text(missing[name])})")
        else:
            items.append(f"{name}: {_scalar(value)}")
    return items


def _machines(record: RunRecord) -> str:
    lines = ["## Machines"]
    for session, fp in enumerate(record.machines):
        lines += ["", f"### Session {session}", ""]
        lines += _bullets(_model_fields(fp, fp.missing, ("gpus",)))
        if fp.gpus is None:
            lines += _bullets([f"gpus: unknown ({_text(fp.missing['gpus'])})"])
        elif not fp.gpus:
            lines += _bullets(["gpus: none"])
        for index, gpu in enumerate(fp.gpus or ()):
            lines += _bullets([f"gpu {index}"])
            lines += _bullets(_model_fields(gpu, gpu.missing), 2)
    return "\n".join(lines)


def _environments(record: RunRecord) -> str:
    lines = ["## Environments"]
    for env in record.environments:
        lines += ["", f"### `{env.version}`, session {env.session}", ""]
        items = _model_fields(
            env,
            env.missing,
            (
                "version",
                "session",
                "env_values",
                "dependency_locks",
                "linked_libraries",
            ),
        )
        why = env.missing.get("linked_libraries")
        if env.linked_libraries is None:
            items.append(
                "linked_libraries: "
                + (
                    "not applicable"
                    if why is None
                    else f"unknown ({_text(why)})"
                )
            )
        elif not env.linked_libraries:
            items.append("linked_libraries: none")
        items += [
            f"linked_library {_code(lib.name)}: `{lib.source}`"
            + ("" if lib.path is None else f", {_code(lib.path)}")
            + ("" if lib.sha256 is None else f", `{lib.sha256}`")
            for lib in env.linked_libraries or ()
        ]
        items += [
            f"env_values {_code(name)}: {_code(env.env_values[name])}"
            for name in sorted(env.env_values)
        ]
        if env.dependency_locks is None:
            items.append(
                "dependency_locks: unknown "
                f"({_text(env.missing['dependency_locks'])})"
            )
        elif not env.dependency_locks:
            items.append("dependency_locks: none")
        items += [
            f"dependency_lock {_code(lock.path)}: `{lock.sha256}`"
            for lock in env.dependency_locks or ()
        ]
        lines += _bullets(items)
    return "\n".join(lines)


def _paths(paths: Sequence[str]) -> str:
    return ", ".join(_code(p) for p in paths) or "none"


def _settings(record: RunRecord) -> str:
    spec = record.spec
    c = spec.correctness
    items = [
        f"name: {_code(spec.name)}",
        f"spec_hash: `{spec.spec_hash}`",
        f"schema_version: {spec.schema_version}",
        f"command: {_argv(spec.command)}",
        f"cwd: {_code(spec.cwd)}",
    ]
    items += [
        f"env {_code(name)}: {_code(spec.env[name])}"
        for name in sorted(spec.env)
    ]
    items += [
        f"metric: kind `{spec.metric.kind}`, "
        f"{spec.metric.direction.replace('_', ' ')}, unit "
        + _code(spec.metric.unit)
        + (
            ""
            if spec.metric.event is None
            else f", event {_code(spec.metric.event)}"
        ),
        "objective: "
        + ("none" if spec.objective is None else f"`{spec.objective}`"),
        "correctness command: "
        + ("none" if c.command is None else _argv(c.command)),
        f"correctness cwd: {_code(c.cwd)}",
        "correctness timeout_seconds: "
        + ("none" if c.timeout_seconds is None else _num(c.timeout_seconds)),
    ]
    items += [
        f"correctness output {_code(o.path)}: compare `{o.compare}`, rtol "
        f"{_num(o.rtol)}, atol {_num(o.atol)}, equal_nan "
        f"{_flag(o.equal_nan)}"
        for o in c.outputs
    ]
    items += [
        f"warmup: {spec.warmup}",
        f"budget: max_rounds {spec.budget.max_rounds}, max_seconds "
        f"{_num(spec.budget.max_seconds)}",
        f"looks: n0 {spec.looks.n0}, k {spec.looks.k}, max_looks "
        f"{spec.looks.max_looks}",
        f"min_effect: {_num(spec.min_effect)}",
        f"alpha: {_num(spec.alpha)}",
        f"timeout_seconds: {_num(spec.timeout_seconds)}",
        f"harness: {_paths(spec.harness)}",
        f"oracle: {_paths(spec.oracle)}",
        f"inputs: {_paths(spec.inputs)}",
        f"config: {_paths(spec.config)}",
        f"allow_runtime_change: {_flag(spec.allow_runtime_change)}",
        f"allow_cross_host: {_flag(spec.allow_cross_host)}",
        f"allow_remote_services: {_flag(spec.allow_remote_services)}",
        f"allow_unchecked_coverage: {_flag(spec.allow_unchecked_coverage)}",
        "reset: "
        + (
            "none"
            if spec.reset is None
            else f"{_argv(spec.reset.command)} in {_code(spec.reset.cwd)}, "
            f"timeout {_num(spec.reset.timeout_seconds)} s"
        ),
    ]
    lines = ["## Settings", "", "Fixed in the spec before any round.", ""]
    lines += _bullets(items)
    for g in spec.guardrails:
        lines += _bullets([f"guardrail `{g.metric}`"])
        lines += _bullets(_guardrail_spec(g), 2)
    return "\n".join(lines)


def _guardrail_spec(g: GuardrailSpec) -> list[str]:
    items = [
        f"applicable: {_flag(g.applicable)}",
        f"direction: {g.direction.replace('_', ' ')}",
        f"unit: {_code(g.unit)}",
        f"reason: {_text(g.reason) if g.reason else 'none given'}",
        "allowance: "
        + (
            "none"
            if g.allowance is None
            else f"{_num(g.allowance)} from `{g.allowance_source}`"
        ),
    ]
    items += [
        f"limit: {_text(limit.label)}, {_num(limit.value)} {_text(g.unit)}"
        for limit in g.limits
    ]
    return items


def _run(record: RunRecord) -> str:
    items = [
        f"run_id: {_code(record.run_id)}",
        f"perfproof_version: {_code(record.perfproof_version)}",
        f"schema_version: {record.schema_version}",
        f"started_at: {_time(record.started_at)}",
        f"finished_at: {_time(record.finished_at)}",
        f"baseline_commit: `{record.baseline_commit}`",
        f"order_seed: {record.order_seed}",
    ]
    return "\n".join(["## Run", "", *_bullets(items)])


def _files(record: RunRecord) -> str:
    lines = ["## Files", "", "Relative to the run directory.", ""]
    samples = (
        "none, no round ran"
        if record.samples_path is None
        else _link(record.samples_path)
    )
    lines += _bullets([f"Samples: {samples}"])
    if not record.profile_paths:
        lines += _bullets(["Profiles: none"])
    lines += _bullets(f"Profile: {_link(p)}" for p in record.profile_paths)
    return "\n".join(lines)
