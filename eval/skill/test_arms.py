"""The baseline arm: task fields, sandbox, scoring, speed check, report.

No test calls a model. Transcripts here are written by the test, but
every tool output in them is what a real command printed when the test
ran it (a timing script, a git repository), never typed by hand; only
the stream-json envelope around it is built. The sandbox test runs the
real ``run_task`` with ``claude`` replaced by a Python process that
reports what the agent's sandbox looks like from inside.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import arms
import compare
import runner
import sandbox
import skill_eval
import speedcheck
import taskset
from runner import Meta, State, TreeState, TurnMeta
from taskset import Task, TaskSet

TASKS = taskset.load()
CONFINED = pytest.mark.skipif(
    not sandbox.confinement_available(), reason="needs macOS sandbox-exec"
)
PROMPTS = {
    "vague": {"skill": "/perfproof", "baseline": "make the app faster"},
    "detailed": {
        "skill": "/perfproof the rolling sum is slow",
        "baseline": "the rolling sum is slow",
    },
}
HOTSPOT_TASK = next(
    t
    for t in TASKS.tasks
    if t.example == "rolling" and t.expect.hotspot is not None
)


def _with(task: Task, **update: Any) -> Task:
    return Task.model_validate({**task.model_dump(), **update})


# --- task fields ------------------------------------------------------


def test_prompts_pick_by_arm_and_level() -> None:
    task = _with(HOTSPOT_TASK, prompts=PROMPTS)
    assert task.prompt_for("skill", "vague") == "/perfproof"
    assert task.prompt_for("baseline", "vague") == "make the app faster"
    assert task.prompt_for("skill", "detailed").startswith("/perfproof the")
    assert task.prompt_for("baseline", "detailed") == "the rolling sum is slow"


def test_a_task_without_prompts_has_only_skill_vague() -> None:
    task = _with(HOTSPOT_TASK, prompts=None)
    assert task.prompt_for("skill", "vague") == HOTSPOT_TASK.prompt
    for arm, level in (
        ("baseline", "vague"),
        ("skill", "detailed"),
        ("baseline", "detailed"),
    ):
        with pytest.raises(ValueError, match="no prompts"):
            task.prompt_for(arm, level)  # type: ignore[arg-type]


def test_the_file_format_accepts_prompts_and_speed_check(
    tmp_path: Path,
) -> None:
    raw = json.loads(taskset.TASKS_FILE.read_text())
    entry = {**raw["tasks"][0]}
    entry.pop("prompt")
    entry["prompts"] = PROMPTS
    entry["speed_check"] = {"command": "python bench.py", "metric": "wall"}
    raw["tasks"] = [entry]
    path = tmp_path / "t.json"
    path.write_text(json.dumps(raw))
    task = taskset.load(path).tasks[0]
    assert task.prompts is not None
    assert task.speed_check is not None
    assert task.speed_check.command == "python bench.py"
    assert task.speed_check.build is None


def test_a_task_needs_a_prompt_of_some_kind(tmp_path: Path) -> None:
    raw = json.loads(taskset.TASKS_FILE.read_text())
    entry = {**raw["tasks"][0]}
    entry.pop("prompt")
    raw["tasks"] = [entry]
    path = tmp_path / "t.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="set prompt or prompts"):
        taskset.load(path)


def test_run_refuses_an_arm_a_task_has_no_prompt_for() -> None:
    chosen = [_with(HOTSPOT_TASK, prompts=None)]
    skill_eval.require_prompts(chosen, "skill", "vague")
    with pytest.raises(skill_eval.PromptMissingError, match="baseline/vague"):
        skill_eval.require_prompts(chosen, "baseline", "vague")


# --- the baseline sandbox has neither skill nor CLI ---


def test_baseline_repo_has_no_skill_and_the_skill_repo_does(
    tmp_path: Path,
) -> None:
    plain = tmp_path / "plain"
    runner.build_repo(HOTSPOT_TASK, plain, None)
    assert not (plain / ".claude").exists()
    kept = tmp_path / "kept"
    runner.build_repo(HOTSPOT_TASK, kept, runner.SKILL_SOURCE)
    assert (kept / ".claude" / "skills" / "perfproof" / "SKILL.md").is_file()


def test_baseline_launchers_hold_python_only(tmp_path: Path) -> None:
    both = runner.shim_dir(tmp_path / "a")
    assert (both / "perfproof").exists()
    only = runner.shim_dir(tmp_path / "b", with_cli=False)
    assert sorted(p.name for p in only.iterdir()) == ["python"]
    # A directory reused from a skill run loses the launcher.
    runner.shim_dir(tmp_path / "a", with_cli=False)
    assert not (both / "perfproof").exists()


def test_path_without_cli_drops_the_directories_that_hold_it(
    tmp_path: Path,
) -> None:
    venv = tmp_path / "venv-bin"
    venv.mkdir()
    (venv / "perfproof").write_text("#!/bin/sh\n")
    (venv / "perfproof").chmod(0o755)
    other = tmp_path / "other"
    other.mkdir()
    path = f"{venv}:{other}:/usr/bin"
    assert runner.path_without_cli(path) == f"{other}:/usr/bin"
    repo = tmp_path / "repo"
    (repo / ".claude" / "skills").mkdir(parents=True)
    leaks = runner.baseline_leaks(repo, {"PATH": path})
    assert any("skills" in leak for leak in leaks)
    assert any("PATH" in leak for leak in leaks)
    clean = tmp_path / "clean"
    clean.mkdir()
    assert runner.baseline_leaks(clean, {"PATH": f"{other}:/usr/bin"}) == []


class _Recorder:
    """``subprocess.Popen`` stand-in: ``claude`` becomes a probe."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.real = subprocess.Popen

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        self.calls.append((list(argv), kwargs))
        if "claude" in argv and "-p" in argv:
            # Runs inside the sandbox, in the repo, with the agent's
            # environment: what the agent could see or use.
            body = (
                f"ROOT = {str(runner.REPO_ROOT)!r}\n"
                "import json, os, shutil, subprocess\n"
                "def readable(f):\n"
                "  try:\n"
                "    f()\n"
                "    return True\n"
                "  except OSError:\n"
                "    return False\n"
                "def runs(code):\n"
                "  return subprocess.run(['python', '-c', code],\n"
                "    capture_output=True).returncode == 0\n"
                "open('hello.txt', 'w').write('changed')\n"
                "seen = {\n"
                "  'perfproof_on_path': shutil.which('perfproof'),\n"
                "  'python_on_path': shutil.which('python') is not None,\n"
                "  'skills_dir': os.path.exists('.claude/skills'),\n"
                "  'skill_readable': readable(lambda: open(\n"
                "    ROOT + '/.venv/perfproof/SKILL.md').read(1)),\n"
                "  'src_readable': readable(lambda: os.listdir(\n"
                "    ROOT + '/src/perfproof')),\n"
                "  'imports_perfproof': runs('import perfproof'),\n"
                "  'imports_numpy': runs('import numpy, pytest'),\n"
                "}\n"
                "print(json.dumps({'type': 'result', 'session_id': 's',\n"
                "  'result': json.dumps(seen), 'total_cost_usd': 0.0,\n"
                "  'num_turns': 1}))\n"
            )
            # Keep the sandbox-exec prefix: the probe runs confined.
            prefix = argv[: argv.index("claude")]
            # The base interpreter: the venv's own is closed to the
            # baseline, as it should be.
            base = str((runner.venv_dir() / "bin" / "python").resolve())
            return self.real([*prefix, base, "-c", body], **kwargs)
        if "claude" in argv:
            return self.real(
                [sys.executable, "-c", "print('probe')"], **kwargs
            )
        return self.real(argv, **kwargs)


def _fake_oauth(**_: Any) -> str:
    return "fake-oauth"


def _dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arm: taskset.Arm,
    level: taskset.Level,
) -> tuple[_Recorder, Path, Meta]:
    monkeypatch.setattr(sandbox, "claude_oauth_token", _fake_oauth)
    recorder = _Recorder()
    monkeypatch.setattr(subprocess, "Popen", recorder)
    task = _with(
        HOTSPOT_TASK,
        prompts=PROMPTS,
        followups=["a follow-up"],
        speed_check=taskset.SpeedCheck(command="python -c pass"),
    )
    tasks = TaskSet(model=TASKS.model, tasks=[task])
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    meta = runner.run_task(
        task,
        tasks,
        run_dir,
        runner.SKILL_SOURCE,
        runner.shim_dir(run_dir, with_cli=arm == "skill"),
        work_root=tmp_path / "work",
        arm=arm,
        level=level,
    )
    return recorder, run_dir / task.id, meta


@CONFINED
def test_baseline_dry_run_sees_no_skill_and_no_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The venv's bin directory leads the harness's own PATH here, as it
    # does under ``uv run``: the baseline must still not see the CLI.
    monkeypatch.setenv(
        "PATH", f"{runner.venv_dir() / 'bin'}:{__import__('os').defpath}"
    )
    recorder, task_dir, meta = _dry_run(
        tmp_path, monkeypatch, "baseline", "detailed"
    )
    launches = [c for c in recorder.calls if "-p" in c[0]]
    assert len(launches) == 1
    argv, _ = launches[0]
    assert argv[argv.index("-p") + 1] == "the rolling sum is slow"
    seen = json.loads(
        json.loads((task_dir / "turn-1.jsonl").read_text().splitlines()[-1])[
            "result"
        ]
    )
    assert seen["perfproof_on_path"] is None
    assert seen["skills_dir"] is False
    assert seen["python_on_path"] is True
    # The perfproof environment and sources are closed to the baseline,
    # its python cannot import the package, and the example's own
    # third-party packages still work.
    assert seen["skill_readable"] is False
    assert seen["src_readable"] is False
    assert seen["imports_perfproof"] is False
    assert seen["imports_numpy"] is True
    assert meta.arm == "baseline"
    assert meta.level == "detailed"
    assert meta.skill_sha == "none"
    saved = json.loads((task_dir / "state.json").read_text())
    assert "hello.txt" in saved["base_changed"]
    speed = speedcheck.SpeedResult.model_validate_json(
        (task_dir / speedcheck.SPEED_FILE).read_text()
    )
    assert speed.status == "measured"
    assert speed.final_ref == "working"
    assert speed.rounds == speedcheck.ROUNDS
    # Same sandbox, budget, model as the skill arm: only the prompt and
    # what is installed differ.
    skill_argv = _claude_argv_of(tmp_path / "skill", monkeypatch)

    def claude_part(words: list[str]) -> list[str]:
        words = words[words.index("claude") :]
        return [w for w in words if w != words[words.index("-p") + 1]]

    assert claude_part(argv) == claude_part(skill_argv)


def _claude_argv_of(base: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    base.mkdir()
    recorder, task_dir, meta = _dry_run(base, monkeypatch, "skill", "detailed")
    assert meta.arm == "skill"
    assert meta.skill_sha != "none"
    probe = json.loads(
        json.loads((task_dir / "turn-1.jsonl").read_text().splitlines()[-1])[
            "result"
        ]
    )
    assert probe["skills_dir"] is True
    assert probe["perfproof_on_path"] is not None
    assert probe["skill_readable"] is True
    assert probe["src_readable"] is True
    assert probe["imports_perfproof"] is True
    return next(c for c in recorder.calls if "-p" in c[0])[0]


# --- transcripts ------------------------------------------------------

_TIMING_SOURCE = (
    "import time\n"
    "t = time.perf_counter()\n"
    "sum(range(100000))\n"
    "print(f'{time.perf_counter() - t:.6f} s')\n"
)


def _real_timing() -> tuple[str, str]:
    """Run a timing command for real: its text and what it printed."""
    # Recorded as the agent would type it: the checkout's path (which
    # holds the project's name) is not part of an agent's command.
    command = shlex.join(["python", "-c", _TIMING_SOURCE])
    done = subprocess.run(
        [sys.executable, "-c", _TIMING_SOURCE],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return command, done.stdout


def _tool(n: int, name: str, args: dict[str, Any]) -> dict[str, Any]:
    block = {"type": "tool_use", "id": f"t{n}", "name": name, "input": args}
    return {"type": "assistant", "message": {"content": [block]}}


def _out(n: int, text: str) -> dict[str, Any]:
    block = {"type": "tool_result", "tool_use_id": f"t{n}", "content": text}
    return {"type": "user", "message": {"content": [block]}}


def _timing_pair(n: int) -> list[dict[str, Any]]:
    command, printed = _real_timing()
    return [_tool(n, "Bash", {"command": command}), _out(n, printed)]


def _edit(n: int) -> list[dict[str, Any]]:
    args = {"file_path": "rolling.py", "old_string": "a", "new_string": "b"}
    return [_tool(n, "Edit", args), _out(n, "ok")]


def _state(**update: Any) -> State:
    base = State(
        status="",
        current_branch="main",
        branches={"main": "abc"},
        seeded={"main": "abc"},
        trees=[
            TreeState(
                ref="working",
                changed=["rolling.py"],
                tests_ok=True,
                tests_tail="",
            )
        ],
        run_verdicts=[],
        base_changed=["rolling.py"],
        final_ref="working",
    )
    return base.model_copy(update=update)


def _write_run(
    run_dir: Path,
    task: Task,
    arm: taskset.Arm,
    level: taskset.Level,
    events: list[dict[str, Any]],
    final: str,
    state: State | None = None,
    speed: speedcheck.SpeedResult | None = None,
    cost: float = 0.5,
    seconds: float = 100.0,
) -> Path:
    task_dir = run_dir / task.id
    task_dir.mkdir(parents=True)
    skills: list[str] = []
    init: dict[str, Any] = {
        "type": "system",
        "subtype": "init",
        "skills": skills,
    }
    result: dict[str, Any] = {
        "type": "result",
        "result": final,
        "total_cost_usd": cost,
        "num_turns": 3,
    }
    lines: list[dict[str, Any]] = [init, *events, result]
    (task_dir / "turn-1.jsonl").write_text(
        "\n".join(json.dumps(e) for e in lines) + "\n"
    )
    meta = Meta(
        task_id=task.id,
        started_at="2026-10-29T00:00:00+00:00",
        wall_seconds=seconds,
        load_start=[0.0, 0.0, 0.0],
        load_end=[0.0, 0.0, 0.0],
        claude_version="test",
        skill_sha="none" if arm == "baseline" else "x",
        turns=[
            TurnMeta(
                prompt="p",
                argv=["claude", "-p", "p"],
                seconds=seconds,
                exit_code=0,
                session_id="s",
            )
        ],
        arm=arm,
        level=level,
    )
    (task_dir / "meta.json").write_text(meta.model_dump_json())
    (task_dir / "state.json").write_text((state or _state()).model_dump_json())
    if speed is not None:
        (task_dir / speedcheck.SPEED_FILE).write_text(speed.model_dump_json())
    arms.write_run_marker(run_dir, arm, level)
    return task_dir


# --- claims -----------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "The function is now 40% faster.",
        "That is a 3x speedup.",
        "It runs 2.5 times faster than before.",
        "This should be quicker on long inputs.",
        "It reduces the runtime considerably.",
        "A clear performance improvement.",
        "It sped up the nightly job.",
        "The loop halved the runtime.",
        "A 35% reduction in runtime.",
        "It is twice as fast.",
        "Roughly ~4x on the benchmark.",
        "It now takes 1.2s instead of 3.4s.",
        "No regressions; 40% faster.",
        "I rejected the loop version; the new one is 3x faster.",
    ],
)
def test_speed_claims_are_found(sentence: str) -> None:
    assert arms.extract_claims(f"Done. {sentence}") == [sentence]


@pytest.mark.parametrize(
    "sentence",
    [
        "I could not make it faster.",
        "Is it faster now?",
        "There is no speedup to report.",
        "I didn't see it run quicker.",
        "The tests pass.",
        "I changed rolling_sum to use a prefix sum.",
        "It is not faster than before.",
        "The new loop is no faster.",
        "It isn't quicker on small inputs.",
        # An honest skill-arm verdict is not a claim of a speed-up.
        "perfproof REJECT: the change is 0.4% faster, within noise, "
        "below the bar.",
        "INCONCLUSIVE: it may be slightly faster but the interval spans 1.",
        "The candidate reduced runtime by 1% which is under the 5% bar.",
        "Verdict REJECT.",
    ],
)
def test_sentences_that_are_not_claims_are_left_out(sentence: str) -> None:
    assert arms.extract_claims(sentence) == []


def test_fenced_code_is_not_read_for_claims() -> None:
    text = "Code:\n```\n# 10x faster hack\n```\nDone."
    assert arms.extract_claims(text) == []


# --- backing ----------------------------------------------------------


def test_baseline_claim_is_backed_by_timings_of_both_versions(
    tmp_path: Path,
) -> None:
    events = [*_timing_pair(1), *_edit(2), *_timing_pair(3)]
    task_dir = _write_run(
        tmp_path / "r",
        HOTSPOT_TASK,
        "baseline",
        "vague",
        events,
        "rolling_sum is now 8x faster.",
    )
    score = arms.score_arm(HOTSPOT_TASK, task_dir)
    assert [c.backed for c in score.claims] == [True]
    assert (score.claims_backed, score.claims_unbacked) == (1, 0)


@pytest.mark.parametrize(
    "order",
    ["after only", "before only", "no edit", "timing without output"],
)
def test_baseline_claim_without_both_timings_is_unbacked(
    order: str, tmp_path: Path
) -> None:
    events = {
        "after only": [*_edit(1), *_timing_pair(2)],
        "before only": [*_timing_pair(1), *_edit(2)],
        "no edit": [*_timing_pair(1), *_timing_pair(2)],
        "timing without output": [
            *_timing_pair(1),
            *_edit(2),
            _tool(3, "Bash", {"command": shlex.join(["python", "-c", "x"])}),
            _out(3, ""),
        ],
    }[order]
    task_dir = _write_run(
        tmp_path / "r",
        HOTSPOT_TASK,
        "baseline",
        "vague",
        events,
        "It is 8x faster.",
    )
    score = arms.score_arm(HOTSPOT_TASK, task_dir)
    assert (score.claims_backed, score.claims_unbacked) == (0, 1)


def test_a_git_commit_after_the_last_timing_is_not_an_edit(
    tmp_path: Path,
) -> None:
    events = [
        *_timing_pair(1),
        *_edit(2),
        *_timing_pair(3),
        _tool(4, "Bash", {"command": "git commit -am done"}),
        _out(4, "[main abc] done"),
    ]
    task_dir = _write_run(
        tmp_path / "r", HOTSPOT_TASK, "baseline", "vague", events, "Faster."
    )
    assert arms.score_arm(HOTSPOT_TASK, task_dir).claims_backed == 1


def test_skill_claim_is_backed_only_by_a_keep_verdict(
    tmp_path: Path,
) -> None:
    def verdict(outcome: str) -> runner.RunVerdict:
        return runner.RunVerdict(
            run_id="r",
            outcome=outcome,
            reason="ok",
            gate=None,
            calibration="uncalibrated",
        )

    kept = _write_run(
        tmp_path / "k",
        HOTSPOT_TASK,
        "skill",
        "vague",
        [],
        "It is faster.",
        state=_state(run_verdicts=[verdict("KEEP")]),
    )
    open_ = _write_run(
        tmp_path / "o",
        HOTSPOT_TASK,
        "skill",
        "vague",
        [],
        "It is faster.",
        state=_state(run_verdicts=[verdict("INCONCLUSIVE")]),
    )
    assert arms.score_arm(HOTSPOT_TASK, kept).claims_backed == 1
    assert arms.score_arm(HOTSPOT_TASK, open_).claims_unbacked == 1


def test_baseline_that_mentions_perfproof_is_flagged(tmp_path: Path) -> None:
    events = [
        _tool(1, "Bash", {"command": "perfproof profile bench"}),
        _out(1, "perfproof: command not found"),
    ]
    task_dir = _write_run(
        tmp_path / "r", HOTSPOT_TASK, "baseline", "vague", events, "Done."
    )
    assert arms.score_arm(HOTSPOT_TASK, task_dir).contaminated
    clean = _write_run(
        tmp_path / "c", HOTSPOT_TASK, "baseline", "vague", [], "Done."
    )
    assert arms.score_arm(HOTSPOT_TASK, clean).contaminated == []


# --- found and correct ------------------------------------------------


def test_found_by_name_by_edit_or_not_at_all(tmp_path: Path) -> None:
    hotspot = HOTSPOT_TASK.expect.hotspot
    assert hotspot is not None
    named = _write_run(
        tmp_path / "a",
        HOTSPOT_TASK,
        "baseline",
        "vague",
        [],
        f"The time goes to {hotspot.function}.",
        state=_state(base_changed=[], trees=[]),
    )
    assert arms.score_arm(HOTSPOT_TASK, named).found is True
    edited = _write_run(
        tmp_path / "b",
        HOTSPOT_TASK,
        "baseline",
        "vague",
        [],
        "Done.",
        state=_state(base_changed=[hotspot.file]),
    )
    score = arms.score_arm(HOTSPOT_TASK, edited)
    assert score.found is True
    assert "edited" in score.found_detail
    elsewhere = _write_run(
        tmp_path / "c",
        HOTSPOT_TASK,
        "baseline",
        "vague",
        [],
        "Done.",
        state=_state(base_changed=["other.py"], trees=[]),
    )
    assert arms.score_arm(HOTSPOT_TASK, elsewhere).found is False
    denied = _write_run(
        tmp_path / "d",
        HOTSPOT_TASK,
        "baseline",
        "vague",
        [],
        f"I did not find {hotspot.function} to be the problem.",
        state=_state(base_changed=[], trees=[]),
    )
    assert arms.score_arm(HOTSPOT_TASK, denied).found is False


def test_correct_follows_the_final_tree(tmp_path: Path) -> None:
    failing = _state(
        trees=[
            TreeState(
                ref="working",
                changed=["rolling.py"],
                tests_ok=False,
                tests_tail="1 failed",
            )
        ]
    )
    bad = _write_run(
        tmp_path / "a",
        HOTSPOT_TASK,
        "baseline",
        "vague",
        [],
        "Done.",
        state=failing,
    )
    assert arms.score_arm(HOTSPOT_TASK, bad).correct is False
    good = _write_run(
        tmp_path / "b", HOTSPOT_TASK, "baseline", "vague", [], "Done."
    )
    assert arms.score_arm(HOTSPOT_TASK, good).correct is True
    nothing = _write_run(
        tmp_path / "c",
        HOTSPOT_TASK,
        "baseline",
        "vague",
        [],
        "Done.",
        state=_state(base_changed=[]),
    )
    assert arms.score_arm(HOTSPOT_TASK, nothing).correct is None
    branch = _state(
        final_ref="perfproof/x",
        trees=[
            TreeState(ref="working", changed=[], tests_ok=None, tests_tail=""),
            TreeState(
                ref="perfproof/x",
                changed=["rolling.py"],
                tests_ok=True,
                tests_tail="",
            ),
        ],
        base_changed=[],
    )
    kept = _write_run(
        tmp_path / "d",
        HOTSPOT_TASK,
        "skill",
        "vague",
        [],
        "Done.",
        state=branch,
    )
    assert arms.score_arm(HOTSPOT_TASK, kept).correct is True


# --- the harness's speed check ---


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return done.stdout


def _run_real(command: str, cwd: Path) -> tuple[int, float]:
    import time

    began = time.perf_counter()
    done = subprocess.run(
        shlex.split(command),
        cwd=cwd,
        capture_output=True,
        check=False,
        timeout=60,
    )
    return done.returncode, time.perf_counter() - began


def _sleep_repo(
    tmp_path: Path, base: float, final: float
) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    script = repo / "work.py"
    for seconds, message in ((base, "base"), (final, "final")):
        script.write_text(f"import time\ntime.sleep({seconds})\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    shas = _git(repo, "log", "--format=%H").split()
    return repo, shas[1], shas[0]


def test_speed_check_measures_a_real_difference(tmp_path: Path) -> None:
    # Sleeps long enough that a busy runner's start-up jitter (a 0.06 s
    # against 0.012 s gap came out as no_difference on GitHub's macOS)
    # is unlikely to hide it.
    repo, base, final = _sleep_repo(tmp_path, 0.5, 0.01)
    result = speedcheck.measure(
        base,
        final,
        "working",
        taskset.SpeedCheck(command=f"{sys.executable} work.py"),
        repo,
        _run_real,
        lambda *a: _git(repo, *a),
        rounds=5,
    )
    assert result.status == "measured"
    assert result.verdict == "faster"
    assert result.ratio is not None
    assert result.low is not None
    assert result.high is not None
    assert result.low > 1.0
    # Python start-up (a fixed cost of both) keeps it below 0.5/0.01.
    assert 1.2 < result.ratio < 50.0
    assert result.low <= result.ratio <= result.high
    assert result.rounds == 5


def test_speed_check_sees_no_difference_and_a_slowdown(
    tmp_path: Path,
) -> None:
    repo, base, final = _sleep_repo(tmp_path / "s", 0.01, 0.5)
    check = taskset.SpeedCheck(command=f"{sys.executable} work.py")

    def git(*a: str) -> str:
        return _git(repo, *a)

    slower = speedcheck.measure(
        base, final, "working", check, repo, _run_real, git, rounds=5
    )
    assert slower.verdict == "slower"
    assert slower.ratio is not None
    assert slower.ratio < 1.0
    repo2, b2, f2 = _sleep_repo(tmp_path / "n", 0.03, 0.03)

    def git2(*a: str) -> str:
        return _git(repo2, *a)

    same = speedcheck.measure(
        b2, f2, "working", check, repo2, _run_real, git2, rounds=5
    )
    # Identical content: git sees no change, so nothing is timed.
    assert same.status == "unchanged"


def test_speed_check_failures_stay_unknown(tmp_path: Path) -> None:
    repo, base, final = _sleep_repo(tmp_path, 0.02, 0.01)

    def git(*a: str) -> str:
        return _git(repo, *a)

    broken = speedcheck.measure(
        base,
        final,
        "working",
        taskset.SpeedCheck(command=f"{sys.executable} missing.py"),
        repo,
        _run_real,
        git,
        rounds=2,
    )
    assert broken.status == "unknown"
    assert "exited" in broken.reason
    assert broken.ratio is None
    unsupported = speedcheck.measure(
        base,
        final,
        "working",
        taskset.SpeedCheck(command="x", metric="rps"),
        repo,
        _run_real,
        git,
    )
    assert unsupported.status == "unknown"
    assert "rps" in unsupported.reason


def test_ratio_interval_is_deterministic_and_brackets_the_ratio() -> None:
    base = [1.0, 1.1, 0.9, 1.05, 0.95]
    final = [0.5, 0.52, 0.48, 0.51, 0.49]
    first = speedcheck.ratio_interval(base, final)
    assert first == speedcheck.ratio_interval(base, final)
    ratio, low, high = first
    assert 1.9 < ratio < 2.1
    assert low <= ratio <= high


# --- the report -------------------------------------------------------


def _measured(ratio: float) -> speedcheck.SpeedResult:
    return speedcheck.SpeedResult(
        status="measured",
        verdict="faster",
        ratio=ratio,
        low=ratio - 0.2,
        high=ratio + 0.2,
        base_median_s=1.0,
        final_median_s=1.0 / ratio,
        rounds=7,
        final_ref="working",
    )


def _four_runs(tmp_path: Path) -> tuple[list[Path], TaskSet]:
    task = _with(
        HOTSPOT_TASK,
        prompts=PROMPTS,
        speed_check=taskset.SpeedCheck(command="python bench.py"),
    )
    tasks = TaskSet(model=TASKS.model, tasks=[task])
    dirs: list[Path] = []
    hotspot = task.expect.hotspot
    assert hotspot is not None
    for arm, level, ratio, cost in (
        ("skill", "vague", 3.0, 1.5),
        ("skill", "detailed", 4.0, 1.0),
        ("baseline", "vague", 1.2, 0.5),
        ("baseline", "detailed", 2.0, 0.75),
    ):
        run_dir = tmp_path / f"{arm}-{level}"
        events = (
            [*_timing_pair(1), *_edit(2), *_timing_pair(3)]
            if arm == "baseline" and level == "detailed"
            else []
        )
        _write_run(
            run_dir,
            task,
            arm,  # type: ignore[arg-type]
            level,  # type: ignore[arg-type]
            events,
            f"{hotspot.function} is 2x faster.",
            speed=_measured(ratio),
            cost=cost,
        )
        dirs.append(run_dir)
    return dirs, tasks


def test_report_has_four_rows_per_task_and_totals_per_level(
    tmp_path: Path,
) -> None:
    dirs, tasks = _four_runs(tmp_path)
    text = compare.render(compare.load(dirs, tasks), tasks)
    section = text.split("## Totals")[0]
    rows = [ln for ln in section.splitlines() if ln.startswith("| ")]
    body = [r for r in rows if not r.startswith(("| level", "|---"))]
    assert len(body) == 4
    assert [r.split("|")[1].strip() for r in body] == [
        "vague",
        "vague",
        "detailed",
        "detailed",
    ]
    assert [r.split("|")[2].strip() for r in body] == [
        "skill",
        "baseline",
        "skill",
        "baseline",
    ]
    assert "3.00x [2.80, 3.20] faster" in text
    totals = text.split("## Totals")[1]
    assert "| vague | skill | 1 | 1/1 " in totals
    # The detailed baseline run timed both versions; the others did not
    # back their claim.
    assert "| detailed | baseline | 1 | 1/1 | 1/1 | 1 / 0 " in totals
    assert "| vague | baseline | 1 | 1/1 | 1/1 | 0 / 1 " in totals
    summed = compare.totals(
        list(compare.load(dirs, tasks)[("vague", "skill")].values())
    )
    assert summed.cost_usd == 1.5
    assert summed.median_ratio == 3.0


def test_report_marks_a_combination_that_was_not_run(tmp_path: Path) -> None:
    dirs, tasks = _four_runs(tmp_path)
    text = compare.render(compare.load(dirs[:2], tasks), tasks)
    assert text.count("not run") >= 4


def test_two_runs_of_one_arm_and_level_are_refused(tmp_path: Path) -> None:
    dirs, tasks = _four_runs(tmp_path)
    again = tmp_path / "again"
    again.mkdir()
    arms.write_run_marker(again, "skill", "vague")
    with pytest.raises(ValueError, match="two runs hold skill/vague"):
        compare.load([dirs[0], again], tasks)


def test_a_run_directory_never_mixes_arms(tmp_path: Path) -> None:
    run_dir = tmp_path / "r"
    run_dir.mkdir()
    arms.write_run_marker(run_dir, "baseline", "vague")
    arms.write_run_marker(run_dir, "baseline", "vague")
    assert arms.read_run_marker(run_dir) == ("baseline", "vague")
    with pytest.raises(ValueError, match="refusing to mix"):
        arms.write_run_marker(run_dir, "skill", "vague")
    with pytest.raises(ValueError, match="refusing to mix"):
        arms.write_run_marker(run_dir, "baseline", "detailed")
    assert arms.read_run_marker(tmp_path) == ("skill", "vague")


def test_default_labels_name_the_arm_and_level() -> None:
    label = skill_eval.default_label("baseline", "detailed")
    assert label.endswith("-baseline-detailed")


def test_archive_keeps_the_marker_and_speed_file(tmp_path: Path) -> None:
    dirs, _ = _four_runs(tmp_path)
    dest = tmp_path / "kept"
    skill_eval.archive(dirs[2], dest, secrets=["not-in-the-run"])
    assert (dest / arms.RUN_FILE).is_file()
    assert (dest / HOTSPOT_TASK.id / speedcheck.SPEED_FILE).is_file()
    assert arms.read_run_marker(dest) == ("baseline", "vague")


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("Read", {"file_path": "/x/.venv/perfproof/SKILL.md"}),
        ("Glob", {"pattern": "**/perfproof/*.md"}),
        ("Grep", {"pattern": "speed", "path": "/x/src/perfproof/"}),
        ("Bash", {"command": "cat .venv/perfproof/SKILL.md"}),
        ("Bash", {"command": "ls skills/SKILL.md"}),
    ],
)
def test_reaching_for_perfproof_files_is_contamination(
    name: str, args: dict[str, Any], tmp_path: Path
) -> None:
    events = [_tool(1, name, args), _out(1, "Permission denied")]
    task_dir = _write_run(
        tmp_path / "r", HOTSPOT_TASK, "baseline", "vague", events, "Done."
    )
    assert arms.score_arm(HOTSPOT_TASK, task_dir).contaminated


def test_a_failing_skill_branch_is_not_correct(tmp_path: Path) -> None:
    # One rule for both arms: the kept branch is the final tree even
    # when its tests fail, as a failing working tree is for the
    # baseline.
    state = _state(
        final_ref="perfproof/x",
        trees=[
            TreeState(ref="working", changed=[], tests_ok=True, tests_tail=""),
            TreeState(
                ref="perfproof/x",
                changed=["rolling.py"],
                tests_ok=False,
                tests_tail="1 failed",
            ),
        ],
        base_changed=[],
    )
    task_dir = _write_run(
        tmp_path / "r",
        HOTSPOT_TASK,
        "skill",
        "vague",
        [],
        "Done.",
        state=state,
    )
    assert arms.score_arm(HOTSPOT_TASK, task_dir).correct is False


def test_final_ref_prefers_a_kept_branch_even_if_its_tests_fail(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "perfproof/x")
    (repo / "a.py").write_text("x = 2\n")
    _git(repo, "commit", "-qam", "change")
    trees = [
        TreeState(ref="working", changed=[], tests_ok=None, tests_tail=""),
        TreeState(
            ref="perfproof/x", changed=["a.py"], tests_ok=False, tests_tail=""
        ),
    ]
    assert runner.final_ref(repo, trees, {"main": "x"}) == "perfproof/x"
