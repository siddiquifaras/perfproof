import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from project_helpers import (
    NOW,
    PYTHON_FILES,
    VERSION,
    MakeRepo,
    confirmed,
)

from perfproof.project import NotConfirmedError, require_confirmed, store
from perfproof.project.entries import find, put, record, with_fields
from perfproof.project.lifecycle import GoalUpdate, confirm, set_goal
from perfproof.project.service import check_project
from perfproof.project.staleness import (
    Baseline,
    CheckResult,
    ProbeResult,
    find_stale,
    load_baselines,
    mark_findings,
    run_probe,
    shape_of,
)
from perfproof.schema import (
    EntryKind,
    GoalEntry,
    ProjectHistoryRecord,
    ProjectModel,
    WorkloadEntry,
)

LATER = NOW + timedelta(hours=1)
HOST_A = "a" * 64
HOST_B = "b" * 64


def model_of(repo: Path) -> ProjectModel:
    model = store.load_model(repo)
    assert model is not None
    return model


def workload(model: ProjectModel, wid: str = "bench") -> WorkloadEntry:
    entry = find(model, wid)
    assert isinstance(entry, WorkloadEntry)
    return entry


def goal(model: ProjectModel) -> GoalEntry:
    entry = find(model, "goal-bench")
    assert isinstance(entry, GoalEntry)
    return entry


def run_check(
    repo: Path,
    host: str | None = HOST_A,
    *,
    version: str = VERSION,
    probe: bool = False,
    revisions: dict[EntryKind, str] | None = None,
) -> CheckResult:
    return check_project(
        repo,
        now=LATER,
        version=version,
        host_id=lambda: host,
        probe=probe,
        probe_timeout=60,
        revisions=revisions,
    )


def freeze(repo: Path, spec_hash: str) -> None:
    """Give the confirmed workload a frozen spec, as ``bench`` will."""

    def change(
        m: ProjectModel,
    ) -> tuple[ProjectModel, list[ProjectHistoryRecord]]:
        entry = with_fields(workload(m), spec_hash=spec_hash)
        return put(m, entry), [record("revalidated", entry, NOW, "setup")]

    store.mutate(repo, change)


@pytest.fixture
def python_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``python`` the interpreter running the tests."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").symlink_to(sys.executable)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


# Each invalidation trigger fires, and records which one


def test_file_changed(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    model = confirmed(repo)
    (repo / "bench.py").write_text("# edited\n")
    findings, unknown = find_stale(repo, model, host_id=None)
    ids = {f.entry_id: f for f in findings}
    assert ids["bench"].trigger == "file_changed"
    assert "bench.py changed" in ids["bench"].detail
    assert "goal-bench" not in ids
    assert unknown == []
    stale, records = mark_findings(model, findings, LATER)
    entry = workload(stale)
    assert entry.status == "stale"
    assert entry.previous_status == "confirmed"
    assert entry.confirmation is not None
    assert entry.invalidation is not None
    assert entry.invalidation.trigger == "file_changed"
    assert [r.event for r in records] == ["marked_stale"]


def test_a_deleted_file_counts_as_changed(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    model = confirmed(repo)
    (repo / "bench.py").unlink()
    (finding,) = [
        f
        for f in find_stale(repo, model, host_id=None)[0]
        if f.entry_id == "bench"
    ]
    assert "bench.py missing" in finding.detail


def test_lockfile_changed(make_repo: MakeRepo) -> None:
    repo = make_repo({**PYTHON_FILES, "uv.lock": "version = 1\n"})
    model = confirmed(repo)
    (repo / "uv.lock").write_text("version = 1\n# new pins\n")
    findings, _ = find_stale(repo, model, host_id=None)
    (finding,) = [f for f in findings if f.entry_id == "bench"]
    assert finding.trigger == "lockfile_changed"
    assert "uv.lock changed" in finding.detail


def test_a_file_change_wins_over_a_lockfile_change_and_names_both(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo({**PYTHON_FILES, "uv.lock": "version = 1\n"})
    model = confirmed(repo)
    (repo / "uv.lock").write_text("changed\n")
    (repo / "bench.py").write_text("changed\n")
    (finding,) = [
        f
        for f in find_stale(repo, model, host_id=None)[0]
        if f.entry_id == "bench"
    ]
    assert finding.trigger == "file_changed"
    assert "lockfile_changed" in finding.detail


def test_machine_differs_for_a_hardware_bound_goal(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    model = confirmed(repo)
    bound = with_fields(goal(model), host_id=HOST_A)
    model = put(model, bound)
    assert find_stale(repo, model, host_id=HOST_A) == ([], [])
    findings, unknown = find_stale(repo, model, host_id=HOST_B)
    assert [(f.entry_id, f.trigger) for f in findings] == [
        ("goal-bench", "machine_differs")
    ]
    assert unknown == []


def test_an_unidentifiable_machine_is_unknown_not_a_pass_or_a_change(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    model = put(
        confirmed(repo), with_fields(goal(confirmed(repo)), host_id=HOST_A)
    )
    findings, unknown = find_stale(repo, model, host_id=None)
    assert findings == []
    assert len(unknown) == 1
    assert unknown[0][0] == "goal-bench"
    assert "could not be identified" in unknown[0][1]


def test_perfproof_upgraded(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    model = confirmed(repo)
    assert find_stale(repo, model, host_id=None)[0] == []
    revisions: dict[EntryKind, str] = {
        "target": "0.1.0",
        "workload": "0.2.0",
        "how_to_run": "0.1.0",
        "goal": "9.9.9",
        "constraint": "0.1.0",
    }
    findings, _ = find_stale(repo, model, host_id=None, revisions=revisions)
    assert [(f.entry_id, f.trigger) for f in findings] == [
        ("bench", "perfproof_upgraded")
    ]


def test_command_failed_and_output_shape_changed(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    model = confirmed(repo)
    bench = workload(model)
    ok = ProbeResult(0, shape_of("PERFPROOF 1.5\n"))
    base = {"bench": Baseline(shape_of("PERFPROOF 9.1\n"), 1)}
    assert find_stale(
        repo, model, host_id=None, probes={"bench": ok}, baselines=base
    ) == ([], [])
    failed = ProbeResult(3, shape_of(""))
    (f1,) = find_stale(
        repo, model, host_id=None, probes={"bench": failed}, baselines=base
    )[0]
    assert (f1.entry_id, f1.trigger) == ("bench", "command_failed")
    changed = ProbeResult(0, shape_of("a different line\n"))
    (f2,) = find_stale(
        repo, model, host_id=None, probes={"bench": changed}, baselines=base
    )[0]
    assert (f2.entry_id, f2.trigger) == ("bench", "output_shape_changed")
    assert bench.status == "confirmed"


def test_a_probe_runs_the_command_for_real(
    make_repo: MakeRepo, python_on_path: None
) -> None:
    repo = make_repo(
        {
            **PYTHON_FILES,
            "bench.py": (
                "import sys\nif __name__ == '__main__':\n"
                "    print('PERFPROOF 0.5')\n    sys.exit(0)\n"
            ),
        }
    )
    entry = workload(confirmed(repo))
    result = run_probe(repo, entry, timeout=60)
    assert result.ok
    assert result.shape == shape_of("PERFPROOF 0.99\n")
    (repo / "bench.py").write_text("raise SystemExit(4)\n")
    failing = run_probe(repo, entry, timeout=60)
    assert failing.exit_code == 4
    assert not failing.ok


def test_shape_ignores_numbers_and_repeats() -> None:
    assert shape_of("t 1.5 s\nt 2.5 s\n") == shape_of("t 9.0 s\n")
    assert shape_of("a\n\nb\n") != shape_of("b\na\n")


# Revalidation: nothing changed in what was derived


def test_revalidation_that_finds_no_change_restores_the_status(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    before = confirmed(repo)
    path = repo / "pyproject.toml"
    path.write_text(path.read_text() + "# a comment\n")
    result = run_check(repo)
    assert [f.entry_id for f in result.marked_stale] == ["target-python_cpu"]
    assert result.revalidated == ("target-python_cpu",)
    assert result.reproposed == ()
    after = model_of(repo)
    old, entry = before.targets[0], after.targets[0]
    assert entry.status == "confirmed"
    assert entry.version == old.version
    assert entry.confirmation == old.confirmation
    assert entry.invalidation is None
    hashes = {f.path: f.sha256 for f in entry.provenance.files}
    assert hashes != {f.path: f.sha256 for f in old.provenance.files}
    history, _ = store.read_history(repo)
    tail = [(r.event, r.entry.id) for r in history][-2:]
    assert tail == [
        ("marked_stale", "target-python_cpu"),
        ("revalidated", "target-python_cpu"),
    ]
    assert workload(after).status == "confirmed"
    assert goal(after).status == "confirmed"
    assert require_confirmed(repo, "bench")


def test_a_proposed_entry_that_goes_stale_returns_to_proposed(
    make_repo: MakeRepo,
) -> None:
    from project_helpers import proposed

    repo = make_repo(PYTHON_FILES)
    proposed(repo)
    path = repo / "pyproject.toml"
    path.write_text(path.read_text() + "# a comment\n")
    result = run_check(repo)
    assert result.revalidated == ("target-python_cpu",)
    assert model_of(repo).targets[0].status == "proposed"


def test_a_workloads_own_file_change_is_reproposed_even_if_the_command_is_not(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    before = confirmed(repo)
    (repo / "bench.py").write_text(PYTHON_FILES["bench.py"] + "# note\n")
    result = run_check(repo)
    assert result.reproposed == ("bench",)
    assert result.revalidated == ()
    after = model_of(repo)
    entry = workload(after)
    assert entry.command == workload(before).command
    assert entry.status == "proposed"
    assert entry.version == workload(before).version + 1
    assert entry.confirmation is None
    history, _ = store.read_history(repo)
    note = [r.note for r in history if r.event == "reproposed"][-1]
    assert "own files changed" in note
    with pytest.raises(NotConfirmedError):
        require_confirmed(repo, "bench")


def test_a_lockfile_change_also_needs_the_workload_confirmed_again(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo({**PYTHON_FILES, "uv.lock": "version = 1\n"})
    confirmed(repo)
    (repo / "uv.lock").write_text("version = 1\n# new pins\n")
    result = run_check(repo)
    assert result.reproposed == ("bench",)


def test_perfproof_upgrade_revalidates_with_new_provenance(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    revisions: dict[EntryKind, str] = {
        "target": "0.1.0",
        "workload": "9.9.9",
        "how_to_run": "0.1.0",
        "goal": "0.1.0",
        "constraint": "0.1.0",
    }
    result = run_check(repo, version="9.9.9", revisions=revisions)
    assert [f.trigger for f in result.marked_stale] == ["perfproof_upgraded"]
    entry = workload(model_of(repo))
    assert entry.status == "confirmed"
    assert entry.provenance.perfproof_version == "9.9.9"


def test_nothing_stale_writes_nothing(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    raw = store.model_path(repo).read_bytes()
    history = store.history_path(repo).read_bytes()
    result = run_check(repo)
    assert result.records == []
    assert store.model_path(repo).read_bytes() == raw
    assert store.history_path(repo).read_bytes() == history


# Revalidation: what was derived differs, so it needs confirming again


def test_revalidation_that_needs_reconfirmation(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    # A lockfile appears and bench.py changes: derived again, the
    # workload now runs under uv, so its command differs.
    (repo / "uv.lock").write_text("version = 1\n")
    (repo / "bench.py").write_text(PYTHON_FILES["bench.py"] + "# note\n")
    result = run_check(repo)
    assert result.reproposed == ("bench",)
    after = model_of(repo)
    entry = workload(after)
    assert entry.status == "proposed"
    assert entry.version == 2
    assert entry.confirmation is None
    assert entry.command == ("uv", "run", "python", "bench.py")
    # Its confirmed goal depends on it and must be confirmed again too.
    assert goal(after).status == "stale"
    invalidation = goal(after).invalidation
    assert invalidation is not None
    assert invalidation.trigger == "dependency_edited"
    history, _ = store.read_history(repo)
    assert [r.event for r in history if r.entry.id == "bench"][-3:] == [
        "confirmed",
        "marked_stale",
        "reproposed",
    ]
    # The developer confirms the new revision, then the goal.
    confirmed_again, _ = confirm(after, ["bench", "goal-bench"], "s2", LATER)
    assert workload(confirmed_again).status == "confirmed"
    assert goal(confirmed_again).status == "confirmed"


def test_a_stale_entry_that_detection_no_longer_finds_stays_stale(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    (repo / "bench.py").unlink()
    result = run_check(repo)
    assert result.unresolved == (
        ("bench", "detection no longer produces it: retire it?"),
    )
    assert workload(model_of(repo)).status == "stale"


def test_a_frozen_spec_is_carried_over_unless_a_dependency_was_edited(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    frozen = "c" * 64
    freeze(repo, frozen)
    revisions: dict[EntryKind, str] = {
        "target": "0.1.0",
        "workload": "9.9.9",
        "how_to_run": "0.1.0",
        "goal": "0.1.0",
        "constraint": "0.1.0",
    }
    run_check(repo, version="9.9.9", revisions=revisions)
    assert workload(model_of(repo)).spec_hash == frozen
    assert workload(model_of(repo)).status == "confirmed"
    # Editing the goal makes the frozen spec stale.
    store.mutate(
        repo,
        lambda m: set_goal(
            m, "bench", GoalUpdate(metric="cpu_time"), LATER, VERSION
        ),
    )
    assert workload(model_of(repo)).status == "stale"
    # The edited goal is unconfirmed, so the workload cannot clear yet.
    waiting = run_check(repo, version="9.9.9", revisions=revisions)
    assert waiting.unresolved == (
        ("bench", "waiting for goal-bench to be confirmed"),
    )
    assert workload(model_of(repo)).status == "stale"
    store.mutate(repo, lambda m: confirm(m, ["goal-bench"], "s", LATER))
    result = run_check(repo, version="9.9.9", revisions=revisions)
    # Derived again it has no spec, so it differs: confirm it again.
    assert result.reproposed == ("bench",)
    assert workload(model_of(repo)).spec_hash is None
    assert workload(model_of(repo)).status == "proposed"


def test_dependency_edited_clears_only_once_the_dependency_is_confirmed(
    make_repo: MakeRepo,
) -> None:
    """A workload stays stale while its target is unconfirmed."""
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    path = repo / "pyproject.toml"
    path.write_text(path.read_text() + "# a comment\n")
    (repo / "extra.py").write_text("x = 1\n")  # changes the target's evidence
    first = run_check(repo)
    assert first.reproposed == ("target-python_cpu",)
    model = model_of(repo)
    assert model.targets[0].status == "proposed"
    entry = workload(model)
    assert entry.status == "stale"
    assert entry.invalidation is not None
    assert entry.invalidation.trigger == "dependency_edited"
    again = run_check(repo)
    assert workload(model_of(repo)).status == "stale"
    assert again.unresolved == (
        ("bench", "waiting for target-python_cpu to be confirmed"),
    )
    with pytest.raises(NotConfirmedError):
        require_confirmed(repo, "bench")
    store.mutate(repo, lambda m: confirm(m, ["target-python_cpu"], "s", LATER))
    last = run_check(repo)
    assert last.revalidated == ("bench",)
    assert workload(model_of(repo)).status == "confirmed"
    assert require_confirmed(repo, "bench")


# Probes through check


def test_check_probe_records_a_baseline_then_catches_a_change(
    make_repo: MakeRepo, python_on_path: None
) -> None:
    files = {
        **PYTHON_FILES,
        "data.txt": "10\n",
        "bench.py": (
            "from pathlib import Path\n"
            "if __name__ == '__main__':\n"
            "    words = Path('data.txt').read_text().split()\n"
            "    for word in words:\n"
            "        print('rows', word)\n"
            "    if len(words) > 1:\n"
            "        print('multi')\n"
        ),
    }
    repo = make_repo(files)
    confirmed(repo)
    first = run_check(repo, probe=True)
    assert first.marked_stale == ()
    base = load_baselines(repo)["bench"]
    assert base.shape == shape_of("rows 1\n")
    assert base.workload_version == 1
    # Same shape, different number: nothing happens.
    (repo / "data.txt").write_text("99\n")
    assert run_check(repo, probe=True).marked_stale == ()
    # A different shape marks it stale, and then it needs the developer.
    (repo / "data.txt").write_text("5 6\n")
    third = run_check(repo, probe=True)
    assert [f.trigger for f in third.marked_stale] == ["output_shape_changed"]
    assert third.reproposed == ("bench",)
    entry = workload(model_of(repo))
    assert (entry.status, entry.version) == ("proposed", 2)
    assert load_baselines(repo)["bench"].shape == shape_of("rows 1\nmulti\n")
    assert load_baselines(repo)["bench"].workload_version == 2


def test_a_baseline_from_an_older_version_is_not_used(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    model = confirmed(repo)
    probes = {"bench": ProbeResult(0, shape_of("new shape\n"))}
    old = {"bench": Baseline(shape_of("old shape\n"), 0)}
    assert find_stale(
        repo, model, host_id=None, probes=probes, baselines=old
    ) == ([], [])
    current = {"bench": Baseline(shape_of("old shape\n"), 1)}
    findings, _ = find_stale(
        repo, model, host_id=None, probes=probes, baselines=current
    )
    assert [f.trigger for f in findings] == ["output_shape_changed"]


def test_check_probe_a_failing_command_stays_stale(
    make_repo: MakeRepo, python_on_path: None
) -> None:
    repo = make_repo(
        {
            **PYTHON_FILES,
            "data.txt": "ok\n",
            "bench.py": (
                "from pathlib import Path\n"
                "if __name__ == '__main__':\n"
                "    text = Path('data.txt').read_text()\n"
                "    print(text)\n"
                "    raise SystemExit(0 if text.strip() == 'ok' else 3)\n"
            ),
        }
    )
    confirmed(repo)
    run_check(repo, probe=True)
    (repo / "data.txt").write_text("broken\n")
    result = run_check(repo, probe=True)
    assert [f.trigger for f in result.marked_stale] == ["command_failed"]
    assert result.unresolved[0][0] == "bench"
    assert "still fails" in result.unresolved[0][1]
    assert workload(model_of(repo)).status == "stale"
    # Fixed: revalidated on the next probe.
    (repo / "data.txt").write_text("ok\n")
    again = run_check(repo, probe=True)
    assert again.revalidated == ("bench",)
    assert workload(model_of(repo)).status == "confirmed"


def test_check_probe_does_not_run_a_workload_whose_files_changed(
    make_repo: MakeRepo, python_on_path: None, tmp_path: Path
) -> None:
    marker = tmp_path / "ran"
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)
    (repo / "bench.py").write_text(
        "from pathlib import Path\n"
        "if __name__ == '__main__':\n"
        f"    Path({str(marker)!r}).write_text('x')\n"
    )
    result = run_check(repo, probe=True)
    assert result.reproposed == ("bench",)
    assert not marker.exists()
    # Still not run while it waits to be confirmed again.
    assert not run_check(repo, probe=True).marked_stale
    assert not marker.exists()


def test_probe_never_runs_a_proposed_workload(
    make_repo: MakeRepo, python_on_path: None, tmp_path: Path
) -> None:
    from project_helpers import proposed

    marker = tmp_path / "ran"
    repo = make_repo(
        {
            **PYTHON_FILES,
            "bench.py": (
                "from pathlib import Path\n"
                "if __name__ == '__main__':\n"
                f"    Path({str(marker)!r}).write_text('x')\n"
            ),
        }
    )
    proposed(repo)
    run_check(repo, probe=True)
    assert not marker.exists()


def test_check_needs_a_model(make_repo: MakeRepo) -> None:
    from perfproof.project.errors import ProjectError

    with pytest.raises(ProjectError) as caught:
        run_check(make_repo(PYTHON_FILES))
    assert caught.value.code == "no_model"


def test_an_edited_goal_bound_to_this_machine_goes_stale_elsewhere(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    confirmed(repo)

    def bind(m: ProjectModel):
        m, a = set_goal(m, "bench", GoalUpdate(host_id=HOST_A), LATER, VERSION)
        m, b = confirm(m, ["goal-bench"], "s", LATER)
        return m, [*a, *b]

    store.mutate(repo, bind)
    assert run_check(repo, host=HOST_A).marked_stale == ()
    result = run_check(repo, host=HOST_B)
    assert [(f.entry_id, f.trigger) for f in result.marked_stale] == [
        ("goal-bench", "machine_differs")
    ]
    assert result.unresolved[0][0] == "goal-bench"
    assert goal(model_of(repo)).status == "stale"
