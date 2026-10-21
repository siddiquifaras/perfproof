import hashlib
import json
import os
import shutil
import subprocess
import sys
import tracemalloc
from pathlib import Path
from typing import Any

import pytest

from perfproof.bench.lock import (
    LOCK_CHUNK_BYTES,
    MAX_DIFFERENCES,
    LockError,
    build_manifest,
    environment_warnings,
    hash_file,
    lock_gate,
    probe_environment,
    procedure_gate,
    resolve_executable,
    runtime_gate,
)
from perfproof.bench.snapshot import add_baseline_worktree, resolve_commit
from perfproof.bench.spec import (
    command_hash,
    normalize_launch_path,
    spec_command_hash,
)
from perfproof.schema import (
    Arm,
    BenchmarkSpec,
    DependencyLock,
    GateResult,
    LockManifest,
    Sample,
    TreeEnvironment,
)

VENV_PYTHON = ".venv/bin/python"
UV_RUN = ("uv", "run", "--no-project")
JSON_OUTPUT = ("--output-format", "json")


@pytest.fixture(autouse=True)
def isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "gitconfig"
    config.write_text(
        "[user]\n\tname = Test\n\temail = test@example.invalid\n"
        "[commit]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n"
        '[protocol "file"]\n\tallow = always\n'
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


def make_spec(**overrides: Any) -> BenchmarkSpec:
    fields: dict[str, Any] = {
        "name": "bench-tile",
        "command": (VENV_PYTHON, "bench.py"),
        "metric": {
            "kind": "wall",
            "direction": "lower_is_better",
            "unit": "s",
        },
        "correctness": {
            "command": (VENV_PYTHON, "-m", "pytest"),
            "timeout_seconds": 60.0,
        },
        "warmup": 1,
        "budget": {"max_rounds": 20, "max_seconds": 600.0},
        "looks": {"n0": 10, "k": 10, "max_looks": 3},
        "timeout_seconds": 30.0,
        "harness": ("bench.py",),
        "oracle": ("tests",),
        "inputs": ("data",),
        "config": ("config.toml",),
    }
    fields.update(overrides)
    return BenchmarkSpec.model_validate(fields)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    write(root / "bench.py", "import time\nprint(time.perf_counter())\n")
    write(root / "tests" / "test_tile.py", "def test_ok():\n    pass\n")
    write(root / "tests" / "golden" / "out.txt", "42\n")
    write(root / "data" / "input.csv", "a,b\n1,2\n")
    write(root / "config.toml", "[tile]\nsize = 256\n")
    write(root / "src" / "tile.py", "def f():\n    return 1\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "baseline")
    return root


@pytest.fixture
def spec() -> BenchmarkSpec:
    return make_spec()


@pytest.fixture
def frozen(repo: Path, spec: BenchmarkSpec) -> LockManifest:
    return build_manifest(spec, repo)


@pytest.fixture
def baseline(repo: Path) -> Path:
    return add_baseline_worktree(repo, resolve_commit(repo, "HEAD"), "base")


def check_lock(
    spec: BenchmarkSpec, frozen: LockManifest, baseline: Path, candidate: Path
) -> GateResult:
    return lock_gate(
        spec, frozen, baseline_root=baseline, candidate_root=candidate
    )


# Lock manifest


def test_manifest_covers_every_declared_file(
    repo: Path, frozen: LockManifest, spec: BenchmarkSpec
) -> None:
    files = {f.path: f for f in frozen.files}

    assert frozen.spec_hash == spec.spec_hash
    assert sorted(files) == [
        "bench.py",
        "config.toml",
        "data/input.csv",
        "tests/golden/out.txt",
        "tests/test_tile.py",
    ]
    assert {f.role for f in frozen.files} == {
        "harness",
        "oracle",
        "input",
        "config",
    }
    assert files["data/input.csv"].role == "input"
    content = (repo / "tests" / "golden" / "out.txt").read_bytes()
    assert files["tests/golden/out.txt"].sha256 == (
        hashlib.sha256(content).hexdigest()
    )
    assert files["tests/golden/out.txt"].size_bytes == len(content)


def test_hash_streams_with_bounded_memory(tmp_path: Path) -> None:
    big = tmp_path / "big.bin"
    block = os.urandom(1 << 20)
    with big.open("wb") as handle:
        for _ in range(32):
            handle.write(block)
    expected = hashlib.sha256(block * 32).hexdigest()

    tracemalloc.start()
    try:
        digest, size = hash_file(big)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert (digest, size) == (expected, 32 << 20)
    assert peak < 2 * LOCK_CHUNK_BYTES


def test_hash_of_an_empty_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.touch()

    assert hash_file(empty) == (hashlib.sha256(b"").hexdigest(), 0)


def test_missing_declared_path_cannot_be_frozen(
    repo: Path, spec: BenchmarkSpec
) -> None:
    (repo / "config.toml").unlink()

    with pytest.raises(LockError, match=r"config\.toml"):
        build_manifest(spec, repo)


def test_file_declared_twice_through_a_directory_is_refused(
    repo: Path,
) -> None:
    spec = make_spec(config=("tests/golden/out.txt",))

    with pytest.raises(LockError, match="more than once"):
        build_manifest(spec, repo)


def test_fifo_in_a_declared_directory_is_refused(repo: Path) -> None:
    os.mkfifo(repo / "data" / "pipe")

    with pytest.raises(LockError, match="not a regular file"):
        build_manifest(make_spec(), repo)


def test_directory_symlink_in_a_declared_directory_is_refused(
    repo: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "data" / "more").symlink_to(outside)

    with pytest.raises(LockError, match="symlink"):
        build_manifest(make_spec(), repo)


def test_file_symlinks_are_hashed_by_their_content(repo: Path) -> None:
    (repo / "data" / "link.csv").symlink_to(repo / "data" / "input.csv")

    files = {f.path: f for f in build_manifest(make_spec(), repo).files}

    assert files["data/link.csv"].sha256 == files["data/input.csv"].sha256


def test_byproduct_directories_are_not_locked(repo: Path) -> None:
    write(repo / "tests" / "__pycache__" / "t.pyc", "x")
    write(repo / "data" / ".git", "gitdir: elsewhere\n")

    paths = [f.path for f in build_manifest(make_spec(), repo).files]

    assert not any("__pycache__" in p or p.endswith(".git") for p in paths)


# Lock gate


def test_untouched_trees_pass_the_lock_gate(
    spec: BenchmarkSpec, frozen: LockManifest, baseline: Path, repo: Path
) -> None:
    result = check_lock(spec, frozen, baseline, repo)

    assert result.status == "passed"
    assert result.differences == ()


def test_edited_benchmark_file_fails_the_lock_gate(
    spec: BenchmarkSpec, frozen: LockManifest, baseline: Path, repo: Path
) -> None:
    write(repo / "bench.py", "print(0.0)\n")

    result = check_lock(spec, frozen, baseline, repo)

    assert result.gate == "lock"
    assert result.status == "failed"
    assert [d.item for d in result.differences] == ["bench.py"]
    diff = result.differences[0]
    assert (
        diff.baseline == {f.path: f for f in frozen.files}["bench.py"].sha256
    )
    assert diff.candidate == hash_file(repo / "bench.py")[0]


def test_edited_test_fails_the_lock_gate(
    spec: BenchmarkSpec, frozen: LockManifest, baseline: Path, repo: Path
) -> None:
    write(repo / "tests" / "test_tile.py", "def test_ok():\n    assert 1\n")

    result = check_lock(spec, frozen, baseline, repo)

    assert result.status == "failed"
    assert [d.item for d in result.differences] == ["tests/test_tile.py"]


def test_new_test_file_fails_the_lock_gate(
    spec: BenchmarkSpec, frozen: LockManifest, baseline: Path, repo: Path
) -> None:
    write(repo / "tests" / "test_new.py", "def test_new():\n    pass\n")

    result = check_lock(spec, frozen, baseline, repo)

    assert result.status == "failed"
    assert result.differences[0].item == "tests/test_new.py"
    assert result.differences[0].baseline is None


def test_changed_input_data_fails_the_lock_gate(
    spec: BenchmarkSpec, frozen: LockManifest, baseline: Path, repo: Path
) -> None:
    write(repo / "data" / "input.csv", "a,b\n1,3\n")

    result = check_lock(spec, frozen, baseline, repo)

    assert result.status == "failed"
    assert [d.item for d in result.differences] == ["data/input.csv"]


def test_removed_input_data_fails_the_lock_gate(
    spec: BenchmarkSpec, frozen: LockManifest, baseline: Path, repo: Path
) -> None:
    (repo / "data" / "input.csv").unlink()
    (repo / "data").rmdir()

    result = check_lock(spec, frozen, baseline, repo)

    assert result.status == "failed"
    assert "data" in result.detail
    assert result.differences[0].item == "data/input.csv"
    assert result.differences[0].candidate is None


def test_changed_config_fails_the_lock_gate(
    spec: BenchmarkSpec, frozen: LockManifest, baseline: Path, repo: Path
) -> None:
    write(repo / "config.toml", "[tile]\nsize = 512\n")

    result = check_lock(spec, frozen, baseline, repo)

    assert result.status == "failed"
    assert [d.item for d in result.differences] == ["config.toml"]


def test_edited_baseline_tree_fails_the_lock_gate(
    spec: BenchmarkSpec, frozen: LockManifest, baseline: Path, repo: Path
) -> None:
    write(baseline / "data" / "input.csv", "tampered\n")

    result = check_lock(spec, frozen, baseline, repo)

    assert result.status == "failed"
    assert result.differences[0].item == "data/input.csv"


def test_same_edit_in_both_trees_still_fails_the_lock_gate(
    spec: BenchmarkSpec, frozen: LockManifest, baseline: Path, repo: Path
) -> None:
    for tree in (baseline, repo):
        write(tree / "data" / "input.csv", "both\n")

    result = check_lock(spec, frozen, baseline, repo)

    assert result.status == "failed"
    diff = result.differences[0]
    assert diff.baseline == diff.candidate
    assert "frozen" in result.detail


def test_unlocked_source_changes_pass_the_lock_gate(
    spec: BenchmarkSpec, frozen: LockManifest, baseline: Path, repo: Path
) -> None:
    write(repo / "src" / "tile.py", "def f():\n    return 2\n")
    write(repo / "tests" / "__pycache__" / "x.pyc", "byproduct")

    assert check_lock(spec, frozen, baseline, repo).status == "passed"


def test_manifest_of_another_spec_fails_the_lock_gate(
    frozen: LockManifest, baseline: Path, repo: Path
) -> None:
    result = check_lock(make_spec(warmup=4), frozen, baseline, repo)

    assert result.status == "failed"
    assert "spec" in result.detail


def test_unreadable_candidate_tree_fails_instead_of_raising(
    spec: BenchmarkSpec, frozen: LockManifest, baseline: Path, repo: Path
) -> None:
    os.mkfifo(repo / "tests" / "pipe")

    result = check_lock(spec, frozen, baseline, repo)

    assert result.status == "failed"
    assert "candidate" in result.detail


def test_lock_differences_are_truncated(
    spec: BenchmarkSpec, frozen: LockManifest, baseline: Path, repo: Path
) -> None:
    for i in range(MAX_DIFFERENCES + 5):
        write(repo / "data" / f"extra{i:03d}.csv", str(i))

    result = check_lock(spec, frozen, baseline, repo)

    assert result.status == "failed"
    assert len(result.differences) == MAX_DIFFERENCES
    assert result.truncated
    assert str(MAX_DIFFERENCES + 5) in result.detail


# Procedure gate

BOTH_ARMS: tuple[Arm, Arm] = ("baseline", "candidate")


def sample(version: Arm, rnd: int, digest: str) -> Sample:
    return Sample(
        version=version,
        round=rnd,
        position=0 if version == "baseline" else 1,
        value=1.0,
        wall_seconds=1.0,
        cpu_seconds=1.0,
        max_rss_bytes=1,
        exit_code=0,
        command_hash=digest,
    )


def rounds(spec: BenchmarkSpec, n: int) -> list[Sample]:
    digest = spec_command_hash(spec, VENV_PYTHON)
    return [sample(v, r, digest) for r in range(n) for v in BOTH_ARMS]


def check_procedure(
    spec: BenchmarkSpec,
    samples: list[Sample],
    *,
    spec_hashes: dict[Arm, str] | None = None,
    versions: dict[Arm, str] | None = None,
) -> GateResult:
    return procedure_gate(
        spec,
        samples,
        spec_hashes=spec_hashes
        or {"baseline": spec.spec_hash, "candidate": spec.spec_hash},
        perfproof_versions=versions
        or {"baseline": "0.1.0", "candidate": "0.1.0"},
        executables={"baseline": VENV_PYTHON, "candidate": VENV_PYTHON},
    )


def test_rounds_that_launched_the_spec_pass_the_procedure_gate(
    spec: BenchmarkSpec,
) -> None:
    result = check_procedure(spec, rounds(spec, 12))

    assert result.gate == "procedure"
    assert result.status == "passed"


def test_changed_spec_field_fails_the_procedure_gate(
    spec: BenchmarkSpec,
) -> None:
    changed = make_spec(warmup=5)

    result = check_procedure(
        changed,
        rounds(changed, 3),
        spec_hashes={
            "baseline": spec.spec_hash,
            "candidate": changed.spec_hash,
        },
    )

    assert result.status == "failed"
    assert result.differences[0].item == "spec_hash"
    assert result.differences[0].baseline == spec.spec_hash
    assert result.differences[0].candidate == changed.spec_hash


def test_spec_hash_other_than_the_spec_in_force_fails(
    spec: BenchmarkSpec,
) -> None:
    other = make_spec(inputs=("data", "data2")).spec_hash

    result = check_procedure(
        spec,
        rounds(spec, 3),
        spec_hashes={"baseline": other, "candidate": other},
    )

    assert result.status == "failed"
    assert "in force" in result.detail


def test_round_with_another_command_fails_the_procedure_gate(
    spec: BenchmarkSpec,
) -> None:
    samples = rounds(spec, 12)
    other = command_hash(
        (VENV_PYTHON, "bench.py", "--fast"), spec.env, spec.cwd, VENV_PYTHON
    )
    samples[15] = sample("candidate", 7, other)

    result = check_procedure(spec, samples)

    assert result.status == "failed"
    assert [d.item for d in result.differences] == ["round 7 command_hash"]
    assert result.differences[0].candidate == other
    assert result.differences[0].baseline == spec_command_hash(
        spec, VENV_PYTHON
    )


@pytest.mark.parametrize(
    ("argv", "env", "cwd", "executable"),
    [
        ((VENV_PYTHON, "bench.py"), {"OMP_NUM_THREADS": "2"}, ".", None),
        ((VENV_PYTHON, "bench.py"), {}, "src", None),
        ((VENV_PYTHON, "bench.py"), {}, ".", "/usr/bin/python3"),
    ],
)
def test_round_with_another_env_cwd_or_executable_fails(
    spec: BenchmarkSpec,
    argv: tuple[str, ...],
    env: dict[str, str],
    cwd: str,
    executable: str | None,
) -> None:
    samples = rounds(spec, 10)
    digest = command_hash(argv, env, cwd, executable or VENV_PYTHON)
    samples[0] = sample("baseline", 0, digest)

    result = check_procedure(spec, samples)

    assert result.status == "failed"
    assert result.differences[0].item == "round 0 command_hash"
    assert result.differences[0].baseline == digest


def test_different_perfproof_versions_fail_the_procedure_gate(
    spec: BenchmarkSpec,
) -> None:
    result = check_procedure(
        spec,
        rounds(spec, 2),
        versions={"baseline": "0.1.0", "candidate": "0.2.0"},
    )

    assert result.status == "failed"
    assert result.differences[0].item == "perfproof_version"


def test_procedure_differences_are_truncated(spec: BenchmarkSpec) -> None:
    bad = "0" * 64
    samples = [sample("candidate", r, bad) for r in range(30)]

    result = check_procedure(spec, samples)

    assert result.status == "failed"
    assert len(result.differences) == MAX_DIFFERENCES
    assert result.truncated
    assert "30 rounds" in result.detail


def test_procedure_gate_needs_both_arms(spec: BenchmarkSpec) -> None:
    with pytest.raises(ValueError, match="candidate"):
        procedure_gate(
            spec,
            [],
            spec_hashes={"baseline": spec.spec_hash},
            perfproof_versions={"baseline": "0.1.0", "candidate": "0.1.0"},
            executables={"baseline": VENV_PYTHON, "candidate": VENV_PYTHON},
        )


# Runtime gate, probes and recorded differences


def link_python(tree: Path, target: str = sys.executable) -> None:
    exe = tree / VENV_PYTHON
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.symlink_to(target)


def stub_python(tree: Path) -> None:
    exe = tree / VENV_PYTHON
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("#!/bin/sh\nprintf '%s\\nstub 0.0' \"$0\"\n")
    exe.chmod(0o755)


def probe(
    version: Arm,
    tree: Path,
    spec: BenchmarkSpec | None = None,
    **env: str,
) -> TreeEnvironment:
    base = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], **env}
    return probe_environment(version, spec or make_spec(), tree, base)


def test_probe_records_the_interpreter_of_the_tree(
    baseline: Path,
) -> None:
    link_python(baseline)

    env = probe("baseline", baseline, OMP_NUM_THREADS="4", FOO="secret")

    assert env.version == "baseline"
    assert env.runtime_kind == "interpreter"
    assert env.runtime_executable == str(Path(sys.executable).resolve())
    assert env.runtime_version == sys.version
    assert env.env_names == ("FOO", "HOME", "OMP_NUM_THREADS", "PATH")
    assert dict(env.env_values) == {"OMP_NUM_THREADS": "4"}
    assert env.dependency_locks == ()
    assert dict(env.missing) == {}


def test_probe_records_dependency_lockfiles(baseline: Path) -> None:
    link_python(baseline)
    write(baseline / "uv.lock", "version = 1\n")

    env = probe("baseline", baseline)

    assert env.dependency_locks == (
        DependencyLock(
            path="uv.lock", sha256=hash_file(baseline / "uv.lock")[0]
        ),
    )


def test_probe_leaves_an_unresolvable_executable_unknown(
    baseline: Path,
) -> None:
    env = probe("baseline", baseline)

    assert env.runtime_kind is None
    assert env.runtime_executable is None
    assert env.runtime_version is None
    assert "runtime_kind" in env.missing
    assert "runtime_executable" in env.missing
    assert "runtime_version" in env.missing


def test_probe_resolves_bare_commands_on_path(baseline: Path) -> None:
    spec = make_spec(command=("git", "--version"))

    exe = resolve_executable(spec, baseline, {"PATH": os.environ["PATH"]})
    env = probe_environment(
        "baseline", spec, baseline, {"PATH": os.environ["PATH"]}
    )

    assert exe is not None
    assert exe.name == "git"
    assert env.runtime_executable == str(exe.resolve())
    assert env.runtime_kind is None
    assert env.runtime_version is None
    assert "native" in env.missing["runtime_kind"]
    assert "outside the tree" in env.missing["runtime_kind"]
    assert env.missing["runtime_version"] == env.missing["runtime_kind"]


def test_resolved_executables_give_matching_launch_hashes(
    baseline: Path, repo: Path, spec: BenchmarkSpec
) -> None:
    hashes: list[str] = []
    for tree in (baseline, repo):
        link_python(tree)
        exe = resolve_executable(spec, tree, {"PATH": os.environ["PATH"]})
        assert exe is not None
        hashes.append(
            spec_command_hash(spec, normalize_launch_path(exe, tree))
        )

    assert hashes[0] == hashes[1] == spec_command_hash(spec, VENV_PYTHON)


def check_runtime(
    spec: BenchmarkSpec, baseline: Path, candidate: Path, **env: str
) -> GateResult:
    return runtime_gate(
        spec,
        probe("baseline", baseline, spec, **env),
        probe("candidate", candidate, spec, **env),
        baseline_root=baseline,
        candidate_root=candidate,
    )


def test_same_interpreter_passes_the_runtime_gate(
    spec: BenchmarkSpec, baseline: Path, repo: Path
) -> None:
    link_python(baseline)
    link_python(repo)

    result = check_runtime(spec, baseline, repo)

    assert result.gate == "runtime"
    assert result.status == "passed"


def test_different_interpreter_fails_the_runtime_gate(
    spec: BenchmarkSpec, baseline: Path, repo: Path
) -> None:
    link_python(baseline)
    stub_python(repo)

    result = check_runtime(spec, baseline, repo)

    assert result.status == "failed"
    items = {d.item: d for d in result.differences}
    assert set(items) == {"runtime_executable", "runtime_version"}
    assert items["runtime_executable"].candidate == VENV_PYTHON
    assert items["runtime_version"].baseline == sys.version
    assert items["runtime_version"].candidate == "stub 0.0"


def test_different_interpreter_is_waived_with_the_opt_in(
    baseline: Path, repo: Path
) -> None:
    link_python(baseline)
    stub_python(repo)

    result = check_runtime(
        make_spec(allow_runtime_change=True), baseline, repo
    )

    assert result.status == "waived"
    assert "allow_runtime_change" in result.detail
    assert len(result.differences) == 2


def test_unknown_runtime_fails_the_runtime_gate(
    spec: BenchmarkSpec, baseline: Path, repo: Path
) -> None:
    link_python(baseline)

    result = check_runtime(spec, baseline, repo)

    assert result.status == "failed"
    assert "unknown" in result.detail


def test_unknown_runtime_in_both_trees_fails_the_runtime_gate(
    spec: BenchmarkSpec, baseline: Path, repo: Path
) -> None:
    result = check_runtime(spec, baseline, repo)

    assert result.status == "failed"
    assert result.differences == ()
    assert "unknown" in result.detail


def test_runtime_gate_checks_which_arm_is_which(
    spec: BenchmarkSpec, baseline: Path, repo: Path
) -> None:
    link_python(baseline)
    env = probe("baseline", baseline)

    with pytest.raises(ValueError, match="candidate"):
        runtime_gate(
            spec, env, env, baseline_root=baseline, candidate_root=repo
        )


def run_python(exe: str) -> str | None:
    try:
        done = subprocess.run(
            [exe, "-I", "-c", "import sys; sys.stdout.write(sys.version)"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 and done.stdout else None


@pytest.fixture(scope="session")
def other_python() -> tuple[str, str]:
    """A real interpreter other than the one running the tests."""
    mine = Path(sys.executable).resolve()
    found: list[str] = []
    uv = shutil.which("uv")
    if uv is not None:
        listed = subprocess.run(
            [uv, "python", "list", "--only-installed", *JSON_OUTPUT],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if listed.returncode == 0:
            found += [entry["path"] for entry in json.loads(listed.stdout)]
    for name in ("python3", "python3.12", "python3.13", "python3.14"):
        path = shutil.which(name)
        if path is not None:
            found.append(path)
    for path in found:
        if Path(path).resolve() != mine:
            version = run_python(path)
            if version is not None:
                return str(Path(path).resolve()), version
    pytest.skip("no second Python interpreter is installed")


def console_script(tree: Path, shebang: str) -> None:
    script = tree / ".venv" / "bin" / "bench-run"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(f"#!{shebang}\nprint('bench')\n")
    script.chmod(0o755)


def test_venv_console_script_with_another_interpreter_fails(
    baseline: Path, repo: Path, other_python: tuple[str, str]
) -> None:
    spec = make_spec(command=(".venv/bin/bench-run",))
    link_python(baseline)
    link_python(repo, other_python[0])
    for tree in (baseline, repo):
        console_script(tree, str(tree / VENV_PYTHON))

    result = check_runtime(spec, baseline, repo)

    assert result.status == "failed"
    items = {d.item: d for d in result.differences}
    assert items["runtime_executable"].baseline == str(
        Path(sys.executable).resolve()
    )
    assert items["runtime_executable"].candidate == other_python[0]
    assert items["runtime_version"].baseline == sys.version
    assert items["runtime_version"].candidate == other_python[1]


def test_venv_console_script_with_the_same_interpreter_passes(
    baseline: Path, repo: Path
) -> None:
    spec = make_spec(command=(".venv/bin/bench-run",))
    for tree in (baseline, repo):
        link_python(tree)
        console_script(tree, str(tree / VENV_PYTHON))

    result = check_runtime(spec, baseline, repo)

    assert result.status == "passed"
    assert sys.version in result.detail


def test_env_shebang_finds_each_tree_interpreter_on_path(
    baseline: Path, repo: Path, other_python: tuple[str, str]
) -> None:
    spec = make_spec(
        command=("bench-run",),
        env={"PATH": f".venv/bin:{os.environ['PATH']}"},
    )
    (baseline / ".venv" / "bin").mkdir(parents=True)
    (baseline / ".venv" / "bin" / "python3").symlink_to(sys.executable)
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python3").symlink_to(other_python[0])
    for tree in (baseline, repo):
        console_script(tree, "/usr/bin/env python3")

    result = check_runtime(spec, baseline, repo)
    same = check_runtime(spec, baseline, baseline)

    assert result.status == "failed"
    versions = {d.item: d for d in result.differences}["runtime_version"]
    assert (versions.baseline, versions.candidate) == (
        sys.version,
        other_python[1],
    )
    assert same.status == "passed"


def uv_spec() -> BenchmarkSpec:
    if shutil.which("uv") is None:
        pytest.skip("uv is not installed")
    return make_spec(
        command=(*UV_RUN, "--python", VENV_PYTHON, "python", "bench.py")
    )


def test_uv_run_with_the_same_interpreter_passes(
    baseline: Path, repo: Path
) -> None:
    spec = uv_spec()
    link_python(baseline)
    link_python(repo)

    env = probe("candidate", repo, spec)
    result = check_runtime(spec, baseline, repo)

    assert env.runtime_executable == str(Path(sys.executable).resolve())
    assert env.runtime_version == sys.version
    assert result.status == "passed"


def test_uv_run_with_another_interpreter_fails(
    baseline: Path, repo: Path, other_python: tuple[str, str]
) -> None:
    spec = uv_spec()
    link_python(baseline)
    link_python(repo, other_python[0])

    result = check_runtime(spec, baseline, repo)

    assert result.status == "failed"
    items = {d.item: d for d in result.differences}
    assert items["runtime_executable"].candidate == other_python[0]
    assert items["runtime_version"].candidate == other_python[1]


def test_uv_run_that_cannot_find_its_interpreter_fails_closed(
    baseline: Path, repo: Path
) -> None:
    spec = uv_spec()

    result = check_runtime(spec, baseline, repo)

    assert result.status == "failed"
    assert result.differences == ()
    assert "the baseline runtime is unknown: uv run exited" in result.detail


def shell_launcher(tree: Path) -> None:
    script = tree / "run.sh"
    script.write_text("#!/bin/sh\nexec python3 bench.py\n")
    script.chmod(0o755)


def test_launcher_without_a_known_version_fails_closed(
    baseline: Path, repo: Path
) -> None:
    spec = make_spec(command=("./run.sh",))
    for tree in (baseline, repo):
        shell_launcher(tree)

    result = check_runtime(spec, baseline, repo)
    waived = check_runtime(
        make_spec(command=("./run.sh",), allow_runtime_change=True),
        baseline,
        repo,
    )

    assert result.status == "failed"
    assert result.differences == ()
    assert "run.sh -> /bin/sh is a shell" in result.detail
    assert "not the one launched" in result.detail
    assert waived.status == "waived"
    assert waived.detail == "allow_runtime_change: " + result.detail


def test_native_executable_outside_the_tree_fails_closed(
    baseline: Path, repo: Path
) -> None:
    result = check_runtime(make_spec(command=("true",)), baseline, repo)

    assert result.status == "failed"
    assert "outside the tree" in result.detail


NATIVE_SPEC_COMMAND = ("build/bench",)
UNTRACKED_NOTE = (
    "untracked, so treated as a build of the code under test and not "
    "compared itself"
)
SYSTEM_LIBRARY = "libSystem" if sys.platform == "darwin" else "libc."


def copy_native(
    tree: Path, extra: bytes = b"", rel: str = "build/bench"
) -> None:
    true = shutil.which("true")
    assert true is not None
    target = tree / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(Path(true).resolve().read_bytes() + extra)
    target.chmod(0o755)


def compiler() -> str:
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("no C compiler is installed")
    return cc


def build_library(
    directory: Path, value: int, install_name: str | None = None
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "foo.c"
    source.write_text(f"int foo(void) {{ return {value}; }}\n")
    if sys.platform == "darwin":
        lib = directory / "libfoo.dylib"
        args = ["-dynamiclib", "-install_name", install_name or str(lib)]
    else:
        lib = directory / "libfoo.so"
        args = ["-shared", "-fPIC"]
    subprocess.run(
        [compiler(), *args, "-o", str(lib), str(source)],
        check=True,
        capture_output=True,
        timeout=120,
    )


def build_native(tree: Path, library: Path) -> None:
    source = tree / "main.c"
    source.write_text("int foo(void);\nint main(void) { return foo(); }\n")
    target = tree / "build" / "bench"
    target.parent.mkdir(parents=True, exist_ok=True)
    link = [f"-L{library}", "-lfoo"]
    if sys.platform != "darwin":
        link.append(f"-Wl,-rpath,{library}")
    subprocess.run(
        [compiler(), "-o", str(target), str(source), *link],
        check=True,
        capture_output=True,
        timeout=120,
    )


def test_untracked_native_build_with_the_same_libraries_passes(
    baseline: Path, repo: Path
) -> None:
    spec = make_spec(command=NATIVE_SPEC_COMMAND)
    copy_native(baseline)
    copy_native(repo, extra=b"\0")

    env = probe("candidate", repo, spec)
    result = check_runtime(spec, baseline, repo)

    assert env.runtime_kind == "native_build"
    assert env.runtime_version is None
    assert "runtime_version" not in env.missing
    assert env.linked_libraries is None
    assert "runtime gate" in env.missing["linked_libraries"]
    assert result.status == "passed"
    assert result.detail.startswith(
        f"build/bench is a native executable built in each tree "
        f"({UNTRACKED_NOTE}); it has no interpreter to compare; libraries "
        "outside the tree match: "
    )
    assert SYSTEM_LIBRARY in result.detail


def test_native_build_linking_the_same_outside_library_passes(
    baseline: Path, repo: Path, tmp_path: Path
) -> None:
    spec = make_spec(command=NATIVE_SPEC_COMMAND)
    library = tmp_path / "libA"
    build_library(library, 1)
    for tree in (baseline, repo):
        build_native(tree, library)

    result = check_runtime(spec, baseline, repo)

    assert result.status == "passed"
    name = "libfoo.dylib" if sys.platform == "darwin" else "libfoo.so"
    real = (library / name).resolve()
    assert f"{real} sha256:{hash_file(real)[0]}" in result.detail


def test_native_build_linking_other_outside_libraries_fails(
    baseline: Path, repo: Path, tmp_path: Path
) -> None:
    spec = make_spec(command=NATIVE_SPEC_COMMAND)
    build_library(tmp_path / "libA", 1)
    build_library(tmp_path / "libB", 2)
    build_native(baseline, tmp_path / "libA")
    build_native(repo, tmp_path / "libB")

    result = check_runtime(spec, baseline, repo)
    waived = check_runtime(
        make_spec(command=NATIVE_SPEC_COMMAND, allow_runtime_change=True),
        baseline,
        repo,
    )

    assert result.status == "failed"
    assert "links different libraries outside the tree" in result.detail
    libraries = [d for d in result.differences if d.item.startswith("library")]
    assert libraries
    assert any("libA" in (d.baseline or "") for d in libraries)
    assert any("libB" in (d.candidate or "") for d in libraries)
    assert waived.status == "waived"


def test_native_library_that_cannot_be_found_fails_closed(
    baseline: Path, repo: Path, tmp_path: Path
) -> None:
    spec = make_spec(command=NATIVE_SPEC_COMMAND)
    library = tmp_path / "libA"
    build_library(library, 1)
    for tree in (baseline, repo):
        build_native(tree, library)
    shutil.rmtree(library)

    result = check_runtime(spec, baseline, repo)

    assert result.status == "failed"
    assert "libraries the baseline build/bench links cannot be listed" in (
        result.detail
    )


@pytest.fixture
def tracked_trees(repo: Path) -> tuple[Path, Path]:
    copy_native(repo)
    git(repo, "add", "build/bench")
    git(repo, "commit", "-q", "-m", "vendored binary")
    base = add_baseline_worktree(repo, resolve_commit(repo, "HEAD"), "bin")
    return base, repo


def test_tracked_native_binary_swapped_in_one_tree_fails(
    tracked_trees: tuple[Path, Path],
) -> None:
    base, cand = tracked_trees
    copy_native(cand, extra=b"\0")

    result = check_runtime(make_spec(command=NATIVE_SPEC_COMMAND), base, cand)
    waived = check_runtime(
        make_spec(command=NATIVE_SPEC_COMMAND, allow_runtime_change=True),
        base,
        cand,
    )

    assert result.status == "failed"
    assert "tracked by git and differs between the trees" in result.detail
    swap = {d.item: d for d in result.differences}["build/bench sha256"]
    assert swap.baseline == hash_file(base / "build" / "bench")[0]
    assert swap.candidate == hash_file(cand / "build" / "bench")[0]
    assert waived.status == "waived"


def test_tracked_native_binary_identical_in_both_trees_passes(
    tracked_trees: tuple[Path, Path],
) -> None:
    base, cand = tracked_trees

    result = check_runtime(make_spec(command=NATIVE_SPEC_COMMAND), base, cand)

    assert result.status == "passed"
    assert "(tracked by git and identical in both trees)" in result.detail


def uv_project(tree: Path, node_version: str) -> None:
    (tree / "pyproject.toml").write_text(
        '[project]\nname = "t"\nversion = "0"\nrequires-python = ">=3.12"\n'
        "dependencies = []\n\n[tool.uv]\npackage = false\n"
    )
    subprocess.run(
        ["uv", "venv", "--quiet", "--python", sys.executable],
        cwd=tree,
        env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]},
        check=True,
        capture_output=True,
        timeout=120,
    )
    node = tree / ".venv" / "bin" / "tool"
    node.write_text(f"#!/bin/sh\necho {node_version}\n")
    node.chmod(0o755)


def test_uv_run_follows_a_command_that_is_not_python(
    baseline: Path, repo: Path
) -> None:
    uv_spec()
    spec = make_spec(command=("uv", "run", "tool", "bench.js"))
    uv_project(baseline, "v18.0.0")
    uv_project(repo, "v22.0.0")

    env = probe("candidate", repo, spec)
    result = check_runtime(spec, baseline, repo)

    assert env.runtime_version is None
    assert env.missing["runtime_version"].startswith(
        f"uv run -> {(repo / '.venv' / 'bin' / 'tool').absolute()} -> "
        "/bin/sh is a shell"
    )
    assert result.status == "failed"
    assert "the candidate runtime is unknown: uv run ->" in result.detail


def test_uv_run_module_probes_uv_python(baseline: Path, repo: Path) -> None:
    uv_spec()
    spec = make_spec(command=(*UV_RUN, "--python", VENV_PYTHON, "-m", "bench"))
    link_python(baseline)
    link_python(repo)

    env = probe("candidate", repo, spec)
    result = check_runtime(spec, baseline, repo)

    assert env.runtime_version == sys.version
    assert result.status == "passed"


def test_uv_run_python_file_probes_uv_python(baseline: Path) -> None:
    uv_spec()
    link_python(baseline)
    write(baseline / "plain.py", "print(1)\n")
    write(
        baseline / "inline.py",
        "# /// script\n# dependencies = []\n# ///\nprint(1)\n",
    )

    plain = probe(
        "baseline",
        baseline,
        make_spec(command=(*UV_RUN, "--python", VENV_PYTHON, "plain.py")),
    )
    inline = probe(
        "baseline",
        baseline,
        make_spec(command=(*UV_RUN, "--python", VENV_PYTHON, "inline.py")),
    )

    assert plain.runtime_version == sys.version
    assert inline.runtime_version is None
    assert "inline metadata" in inline.missing["runtime_version"]


def test_native_executable_against_an_interpreter_fails(
    baseline: Path, repo: Path
) -> None:
    spec = make_spec(command=("build/bench",))
    copy_native(baseline)
    (repo / "build").mkdir()
    (repo / "build" / "bench").symlink_to(sys.executable)

    result = check_runtime(spec, baseline, repo)

    assert result.status == "failed"
    items = {d.item: d for d in result.differences}
    assert items["runtime_executable"].baseline == "build/bench"
    assert items["runtime_executable"].candidate == str(
        Path(sys.executable).resolve()
    )
    assert items["runtime_version"].baseline is None
    assert items["runtime_version"].candidate == sys.version


@pytest.mark.parametrize(
    "command",
    [
        ("tools/uv", "-q", "run", "python", "bench.py"),
        ("tools/uv", "--directory", "x", "run", "python", "bench.py"),
        ("tools/uv", "tool", "run", "ruff"),
    ],
)
def test_in_tree_uv_that_is_not_followed_fails_closed(
    baseline: Path, repo: Path, command: tuple[str, ...]
) -> None:
    for tree in (baseline, repo):
        copy_native(tree, rel="tools/uv")

    result = check_runtime(make_spec(command=command), baseline, repo)

    assert result.status == "failed"
    assert "only 'uv run' as uv's first argument" in result.detail


def test_in_tree_launcher_fails_closed(baseline: Path, repo: Path) -> None:
    for tree in (baseline, repo):
        copy_native(tree, rel="tools/uvx")

    spec = make_spec(command=("tools/uvx", "ruff"))
    result = check_runtime(spec, baseline, repo)

    assert result.status == "failed"
    assert "is a launcher that picks its own runtime" in result.detail


def test_native_tool_installed_in_a_tree_virtualenv_fails_closed(
    baseline: Path, repo: Path
) -> None:
    for tree in (baseline, repo):
        copy_native(tree, rel=".venv/bin/tool")
        write(tree / ".venv" / "pyvenv.cfg", "home = /usr/bin\n")

    spec = make_spec(command=(".venv/bin/tool",))
    result = check_runtime(spec, baseline, repo)

    assert result.status == "failed"
    assert "installed into the tree (virtualenv .venv)" in result.detail


def build_wrapped(tree: Path, ext: Path) -> None:
    """Build build/app -> in-tree libwrap -> libfoo in ``ext``."""
    cc = compiler()
    lib = tree / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    (tree / "build").mkdir(exist_ok=True)
    wrap_src = tree / "wrap.c"
    wrap_src.write_text("int foo(void);\nint wrap(void) { return foo(); }\n")
    app_src = tree / "app.c"
    app_src.write_text("int wrap(void);\nint main(void) { return wrap(); }\n")
    if sys.platform == "darwin":
        wrap = [
            "-dynamiclib",
            "-install_name",
            "@rpath/libwrap.dylib",
            "-o",
            str(lib / "libwrap.dylib"),
        ]
        app_rpath = "-Wl,-rpath,@executable_path/../lib"
    else:
        wrap = ["-shared", "-fPIC", "-o", str(lib / "libwrap.so")]
        wrap.append(f"-Wl,-rpath,{ext}")
        app_rpath = "-Wl,-rpath,$ORIGIN/../lib"
    app = ["-o", str(tree / "build" / "app"), str(app_src)]
    for argv in (
        [cc, *wrap, str(wrap_src), f"-L{ext}", "-lfoo"],
        [cc, *app, f"-L{lib}", "-lwrap", app_rpath],
    ):
        subprocess.run(argv, check=True, capture_output=True, timeout=120)


def test_outside_library_behind_an_in_tree_library_is_compared(
    baseline: Path, repo: Path, tmp_path: Path
) -> None:
    build_library(tmp_path / "extA", 1)
    build_library(tmp_path / "extB", 2)
    build_wrapped(baseline, tmp_path / "extA")
    build_wrapped(repo, tmp_path / "extB")
    spec = make_spec(command=("build/app",))

    result = check_runtime(spec, baseline, repo)
    same = check_runtime(spec, baseline, baseline)

    assert result.status == "failed"
    assert any("extA" in (d.baseline or "") for d in result.differences)
    assert any("extB" in (d.candidate or "") for d in result.differences)
    assert same.status == "passed"
    assert str((tmp_path / "extA").resolve()) in same.detail


@pytest.mark.parametrize(
    "name",
    [
        "DYLD_LIBRARY_PATH",
        "DYLD_FALLBACK_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
    ],
)
def test_loader_variables_fail_native_builds_closed(
    baseline: Path, repo: Path, name: str
) -> None:
    for tree in (baseline, repo):
        copy_native(tree)
    spec = make_spec(command=NATIVE_SPEC_COMMAND, env={name: "lib"})

    result = check_runtime(spec, baseline, repo)

    assert result.status == "failed"
    assert f"loader variables are set ({name})" in result.detail


def test_unrecorded_environment_fails_native_builds_closed(
    baseline: Path, repo: Path
) -> None:
    spec = make_spec(command=NATIVE_SPEC_COMMAND)
    for tree in (baseline, repo):
        copy_native(tree)
    recorded = probe("candidate", repo, spec)
    blind = TreeEnvironment.model_validate(
        {
            **recorded.model_dump(mode="json"),
            "env_names": None,
            "env_values": {},
            "missing": {**recorded.missing, "env_names": "not probed"},
        }
    )

    result = runtime_gate(
        spec,
        probe("baseline", baseline, spec),
        blind,
        baseline_root=baseline,
        candidate_root=repo,
    )

    assert result.status == "failed"
    assert "variable names were not recorded" in result.detail


def test_native_build_is_read_from_the_runtime_kind(
    baseline: Path, repo: Path
) -> None:
    # Schema version 2 recorded a native build as fixed text in
    # missing["runtime_version"]. That text, without the kind, is an
    # unknown runtime now: only runtime_kind makes a native build.
    spec = make_spec(command=NATIVE_SPEC_COMMAND)
    for tree in (baseline, repo):
        copy_native(tree)
    recorded = probe("candidate", repo, spec)
    marker = "a native executable built in the tree; it has no interpreter"
    unmarked = TreeEnvironment.model_validate(
        {
            **recorded.model_dump(mode="json"),
            "runtime_kind": None,
            "missing": {
                **recorded.missing,
                "runtime_kind": marker,
                "runtime_version": marker,
            },
        }
    )

    result = runtime_gate(
        spec,
        probe("baseline", baseline, spec),
        unmarked,
        baseline_root=baseline,
        candidate_root=repo,
    )

    assert recorded.runtime_kind == "native_build"
    assert result.status == "failed"
    assert marker in result.detail


def test_native_build_kind_outside_the_tree_fails_closed(
    baseline: Path, repo: Path
) -> None:
    spec = make_spec(command=NATIVE_SPEC_COMMAND)
    for tree in (baseline, repo):
        copy_native(tree)
    true = shutil.which("true")
    assert true is not None
    recorded = probe("candidate", repo, spec)
    outside = recorded.model_copy(
        update={"runtime_executable": str(Path(true).resolve())}
    )

    result = runtime_gate(
        spec,
        probe("baseline", baseline, spec),
        TreeEnvironment.model_validate(outside.model_dump(mode="json")),
        baseline_root=baseline,
        candidate_root=repo,
    )

    assert result.status == "failed"
    assert "recorded as a native build, but" in result.detail


@pytest.mark.skipif(
    sys.platform == "darwin", reason="relative RPATH is an ELF behaviour"
)
def test_relative_rpath_resolves_from_the_benchmark_cwd(
    baseline: Path, repo: Path, tmp_path: Path
) -> None:
    build_library(tmp_path / "extA", 1)
    build_library(tmp_path / "extB", 2)
    for tree in (baseline, repo):
        (tree / "lib").mkdir()
        (tree / "lib" / "libfoo.so").symlink_to(
            tmp_path / "extA" / "libfoo.so"
        )
        source = tree / "main.c"
        source.write_text("int foo(void);\nint main(void) { return foo(); }\n")
        (tree / "build").mkdir()
        output = ["-o", str(tree / "build" / "bench"), str(source)]
        subprocess.run(
            [
                compiler(),
                *output,
                f"-L{tree / 'lib'}",
                "-lfoo",
                "-Wl,-rpath,lib",
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
    spec = make_spec(command=NATIVE_SPEC_COMMAND)

    same = check_runtime(spec, baseline, repo)
    (repo / "lib" / "libfoo.so").unlink()
    (repo / "lib" / "libfoo.so").symlink_to(tmp_path / "extB" / "libfoo.so")
    different = check_runtime(spec, baseline, repo)

    assert same.status == "passed"
    assert str((tmp_path / "extA" / "libfoo.so").resolve()) in same.detail
    assert different.status == "failed"


@pytest.mark.skipif(sys.platform != "darwin", reason="Mach-O load paths")
@pytest.mark.parametrize(
    ("install_name", "rpath"),
    [
        ("@rpath/libfoo.dylib", ["-Wl,-rpath,lib"]),
        ("lib/libfoo.dylib", []),
    ],
)
def test_relative_macho_paths_resolve_from_the_benchmark_cwd(
    baseline: Path,
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_name: str,
    rpath: list[str],
) -> None:
    for ext, value in (("extA", 1), ("extB", 2)):
        build_library(tmp_path / ext, value, install_name)
    for tree, ext in ((baseline, "extA"), (repo, "extB")):
        (tree / "lib").mkdir()
        (tree / "lib" / "libfoo.dylib").symlink_to(
            tmp_path / ext / "libfoo.dylib"
        )
        source = tree / "main.c"
        source.write_text("int foo(void);\nint main(void) { return foo(); }\n")
        (tree / "build").mkdir()
        output = ["-o", str(tree / "build" / "bench"), str(source)]
        subprocess.run(
            [compiler(), *output, f"-L{tree / 'lib'}", "-lfoo", *rpath],
            check=True,
            capture_output=True,
            timeout=120,
        )
    monkeypatch.chdir(repo)
    spec = make_spec(command=NATIVE_SPEC_COMMAND)

    different = check_runtime(spec, baseline, repo)
    (baseline / "lib" / "libfoo.dylib").unlink()
    (baseline / "lib" / "libfoo.dylib").symlink_to(
        tmp_path / "extB" / "libfoo.dylib"
    )
    same = check_runtime(spec, baseline, repo)

    assert different.status == "failed"
    assert any("extA" in (d.baseline or "") for d in different.differences)
    assert same.status == "passed"
    assert str((tmp_path / "extB" / "libfoo.dylib").resolve()) in same.detail


def shebang_script(tree: Path, interpreter: str) -> None:
    script = tree / "bench-run"
    script.write_text(f"#!{interpreter}\nprint(1)\n")
    script.chmod(0o755)


def test_slashless_shebang_resolves_against_the_cwd(
    baseline: Path, repo: Path, other_python: tuple[str, str]
) -> None:
    (baseline / "python3").symlink_to(sys.executable)
    (repo / "python3").symlink_to(other_python[0])
    for tree in (baseline, repo):
        shebang_script(tree, "python3")
    spec = make_spec(command=("./bench-run",))

    result = check_runtime(spec, baseline, repo)
    same = check_runtime(spec, baseline, baseline)

    assert result.status == "failed"
    versions = {d.item: d for d in result.differences}["runtime_version"]
    assert (versions.baseline, versions.candidate) == (
        sys.version,
        other_python[1],
    )
    assert same.status == "passed"


def test_slashless_shebang_missing_from_the_cwd_fails_closed(
    baseline: Path, repo: Path
) -> None:
    for tree in (baseline, repo):
        shebang_script(tree, "python3")

    result = check_runtime(make_spec(command=("./bench-run",)), baseline, repo)

    assert result.status == "failed"
    assert "'./python3' is not an executable file" in result.detail


@pytest.mark.parametrize(
    "command",
    [
        (".venv/bin/tox", "-q", "-e", "bench"),
        (VENV_PYTHON, "-m", "tox", "-q", "-e", "bench"),
        (VENV_PYTHON, "-Im", "tox"),
        (VENV_PYTHON, "-X", "dev", "-m", "nox"),
        (VENV_PYTHON, "-mpipenv", "run", "bench"),
        (VENV_PYTHON, "-m", "invoke.main"),
    ],
)
def test_python_launchers_fail_closed(
    baseline: Path, repo: Path, command: tuple[str, ...]
) -> None:
    for tree in (baseline, repo):
        link_python(tree)
        script = tree / ".venv" / "bin" / "tox"
        script.write_text(f"#!{tree / VENV_PYTHON}\nimport tox\n")
        script.chmod(0o755)

    result = check_runtime(make_spec(command=command), baseline, repo)

    assert result.status == "failed"
    assert "launcher that picks its own runtime" in result.detail


@pytest.mark.parametrize(
    "command",
    [
        (VENV_PYTHON, "-m", "bench"),
        (VENV_PYTHON, "-X", "tox", "bench.py"),
        (VENV_PYTHON, "-c", "import tox"),
        (VENV_PYTHON, "tox"),
    ],
)
def test_python_programs_that_are_not_launchers_pass(
    baseline: Path, repo: Path, command: tuple[str, ...]
) -> None:
    for tree in (baseline, repo):
        link_python(tree)

    result = check_runtime(make_spec(command=command), baseline, repo)

    assert result.status == "passed"


@pytest.mark.parametrize(
    "command",
    [
        (VENV_PYTHON, "-m", "uv", "run", "bench.py"),
        (VENV_PYTHON, "-m", "pip_run", "bench.py"),
        (VENV_PYTHON, "-m", "TOX"),
        (".venv/bin/pip_run", "bench.py"),
        ("/usr/bin/env", VENV_PYTHON, "-m", "tox"),
        ("./bench-run",),
    ],
)
def test_every_python_form_refuses_a_launcher_module(
    baseline: Path, repo: Path, command: tuple[str, ...]
) -> None:
    for tree in (baseline, repo):
        link_python(tree)
        script = tree / ".venv" / "bin" / "pip_run"
        script.write_text(f"#!{tree / VENV_PYTHON}\nimport pip_run\n")
        script.chmod(0o755)
        shebang_script(tree, f"{tree / VENV_PYTHON} -m tox")

    result = check_runtime(make_spec(command=command), baseline, repo)

    assert result.status == "failed"
    assert "launcher that picks its own runtime" in result.detail


def test_uv_run_python_module_launcher_fails_closed(
    baseline: Path, repo: Path
) -> None:
    uv_spec()
    spec = make_spec(
        command=(*UV_RUN, "--python", VENV_PYTHON, "python", "-m", "tox")
    )
    for tree in (baseline, repo):
        link_python(tree)

    result = check_runtime(spec, baseline, repo)

    assert result.status == "failed"
    assert "uv run -m tox runs a launcher" in result.detail


def test_uv_run_module_launcher_fails_closed(
    baseline: Path, repo: Path
) -> None:
    uv_spec()
    spec = make_spec(command=(*UV_RUN, "--python", VENV_PYTHON, "-m", "tox"))
    for tree in (baseline, repo):
        link_python(tree)

    result = check_runtime(spec, baseline, repo)

    assert result.status == "failed"
    assert "uv run -m tox runs a launcher" in result.detail


def test_committed_binary_in_a_submodule_is_tracked(
    repo: Path, tmp_path: Path
) -> None:
    sub = tmp_path / "tool"
    sub.mkdir()
    git(sub, "init", "-q")
    copy_native(sub, rel="bench")
    git(sub, "add", "bench")
    git(sub, "commit", "-q", "-m", "binary")
    git(repo, "submodule", "add", "-q", str(sub), "vendor/tool")
    git(repo, "commit", "-q", "-m", "submodule")
    base = add_baseline_worktree(repo, resolve_commit(repo, "HEAD"), "sub")
    git(base, "submodule", "update", "--init", "-q")
    copy_native(repo, extra=b"\0", rel="vendor/tool/bench")
    spec = make_spec(command=("vendor/tool/bench",))

    result = check_runtime(spec, base, repo)
    same = check_runtime(spec, base, base)

    assert result.status == "failed"
    assert "tracked by git and differs between the trees" in result.detail
    assert same.status == "passed"
    assert "(tracked by git and identical in both trees)" in same.detail


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        (("/usr/bin/env", "--bogus", "python3"), "not understood"),
        (("/usr/bin/env", "A=1"), "names no program"),
        (("uv", "pip", "list"), "only 'uv run'"),
        (("uv", "run", "--python"), "has no value"),
        (("uv", "run", "--no-project"), "names no command"),
        (("uv", "run", "--script", "x.py"), "own environment"),
    ],
)
def test_unfollowable_launchers_are_unknown(
    baseline: Path, command: tuple[str, ...], reason: str
) -> None:
    if command[0] == "uv" and shutil.which("uv") is None:
        pytest.skip("uv is not installed")

    env = probe("baseline", baseline, make_spec(command=command))

    assert env.runtime_version is None
    assert reason in env.missing["runtime_version"]


def test_undeclared_env_difference_is_a_warning_not_a_failure(
    spec: BenchmarkSpec, frozen: LockManifest, baseline: Path, repo: Path
) -> None:
    link_python(baseline)
    link_python(repo)
    base_env = probe("baseline", baseline, OMP_NUM_THREADS="4")
    cand_env = probe("candidate", repo, OMP_NUM_THREADS="8", EXTRA="1")
    digest = {arm: spec_command_hash(spec, VENV_PYTHON) for arm in BOTH_ARMS}

    warnings = environment_warnings(base_env, cand_env)
    gates = [
        check_lock(spec, frozen, baseline, repo),
        procedure_gate(
            spec,
            [sample(v, 0, digest[v]) for v in BOTH_ARMS],
            spec_hashes=dict.fromkeys(BOTH_ARMS, spec.spec_hash),
            perfproof_versions=dict.fromkeys(BOTH_ARMS, "0.1.0"),
            executables=dict.fromkeys(BOTH_ARMS, VENV_PYTHON),
        ),
        runtime_gate(
            spec,
            base_env,
            cand_env,
            baseline_root=baseline,
            candidate_root=repo,
        ),
    ]

    assert [g.status for g in gates] == ["passed", "passed", "passed"]
    assert {w.code for w in warnings} == {"environment_differs"}
    messages = " | ".join(w.message for w in warnings)
    assert "OMP_NUM_THREADS" in messages
    assert "'4'" in messages
    assert "'8'" in messages
    assert "EXTRA" in messages


def test_identical_environments_give_no_warnings(baseline: Path) -> None:
    link_python(baseline)

    assert (
        environment_warnings(
            probe("baseline", baseline, OMP_NUM_THREADS="4"),
            probe("candidate", baseline, OMP_NUM_THREADS="4"),
        )
        == ()
    )


def test_dependency_lock_difference_is_a_warning(
    baseline: Path, repo: Path
) -> None:
    write(baseline / "uv.lock", "version = 1\n")
    write(repo / "uv.lock", "version = 2\n")
    write(repo / "Cargo.lock", "x\n")

    warnings = environment_warnings(
        probe("baseline", baseline), probe("candidate", repo)
    )

    codes = [w.code for w in warnings if w.code == "dependency_lock_differs"]
    messages = [w.message for w in warnings]
    assert len(codes) == 2
    assert any("uv.lock" in m for m in messages)
    assert any("Cargo.lock" in m for m in messages)


def test_unrecorded_names_are_warned_as_unknown() -> None:
    unprobed = {
        "runtime_kind": "not probed",
        "runtime_executable": "not probed",
        "runtime_version": "not probed",
        "linked_libraries": "not probed",
    }
    known = TreeEnvironment(
        version="baseline",
        env_names=("A",),
        dependency_locks=(),
        missing=unprobed,
    )
    unknown = TreeEnvironment(
        version="candidate",
        dependency_locks=(),
        missing={**unprobed, "env_names": "env not readable"},
    )

    warnings = environment_warnings(known, unknown)

    assert [w.code for w in warnings] == ["environment_unknown"]
    assert "env not readable" in warnings[0].message
