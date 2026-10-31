"""One ``perfproof find`` call: understand, tooling, probes, map, plan.

`run_find` advances the plan as far as the budget allows and returns
where it stands; calling it again resumes from the saved state.
"""

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from perfproof import __version__
from perfproof.bench.snapshot import SnapshotError, exclude_state_dir
from perfproof.commands import CommandError
from perfproof.commands._project import as_command_error
from perfproof.find import gittree, mapping, plan
from perfproof.find.probes import PlannedProbe, native_leaf, run_probe
from perfproof.find.profilers import (
    NATIVE_LANGUAGES,
    native_template,
    profiler_template,
)
from perfproof.find.specs import (
    BENCH_TIMEOUT_SECONDS,
    NO_BYTECODE,
    UserSpec,
    bench_spec_name,
    build_spec,
    tests_spec_name,
    user_specs,
)
from perfproof.find.state import (
    current_record,
    evolve,
    fresh_state,
    hash_parts,
    load_state,
    load_test_commands,
    save_state,
    save_test_commands,
    with_probe,
)
from perfproof.project.errors import ProjectError
from perfproof.project.guard import current_host_id
from perfproof.project.service import check_project, init_project
from perfproof.project.store import load_model
from perfproof.schema import (
    BenchmarkSpec,
    ExistingBenchmark,
    FindResult,
    FindState,
    Part,
    ProbeRecord,
    ProjectModel,
    SizeKnobSpec,
)

__all__ = ["FindRequest", "FindRun", "default_detect", "run_find"]

_NO_SPEC: Final = "0" * 64
"""Spec hash of a record for a probe that never got a spec."""


@dataclass(frozen=True, slots=True)
class FindRequest:
    """The options of one call.

    Attributes
    ----------
    root : pathlib.Path
        Repository root.
    depth : str
        ``light``, ``medium`` or ``deep``.
    budget_seconds : float or None
        ``--budget``; the depth's default when ``None``.
    size_knobs : Mapping of str to SizeKnobSpec
        Knobs declared in this call, by workload name.
    no_tests : bool
        ``--no-tests``.
    test_timeout : float
        ``--test-timeout``, seconds per test run.
    confirm_tests : tuple of str
        ``--confirm-tests`` part ids.
    waive : Mapping of str to str
        ``--waive`` reasons by step code.
    test_commands : Mapping of str to tuple of str
        ``--test-command`` argv by part id, given in this call.
    """

    root: Path
    depth: str
    budget_seconds: float | None
    size_knobs: Mapping[str, SizeKnobSpec]
    no_tests: bool
    test_timeout: float
    confirm_tests: tuple[str, ...]
    waive: Mapping[str, str]
    test_commands: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: dict[str, tuple[str, ...]]()
    )


@dataclass(frozen=True, slots=True)
class FindRun:
    """What `run_find` returns.

    Attributes
    ----------
    result : perfproof.schema.FindResult
        The result.
    profilers : Mapping of str to tuple of str
        Each part's ``--profiler-command`` template; empty for the
        default profiler, absent when none is known.
    """

    result: FindResult
    profilers: Mapping[str, tuple[str, ...]]


def run_find(
    request: FindRequest,
    *,
    detect: Callable[[Path, ProjectModel], Sequence[Part]] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> FindRun:
    """Run one ``find`` call.

    Parameters
    ----------
    request : FindRequest
        The options.
    detect : callable, optional
        ``detect_parts``; replaced in tests.
    clock : callable, optional
        Monotonic seconds, for tests.

    Returns
    -------
    FindRun
        The result and the profiler templates.

    Raises
    ------
    CommandError
        ``unknown_part``, ``no_test_command``, ``not_found`` (a size
        knob for an undefined workload), ``invalid_argument``,
        ``tree_check_failed``, the state errors, or the project
        model's.
    """
    root = request.root
    started = datetime.now(UTC)
    began = clock()
    model = _model(root)
    finder = default_detect() if detect is None else detect
    parts = _with_test_commands(
        root, tuple(finder(root, model)), request.test_commands
    )
    users = user_specs(root)
    state = _record_inputs(request, parts, users, load_state(root))
    save_state(root, state)
    try:
        tree = gittree.tree_id(root)
    except gittree.GitError as exc:
        raise CommandError("tree_check_failed", str(exc)) from exc
    limit = request.budget_seconds or float(
        plan.DEPTH_BUDGET_SECONDS[request.depth]
    )
    runner = _Runner(request, parts, users, state, tree, limit, clock)
    runner.run_all()
    mapped = mapping.build(
        root,
        parts,
        user_names=runner.names("workload"),
        find_names=runner.names("find"),
        test_runs=runner.test_runs(),
    )
    entries = _entries(parts, mapped)
    asked = plan.asks(
        parts,
        depth=request.depth,
        unconfirmed_targets={
            t.id: t.target_class
            for t in model.targets
            if t.status != "confirmed"
        },
        confirmed_tests={
            k: tuple(v) for k, v in runner.state.confirmed_tests.items()
        },
        no_tests=request.no_tests,
        test_timeout=request.test_timeout,
    )
    decision = plan.decide(
        depth=request.depth,
        asked=asked,
        views=runner.views,
        workloads=sorted(users),
        knobs=sorted(runner.state.size_knobs),
        uncovered_entries=entries.uncovered,
        entry_points=entries.total,
        covered_entries=entries.covered,
        distinct_tops=(
            0
            if mapped.user is None
            else mapping.distinct_tops(mapped.user, runner.names("workload"))
        ),
        leads=mapped.leads,
        waivers={str(k): v for k, v in runner.state.waivers.items()},
    )
    spent = clock() - began
    skipped = tuple(
        v.record
        for v in runner.views
        if v.record and v.record.status != "done"
    )
    state = evolve(
        runner.state,
        calls=[
            *runner.state.calls,
            {"started_at": started.isoformat(), "spent_seconds": spent},
        ],
    )
    save_state(root, state)
    result = FindResult.model_validate(
        {
            "done": decision.done,
            "depth": request.depth,
            "parts": [p.model_dump(mode="json") for p in parts],
            "question": decision.question,
            "next_steps": [
                s.model_dump(mode="json") for s in decision.next_steps
            ],
            "map": [m.model_dump(mode="json") for m in mapped.map],
            "leads": [lead.model_dump(mode="json") for lead in mapped.leads],
            "not_reached": list(mapped.not_reached),
            "skipped": [s.model_dump(mode="json") for s in skipped],
            "budget": {
                "spent_seconds": runner.spent,
                "limit_seconds": limit,
                "exhausted": any(v.record is None for v in runner.views),
            },
        }
    )
    profilers = {
        p.id: t
        for p in parts
        if (t := profiler_template(p.language, root)) is not None
    }
    return FindRun(result, profilers)


def default_detect() -> Callable[[Path, ProjectModel], Sequence[Part]]:
    """Return ``perfproof.project.parts.detect_parts``."""
    from perfproof.project.parts import detect_parts

    return detect_parts


def _model(root: Path) -> ProjectModel:
    """Load the project model, running ``init`` or its stale check."""
    try:
        exclude_state_dir(root)
        if load_model(root) is None:
            init_project(root, now=datetime.now(UTC), version=__version__)
        else:
            check_project(
                root,
                now=datetime.now(UTC),
                version=__version__,
                host_id=current_host_id,
            )
        model = load_model(root)
    except SnapshotError as exc:
        raise CommandError("state_write_failed", str(exc)) from exc
    except ProjectError as exc:
        raise as_command_error(exc) from exc
    if model is None:
        msg = "init wrote no project model"
        raise CommandError("internal_fault", msg)
    return model


def _record_inputs(
    request: FindRequest,
    parts: Sequence[Part],
    users: Mapping[str, UserSpec],
    saved: FindState | None,
) -> FindState:
    """Fold this call's confirmations, knobs and waivers in."""
    state = saved or fresh_state(hash_parts(parts))
    by_id = {p.id: p for p in parts}
    confirmed = dict(state.confirmed_tests)
    for part_id in request.confirm_tests:
        part = by_id.get(part_id)
        if part is None:
            msg = f"no part {part_id!r}; the parts are {', '.join(by_id)}"
            raise CommandError("unknown_part", msg, {"part": part_id})
        command = part.test_command.value
        if command is None:
            msg = f"part {part_id} has no test command to confirm"
            raise CommandError("no_test_command", msg, {"part": part_id})
        confirmed[part_id] = tuple(command.argv)
    knobs = dict(state.size_knobs)
    for name, knob in request.size_knobs.items():
        if name not in users:
            msg = (
                f"{name!r} is not a defined workload; define it with "
                "perfproof bench define first"
            )
            raise CommandError("not_found", msg, {"name": name})
        knobs[name] = knob
    waivers = {**state.waivers, **request.waive}
    return evolve(
        state,
        parts_hash=hash_parts(parts),
        confirmed_tests=confirmed,
        size_knobs=knobs,
        waivers=waivers,
    )


@dataclass(frozen=True, slots=True)
class _Entries:
    total: int
    covered: int
    uncovered: tuple[str, ...]


def _entries(parts: Sequence[Part], mapped: mapping.Mapped) -> _Entries:
    """Count the entry points a current user profile covers."""
    rows = () if mapped.user is None else mapped.user.functions
    names = {str(r.get("function")) for r in rows}
    short = {n.rsplit(".", 1)[-1].rsplit("::", 1)[-1] for n in names}
    files = {str(r.get("file")) for r in rows if r.get("file")}
    total = 0
    uncovered: list[str] = []
    for part in parts:
        for entry in part.entry_points:
            total += 1
            file = entry.file.removeprefix("./")
            if not (
                entry.name in names or entry.name in short or file in files
            ):
                uncovered.append(f"{part.id}:{entry.name}")
    return _Entries(total, total - len(uncovered), tuple(uncovered))


class _Runner:
    """Plans and runs the probes of one call, in the spec's order."""

    def __init__(
        self,
        request: FindRequest,
        parts: Sequence[Part],
        users: Mapping[str, UserSpec],
        state: FindState,
        tree: str,
        limit: float,
        clock: Callable[[], float],
    ) -> None:
        self.request = request
        self.parts = parts
        self.users = users
        self.state = state
        self.tree = tree
        self.limit = limit
        self.clock = clock
        self.spent = 0.0
        self.views: list[plan.ProbeView] = []
        self.exempt = _exemptions(parts)

    def names(self, group: str) -> list[str]:
        """Spec names with a done profile, user or ``find-*``."""
        found: list[str] = []
        for view in self.views:
            if view.record is None or view.record.status != "done":
                continue
            is_user = view.kind.startswith("workload")
            if (group == "workload") == is_user:
                name = view.key.split("@", 1)[0]
                if name not in found:
                    found.append(name)
        return found

    def test_runs(self) -> list[tuple[str, str]]:
        """``(name, run id)`` of every done tests probe."""
        return [
            (v.key.split("@", 1)[0], run_id)
            for v in self.views
            if v.kind.startswith("tests")
            and v.record is not None
            and v.record.status == "done"
            for run_id in v.record.run_ids
        ]

    def run_all(self) -> None:
        """Run every probe the depth asks for."""
        if self.request.depth != "light":
            for part in self.parts:
                self._tests(part)
            for part in self.parts:
                for benchmark in part.benchmarks:
                    self._bench(part, benchmark)
        self._workloads()

    def _part_for(self, cwd: str) -> Part | None:
        best: tuple[int, Part] | None = None
        for part in self.parts:
            base = part.root.strip("/")
            if base in ("", "."):
                size = 0
            elif cwd == base or cwd.startswith(f"{base}/"):
                size = len(base)
            else:
                continue
            if best is None or size > best[0]:
                best = (size, part)
        return None if best is None else best[1]

    def _harness(self, part: Part) -> str | None:
        for manifest in part.manifests:
            for candidate in (manifest, f"{part.root.strip('/')}/{manifest}"):
                if (self.request.root / candidate).is_file():
                    return candidate
        return None

    def _blocked(
        self, part: Part, template: tuple[str, ...] | None
    ) -> str | None:
        status = part.tooling.profiler
        if not status.available:
            return f"failed: profiler unavailable: {status.reason}"
        if template is None:
            return f"failed: no profiler is known for {part.language}"
        return None

    def _own(self, part: Part) -> tuple[str, ...]:
        if part.language in ("python", "javascript", "typescript"):
            return ()
        return tuple(n.own_module for n in part.native)

    def _skip(
        self,
        key: str,
        spec: BenchmarkSpec | None,
        status: str,
        reason: str,
        kind: str,
        part: Part,
    ) -> None:
        record = ProbeRecord.model_validate(
            {
                "key": key,
                "requirement": True,
                "spec_hash": _NO_SPEC if spec is None else spec.spec_hash,
                "tree_id": self.tree,
                "status": status,
                "run_ids": [],
                "reason": reason,
                "log_tail": None,
            }
        )
        self.state = with_probe(self.state, record)
        save_state(self.request.root, self.state)
        self.views.append(plan.ProbeView(key, kind, part.id, True, record))

    def _tests(self, part: Part) -> None:
        name = tests_spec_name(part.id)
        command = part.test_command.value
        if self.request.no_tests or command is None:
            why = (
                "no_tests: --no-tests"
                if self.request.no_tests
                else f"no_tests: {part.test_command.reason}"
            )
            self._skip(name, None, "skipped_no_tests", why, "tests", part)
            return
        if self.state.confirmed_tests.get(part.id) != tuple(command.argv):
            self._skip(
                name,
                None,
                "skipped_unconfirmed",
                "the test command is not confirmed",
                "tests",
                part,
            )
            return
        harness = self._harness(part)
        if harness is None:
            self._skip(
                name,
                None,
                "failed",
                f"failed: the manifests {list(part.manifests)} are not files",
                "tests",
                part,
            )
            return
        template = profiler_template(part.language, self.request.root)
        spec = build_spec(
            name=name,
            command=tuple(command.argv),
            cwd=command.cwd,
            harness=harness,
            target_class=part.target_class.value,
            timeout_seconds=self.request.test_timeout,
            env=NO_BYTECODE if part.language == "python" else None,
        )
        record = self._attempt(
            PlannedProbe(
                key=name,
                name=name,
                spec=spec,
                define=True,
                part_id=part.id,
                kind="tests",
                requirement=True,
                own_timeout=self.request.test_timeout,
                template=template or (),
                own_modules=self._own(part),
                blocked=self._blocked(part, template),
            )
        )
        if not self._wants_native(part, name, record, spec):
            return
        recorder = part.tooling.native_recorder
        self._attempt(
            PlannedProbe(
                key=f"{name}@native",
                name=name,
                spec=spec,
                define=False,
                part_id=part.id,
                kind="tests_native",
                requirement=True,
                own_timeout=self.request.test_timeout,
                template=native_template(),
                own_modules=tuple(n.own_module for n in part.native),
                blocked=None
                if recorder.available
                else f"recorder_unavailable: {recorder.reason}",
            )
        )

    def _wants_native(
        self,
        part: Part | None,
        name: str,
        record: ProbeRecord | None,
        spec: BenchmarkSpec,
    ) -> bool:
        """Apply the depth table's native recorder row to one part.

        The recorder runs for a part that has native code. At medium it
        also runs for a Python part whose first profile of this spec
        has a builtin or extension leaf at ``min_effect``; that rule
        is never applied to a JavaScript, JVM or Go part.
        """
        if (
            part is None
            or self.request.depth == "light"
            or part.language in NATIVE_LANGUAGES
        ):
            return False
        if part.native:
            return True
        if (
            self.request.depth != "medium"
            or part.language != "python"
            or record is None
            or record.status != "done"
            or not record.run_ids
        ):
            return False
        return native_leaf(
            self.request.root, name, record.run_ids[0], spec.min_effect
        )

    def _bench(self, part: Part, item: ExistingBenchmark) -> None:
        name = bench_spec_name(part.id, item.name)
        harness = self._harness(part)
        template = profiler_template(part.language, self.request.root)
        if harness is None:
            self._skip(
                name,
                None,
                "failed",
                "failed: the part's manifests are not files",
                "bench",
                part,
            )
            return
        spec = build_spec(
            name=name,
            command=tuple(item.argv),
            cwd=item.cwd,
            harness=harness,
            target_class=part.target_class.value,
            timeout_seconds=BENCH_TIMEOUT_SECONDS,
            env=NO_BYTECODE if part.language == "python" else None,
        )
        self._attempt(
            PlannedProbe(
                key=name,
                name=name,
                spec=spec,
                define=True,
                part_id=part.id,
                kind="bench",
                requirement=True,
                own_timeout=BENCH_TIMEOUT_SECONDS,
                template=template or (),
                own_modules=self._own(part),
                blocked=self._blocked(part, template),
            )
        )

    def _workloads(self) -> None:
        depth = self.request.depth
        asked = plan.workloads_asked(depth, 0)[0]
        for index, name in enumerate(sorted(self.users)):
            spec = self.users[name].spec
            part = self._part_for(spec.cwd) or (
                self.parts[0] if self.parts else None
            )
            language = "python" if part is None else part.language
            template = profiler_template(language, self.request.root)
            knob = self.state.size_knobs.get(name)
            requirement = index < asked or depth == "deep"
            key = f"{name}@sizes" if knob else name
            blocked = None if part is None else self._blocked(part, template)
            if part is None and template is None:
                blocked = "failed: no profiler is known"
            record = self._attempt(
                PlannedProbe(
                    key=key,
                    name=name,
                    spec=spec,
                    define=False,
                    part_id=None,
                    kind="workload_sizes" if knob else "workload",
                    requirement=requirement,
                    own_timeout=spec.timeout_seconds,
                    template=template or (),
                    own_modules=() if part is None else self._own(part),
                    knob=knob,
                    blocked=blocked,
                )
            )
            if part is None or not self._wants_native(
                part, name, record, spec
            ):
                continue
            recorder = part.tooling.native_recorder
            self._attempt(
                PlannedProbe(
                    key=f"{name}@native",
                    name=name,
                    spec=spec,
                    define=False,
                    part_id=None,
                    kind="workload_native",
                    requirement=requirement,
                    own_timeout=spec.timeout_seconds,
                    template=native_template(),
                    own_modules=tuple(n.own_module for n in part.native),
                    blocked=None
                    if recorder.available
                    else f"recorder_unavailable: {recorder.reason}",
                )
            )

    def _attempt(self, planned: PlannedProbe) -> ProbeRecord | None:
        """Use the current record or run the probe; note the view."""
        spec_hash = planned.spec.spec_hash
        record = current_record(self.state, planned.key, spec_hash, self.tree)
        if record is None:
            old = next(
                (r for r in self.state.probes if r.key == planned.key), None
            )
            outcome = run_probe(
                self.request.root,
                replace(planned, stale=old, exempt=self.exempt),
                tree=self.tree,
                remaining_seconds=self.limit - self.spent,
                clock=self.clock,
            )
            self.spent += outcome.spent_seconds
            record = outcome.record
            if record is not None:
                self.state = with_probe(self.state, record)
                save_state(self.request.root, self.state)
        self.views.append(
            plan.ProbeView(
                planned.key,
                planned.kind,
                planned.part_id,
                planned.requirement,
                record,
            )
        )
        return record


def _exemptions(parts: Sequence[Part]) -> gittree.Exemptions:
    """Name the part roots where more tool output is expected."""

    def root(part: Part) -> str:
        base = part.root.strip("/")
        return "" if base in ("", ".") else base

    def has(part: Part, names: tuple[str, ...]) -> bool:
        return any(m.rsplit("/", 1)[-1] in names for m in part.manifests)

    return gittree.Exemptions(
        coverage=frozenset(root(p) for p in parts),
        cargo=frozenset(root(p) for p in parts if has(p, ("Cargo.toml",))),
        gradle=frozenset(
            root(p)
            for p in parts
            if has(
                p,
                (
                    "build.gradle",
                    "build.gradle.kts",
                    "settings.gradle",
                    "settings.gradle.kts",
                ),
            )
        ),
    )


def _with_test_commands(
    root: Path,
    parts: tuple[Part, ...],
    given: Mapping[str, tuple[str, ...]],
) -> tuple[Part, ...]:
    """Use the user's test command for a part, where they gave one.

    The commands persist in ``.perfproof/find/test-commands.json``; a
    new one for a part replaces the old. It is only the command: running
    it still needs ``--confirm-tests`` for exactly that argv.
    """
    known = {p.id for p in parts}
    for part_id in given:
        if part_id not in known:
            listed = ", ".join(sorted(known))
            msg = f"no part {part_id!r}; the parts are {listed}"
            raise CommandError("unknown_part", msg, {"part": part_id})
    commands = load_test_commands(root)
    if given:
        commands = {**commands, **given}
        save_test_commands(root, commands)
    out: list[Part] = []
    for part in parts:
        argv = commands.get(part.id)
        if argv is None:
            out.append(part)
            continue
        data = part.model_dump(mode="json")
        data["test_command"] = {
            "value": {
                "argv": list(argv),
                "cwd": part.root,
                "evidence": "--test-command",
            }
        }
        out.append(Part.model_validate(data))
    return tuple(out)
