"""The hotspot check on saved ``perfproof profile`` results.

The results are produced by running the real CLI on the ``membership``
example, with a stand-in command profiler that emits a captured py-spy
fixture (``tests/fixtures/py-spy/membership.txt``). In that profile
``find_spec`` is rank 5 by self share and in no top eight by total
share; the argparse ``__init__`` is rank 6 by total share and has no
self time. Transcripts around them are built here, with timestamps
set against the result's own ``finished_at``.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from perfproof.cli import main

import runner
import scoring
import taskset
import transcript
from taskset import Hotspot, Task

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FOLDED = ROOT / "tests" / "fixtures" / "py-spy" / "membership.txt"

PROFILER = """\
import shutil, subprocess, sys
fixture, output, *command = sys.argv[1:]
subprocess.run(command, check=True)
shutil.copyfile(fixture, output)
"""

HIDDEN = Hotspot(
    file="<frozen importlib._bootstrap_external>",
    function="find_spec",
    top_n=5,
)
TOTAL_SIX = Hotspot(file="argparse.py", function="__init__", top_n=5)


class Profiled:
    """What one real ``perfproof profile`` run left."""

    def __init__(self, result: Path, text: str) -> None:
        self.result = result
        self.text = text
        self.data: dict[str, Any] = json.loads(result.read_text())
        finished = transcript.parse_time(self.data["finished_at"])
        assert finished is not None
        self.finished = finished


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        timeout=30,
    )


@pytest.fixture(scope="module")
def profiled(tmp_path_factory: pytest.TempPathFactory) -> Profiled:
    repo = tmp_path_factory.mktemp("repo") / "membership"
    shutil.copytree(
        ROOT / "examples" / "membership",
        repo,
        ignore=shutil.ignore_patterns("candidates"),
    )
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(
        repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"
    )
    (repo / "copy_profiler.py").write_text(PROFILER)
    template = json.dumps(
        [
            sys.executable,
            str(repo / "copy_profiler.py"),
            str(FOLDED),
            "{output}",
            "{command}",
        ]
    )
    with pytest.MonkeyPatch.context() as patch:
        path = os.environ.get("PATH", os.defpath)
        patch.setenv(
            "PATH", f"{Path(sys.executable).parent}{os.pathsep}{path}"
        )
        spec = ["--spec", str(repo / "spec.json"), "--repo", str(repo)]
        assert main(["bench", "define", "held-orders", *spec]) == 0
        argv = [
            "profile",
            "held-orders",
            "--profiler-command",
            template,
            "--repo",
            str(repo),
            "--top",
            "10",
        ]
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            assert main(argv) == 0
    (result,) = (repo / ".perfproof" / "profiles").glob("*/*/result.json")
    return Profiled(result, printed.getvalue())


def _task(hotspot: Hotspot) -> Task:
    tasks = taskset.load(HERE / "tasks-real.json")
    task = next(t for t in tasks.tasks if t.id == "real-fd-hidden-fix")
    return task.model_copy(
        update={"expect": task.expect.model_copy(update={"hotspot": hotspot})}
    )


def _stamp(profile: Profiled, seconds: float) -> str:
    when = profile.finished + timedelta(seconds=seconds)
    return when.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _call(
    profile: Profiled,
    seconds: float,
    tool: str,
    args: dict[str, Any],
    ident: str,
) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": _stamp(profile, seconds),
        "message": {
            "content": [
                {"type": "tool_use", "id": ident, "name": tool, "input": args}
            ]
        },
    }


def _said(profile: Profiled, seconds: float, text: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": _stamp(profile, seconds),
        "message": {"content": [{"type": "text", "text": text}]},
    }


def _back(profile: Profiled, seconds: float, ident: str, text: str) -> Any:
    return {
        "type": "user",
        "timestamp": _stamp(profile, seconds),
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": ident, "content": text}
            ]
        },
    }


def _run(
    profile: Profiled,
    printed: str,
    said: str,
    edit_at: float | None = None,
) -> list[transcript.Turn]:
    """A profile call, what it printed, what was said, maybe an edit."""
    command = "perfproof profile held-orders --json > /dev/null"
    events = [
        _call(profile, -3, "Bash", {"command": command}, "p1"),
        _back(profile, 0.5, "p1", printed),
        _said(profile, 1, said),
    ]
    if edit_at is not None:
        events.append(
            _call(profile, edit_at, "Edit", {"file_path": "orders.py"}, "e1")
        )
    return [transcript.Turn(events, None)]


def _saved(profile: Profiled, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    name = runner.profile_file_name("held-orders", profile.data["run_id"])
    shutil.copyfile(profile.result, directory / name)
    return directory


def _score(
    task: Task, turns: list[transcript.Turn], directory: Path | None
) -> tuple[bool | None, str]:
    saved = (
        None if directory is None else scoring.load_saved_profiles(directory)
    )
    return scoring.hotspot_check(task, turns, saved)


NAMED = "`find_spec` is where the time goes."


def test_the_saved_result_is_the_one_the_cli_printed(
    profiled: Profiled,
) -> None:
    names = [h["function"] for h in profiled.data["hotspots"]]
    assert names.index("find_spec") == 4
    totals = [h["function"] for h in profiled.data["frames_by_total"]]
    assert "find_spec" not in totals
    assert totals.index("__init__") == 5


def test_a_hotspot_at_self_rank_five_passes_with_no_labelled_printout(
    profiled: Profiled, tmp_path: Path
) -> None:
    turns = _run(profiled, "done", NAMED)
    directory = _saved(profiled, tmp_path)
    assert _score(_task(HIDDEN), turns, directory) == (
        True,
        "find_spec is in the profile's top 5 and is named",
    )
    # Without the saved result the printed text is all there is, and it
    # holds no profile.
    found, why = _score(_task(HIDDEN), turns, None)
    assert found is False
    assert "no profile output" in why


def test_a_function_at_total_rank_six_fails(
    profiled: Profiled, tmp_path: Path
) -> None:
    turns = _run(profiled, "done", "`__init__` is where the time goes.")
    directory = _saved(profiled, tmp_path)
    found, why = _score(_task(TOTAL_SIX), turns, directory)
    assert found is False
    assert "is not in the first 5 of hotspots or frames_by_total" in why
    wide = TOTAL_SIX.model_copy(update={"top_n": 6})
    assert _score(_task(wide), turns, directory)[0] is True


def test_a_profile_that_finished_after_the_first_edit_does_not_count(
    profiled: Profiled, tmp_path: Path
) -> None:
    directory = _saved(profiled, tmp_path)
    before = _run(profiled, "done", NAMED, edit_at=-1.0)
    found, why = _score(_task(HIDDEN), before, directory)
    assert found is False
    assert "no profile output" in why
    after = _run(profiled, "done", NAMED, edit_at=2.0)
    assert _score(_task(HIDDEN), after, directory)[0] is True


def test_naming_before_the_profile_finished_does_not_count(
    profiled: Profiled, tmp_path: Path
) -> None:
    directory = _saved(profiled, tmp_path)
    turns = _run(profiled, "done", "nothing to say yet")
    early = _said(profiled, -2, NAMED)
    turns[0].events.insert(1, early)
    found, why = _score(_task(HIDDEN), turns, directory)
    assert found is False
    assert "never named" in why


def test_an_unreadable_saved_result_falls_back_to_the_printed_text(
    profiled: Profiled, tmp_path: Path
) -> None:
    directory = _saved(profiled, tmp_path)
    (directory / "profile-held-orders-torn.json").write_text("{")
    assert scoring.load_saved_profiles(directory) is None
    printed = "no profile here"
    found, why = _score(
        _task(HIDDEN), _run(profiled, printed, NAMED), directory
    )
    assert found is False
    assert "no profile output" in why


def test_runs_without_saved_results_are_read_from_the_printed_text(
    profiled: Profiled, tmp_path: Path
) -> None:
    """The reader every archived run still uses."""
    task = _task(Hotspot(file="orders.py", function="held_orders", top_n=5))
    turns = _run(profiled, profiled.text, "`held_orders` is the cost.")
    assert scoring.load_saved_profiles(tmp_path) == []
    assert _score(task, turns, tmp_path) == (
        True,
        "held_orders is in the profile's top 5 and is named",
    )


def test_the_runner_keeps_only_the_result_files(
    profiled: Profiled, tmp_path: Path
) -> None:
    repo = profiled.result.parents[4]
    out = tmp_path / "task"
    out.mkdir()
    kept = runner.save_profile_results(repo, out)
    run_id = profiled.data["run_id"]
    assert [p.name for p in kept] == [
        runner.profile_file_name("held-orders", run_id)
    ]
    assert sorted(p.name for p in out.iterdir()) == [kept[0].name]
    assert kept[0].read_bytes() == profiled.result.read_bytes()
    loaded = scoring.load_saved_profiles(out)
    assert loaded is not None
    assert [p.finished_at for p in loaded] == [profiled.finished]


def test_the_runner_skips_a_linked_result(
    profiled: Profiled, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    run = repo / ".perfproof" / "profiles" / "held-orders" / "id1"
    run.mkdir(parents=True)
    secret = tmp_path / "secret.json"
    secret.write_text("{}")
    (run / "result.json").symlink_to(secret)
    out = tmp_path / "task"
    out.mkdir()
    assert runner.save_profile_results(repo, out) == []
    assert [p.name for p in out.iterdir()] == [runner.PROFILE_SKIPPED]


def test_the_runner_skips_a_pipe_named_result(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    run = repo / ".perfproof" / "profiles" / "held-orders" / "id1"
    run.mkdir(parents=True)
    os.mkfifo(run / "result.json")
    out = tmp_path / "task"
    out.mkdir()
    assert runner.save_profile_results(repo, out) == []


def test_a_deeply_nested_saved_file_is_unreadable_not_fatal(
    tmp_path: Path,
) -> None:
    (tmp_path / "profile-a-b.json").write_text("[" * 100_000 + "]" * 100_000)
    assert scoring.load_saved_profiles(tmp_path) is None


def _planted(tmp_path: Path, profiled: Profiled) -> tuple[Path, Path]:
    """A repo with one real result under ``.perfproof/profiles``."""
    repo = tmp_path / "repo"
    run = repo / ".perfproof" / "profiles" / "held-orders" / "id1"
    run.mkdir(parents=True)
    shutil.copyfile(profiled.result, run / "result.json")
    out = tmp_path / "task"
    out.mkdir()
    return repo, out


def test_the_runner_copies_a_plain_result(
    profiled: Profiled, tmp_path: Path
) -> None:
    repo, out = _planted(tmp_path, profiled)
    assert len(runner.save_profile_results(repo, out)) == 1
    assert not (out / runner.PROFILE_SKIPPED).exists()


@pytest.mark.parametrize("link", [0, 1, 2, 3])
def test_the_runner_skips_a_result_under_a_linked_directory(
    profiled: Profiled, tmp_path: Path, link: int
) -> None:
    """One level of the path to the result is a link."""
    repo, out = _planted(tmp_path, profiled)
    parts = [".perfproof", "profiles", "held-orders", "id1"]
    path = repo.joinpath(*parts[: link + 1])
    real = tmp_path / "elsewhere"
    path.rename(real)
    path.symlink_to(real)
    assert runner.save_profile_results(repo, out) == []
    assert "symlink" in (out / runner.PROFILE_SKIPPED).read_text()


def test_the_runner_skips_a_result_over_the_size_limit(
    profiled: Profiled, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, out = _planted(tmp_path, profiled)
    size = profiled.result.stat().st_size
    monkeypatch.setattr(runner, "PROFILE_RESULT_LIMIT", size)
    assert len(runner.save_profile_results(repo, out)) == 1
    monkeypatch.setattr(runner, "PROFILE_RESULT_LIMIT", size - 1)
    assert runner.save_profile_results(repo, out) == []
    assert "bytes" in (out / runner.PROFILE_SKIPPED).read_text()


def test_a_long_agent_made_name_is_bounded_and_does_not_collide(
    profiled: Profiled, tmp_path: Path
) -> None:
    repo, out = _planted(tmp_path, profiled)
    base = repo / ".perfproof" / "profiles"
    for name in ("n" * 200, "n" * 199 + "m"):
        run = base / name / ("i" * 100)
        run.mkdir(parents=True)
        shutil.copyfile(profiled.result, run / "result.json")
    kept = runner.save_profile_results(repo, out)
    assert len(kept) == 3
    assert len({p.name for p in kept}) == 3
    assert all(len(p.name) < 120 for p in kept)
    assert not (out / runner.PROFILE_SKIPPED).exists()


def test_a_copy_that_cannot_be_written_is_skipped_and_recorded(
    profiled: Profiled, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, out = _planted(tmp_path, profiled)

    def refuse(_: str, __: str) -> str:
        return "x" * 300

    monkeypatch.setattr(runner, "profile_file_name", refuse)
    assert runner.save_profile_results(repo, out) == []
    assert "not copied" in (out / runner.PROFILE_SKIPPED).read_text()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_a_result_in_an_unsearchable_directory_is_skipped(
    profiled: Profiled, tmp_path: Path
) -> None:
    repo, out = _planted(tmp_path, profiled)
    run = repo / ".perfproof" / "profiles" / "held-orders" / "id1"
    run.chmod(0o600)
    try:
        assert runner.save_profile_results(repo, out) == []
    finally:
        run.chmod(0o755)
    assert "held-orders/id1" in (out / runner.PROFILE_SKIPPED).read_text()


def test_a_name_that_is_not_utf8_is_copied_or_skipped_not_raised(
    profiled: Profiled, tmp_path: Path
) -> None:
    """Linux keeps such names as surrogate escapes; macOS refuses."""
    repo, out = _planted(tmp_path, profiled)
    raw = os.fsencode(repo / ".perfproof" / "profiles") + b"/na\xff\xfe"
    try:
        os.makedirs(raw + b"/r1")
    except OSError:
        pytest.skip("the file system refuses non-UTF-8 names")
    shutil.copyfile(profiled.result, os.fsdecode(raw + b"/r1/result.json"))
    saved = runner.save_profile_results(repo, out)
    assert len(saved) == 2
    assert all(p.name.startswith("profile-") for p in saved)
