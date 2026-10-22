"""The agent's environment cannot act as the owner (run, not read)."""

import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

import realrepo
import runner
import sandbox
import skill_eval
import taskset

_FAKE = {
    "PATH": os.environ["PATH"],
    "HOME": os.environ["HOME"],
    "GITHUB_TOKEN": "ghp_not_real",
    "GH_TOKEN": "gho_not_real",
    "AWS_SECRET_ACCESS_KEY": "x",
    "MY_API_KEY": "x",
    "DB_PASSWORD": "x",
    "SSH_AUTH_SOCK": "/tmp/agent.sock",  # noqa: S108
    "SSH_AGENT_PID": "1",
    "GIT_ASKPASS": "/bin/true",
    "ANTHROPIC_API_KEY": "keep-me",
    "CLAUDE_CODE_OAUTH_TOKEN": "keep-me-too",
    "LANG": "C",
    "LC_ALL": "C",
    "UV_CACHE_DIR": "/x",
    "JAVA_HOME": "/j",
    "GITHUB_PAT": "x",
    "SSHPASS": "x",
    "OPENAI_APIKEY": "x",
    "BUNDLE_GITHUB__COM": "x",
    "DOCKER_AUTH_CONFIG": "x",
    "DATABASE_URL": "x",
    "NETRC": "x",
    "PGPASSFILE": "x",
    "SOMETHING_ELSE": "x",
}
confinement = pytest.mark.skipif(
    not sandbox.confinement_available(), reason="needs macOS sandbox-exec"
)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return sandbox.agent_env(_FAKE, tmp_path / "s", [tmp_path / "bin"])


def test_only_allowlisted_names_survive(env: dict[str, str]) -> None:
    assert env["ANTHROPIC_API_KEY"] == "keep-me"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "keep-me-too"  # noqa: S105
    for kept in ("LANG", "LC_ALL", "UV_CACHE_DIR", "JAVA_HOME", "HOME"):
        assert kept in env
    for dropped in (
        "GITHUB_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "MY_API_KEY",
        "DB_PASSWORD",
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        "GIT_ASKPASS",
        "GITHUB_PAT",
        "SSHPASS",
        "OPENAI_APIKEY",
        "BUNDLE_GITHUB__COM",
        "DOCKER_AUTH_CONFIG",
        "DATABASE_URL",
        "NETRC",
        "PGPASSFILE",
        "SOMETHING_ELSE",
    ):
        assert dropped not in env, dropped
    assert env["GH_TOKEN"] == sandbox.SENTINEL
    assert sandbox.suspicious(env) == []


def test_the_real_environment_has_nothing_left_either(
    tmp_path: Path,
) -> None:
    built = sandbox.agent_env(os.environ, tmp_path, [])
    assert sandbox.suspicious(built) == []
    assert "SSH_AUTH_SOCK" not in built


def _repo(tmp_path: Path, env: dict[str, str]) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, env=env, check=True)
    return repo


def test_a_repo_local_credential_helper_is_not_run(
    env: dict[str, str], tmp_path: Path
) -> None:
    repo = _repo(tmp_path, env)
    helper = "!echo username=u; echo password=LEAKED; true"
    subprocess.run(
        ["git", "config", "credential.helper", helper],
        cwd=repo,
        env=env,
        check=True,
    )
    asked = subprocess.run(
        ["git", "credential", "fill"],
        cwd=repo,
        env=env,
        input="protocol=https\nhost=example.com\n\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert "LEAKED" not in asked.stdout + asked.stderr
    shown = subprocess.run(
        ["git", "config", "--get-all", "credential.helper"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert shown.stdout.splitlines()[-1] == ""


def test_a_repo_local_fsmonitor_and_hooks_do_not_run(
    env: dict[str, str], tmp_path: Path
) -> None:
    repo = _repo(tmp_path, env)
    marker = tmp_path / "ran"
    script = tmp_path / "mon.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\nprintf ''\n")
    script.chmod(0o755)
    subprocess.run(
        ["git", "config", "core.fsmonitor", str(script)],
        cwd=repo,
        env=env,
        check=True,
    )
    (repo / ".git" / "hooks").mkdir(exist_ok=True)
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    (repo / "f").write_text("x")
    for args in (["add", "f"], ["status"], ["commit", "-q", "-m", "x"]):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True)
    assert not marker.exists()


def test_git_has_no_ssh_and_no_prompt(env: dict[str, str]) -> None:
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_SSH_COMMAND"] == "false"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull


def test_gh_is_a_stub_and_the_keychain_is_never_read(
    env: dict[str, str],
) -> None:
    stubbed = subprocess.run(
        ["gh", "auth", "token"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert stubbed.returncode != 0
    assert stubbed.stdout == ""
    real = shutil.which("gh", path=_FAKE["PATH"])
    if real is None:
        pytest.skip("no gh on this machine")
    direct = subprocess.run(
        [real, "auth", "token"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert direct.stdout.strip() in {"", sandbox.SENTINEL}
    assert direct.stdout.strip() != _FAKE["GH_TOKEN"]


def test_commits_need_no_global_git_config(
    env: dict[str, str], tmp_path: Path
) -> None:
    repo = _repo(tmp_path, env)
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "x"],
        cwd=repo,
        env=env,
        check=True,
    )


def test_the_task_env_of_a_real_repo_is_sandboxed(tmp_path: Path) -> None:
    spec = realrepo.load_spec("sharkdp-fd-pr853")
    repo = tmp_path / "run" / "repos" / "t"
    (repo / ".venv" / "bin").mkdir(parents=True)
    built = realrepo.task_env(spec, repo, [tmp_path / "shim"])
    assert sandbox.suspicious(built) == []
    assert built["PATH"].split(os.pathsep)[0] == str(repo / ".venv" / "bin")
    assert (tmp_path / "run" / "sandbox" / "t" / "denied-bin" / "gh").exists()


class _Recorder:
    """A ``subprocess.Popen`` stand-in that records its arguments."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.real = subprocess.Popen
        self.body: str | None = None

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        self.calls.append((list(argv), kwargs))
        if "claude" in argv:
            body = self.body or (
                "import json,sys;"
                "print(json.dumps({'type':'result','session_id':'s',"
                "'result':'Result: NO VERDICT (probe)',"
                "'total_cost_usd':0.0,'num_turns':1}))"
                if "-p" in argv
                else "print('probe')"
            )
            return self.real([sys.executable, "-c", body], **kwargs)
        return self.real(argv, **kwargs)


def _check_clean(env: dict[str, str]) -> None:
    assert env["GH_TOKEN"] == sandbox.SENTINEL
    assert "SSH_AUTH_SOCK" not in env
    assert "GITHUB_PAT" not in env
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert env["GIT_CONFIG_COUNT"] == "4"
    assert env["GIT_CONFIG_KEY_0"] == "credential.helper"
    assert env["GIT_CONFIG_VALUE_0"] == ""
    assert not list(Path(env["GH_CONFIG_DIR"]).iterdir())


@confinement
def test_run_task_reaches_claude_through_the_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")  # noqa: S108
    monkeypatch.setenv("GITHUB_PAT", "x")
    monkeypatch.setenv("GH_TOKEN", "gho_not_real")
    monkeypatch.setattr(sandbox, "claude_oauth_token", lambda: "fake-oauth")
    recorder = _Recorder()
    monkeypatch.setattr(subprocess, "Popen", recorder)
    tasks = taskset.load()
    task = next(t for t in tasks.tasks if t.id == "prove-rolling-fix")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    runner.run_task(
        task,
        tasks,
        run_dir,
        runner.SKILL_SOURCE,
        runner.shim_dir(run_dir),
        work_root=tmp_path / "work",
    )
    launches = [c for c in recorder.calls if "-p" in c[0]]
    assert len(launches) == 1
    argv, kwargs = launches[0]
    assert kwargs["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "fake-oauth"  # noqa: S105
    assert argv[0] == "sandbox-exec"
    assert "claude" in argv
    _check_clean(kwargs["env"])
    config = Path(kwargs["env"]["CLAUDE_CONFIG_DIR"])
    assert config.is_relative_to(tmp_path / "work")
    tmpdir = Path(kwargs["env"]["CLAUDE_CODE_TMPDIR"])
    assert tmpdir.is_relative_to(tmp_path / "work")
    assert tmpdir.is_dir()
    # Every other process of the task is clean too, git included.
    for call_argv, call_kwargs in recorder.calls:
        if call_argv[:1] == ["sandbox-exec"]:
            # Own session, so "other processes" includes the harness.
            assert call_kwargs.get("start_new_session") is True, call_argv
        env = call_kwargs.get("env")
        assert env is not None, call_argv
        assert env.get("GH_TOKEN") == sandbox.SENTINEL, call_argv
        assert "SSH_AUTH_SOCK" not in env, call_argv


def test_realrepo_setup_runs_in_the_sandbox_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")  # noqa: S108
    monkeypatch.setenv("GITHUB_PAT", "x")
    recorder = _Recorder()
    monkeypatch.setattr(subprocess, "Popen", recorder)
    spec = realrepo.load_spec("sharkdp-fd-pr853").model_copy(
        update={"setup": ["python3 -c pass"]}
    )
    repo = tmp_path / "run" / "repos" / "t"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    recorder.calls.clear()
    realrepo.run_setup(spec, repo, [])
    setups = [c for c in recorder.calls if c[0][:1] == ["python3"]]
    assert len(setups) == 1
    _check_clean(setups[0][1]["env"])


def test_a_missing_sandbox_exec_fails_closed_for_the_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox, "confinement_available", lambda: False)
    with pytest.raises(sandbox.SandboxUnavailableError):
        sandbox.wrap(
            ["claude"], tmp_path, checkout=tmp_path, allow_docker=False
        )
    assert sandbox.wrap(
        ["git"], tmp_path, checkout=tmp_path, allow_docker=False, strict=False
    ) == ["git"]


def test_the_auth_probe_outputs_are_committed_and_hold_no_token() -> None:
    probes = sorted(
        (Path(__file__).parent / "runs" / "sandbox-probes").glob("*.json")
    )
    records = [json.loads(p.read_text(encoding="utf-8")) for p in probes]
    assert any(r["authenticated"] for r in records), "no probe signed in"
    assert any(not r["authenticated"] for r in records), "keep the failure"
    for probe in probes:
        text = probe.read_text(encoding="utf-8")
        assert "sk-ant" not in text
        assert not re.search(r"[A-Za-z0-9_-]{60,}", text)


def _keychain(payload: str, code: int = 0) -> Any:
    def run(*_: Any, **__: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], code, payload, "")

    return run


def _oauth(expires_ms: float) -> str:
    return json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": "access-123",
                "refreshToken": "refresh-456",
                "expiresAt": expires_ms,
            }
        }
    )


def test_the_token_comes_from_the_keychain_and_never_the_refresh_token() -> (
    None
):
    now = 1_000_000.0
    token = sandbox.claude_oauth_token(
        {}, _keychain(_oauth((now + 7200) * 1000)), now
    )
    assert token == "access-123"  # noqa: S105


def test_a_sign_in_already_in_the_environment_is_left_alone() -> None:
    def boom(*_: Any, **__: Any) -> subprocess.CompletedProcess[str]:
        msg = "must not read the keychain"
        raise AssertionError(msg)

    assert sandbox.claude_oauth_token({"ANTHROPIC_API_KEY": "k"}, boom) is None


def test_a_missing_or_expiring_sign_in_stops_the_run() -> None:
    now = 1_000_000.0
    with pytest.raises(sandbox.SandboxUnavailableError, match="keychain"):
        sandbox.claude_oauth_token({}, _keychain("", 44), now)
    with pytest.raises(sandbox.SandboxUnavailableError, match="keychain"):
        sandbox.claude_oauth_token({}, _keychain("not json"), now)
    with pytest.raises(sandbox.SandboxUnavailableError, match="expires"):
        sandbox.claude_oauth_token(
            {}, _keychain(_oauth((now + 60) * 1000)), now
        )


def test_a_transcript_never_keeps_the_token(tmp_path: Path) -> None:
    secret = "tok-SECRET-value"  # noqa: S105
    code = (
        "import json;"
        "print(json.dumps({'type':'assistant','message':{'content':"
        f"[{{'type':'text','text':'env says {secret}'}}]}}}}));"
        "print(json.dumps({'type':'result','session_id':'s',"
        f"'result':'{secret}'}}))"
    )
    out = tmp_path / "out.jsonl"
    runner.run_turn(
        [sys.executable, "-c", code],
        tmp_path,
        dict(os.environ),
        out,
        tmp_path / "err",
        30.0,
        redact=[secret],
    )
    assert secret not in out.read_text(encoding="utf-8")
    assert "<redacted>" in out.read_text(encoding="utf-8")


_PROBE = Path(__file__).parent / "keychain_probe.py"


def _security_framework(
    tmp_path: Path, profile_text: str | None
) -> tuple[int, str]:
    # The script is copied out of ``eval/``, which the profile hides.
    script = tmp_path / "kp.py"
    shutil.copy(_PROBE, script)
    argv = [sys.executable, str(script)]
    if profile_text is not None:
        sb = tmp_path / "t.sb"
        sb.write_text(profile_text, encoding="utf-8")
        argv = ["sandbox-exec", "-f", str(sb), *argv]
    done = subprocess.run(argv, capture_output=True, text=True, check=False)
    return done.returncode, done.stdout.strip()


@confinement
def test_the_security_framework_cannot_reach_the_keychain(
    tmp_path: Path,
) -> None:
    reachable = {"0", "-25300"}
    code, plain = _security_framework(tmp_path, None)
    assert code == 0
    assert plain in reachable, "the probe needs the framework to work here"
    full = sandbox.profile(
        Path.home(),
        tmp_path / "pp",
        allow_docker=False,
        read=[Path(sys.prefix), tmp_path],
    )
    code, closed = _security_framework(tmp_path, full)
    assert code == 0
    assert closed not in reachable, closed
    # The control: the same profile minus the Mach rule can still ask.
    without = "\n".join(
        ln for ln in full.splitlines() if "mach-lookup" not in ln
    )
    code, open_ = _security_framework(tmp_path, without)
    assert code == 0
    assert open_ in reachable, open_


def _local_remote(tmp_path: Path) -> tuple[str, str]:
    host = tmp_path / "host"
    src = host / "o" / "r.git"
    src.mkdir(parents=True)
    env = sandbox.agent_env(os.environ, tmp_path / "s", [])
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=src, env=env)
    (src / "f").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=src, env=env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "c"], cwd=src, env=env)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=src,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return f"file://{host}", sha


def test_seeding_runs_git_in_the_sandbox_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, sha = _local_remote(tmp_path)
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")  # noqa: S108
    monkeypatch.setenv("GITHUB_PAT", "x")
    recorder = _Recorder()
    monkeypatch.setattr(subprocess, "Popen", recorder)
    spec = realrepo.RepoSpec(
        repository="o/r",
        task_commit=sha,
        reference_commit=sha,
        reference_number="",
        reference_title="",
        setup=[],
        env={},
        oracle=None,
    )
    realrepo.seed_repo(
        spec,
        taskset.RealRepo(manifest="x"),
        tmp_path / "run" / "repos" / "t",
        tmp_path / "cache",
        host,
    )
    gits = [c for c in recorder.calls if c[0][:1] == ["git"]]
    assert len(gits) >= 4
    for argv, kwargs in gits:
        assert kwargs["env"]["GH_TOKEN"] == sandbox.SENTINEL, argv
        assert "SSH_AUTH_SOCK" not in kwargs["env"], argv
        assert "GITHUB_PAT" not in kwargs["env"], argv


@confinement
def test_the_oracle_runs_confined_and_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")  # noqa: S108
    recorder = _Recorder()
    monkeypatch.setattr(subprocess, "Popen", recorder)
    spec = realrepo.load_spec("sharkdp-fd-pr853").model_copy(
        update={"oracle": "true"}
    )
    repo = tmp_path / "run" / "repos" / "t"
    repo.mkdir(parents=True)
    assert realrepo.run_oracle(spec, repo, [])[0] is True
    argv, kwargs = recorder.calls[0]
    assert argv[0] == "sandbox-exec"
    _check_clean(kwargs["env"])


def _fake_world(tmp_path: Path) -> dict[str, Path]:
    home = tmp_path / "home"
    root = tmp_path / "pp"
    tree = tmp_path / "work" / "t"
    free = tmp_path / "free"
    free.mkdir()
    paths = {
        "home": home,
        "root": root,
        "tree": tree,
        "free": free,
        "ssh": home / ".ssh" / "id",
        "claude": home / ".claude" / "projects" / "-p" / "s.jsonl",
        "claudejson": home / ".claude.json",
        "codex": home / ".codex" / "auth.json",
        "history": home / ".zsh_history",
        "rc": home / ".zshrc",
        "config": home / ".config" / "x",
        "support": home / "Library" / "Application Support" / "x",
        "dotenv": home / "proj" / ".env",
        "old_run": root / "scratch" / "skill-eval" / "r.json",
        "tasks": root / "eval" / "tasks" / "m.json",
        "fixtures": root / "tests" / "fixtures" / "f",
        "carved": home / ".cargo" / "credentials.toml",
        "cargo_config": home / ".cargo" / "config.toml",
        "tool": home / ".local" / "bin" / "tool",
        "registry": home / ".cargo" / "bin" / "r",
        "treefile": tree / "f",
    }
    for key, path in paths.items():
        if key in {"home", "root", "tree", "free"}:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content", encoding="utf-8")
    return paths


def _sandboxed(
    profile_text: str, tmp_path: Path, *cmd: str
) -> subprocess.CompletedProcess[str]:
    sb = tmp_path / "p.sb"
    sb.write_text(profile_text, encoding="utf-8")
    return subprocess.run(
        ["sandbox-exec", "-f", str(sb), *cmd],
        capture_output=True,
        text=True,
        check=False,
    )


def _world_profile(w: dict[str, Path]) -> str:
    return sandbox.profile(
        w["home"],
        w["root"],
        False,
        read=[w["tree"]],
        write=[w["tree"]],
        temp_dirs=[w["free"]],
    )


@confinement
def test_reads_outside_the_allowlist_fail_and_the_allowed_ones_work(
    tmp_path: Path,
) -> None:
    w = _fake_world(tmp_path)
    text = _world_profile(w)
    for name in (
        "ssh",
        "claude",
        "claudejson",
        "codex",
        "history",
        "rc",
        "config",
        "support",
        "dotenv",
        "old_run",
        "tasks",
        "fixtures",
        "carved",
        "cargo_config",
    ):
        got = _sandboxed(text, tmp_path, "cat", str(w[name]))
        assert got.returncode != 0, name
        assert got.stdout == "", name
    for name in ("tool", "registry", "treefile"):
        assert _sandboxed(text, tmp_path, "cat", str(w[name])).stdout == (
            "content"
        ), name


@confinement
def test_a_denied_directory_cannot_be_renamed_into_view(
    tmp_path: Path,
) -> None:
    w = _fake_world(tmp_path)
    text = _world_profile(w)
    for name, folder in (
        ("ssh", w["ssh"].parent),
        ("claude", w["home"] / ".claude"),
        ("tasks", w["root"] / "eval"),
    ):
        target = w["free"] / f"moved-{name}"
        moved = _sandboxed(text, tmp_path, "mv", str(folder), str(target))
        assert moved.returncode != 0, name
        assert not target.exists(), name
        assert folder.exists(), name
    # The same attack against a read-only deny list, as before this
    # profile existed, works: the test fails on that design.
    legacy = "(version 1)\n(allow default)\n" + "\n".join(
        f'(deny file-read* (subpath "{p}"))'
        for p in (w["ssh"].parent, w["root"] / "eval")
    )
    victim = w["root"] / "eval"
    target = w["free"] / "leaked"
    moved = _sandboxed(legacy, tmp_path, "mv", str(victim), str(target))
    assert moved.returncode == 0
    assert (target / "tasks" / "m.json").read_text() == "content"


@confinement
def test_writes_are_confined_to_the_tree_and_the_temp_dirs(
    tmp_path: Path,
) -> None:
    w = _fake_world(tmp_path)
    text = _world_profile(w)
    for ok in (w["tree"] / "new", w["free"] / "new"):
        assert _sandboxed(text, tmp_path, "touch", str(ok)).returncode == 0
    for bad in (
        w["home"] / "new",
        w["home"] / ".ssh" / "new",
        w["root"] / "new",
        w["home"] / ".local" / "bin" / "new",
        w["home"] / ".cache" / "uv" / "new",
        w["home"] / ".cargo" / "registry" / "new",
        w["home"] / "go" / "pkg" / "new",
    ):
        assert _sandboxed(text, tmp_path, "touch", str(bad)).returncode != 0
        assert not bad.exists()


def _docker_up() -> bool:
    try:
        return (
            subprocess.run(
                ["docker", "info"],
                capture_output=True,
                check=False,
                timeout=30,
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


@confinement
@pytest.mark.skipif(not _docker_up(), reason="no running docker daemon")
def test_the_docker_sockets_are_closed_even_to_a_copied_client(
    tmp_path: Path,
) -> None:
    real = shutil.which("docker")
    assert real is not None
    copy = tmp_path / "d2"
    shutil.copy(real, copy)
    copy.chmod(0o755)
    root = Path(__file__).resolve().parents[2]
    control = subprocess.run(
        [str(copy), "ps", "-q"], capture_output=True, text=True, check=False
    )
    assert control.returncode == 0, "the copy must work unconfined"
    closed = _sandboxed(
        sandbox.profile(Path.home(), root, allow_docker=False),
        tmp_path,
        str(copy),
        "ps",
        "-q",
    )
    assert closed.returncode != 0
    assert "permission denied" in (closed.stdout + closed.stderr).lower()
    opened = _sandboxed(
        sandbox.profile(Path.home(), root, allow_docker=True),
        tmp_path,
        str(copy),
        "ps",
        "-q",
    )
    assert opened.returncode == 0


@confinement
def test_a_copied_gh_cannot_read_the_login(tmp_path: Path) -> None:
    real = shutil.which("gh", path="/usr/local/bin:/opt/homebrew/bin")
    if real is None:
        pytest.skip("no gh")
    copy = tmp_path / "g2"
    shutil.copy(real, copy)
    copy.chmod(0o755)
    clean = {k: v for k, v in os.environ.items() if k != "GH_TOKEN"}
    control = subprocess.run(
        [str(copy), "auth", "token"],
        env=clean,
        capture_output=True,
        text=True,
        check=False,
    )
    if not control.stdout.strip():
        pytest.skip("gh is not logged in here")
    root = Path(__file__).resolve().parents[2]
    sb = tmp_path / "p.sb"
    sb.write_text(
        sandbox.profile(Path.home(), root, allow_docker=False),
        encoding="utf-8",
    )
    got = subprocess.run(
        ["sandbox-exec", "-f", str(sb), str(copy), "auth", "token"],
        env=clean,
        capture_output=True,
        text=True,
        check=False,
    )
    assert not got.stdout.strip()


def test_secret_forms_cover_reversed_hex_and_base64() -> None:
    secret = "tok-SECRET-value-123456"  # noqa: S105
    forms = sandbox.secret_forms([secret])
    raw = secret.encode()
    assert secret in forms
    assert secret[::-1] in forms
    assert raw.hex() in forms
    assert base64.b64encode(raw).decode() in forms
    assert base64.urlsafe_b64encode(raw).decode() in forms
    shifted = base64.b64encode(b"x" + raw + b"y").decode()
    assert any(f in shifted for f in forms)


def test_a_child_that_prints_the_token_leaves_none_in_either_file(
    tmp_path: Path,
) -> None:
    secret = "tok-SECRET-value-123456"  # noqa: S105
    b64 = base64.b64encode(secret.encode()).decode()
    code = (
        "import sys,json;"
        f"sys.stderr.write('e {secret} {b64} '+{secret[::-1]!r});"
        "print(json.dumps({'type':'assistant','message':{'content':"
        f"[{{'type':'text','text':'{b64} and {secret[::-1]}'}}]}}}}))"
    )
    out, err = tmp_path / "out.jsonl", tmp_path / "err"
    runner.run_turn(
        [sys.executable, "-c", code],
        tmp_path,
        dict(os.environ),
        out,
        err,
        30.0,
        redact=sandbox.secret_forms([secret]),
    )
    for path in (out, err):
        text = path.read_text(encoding="utf-8")
        for form in (secret, b64, secret[::-1]):
            assert form not in text, path.name
    assert "<redacted>" in err.read_text(encoding="utf-8")


@confinement
def test_a_sign_in_from_the_environment_is_redacted_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "env-API-secret-value-98765"  # noqa: S105
    b64 = base64.b64encode(secret.encode()).decode()
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    recorder = _Recorder()
    recorder.body = (
        "import sys,json;"
        f"sys.stderr.write({secret!r});"
        "print(json.dumps({'type':'assistant','message':{'content':"
        f"[{{'type':'text','text':{b64!r}}}]}}}}));"
        "print(json.dumps({'type':'result','session_id':'s',"
        "'result':'Result: NO VERDICT (x)','total_cost_usd':0.0}))"
    )
    monkeypatch.setattr(subprocess, "Popen", recorder)
    tasks = taskset.load()
    task = next(t for t in tasks.tasks if t.id == "prove-rolling-fix")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    runner.run_task(
        task,
        tasks,
        run_dir,
        runner.SKILL_SOURCE,
        runner.shim_dir(run_dir),
        work_root=tmp_path / "work",
    )
    launch = next(c for c in recorder.calls if "-p" in c[0])
    assert launch[1]["env"]["ANTHROPIC_API_KEY"] == secret
    for name in ("turn-1.jsonl", "turn-1.err"):
        text = (run_dir / task.id / name).read_text(encoding="utf-8")
        assert secret not in text, name
        assert b64 not in text, name


def _run_with(tmp_path: Path, text: str) -> Path:
    run = tmp_path / "run"
    (run / "task").mkdir(parents=True)
    (run / "task" / "turn-1.jsonl").write_text(text, encoding="utf-8")
    return run


def test_archiving_refuses_a_run_that_holds_a_credential(
    tmp_path: Path,
) -> None:
    secret = "arch-SECRET-value-123456"  # noqa: S105
    encoded = base64.b64encode(secret.encode()).decode()
    for leaked in (secret, encoded, secret[::-1]):
        run = _run_with(tmp_path / leaked[:4], f"x {leaked} y")
        dest = tmp_path / f"out-{leaked[:4]}"
        with pytest.raises(skill_eval.ArchiveRefusedError) as raised:
            skill_eval.archive(run, dest, [secret])
        assert secret not in str(raised.value)
        assert "task/turn-1.jsonl" in str(raised.value)
        assert not dest.exists()
    clean = _run_with(tmp_path / "clean", "nothing here")
    skill_eval.archive(clean, tmp_path / "kept", [secret])
    assert (tmp_path / "kept" / "task" / "turn-1.jsonl").exists()


def test_each_task_gets_a_private_home_and_a_private_tmpdir(
    tmp_path: Path,
) -> None:
    env = sandbox.agent_env(os.environ, tmp_path / "s", [])
    for key in ("HOME", "TMPDIR"):
        path = Path(env[key])
        assert path.is_relative_to(tmp_path / "s" / "private"), key
        assert path.is_dir(), key
    assert env["HOME"] != os.environ["HOME"]
    rustup = Path.home() / ".rustup"
    if rustup.is_dir():
        assert env["RUSTUP_HOME"] == str(rustup)
        assert (Path(env["HOME"]) / ".rustup").resolve() == rustup.resolve()


@confinement
def test_the_claude_scratch_is_not_writable_and_folders_stay_closed(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    text = sandbox.profile(Path.home(), root, allow_docker=False)
    plant = Path("/private/tmp/claude-99999/planted")
    try:
        got = _sandboxed(text, tmp_path, "mkdir", "-p", str(plant))
        assert got.returncode != 0
        assert not plant.exists()
    finally:
        shutil.rmtree(plant.parent, ignore_errors=True)
    temp = Path(tempfile.gettempdir())
    mark = temp / f"perfproof-probe-{os.getpid()}"
    try:
        got = _sandboxed(text, tmp_path, "touch", str(mark))
        assert got.returncode != 0
        assert not mark.exists()
    finally:
        mark.unlink(missing_ok=True)
    cache = temp.parent / "C"
    got = _sandboxed(text, tmp_path, "ls", str(cache))
    assert got.returncode != 0
    assert got.stdout == ""


def _copy_world(tmp_path: Path) -> tuple[Path, Path]:
    secret = tmp_path / "home" / ".ssh" / "id_rsa"
    secret.parent.mkdir(parents=True)
    secret.write_text("PRIVATE-KEY-BYTES", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "f.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "lnk").symlink_to(secret)
    return repo, secret


def test_the_tree_copy_keeps_a_planted_symlink_a_symlink(
    tmp_path: Path,
) -> None:
    repo, secret = _copy_world(tmp_path)
    scratch = tmp_path / "copy"
    runner.copy_tree(repo, scratch)
    assert (scratch / "lnk").is_symlink()
    assert (scratch / "lnk").readlink() == secret
    for path in scratch.rglob("*"):
        if path.is_file() and not path.is_symlink():
            assert "PRIVATE-KEY-BYTES" not in path.read_text("utf-8")


@confinement
def test_a_planted_symlink_never_reaches_the_state(tmp_path: Path) -> None:
    rc = Path.home() / ".zshrc"
    if not rc.is_file():
        pytest.skip("no ~/.zshrc here to plant a link to")
    lines = [
        ln.strip()
        for ln in rc.read_text("utf-8", errors="replace").splitlines()
        if len(ln.strip()) >= 12
    ][:1]
    if not lines:
        pytest.skip("~/.zshrc has nothing distinctive")
    work = tmp_path / "run" / "repos" / "t"
    work.mkdir(parents=True)
    env = sandbox.agent_env(os.environ, tmp_path / "run" / "sandbox" / "t", [])
    for args in (["init", "-q", "-b", "main"],):
        subprocess.run(["git", *args], cwd=work, env=env, check=True)
    (work / "lnk").symlink_to(rc)
    (work / "test_it.py").write_text(
        "def test_it():\n"
        "    ls = open('lnk').read().splitlines()\n"
        "    keep = [x.strip() for x in ls if len(x.strip()) >= 12]\n"
        "    raise AssertionError(keep[0])\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=work, env=env, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "x"], cwd=work, env=env, check=True
    )
    state = runner.capture_state(
        work, {"main": "x"}, tmp_path / "run" / "work" / "t"
    )
    blob = state.model_dump_json()
    tails = " ".join(tree.tests_tail for tree in state.trees)
    for line in lines:
        assert line[:12] not in blob
        assert line[:12] not in tails


def test_a_wrapped_or_encoded_secret_is_still_redacted() -> None:
    secret = "tok-SECRET-value-123456789"  # noqa: S105
    raw = secret.encode()
    b64 = base64.b64encode(raw).decode()
    wrapped = "\n".join(b64[i : i + 12] for i in range(0, len(b64), 12))
    hexed = raw.hex()
    xxd = "\n".join(hexed[i : i + 16] for i in range(0, len(hexed), 16))
    forms = sandbox.secret_forms([secret])
    samples = [
        wrapped,
        wrapped.replace("\n", "\\n"),  # as inside a JSON string
        xxd,
        hexed.upper(),
        urllib.parse.quote(secret, safe=""),
        urllib.parse.quote_plus(secret),
    ]
    for text in samples:
        out = sandbox.redact(f"a {text} b", forms)
        assert "<redacted>" in out, text
        assert sandbox.holds_secret(f"a {text} b", forms), text
    assert not sandbox.holds_secret("nothing here", forms)


def _quiet_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in sorted(sandbox.ALLOWED_SECRETS):
        monkeypatch.delenv(name, raising=False)

    def gone(*_: Any, **__: Any) -> str:
        msg = "no sign-in"
        raise sandbox.SandboxUnavailableError(msg)

    monkeypatch.setattr(sandbox, "claude_oauth_token", gone)


def test_archiving_fails_closed_when_the_sign_in_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet_keychain(monkeypatch)
    monkeypatch.setattr(runner, "SECRETS_USED", [])
    run = _run_with(tmp_path, "nothing secret here")
    dest = tmp_path / "out"
    with pytest.raises(skill_eval.ArchiveRefusedError, match="cannot read"):
        skill_eval.archive(run, dest)
    assert not dest.exists()
    with pytest.raises(skill_eval.ArchiveRefusedError, match="no secret"):
        skill_eval.scan_for_secrets(run, [])


def test_archiving_uses_the_secrets_this_process_handed_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "mem-SECRET-value-123456"  # noqa: S105
    _quiet_keychain(monkeypatch)
    monkeypatch.setattr(runner, "SECRETS_USED", [secret])
    leaked = _run_with(tmp_path / "a", f"x {secret} y")
    with pytest.raises(skill_eval.ArchiveRefusedError) as raised:
        skill_eval.archive(leaked, tmp_path / "out-a")
    assert secret not in str(raised.value)
    assert not (tmp_path / "out-a").exists()
    clean = _run_with(tmp_path / "b", "all clear")
    skill_eval.archive(clean, tmp_path / "out-b")
    assert (tmp_path / "out-b" / "task" / "turn-1.jsonl").exists()


def test_a_record_that_is_a_symlink_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps(
            {
                "run_id": "r",
                "verdict": {"outcome": "KEEP", "reason": "x", "gate": None},
            }
        ),
        encoding="utf-8",
    )
    runs = tmp_path / "repo" / ".perfproof" / "runs"
    (runs / "a").mkdir(parents=True)
    (runs / "a" / "record.json").symlink_to(outside)
    assert runner.run_verdicts(tmp_path / "repo") == []
    (runs / "b").mkdir()
    (runs / "b" / "record.json").write_text(outside.read_text("utf-8"))
    assert [v.outcome for v in runner.run_verdicts(tmp_path / "repo")] == [
        "KEEP"
    ]


def test_the_private_home_is_what_perfproofs_own_commands_inherit() -> None:
    from perfproof.bench.side_effects import BASE_ENV_VARIABLES

    # perfproof passes benchmark and profiler commands only these, so a
    # private cache variable would be dropped but HOME and TMPDIR stay.
    assert {"HOME", "TMPDIR"} <= BASE_ENV_VARIABLES
    assert "UV_CACHE_DIR" not in BASE_ENV_VARIABLES


_CONNECT = (
    "import socket,sys;s=socket.socket(socket.AF_UNIX);s.settimeout(3)\n"
    "try:\n"
    "    s.connect(sys.argv[1]);print('connected')\n"
    "except OSError as e:\n"
    "    print('failed', e.errno)\n"
)


def _listening(path: Path) -> socket.socket:
    path.parent.mkdir(parents=True, exist_ok=True)
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(path))
    server.listen(4)
    return server


def _connect(profile_text: str | None, tmp_path: Path, sock: Path) -> str:
    argv = [sys.executable, "-I", "-c", _CONNECT, str(sock)]
    if profile_text is not None:
        sb = tmp_path / "c.sb"
        sb.write_text(profile_text, encoding="utf-8")
        argv = ["sandbox-exec", "-f", str(sb), *argv]
    done = subprocess.run(
        argv, capture_output=True, text=True, check=False, cwd="/"
    )
    return done.stdout.strip()


@confinement
def test_unix_sockets_cannot_be_connected_to(tmp_path: Path) -> None:
    # Short paths: a unix socket path is limited to about 100 bytes.
    fake_home = Path(tempfile.mkdtemp(prefix="h", dir="/tmp"))
    shared = Path(tempfile.mkdtemp(prefix="s", dir="/tmp"))
    inside = tmp_path / "tree"
    inside.mkdir()
    short = Path(tempfile.mkdtemp(prefix="t", dir="/tmp"))
    servers: list[socket.socket] = []
    try:
        planted = {
            "in a denied home dir": fake_home / ".ssh" / "a.sock",
            "in shared temp": shared / "b.sock",
        }
        servers.extend(_listening(path) for path in planted.values())
        allowed = short / "c.sock"
        servers.append(_listening(allowed))
        text = sandbox.profile(
            fake_home,
            tmp_path / "pp",
            False,
            read=[Path(sys.prefix)],
            write=[short],
        )
        for where, path in planted.items():
            assert _connect(None, tmp_path, path) == "connected", where
            assert _connect(text, tmp_path, path).startswith("failed"), where
        # A socket inside a path the task may write is its own.
        assert _connect(text, tmp_path, allowed) == "connected"
    finally:
        for server in servers:
            server.close()
        for folder in (fake_home, shared, short):
            shutil.rmtree(folder, ignore_errors=True)


@confinement
def test_name_resolution_still_works_without_other_sockets(
    tmp_path: Path,
) -> None:
    text = sandbox.profile(
        Path.home(), tmp_path / "pp", False, read=[Path(sys.prefix)]
    )
    sb = tmp_path / "n.sb"
    sb.write_text(text, encoding="utf-8")
    done = subprocess.run(
        [
            "sandbox-exec",
            "-f",
            str(sb),
            sys.executable,
            "-I",
            "-c",
            "import socket;print(bool(socket.getaddrinfo('localhost', 80)))",
        ],
        cwd="/",
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.stdout.strip() == "True", done.stderr


@confinement
def test_shared_temp_is_closed_and_tmpdir_is_the_way_in(
    tmp_path: Path,
) -> None:
    planted = Path("/private/tmp") / f"perfproof-probe-{os.getpid()}"
    private = tmp_path / "private-tmp"
    private.mkdir()
    planted.write_text("owner data", encoding="utf-8")
    try:
        text = sandbox.profile(
            Path.home(),
            tmp_path / "pp",
            False,
            read=[Path(sys.prefix)],
            write=[private],
        )
        sb = tmp_path / "t.sb"
        sb.write_text(text, encoding="utf-8")

        def run(code: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [
                    "sandbox-exec",
                    "-f",
                    str(sb),
                    sys.executable,
                    "-I",
                    "-c",
                    code,
                ],
                cwd="/",
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, "TMPDIR": str(private)},
            )

        assert run(f"open({str(planted)!r}).read()").returncode != 0
        assert run("open('/tmp/x-probe','w')").returncode != 0
        assert not Path("/tmp/x-probe").exists()  # noqa: S108
        ok = run(
            "import tempfile\n"
            "with tempfile.NamedTemporaryFile() as f:\n"
            "    f.write(b'x')\n"
            "print('ok')"
        )
        assert ok.stdout.strip() == "ok", ok.stderr
    finally:
        planted.unlink(missing_ok=True)


@confinement
@pytest.mark.skipif(shutil.which("clang") is None, reason="no clang")
def test_clang_compiles_and_links_without_the_xcrun_cache(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "run" / "repos" / "t"
    tree.mkdir(parents=True)
    (tree / "t.c").write_text("int main(void){return 0;}\n")
    env = sandbox.agent_env(os.environ, tmp_path / "run" / "sandbox" / "t", [])
    text = sandbox.profile(
        Path.home(),
        tmp_path / "pp",
        False,
        read=[tree],
        write=[tree, tmp_path / "run" / "sandbox" / "t" / "private"],
    )
    assert "xcrun_db" not in text
    sb = tmp_path / "x.sb"
    sb.write_text(text, encoding="utf-8")
    done = subprocess.run(
        [
            "sandbox-exec",
            "-f",
            str(sb),
            "sh",
            "-c",
            "clang t.c -o t.out && ./t.out",
        ],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "xcrun" not in done.stderr
    if sandbox.toolchain_env():
        assert env["DEVELOPER_DIR"] == sandbox.toolchain_env()["DEVELOPER_DIR"]


def test_the_private_home_survives_what_the_agent_does_to_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "realhome"
    (real / ".rustup").mkdir(parents=True)
    (real / ".local" / "share" / "uv" / "python").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: real)
    monkeypatch.setattr(sandbox, "TAMPERED", [])
    base = tmp_path / "private"
    first = sandbox.private_env(base)
    home, tmp = Path(first["HOME"]), Path(first["TMPDIR"])
    assert (home / ".rustup").is_symlink()
    # The agent swaps the link for a directory, a parent for a file, and
    # the temp directory for a file.
    (home / ".rustup").unlink()
    (home / ".rustup").mkdir()
    (home / ".rustup" / "mine").write_text("x")
    shutil.rmtree(home / ".local")
    (home / ".local").write_text("not a directory")
    shutil.rmtree(tmp)
    tmp.write_text("not a directory")
    again = sandbox.private_env(base)
    assert again == first
    assert (home / ".rustup").is_symlink()
    assert (home / ".rustup").readlink() == real / ".rustup"
    assert (home / ".local/share/uv/python").readlink() == (
        real / ".local/share/uv/python"
    )
    assert tmp.is_dir()
    assert len(sandbox.TAMPERED) >= 3
    assert all("replaced" in m for m in sandbox.TAMPERED)
    sandbox.private_env(base)  # and again: nothing left to replace


def test_a_run_stopped_by_the_sign_in_is_scored_and_archived(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []

    def stop(*_: Any, **__: Any) -> None:
        msg = "the claude access token expires within an hour"
        raise sandbox.SandboxUnavailableError(msg)

    monkeypatch.setattr(runner, "run_task", stop)

    def scored(*_: Any, **__: Any) -> int:
        calls.append("scored")
        return 0

    def archived(*_: Any, **__: Any) -> None:
        calls.append("archived")

    monkeypatch.setattr(skill_eval, "_score_and_report", scored)
    monkeypatch.setattr(skill_eval, "archive", archived)
    code = skill_eval.main(
        [
            "run",
            "--only",
            "prove-rolling-fix",
            "--label",
            "stopped",
            "--out",
            str(tmp_path),
            "--cache",
            str(tmp_path / "cache"),
            "--work-root",
            str(tmp_path / "work"),
            "--archive-to",
            str(tmp_path / "kept"),
        ]
    )
    assert code == 2
    assert calls == ["scored", "archived"]
    seen = capsys.readouterr()
    assert "STOPPED" in seen.out
    assert "expires within an hour" in seen.err


@confinement
@pytest.mark.skipif(shutil.which("zsh") is None, reason="no zsh")
def test_shell_heredocs_work_without_a_shared_tmp(tmp_path: Path) -> None:
    tree = tmp_path / "run" / "repos" / "t"
    tree.mkdir(parents=True)
    scratch = tmp_path / "run" / "sandbox" / "t"
    env = sandbox.agent_env(os.environ, scratch, [])
    assert env["TMPPREFIX"].startswith(env["TMPDIR"])
    text = sandbox.profile(
        Path.home(),
        tmp_path / "pp",
        False,
        read=[tree, scratch],
        write=[tree, scratch / "private"],
    )
    sb = tmp_path / "h.sb"
    sb.write_text(text, encoding="utf-8")
    script = "cat <<'X' > out.txt\nhello\nX\ncat out.txt"
    done = subprocess.run(
        ["sandbox-exec", "-f", str(sb), "zsh", "-c", script],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.stdout.strip() == "hello", done.stderr
    broken = {k: v for k, v in env.items() if k != "TMPPREFIX"}
    again = subprocess.run(
        ["sandbox-exec", "-f", str(sb), "zsh", "-c", script],
        cwd=tree,
        env=broken,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "temp file for here document" in again.stderr


def _clipboard_is_empty() -> bool:
    try:
        got = subprocess.run(
            ["pbpaste"], capture_output=True, check=False, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return got.returncode == 0 and got.stdout == b""


def _profile_run(
    tmp_path: Path, *cmd: str
) -> subprocess.CompletedProcess[str]:
    root = Path(__file__).resolve().parents[2]
    sb = tmp_path / "m.sb"
    sb.write_text(
        sandbox.profile(Path.home(), root, allow_docker=False),
        encoding="utf-8",
    )
    return subprocess.run(
        ["sandbox-exec", "-f", str(sb), *cmd],
        capture_output=True,
        text=True,
        check=False,
        cwd="/",
        timeout=60,
    )


@confinement
@pytest.mark.skipif(
    shutil.which("pbpaste") is None, reason="no pasteboard tools"
)
def test_the_clipboard_cannot_be_read_by_any_route(tmp_path: Path) -> None:
    if not _clipboard_is_empty():
        pytest.skip("the owner's clipboard is not empty: not touching it")
    marker = f"perfproof-clipboard-probe-{os.getpid()}"
    subprocess.run(["pbcopy"], input=marker, text=True, check=True)
    try:
        plain = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, check=True
        )
        assert plain.stdout == marker, "the control must read it"
        got = _profile_run(tmp_path, "pbpaste")
        assert marker not in got.stdout + got.stderr
        js = (
            "ObjC.import('AppKit');"
            "$.NSPasteboard.generalPasteboard"
            ".stringForType($.NSPasteboardTypeString).js"
        )
        unconfined = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", js],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if marker in unconfined.stdout:
            via = _profile_run(
                tmp_path, "osascript", "-l", "JavaScript", "-e", js
            )
            assert marker not in via.stdout + via.stderr
    finally:
        subprocess.run(["pbcopy"], input="", text=True, check=True)


@confinement
@pytest.mark.skipif(shutil.which("lsappinfo") is None, reason="no lsappinfo")
def test_launchservices_is_closed(tmp_path: Path) -> None:
    query = ["lsappinfo", "find", "bundleid=com.apple.finder"]
    control = subprocess.run(
        query, capture_output=True, text=True, check=False, timeout=30
    )
    if "ASN:" not in control.stdout:
        pytest.skip("lsappinfo cannot see Finder here")
    got = _profile_run(tmp_path, *query)
    assert "ASN:" not in got.stdout
    opened = _profile_run(tmp_path, "open", "-g", "https://example.invalid/")
    assert opened.returncode != 0


@confinement
def test_sample_and_user_lookup_still_work_with_their_two_names(
    tmp_path: Path,
) -> None:
    sampled = _profile_run(
        tmp_path,
        "sh",
        "-c",
        "(sleep 3 & sample $! 1 -file /dev/stdout 2>&1"
        " | grep -c 'Call graph')",
    )
    assert sampled.stdout.strip() == "1", sampled.stderr
    got = _profile_run(tmp_path, "id", "-un")
    assert got.stdout.strip() == os.environ.get("USER", got.stdout.strip())
    assert got.stdout.strip() not in {"", str(os.getuid())}


def test_the_mach_allowlist_holds_only_its_two_names() -> None:
    text = sandbox.profile(Path("/h"), Path("/r"), allow_docker=False)
    allowed = [ln for ln in text.splitlines() if "(allow mach-lookup" in ln]
    assert len(allowed) == 1
    for name in (
        "com.apple.coresymbolicationd",
        "com.apple.system.opendirectoryd.libinfo",
    ):
        assert name in allowed[0]
    assert allowed[0].count("global-name") == 2
    assert "(deny mach-lookup)" in text.splitlines()
    for forbidden in (
        "com.apple.SecurityServer",
        "com.apple.securityd",
        "com.apple.security.agent",
        "com.apple.pasteboard",
        "launchservices",
        "appleevents",
    ):
        assert forbidden not in allowed[0], forbidden


def test_only_the_compiler_tools_are_put_ahead_of_path(
    tmp_path: Path,
) -> None:
    developer = sandbox.toolchain_env().get("DEVELOPER_DIR")
    if not developer:
        pytest.skip("no developer directory here")
    base = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]}
    env = sandbox.agent_env(base, tmp_path / "s", [])
    resolved = {
        name: shutil.which(name, path=env["PATH"])
        for name in ("python3", "pip3", "clang", "git", "make")
    }
    # Not shadowed by the Command Line Tools' own Python.
    for name in ("python3", "pip3"):
        assert resolved[name] == shutil.which(name, path=base["PATH"]), name
        assert not (resolved[name] or "").startswith(developer), name
    # The compiler and git are the real binaries, not the shims.
    for name in ("clang", "git", "make"):
        found = resolved[name]
        assert found is not None, name
        assert found.startswith(str(tmp_path / "s" / "toolbin")), name
        assert os.path.realpath(found).startswith(
            os.path.realpath(developer)
        ), name
        assert not os.path.realpath(found).startswith("/usr/bin/"), name


def test_docker_tasks_get_only_the_daemon_socket() -> None:
    allowed = sandbox.profile(Path("/h"), Path("/r"), allow_docker=True)
    assert "mutagen" not in allowed
    assert "/h/.docker/run/docker.sock" in allowed
    assert "/var/run/docker.sock" in allowed
    closed = sandbox.profile(Path("/h"), Path("/r"), allow_docker=False)
    assert "docker.sock" not in closed


_PROCARGS = (
    "import ctypes,sys\n"
    "libc=ctypes.CDLL(None)\n"
    "mib=(ctypes.c_int*3)(1,49,int(sys.argv[1]))\n"
    "size=ctypes.c_size_t(0)\n"
    "r0=libc.sysctl(mib,3,None,ctypes.byref(size),None,0)\n"
    "buf=ctypes.create_string_buffer(size.value or 1)\n"
    "r=libc.sysctl(mib,3,buf,ctypes.byref(size),None,0)\n"
    "print(r0, r, b'marker-xyz' in buf.raw)\n"
)
_SLEEPER = [sys.executable, "-I", "-c", "import time;time.sleep(60)"]


def _in_new_session(
    profile_text: str | None, tmp_path: Path, argv: list[str]
) -> subprocess.CompletedProcess[str]:
    """Run like the harness does: confined, in a session of its own."""
    if profile_text is not None:
        sb = tmp_path / "proc.sb"
        sb.write_text(profile_text, encoding="utf-8")
        argv = ["sandbox-exec", "-f", str(sb), *argv]
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        check=False,
        cwd="/",
        start_new_session=True,
        timeout=90,
    )


def _proc_profile(tmp_path: Path) -> str:
    return sandbox.profile(
        Path.home(), tmp_path / "pp", False, read=[Path(sys.prefix)]
    )


@confinement
def test_another_process_arguments_and_environment_cannot_be_read(
    tmp_path: Path,
) -> None:
    target = subprocess.Popen(
        _SLEEPER, env={**os.environ, "PERFPROOF_MARKER": "marker-xyz-123"}
    )
    try:
        time.sleep(0.5)
        read = [sys.executable, "-I", "-c", _PROCARGS, str(target.pid)]
        control = _in_new_session(None, tmp_path, read)
        assert control.stdout.split()[-1] == "True", "the control must read it"
        got = _in_new_session(_proc_profile(tmp_path), tmp_path, read)
        assert got.stdout.split()[:2] == ["-1", "-1"], got.stderr
        assert got.stdout.split()[-1] == "False"
    finally:
        target.kill()
        target.wait()


@confinement
def test_the_agents_own_children_stay_inspectable(tmp_path: Path) -> None:
    own = (
        "import subprocess,sys;"
        "p=subprocess.Popen([sys.executable,'-I','-c','import time;"
        "time.sleep(3)'],env={'MARK':'marker-xyz-own'});"
        + _PROCARGS.replace("int(sys.argv[1])", "p.pid")
        .replace("import ctypes,sys\n", "import ctypes\n")
        .replace("\n", ";")
        + "p.kill()"
    )
    got = _in_new_session(
        _proc_profile(tmp_path), tmp_path, [sys.executable, "-I", "-c", own]
    )
    assert got.stdout.split() == ["0", "0", "True"], got.stderr
    pool = _in_new_session(
        _proc_profile(tmp_path),
        tmp_path,
        [
            sys.executable,
            "-I",
            "-c",
            "import multiprocessing as m;print(m.Pool(2).map(abs,[-1,-2]))",
        ],
    )
    assert pool.stdout.strip() == "[1, 2]", pool.stderr
    shell = _in_new_session(
        _proc_profile(tmp_path),
        tmp_path,
        [
            "sh",
            "-c",
            "sleep 1 & wait; echo w$?; sleep 5 & p=$!; kill $p;"
            " wait $p 2>/dev/null; echo killed",
        ],
    )
    assert shell.stdout.split() == ["w0", "killed"], shell.stderr


@confinement
def test_sample_and_vmmap_work_on_own_children_only(tmp_path: Path) -> None:
    target = subprocess.Popen(_SLEEPER)
    try:
        time.sleep(0.5)
        sample = "sample {pid} 1 -file /dev/stdout 2>&1 | grep -c 'Call graph'"
        vmmap = "vmmap {pid} 2>&1 | grep -c 'Virtual Memory Map'"
        control = _in_new_session(
            None,
            tmp_path,
            ["sh", "-c", sample.format(pid=target.pid)],
        )
        if control.stdout.strip() != "1":
            pytest.skip("sample cannot sample a process here")
        text = _proc_profile(tmp_path)
        for tool in (sample, vmmap):
            other = _in_new_session(
                text, tmp_path, ["sh", "-c", tool.format(pid=target.pid)]
            )
            assert other.stdout.strip() == "0", tool
            own = _in_new_session(
                text,
                tmp_path,
                ["sh", "-c", "sleep 3 & p=$!; " + tool.format(pid="$p")],
            )
            assert own.stdout.strip() == "1", (tool, own.stderr)
    finally:
        target.kill()
        target.wait()


@confinement
def test_the_auth_probe_runs_in_a_session_of_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(
        argv: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "ok\n", "")

    monkeypatch.setattr(sandbox, "claude_oauth_token", lambda: "tok-secret-1")
    monkeypatch.setattr(skill_eval.subprocess, "run", fake_run)
    record = skill_eval.auth_probe(tmp_path / "probes", tmp_path / "w", False)
    assert seen["start_new_session"] is True
    assert "tok-secret-1" not in record.read_text(encoding="utf-8")
