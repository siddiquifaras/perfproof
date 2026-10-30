"""Running the eval as a separate macOS user.

Unit tests cover command construction, the fail-closed choices and the
extraction filter. The ``integration`` tests use the real ``ppeval``
account and skip where it does not exist or ``sudo -n`` is not allowed.
"""

import contextlib
import io
import json
import os
import re
import signal
import subprocess
import sys
import tarfile
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

import eval_user
import realrepo
import runner
import sandbox
import skill_eval
import taskset

USER = eval_user.EvalUser("ppeval", 502, Path("/Users/ppeval"))


def _usable() -> bool:
    try:
        eval_user.require()
    except eval_user.EvalUserUnavailableError:
        return False
    return True


needs_user = pytest.mark.skipif(
    not _usable(), reason="needs the ppeval user and passwordless sudo to it"
)
integration = pytest.mark.integration


# command construction


def test_the_command_runs_as_the_user_with_a_scrubbed_environment() -> None:
    argv = eval_user.command(USER, ["echo", "hi there"], {"X": "1"})
    assert argv[:7] == [
        "sudo",
        "-n",
        "-u",
        "ppeval",
        "-H",
        "/usr/bin/env",
        "-i",
    ]
    assigned = {a.split("=", 1)[0] for a in argv[7:] if "=" in a[:30]}
    assert {"HOME", "USER", "LOGNAME", "X"} <= assigned
    # PATH is set by the launcher from the user's own toolchains, never
    # carried over from the owner (sudo would pass it through).
    assert "PATH" not in assigned
    assert argv[-3:] == ["sh", "echo", "hi there"]


def test_the_launcher_sets_path_inside_the_users_shell() -> None:
    script = eval_user._LAUNCHER  # pyright: ignore[reportPrivateUsage]
    assert "$HOME/.local/bin" in script
    assert "$HOME/.cargo/bin" in script
    assert "JAVA_HOME" in script
    assert 'exec "$@"' in script


def test_no_secret_reaches_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "sk-ant-oat01-" + "x" * 40
    token = tmp_path / "token"
    token.write_text(secret, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", secret)
    user = eval_user.EvalUser("ppeval", 502, tmp_path)
    for argv in (
        eval_user.command(
            user, eval_user.guest_argv(user, label="l", only=[])
        ),
        eval_user.command(user, ["x"], eval_user.guest_env(user)),
    ):
        assert secret not in " ".join(argv)
    # The environment names the file, not its contents.
    assert eval_user.guest_env(user)[sandbox.TOKEN_FILE_ENV] == str(
        user.token_file
    )


def test_the_held_out_flag_reaches_the_guest_only_when_given() -> None:
    plain = [str(a) for a in eval_user.guest_argv(USER, label="r", only=[])]
    held = [
        str(a)
        for a in eval_user.guest_argv(USER, label="r", only=[], held_out=True)
    ]
    assert "--held-out" not in plain
    assert "--held-out" in held


def test_the_guest_runs_from_the_users_home_only() -> None:
    argv = eval_user.guest_argv(USER, label="r1", only=["a", "b"])
    paths = [a for a in argv if isinstance(a, Path)]
    assert paths
    assert all(USER.home in p.parents for p in paths)
    text = [str(a) for a in argv]
    assert "--no-eval-user" in text
    assert text[text.index("--only") + 1 :] == ["a", "b"]
    root = str(runner.REPO_ROOT)
    assert not any(a == root or a.startswith(root + "/") for a in text)
    assert text[0].endswith("/uv/tools/perfproof/bin/python")


def test_the_bundle_holds_the_harness_without_its_tests() -> None:
    files = eval_user.bundle_files(runner.REPO_ROOT, runner.SKILL_SOURCE)
    assert "eval/skill/skill_eval.py" in files
    assert "eval/skill/tasks-real.json" in files
    assert "eval/mine/checks.json" in files
    assert "eval/mine/heldout-discovery-draw.json" in files
    assert "eval/mine/heldout-discovery-2-draw.json" in files
    assert "examples/materialize.py" in files
    assert "skills/perfproof/SKILL.md" in files
    # Workloads and the examples' own tests are task content; the
    # harness's tests are not.
    assert not [n for n in files if n.startswith("eval/skill/test_")]
    assert not [n for n in files if "__pycache__" in n or "/runs/" in n]
    assert not [n for n in files if n.startswith((".claude", "scratch"))]


_LOAD_ALL = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import realrepo, taskset
out = {}
for path in sorted(Path(sys.argv[1]).glob("tasks*.json")):
    for task in taskset.load(path).tasks:
        if task.repo is None:
            continue
        try:
            spec = realrepo.load_spec(task.repo.manifest)
            out[task.id] = ["ok", spec.repository, spec.task_commit]
        except Exception as exc:  # noqa: BLE001
            out[task.id] = [type(exc).__name__, str(exc)[:200]]
print(json.dumps(out))
"""


def _load_every_task(root: Path) -> dict[str, list[str]]:
    skill = root / "eval" / "skill"
    done = subprocess.run(
        [sys.executable, "-I", "-c", _LOAD_ALL, str(skill)],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ["PATH"]},
        cwd=root,
        timeout=300,
    )
    assert done.returncode == 0, done.stderr[-800:]
    return json.loads(done.stdout)


def test_every_task_file_loads_from_the_guests_bundle(
    tmp_path: Path,
) -> None:
    files = eval_user.bundle_files(runner.REPO_ROOT, runner.SKILL_SOURCE)
    _extract_local(files, tmp_path / "guest")
    from_bundle = _load_every_task(tmp_path / "guest")
    from_checkout = _load_every_task(runner.REPO_ROOT)
    assert from_bundle
    assert not [
        v for v in from_bundle.values() if v[0] == "ModuleNotFoundError"
    ]
    # The guest decides exactly as the checkout does, including which
    # candidates the selection rule refuses.
    assert from_bundle == from_checkout


def test_remove_refuses_paths_outside_the_work_directory() -> None:
    with pytest.raises(RuntimeError, match="not under"):
        eval_user.remove(USER, Path("/Users/faras"))
    with pytest.raises(RuntimeError, match="not under"):
        eval_user.remove(USER, USER.home / ".config")


# fail closed


def test_a_missing_user_is_an_error_that_names_the_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def gone(_: str) -> Any:
        raise KeyError

    monkeypatch.setattr(eval_user.pwd, "getpwnam", gone)
    with pytest.raises(eval_user.EvalUserUnavailableError) as raised:
        eval_user.lookup()
    assert "--no-eval-user" in str(raised.value)


def test_running_as_the_eval_user_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "getuid", lambda: 502)
    entry: Any = type("E", (), {"pw_uid": 502, "pw_dir": "/Users/ppeval"})

    def found(_: str) -> Any:
        return entry

    monkeypatch.setattr(eval_user.pwd, "getpwnam", found)
    with pytest.raises(eval_user.EvalUserUnavailableError, match="no separ"):
        eval_user.lookup()


@pytest.mark.parametrize(
    ("code", "out"), [(1, b""), (0, b"501\n"), (0, b"")], ids=str
)
def test_a_sudo_that_does_not_run_as_the_user_is_unusable(
    monkeypatch: pytest.MonkeyPatch, code: int, out: bytes
) -> None:
    def fake(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess([], code, out, b"a password")

    monkeypatch.setattr(eval_user, "run", fake)
    with pytest.raises(eval_user.EvalUserUnavailableError, match="sudo"):
        eval_user.check(USER)


def test_no_flag_and_no_user_never_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def gone(_: str = "") -> Any:
        raise eval_user.EvalUserUnavailableError(
            "no such user; --no-eval-user"
        )

    ran: list[str] = []
    monkeypatch.setattr(eval_user, "lookup", gone)

    def run_task(*_: Any, **__: Any) -> None:
        ran.append("x")

    monkeypatch.setattr(runner, "run_task", run_task)
    code = skill_eval.main(
        ["run", "--only", "prove-rolling-fix", "--out", str(tmp_path)]
    )
    assert code == 2
    assert ran == []
    assert "--no-eval-user" in capsys.readouterr().err


def test_the_flag_runs_here_and_does_not_look_for_the_user(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def forbidden(*_: Any, **__: Any) -> Any:
        raise AssertionError("looked for the eval user")

    monkeypatch.setattr(eval_user, "lookup", forbidden)
    monkeypatch.setattr(eval_user, "require", forbidden)
    seen: list[bool] = []

    def here(args: Any) -> int:
        seen.append(bool(args.no_eval_user))
        return 0

    monkeypatch.setattr(skill_eval, "_run_here", here)
    assert skill_eval.main(["run", "--no-eval-user", "--out", "x"]) == 0
    assert seen == [True]


def test_work_root_and_cache_belong_to_the_fallback_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    def usable(*_: Any) -> eval_user.EvalUser:
        return USER

    monkeypatch.setattr(eval_user, "require", usable)
    for flag in ("--work-root", "--cache"):
        code = skill_eval.main(["run", flag, str(tmp_path), "--out", "x"])
        assert code == 2


# the token


def test_the_token_is_read_from_the_file_inside_the_process(
    tmp_path: Path,
) -> None:
    file = tmp_path / "token"
    file.write_text("tok-123\n", encoding="utf-8")
    env = {sandbox.TOKEN_FILE_ENV: str(file)}
    assert sandbox.claude_oauth_token(env) == "tok-123"


@pytest.mark.parametrize("content", [None, "", "  \n"], ids=repr)
def test_a_missing_or_empty_token_file_stops_the_run(
    tmp_path: Path, content: str | None
) -> None:
    file = tmp_path / "token"
    if content is not None:
        file.write_text(content, encoding="utf-8")
    with pytest.raises(sandbox.SandboxUnavailableError, match="token file"):
        sandbox.claude_oauth_token({sandbox.TOKEN_FILE_ENV: str(file)})


def test_the_token_file_variable_is_not_passed_to_agents(
    tmp_path: Path,
) -> None:
    env = sandbox.agent_env(
        {"PATH": "/usr/bin", sandbox.TOKEN_FILE_ENV: "/x/token"},
        tmp_path / "s",
        [],
    )
    assert sandbox.TOKEN_FILE_ENV not in env
    assert "PERFPROOF_EVAL_VENV" not in env


def test_the_venv_can_be_the_installed_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(runner.VENV_ENV, raising=False)
    assert runner.venv_dir() == runner.REPO_ROOT / ".venv"
    monkeypatch.setenv(runner.VENV_ENV, "/x/tools/perfproof")
    assert runner.venv_dir() == Path("/x/tools/perfproof")


# the profile, adapted to the eval user's paths


def test_the_profile_for_the_eval_user_keeps_every_closing_rule() -> None:
    text = sandbox.profile(
        USER.home,
        USER.bundle,
        False,
        read=[USER.work / "work" / "r" / "repos" / "t"],
    )
    assert f'(deny file-read* (subpath "{USER.home}"))' in text
    assert "(deny mach-lookup)\n" in text
    assert "(deny network-outbound (remote unix-socket))" in text
    assert "(deny process-info* (target others))" in text
    assert "(deny mach-priv-task-port (target others))" in text
    assert "(deny file-write*)" in text
    for allowed in (".local/share/uv", ".local/bin", ".nvm", ".rustup"):
        assert f'(allow file-read* (subpath "{USER.home}/{allowed}"))' in text
    assert '(allow file-read* (subpath "/Users/faras' not in text
    token_dir = USER.token_file.parent
    assert str(token_dir) not in text


# results are data


def _tar(*members: tuple[str, bytes | None, bytes]) -> io.BytesIO:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, link, data in members:
            info = tarfile.TarInfo(name)
            if link is not None:
                info.type = tarfile.SYMTYPE
                info.linkname = link.decode()
            else:
                info.size = len(data)
            tar.addfile(info, None if link is not None else io.BytesIO(data))
    buffer.seek(0)
    return buffer


def test_plain_files_are_extracted(tmp_path: Path) -> None:
    stream = _tar(("a/b.json", None, b"{}"), ("c.md", None, b"x"))
    assert eval_user.extract_safely(stream, tmp_path / "out") == 2
    assert (tmp_path / "out" / "a" / "b.json").read_text() == "{}"


@pytest.mark.parametrize(
    "member",
    [
        ("lnk", b"/Users/faras/.ssh", b""),
        ("../escape", None, b"x"),
        ("/abs/path", None, b"x"),
        ("a/../../escape", None, b"x"),
    ],
    ids=["symlink", "dotdot", "absolute", "nested-dotdot"],
)
def test_anything_but_plain_files_is_refused(
    tmp_path: Path, member: tuple[str, bytes | None, bytes]
) -> None:
    with pytest.raises(eval_user.PullRefusedError):
        eval_user.extract_safely(_tar(member), tmp_path / "out")
    assert not (tmp_path / "escape").exists()


def test_a_flood_is_refused(tmp_path: Path) -> None:
    stream = _tar(("big", None, b"x" * 100))
    with pytest.raises(eval_user.PullRefusedError, match="more than"):
        eval_user.extract_safely(stream, tmp_path / "out", limit=50)


def test_a_pulled_run_is_archived_without_the_owners_scan(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "scores.json").write_text("{}", encoding="utf-8")
    staged = eval_user.Staged("w", "a", "b", "p")
    eval_user.write_marker(run, USER, staged, {})
    skill_eval.archive(run, tmp_path / "kept", scanned_by_eval_user=True)
    assert (tmp_path / "kept" / "scores.json").exists()


def test_the_marker_records_provenance_and_no_secret(tmp_path: Path) -> None:
    staged = eval_user.Staged("w.whl", "a" * 64, "b" * 64, "/p/perfproof")
    eval_user.write_marker(tmp_path, USER, staged, {"guest_exit_codes": [0]})
    record = json.loads((tmp_path / eval_user.MARKER).read_text())
    assert record["user"] == "ppeval"
    assert record["guest_exit_codes"] == [0]
    assert record["wheel_sha256"] == "a" * 64


# the real account (integration)

_GUEST = r"""
import json, os, subprocess, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import sandbox
base = Path(sys.argv[2])
task = base / "task"
task.mkdir(parents=True, exist_ok=True)
scratch = base / "sandbox"
env = sandbox.agent_env(
    {"PATH": os.environ["PATH"], "HOME": str(Path.home())}, scratch, []
)
argv = json.loads(sys.argv[3])
if sys.argv[4] == "profile":
    argv = sandbox.wrap(
        argv, scratch, checkout=Path(sys.argv[1]), allow_docker=False,
        cwd=task, read=[scratch], write=[scratch / "private"],
    )
done = subprocess.run(
    argv, cwd=task, env=env, capture_output=True, text=True, timeout=60,
    stdin=subprocess.DEVNULL, start_new_session=True, check=False,
)
print(json.dumps({"rc": done.returncode, "out": done.stdout + done.stderr}))
"""


@pytest.fixture(scope="module")
def guest() -> Any:
    user = eval_user.require()
    root = user.work / "itest"
    buffer = io.BytesIO()
    source = Path(sandbox.__file__)
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tar.gettarinfo(str(source), arcname="bundle/sandbox.py")
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        with source.open("rb") as handle:
            tar.addfile(info, handle)
    done = eval_user.run(
        user,
        ["/bin/sh", "-c", eval_user._EXTRACT, "sh", root],  # pyright: ignore[reportPrivateUsage]
        stdin=buffer.getvalue(),
    )
    assert done.returncode == 0, done.stderr

    def call(argv: list[str], mode: str) -> tuple[int, str]:
        ran = eval_user.run(
            user,
            [
                user.home / ".local" / "bin" / "python3.12",
                "-c",
                _GUEST,
                root / "bundle",
                root,
                json.dumps(argv),
                mode,
            ],
            timeout=120,
        )
        assert ran.returncode == 0, ran.stderr
        data = json.loads(ran.stdout)
        return data["rc"], data["out"]

    return call


@integration
@needs_user
def test_the_eval_user_has_the_build_tools_the_tasks_ask_for() -> None:
    # real-arrow-known-change needs cmake and ninja; the eval-user setup
    # notes say how they were installed (uv tool, no brew).
    user = eval_user.require()
    for tool in ("cmake", "ninja"):
        done = eval_user.run(user, ["/bin/sh", "-c", f"command -v {tool}"])
        found = done.stdout.decode().strip()
        assert found == f"{user.home}/.local/bin/{tool}", (tool, found)


@integration
@needs_user
def test_the_guest_is_the_eval_user_with_no_owner_environment() -> None:
    user = eval_user.require()
    done = eval_user.run(user, ["/usr/bin/id", "-un"])
    assert done.stdout.strip() == b"ppeval"
    env = eval_user.run(user, ["/usr/bin/env"]).stdout.decode()
    assert str(Path.home()) not in env
    assert "SSH_AUTH_SOCK" not in env
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env


@integration
@needs_user
@pytest.mark.parametrize("mode", ["bare", "profile"])
@pytest.mark.parametrize(
    "argv",
    [
        ["ls", str(Path.home())],
        ["cat", str(Path.home() / ".ssh" / "id_ed25519.pub")],
        ["ls", str(runner.REPO_ROOT)],
        ["touch", str(Path.home() / ".t320-itest")],
    ],
    ids=["home", "ssh", "checkout", "write-home"],
)
def test_the_owners_files_are_closed(
    guest: Any, mode: str, argv: list[str]
) -> None:
    code, _ = guest(argv, mode)
    assert code != 0
    assert not (Path.home() / ".t320-itest").exists()


@integration
@needs_user
def test_the_profile_closes_what_the_user_boundary_leaves_open(
    guest: Any,
) -> None:
    # sudo -u stays in the owner's GUI session: bare, the eval user's
    # Finder call is answered (with the owner's home).
    script = 'tell application "Finder" to count items of home'
    bare, _ = guest(["osascript", "-e", script], "bare")
    closed, _ = guest(["osascript", "-e", script], "profile")
    assert bare == 0
    assert closed != 0
    opened, _ = guest(["open", "-g", "-a", "Finder"], "profile")
    assert opened != 0


@integration
@needs_user
def test_interrupting_the_host_stops_the_guest_process() -> None:
    user = eval_user.require()
    marker = "sleep 4321"
    seen: list[int] = []

    def started() -> bool:
        listed = eval_user.run(user, ["/usr/bin/pgrep", "-f", marker])
        return listed.returncode == 0

    def interrupt() -> None:
        for _ in range(100):
            if started():
                break
            time.sleep(0.1)
        seen.append(1 if started() else 0)
        os.kill(os.getpid(), signal.SIGINT)

    thread = threading.Thread(target=interrupt)
    thread.start()
    with pytest.raises(KeyboardInterrupt):
        eval_user.run_guest(user, ["/bin/sleep", "4321"])
    thread.join()
    assert seen == [1]
    time.sleep(1)
    assert not started()


def test_remove_refuses_dotdot_and_labels_are_plain_names() -> None:
    with pytest.raises(RuntimeError, match="not under"):
        eval_user.remove(USER, USER.work / "runs" / ".." / ".." / ".config")
    for bad in ("../../.config", "a/b", ".hidden", "", "a..b"):
        with pytest.raises(ValueError, match="run label"):
            eval_user.check_label(bad)
    assert eval_user.check_label("real-1.a_b") == "real-1.a_b"


def test_a_resumed_run_keeps_every_staging(tmp_path: Path) -> None:
    for sha in ("1", "2"):
        staged = eval_user.Staged("w", sha, "b", "p")
        eval_user.write_marker(tmp_path, USER, staged, {})
    record = json.loads((tmp_path / eval_user.MARKER).read_text())
    assert record["wheel_sha256"] == "2"
    assert [s["wheel_sha256"] for s in record["stagings"]] == ["1"]


def _local_pull(monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    def fake(*_: Any, **__: Any) -> list[str]:
        return ["/bin/sh", "-c", script]

    monkeypatch.setattr(eval_user, "command", fake)


def test_pull_of_a_missing_directory_is_a_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _local_pull(monkeypatch, "tar -c -C /nonexistent-t320 . 2>&2")
    with pytest.raises(eval_user.PullRefusedError):
        eval_user.pull(USER, USER.work, tmp_path / "out")


def test_pull_does_not_block_on_a_noisy_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("x", encoding="utf-8")
    _local_pull(
        monkeypatch,
        f"yes err | head -c 300000 >&2; tar -c -C {src} .",
    )
    assert eval_user.pull(USER, USER.work, tmp_path / "out") >= 1
    assert (tmp_path / "out" / "a.txt").read_text() == "x"


def _nothing(*_: Any, **__: Any) -> None:
    return None


def test_the_pause_marker_is_honoured_before_staging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    order: list[str] = []

    def waited(marker: Path | None, *_: Any) -> float:
        order.append("wait")
        return 0.0

    def staged(*_: Any, **__: Any) -> Any:
        order.append("stage")
        raise eval_user.EvalUserUnavailableError("stop here")

    def usable(*_: Any) -> eval_user.EvalUser:
        return USER

    monkeypatch.setattr(eval_user, "require", usable)
    monkeypatch.setattr(eval_user, "sweep", _nothing)
    monkeypatch.setattr(eval_user, "exclusive", contextlib.nullcontext)
    monkeypatch.setattr(skill_eval, "wait_while_marked", waited)
    monkeypatch.setattr(eval_user, "stage", staged)
    skill_eval.main(
        ["run", "--out", str(tmp_path), "--pause-marker", str(tmp_path / "m")]
    )
    assert order == ["wait", "stage"]


_HOSTILE = """#!/bin/sh
touch "$BUNDLE/planted" 2>/dev/null
echo "bundle=$?" > result.txt
osascript -e 'tell application "Finder" to count items of home' \\
    >> result.txt 2>&1
echo "finder=$?" >> result.txt
exit 0
"""


@pytest.mark.skipif(
    not sandbox.confinement_available(), reason="needs macOS sandbox-exec"
)
def test_a_hostile_setup_cannot_write_outside_its_tree_or_reach_the_gui(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    repo = tmp_path / "run" / "repos" / "t"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "evil.sh").write_text(_HOSTILE, encoding="utf-8")
    spec = realrepo.RepoSpec(
        repository="o/r",
        task_commit="a",
        reference_commit="b",
        reference_number="",
        reference_title="",
        setup=["sh evil.sh"],
        env={"BUNDLE": str(bundle)},
        oracle=None,
    )
    realrepo.run_setup(spec, repo, [], confine=True)
    result = (repo / "result.txt").read_text()
    assert not (bundle / "planted").exists()
    assert "bundle=1" in result
    assert "finder=0" not in result
    # The same script, unconfined, does write the bundle (the control).
    (repo / "result.txt").unlink()
    realrepo.run_setup(spec, repo, [], confine=False)
    assert (bundle / "planted").exists()


@pytest.mark.skipif(
    not sandbox.confinement_available(), reason="needs macOS sandbox-exec"
)
def test_a_confined_setup_can_run_the_launchers_on_its_path(
    tmp_path: Path,
) -> None:
    launchers = tmp_path / "run" / "bin"
    launchers.mkdir(parents=True)
    tool = launchers / "mytool"
    tool.write_text("#!/bin/sh\necho launched > launched.txt\n")
    tool.chmod(0o755)
    repo = tmp_path / "run" / "repos" / "t"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    spec = realrepo.RepoSpec(
        repository="o/r",
        task_commit="a",
        reference_commit="b",
        reference_number="",
        reference_title="",
        setup=["mytool"],
        env={},
        oracle=None,
    )
    realrepo.run_setup(spec, repo, [launchers], confine=True)
    assert (repo / "launched.txt").read_text() == "launched\n"


@pytest.mark.skipif(
    not sandbox.confinement_available(), reason="needs macOS sandbox-exec"
)
def test_the_confined_oracle_can_run_the_launchers_on_its_path(
    tmp_path: Path,
) -> None:
    launchers = tmp_path / "run" / "bin"
    launchers.mkdir(parents=True)
    tool = launchers / "mytool"
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o755)
    repo = tmp_path / "run" / "repos" / "t"
    repo.mkdir(parents=True)
    spec = realrepo.RepoSpec(
        repository="o/r",
        task_commit="a",
        reference_commit="b",
        reference_number="",
        reference_title="",
        setup=[],
        env={},
        oracle="mytool",
    )
    ok, tail = realrepo.run_oracle(spec, repo, [launchers])
    assert ok, tail


def test_docker_tasks_are_skipped_with_a_reason_for_the_eval_user(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(sandbox.TOKEN_FILE_ENV, "/x/token")
    tasks = taskset.load(runner.REPO_ROOT / "eval/skill/tasks-real.json")
    # No task file names a launcher any more (the Go, Ruby and .NET
    # tasks run natively); a task that still did would be skipped.
    base = next(t for t in tasks.tasks if t.repo)
    assert base.repo is not None
    task = base.model_copy(
        update={"repo": base.repo.model_copy(update={"shim": "go"})}
    )
    with pytest.raises(realrepo.SetupError) as raised:
        runner.run_task(
            task, tasks, tmp_path / "r", runner.SKILL_SOURCE, tmp_path
        )
    assert str(raised.value) == (
        "skipped: needs docker, not available to ppeval"
    )


@integration
@needs_user
def test_a_changed_bundle_is_detected(tmp_path: Path) -> None:
    user = eval_user.require()
    files = _files(tmp_path, {"a.txt": b"one", "d/b.txt": b"two"})
    done = eval_user.run(
        user,
        ["/bin/sh", "-c", eval_user._EXTRACT, "sh", user.bundle],  # pyright: ignore[reportPrivateUsage]
        stdin=eval_user._tar_bytes(files),  # pyright: ignore[reportPrivateUsage]
    )
    assert done.returncode == 0, done.stderr
    staged = eval_user.Staged("w", "s", _host_digest(files), "p")
    eval_user.verify_bundle(user, staged)
    eval_user.run(user, ["/usr/bin/touch", user.bundle / "extra.pyc"])
    with pytest.raises(eval_user.BundleTamperedError):
        eval_user.verify_bundle(user, staged)
    eval_user.remove(user, user.bundle)


def _extract_local(files: dict[str, Path], dest: Path) -> None:
    """Extract the bundle archive the way the eval user's shell does."""
    dest.mkdir()
    subprocess.run(
        [
            "/bin/sh",
            "-c",
            'umask 022; exec tar -x -C "$1" -f -',
            "sh",
            str(dest),
        ],
        input=eval_user._tar_bytes(files),  # pyright: ignore[reportPrivateUsage]
        check=True,
    )


def _guest_digest(root: Path) -> str:
    done = subprocess.run(
        [sys.executable, "-B", "-I", "-c", eval_user._DIGEST, str(root)],  # pyright: ignore[reportPrivateUsage]
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


def _files(tmp_path: Path, content: dict[str, bytes]) -> dict[str, Path]:
    src = tmp_path / "src"
    found: dict[str, Path] = {}
    for name, data in content.items():
        path = src / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        found[name] = path
    return found


def _host_digest(files: dict[str, Path]) -> str:
    return eval_user._digest(files)  # pyright: ignore[reportPrivateUsage]


def test_the_guest_digest_equals_the_hosts(tmp_path: Path) -> None:
    files = _files(tmp_path, {"a.txt": b"one", "d/e/b.py": b"two"})
    files["d/e/b.py"].chmod(0o755)
    _extract_local(files, tmp_path / "tree")
    assert _guest_digest(tmp_path / "tree") == _host_digest(files)


def test_the_digest_sees_a_chmod(tmp_path: Path) -> None:
    files = _files(tmp_path, {"a.txt": b"one"})
    _extract_local(files, tmp_path / "tree")
    before = _guest_digest(tmp_path / "tree")
    (tmp_path / "tree" / "a.txt").chmod(0o666)
    assert _guest_digest(tmp_path / "tree") != before


def test_the_digest_sees_an_empty_directory(tmp_path: Path) -> None:
    files = _files(tmp_path, {"a.txt": b"one"})
    _extract_local(files, tmp_path / "tree")
    before = _guest_digest(tmp_path / "tree")
    (tmp_path / "tree" / "planted").mkdir()
    assert _guest_digest(tmp_path / "tree") != before


def test_the_digest_sees_a_link_to_a_directory(tmp_path: Path) -> None:
    files = _files(tmp_path, {"a.txt": b"one"})
    _extract_local(files, tmp_path / "tree")
    before = _guest_digest(tmp_path / "tree")
    (tmp_path / "tree" / "lnk").symlink_to("/Users")
    after = _guest_digest(tmp_path / "tree")
    assert after != before
    (tmp_path / "tree" / "lnk").unlink()
    (tmp_path / "tree" / "lnk").symlink_to("/private")
    assert _guest_digest(tmp_path / "tree") != after


def test_the_digest_does_not_confuse_a_name_with_content(
    tmp_path: Path,
) -> None:
    one = _files(tmp_path / "x", {"ab": b"c"})
    two = _files(tmp_path / "y", {"a": b"bc"})
    assert _host_digest(one) != _host_digest(two)
    _extract_local(one, tmp_path / "t1")
    _extract_local(two, tmp_path / "t2")
    assert _guest_digest(tmp_path / "t1") != _guest_digest(tmp_path / "t2")


def test_too_many_entries_are_refused(tmp_path: Path) -> None:
    stream = _tar(*[(f"f{i}", None, b"x") for i in range(5)])
    with pytest.raises(eval_user.PullRefusedError, match="entries"):
        eval_user.extract_safely(stream, tmp_path / "out", max_members=4)


def test_a_marker_file_is_not_trusted_to_skip_the_scan(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "t.json").write_text("leak " + "x" * 20, encoding="utf-8")
    staged = eval_user.Staged("w", "a", "b", "p")
    eval_user.write_marker(run, USER, staged, {})
    with pytest.raises(skill_eval.ArchiveRefusedError):
        skill_eval.archive(run, tmp_path / "k1", ["x" * 20])
    skill_eval.archive(
        run, tmp_path / "k2", ["x" * 20], scanned_by_eval_user=True
    )
    assert (tmp_path / "k2" / "t.json").exists()


_FIXTURES = Path(__file__).parent / "fixtures"


def _captured_result() -> dict[str, object]:
    """The ``result`` event of a real rejected-token call.

    Captured as the eval user, inside the profile, with a made-up token
    (see ``claude-rejected-token.version`` for the tool version).
    """
    lines = (_FIXTURES / "claude-rejected-token.jsonl").read_text()
    events = [json.loads(line) for line in lines.splitlines()]
    results = [e for e in events if e.get("type") == "result"]
    assert len(results) == 1
    return results[0]


_REJECTED = _captured_result()


def test_the_fixture_is_a_real_capture_with_its_version() -> None:
    version = (_FIXTURES / "claude-rejected-token.version").read_text()
    assert version.startswith("2.1.")
    text = (_FIXTURES / "claude-rejected-token.jsonl").read_text()
    assert not re.search(r"sk-ant-|Bearer ", text)
    assert _REJECTED["api_error_status"] == 401


def test_a_rejected_sign_in_is_not_a_task_failure() -> None:
    assert runner.auth_failed(_REJECTED)
    assert runner.auth_failed({**_REJECTED, "api_error_status": 403})
    assert not runner.auth_failed({**_REJECTED, "api_error_status": 500})
    assert not runner.auth_failed({**_REJECTED, "is_error": False})
    assert not runner.auth_failed(None)


@pytest.mark.skipif(
    not sandbox.confinement_available(), reason="needs macOS sandbox-exec"
)
def test_a_sign_in_that_dies_mid_run_stops_and_leaves_no_task_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tasks = taskset.load()
    task = next(t for t in tasks.tasks if t.example is not None)

    def build(_t: Any, dest: Path, _s: Path) -> dict[str, str]:
        dest.mkdir(parents=True)
        return {}

    def turn(argv: list[str], *_: Any, **__: Any) -> Any:
        meta = runner.TurnMeta(
            prompt=argv[2],
            argv=argv,
            seconds=0.0,
            exit_code=1,
            session_id="s",
        )
        return meta, _REJECTED

    def token(**_: object) -> str:
        return "tok-for-test"

    monkeypatch.setattr(runner, "build_repo", build)
    monkeypatch.setattr(runner, "run_turn", turn)
    monkeypatch.setattr(sandbox, "claude_oauth_token", token)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with pytest.raises(sandbox.SandboxUnavailableError, match="rejected"):
        runner.run_task(
            task, tasks, run_dir, runner.SKILL_SOURCE, tmp_path, None, tmp_path
        )
    assert not (run_dir / task.id).exists()


def test_a_dead_token_file_stops_the_guest_before_any_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    probe = tmp_path / "probe.json"
    probe.write_text(
        json.dumps({"authenticated": False, "returncode": 1}),
        encoding="utf-8",
    )

    def fake_probe(*_: Any, **__: Any) -> Path:
        return probe

    ran: list[str] = []

    def run_task(*_: Any, **__: Any) -> None:
        ran.append("x")

    monkeypatch.setenv(sandbox.TOKEN_FILE_ENV, str(tmp_path / "token"))
    monkeypatch.setattr(skill_eval, "auth_probe", fake_probe)
    monkeypatch.setattr(runner, "run_task", run_task)
    code = skill_eval.main(
        [
            "run",
            "--no-eval-user",
            "--only",
            "prove-rolling-fix",
            "--label",
            "dead",
            "--out",
            str(tmp_path),
            "--cache",
            str(tmp_path / "cache"),
            "--work-root",
            str(tmp_path / "work"),
        ]
    )
    assert code == 2
    assert ran == []
    assert "STOPPED" in capsys.readouterr().out


def test_setup_is_confined_exactly_when_running_as_the_eval_user(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tasks = taskset.load(runner.REPO_ROOT / "eval/skill/tasks-real.json")
    task = next(t for t in tasks.tasks if t.repo is not None)
    spec = realrepo.RepoSpec(
        repository="o/r",
        task_commit="a",
        reference_commit="b",
        reference_number="",
        reference_title="",
        setup=[],
        env={},
        oracle=None,
    )
    seen: list[bool] = []

    def seed(_s: Any, _r: Any, dest: Path, _c: Any) -> dict[str, str]:
        (dest / ".git").mkdir(parents=True)
        return {}

    def setup(*_: Any, **kwargs: Any) -> None:
        seen.append(bool(kwargs.get("confine")))

    monkeypatch.setattr(realrepo, "seed_repo", seed)
    monkeypatch.setattr(realrepo, "run_setup", setup)
    for env_set in (True, False):
        if env_set:
            monkeypatch.setenv(sandbox.TOKEN_FILE_ENV, "/x/token")
        else:
            monkeypatch.delenv(sandbox.TOKEN_FILE_ENV)
        runner.build_real_repo(
            task,
            spec,
            tmp_path / str(env_set),
            runner.SKILL_SOURCE,
            tmp_path,
            [],
        )
    assert seen == [True, False]


def test_go_modules_are_fetched_after_the_seed_and_before_the_confined_setup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tasks = taskset.load(runner.REPO_ROOT / "eval/skill/tasks-real.json")
    task = next(t for t in tasks.tasks if t.repo is not None)
    spec = realrepo.RepoSpec(
        repository="o/r",
        task_commit="a",
        reference_commit="b",
        reference_number="",
        reference_title="",
        setup=[],
        env={},
        oracle=None,
    )
    order: list[str] = []

    def seed(_s: Any, _r: Any, dest: Path, _c: Any) -> dict[str, str]:
        (dest / ".git").mkdir(parents=True)
        order.append("seed")
        return {}

    def setup(*_: Any, **__: Any) -> None:
        order.append("setup")

    monkeypatch.setattr(realrepo, "seed_repo", seed)
    monkeypatch.setattr(realrepo, "run_setup", setup)
    runner.build_real_repo(
        task,
        spec,
        tmp_path / "t",
        runner.SKILL_SOURCE,
        tmp_path,
        [],
        lambda: order.append("modules"),
    )
    assert order == ["seed", "modules", "setup"]


@integration
@needs_user
def test_a_guest_that_ignores_sigterm_is_still_swept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = eval_user.require()
    monkeypatch.setattr(eval_user, "_TERMINATE_WAIT", 2.0)

    def alive() -> bool:
        listed = eval_user.run(user, ["/usr/bin/pgrep", "-f", "sleep 4322"])
        return listed.returncode == 0

    def interrupt() -> None:
        for _ in range(100):
            if alive():
                break
            time.sleep(0.1)
        os.kill(os.getpid(), signal.SIGINT)

    thread = threading.Thread(target=interrupt)
    thread.start()
    with pytest.raises(KeyboardInterrupt):
        eval_user.run_guest(
            user, ["/bin/sh", "-c", "trap '' TERM; sleep 4322; sleep 4322"]
        )
    thread.join()
    time.sleep(1)
    assert not alive()


def test_the_lock_refuses_a_second_eval(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"
    with eval_user.exclusive(lock):
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, sys.argv[1]);"
                "import eval_user, pathlib;"
                "\ntry:\n"
                "    with eval_user.exclusive(pathlib.Path(sys.argv[2])):"
                " print('GOT')\n"
                "except eval_user.EvalUserBusyError as e: print('BUSY')",
                str(Path(eval_user.__file__).parent),
                str(lock),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert child.stdout.strip() == "BUSY"
    with eval_user.exclusive(lock):
        pass


def test_sweep_refuses_without_the_lock() -> None:
    with pytest.raises(RuntimeError, match="lock"):
        eval_user.sweep(USER)


def test_the_host_probes_the_sign_in_once_for_the_whole_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def run_guest(_u: Any, argv: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append([str(a) for a in argv])
        return subprocess.CompletedProcess([], 0, b"", b"")

    def noop(*_: Any, **__: Any) -> None:
        return None

    def staged(*_: Any, **__: Any) -> eval_user.Staged:
        return eval_user.Staged("w", "a", "b", "p")

    def pulled(*_: Any, **__: Any) -> int:
        return 0

    def scored(*_: Any, **__: Any) -> int:
        return 0

    def usable(*_: Any) -> eval_user.EvalUser:
        return USER

    monkeypatch.setattr(eval_user, "require", usable)
    monkeypatch.setattr(eval_user, "exclusive", contextlib.nullcontext)
    monkeypatch.setattr(eval_user, "sweep", noop)
    monkeypatch.setattr(eval_user, "stage", staged)
    monkeypatch.setattr(eval_user, "reset", noop)
    monkeypatch.setattr(eval_user, "remove", noop)
    monkeypatch.setattr(eval_user, "verify_bundle", noop)
    monkeypatch.setattr(eval_user, "run_guest", run_guest)
    monkeypatch.setattr(eval_user, "pull", pulled)
    monkeypatch.setattr(eval_user, "write_marker", noop)
    monkeypatch.setattr(skill_eval, "_score_and_report", scored)
    code = skill_eval.main(
        [
            "run",
            "--only",
            "prove-rolling-fix",
            "find-rolling-light",
            "--label",
            "once",
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 0
    probes = [c for c in calls if "auth-probe" in c]
    runs = [c for c in calls if "run" in c]
    assert len(probes) == 1
    assert probes == calls[:1]
    assert all("--sign-in-checked" in c for c in runs)


def test_a_failed_host_probe_runs_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[list[str]] = []

    def run_guest(_u: Any, argv: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append([str(a) for a in argv])
        return subprocess.CompletedProcess([], 2, b"", b"")

    def noop(*_: Any, **__: Any) -> None:
        return None

    def staged(*_: Any, **__: Any) -> eval_user.Staged:
        return eval_user.Staged("w", "a", "b", "p")

    def usable(*_: Any) -> eval_user.EvalUser:
        return USER

    monkeypatch.setattr(eval_user, "require", usable)
    monkeypatch.setattr(eval_user, "exclusive", contextlib.nullcontext)
    monkeypatch.setattr(eval_user, "sweep", noop)
    monkeypatch.setattr(eval_user, "stage", staged)
    monkeypatch.setattr(eval_user, "reset", noop)
    monkeypatch.setattr(eval_user, "remove", noop)
    monkeypatch.setattr(eval_user, "verify_bundle", noop)
    monkeypatch.setattr(eval_user, "run_guest", run_guest)
    code = skill_eval.main(
        ["run", "--only", "prove-rolling-fix", "--out", str(tmp_path)]
    )
    assert code == 2
    assert "sign-in" in capsys.readouterr().err
    assert len(calls) == 1


@pytest.mark.skipif(
    not sandbox.confinement_available(), reason="needs macOS sandbox-exec"
)
def test_a_probe_that_times_out_is_not_signed_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def hang(argv: list[str], **_: Any) -> Any:
        raise subprocess.TimeoutExpired(argv, 300)

    def token(**_: object) -> str:
        return "tok-secret-2"

    monkeypatch.setattr(sandbox, "claude_oauth_token", token)
    monkeypatch.setattr(skill_eval.subprocess, "run", hang)
    record = skill_eval.auth_probe(tmp_path / "p", tmp_path / "w", False)
    data = json.loads(record.read_text(encoding="utf-8"))
    assert data["authenticated"] is False
    assert data["timed_out"] is True

    def again(*_: Any) -> Path:
        return record

    monkeypatch.setattr(skill_eval, "auth_probe", again)
    with pytest.raises(sandbox.SandboxUnavailableError, match="sign-in"):
        skill_eval.require_sign_in(tmp_path, tmp_path)


@pytest.fixture(autouse=True)
def _one_eval_user_user_at_a_time(request: Any) -> Generator[None]:
    """Integration tests sweep the eval user: not while an eval runs."""
    if request.node.get_closest_marker("integration") is None:
        yield
        return
    try:
        with eval_user.exclusive():
            yield
    except eval_user.EvalUserBusyError:
        pytest.skip("an eval holds the eval user")


@pytest.mark.parametrize(
    ("authenticated", "code"), [(True, 0), (False, 2)], ids=["in", "out"]
)
def test_auth_probe_exits_0_when_signed_in_and_2_when_not(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    authenticated: bool,
    code: int,
) -> None:
    record = tmp_path / "probe-1.json"
    record.write_text(
        json.dumps({"authenticated": authenticated}), encoding="utf-8"
    )

    def fake(*_: Any, **__: Any) -> Path:
        return record

    monkeypatch.setattr(skill_eval, "auth_probe", fake)
    assert (
        skill_eval.main(
            [
                "auth-probe",
                "--no-eval-user",
                str(tmp_path / "out"),
                "--work-root",
                str(tmp_path / "w"),
            ]
        )
        == code
    )


def test_a_guest_told_the_sign_in_was_checked_does_not_probe_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    probes: list[str] = []

    def probe(*_: Any, **__: Any) -> Path:
        probes.append("x")
        raise AssertionError("probed")

    def run_task(*_: Any, **__: Any) -> Any:
        raise sandbox.SandboxUnavailableError("stop after the probe step")

    monkeypatch.setenv(sandbox.TOKEN_FILE_ENV, str(tmp_path / "token"))
    monkeypatch.setattr(skill_eval, "auth_probe", probe)
    monkeypatch.setattr(runner, "run_task", run_task)
    argv = [
        "run",
        "--no-eval-user",
        "--only",
        "prove-rolling-fix",
        "--label",
        "checked",
        "--out",
        str(tmp_path),
        "--cache",
        str(tmp_path / "cache"),
        "--work-root",
        str(tmp_path / "work"),
    ]
    assert skill_eval.main([*argv, "--sign-in-checked"]) == 2
    assert probes == []
    # Without the flag the guest does probe (the control).
    with pytest.raises(AssertionError, match="probed"):
        skill_eval.main([*argv, "--resume"])
    assert probes == ["x"]


def test_the_pause_marker_holds_the_host_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    order: list[str] = []

    def waited(*_: Any) -> float:
        order.append("wait")
        return 0.0

    def run_guest(_u: Any, argv: Any) -> subprocess.CompletedProcess[bytes]:
        order.append("probe" if "auth-probe" in map(str, argv) else "run")
        return subprocess.CompletedProcess([], 0, b"", b"")

    def staged(*_: Any, **__: Any) -> eval_user.Staged:
        return eval_user.Staged("w", "a", "b", "p")

    def usable(*_: Any) -> eval_user.EvalUser:
        return USER

    def pulled(*_: Any, **__: Any) -> int:
        return 0

    def scored(*_: Any, **__: Any) -> int:
        return 0

    for name in ("sweep", "reset", "remove", "verify_bundle", "write_marker"):
        monkeypatch.setattr(eval_user, name, _nothing)
    monkeypatch.setattr(eval_user, "require", usable)
    monkeypatch.setattr(eval_user, "exclusive", contextlib.nullcontext)
    monkeypatch.setattr(eval_user, "stage", staged)
    monkeypatch.setattr(eval_user, "run_guest", run_guest)
    monkeypatch.setattr(eval_user, "pull", pulled)
    monkeypatch.setattr(skill_eval, "wait_while_marked", waited)
    monkeypatch.setattr(skill_eval, "_score_and_report", scored)
    skill_eval.main(
        ["run", "--only", "prove-rolling-fix", "--out", str(tmp_path)]
    )
    assert order.index("probe") > 1
    assert order[order.index("probe") - 1] == "wait"
