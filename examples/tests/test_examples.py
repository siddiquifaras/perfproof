"""Checks that every example and candidate behaves as the README says.

Each test builds the trees it needs with `materialize` in a temporary
directory and runs them in subprocesses, as perfproof would.
"""

import importlib.util
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from urllib.parse import urlsplit

import numpy as np
import pytest
from perfproof.schema import DEFAULT_GUARDRAILS, BenchmarkSpec

from materialize import EXAMPLES, candidates, changed_files, materialize

PYTHON_EXAMPLES = ("rolling", "membership")
DECOYS = (
    "bench-edit",
    "wrong-output",
    "noop-refactor",
    "slight-speedup",
    "untested-branch",
    "memory-cache",
)
SUITE_ARGS = {
    "rolling": ["-m", "not deliberately_flaky"],
    "membership": [],
}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
TIMEOUT_S = 300
POLL_S = 0.01
PYTEST_FAILED = 1
RSS_RUNS = 3
MIN_MEMORY_RATIO = 2.5
MAX_FIX_MEMORY_RATIO = 1.25


def run(
    argv: list[str], cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )


def run_suite(
    tree: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return run([sys.executable, "-m", "pytest", "-q", *args], tree, env)


def run_bench(tree: Path, output: str) -> float:
    done = run([sys.executable, "bench.py", "--output", output], tree)
    assert done.returncode == 0, done.stderr
    lines = [
        line
        for line in done.stdout.splitlines()
        if line.startswith("PERFPROOF ")
    ]
    assert len(lines) == 1
    value = json.loads(lines[0].removeprefix("PERFPROOF "))["value"]
    assert isinstance(value, float)
    assert value > 0
    return value


def peak_rss(tree: Path, timeout_s: float = TIMEOUT_S) -> int:
    """Run the tree's benchmark once and return its ``ru_maxrss``.

    The benchmark runs in its own process group. If it hasn't exited
    after ``timeout_s`` seconds, the whole group is killed and reaped,
    and `subprocess.TimeoutExpired` is raised.
    """
    argv = [sys.executable, "bench.py"]
    deadline = time.monotonic() + timeout_s
    with subprocess.Popen(
        argv,
        cwd=tree,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    ) as proc:
        pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
        while pid == 0:
            if time.monotonic() >= deadline:
                os.killpg(proc.pid, signal.SIGKILL)
                _, status, _ = os.wait4(proc.pid, 0)
                proc.returncode = os.waitstatus_to_exitcode(status)
                raise subprocess.TimeoutExpired(argv, timeout_s)
            time.sleep(POLL_S)
            pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
        proc.returncode = os.waitstatus_to_exitcode(status)
    assert proc.returncode == 0
    return usage.ru_maxrss


def load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def spec_paths(example: str) -> list[Path]:
    return sorted((EXAMPLES / example).glob("spec*.json"))


def output_path(example: str) -> str:
    spec = BenchmarkSpec.model_validate_json(
        (EXAMPLES / example / "spec.json").read_text()
    )
    (check,) = spec.correctness.outputs
    return check.path


def read_output(tree: Path, rel: str) -> object:
    path = tree / rel
    if path.suffix == ".npy":
        return np.load(path).tolist()
    return path.read_text()


@pytest.fixture(scope="module")
def baseline_outputs(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, object]:
    outputs: dict[str, object] = {}
    for example in PYTHON_EXAMPLES:
        tree = materialize(
            example, None, tmp_path_factory.mktemp(example) / "tree"
        )
        rel = output_path(example)
        run_bench(tree, rel)
        outputs[example] = read_output(tree, rel)
    return outputs


@pytest.mark.parametrize("example", PYTHON_EXAMPLES)
def test_ships_the_fix_and_every_decoy(example: str) -> None:
    assert candidates(example) == sorted(("fix", *DECOYS))


@pytest.mark.parametrize("example", PYTHON_EXAMPLES)
@pytest.mark.parametrize("candidate", [None, "fix", *DECOYS])
def test_suite_fails_only_for_the_wrong_output_decoy(
    example: str, candidate: str | None, tmp_path: Path
) -> None:
    tree = materialize(example, candidate, tmp_path / "tree")

    done = run_suite(tree, *SUITE_ARGS[example])

    expected = PYTEST_FAILED if candidate == "wrong-output" else 0
    assert done.returncode == expected, done.stdout


@pytest.mark.parametrize("example", PYTHON_EXAMPLES)
@pytest.mark.parametrize(
    "candidate", [c for c in ("fix", *DECOYS) if c != "bench-edit"]
)
def test_output_differs_only_for_the_wrong_output_decoy(
    example: str,
    candidate: str,
    tmp_path: Path,
    baseline_outputs: dict[str, object],
) -> None:
    tree = materialize(example, candidate, tmp_path / "tree")
    rel = output_path(example)

    run_bench(tree, rel)

    same = read_output(tree, rel) == baseline_outputs[example]
    assert same is (candidate != "wrong-output")


@pytest.mark.parametrize("example", PYTHON_EXAMPLES)
def test_only_the_bench_edit_touches_locked_files(example: str) -> None:
    spec = BenchmarkSpec.model_validate_json(
        (EXAMPLES / example / "spec.json").read_text()
    )
    locked = {*spec.harness, *spec.oracle, *spec.inputs, *spec.config}

    touched = {
        c: locked.intersection(changed_files(example, c))
        for c in candidates(example)
    }

    assert touched.pop("bench-edit") == set(spec.harness)
    assert all(not paths for paths in touched.values())


@pytest.mark.parametrize(
    "path",
    [p for e in PYTHON_EXAMPLES for p in spec_paths(e)],
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_specs_declare_objective_and_every_default_guardrail(
    path: Path,
) -> None:
    spec = BenchmarkSpec.model_validate_json(path.read_text())

    assert spec.schema_version == 4
    assert spec.objective == "wall_time"
    by_metric = {g.metric: g for g in spec.guardrails}
    assert set(by_metric) == set(DEFAULT_GUARDRAILS[None])
    memory = by_metric["peak_memory"]
    assert memory.applicable
    assert memory.allowance == 0.1
    assert memory.allowance_source == "default"
    for name in ("peak_gpu_memory", "p99_latency"):
        assert not by_metric[name].applicable
        assert by_metric[name].reason


@pytest.mark.parametrize(
    "path",
    [p for e in PYTHON_EXAMPLES for p in spec_paths(e)],
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_specs_validate_and_name_existing_files(path: Path) -> None:
    spec = BenchmarkSpec.model_validate_json(path.read_text())

    for rel in (*spec.harness, *spec.oracle, *spec.inputs, *spec.config):
        assert (path.parent / rel).is_file(), rel
    assert (path.parent / spec.command[1]).is_file()


def test_remote_db_spec_differs_only_in_a_non_local_database_url() -> None:
    root = EXAMPLES / "membership"
    local = cast(
        "dict[str, Any]", json.loads((root / "spec.json").read_text())
    )
    remote = cast(
        "dict[str, Any]",
        json.loads((root / "spec-remote-db.json").read_text()),
    )

    remote_env = cast("dict[str, str]", remote.pop("env"))
    local_env = cast("dict[str, str]", local.pop("env"))

    assert remote == local
    assert local_env == {}
    assert list(remote_env) == ["DATABASE_URL"]
    host = urlsplit(remote_env["DATABASE_URL"]).hostname
    assert host is not None
    assert host not in LOCAL_HOSTS


def test_flaky_test_both_passes_and_fails(tmp_path: Path) -> None:
    tree = materialize("rolling", None, tmp_path / "tree")
    outcomes: set[int] = set()

    for seed in range(12):
        done = run_suite(
            tree,
            "-m",
            "deliberately_flaky",
            env={"ROLLING_FLAKY_SEED": str(seed)},
        )
        outcomes.add(done.returncode)
        if len(outcomes) == 2:
            break

    assert outcomes == {0, PYTEST_FAILED}


def test_rolling_untested_branch_aliases_its_input() -> None:
    base = load_module(EXAMPLES / "rolling" / "rolling.py", "rolling_base")
    decoy = load_module(
        EXAMPLES / "rolling/candidates/untested-branch/rolling.py",
        "rolling_untested",
    )
    values = np.arange(5, dtype=np.int64)

    assert base.rolling_sum(values, 1) is not values
    assert decoy.rolling_sum(values, 1) is values


def test_membership_untested_branch_misreads_one_customer() -> None:
    base = load_module(EXAMPLES / "membership" / "orders.py", "orders_base")
    decoy = load_module(
        EXAMPLES / "membership/candidates/untested-branch/orders.py",
        "orders_untested",
    )
    orders = [
        base.Order(order_id=1, customer_id=2, amount_cents=10),
        base.Order(order_id=2, customer_id=1, amount_cents=10),
    ]

    assert [o.order_id for o in base.held_orders(orders, [2])] == [1]
    assert [o.order_id for o in decoy.held_orders(orders, [2])] == [2]


@pytest.mark.parametrize("example", PYTHON_EXAMPLES)
def test_memory_decoy_peaks_at_several_times_the_baseline(
    example: str, tmp_path: Path
) -> None:
    trees = {
        name: materialize(example, cand, tmp_path / name)
        for name, cand in [
            ("baseline", None),
            ("fix", "fix"),
            ("memory", "memory-cache"),
        ]
    }

    rss = {
        name: statistics.median(peak_rss(tree) for _ in range(RSS_RUNS))
        for name, tree in trees.items()
    }

    assert rss["memory"] >= MIN_MEMORY_RATIO * rss["baseline"]
    assert rss["fix"] <= MAX_FIX_MEMORY_RATIO * rss["baseline"]


HANGING_BENCH = """\
import os
import subprocess
import sys
import time
from pathlib import Path

sleeper = "import time; time.sleep(120)"
child = subprocess.Popen([sys.executable, "-c", sleeper])
Path("pids").write_text(f"{os.getpid()} {child.pid}")
time.sleep(120)
"""


def gone(pid: int, within_s: float) -> bool:
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(POLL_S)
    return False


def test_peak_rss_kills_a_hanging_benchmark_and_its_children(
    tmp_path: Path,
) -> None:
    (tmp_path / "bench.py").write_text(HANGING_BENCH)
    start = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired):
        peak_rss(tmp_path, timeout_s=3)

    assert time.monotonic() - start < 30
    bench_pid, child_pid = map(int, (tmp_path / "pids").read_text().split())
    assert gone(bench_pid, within_s=5)
    assert gone(child_pid, within_s=5)


@pytest.mark.skipif(
    shutil.which("make") is None or shutil.which("cc") is None,
    reason="needs make and a C compiler",
)
def test_colsum_fix_gives_byte_identical_sums(tmp_path: Path) -> None:
    done = run(
        ["make", "-C", str(EXAMPLES / "colsum"), "check", f"BUILD={tmp_path}"],
        EXAMPLES,
    )

    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.count("PERFPROOF ") == 2
