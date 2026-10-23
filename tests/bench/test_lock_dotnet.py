"""The runtime gate for .NET: the SDK, the runtimes and a hash.

The tests that need .NET run the real ``dotnet`` and skip with their
reason where there is none on the ``PATH`` (this Mac has none: they run
in the official SDK image, see ``tests/fixtures/dotnet/SOURCE.md``).
The others read the committed captures of that image's ``dotnet`` and
use empty executables named ``dotnet``, which the gate never starts.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from test_lock_runtimes import gate, make_spec, probe, tree

from perfproof.bench.lock import fetching_launch, hash_file

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "dotnet"


def real_dotnet() -> Path:
    found = shutil.which("dotnet")
    if found is None:
        pytest.skip("no dotnet on the PATH")
    done = subprocess.run(
        [found, "--list-runtimes"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if done.returncode != 0:
        pytest.skip(f"dotnet is not usable here: {done.stderr[:100]!r}")
    return Path(found)


def says(*args: str, cwd: Path | None = None) -> str:
    done = subprocess.run(
        [str(real_dotnet()), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
        cwd=cwd,
    )
    return done.stdout.strip()


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    [
        ("dotnet", "build", "bench.csproj"),
        ("dotnet", "run", "--project", "bench.csproj"),
        ("dotnet", "test", "bench.csproj"),
    ],
)
def test_the_sdk_and_runtimes_are_what_dotnet_says(
    tmp_path: Path, command: tuple[str, ...]
) -> None:
    host = real_dotnet().resolve()
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    spec = make_spec(command)

    env = probe("baseline", spec, base)
    result = gate(spec, base, cand)

    assert result.status == "passed", result.detail
    assert env.runtime_kind == "interpreter"
    assert env.runtime_executable == str(host)
    assert env.runtime_version is not None
    sdk = says("--version", cwd=base)
    assert env.runtime_version.startswith(f"sdk {sdk}; runtimes ")
    for line in says("--list-runtimes").splitlines():
        name, version = line.split()[:2]
        assert f"{name} {version}" in env.runtime_version
    assert "[" not in env.runtime_version
    digest, _ = hash_file(host)
    assert env.runtime_version.endswith(f"; sha256 {digest}")


@pytest.mark.integration
def test_a_dll_or_exec_asks_only_for_the_runtimes(tmp_path: Path) -> None:
    real_dotnet()
    base = tree(tmp_path, "a")
    for command in (
        ("dotnet", "bin/App.dll"),
        ("dotnet", "exec", "bin/App.dll"),
    ):
        env = probe("baseline", make_spec(command), base)
        assert env.runtime_kind == "interpreter"
        assert env.runtime_version is not None
        assert env.runtime_version.startswith("runtimes ")


@pytest.mark.integration
def test_a_global_json_that_asks_for_a_missing_sdk_is_unknown(
    tmp_path: Path,
) -> None:
    real_dotnet()
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    for root in (base, cand):
        (root / "global.json").write_text(
            '{"sdk": {"version": "1.0.0", "rollForward": "disable"}}'
        )
    spec = make_spec(("dotnet", "build", "bench.csproj"))

    result = gate(spec, base, cand)

    assert result.status == "not_available"
    assert "could not report its sdk version" in result.detail


@pytest.mark.integration
def test_a_global_json_that_pins_another_sdk_than_the_other_arm_fails(
    tmp_path: Path,
) -> None:
    """The arms differ only in the SDK their ``global.json`` selects."""
    real_dotnet()
    sdks = says("--list-sdks").splitlines()
    if len(sdks) < 2:
        pytest.skip("only one SDK is installed")
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    for root, line in ((base, sdks[0]), (cand, sdks[1])):
        version = line.split()[0]
        (root / "global.json").write_text(
            f'{{"sdk": {{"version": "{version}", "rollForward": "disable"}}}}'
        )
    spec = make_spec(("dotnet", "build", "bench.csproj"))

    assert gate(spec, base, cand).status == "failed"


@pytest.mark.integration
def test_other_subcommands_are_unknown(tmp_path: Path) -> None:
    real_dotnet()
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    spec = make_spec(("dotnet", "tool", "run", "thing"))
    result = gate(spec, base, cand)
    assert result.status == "not_available"
    assert "only dotnet build, run, test and exec" in result.detail


def stand_in(directory: Path, name: str, text: str = "") -> Path:
    path = directory / name
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_a_dotnet_script_is_not_asked(tmp_path: Path) -> None:
    shims = tmp_path / "shims"
    shims.mkdir()
    stand_in(shims, "dotnet", "#!/bin/sh\nexec /usr/bin/true\n")
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    spec = make_spec(("dotnet", "build", "bench.csproj"))
    path = f"{shims}{os.pathsep}{os.environ['PATH']}"

    result = gate(spec, base, cand, path=path)

    assert result.status == "not_available"


@pytest.mark.parametrize(
    ("argv", "word"),
    [
        (("dotnet", "tool", "install", "-g", "x"), "dotnet tool install"),
        (("dotnet", "tool", "update", "x"), "dotnet tool update"),
        (("dotnet", "tool", "exec", "x"), "dotnet tool exec"),
        (("dotnet", "dnx", "x"), "dotnet dnx"),
        (("dotnet", "workload", "install", "x"), "dotnet workload install"),
        (("dotnet", "add", "package", "x"), "dotnet add"),
        (("dotnet", "restore"), "dotnet restore"),
        (("dotnet", "package", "add", "x"), "dotnet package"),
        (("dotnet", "--verbosity", "q", "restore"), "dotnet restore"),
        (("dotnet", "run", "app.cs"), "#:package"),
    ],
)
def test_downloads_are_fetching_launches(
    tmp_path: Path, argv: tuple[str, ...], word: str
) -> None:
    stand_in(tmp_path, "dotnet")
    env = {"PATH": str(tmp_path)}
    reason = fetching_launch(argv, env, tmp_path)
    assert reason is not None
    assert word in reason


@pytest.mark.parametrize(
    "argv",
    [
        ("dotnet", "build", "x.csproj"),
        ("dotnet", "run", "--project", "x.csproj"),
        ("dotnet", "test"),
        ("dotnet", "bin/App.dll"),
        ("dotnet", "tool", "list"),
    ],
)
def test_building_and_running_are_not_fetching_launches(
    tmp_path: Path, argv: tuple[str, ...]
) -> None:
    stand_in(tmp_path, "dotnet")
    env = {"PATH": str(tmp_path)}
    assert fetching_launch(argv, env, tmp_path) is None


def test_the_captured_host_reports_what_the_gate_reads() -> None:
    """The fixture's ``--list-runtimes`` has the two-field form."""
    lines = (FIXTURES / "list-runtimes.txt").read_text().splitlines()
    assert lines
    for line in lines:
        assert len(line.split("[")[0].split()) == 2
    version = (FIXTURES / "dotnet-version.txt").read_text().strip()
    sdks = (FIXTURES / "list-sdks.txt").read_text()
    assert sdks.startswith(version)
