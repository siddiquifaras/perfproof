import importlib
import io
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from perfproof import commands
from perfproof.bench.spec import load_spec
from perfproof.cli import COMMANDS, HELP, available_commands, main
from perfproof.commands import doctor
from perfproof.machine import machine_warnings
from perfproof.schema import BenchmarkSpec, MachineFingerprint

ROOT = Path(__file__).resolve().parents[1]
BUILT = (
    "bench",
    "compare",
    "doctor",
    "init",
    "log",
    "measure",
    "profile",
    "project",
)
NOT_BUILT = tuple(c for c in COMMANDS if c not in BUILT)

type Run = Callable[..., tuple[int, str, str]]


@pytest.fixture
def cli(capsys: pytest.CaptureFixture[str]) -> Run:
    def run(*argv: str) -> tuple[int, str, str]:
        status = main(list(argv))
        out, err = capsys.readouterr()
        return status, out, err

    return run


def as_json(out: str) -> dict[str, Any]:
    document: Any = json.loads(out)
    assert isinstance(document, dict)
    return cast("dict[str, Any]", document)


def documented_help() -> str:
    text = (ROOT / "docs" / "cli.md").read_text(encoding="utf-8")
    section = text.split("## `perfproof --help`", 1)[1]
    return section.split("```\n", 2)[1].rstrip("\n")


# Help


def test_help_text_is_the_one_in_docs() -> None:
    assert documented_help() == HELP


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_prints_the_text_and_lists_what_is_not_built(
    cli: Run, flag: str
) -> None:
    status, out, err = cli(flag)
    assert (status, err) == (0, "")
    assert out.startswith(HELP + "\n\nNot built yet in perfproof ")
    footer = " ".join(out[len(HELP) :].split())
    for name in NOT_BUILT:
        assert f" {name}," in footer or f" {name}." in footer
    for name in BUILT:
        assert f" {name}," not in footer
        assert f" {name}." not in footer


def test_help_lists_exactly_the_commands() -> None:
    listed = [
        line.split()[0]
        for line in HELP.splitlines()
        if line.startswith("  ") and not line.startswith(("   ", "  --"))
    ]
    assert tuple(listed) == COMMANDS


def test_built_commands_are_found_by_name() -> None:
    assert set(BUILT) <= set(available_commands())
    assert set(available_commands()) & set(NOT_BUILT) == set()


@pytest.mark.parametrize("command", BUILT)
def test_command_help(cli: Run, command: str) -> None:
    status, out, _ = cli(command, "--help")
    assert status == 0
    assert out.startswith(f"usage: perfproof {command}")
    assert "--json" in out
    assert cli("-h", command)[:2] == (0, out)


# Dispatch errors


def test_no_command_is_a_usage_error(cli: Run) -> None:
    status, out, err = cli()
    assert (status, out) == (2, "")
    assert err == "perfproof: error: name a command; see perfproof --help\n"
    status, out, _ = cli("--json")
    assert status == 2
    assert as_json(out) == {
        "command": None,
        "ok": False,
        "error": {
            "code": "usage",
            "message": "name a command; see perfproof --help",
            "details": {},
        },
    }


def test_unknown_top_level_option(cli: Run) -> None:
    status, out, err = cli("--bogus")
    assert (status, out) == (2, "")
    assert "unrecognized arguments: --bogus" in err
    status, out, _ = cli("--bogus", "--json")
    assert status == 2
    assert as_json(out)["error"]["code"] == "usage"


def test_unknown_command(cli: Run) -> None:
    status, out, err = cli("frobnicate")
    assert (status, out) == (2, "")
    assert "unknown command 'frobnicate'" in err
    status, out, _ = cli("frobnicate", "--json")
    assert status == 2
    document = as_json(out)
    assert document["command"] is None
    assert document["error"]["code"] == "unknown_command"
    assert document["error"]["details"] == {"command": "frobnicate"}


@pytest.mark.parametrize("command", NOT_BUILT)
def test_unbuilt_command_fails_and_says_so(cli: Run, command: str) -> None:
    status, out, err = cli(command)
    assert (status, out) == (1, "")
    assert err == (
        f"perfproof {command}: error: perfproof {command} is not built "
        "yet in perfproof 0.1.0.\n"
    )
    for argv in ((command, "--json"), ("--json", command, "x")):
        status, out, err = cli(*argv)
        assert (status, err) == (1, "")
        document = as_json(out)
        assert document["command"] == command
        assert document["ok"] is False
        assert document["error"]["code"] == "not_built"


@pytest.mark.parametrize(
    "argv",
    [
        ("doctor",),
        ("bench", "list", "tile"),
        ("compare", "tile", "--candidate", "."),
        ("profile", "tile"),
    ],
    ids=("doctor", "bench", "compare", "profile"),
)
def test_unknown_command_option(cli: Run, argv: tuple[str, ...]) -> None:
    status, out, err = cli(*argv, "--bogus")
    assert (status, out) == (2, "")
    assert err == (
        f"perfproof {argv[0]}: error: unrecognized arguments: --bogus\n"
    )
    for args in ((*argv, "--bogus", "--json"), ("--json", *argv, "--bogus")):
        status, out, _ = cli(*args)
        assert status == 2
        document = as_json(out)
        assert document["command"] == argv[0]
        assert document["error"]["code"] == "usage"


# Registry: a new module is a new command, with no other edit

HELLO = """\
import argparse

from perfproof.commands import CommandError, Output


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--who", default="world")


def run(args: argparse.Namespace) -> Output:
    if args.who == "nobody":
        raise CommandError("no_one", "nobody to greet", {"who": "nobody"})
    return Output(result={"greeting": f"hello {args.who}"}, text="hi")
"""


@pytest.fixture
def extra_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    directory = tmp_path / "more_commands"
    directory.mkdir()
    monkeypatch.setattr(
        commands, "__path__", [*commands.__path__, str(directory)]
    )
    before = set(sys.modules)
    yield directory
    for name in set(sys.modules) - before:
        if name.startswith(f"{commands.__name__}."):
            del sys.modules[name]


def add_module(directory: Path, name: str, text: str = HELLO) -> None:
    (directory / f"{name}.py").write_text(text)
    importlib.invalidate_caches()


def test_a_new_module_is_a_new_command(cli: Run, extra_commands: Path) -> None:
    add_module(extra_commands, "say_hello")
    assert "say-hello" in available_commands()
    assert cli("say-hello") == (0, "hi\n", "")
    status, out, _ = cli("say-hello", "--who", "you", "--json")
    assert status == 0
    assert as_json(out) == {
        "command": "say-hello",
        "ok": True,
        "result": {"greeting": "hello you"},
    }
    status, out, err = cli("--json", "say-hello", "--who", "nobody")
    assert (status, err) == (1, "")
    assert as_json(out) == {
        "command": "say-hello",
        "ok": False,
        "error": {
            "code": "no_one",
            "message": "nobody to greet",
            "details": {"who": "nobody"},
        },
    }
    status, out, err = cli("say-hello", "--who", "nobody")
    assert (status, out) == (1, "")
    assert err == "perfproof say-hello: error: nobody to greet\n"


def test_a_module_builds_a_listed_command(
    cli: Run, extra_commands: Path
) -> None:
    add_module(extra_commands, "install_skills")
    assert cli("install-skills") == (0, "hi\n", "")
    _, out, _ = cli("--help")
    assert "install-skills" not in out[len(HELP) :]


def test_private_modules_are_not_commands(
    cli: Run, extra_commands: Path
) -> None:
    add_module(extra_commands, "_helper")
    assert "-helper" not in available_commands()
    assert cli("_helper")[0] == 2


def internal_error(out: str, command: str, exception: str) -> str:
    document = as_json(out)
    assert set(document) == {"command", "ok", "error"}
    assert (document["command"], document["ok"]) == (command, False)
    error = document["error"]
    assert error["code"] == "internal_error"
    assert error["details"] == {"exception": exception}
    assert error["message"].startswith(f"perfproof failed: {exception}: ")
    return error["message"]


def test_a_module_without_run_is_an_internal_error(
    cli: Run, extra_commands: Path
) -> None:
    add_module(extra_commands, "broken", "def add_arguments(parser): ...\n")
    status, out, err = cli("broken")
    assert (status, out) == (3, "")
    assert "Traceback (most recent call last)" in err
    assert err.endswith(
        "perfproof broken: error: perfproof failed: TypeError: "
        "perfproof.commands.broken must define add_arguments(parser) and "
        "run(args)\n"
    )
    status, out, err = cli("broken", "--json")
    assert status == 3
    assert "Traceback (most recent call last)" in err
    message = internal_error(out, "broken", "TypeError")
    assert "must define add_arguments(parser) and run(args)" in message


CRASHING = """\
import argparse

from perfproof.commands import Output


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--how", choices=["raise", "unserializable"])


def run(args: argparse.Namespace) -> Output:
    if args.how == "raise":
        raise RuntimeError("boom")
    return Output(result={"value": object()}, text="unreachable")
"""


def test_an_unexpected_exception_is_an_internal_error(
    cli: Run, extra_commands: Path
) -> None:
    add_module(extra_commands, "crash", CRASHING)
    status, out, err = cli("crash", "--how", "raise")
    assert (status, out) == (3, "")
    assert "RuntimeError: boom" in err
    assert err.endswith(
        "perfproof crash: error: perfproof failed: RuntimeError: boom\n"
    )
    status, out, err = cli("--json", "crash", "--how", "raise")
    assert status == 3
    assert "Traceback (most recent call last)" in err
    assert internal_error(out, "crash", "RuntimeError") == (
        "perfproof failed: RuntimeError: boom"
    )


def test_an_unserializable_result_writes_only_the_error(
    cli: Run, extra_commands: Path
) -> None:
    add_module(extra_commands, "crash", CRASHING)
    status, out, err = cli("crash", "--how", "unserializable", "--json")
    assert status == 3
    assert "Traceback (most recent call last)" in err
    message = internal_error(out, "crash", "TypeError")
    assert "object is not JSON serializable" in message


# Help under --json is one document too


def test_top_level_help_as_json(cli: Run) -> None:
    for argv in (("--help", "--json"), ("--json", "-h")):
        status, out, err = cli(*argv)
        assert (status, err) == (0, "")
        document = as_json(out)
        assert document == {
            "command": None,
            "ok": True,
            "result": {"help": document["result"]["help"]},
        }
        assert document["result"]["help"].startswith(HELP + "\n\nNot built")


@pytest.mark.parametrize(
    "argv",
    [("doctor",), ("bench",), ("bench", "define"), ("bench", "show")],
    ids=" ".join,
)
def test_command_help_as_json(cli: Run, argv: tuple[str, ...]) -> None:
    status, text, _ = cli(*argv, "--help")
    assert status == 0
    usage = f"usage: perfproof {' '.join(argv)}"
    assert text.startswith(usage)
    for args in ((*argv, "--help", "--json"), ("--json", *argv, "-h")):
        status, out, err = cli(*args)
        assert (status, err) == (0, "")
        assert as_json(out) == {
            "command": argv[0],
            "ok": True,
            "result": {"help": text.rstrip("\n")},
        }


# Paths that are not UTF-8, and the output encoding

BAD = os.fsdecode(b"bad-\xff-path")
SURROGATE_RESULT = f"""\
import argparse

from perfproof.commands import Output


def add_arguments(parser: argparse.ArgumentParser) -> None:
    pass


def run(args: argparse.Namespace) -> Output:
    return Output(result={{"path": {BAD!r}}}, text={BAD!r})
"""

# (encoding, errors) of stdout and stderr: a strict UTF-8 locale, the C
# locale as Python sets it up (UTF-8 with surrogateescape), and ASCII.
STREAMS = [
    ("utf-8", "strict"),
    ("utf-8", "surrogateescape"),
    ("ascii", "strict"),
]


@pytest.fixture(params=STREAMS, ids=lambda p: f"{p[0]}-{p[1]}")
def raw_cli(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., tuple[int, bytes, bytes]]:
    encoding, errors = cast("tuple[str, str]", request.param)

    def run(*argv: str) -> tuple[int, bytes, bytes]:
        out = io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors=errors)
        err = io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors=errors)
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setattr(sys, "stderr", err)
        status = main(list(argv))
        out.flush()
        err.flush()
        return (
            status,
            out.buffer.getvalue(),
            err.buffer.getvalue(),
        )

    return run


def test_a_surrogate_in_a_result(
    raw_cli: Callable[..., tuple[int, bytes, bytes]], extra_commands: Path
) -> None:
    add_module(extra_commands, "odd_path", SURROGATE_RESULT)
    status, out, err = raw_cli("odd-path", "--json")
    assert (status, err) == (0, b"")
    assert out.isascii()
    assert json.loads(out)["result"] == {"path": BAD}
    status, out, err = raw_cli("odd-path")
    assert (status, err) == (0, b"")
    assert out == b"bad-\\udcff-path\n"


def test_a_surrogate_in_a_command_error(
    raw_cli: Callable[..., tuple[int, bytes, bytes]], tmp_path: Path
) -> None:
    missing = str(tmp_path / BAD)
    status, out, err = raw_cli(
        "bench", "list", "x", "--repo", missing, "--json"
    )
    assert (status, err) == (1, b"")
    assert out.isascii()
    error = json.loads(out)["error"]
    assert error["code"] == "not_a_repository"
    assert error["details"] == {"repo": missing}
    status, out, err = raw_cli("bench", "list", "x", "--repo", missing)
    assert (status, out) == (1, b"")
    err.decode("ascii")
    assert b"bad-\\udcff-path" in err
    assert b"\xff" not in err


@pytest.mark.parametrize(
    "env",
    [
        {"LC_ALL": "C"},
        {"LC_ALL": "C.UTF-8", "PYTHONIOENCODING": "utf-8:strict"},
    ],
    ids=["C", "strict-utf-8"],
)
def test_a_surrogate_under_each_locale(
    tmp_path: Path, env: dict[str, str]
) -> None:
    environ = {
        k: v
        for k, v in os.environ.items()
        if k not in ("LANG", "LC_ALL", "LC_CTYPE", "PYTHONIOENCODING")
        and k != "PYTHONUTF8"
    }
    missing = os.fsencode(tmp_path) + b"/bad-\xff-path"
    base = [sys.executable, "-m", "perfproof", "bench", "list", "x"]
    for extra in ([b"--json"], []):
        done = subprocess.run(
            [*base, "--repo", missing, *extra],
            capture_output=True,
            timeout=60,
            check=False,
            env={**environ, **env},
        )
        assert done.returncode == 1
        if extra:
            assert done.stdout.isascii()
            error = json.loads(done.stdout)["error"]
            assert error["code"] == "not_a_repository"
            assert os.fsencode(error["details"]["repo"]) == missing
        else:
            assert done.stdout == b""
            assert b"bad-\\udcff-path" in done.stderr
            assert b"\xff" not in done.stderr
            done.stderr.decode("utf-8")


# A reader that closes stdout early


@pytest.mark.parametrize("argv", [["--help"], ["--json", "doctor"]])
def test_closed_stdout_exits_quietly(argv: list[str]) -> None:
    read_end, write_end = os.pipe()
    os.close(read_end)
    try:
        done = subprocess.run(
            [sys.executable, "-m", "perfproof", *argv],
            stdout=write_end,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
    finally:
        os.close(write_end)
    assert (done.returncode, done.stderr) == (141, b"")


# A closed or broken stderr leaves the exit status to report the outcome

STDERR_CASES = [
    (["confirm"], 1, b""),
    (["frobnicate"], 2, b""),
    (["bench", "list", "x", "--repo", "/nonexistent/perfproof"], 1, b""),
    (["confirm", "--json"], 1, b'"code": "not_built"'),
]


@pytest.mark.parametrize(("argv", "status", "out"), STDERR_CASES)
def test_closed_stderr(argv: list[str], status: int, out: bytes) -> None:
    closing = ("sh", "-c", 'exec "$@" 2>&-', "sh")
    done = subprocess.run(
        [*closing, sys.executable, "-m", "perfproof", *argv],
        stdout=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    assert done.returncode == status
    assert (out in done.stdout) if out else done.stdout == b""
    assert b"Traceback" not in done.stdout


@pytest.mark.parametrize(("argv", "status", "out"), STDERR_CASES)
def test_broken_stderr_pipe(argv: list[str], status: int, out: bytes) -> None:
    read_end, write_end = os.pipe()
    os.close(read_end)
    try:
        done = subprocess.run(
            [sys.executable, "-m", "perfproof", *argv],
            stdout=subprocess.PIPE,
            stderr=write_end,
            timeout=120,
            check=False,
        )
    finally:
        os.close(write_end)
    assert done.returncode == status
    assert (out in done.stdout) if out else done.stdout == b""


def test_stderr_that_cannot_be_written(
    cli: Run, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # What an emulator leaves when it reuses a closed fd 2 for a file
    # it opened read-only: stderr exists, and every write fails, EBADF.
    target = tmp_path / "read-only"
    target.write_text("")
    stderr = open(  # noqa: SIM115
        os.open(target, os.O_RDONLY), "w", encoding="utf-8"
    )
    monkeypatch.setattr(sys, "stderr", stderr)
    status, out, _ = cli("confirm")
    assert (status, out) == (1, "")
    assert sys.stderr is None
    monkeypatch.setattr(sys, "stderr", stderr)
    status, _, _ = cli("frobnicate")
    assert status == 2
    with pytest.raises(OSError, match="Bad file descriptor"):
        stderr.close()


def test_internal_error_with_stderr_closed(
    cli: Run, extra_commands: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_module(extra_commands, "crash", CRASHING)
    monkeypatch.setattr(sys, "stderr", None)
    status, out, _ = cli("crash", "--how", "raise")
    assert (status, out) == (3, "")
    status, out, _ = cli("crash", "--how", "raise", "--json")
    assert status == 3
    assert internal_error(out, "crash", "RuntimeError")


# Entry points


@pytest.mark.parametrize(
    "argv",
    [
        [sys.executable, "-m", "perfproof", "--help"],
        [str(Path(sys.executable).parent / "perfproof"), "--help"],
    ],
    ids=["module", "script"],
)
def test_entry_points(argv: list[str]) -> None:
    done = subprocess.run(
        argv, capture_output=True, text=True, timeout=60, check=False
    )
    assert (done.returncode, done.stderr) == (0, "")
    assert done.stdout.startswith(HELP + "\n")


def test_entry_point_exit_status() -> None:
    done = subprocess.run(
        [sys.executable, "-m", "perfproof", "confirm", "--json"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 1
    assert json.loads(done.stdout)["error"]["code"] == "not_built"


# doctor

T0 = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
PROBES = tuple(
    f
    for f in MachineFingerprint.model_fields
    if f not in ("captured_at", "missing")
)


def fake_fingerprint(**values: object) -> MachineFingerprint:
    missing = {
        f: f"{f} was not probed in this test"
        for f in PROBES
        if f not in values
    }
    return MachineFingerprint.model_validate(
        {"captured_at": T0, **values, "missing": missing}
    )


def test_doctor_on_this_machine(cli: Run) -> None:
    status, out, err = cli("doctor")
    assert (status, err) == (0, "")
    sections = [line for line in out.splitlines() if line[:1].isalpha()]
    assert sections == ["Machine", "Warnings", "Tools"]
    status, out, err = cli("doctor", "--json")
    assert (status, err) == (0, "")
    document = as_json(out)
    assert set(document) == {"command", "ok", "result"}
    assert (document["command"], document["ok"]) == ("doctor", True)
    result = document["result"]
    assert set(result) == {"machine", "warnings", "tools"}
    fp = MachineFingerprint.model_validate(result["machine"])
    warnings = result["warnings"]
    assert [(w["code"], w["message"]) for w in warnings] == [
        (w.code, w.message) for w in machine_warnings(fp)
    ]
    assert all(isinstance(w["advice"], str) for w in warnings)
    assert set(result["tools"]) == {"git", "perf"}
    for tool in result["tools"].values():
        assert set(tool) == {"available", "used_for", "detail"}
        assert isinstance(tool["available"], bool)
        assert tool["detail"]
    assert result["tools"]["git"]["available"] is True
    if sys.platform != "linux":
        assert result["tools"]["perf"]["available"] is False
        assert "Linux-only" in result["tools"]["perf"]["detail"]


def test_doctor_shows_unknowns_as_unknown(
    cli: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    fp = fake_fingerprint(os="Linux", arch="x86_64")
    monkeypatch.setattr(doctor, "fingerprint", lambda: fp)
    status, out, _ = cli("doctor")
    assert status == 0
    lines = out.splitlines()
    for field in PROBES:
        if field in ("os", "arch"):
            continue
        row = next(line for line in lines if line.split()[:1] == [field])
        assert row.endswith(f"unknown: {field} was not probed in this test")
    assert "  none" in lines
    status, out, _ = cli("doctor", "--json")
    machine = as_json(out)["result"]["machine"]
    assert machine["os"] == "Linux"
    assert machine["cpu_model"] is None
    assert (
        machine["missing"]["cpu_model"]
        == "cpu_model was not probed in this test"
    )
    assert as_json(out)["result"]["warnings"] == []


@pytest.mark.parametrize(
    ("values", "codes"),
    [
        (
            {
                "power_source": "battery",
                "battery_percent": 41.0,
                "logical_cores": 2,
                "load_average": (9.0, 9.0, 9.0),
                "frequency_policy": "scaling_governor=powersave",
                "thermal_state": "processor_cooling_state=2",
            },
            [
                "on_battery",
                "high_load",
                "non_performance_governor",
                "thermal_limit",
            ],
        ),
        ({"frequency_policy": "lowpowermode=1"}, ["low_power_mode"]),
    ],
)
def test_doctor_gives_advice_for_every_warning(
    cli: Run,
    monkeypatch: pytest.MonkeyPatch,
    values: dict[str, object],
    codes: list[str],
) -> None:
    fp = fake_fingerprint(**values)
    monkeypatch.setattr(doctor, "fingerprint", lambda: fp)
    status, out, _ = cli("doctor", "--json")
    assert status == 0
    warnings = as_json(out)["result"]["warnings"]
    assert [w["code"] for w in warnings] == codes
    assert all(isinstance(w["advice"], str) and w["advice"] for w in warnings)
    _, out, _ = cli("doctor")
    for w in warnings:
        assert f"  {w['code']}: {w['message']}" in out
        assert f"    Advice: {w['advice']}" in out


def test_doctor_reports_missing_git(
    cli: Run, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    status, out, _ = cli("doctor", "--json")
    assert status == 0
    assert as_json(out)["result"]["tools"]["git"] == {
        "available": False,
        "used_for": "baseline snapshots as git worktrees",
        "detail": "git is not on PATH",
    }


# bench


@pytest.fixture(autouse=True)
def isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "gitconfig"
    config.write_text(
        "[user]\n\tname = Test\n\temail = test@example.invalid\n"
        "[commit]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        timeout=60,
    )


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


SPEC: dict[str, Any] = {
    "command": ["python", "bench.py"],
    "metric": {"kind": "wall", "direction": "lower_is_better", "unit": "s"},
    "correctness": {
        "command": ["python", "-m", "pytest"],
        "timeout_seconds": 60.0,
    },
    "warmup": 1,
    "budget": {"max_rounds": 20, "max_seconds": 600.0},
    "looks": {"n0": 10, "k": 10, "max_looks": 3},
    "timeout_seconds": 30.0,
    "harness": ["bench.py"],
    "oracle": ["tests"],
    "inputs": ["data"],
    "config": ["config.toml"],
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    write(root / "bench.py", "print(1)\n")
    write(root / "tests" / "test_tile.py", "def test_ok():\n    pass\n")
    write(root / "tests" / "golden" / "out.txt", "42\n")
    write(root / "data" / "input.csv", "a,b\n1,2\n")
    write(root / "config.toml", "[tile]\nsize = 256\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "baseline")
    return root


def spec_file(tmp_path: Path, fields: dict[str, Any], name: str = "s") -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(fields))
    return path


def spec_hash(fields: dict[str, Any], name: str = "tile") -> str:
    return BenchmarkSpec.model_validate_json(
        json.dumps({"name": name, **fields})
    ).spec_hash


def define(
    cli: Run, repo: Path, spec: Path, *extra: str, name: str = "tile"
) -> tuple[int, str, str]:
    return cli(
        "bench",
        "define",
        name,
        "--spec",
        str(spec),
        "--repo",
        str(repo),
        *extra,
    )


def test_bench_define(cli: Run, repo: Path, tmp_path: Path) -> None:
    expected = spec_hash(SPEC)
    status, out, err = define(cli, repo, spec_file(tmp_path, SPEC))
    assert (status, err) == (0, "")
    record = repo / ".perfproof" / "benchmarks" / "tile" / f"{expected}.json"
    assert out == (
        f"Spec {expected} locked: bench.py, tests/ (2 files), "
        f"data/ (1 file), config.toml\nRecord: {record}\n"
    )
    spec, lock = load_spec(repo, "tile", expected)
    assert spec.command == ("python", "bench.py")
    assert [f.path for f in lock.files] == [
        "bench.py",
        "config.toml",
        "data/input.csv",
        "tests/golden/out.txt",
        "tests/test_tile.py",
    ]
    exclude = repo / ".git" / "info" / "exclude"
    assert ".perfproof/" in exclude.read_text().splitlines()


def test_bench_define_json(cli: Run, repo: Path, tmp_path: Path) -> None:
    expected = spec_hash(SPEC)
    status, out, err = define(cli, repo, spec_file(tmp_path, SPEC), "--json")
    assert (status, err) == (0, "")
    document = as_json(out)
    assert (document["command"], document["ok"]) == ("bench", True)
    result = document["result"]
    assert set(result) == {
        "action",
        "name",
        "spec_hash",
        "path",
        "spec",
        "lock",
        "new",
    }
    spec, lock = load_spec(repo, "tile", expected)
    assert result["action"] == "define"
    assert result["name"] == "tile"
    assert result["spec_hash"] == expected
    assert result["new"] is True
    assert result["path"] == str(
        repo / ".perfproof" / "benchmarks" / "tile" / f"{expected}.json"
    )
    assert result["spec"] == spec.model_dump(mode="json")
    assert result["lock"] == lock.model_dump(mode="json")


def test_bench_define_again_changes_nothing(
    cli: Run, repo: Path, tmp_path: Path
) -> None:
    path = spec_file(tmp_path, SPEC)
    define(cli, repo, path)
    record = (
        repo / ".perfproof" / "benchmarks" / "tile" / f"{spec_hash(SPEC)}.json"
    )
    before = record.read_bytes()
    status, out, _ = define(cli, repo, path)
    assert status == 0
    assert out.startswith(
        f"Spec {spec_hash(SPEC)} was already defined with the same lock; "
        "nothing changed.\n"
    )
    status, out, _ = define(cli, repo, path, "--json")
    assert status == 0
    assert as_json(out)["result"]["new"] is False
    assert record.read_bytes() == before


def test_bench_define_from_stdin(
    cli: Run, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(SPEC)))
    status, out, _ = cli(
        "bench", "define", "tile", "--spec", "-", "--repo", str(repo), "--json"
    )
    assert status == 0
    assert as_json(out)["result"]["spec_hash"] == spec_hash(SPEC)


def test_bench_define_from_a_subdirectory(
    cli: Run, repo: Path, tmp_path: Path
) -> None:
    status, out, _ = define(
        cli, repo / "tests", spec_file(tmp_path, SPEC), "--json"
    )
    assert status == 0
    assert as_json(out)["result"]["path"].startswith(str(repo / ".perfproof"))


def test_a_changed_spec_is_a_new_file(
    cli: Run, repo: Path, tmp_path: Path
) -> None:
    define(cli, repo, spec_file(tmp_path, SPEC))
    changed = {**SPEC, "warmup": 2}
    status, _, _ = define(cli, repo, spec_file(tmp_path, changed, "b"))
    assert status == 0
    stored = sorted(
        p.stem for p in (repo / ".perfproof" / "benchmarks" / "tile").iterdir()
    )
    assert stored == sorted([spec_hash(SPEC), spec_hash(changed)])


def error_of(out: str) -> dict[str, Any]:
    document = as_json(out)
    assert document["ok"] is False
    assert document["command"] == "bench"
    error = document["error"]
    assert set(error) == {"code", "message", "details"}
    return error


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{not json", "is not JSON"),
        ("[1, 2]", "must be a JSON object"),
        (
            json.dumps({**SPEC, "name": "other"}),
            "is named 'other', not 'tile'",
        ),
        (json.dumps({**SPEC, "warmup": -1}), "is not a valid benchmark spec"),
        (json.dumps({**SPEC, "bogus": 1}), "is not a valid benchmark spec"),
    ],
)
def test_bench_define_refuses_a_bad_spec(
    cli: Run, repo: Path, tmp_path: Path, text: str, message: str
) -> None:
    path = tmp_path / "bad.json"
    path.write_text(text)
    status, out, err = define(cli, repo, path)
    assert (status, out) == (1, "")
    assert err.startswith("perfproof bench: error: the spec in ")
    assert message in err
    status, out, _ = define(cli, repo, path, "--json")
    assert status == 1
    error = error_of(out)
    assert error["code"] == "invalid_spec"
    assert message in error["message"]
    assert error["details"] == {"spec": str(path)}
    assert not (repo / ".perfproof").exists()


def test_bench_define_refuses_an_unreadable_spec(
    cli: Run, repo: Path, tmp_path: Path
) -> None:
    status, out, _ = define(cli, repo, tmp_path / "absent.json", "--json")
    assert status == 1
    assert error_of(out)["code"] == "invalid_spec"


def test_bench_define_refuses_a_missing_declared_path(
    cli: Run, repo: Path, tmp_path: Path
) -> None:
    fields = {**SPEC, "inputs": ["data", "more-data"]}
    status, out, _ = define(cli, repo, spec_file(tmp_path, fields), "--json")
    assert status == 1
    error = error_of(out)
    assert error["code"] == "lock_failed"
    assert "more-data" in error["message"]
    assert error["details"] == {"spec_hash": spec_hash(fields)}
    assert not (repo / ".perfproof" / "benchmarks").exists()


def test_bench_define_never_overwrites_a_lock(
    cli: Run, repo: Path, tmp_path: Path
) -> None:
    path = spec_file(tmp_path, SPEC)
    define(cli, repo, path)
    write(repo / "bench.py", "print(2)\n")
    status, out, _ = define(cli, repo, path, "--json")
    assert status == 1
    error = error_of(out)
    assert error["code"] == "spec_conflict"
    assert "never overwritten" in error["message"]
    _, lock = load_spec(repo, "tile", spec_hash(SPEC))
    bench = next(f for f in lock.files if f.path == "bench.py")
    assert bench.size_bytes == len("print(1)\n")


def test_bench_outside_a_repository(cli: Run, tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    for argv in (
        ("define", "tile", "--spec", str(spec_file(tmp_path, SPEC))),
        ("list", "tile"),
        ("show", "tile", "ab"),
    ):
        status, out, err = cli("bench", *argv, "--repo", str(plain))
        assert (status, out) == (1, "")
        assert "is not in a git working tree" in err
        status, out, _ = cli("bench", *argv, "--repo", str(plain), "--json")
        assert status == 1
        error = error_of(out)
        assert error["code"] == "not_a_repository"
        assert error["details"] == {"repo": str(plain)}


@pytest.mark.parametrize("action", ["define", "list", "show"])
def test_bench_refuses_a_bad_name(
    cli: Run, repo: Path, tmp_path: Path, action: str
) -> None:
    extra = {
        "define": ["--spec", str(spec_file(tmp_path, SPEC))],
        "list": [],
        "show": ["ab"],
    }[action]
    status, out, _ = cli(
        "bench", action, "../up", *extra, "--repo", str(repo), "--json"
    )
    assert status == 1
    error = error_of(out)
    assert error["code"] == "invalid_name"
    assert error["details"] == {"name": "../up"}


def test_bench_needs_an_action(cli: Run) -> None:
    status, out, err = cli("bench")
    assert (status, out) == (2, "")
    assert "ACTION" in err
    status, out, _ = cli("bench", "--json")
    assert status == 2
    assert error_of(out)["code"] == "usage"


def test_bench_list(cli: Run, repo: Path, tmp_path: Path) -> None:
    changed = {**SPEC, "warmup": 2}
    define(cli, repo, spec_file(tmp_path, SPEC))
    define(cli, repo, spec_file(tmp_path, changed, "b"))
    hashes = sorted([spec_hash(SPEC), spec_hash(changed)])
    status, out, err = cli("bench", "list", "tile", "--repo", str(repo))
    assert (status, err) == (0, "")
    assert out == (
        "Benchmark tile: versions by hash\n"
        + "".join(f"  {h}  5 locked files\n" for h in hashes)
    )
    status, out, _ = cli(
        "bench", "list", "tile", "--repo", str(repo), "--json"
    )
    assert status == 0
    directory = repo / ".perfproof" / "benchmarks" / "tile"
    assert as_json(out)["result"] == {
        "action": "list",
        "name": "tile",
        "versions": [
            {
                "spec_hash": h,
                "path": str(directory / f"{h}.json"),
                "locked_files": 5,
            }
            for h in hashes
        ],
    }


def test_bench_list_ignores_temporary_files(
    cli: Run, repo: Path, tmp_path: Path
) -> None:
    define(cli, repo, spec_file(tmp_path, SPEC))
    directory = repo / ".perfproof" / "benchmarks" / "tile"
    (directory / f".{spec_hash(SPEC)}.abc.tmp").write_text("partial")
    status, out, _ = cli(
        "bench", "list", "tile", "--repo", str(repo), "--json"
    )
    assert status == 0
    assert len(as_json(out)["result"]["versions"]) == 1


def test_bench_list_of_an_undefined_benchmark(cli: Run, repo: Path) -> None:
    status, out, err = cli("bench", "list", "tile", "--repo", str(repo))
    assert (status, out) == (1, "")
    assert "no spec is defined for benchmark 'tile'" in err
    status, out, _ = cli(
        "bench", "list", "tile", "--repo", str(repo), "--json"
    )
    assert status == 1
    error = error_of(out)
    assert error["code"] == "not_found"
    assert error["details"] == {"name": "tile"}


def test_bench_list_refuses_a_bad_record(
    cli: Run, repo: Path, tmp_path: Path
) -> None:
    define(cli, repo, spec_file(tmp_path, SPEC))
    bad = repo / ".perfproof" / "benchmarks" / "tile" / f"{'f' * 64}.json"
    bad.write_text("{}")
    status, out, _ = cli(
        "bench", "list", "tile", "--repo", str(repo), "--json"
    )
    assert status == 1
    error = error_of(out)
    assert error["code"] == "invalid_record"
    assert error["details"] == {"path": str(bad)}


def test_bench_define_when_the_state_dir_is_a_file(
    cli: Run, repo: Path, tmp_path: Path
) -> None:
    (repo / ".perfproof").write_text("not a directory\n")
    path = spec_file(tmp_path, SPEC)
    status, out, err = define(cli, repo, path)
    assert (status, out) == (1, "")
    assert err.startswith(f"perfproof bench: error: {repo / '.perfproof'}")
    assert "Traceback" not in err
    status, out, err = define(cli, repo, path, "--json")
    assert (status, err) == (1, "")
    error = error_of(out)
    assert error["code"] == "state_write_failed"
    assert error["details"]["path"].startswith(str(repo / ".perfproof"))
    assert (repo / ".perfproof").read_text() == "not a directory\n"


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root reads a directory without permission"
)
@pytest.mark.parametrize("locked", ["benchmarks", "benchmarks/tile"])
def test_bench_define_in_an_unreadable_directory(
    cli: Run, repo: Path, tmp_path: Path, locked: str
) -> None:
    define(cli, repo, spec_file(tmp_path, SPEC))
    directory = repo / ".perfproof" / locked
    directory.chmod(0)
    changed = spec_file(tmp_path, {**SPEC, "warmup": 2}, "b")
    try:
        status, out, err = define(cli, repo, changed)
        assert (status, out) == (1, "")
        assert "Traceback" not in err
        assert "Permission denied" in err
        status, out, err = define(cli, repo, changed, "--json")
        assert (status, err) == (1, "")
        error = error_of(out)
        assert error["code"] == "state_write_failed"
        assert error["details"]["path"].startswith(str(directory))
    finally:
        directory.chmod(0o755)


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root reads a directory without permission"
)
def test_bench_list_of_an_unreadable_directory(
    cli: Run, repo: Path, tmp_path: Path
) -> None:
    define(cli, repo, spec_file(tmp_path, SPEC))
    directory = repo / ".perfproof" / "benchmarks" / "tile"
    directory.chmod(0)
    try:
        for action in (("list", "tile"), ("show", "tile", "ab")):
            status, out, err = cli(
                "bench", *action, "--repo", str(repo), "--json"
            )
            assert (status, err) == (1, "")
            error = error_of(out)
            assert error["code"] == "state_read_failed"
            assert error["details"] == {"path": str(directory)}
            assert "Permission denied" in error["message"]
    finally:
        directory.chmod(0o755)


def test_bench_list_of_an_unreadable_record(
    cli: Run, repo: Path, tmp_path: Path
) -> None:
    define(cli, repo, spec_file(tmp_path, SPEC))
    bad = repo / ".perfproof" / "benchmarks" / "tile" / f"{'f' * 64}.json"
    bad.mkdir()
    status, out, err = cli("bench", "list", "tile", "--repo", str(repo))
    assert (status, out) == (1, "")
    assert err.startswith(f"perfproof bench: error: {bad}: ")
    status, out, _ = cli(
        "bench", "list", "tile", "--repo", str(repo), "--json"
    )
    assert status == 1
    error = error_of(out)
    assert error["code"] == "state_read_failed"
    assert error["details"] == {"path": str(bad)}


@pytest.mark.parametrize("length", [64, 6])
def test_bench_show(cli: Run, repo: Path, tmp_path: Path, length: int) -> None:
    define(cli, repo, spec_file(tmp_path, SPEC))
    full = spec_hash(SPEC)
    spec, lock = load_spec(repo, "tile", full)
    record = repo / ".perfproof" / "benchmarks" / "tile" / f"{full}.json"
    status, out, err = cli(
        "bench", "show", "tile", full[:length], "--repo", str(repo)
    )
    assert (status, err) == (0, "")
    lines = out.splitlines()
    assert lines[:3] == [
        f"Spec {full} of tile",
        f"Record: {record}",
        "Locked: bench.py, tests/ (2 files), data/ (1 file), config.toml",
    ]
    assert json.loads("\n".join(lines[3:])) == spec.model_dump(mode="json")
    status, out, _ = cli(
        "bench", "show", "tile", full[:length], "--repo", str(repo), "--json"
    )
    assert status == 0
    assert as_json(out)["result"] == {
        "action": "show",
        "name": "tile",
        "spec_hash": full,
        "path": str(record),
        "spec": spec.model_dump(mode="json"),
        "lock": lock.model_dump(mode="json"),
    }


@pytest.mark.parametrize("prefix", ["0000000", "xyz", "ABCD"])
def test_bench_show_unknown_hash(
    cli: Run, repo: Path, tmp_path: Path, prefix: str
) -> None:
    define(cli, repo, spec_file(tmp_path, SPEC))
    if spec_hash(SPEC).startswith(prefix):
        pytest.skip("the prefix happens to match")
    status, out, _ = cli(
        "bench", "show", "tile", prefix, "--repo", str(repo), "--json"
    )
    assert status == 1
    error = error_of(out)
    assert error["code"] == "not_found"
    assert error["details"] == {"name": "tile"}


def test_bench_show_ambiguous_prefix(
    cli: Run, repo: Path, tmp_path: Path
) -> None:
    by_first: dict[str, list[str]] = {}
    for warmup in range(17):
        fields = {**SPEC, "warmup": warmup}
        define(cli, repo, spec_file(tmp_path, fields, str(warmup)))
        full = spec_hash(fields)
        by_first.setdefault(full[0], []).append(full)
    first, shared = next((k, v) for k, v in by_first.items() if len(v) > 1)
    status, out, _ = cli(
        "bench", "show", "tile", first, "--repo", str(repo), "--json"
    )
    assert status == 1
    error = error_of(out)
    assert error["code"] == "ambiguous_hash"
    assert error["details"] == {"name": "tile", "matches": sorted(shared)}
