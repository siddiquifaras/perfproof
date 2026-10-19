import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest
from project_helpers import NOW, PYTHON_FILES, MakeRepo, proposed

from perfproof.project import store
from perfproof.project.entries import record, with_fields
from perfproof.project.errors import ProjectError
from perfproof.schema import ProjectHistoryRecord, ProjectModel


def test_round_trip_through_disk(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    written = proposed(repo)
    loaded = store.load_model(repo)
    assert loaded is not None
    assert loaded == written
    assert [e.id for e in loaded.targets] == ["target-python_cpu"]


def test_no_model_is_none(make_repo: MakeRepo) -> None:
    assert store.load_model(make_repo(PYTHON_FILES)) is None


def test_unreadable_model_is_refused_and_not_replaced(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    proposed(repo)
    path = store.model_path(repo)
    path.write_text("{not json")
    with pytest.raises(ProjectError) as caught:
        proposed(repo)
    assert caught.value.code == "invalid_model"
    assert path.read_text() == "{not json"


def test_history_keeps_every_revision_and_skips_torn_lines(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    proposed(repo)
    records, skipped = store.read_history(repo)
    assert [r.event for r in records] == ["created"] * 4
    assert skipped == ()
    with store.history_path(repo).open("a") as handle:
        handle.write('{"torn": ')
    records, skipped = store.read_history(repo)
    assert len(records) == 4
    assert skipped == (5,)


def retire_first_workload(
    model: ProjectModel,
) -> tuple[ProjectModel, list[ProjectHistoryRecord]]:
    entry = with_fields(
        model.workloads[0], status="retired", retired_reason="x"
    )
    return model, [record("retired", entry, NOW)]


def test_append_after_a_torn_line_starts_a_new_line(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    proposed(repo)
    with store.history_path(repo).open("a") as handle:
        handle.write('{"torn": ')
    store.mutate(repo, retire_first_workload)
    records, skipped = store.read_history(repo)
    assert len(records) == 5
    assert len(skipped) == 1


def test_failed_rename_leaves_the_model_intact_and_no_temp_file(
    make_repo: MakeRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(PYTHON_FILES)
    before = proposed(repo)
    raw = store.model_path(repo).read_text()

    def boom(self: Path, target: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(ProjectError) as caught:
        store.mutate(repo, retire_first_workload)
    assert caught.value.code == "state_write_failed"
    monkeypatch.undo()
    assert store.model_path(repo).read_text() == raw
    assert store.load_model(repo) == before
    leftovers = [p.name for p in (repo / ".perfproof").iterdir()]
    assert not [n for n in leftovers if n.endswith(".tmp")]


CRASH = textwrap.dedent(
    """
    import os, sys
    from pathlib import Path
    from datetime import datetime, UTC
    from perfproof.project import store
    from perfproof.project.entries import record, with_fields

    repo = Path(sys.argv[1])
    Path.replace = lambda self, target: os._exit(9)

    def change(model):
        entry = with_fields(
            model.workloads[0], status="retired", retired_reason="x"
        )
        return model, [record("retired", entry, datetime.now(UTC))]

    store.mutate(repo, change)
    """
)


def test_hard_crash_between_history_and_rename_leaves_the_model_whole(
    make_repo: MakeRepo,
) -> None:
    repo = make_repo(PYTHON_FILES)
    before = proposed(repo)
    raw = store.model_path(repo).read_text()
    done = subprocess.run(
        [sys.executable, "-c", CRASH, str(repo)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 9, done.stderr
    assert store.model_path(repo).read_text() == raw
    assert store.load_model(repo) == before
    records, skipped = store.read_history(repo)
    assert skipped == ()
    # The revision whose rename never happened is an orphan line: kept,
    # and the model still says what it said.
    assert [r.event for r in records][-1] == "retired"
    assert before.workloads[0].status == "proposed"
    # A later change works after the crash, with the lock released.
    proposed(repo)
    assert store.load_model(repo) == before


def test_writers_are_serialised(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    proposed(repo)
    order: list[str] = []
    gate = threading.Event()

    def first() -> None:
        with store.locked(repo):
            order.append("first-in")
            gate.set()
            threading.Event().wait(0.3)
            order.append("first-out")

    def second() -> None:
        gate.wait()
        with store.locked(repo):
            order.append("second-in")

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert order == ["first-in", "first-out", "second-in"]


def test_lock_timeout(
    make_repo: MakeRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(PYTHON_FILES)
    proposed(repo)
    monkeypatch.setattr(store, "LOCK_TIMEOUT_SECONDS", 0.2)
    with store.locked(repo):
        fd = os.open(repo / ".perfproof" / "project.lock", os.O_RDWR)
        try:
            import fcntl

            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        result: list[str] = []

        def other() -> None:
            try:
                with store.locked(repo):
                    result.append("got it")
            except ProjectError as exc:
                result.append(exc.code)

        t = threading.Thread(target=other)
        t.start()
        t.join()
    assert result == ["lock_timeout"]
