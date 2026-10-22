"""The runtime gate for Go: the toolchain and the executables it builds.

The tests that need Go run the real ``go`` and skip with their reason
where there is none on the ``PATH`` (this Mac has none: they run in the
official ``golang`` image, see ``tests/fixtures/go/SOURCE.md``). The
others read the committed captures of real runs in
``tests/fixtures/go``.
"""

import json
import os
import shutil
import subprocess
import tracemalloc
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from test_lock_runtimes import gate, make_spec, probe, tree

from perfproof.bench import lock as lock_module
from perfproof.bench.gobuildinfo import go_build_version
from perfproof.bench.lock import fetching_launch
from perfproof.schema import GateResult

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "go"
REGION = FIXTURES / "hot-buildinfo-region.bin"


class AnswerLaunch(lock_module._Launch):  # pyright: ignore[reportPrivateUsage]
    """A launch whose version probe prints a fixed answer."""

    def __init__(self, root: Path, answers: Mapping[str, str]) -> None:
        super().__init__(root, root, 30.0)
        self.answers = answers

    def report(
        self, argv: Sequence[str], env: Mapping[str, str]
    ) -> tuple[str, str, str | None]:
        return self.answers[argv[1]], "", None


def no_native(path: Path) -> None:
    """Stand-in for ``_native_kind``: nothing here is a native file."""
    del path


def any_native(path: Path) -> str:
    """Stand-in for ``_native_kind``: every file is a native one."""
    del path
    return "ELF"


def real_go() -> Path:
    found = shutil.which("go")
    if found is None:
        pytest.skip("no go on the PATH")
    done = subprocess.run(
        [found, "version"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if done.returncode != 0:
        pytest.skip(f"go is not usable here: {done.stderr.strip()[:100]!r}")
    return Path(found)


def go_tree(root: Path, name: str) -> Path:
    """A copy of the fixture module, at ``root/name``."""
    path = tree(root, name)
    shutil.copytree(FIXTURES / "prog", path, dirs_exist_ok=True)
    return path


def repo(path: Path) -> Path:
    """Make a tree a repository, which the native-build check asks."""
    subprocess.run(
        ["git", "-C", str(path), "init", "-q"],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return path


def build(path: Path, out: str = "bin/hot") -> Path:
    subprocess.run(
        ["go", "build", "-o", out, "./cmd/hot"],
        cwd=path,
        check=True,
        capture_output=True,
        timeout=300,
    )
    return path / out


def go_version_of(path: Path) -> str:
    """What the real ``go version`` says a file was built with."""
    done = subprocess.run(
        ["go", "version", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return done.stdout.split()[1]


# The header of a real executable, from the fixture capture.


def test_the_version_in_a_real_header_is_the_one_go_version_reports() -> None:
    captured = (FIXTURES / "hot-version-m.txt").read_text().split()[1]
    assert go_build_version(REGION) == captured == "go1.25.14"


def test_a_file_with_no_header_has_no_version(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.write_bytes(b"\x00" * 5000)
    assert go_build_version(plain) is None
    assert go_build_version(FIXTURES / "cpu.pprof") is None


def test_a_header_at_an_unaligned_offset_is_not_read(tmp_path: Path) -> None:
    moved = tmp_path / "moved"
    moved.write_bytes(b"\x00" * 1001 + REGION.read_bytes())
    assert go_build_version(moved) is None


def test_the_magic_followed_by_other_bytes_is_not_a_version(
    tmp_path: Path,
) -> None:
    """A program that reads headers holds the magic as a constant."""
    region = bytearray(REGION.read_bytes())
    region[33:42] = b"\x09not a ver"
    other = tmp_path / "other"
    other.write_bytes(b"\x00" * 64 + bytes(region))
    assert go_build_version(other) is None


@pytest.mark.parametrize("pad", [0, 16, 1 << 20, (1 << 20) - 16, 3 << 20])
def test_a_header_across_chunk_boundaries_is_found(
    tmp_path: Path, pad: int
) -> None:
    padded = tmp_path / "padded"
    padded.write_bytes(b"\x00" * pad + REGION.read_bytes())
    assert go_build_version(padded) == "go1.25.14"


def test_a_header_near_the_end_of_a_chunk_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reader waits for the bytes after a hit that ends a chunk."""
    monkeypatch.setattr("perfproof.bench.gobuildinfo._CHUNK_BYTES", 400)
    for pad in range(0, 800, 16):
        padded = tmp_path / f"p{pad}"
        padded.write_bytes(b"\x00" * pad + REGION.read_bytes())
        assert go_build_version(padded) == "go1.25.14", pad


def test_reading_a_large_file_uses_bounded_memory(tmp_path: Path) -> None:
    big = tmp_path / "big"
    with big.open("wb") as handle:
        handle.truncate(48 << 20)
        handle.seek(40 << 20)
        handle.write(REGION.read_bytes())
    tracemalloc.start()
    try:
        assert go_build_version(big) == "go1.25.14"
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 << 20


# The probe, from a real ``go env -json`` capture.


def test_a_goroot_with_no_native_go_here_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``go`` reports a toolchain this machine does not have.

    That is what a launcher that runs Go in a container reports: the
    capture's GOROOT, ``/usr/local/go``, inside the official image.
    """
    captured = json.loads((FIXTURES / "go-env.json").read_text())
    launch = AnswerLaunch(
        go_tree(tmp_path, "a"),
        {"list": captured["GOROOT"] + "\n", "env": "{}"},
    )
    monkeypatch.setattr("perfproof.bench.lock._native_kind", no_native)
    runtime = launch.go(Path("/x/go"), Path("/x/go"), ["test", "."], {})
    assert runtime.kind == "unknown"
    assert runtime.reason is not None
    assert "/usr/local/go" in runtime.reason
    assert "has no native bin/go on this machine" in runtime.reason


@pytest.mark.parametrize(
    "output",
    ["", "not json", "[]", '{"GOVERSION": 1}', '{"GOROOT": "/usr/local/go"}'],
)
def test_a_go_env_that_is_not_a_report_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    launch = AnswerLaunch(
        go_tree(tmp_path, "a"), {"list": "/usr/local/go\n", "env": output}
    )
    monkeypatch.setattr("perfproof.bench.lock._native_kind", any_native)
    runtime = launch.go(Path("/x/go"), Path("/x/go"), ["run", "."], {})
    assert runtime.kind == "unknown"


@pytest.mark.parametrize(
    "sub", ["install", "tool", "generate", "get", "mod", "env", "", "-C"]
)
def test_other_subcommands_are_not_identified(
    tmp_path: Path, sub: str
) -> None:
    launch = AnswerLaunch(go_tree(tmp_path, "a"), {})
    runtime = launch.go(Path("/x/go"), Path("/x/go"), [sub, "x"], {})
    assert runtime.kind == "unknown"
    assert runtime.reason is not None
    assert "only go run, test, build and vet" in runtime.reason


def test_a_go_script_is_a_shell_not_a_toolchain(tmp_path: Path) -> None:
    """A launcher named ``go`` that runs Go elsewhere is a shell."""
    base, cand = go_tree(tmp_path, "a"), go_tree(tmp_path, "b")
    for root in (base, cand):
        shim = root / "bin" / "go"
        shim.write_text('#!/bin/sh\nexec docker run golang:1.25 go "$@"\n')
        shim.chmod(0o755)
    spec = make_spec(("bin/go", "test", "."))

    result = gate(spec, base, cand)

    assert result.status == "not_available"
    assert "is a shell" in result.detail


# The fetch forms.


@pytest.mark.parametrize(
    "argv",
    [
        ["go", "run", "example.com/cmd/tool@v1.2.3"],
        ["go", "run", "github.com/x/y/cmd/z@latest", "--flag"],
        ["go", "install", "golang.org/x/tools/cmd/stringer@latest"],
        ["go", "get", "example.com/x"],
        ["go", "mod", "download"],
    ],
)
def test_go_forms_that_fetch_code_are_named(
    tmp_path: Path, argv: list[str]
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "go"
    stub.write_text("#!/bin/sh\n")
    stub.chmod(0o755)
    env = {"PATH": str(bin_dir)}
    assert fetching_launch(argv, env, tmp_path) is not None


@pytest.mark.parametrize(
    "argv",
    [
        ["go", "run", "."],
        ["go", "run", "./cmd/hot"],
        ["go", "run", "-ldflags", "-X main.v=a@b", "."],
        ["go", "test", "-run", "^$", "-bench", ".", "./..."],
        ["go", "build", "-o", "bin/x", "./cmd/x"],
        ["go", "install", "./cmd/x"],
        ["go", "mod", "tidy"],
        ["go", "version"],
    ],
)
def test_go_forms_that_run_the_projects_own_code_are_not_named(
    tmp_path: Path, argv: list[str]
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "go"
    stub.write_text("#!/bin/sh\n")
    stub.chmod(0o755)
    env = {"PATH": str(bin_dir)}
    assert fetching_launch(argv, env, tmp_path) is None


# The real toolchain.


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    [
        ("go", "test", "-count", "1", "-run", "^$", "."),
        ("go", "run", "./cmd/hot"),
        ("go", "build", "-o", "bin/x", "./cmd/hot"),
    ],
)
def test_the_same_toolchain_passes_for_run_test_and_build(
    tmp_path: Path, command: tuple[str, ...]
) -> None:
    go = real_go()
    base, cand = go_tree(tmp_path, "a"), go_tree(tmp_path, "b")
    spec = make_spec(command)

    env = probe("baseline", spec, base)
    result = gate(spec, base, cand)

    assert result.status == "passed", result.detail
    assert env.runtime_kind == "interpreter"
    reported = json.loads(
        subprocess.run(
            [str(go), "env", "-json", "GOROOT", "GOVERSION"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
            cwd=base,
        ).stdout
    )
    assert env.runtime_executable == str(
        (Path(reported["GOROOT"]) / "bin" / "go").resolve()
    )
    assert env.runtime_version is not None
    assert env.runtime_version.startswith(reported["GOVERSION"])
    assert "; sha256 " in env.runtime_version


@pytest.mark.integration
def test_a_go_executable_with_different_bytes_fails(tmp_path: Path) -> None:
    """Both trees hold their own copy of the toolchain's ``go``.

    The copy's bytes differ in the candidate, so the toolchain that
    answers is another file with the same version.
    """
    go = real_go()
    reported = json.loads(
        subprocess.run(
            [str(go), "env", "-json", "GOROOT"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        ).stdout
    )
    home = Path(reported["GOROOT"])
    base, cand = go_tree(tmp_path, "a"), go_tree(tmp_path, "b")
    for root in (base, cand):
        shutil.copytree(home, root / "goroot", symlinks=True)
    spec = make_spec(("goroot/bin/go", "test", "."))
    assert gate(spec, base, cand).status == "passed"
    with (cand / "goroot" / "bin" / "go").open("ab") as handle:
        handle.write(b"\0")

    result = gate(spec, base, cand)

    assert result.status == "failed", result.detail
    assert {d.item for d in result.differences} == {"runtime_version"}


@pytest.mark.integration
def test_a_go_mod_that_needs_a_newer_toolchain_is_unknown(
    tmp_path: Path,
) -> None:
    real_go()
    base, cand = go_tree(tmp_path, "a"), go_tree(tmp_path, "b")
    (cand / "go.mod").write_text("module example.com/prog\n\ngo 1.99\n")
    spec = make_spec(("go", "test", "."))
    local = {"GOTOOLCHAIN": "local"}

    result = gate(spec, base, cand, base_extra=local, cand_extra=local)

    assert result.status == "not_available"
    assert "could not report its go root" in result.detail
    assert "go.mod requires go >= 1.99" in result.detail


@pytest.mark.integration
def test_go_env_and_go_version_do_not_read_the_go_mod_but_go_list_does(
    tmp_path: Path,
) -> None:
    """Why the probe asks ``go list``: the other two never switch."""
    go = real_go()
    root = go_tree(tmp_path, "a")
    (root / "go.mod").write_text("module example.com/prog\n\ngo 1.99\n")
    env = {**os.environ, "GOTOOLCHAIN": "local"}

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(go), *args],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    assert run("env", "GOVERSION").returncode == 0
    assert run("version").returncode == 0
    for failing in (
        run("list", "-f", "{{.Root}}", "runtime"),
        run("test", "-run", "^$", "."),
    ):
        assert failing.returncode != 0
        assert "go.mod requires go >= 1.99" in failing.stderr


@pytest.mark.integration
def test_a_built_binary_records_the_toolchain_that_built_it(
    tmp_path: Path,
) -> None:
    real_go()
    base, cand = repo(go_tree(tmp_path, "a")), repo(go_tree(tmp_path, "b"))
    built = [build(base), build(cand)]
    spec = make_spec(("bin/hot",))

    result = gate(spec, base, cand)

    assert result.status == "passed", result.detail
    toolchain = go_version_of(built[0])
    assert go_build_version(built[0]) == toolchain
    assert f"built by {toolchain} in both" in result.detail


@pytest.mark.integration
def test_binaries_built_by_different_toolchains_fail(tmp_path: Path) -> None:
    """The candidate's binary names another toolchain in its header.

    The version text is overwritten in a real build, one digit, so the
    program still runs and only the header differs.
    """
    real_go()
    base, cand = repo(go_tree(tmp_path, "a")), repo(go_tree(tmp_path, "b"))
    build(base)
    built = build(cand)
    version = go_version_of(built)
    data = bytearray(built.read_bytes())
    needle = b"\xff Go buildinf:"
    at = next(
        i
        for i in range(len(data))
        if data.startswith(needle, i) and i % 16 == 0
    )
    stamp = data.index(version.encode(), at)
    assert stamp - at < 64
    other = version[:-1] + ("0" if version[-1] != "0" else "1")
    data[stamp : stamp + len(version)] = other.encode()
    built.write_bytes(bytes(data))
    spec = make_spec(("bin/hot",))

    result: GateResult = gate(spec, base, cand)

    assert result.status == "failed", result.detail
    assert [d.item for d in result.differences] == ["bin/hot go toolchain"]
    assert version in (result.differences[0].baseline or "")
    assert other in (result.differences[0].candidate or "")
