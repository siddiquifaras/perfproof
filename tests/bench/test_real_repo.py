"""A real-repository comparison: harness files and per-tree setup.

The repository is a uv project whose source is installed editable into
a ``.venv`` that git ignores, the benchmark is a script perfproof owns
under ``.perfproof/harness/`` and nothing else is committed for it. The
tests run the CLI against the real ``uv``, so they prove that each tree
builds its own environment and imports its own source.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from perfproof.bench.harness import HARNESS_DIR
from perfproof.report import read_record

PY = sys.executable
UV = shutil.which("uv")
pytestmark = pytest.mark.skipif(UV is None, reason="uv is not installed")

PYPROJECT = """\
[project]
name = "toyproj"
version = "0.1"
requires-python = ">=3.12"
dependencies = []

[build-system]
requires = []
build-backend = "backend"
backend-path = ["_backend"]
"""
# A PEP 660 backend that needs no download: an editable install is a
# .pth file naming the tree's own src directory.
BACKEND = """\
import os
import zipfile

NAME, VERSION = "toyproj", "0.1"
DIST = f"{NAME}-{VERSION}.dist-info"


def get_requires_for_build_editable(config_settings=None):
    return []


def build_editable(wheel_directory, config_settings=None,
                   metadata_directory=None):
    files = {
        f"{NAME}.pth": os.path.abspath("src") + "\\n",
        f"{DIST}/METADATA": (
            f"Metadata-Version: 2.1\\nName: {NAME}\\nVersion: {VERSION}\\n"
        ),
        f"{DIST}/WHEEL": (
            "Wheel-Version: 1.0\\nGenerator: toy\\n"
            "Root-Is-Purelib: true\\nTag: py3-none-any\\n"
        ),
    }
    files[f"{DIST}/RECORD"] = "".join(f"{p},,\\n" for p in files) + (
        f"{DIST}/RECORD,,\\n"
    )
    wheel = f"{NAME}-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(os.path.join(wheel_directory, wheel), "w") as z:
        for path, text in files.items():
            z.writestr(path, text)
    return wheel
"""
LIBRARY = """\
import time


def compute():
    time.sleep(0.02)
    return {answer}
"""
BENCH = """\
import time

import toyproj

start = time.perf_counter()
result = toyproj.compute()
elapsed = time.perf_counter() - start
with open("out.txt", "w") as f:
    f.write(f"{result}\\n")
print("PERFPROOF {\\"value\\": %.6f}" % elapsed)
"""
CHECK = """\
import toyproj

assert isinstance(toyproj.compute(), int)
"""
GUARDRAILS: list[dict[str, Any]] = [
    {
        "metric": "peak_memory",
        "direction": "lower_is_better",
        "unit": "bytes",
        "allowance": 0.1,
        "allowance_source": "default",
    },
    {
        "metric": "peak_gpu_memory",
        "direction": "lower_is_better",
        "unit": "bytes",
        "applicable": False,
        "reason": "a CPU-only benchmark",
    },
    {
        "metric": "p99_latency",
        "direction": "lower_is_better",
        "unit": "s",
        "applicable": False,
        "reason": "serves no requests",
    },
]


def run(argv: list[str], cwd: Path, env: dict[str, str]) -> str:
    done = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return done.stdout.strip()


def cli(*argv: str, ok: bool = True) -> dict[str, Any]:
    done = subprocess.run(
        [PY, "-m", "perfproof", *argv, "--json"],
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    document: dict[str, Any] = json.loads(done.stdout)
    assert document["ok"] is ok, done.stdout
    return document


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    config = tmp_path / "gitconfig"
    config.write_text(
        "[user]\n\tname = Test\n\temail = test@example.invalid\n"
        "[commit]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    return dict(os.environ)


def spec_fields(**changes: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "name": "toy",
        "command": [".venv/bin/python", f"{HARNESS_DIR}/bench.py"],
        "env": {"UV_PYTHON": PY, "UV_OFFLINE": "1"},
        "metric": {
            "kind": "reported",
            "direction": "lower_is_better",
            "unit": "s",
        },
        "correctness": {
            "command": [".venv/bin/python", "tests/check.py"],
            "timeout_seconds": 60.0,
            "outputs": [{"path": "out.txt", "compare": "text"}],
        },
        "warmup": 1,
        "budget": {"max_rounds": 10, "max_seconds": 300.0},
        "looks": {"n0": 10, "k": 2, "max_looks": 1},
        "timeout_seconds": 30.0,
        "harness": [f"{HARNESS_DIR}/bench.py"],
        "oracle": ["tests/check.py"],
        "objective": "wall_time",
        "guardrails": GUARDRAILS,
        "setup": {
            "commands": [["uv", "sync", "--frozen"]],
            "timeout_seconds": 120.0,
        },
    }
    fields.update(changes)
    return fields


@pytest.fixture
def repo(tmp_path: Path, env: dict[str, str]) -> Path:
    root = tmp_path / "toy"
    (root / "src" / "toyproj").mkdir(parents=True)
    (root / "_backend").mkdir()
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text(PYPROJECT)
    (root / "_backend" / "backend.py").write_text(BACKEND)
    (root / "src" / "toyproj" / "__init__.py").write_text(
        LIBRARY.format(answer=42)
    )
    (root / "tests" / "check.py").write_text(CHECK)
    (root / ".gitignore").write_text(".venv/\n__pycache__/\n")
    uv_env = {**env, "UV_PYTHON": PY, "UV_OFFLINE": "1"}
    run(["uv", "lock"], root, uv_env)
    run(["git", "init", "-q"], root, env)
    run(["git", "add", "-A"], root, env)
    run(["git", "commit", "-q", "-m", "baseline"], root, env)
    harness = root / HARNESS_DIR
    harness.mkdir(parents=True)
    (harness / "bench.py").write_text(BENCH)
    return root


def candidate(root: Path, answer: int, name: str) -> Path:
    tree = root.parent / name
    shutil.copytree(
        root,
        tree,
        ignore=shutil.ignore_patterns(".git", ".venv", ".perfproof"),
    )
    (tree / "src" / "toyproj" / "__init__.py").write_text(
        LIBRARY.format(answer=answer)
    )
    return tree


def define(root: Path, **changes: Any) -> str:
    path = root.parent / "spec.json"
    path.write_text(json.dumps(spec_fields(**changes)))
    result = cli(
        "bench", "define", "toy", "--spec", str(path), "--repo", str(root)
    )
    return str(result["result"]["spec_hash"])


def compare(root: Path, tree: Path, ok: bool = True) -> dict[str, Any]:
    return cli(
        "compare",
        "toy",
        "--repo",
        str(root),
        "--candidate",
        str(tree),
        ok=ok,
    )


def test_each_tree_imports_its_own_source(repo: Path) -> None:
    define(repo)
    same = compare(repo, candidate(repo, 42, "same"))["result"]
    v = same["verdict"]
    assert (v["outcome"], v["reason"]) == (
        "INCONCLUSIVE",
        "insufficient_evidence",
    )
    gates = {g["gate"]: g["status"] for g in same["gates"]}
    assert gates["lock"] == gates["correctness"] == "passed"
    # A different answer is caught only if the two trees ran different
    # source: one environment shared by both would print the same value.
    changed = compare(repo, candidate(repo, 43, "changed"))["result"]
    v = changed["verdict"]
    assert (v["outcome"], v["reason"], v["gate"]) == (
        "REJECT",
        "correctness",
        "correctness",
    )
    record = read_record(Path(changed["record_path"]))
    differences = [d for g in record.gates for d in g.differences]
    assert [(d.item, d.baseline, d.candidate) for d in differences] == [
        ("out.txt line 1", "42", "43")
    ]
    baseline = repo / ".perfproof" / "worktrees"
    trees = sorted(baseline.iterdir())
    assert len(trees) == 1
    for tree in (trees[0], repo.parent / "changed"):
        assert (tree / HARNESS_DIR / "bench.py").read_text() == BENCH
        assert (tree / ".venv" / "bin" / "python").exists()
    assert not (repo / ".venv").exists()


def test_a_candidate_cannot_alter_the_harness(repo: Path) -> None:
    define(repo)
    tree = candidate(repo, 42, "tamper")
    harness = tree / HARNESS_DIR
    harness.mkdir(parents=True)
    (harness / "bench.py").write_text(BENCH + "\nraise SystemExit(9)\n")
    (harness / "extra.py").write_text("raise SystemExit(9)\n")
    result = compare(repo, tree)["result"]
    assert result["verdict"]["outcome"] == "INCONCLUSIVE"
    assert (harness / "bench.py").read_text() == BENCH
    assert not (harness / "extra.py").exists()


def test_a_harness_symlink_in_the_candidate_is_replaced(
    repo: Path, tmp_path: Path
) -> None:
    define(repo)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    tree = candidate(repo, 42, "link")
    (tree / ".perfproof").mkdir(exist_ok=True)
    (tree / HARNESS_DIR).symlink_to(elsewhere, target_is_directory=True)
    compare(repo, tree)
    assert list(elsewhere.iterdir()) == []
    assert not (tree / HARNESS_DIR).is_symlink()


def test_harness_edited_after_define_is_refused(repo: Path) -> None:
    define(repo)
    (repo / HARNESS_DIR / "bench.py").write_text(BENCH + "\n")
    result = compare(repo, candidate(repo, 42, "same"))["result"]
    v = result["verdict"]
    assert (v["outcome"], v["reason"], v["gate"]) == (
        "REJECT",
        "integrity",
        "lock",
    )


def test_baseline_setup_failure_is_a_broken_baseline(repo: Path) -> None:
    define(
        repo,
        setup={
            "commands": [["uv", "sync", "--frozen", "--no-such"]],
            "timeout_seconds": 60.0,
        },
    )
    failed = compare(repo, candidate(repo, 42, "same"), ok=False)
    assert failed["error"]["code"] == "broken_baseline"
    assert "failed setup" in failed["error"]["message"]


def test_candidate_only_setup_failure_is_a_correctness_reject(
    repo: Path,
) -> None:
    refuse_43 = (
        "import pathlib, sys; "
        "text = pathlib.Path('src/toyproj/__init__.py').read_text(); "
        "sys.exit(7 if '43' in text else 0)"
    )
    define(
        repo,
        setup={
            "commands": [["uv", "sync", "--frozen"], [PY, "-c", refuse_43]],
            "timeout_seconds": 120.0,
        },
    )
    result = compare(repo, candidate(repo, 43, "bad"))["result"]
    v = result["verdict"]
    assert (v["outcome"], v["reason"], v["gate"]) == (
        "REJECT",
        "correctness",
        "correctness",
    )
    record = read_record(Path(result["record_path"]))
    gate = record.gates[-1]
    assert "failed setup" in gate.detail
    assert gate.output is not None
    assert gate.output.exit_code == 7


def test_program_in_an_ignored_directory_is_explained(repo: Path) -> None:
    define(repo, setup=None)
    failed = compare(repo, candidate(repo, 42, "same"), ok=False)
    error = failed["error"]
    assert error["code"] == "broken_baseline"
    assert "git-ignored" in error["message"]
    assert "setup" in error["message"]
    assert "uv run" in error["message"]


def counted_setup(tmp_path: Path, failing: set[int]) -> dict[str, Any]:
    """Setup whose second command fails on the listed invocations.

    The counter lives outside both trees, so it counts every setup run
    across the baseline, the candidate, its retry and the baseline
    recheck, in that order.
    """
    counter = tmp_path / "setup-count"
    code = (
        "import pathlib, sys; "
        f"p = pathlib.Path({str(counter)!r}); "
        "n = int(p.read_text()) if p.exists() else 0; "
        "p.write_text(str(n + 1)); "
        f"sys.exit(5 if n in {sorted(failing)!r} else 0)"
    )
    return {
        "commands": [["uv", "sync", "--frozen"], [PY, "-c", code]],
        "timeout_seconds": 120.0,
    }


def test_setup_that_edits_the_harness_is_rejected_by_the_lock(
    repo: Path,
) -> None:
    # Setup runs the candidate's own build: the lock is checked again.
    tamper = "open('.perfproof/harness/bench.py', 'a').write('\\n# x\\n')"
    define(
        repo,
        setup={
            "commands": [["uv", "sync", "--frozen"], [PY, "-c", tamper]],
            "timeout_seconds": 120.0,
        },
    )
    result = compare(repo, candidate(repo, 42, "same"))["result"]
    v = result["verdict"]
    assert (v["outcome"], v["reason"], v["gate"]) == (
        "REJECT",
        "integrity",
        "lock",
    )


def test_transient_candidate_setup_failure_is_retried(
    repo: Path, tmp_path: Path
) -> None:
    # Invocation 0 is the baseline, 1 the candidate's first attempt.
    define(repo, setup=counted_setup(tmp_path, {1}))
    result = compare(repo, candidate(repo, 42, "same"))["result"]
    assert result["verdict"]["outcome"] == "INCONCLUSIVE"
    record = read_record(Path(result["record_path"]))
    assert [w.code for w in record.warnings if "setup" in w.code] == [
        "setup_retried"
    ]


def test_setup_failing_in_both_trees_is_a_shared_fault(
    repo: Path, tmp_path: Path
) -> None:
    # Baseline passes (0); the candidate fails twice (1, 2); the
    # baseline fails when rerun (3).
    define(repo, setup=counted_setup(tmp_path, {1, 2, 3}))
    failed = compare(repo, candidate(repo, 42, "same"), ok=False)
    assert failed["error"]["code"] == "broken_baseline"
    assert "shared" in failed["error"]["message"]


def test_candidate_setup_failing_twice_records_both_attempts(
    repo: Path, tmp_path: Path
) -> None:
    define(repo, setup=counted_setup(tmp_path, {1, 2}))
    result = compare(repo, candidate(repo, 42, "same"))["result"]
    v = result["verdict"]
    assert (v["outcome"], v["reason"], v["gate"]) == (
        "REJECT",
        "correctness",
        "correctness",
    )
    record = read_record(Path(result["record_path"]))
    detail = record.gates[-1].detail
    assert "twice" in detail
    assert detail.count("exited 5") == 2


def test_a_helper_beside_the_benchmark_is_locked_too(repo: Path) -> None:
    helper = repo / HARNESS_DIR / "helper.py"
    helper.write_text("VALUE = 1\n")
    define(repo)
    helper.write_text("VALUE = 2\n")
    result = compare(repo, candidate(repo, 42, "same"))["result"]
    v = result["verdict"]
    assert (v["outcome"], v["reason"], v["gate"]) == (
        "REJECT",
        "integrity",
        "lock",
    )
    record = read_record(Path(result["record_path"]))
    items = [d.item for g in record.gates for d in g.differences]
    assert f"{HARNESS_DIR}/helper.py" in items
