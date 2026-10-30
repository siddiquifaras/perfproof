"""What the user must answer, what to do next, and whether find is done.

Pure functions of the parts, the saved state and what the probes
produced: nothing here runs a command or reads a file, so every rule in
``docs/find.md`` is tested by handing it values.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from perfproof.find.state import reason_code
from perfproof.schema import (
    Lead,
    NextStep,
    Part,
    ProbeRecord,
    metric_in_calibrated_domain,
)

__all__ = [
    "AGENT_WAIVABLE",
    "DEPTH_BUDGET_SECONDS",
    "Asks",
    "Decision",
    "ProbeView",
    "asks",
    "decide",
    "workloads_asked",
]

DEPTH_BUDGET_SECONDS: Final = {"light": 300, "medium": 900, "deep": 2400}
"""Default ``--budget`` per depth, in seconds."""
AGENT_WAIVABLE: Final = ("define_workloads", "add_size_knob", "reach_lead")
"""The ``NextStep.code`` values ``--waive`` accepts."""
_MECHANICAL: Final = frozenset(
    {
        "no_tests",
        "recorder_unavailable",
        "tests_modified_tree",
        "timeout",
        "failed",
    }
)


@dataclass(frozen=True, slots=True)
class ProbeView:
    """A planned probe and how it stands.

    Attributes
    ----------
    key : str
        The probe's key.
    kind : str
        ``tests``, ``tests_native``, ``bench``, ``workload``,
        ``workload_native`` or ``workload_sizes``.
    part_id : str or None
        Its part.
    requirement : bool
        Whether the depth table requires it.
    record : ProbeRecord or None
        Its current record; ``None`` when it still has to run.
    """

    key: str
    kind: str
    part_id: str | None
    requirement: bool
    record: ProbeRecord | None


@dataclass(frozen=True, slots=True)
class Asks:
    """What only the user can settle.

    Attributes
    ----------
    targets : tuple of (str, str)
        ``(target_id, class)`` of each class to confirm.
    tests : tuple of (str, tuple of str, float)
        ``(part id, argv, time limit)`` of each test command to
        confirm.
    installs : tuple of (str, tuple of str)
        ``(part id, argv)`` of each coverage tool to install.
    """

    targets: tuple[tuple[str, str], ...]
    tests: tuple[tuple[str, tuple[str, ...], float], ...]
    installs: tuple[tuple[str, tuple[str, ...]], ...]

    @property
    def empty(self) -> bool:
        """Whether nothing is left to ask."""
        return not (self.targets or self.tests or self.installs)

    def question(self) -> str | None:
        """Return the one question, or ``None`` when all is settled."""
        if self.empty:
            return None
        pieces: list[str] = []
        if self.targets:
            listed = ", ".join(f"{t} is {c}" for t, c in self.targets)
            pieces.append(f"is the target class right ({listed})")
        pieces.extend(
            f"ok for perfproof to run `{' '.join(argv)}` under a profiler "
            f"for up to {limit:g} s (part {part})"
            for part, argv, limit in self.tests
        )
        pieces.extend(
            f"install the coverage tool with `{' '.join(argv)}` (part {part})"
            for part, argv in self.installs
        )
        return "Please confirm: " + "; ".join(pieces) + "?"

    def command(self) -> tuple[str, ...] | None:
        """Return one command that settles it all, when one does."""
        if self.tests and not (self.targets or self.installs):
            argv = ["perfproof", "find"]
            for part, _, _ in self.tests:
                argv += ["--confirm-tests", part]
            return tuple(argv)
        if len(self.targets) == 1 and not (self.tests or self.installs):
            return ("perfproof", "project", "confirm", self.targets[0][0])
        if len(self.installs) == 1 and not (self.targets or self.tests):
            return self.installs[0][1]
        return None

    def how(self) -> str:
        """Say how each answer is recorded."""
        lines = [f"perfproof project confirm {t}" for t, _ in self.targets]
        lines += [
            f"perfproof find --confirm-tests {p}" for p, _, _ in self.tests
        ]
        lines += [" ".join(argv) for _, argv in self.installs]
        return "; ".join(lines)


def asks(
    parts: Sequence[Part],
    *,
    depth: str,
    unconfirmed_targets: Mapping[str, str],
    confirmed_tests: Mapping[str, tuple[str, ...]],
    no_tests: bool,
    test_timeout: float,
) -> Asks:
    """Gather the user's questions.

    Parameters
    ----------
    parts : sequence of Part
        The project's parts.
    depth : str
        ``light``, ``medium`` or ``deep``; light runs no tests, so it
        asks about none.
    unconfirmed_targets : Mapping
        ``target_id`` to class, for each project-model target that is
        not confirmed.
    confirmed_tests : Mapping
        ``Part.id`` to the argv the user confirmed.
    no_tests : bool
        ``--no-tests``: no test command is asked about.
    test_timeout : float
        Seconds each test probe may take.

    Returns
    -------
    Asks
        Classes to confirm, test commands to confirm, and coverage
        tools to install, the last only for a part whose class can
        reach a KEEP (not ``gpu_kernel``, and ``wall`` inside the
        calibrated domain). A part whose class is unknown is not asked.
    """
    targets: dict[str, str] = {}
    tests: list[tuple[str, tuple[str, ...], float]] = []
    installs: list[tuple[str, tuple[str, ...]]] = []
    for part in parts:
        if part.target_id is not None and part.target_id in (
            unconfirmed_targets
        ):
            targets[part.target_id] = unconfirmed_targets[part.target_id]
        command = part.test_command.value
        if (
            depth != "light"
            and not no_tests
            and command is not None
            and confirmed_tests.get(part.id) != tuple(command.argv)
        ):
            tests.append((part.id, tuple(command.argv), test_timeout))
        target_class = part.target_class.value
        install = part.tooling.coverage_install
        if (
            part.tooling.coverage == "missing"
            and install is not None
            and target_class is not None
            and target_class != "gpu_kernel"
            and metric_in_calibrated_domain(target_class, "wall")
        ):
            installs.append((part.id, tuple(install)))
    return Asks(tuple(targets.items()), tuple(tests), tuple(installs))


def workloads_asked(depth: str, entry_points: int) -> tuple[int, int]:
    """Return how many user workloads, and entry points they must cover.

    Parameters
    ----------
    depth : str
        ``light``, ``medium`` or ``deep``.
    entry_points : int
        Entry points the parts have.

    Returns
    -------
    tuple of int
        ``(workloads, covered entry points)``: light 1 and 0; medium 3
        and ``min(2, entry points)``; deep 1 and every entry point.
    """
    if depth == "light":
        return 1, 0
    if depth == "medium":
        return 3, min(2, entry_points)
    return 1, entry_points


def mechanical_waiver(view: ProbeView) -> str | None:
    """Return the code that waives a probe, or ``None``.

    A probe that ran and did not finish, was skipped for lack of tests,
    or changed the tree is waived by the reason its record carries.
    """
    record = view.record
    if record is None or record.status in ("done", "skipped_unconfirmed"):
        return None
    code = reason_code(record)
    return code if code in _MECHANICAL else "failed"


@dataclass(frozen=True, slots=True)
class Decision:
    """The three outputs that depend on the rest.

    Attributes
    ----------
    question : str or None
        The one question.
    next_steps : tuple of NextStep
        In priority order.
    done : bool
        Whether the depth's requirements are met or waived.
    """

    question: str | None
    next_steps: tuple[NextStep, ...]
    done: bool


def decide(
    *,
    depth: str,
    asked: Asks,
    views: Sequence[ProbeView],
    workloads: Sequence[str],
    knobs: Sequence[str],
    uncovered_entries: Sequence[str],
    entry_points: int,
    covered_entries: int,
    distinct_tops: int,
    leads: Sequence[Lead],
    waivers: Mapping[str, str],
) -> Decision:
    """Compute ``question``, ``next_steps`` and ``done``.

    Parameters
    ----------
    depth : str
        ``light``, ``medium`` or ``deep``.
    asked : Asks
        What `asks` returned.
    views : sequence of ProbeView
        Every planned probe.
    workloads : sequence of str
        Names of the user's workload specs.
    knobs : sequence of str
        Workload names with a stored size knob.
    uncovered_entries : sequence of str
        Entry points no current user profile covers.
    entry_points : int
        Entry points the parts have.
    covered_entries : int
        How many of them a current user profile covers.
    distinct_tops : int
        Distinct functions at the top of the workloads' rankings; with
        fewer than 2 entry points, medium asks for at least 2.
    leads : sequence of Lead
        The leads, function leads first.
    waivers : Mapping
        The agent's ``--waive`` reasons by code.

    Returns
    -------
    Decision
        ``done`` is never true while a probe is pending or a question
        is open. ``reach_lead`` is advice: ``done`` does not wait for
        it.
    """
    question = asked.question()
    pending = [v for v in views if v.record is None]
    steps: list[NextStep] = []
    if question is not None:
        steps.append(
            NextStep.model_validate(
                {
                    "code": "install_or_confirm",
                    "message": f"{question} Record the answers with: "
                    f"{asked.how()}.",
                    "command": asked.command(),
                }
            )
        )
    need, need_covered = workloads_asked(depth, entry_points)
    thin = depth == "medium" and entry_points < 2 and distinct_tops < 2
    short = len(workloads) < need or covered_entries < need_covered or thin
    if short and "define_workloads" not in waivers:
        parts_text = (
            f" Entry points with no workload: {', '.join(uncovered_entries)}."
            if uncovered_entries
            else ""
        )
        lead_text = _lead_text(leads)
        steps.append(
            NextStep.model_validate(
                {
                    "code": "define_workloads",
                    "message": (
                        f"{len(workloads)} user workload(s) defined; "
                        f"{depth} asks for {need} covering "
                        f"{need_covered or 'one or more'} entry point(s)"
                        f"{_tops_text(depth, entry_points)}, "
                        f"{covered_entries} covered.{parts_text}{lead_text}"
                    ),
                    "command": None,
                }
            )
        )
    knobs_missing = _knobs_missing(depth, workloads, knobs)
    if knobs_missing and "add_size_knob" not in waivers:
        steps.append(
            NextStep.model_validate(
                {
                    "code": "add_size_knob",
                    "message": (
                        f"No size knob stored for {', '.join(knobs_missing)}."
                        " Run perfproof find --size-arg "
                        "WORKLOAD:FLAG=BASE (or --size-env "
                        "WORKLOAD:NAME=BASE) with the input that sets its "
                        "size."
                    ),
                    "command": None,
                }
            )
        )
    top = next((lead for lead in leads if lead.kind == "function"), None)
    tests_ran = any(
        v.kind.startswith("tests")
        and v.record is not None
        and v.record.status == "done"
        for v in views
    )
    if (
        top is not None
        and depth != "light"
        and tests_ran
        and "reach_lead" not in waivers
    ):
        how = (
            f"the test {top.via_test} reaches it; read its inputs for the "
            "shape"
            if top.via_test
            else f"it was reached by {', '.join(top.seen_in)}"
        )
        steps.append(
            NextStep.model_validate(
                {
                    "code": "reach_lead",
                    "message": (
                        f"No user workload reaches {top.name}"
                        f"{'' if top.file is None else f' ({top.file})'}: "
                        f"{how}. Write a workload that does."
                    ),
                    "command": None,
                }
            )
        )
    if pending:
        steps.append(
            NextStep.model_validate(
                {
                    "code": "continue_find",
                    "message": (
                        f"The budget ran out with {len(pending)} probe(s) "
                        "pending. Call find again."
                    ),
                    "command": ("perfproof", "find", "--depth", depth),
                }
            )
        )
    probes_met = all(
        v.record is not None
        and (v.record.status == "done" or mechanical_waiver(v) is not None)
        for v in views
        if v.requirement
    )
    done = (
        question is None
        and not pending
        and probes_met
        and (not short or "define_workloads" in waivers)
        and (not knobs_missing or "add_size_knob" in waivers)
    )
    return Decision(question, tuple(steps), done)


def _knobs_missing(
    depth: str, workloads: Sequence[str], knobs: Sequence[str]
) -> list[str]:
    if depth == "light":
        return []
    have = set(knobs)
    if depth == "medium":
        return (
            []
            if have & set(workloads) or not workloads
            else list(workloads[:1])
        )
    return [w for w in workloads if w not in have]


def _lead_text(leads: Sequence[Lead]) -> str:
    names = [lead.name for lead in leads if lead.kind == "function"][:3]
    if not names:
        return ""
    return f" Leads that would justify one: {', '.join(names)}."


def _tops_text(depth: str, entry_points: int) -> str:
    if depth == "medium" and entry_points < 2:
        return (
            " and at least 2 distinct functions at the top of their rankings"
        )
    return ""
