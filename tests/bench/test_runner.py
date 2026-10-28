import gc
import json
import marshal
import math
import os
import resource
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from types import FrameType
from typing import cast

import numpy as np
import pytest

from perfproof.bench import runner as runner_module
from perfproof.bench.lock import procedure_gate, resolve_executable
from perfproof.bench.runner import (
    MAX_TIMEOUT_SECONDS,
    ExecutionError,
    MetricUnavailableError,
    ResetFailedError,
    Runner,
    RunnerError,
    counter_unavailable_reason,
    reported_guardrails,
    round_orders,
)
from perfproof.bench.spec import normalize_launch_path
from perfproof.schema import (
    Arm,
    BenchmarkSpec,
    Budget,
    CorrectnessSpec,
    GateResult,
    GuardrailSpec,
    LookSchedule,
    MetricKind,
    MetricSpec,
    ResetSpec,
    Sample,
)
from perfproof.stats.sequential import plan_looks

PY = sys.executable
MIB = 1024 * 1024


def make_spec(
    code: str = "pass",
    *,
    command: tuple[str, ...] | None = None,
    kind: MetricKind = "wall",
    event: str | None = None,
    env: Mapping[str, str] | None = None,
    cwd: str = ".",
    warmup: int = 0,
    timeout: float = 30.0,
    guardrails: tuple[GuardrailSpec, ...] = (),
) -> BenchmarkSpec:
    unit = {"wall": "s", "cpu": "s", "reported": "ms", "counter": "count"}
    return BenchmarkSpec(
        name="bench",
        command=command or (PY, "-c", code),
        cwd=cwd,
        env=dict(env or {}),
        metric=MetricSpec(
            kind=kind,
            direction="lower_is_better",
            unit=unit[kind],
            event=event,
        ),
        correctness=CorrectnessSpec(command=("true",), timeout_seconds=10.0),
        warmup=warmup,
        budget=Budget(max_rounds=20, max_seconds=600.0),
        looks=LookSchedule(n0=10, k=10, max_looks=3),
        timeout_seconds=timeout,
        harness=("bench.py",),
        objective="wall_time" if guardrails else None,
        guardrails=guardrails,
    )


def reported_guardrail(
    metric: str = "p99_latency", *, applicable: bool = True
) -> GuardrailSpec:
    """A guardrail the benchmark reports, in milliseconds."""
    if not applicable:
        return GuardrailSpec(
            metric=metric,
            direction="lower_is_better",
            unit="ms",
            applicable=False,
            reason="serves no requests",
        )
    return GuardrailSpec(
        metric=metric,
        direction="lower_is_better",
        unit="ms",
        allowance=0.05,
        allowance_source="default",
    )


DIRECT_ONLY = frozenset(
    {
        "test_sigchld_fallback_without_pidfd",
        "test_sigchld_fallback_forwards_the_callers_sigchld",
        "test_sigchld_fallback_refuses_a_handler_it_cannot_restore",
        "test_sigchld_fallback_works_above_fd_1024",
    }
)
"""Tests of the in-process exit wait. The helper's own wait is a plain
SIGCHLD wakeup in its own process, which every other test exercises."""


@pytest.fixture(autouse=True, params=["direct", "helper"])
def launch_path(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> str:
    """Run every test on both launch paths, on every platform.

    ``direct`` launches and reaps in this process, as macOS does;
    ``helper`` goes through `perfproof.bench.launch_helper`, as Linux
    does. Each path's code is plain POSIX, so both run everywhere and a
    test that holds on one but not the other fails here.
    """
    path = str(request.param)
    name = cast("str", request.node.originalname)  # pyright: ignore[reportUnknownMemberType]
    if path == "helper" and name in DIRECT_ONLY:
        pytest.skip("tests the in-process wait, which the helper replaces")
    monkeypatch.setattr(
        runner_module, "_default_isolation", lambda: path == "helper"
    )
    return path


@pytest.fixture
def trees(tmp_path: Path) -> dict[Arm, Path]:
    out: dict[Arm, Path] = {}
    for arm in ("baseline", "candidate"):
        path = tmp_path / arm
        path.mkdir()
        out[arm] = path
    return out


def make_runner(
    spec: BenchmarkSpec,
    trees: Mapping[Arm, Path],
    tmp_path: Path,
    *,
    seed: int = 7,
    base_env: Mapping[str, str] | None = None,
) -> Runner:
    return Runner(
        spec,
        trees,
        tmp_path / "logs",
        order_seed=seed,
        base_env=base_env,
    )


def write_tool(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_wall_times_the_whole_child(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    spec = make_spec("import time; time.sleep(0.3)")
    rnd = make_runner(spec, trees, tmp_path).run_round(0)
    for sample in rnd.samples:
        assert 0.3 <= sample.wall_seconds < 1.3
        assert sample.value == sample.wall_seconds
        assert sample.exit_code == 0


def test_cpu_counts_busy_time_not_sleep(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    busy = make_spec(
        "import time\nwhile time.process_time() < 0.3: pass", kind="cpu"
    )
    for sample in make_runner(busy, trees, tmp_path).run_round(0).samples:
        assert sample.cpu_seconds is not None
        assert sample.cpu_seconds >= 0.3
        assert sample.value == sample.cpu_seconds
        assert sample.cpu_seconds <= sample.wall_seconds + 0.05
    # Interpreter start-up alone can take a few tenths of a CPU second
    # on a loaded or emulated host, so sleep long enough that counting
    # the sleep would be unmistakable.
    idle = make_spec("import time; time.sleep(1.0)", kind="cpu")
    runner = Runner(idle, trees, tmp_path / "idle", order_seed=1)
    for sample in runner.run_round(0).samples:
        assert sample.cpu_seconds is not None
        assert sample.cpu_seconds < 0.6
        assert sample.wall_seconds >= 1.0


def test_cpu_includes_reaped_grandchildren(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    grandchild = "import time\nwhile time.process_time() < 0.3: pass"
    code = (
        "import subprocess, sys\n"
        f"subprocess.run([sys.executable, '-c', {grandchild!r}], "
        "check=True, timeout=30)"
    )
    spec = make_spec(code, kind="cpu")
    for sample in make_runner(spec, trees, tmp_path).run_round(0).samples:
        assert sample.cpu_seconds is not None
        assert sample.cpu_seconds >= 0.3


def own_peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak * 1024 if sys.platform == "linux" else peak


def test_max_rss_is_in_bytes(trees: dict[Arm, Path], tmp_path: Path) -> None:
    size = max(256 * MIB, own_peak_rss_bytes() + 128 * MIB)
    code = (
        f"b = bytearray({size})\n"
        "b[::4096] = b'x' * len(range(0, len(b), 4096))"
    )
    big = make_spec(code)
    rnd = make_runner(big, trees, tmp_path).run_round(0)
    for ex, sample in zip(rnd.executions, rnd.samples, strict=True):
        assert sample.max_rss_bytes is not None
        assert size <= sample.max_rss_bytes < size + 1024 * MIB
        assert ex.max_rss_bytes == sample.max_rss_bytes
        assert ex.max_rss_missing is None


SMALL_BENCHMARK = (
    "b = bytearray(16 * 1024 * 1024)\n"
    "b[::4096] = b'x' * len(range(0, len(b), 4096))"
)
"""A small Python benchmark: a 16 MiB buffer on a bare interpreter, well
under this test process's own peak (NumPy and pydantic are loaded) and
far above a launch helper's."""


def test_a_small_python_benchmark_has_a_measured_peak_by_default(
    trees: dict[Arm, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default path measures an ordinary small benchmark everywhere.

    The platform's own default, not the parametrized path: macOS reads
    the child's peak directly, Linux through the launch helper, which
    keeps the kernel from flooring it at this process's resident size.
    """
    monkeypatch.undo()
    assert own_peak_rss_bytes() > 64 * MIB
    runner = make_runner(make_spec(SMALL_BENCHMARK), trees, tmp_path)
    rnd = runner.run_round(0)
    for ex, sample in zip(rnd.executions, rnd.samples, strict=True):
        assert sample.max_rss_bytes is not None
        assert 16 * MIB <= sample.max_rss_bytes < 128 * MIB
        assert ex.max_rss_missing is None


def test_the_launch_helper_measures_a_small_benchmark_on_any_platform(
    trees: dict[Arm, Path], tmp_path: Path, launch_path: str
) -> None:
    if launch_path != "helper":
        pytest.skip("the direct path is the one the helper replaces")
    runner = make_runner(make_spec(SMALL_BENCHMARK), trees, tmp_path)
    for sample in runner.run_round(0).samples:
        assert sample.max_rss_bytes is not None
        assert 16 * MIB <= sample.max_rss_bytes < 128 * MIB


def test_the_helper_floor_is_far_below_this_process(
    trees: dict[Arm, Path], tmp_path: Path, launch_path: str
) -> None:
    if launch_path != "helper" or sys.platform != "linux":
        pytest.skip("the floor is Linux-only and the helper's to lower")
    runner = make_runner(make_spec("pass"), trees, tmp_path)
    for ex in runner.run_round(0).executions:
        if ex.max_rss_bytes is None:
            reason = ex.max_rss_missing or ""
            floor = int(reason.split("peak of ")[1].split(" bytes")[0])
            assert "launch helper" in reason
            assert floor < 32 * MIB
            assert floor < own_peak_rss_bytes() // 2


@pytest.mark.skipif(
    sys.platform != "linux", reason="the ru_maxrss floor is Linux-only"
)
def test_a_tiny_benchmark_on_the_direct_path_is_unknown_on_linux(
    trees: dict[Arm, Path], tmp_path: Path, launch_path: str
) -> None:
    if launch_path != "direct":
        pytest.skip("the helper path is the one that avoids this")
    if own_peak_rss_bytes() < 64 * MIB:
        pytest.skip("this process is too small to floor the child's RSS")
    rnd = make_runner(make_spec(), trees, tmp_path).run_round(0)
    for ex, sample in zip(rnd.executions, rnd.samples, strict=True):
        assert sample.max_rss_bytes is None
        assert ex.max_rss_missing is not None
        assert "launching process" in ex.max_rss_missing


def test_reported_value_with_wall_alongside(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    code = (
        "import sys, time\n"
        "print('warming up')\n"
        "print('noise', file=sys.stderr)\n"
        "time.sleep(0.1)\n"
        "print('PERFPROOF {\"value\": 2.5}')\n"
        "print('done')"
    )
    spec = make_spec(code, kind="reported")
    for sample in make_runner(spec, trees, tmp_path).run_round(0).samples:
        assert sample.value == 2.5
        assert sample.wall_seconds >= 0.1


def test_reported_integer_value_becomes_float(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    spec = make_spec("print('PERFPROOF {\"value\": 3}')", kind="reported")
    for sample in make_runner(spec, trees, tmp_path).run_round(0).samples:
        assert sample.value == 3.0
        assert type(sample.value) is float


@pytest.mark.parametrize(
    ("stdout", "match"),
    [
        ("no value here\n", "no PERFPROOF line"),
        ('PERFPROOF {"value": 1}\nPERFPROOF {"value": 2}\n', "2 PERFPROOF"),
        ('PERFPROOF {"value": 0}\n', "positive"),
        ('PERFPROOF {"value": -1.5}\n', "positive"),
        ('PERFPROOF {"value": NaN}\n', "not valid JSON"),
        ('PERFPROOF {"value": Infinity}\n', "not valid JSON"),
        ('PERFPROOF {"value": 1e400}\n', "finite"),
        ('PERFPROOF {"value": "1.5"}\n', "a number"),
        ('PERFPROOF {"value": true}\n', "a number"),
        ('PERFPROOF {"value": 1, "unit": "ms"}\n', "exactly one key"),
        ("PERFPROOF [1]\n", "exactly one key"),
        ("PERFPROOF value=1\n", "not valid JSON"),
        ('PERFPROOF {"value": 1, "value": 2}\n', "repeats a key"),
    ],
)
def test_reported_refuses_bad_output(
    trees: dict[Arm, Path], tmp_path: Path, stdout: str, match: str
) -> None:
    code = f"import sys; sys.stdout.write({stdout!r})"
    spec = make_spec(code, kind="reported")
    with pytest.raises(ExecutionError, match=match) as info:
        make_runner(spec, trees, tmp_path).run_round(0)
    assert info.value.stdout.read_text() == stdout


def test_reported_guardrails_are_the_applicable_ones_the_runner_lacks() -> (
    None
):
    spec = make_spec(
        kind="reported",
        guardrails=(
            GuardrailSpec(
                metric="peak_memory",
                direction="lower_is_better",
                unit="bytes",
                allowance=0.1,
                allowance_source="default",
            ),
            reported_guardrail("p99_latency"),
            reported_guardrail("peak_gpu_memory", applicable=False),
            GuardrailSpec(
                metric="throughput",
                direction="higher_is_better",
                unit="req/s",
                allowance=0.05,
                allowance_source="project",
            ),
        ),
    )
    assert reported_guardrails(spec) == ("p99_latency", "throughput")


def test_a_reported_guardrail_is_read_from_the_objectives_line(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    code = (
        "print('warming up')\n"
        'print(\'PERFPROOF {"value": 2.5, "p99_latency": 41.25}\')\n'
        "print('done')"
    )
    spec = make_spec(code, kind="reported", guardrails=(reported_guardrail(),))
    rnd = make_runner(spec, trees, tmp_path).run_round(0)
    for ex, sample in zip(rnd.executions, rnd.samples, strict=True):
        assert sample.value == 2.5
        assert ex.reported == {"p99_latency": 41.25}


def test_a_reported_guardrail_needs_no_reported_objective(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    code = "print('PERFPROOF {\"p99_latency\": 12}')"
    spec = make_spec(code, guardrails=(reported_guardrail(),))
    rnd = make_runner(spec, trees, tmp_path).run_round(0)
    for ex, sample in zip(rnd.executions, rnd.samples, strict=True):
        assert sample.value == sample.wall_seconds
        assert ex.reported == {"p99_latency": 12.0}
        assert type(ex.reported["p99_latency"]) is float


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        ("reported", "print('PERFPROOF {\"value\": 2.5}')"),
        ("wall", "print('PERFPROOF {}')"),
        ("wall", "print('no report at all')"),
    ],
    ids=["line-without-it", "empty-object", "no-line"],
)
def test_a_reported_guardrail_the_line_leaves_out_is_unknown(
    trees: dict[Arm, Path], tmp_path: Path, kind: MetricKind, code: str
) -> None:
    spec = make_spec(code, kind=kind, guardrails=(reported_guardrail(),))
    rnd = make_runner(spec, trees, tmp_path).run_round(0)
    for ex in rnd.executions:
        assert ex.reported == {}


@pytest.mark.parametrize(
    ("kind", "body", "match"),
    [
        ("reported", '{"value": 1, "p99_latency": 0}', "positive"),
        ("reported", '{"value": 1, "p99_latency": -3}', "positive"),
        ("reported", '{"value": 1, "p99_latency": "9"}', "a number"),
        ("reported", '{"value": 1, "p99_latency": null}', "a number"),
        ("reported", '{"value": 1, "p99_latency": true}', "a number"),
        ("reported", '{"value": 1, "p99_latency": 1e400}', "finite"),
        ("reported", '{"value": 1, "p99_latency": NaN}', "not valid JSON"),
        (
            "reported",
            '{"value": 1, "p99_latency": 2, "p99_latency": 3}',
            "repeats",
        ),
        ("reported", '{"value": 1, "p50_latency": 2}', "optionally"),
        ("reported", '{"p99_latency": 2}', "optionally"),
        ("wall", '{"value": 1, "p99_latency": 2}', "among"),
        ("wall", '{"p50_latency": 2}', "among"),
        ("wall", "[1]", "among"),
    ],
)
def test_a_malformed_reported_guardrail_is_refused(
    trees: dict[Arm, Path],
    tmp_path: Path,
    kind: MetricKind,
    body: str,
    match: str,
) -> None:
    stdout = f"PERFPROOF {body}\n"
    code = f"import sys; sys.stdout.write({stdout!r})"
    spec = make_spec(code, kind=kind, guardrails=(reported_guardrail(),))
    with pytest.raises(ExecutionError, match=match) as info:
        make_runner(spec, trees, tmp_path).run_round(0)
    assert info.value.cause == "output"


def test_two_lines_are_refused_when_only_a_guardrail_is_reported(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    code = (
        "print('PERFPROOF {\"p99_latency\": 1}')\n"
        "print('PERFPROOF {\"p99_latency\": 2}')"
    )
    spec = make_spec(code, guardrails=(reported_guardrail(),))
    with pytest.raises(ExecutionError, match="2 PERFPROOF"):
        make_runner(spec, trees, tmp_path).run_round(0)


def test_a_line_is_not_read_when_nothing_is_reported(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    code = "print('PERFPROOF not even json')"
    spec = make_spec(code, guardrails=(reported_guardrail(applicable=False),))
    for ex in make_runner(spec, trees, tmp_path).run_round(0).executions:
        assert ex.reported == {}


def test_reported_streams_past_huge_lines(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    code = (
        "import sys\n"
        "sys.stdout.write('y' * (8 * 1024 * 1024))\n"
        "sys.stdout.write('\\nPERFPROOF {\"value\": 4.0}\\n')"
    )
    spec = make_spec(code, kind="reported")
    for sample in make_runner(spec, trees, tmp_path).run_round(0).samples:
        assert sample.value == 4.0


def test_reported_refuses_overlong_perfproof_line(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    code = (
        "import sys\n"
        'sys.stdout.write(\'PERFPROOF {"value": 1.0}\' + " " * 100000)'
    )
    spec = make_spec(code, kind="reported")
    with pytest.raises(ExecutionError, match="longer than"):
        make_runner(spec, trees, tmp_path).run_round(0)


def test_output_goes_to_files(trees: dict[Arm, Path], tmp_path: Path) -> None:
    code = "import sys; print('to-out'); print('to-err', file=sys.stderr)"
    rnd = make_runner(make_spec(code), trees, tmp_path).run_round(3)
    for ex in rnd.executions:
        assert ex.stdout.read_text() == "to-out\n"
        assert ex.stderr.read_text() == "to-err\n"
        assert ex.stdout.parent == tmp_path / "logs"
        assert ex.stdout.name == f"round-3.{ex.arm}.stdout"
        assert ex.metric_output is None


def test_env_cwd_and_base_env(trees: dict[Arm, Path], tmp_path: Path) -> None:
    for tree in trees.values():
        (tree / "sub").mkdir()
    code = (
        "import json, os\n"
        "print(json.dumps([os.getcwd(), os.environ.get('PP_DECL'), "
        "os.environ.get('PP_BASE'), os.environ.get('PP_BOTH')]))"
    )
    spec = make_spec(
        code, env={"PP_DECL": "d", "PP_BOTH": "declared"}, cwd="sub"
    )
    base = {"PATH": os.environ["PATH"], "PP_BASE": "b", "PP_BOTH": "base"}
    rnd = make_runner(spec, trees, tmp_path, base_env=base).run_round(0)
    for ex in rnd.executions:
        cwd, decl, from_base, both = json.loads(ex.stdout.read_text())
        assert Path(cwd) == trees[ex.arm].resolve() / "sub"
        assert (decl, from_base, both) == ("d", "b", "declared")
        assert ex.launch.cwd == str(trees[ex.arm].resolve() / "sub")
        assert ex.launch.env == {"PP_DECL": "d", "PP_BOTH": "declared"}


def test_resolves_relative_executable_in_tree(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    for tree in trees.values():
        write_tool(tree / "tool.sh", 'echo "$0"')
    spec = make_spec(command=("./tool.sh", "arg"))
    rnd = make_runner(spec, trees, tmp_path).run_round(0)
    hashes: set[str] = set()
    for ex in rnd.executions:
        exe = str(trees[ex.arm].resolve() / "tool.sh")
        assert ex.launch.executable == exe
        assert ex.launch.hashed_executable == "tool.sh"
        assert ex.launch.argv == ("./tool.sh", "arg")
        assert ex.stdout.read_text() == f"{exe}\n"
        hashes.add(ex.launch.command_hash)
    assert len(hashes) == 1
    for sample, ex in zip(rnd.samples, rnd.executions, strict=True):
        assert sample.command_hash == ex.launch.command_hash
        assert sample.version == ex.arm


def test_argv0_is_kept_as_written(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "shalias").symlink_to("/bin/sh")
    script = "ps -o args= -p $$; true"
    spec = make_spec(
        command=("shalias", "-c", script),
        env={"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"},
    )
    for ex in make_runner(spec, trees, tmp_path).run_round(0).executions:
        assert ex.stdout.read_text().split()[0] == "shalias"
        assert ex.launch.argv == ("shalias", "-c", script)
        assert ex.launch.executable == str(bindir.resolve() / "shalias")
        assert ex.launch.hashed_executable == ex.launch.executable


def test_resolves_bare_name_on_declared_path(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    write_tool(bindir / "pptool", "echo ran")
    spec = make_spec(command=("pptool",), env={"PATH": str(bindir)})
    for ex in make_runner(spec, trees, tmp_path).run_round(0).executions:
        assert ex.launch.executable == str(bindir.resolve() / "pptool")
        assert ex.stdout.read_text() == "ran\n"


def test_missing_executable_is_a_launch_failure(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    spec = make_spec(command=("no-such-perfproof-tool",))
    runner = make_runner(spec, trees, tmp_path)
    with pytest.raises(ExecutionError, match="no-such-perfproof-tool") as info:
        runner.run_round(0)
    assert (info.value.cause, info.value.arm) == ("launch", runner.orders[0])


def test_candidate_missing_its_program_fails_the_candidate(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    write_tool(trees["baseline"] / "tool.sh", "true")
    runner = make_runner(make_spec(command=("./tool.sh",)), trees, tmp_path)
    with pytest.raises(ExecutionError, match=r"tool\.sh") as info:
        runner.run_round(runner.orders.index("baseline"))
    err = info.value
    assert (err.arm, err.cause, err.exit_code, err.wall_seconds) == (
        "candidate",
        "launch",
        None,
        None,
    )
    assert err.stdout.is_file()
    assert err.stderr.is_file()


def test_candidate_missing_its_cwd_fails_the_candidate(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    (trees["baseline"] / "sub").mkdir()
    runner = make_runner(make_spec(cwd="sub"), trees, tmp_path)
    with pytest.raises(ExecutionError, match="'sub' does not exist") as info:
        runner.run_round(runner.orders.index("baseline"))
    err = info.value
    assert (err.arm, err.cause, err.exit_code, err.wall_seconds) == (
        "candidate",
        "launch",
        None,
        None,
    )


def gate_procedure(
    spec: BenchmarkSpec, trees: Mapping[Arm, Path], samples: list[Sample]
) -> GateResult:
    executables: dict[Arm, str] = {}
    for arm, root in trees.items():
        found = resolve_executable(spec, root.resolve(), os.environ)
        assert found is not None
        executables[arm] = normalize_launch_path(found, root.resolve())
    return procedure_gate(
        spec,
        samples,
        spec_hashes={"baseline": spec.spec_hash, "candidate": spec.spec_hash},
        perfproof_versions={"baseline": "0.1.0", "candidate": "0.1.0"},
        executables=executables,
    )


def test_runner_samples_pass_the_procedure_gate(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    for tree in trees.values():
        (tree / "bin").mkdir()
        write_tool(tree / "bin" / "tool", 'echo "$@"')
    command = ("tool", "--n", "1")
    spec = make_spec(command=command, env={"PATH": "bin"})
    runner = make_runner(spec, trees, tmp_path)
    samples = [s for r in runner.rounds(0, 3) for s in r.samples]
    result = gate_procedure(spec, trees, samples)
    assert (result.status, result.differences) == ("passed", ())
    other = make_spec(command=("tool", "--n", "2"), env={"PATH": "bin"})
    stray = Runner(other, trees, tmp_path / "other", order_seed=7)
    swapped = [s for s in samples if s.round != 1]
    swapped += stray.run_round(1).samples
    result = gate_procedure(spec, trees, swapped)
    assert result.status == "failed"
    assert [d.item for d in result.differences] == ["round 1 command_hash"]


def test_command_hash_tracks_declared_env(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    def one_hash(spec: BenchmarkSpec, logs: str) -> set[str]:
        runner = Runner(spec, trees, tmp_path / logs, order_seed=0)
        return {e.launch.command_hash for e in runner.run_round(0).executions}

    plain = one_hash(make_spec(), "a")
    again = one_hash(make_spec(), "b")
    other = one_hash(make_spec(env={"PP_X": "1"}), "c")
    assert len(plain) == 1
    assert plain == again
    assert plain != other


def test_round_orders_are_reproducible() -> None:
    first = round_orders(123, 200)
    assert first == round_orders(123, 200)
    assert first[:50] == round_orders(123, 50)
    assert first != round_orders(124, 200)
    assert set(first) == {"baseline", "candidate"}
    assert 60 < first.count("baseline") < 140
    with pytest.raises(ValueError, match="seed"):
        round_orders(-1, 5)


def test_rounds_follow_seeded_order(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    code = "import time; print(time.monotonic_ns())"
    spec = make_spec(code)
    runner = make_runner(spec, trees, tmp_path, seed=99)
    expected = round_orders(99, spec.budget.max_rounds)
    rounds = list(runner.rounds(0, 12))
    assert [r.index for r in rounds] == list(range(12))
    assert {r.samples[0].version for r in rounds} == {
        "baseline",
        "candidate",
    }
    for rnd in rounds:
        first, second = rnd.samples
        assert first.version == expected[rnd.index]
        assert {first.version, second.version} == {"baseline", "candidate"}
        assert (first.round, first.position) == (rnd.index, 0)
        assert (second.round, second.position) == (rnd.index, 1)
        started = [int(e.stdout.read_text()) for e in rnd.executions]
        assert started[0] < started[1]


def test_resumed_rounds_keep_their_order(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    runner = make_runner(make_spec(), trees, tmp_path, seed=5)
    resumed = list(runner.rounds(start=15))
    expected = round_orders(5, 20)
    assert [r.index for r in resumed] == [15, 16, 17, 18, 19]
    assert [r.samples[0].version for r in resumed] == list(expected[15:])


def test_round_orders_come_in_balanced_pairs() -> None:
    longest = 41
    firsts_of_pairs: set[Arm] = set()
    for seed in range(200):
        full = round_orders(seed, longest)
        raw = np.random.PCG64(seed).random_raw((longest + 1) // 2)
        draws = [int(bit) for bit in (raw >> np.uint64(63)).tolist()]
        # Draw j picks round 2j's first version, and round 2j + 1 is
        # the other order; an odd last round keeps its own draw.
        assert full == tuple(
            ("baseline", "candidate")[draws[i // 2] ^ (i % 2)]
            for i in range(longest)
        )
        for n in range(longest + 1):
            orders = round_orders(seed, n)
            assert orders == full[:n]
            if n % 2 == 0:
                assert orders.count("baseline") == n // 2
        for j in range(longest // 2):
            assert full[2 * j] != full[2 * j + 1]
        firsts_of_pairs.update(full[0::2])
        assert len(set(full[0::2])) == 2
    assert firsts_of_pairs == {"baseline", "candidate"}


def test_every_look_splits_its_rounds_between_the_orders(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    # An odd budget, so the budget's own final look is cut to the
    # complete pairs; any round can also be where the time runs out.
    budget = Budget(max_rounds=21, max_seconds=600.0)
    looks = LookSchedule(n0=10, k=4, max_looks=4)
    spec = make_spec().model_copy(update={"budget": budget, "looks": looks})
    runner = make_runner(spec, trees, tmp_path, seed=11)
    firsts = [r.samples[0].version for r in runner.rounds()]
    collected = np.arange(1, 22, dtype=np.int64)
    sizes: set[int] = set()
    for exhausted in (collected == 21, np.ones(21, dtype=np.bool_)):
        plan = plan_looks(looks, collected, exhausted, mode="one_host")
        sizes.update(int(n) for n in plan.rounds[plan.rounds > 0])
    assert sizes == {10, 12, 14, 16, 18, 20}
    for n in sizes:
        assert firsts[:n].count("baseline") == n // 2
        assert firsts[:n].count("candidate") == n // 2


# Reset

RESET_LOG = (
    "import os, sys\n"
    "with open(sys.argv[1], 'a') as f:\n"
    "    f.write(f'{sys.argv[2]} {os.getcwd()}\\n')\n"
)


def logging_spec(
    events: Path, *, reset_code: str = "", warmup: int = 0
) -> BenchmarkSpec:
    reset = ResetSpec(
        command=(PY, "-c", reset_code + RESET_LOG, str(events), "reset"),
        timeout_seconds=30.0,
    )
    spec = make_spec(
        command=(PY, "-c", RESET_LOG, str(events), "run"), warmup=warmup
    )
    return spec.model_copy(update={"reset": reset})


def test_reset_runs_once_before_every_execution(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    events = tmp_path / "events.txt"
    runner = make_runner(logging_spec(events, warmup=2), trees, tmp_path)
    executions = list(runner.warmup())
    for rnd in runner.rounds(0, 4):
        executions.extend(rnd.executions)
    executions.append(runner.run_single("baseline", "recheck"))
    assert len(executions) == 2 * 2 + 4 * 2 + 1
    lines = events.read_text().splitlines()
    resets = [line for line in lines if line.startswith("reset ")]
    assert len(resets) == len(executions)
    expected: list[str] = []
    for ex in executions:
        where = trees[ex.arm].resolve()
        expected += [f"reset {where}", f"run {where}"]
    assert lines == expected


def test_reset_runs_outside_the_timed_region(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    events = tmp_path / "events.txt"
    slow = "import time\ntime.sleep(0.3)\n"
    spec = logging_spec(events, reset_code=slow)
    for sample in make_runner(spec, trees, tmp_path).run_round(0).samples:
        assert sample.wall_seconds < 0.3
    lines = events.read_text().splitlines()
    assert [line.split()[0] for line in lines] == ["reset", "run"] * 2


def test_reset_gets_the_benchmark_environment(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    dump = tmp_path / "reset-env.json"
    code = (
        "import json, os, sys\n"
        "json.dump(dict(os.environ), open(sys.argv[1], 'w'))"
    )
    reset = ResetSpec(
        command=(PY, "-c", code, str(dump)), timeout_seconds=30.0
    )
    spec = make_spec(env={"PP_DECL": "d"}).model_copy(update={"reset": reset})
    base = {
        "PATH": os.environ["PATH"] + os.pathsep + str(tmp_path),
        "PP_AGENT_TOKEN": "x",
    }
    make_runner(spec, trees, tmp_path, base_env=base).run_single(
        "candidate", "single"
    )
    seen = json.loads(dump.read_text())
    assert seen["PATH"] == base["PATH"]
    assert seen["PP_DECL"] == "d"
    assert "PP_AGENT_TOKEN" not in seen


def test_failed_reset_is_no_execution_and_blames_neither(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    events = tmp_path / "events.txt"
    (trees["candidate"] / "reset-fails").touch()
    fails = (
        "import os, sys\n"
        "if os.path.exists('reset-fails'):\n"
        "    sys.exit('reset broke')\n"
    )
    runner = make_runner(
        logging_spec(events, reset_code=fails), trees, tmp_path, seed=3
    )
    index = runner.orders.index("baseline")
    with pytest.raises(ResetFailedError) as info:
        runner.run_round(index)
    err = info.value
    assert not isinstance(err, ExecutionError)
    assert (err.arm, err.label, err.round) == (
        "candidate",
        f"round-{index}",
        index,
    )
    gate = err.gate
    assert (gate.gate, gate.status) == ("side_effects", "failed")
    assert gate.detail.startswith(f"before round-{index} candidate: ")
    assert gate.output is not None
    assert gate.output.exit_code == 1
    assert "reset broke" in gate.output.stderr
    # The baseline ran after its reset; the candidate never ran.
    baseline = trees["baseline"].resolve()
    assert events.read_text().splitlines() == [
        f"reset {baseline}",
        f"run {baseline}",
    ]


def test_spec_without_reset_runs_none(
    trees: dict[Arm, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_reset(*args: object) -> None:
        msg = "run_reset called for a spec without a reset"
        raise AssertionError(msg)

    monkeypatch.setattr(runner_module, "run_reset", no_reset)
    make_runner(make_spec(warmup=1), trees, tmp_path).warmup()


# Single execution


def test_run_single_runs_one_execution_of_one_arm(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    events = tmp_path / "events.txt"
    runner = make_runner(logging_spec(events), trees, tmp_path)
    ex = runner.run_single("baseline", "recheck")
    assert (ex.arm, ex.label) == ("baseline", "recheck")
    assert ex.value == ex.wall_seconds > 0
    assert ex.launch.cwd == str(trees["baseline"].resolve())
    assert sorted(p.name for p in (tmp_path / "logs").iterdir()) == [
        "recheck.baseline.stderr",
        "recheck.baseline.stdout",
    ]
    baseline = trees["baseline"].resolve()
    assert events.read_text().splitlines() == [
        f"reset {baseline}",
        f"run {baseline}",
    ]


def test_failed_single_execution_has_no_round(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    runner = make_runner(make_spec("raise SystemExit(4)"), trees, tmp_path)
    with pytest.raises(ExecutionError) as info:
        runner.run_single("candidate", "check")
    err = info.value
    assert (err.arm, err.label, err.round) == ("candidate", "check", None)
    assert (err.cause, err.exit_code) == ("exit", 4)


@pytest.mark.parametrize(
    "label", ["", "round-3", "warmup-0", "../up", "a/b", ".hidden", "-x"]
)
def test_run_single_refuses_unsafe_labels(
    trees: dict[Arm, Path], tmp_path: Path, label: str
) -> None:
    runner = make_runner(make_spec(), trees, tmp_path)
    with pytest.raises(ValueError, match="label"):
        runner.run_single("baseline", label)
    assert not (tmp_path / "logs").exists() or not list(
        (tmp_path / "logs").iterdir()
    )


def test_run_single_refuses_an_unknown_arm(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    runner = make_runner(make_spec(), trees, tmp_path)
    with pytest.raises(ValueError, match="arm"):
        runner.run_single(cast("Arm", "other"), "check")


def test_warmup_alternates_and_makes_no_samples(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    runner = make_runner(make_spec(warmup=2), trees, tmp_path)
    runs = runner.warmup()
    assert [e.arm for e in runs] == [
        "baseline",
        "candidate",
        "baseline",
        "candidate",
    ]
    assert [e.stdout.name for e in runs] == [
        "warmup-0.baseline.stdout",
        "warmup-0.candidate.stdout",
        "warmup-1.baseline.stdout",
        "warmup-1.candidate.stdout",
    ]
    assert not list((tmp_path / "logs").glob("round-*"))


@pytest.mark.parametrize(
    ("code", "kind", "cause", "exit_code", "match"),
    [
        ("import sys; sys.exit(3)", "wall", "exit", 3, "status 3"),
        ("import no_such_module_pp", "wall", "exit", 1, "status 1"),
        (
            "import os, signal; os.kill(os.getpid(), signal.SIGTERM)",
            "wall",
            "signal",
            -signal.SIGTERM,
            "SIGTERM",
        ),
        ("import os; os.abort()", "cpu", "signal", -signal.SIGABRT, "SIGABRT"),
        (
            "print('PERFPROOF {\"value\": 1.0}'); raise SystemExit(2)",
            "reported",
            "exit",
            2,
            "status 2",
        ),
    ],
)
def test_failed_execution_gives_no_sample(
    trees: dict[Arm, Path],
    tmp_path: Path,
    code: str,
    kind: MetricKind,
    cause: str,
    exit_code: int,
    match: str,
) -> None:
    for tree in trees.values():
        (tree / "fail").touch()
    guarded = f"import os\nif os.path.exists('fail'):\n    exec({code!r})"
    if kind == "reported":
        guarded += "\nprint('PERFPROOF {\"value\": 1.0}')"
    spec = make_spec(guarded, kind=kind)
    runner = make_runner(spec, trees, tmp_path)
    with pytest.raises(ExecutionError, match=match) as info:
        runner.run_round(4)
    err = info.value
    assert (err.cause, err.exit_code) == (cause, exit_code)
    assert (err.arm, err.round, err.label) == (
        runner.orders[4],
        4,
        "round-4",
    )
    assert err.stdout.is_file()
    assert err.stderr.is_file()
    assert str(err.stderr) in str(err)


def test_candidate_failure_names_the_candidate(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    (trees["candidate"] / "fail").touch()
    code = (
        "import os, sys\n"
        "if os.path.exists('fail'):\n"
        "    sys.exit('candidate broke')"
    )
    runner = make_runner(make_spec(code), trees, tmp_path, seed=3)
    index = runner.orders.index("baseline")
    with pytest.raises(ExecutionError) as info:
        runner.run_round(index)
    assert (info.value.arm, info.value.cause) == ("candidate", "exit")
    assert info.value.stderr.read_text() == "candidate broke\n"


def test_failed_warmup_gives_no_execution(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    (trees["baseline"] / "fail").touch()
    code = "import os, sys\nif os.path.exists('fail'): sys.exit(5)"
    runner = make_runner(make_spec(code, warmup=1), trees, tmp_path)
    with pytest.raises(ExecutionError, match="status 5") as info:
        runner.warmup()
    err = info.value
    assert (err.arm, err.round, err.label) == ("baseline", None, "warmup-0")
    assert err.exit_code == 5


def test_timeout_kills_the_process_group(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    pid_file = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen(['sleep', '60'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(60)"
    )
    spec = make_spec(code, timeout=1.0)
    start = time.monotonic()
    with pytest.raises(ExecutionError, match="timed out") as info:
        make_runner(spec, trees, tmp_path).run_round(0)
    assert time.monotonic() - start < 10
    assert info.value.exit_code == -signal.SIGKILL
    assert info.value.cause == "timeout"
    grandchild = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"grandchild {grandchild} outlived the timeout")


@pytest.mark.skipif(
    sys.platform != "linux", reason="the SIGCHLD fallback is Linux-only"
)
def test_sigchld_fallback_without_pidfd(
    trees: dict[Arm, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "perfproof.bench.runner._pidfd_available", lambda: False
    )
    handler = signal.getsignal(signal.SIGCHLD)
    spec = make_spec("import time; time.sleep(0.2)")
    for sample in make_runner(spec, trees, tmp_path).run_round(0).samples:
        assert 0.2 <= sample.wall_seconds < 1.2
    slow = make_spec("import time; time.sleep(30)", timeout=0.5)
    start = time.monotonic()
    with pytest.raises(ExecutionError) as info:
        Runner(slow, trees, tmp_path / "slow", order_seed=0).run_round(0)
    assert info.value.cause == "timeout"
    assert time.monotonic() - start < 10
    assert signal.getsignal(signal.SIGCHLD) == handler
    assert signal.set_wakeup_fd(-1) == -1


@pytest.mark.skipif(
    sys.platform != "linux", reason="the SIGCHLD fallback is Linux-only"
)
def test_sigchld_fallback_forwards_the_callers_sigchld(
    trees: dict[Arm, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "perfproof.bench.runner._pidfd_available", lambda: False
    )
    calls: list[int] = []

    def caller_handler(signum: int, frame: FrameType | None) -> None:
        calls.append(signum)

    previous = signal.signal(signal.SIGCHLD, caller_handler)
    try:
        own_child = subprocess.Popen(["sleep", "0.1"])
        spec = make_spec("import time; time.sleep(0.6)")
        make_runner(spec, trees, tmp_path).run_round(0)
        assert own_child.wait(timeout=10) == 0
    finally:
        signal.signal(signal.SIGCHLD, previous)
    assert calls
    assert calls[0] == signal.SIGCHLD


@pytest.mark.skipif(
    sys.platform != "linux", reason="the SIGCHLD fallback is Linux-only"
)
def test_sigchld_fallback_refuses_a_handler_it_cannot_restore(
    trees: dict[Arm, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "perfproof.bench.runner._pidfd_available", lambda: False
    )

    def no_python_handler(signum: int) -> None:
        return None

    monkeypatch.setattr(signal, "getsignal", no_python_handler)
    with pytest.raises(RunnerError, match="outside Python"):
        make_runner(make_spec(), trees, tmp_path).run_round(0)


@pytest.mark.skipif(
    sys.platform != "linux", reason="the SIGCHLD fallback is Linux-only"
)
def test_sigchld_fallback_works_above_fd_1024(
    trees: dict[Arm, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "perfproof.bench.runner._pidfd_available", lambda: False
    )
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    want = 1200
    if hard != resource.RLIM_INFINITY and hard < want:
        pytest.skip(f"RLIMIT_NOFILE hard limit {hard} is below {want}")
    resource.setrlimit(resource.RLIMIT_NOFILE, (max(soft, want), hard))
    fillers: list[int] = []
    try:
        while not fillers or fillers[-1] < 1100:
            fillers.append(os.open(os.devnull, os.O_RDONLY))
        spec = make_spec("import time; time.sleep(0.1)")
        for s in make_runner(spec, trees, tmp_path).run_round(0).samples:
            assert s.wall_seconds >= 0.1
    finally:
        for fd in fillers:
            os.close(fd)
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


def test_a_child_reaped_by_a_caller_handler_is_unknown(
    trees: dict[Arm, Path], tmp_path: Path, launch_path: str
) -> None:
    if launch_path != "direct":
        pytest.skip("through the helper the caller never owns the child")
    if sys.platform == "linux" and not runner_module._pidfd_available():  # pyright: ignore[reportPrivateUsage]
        pytest.skip("the SIGCHLD fallback replaces the caller's handler")

    def reap_everything(signum: int, frame: FrameType | None) -> None:
        with suppress(ChildProcessError):
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass

    previous = signal.signal(signal.SIGCHLD, reap_everything)
    try:
        spec = make_spec("import time; time.sleep(0.3)")
        with pytest.raises(RunnerError, match="reaped") as info:
            make_runner(spec, trees, tmp_path).run_round(0)
    finally:
        signal.signal(signal.SIGCHLD, previous)
    assert type(info.value) is RunnerError
    assert "perfproof's own process" in str(info.value)


def test_a_caller_handler_that_reaps_cannot_lose_a_helper_run(
    trees: dict[Arm, Path], tmp_path: Path, launch_path: str
) -> None:
    if launch_path != "helper":
        pytest.skip("the direct path reports this as an error instead")

    def reap_everything(signum: int, frame: FrameType | None) -> None:
        with suppress(ChildProcessError):
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass

    previous = signal.signal(signal.SIGCHLD, reap_everything)
    try:
        spec = make_spec("import time; time.sleep(0.3)")
        samples = make_runner(spec, trees, tmp_path).run_round(0).samples
    finally:
        signal.signal(signal.SIGCHLD, previous)
    for sample in samples:
        assert sample.exit_code == 0
        assert sample.wall_seconds >= 0.3


def test_an_oversized_environment_blames_neither_arm(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    base = {**os.environ, "BIG": "x" * (4 << 20)}
    runner = make_runner(make_spec(), trees, tmp_path, base_env=base)
    for index in range(4):
        with pytest.raises(
            RunnerError, match="Argument list too long"
        ) as info:
            runner.run_round(index)
        assert type(info.value) is RunnerError
        assert "perfproof's own process" in str(info.value)


def test_running_out_of_fds_blames_neither_arm(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    runner = make_runner(make_spec(), trees, tmp_path)
    runner.run_round(0)
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(soft, 256), hard))
    fillers: list[int] = []
    try:
        with suppress(OSError):
            while True:
                fillers.append(os.open(os.devnull, os.O_RDONLY))
        for _ in range(2):
            os.close(fillers.pop())
        with pytest.raises(RunnerError, match="Too many open files") as info:
            runner.run_round(1)
    finally:
        for fd in fillers:
            os.close(fd)
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
    assert type(info.value) is RunnerError
    assert "perfproof's own process" in str(info.value)


def test_a_candidate_that_cannot_start_fails_the_candidate(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    write_tool(trees["baseline"] / "tool.sh", "true")
    bad = trees["candidate"] / "tool.sh"
    bad.write_text("#!/no/such/interpreter\n")
    bad.chmod(bad.stat().st_mode | stat.S_IXUSR)
    runner = make_runner(make_spec(command=("./tool.sh",)), trees, tmp_path)
    with pytest.raises(ExecutionError, match="could not start") as info:
        runner.run_round(runner.orders.index("baseline"))
    err = info.value
    assert (err.arm, err.cause, err.exit_code, err.wall_seconds) == (
        "candidate",
        "launch",
        None,
        None,
    )


def test_timeouts_the_waits_cannot_represent_are_refused(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    huge = make_spec(timeout=MAX_TIMEOUT_SECONDS * 2)
    with pytest.raises(ValueError, match="at most"):
        make_runner(huge, trees, tmp_path)
    endless = make_spec().model_copy(update={"timeout_seconds": math.inf})
    with pytest.raises(ValueError, match="finite"):
        make_runner(endless, trees, tmp_path)
    longest = make_spec(timeout=MAX_TIMEOUT_SECONDS)
    for s in make_runner(longest, trees, tmp_path / "ok").run_round(0).samples:
        assert s.exit_code == 0


def test_logs_are_never_overwritten(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    runner = make_runner(make_spec(), trees, tmp_path)
    runner.run_round(0)
    with pytest.raises(RunnerError, match="exists"):
        runner.run_round(0)


def test_runner_leaves_no_threads_and_restores_gc(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    threads = threading.active_count()
    assert gc.isenabled()
    runner = make_runner(make_spec(warmup=1), trees, tmp_path)
    runner.warmup()
    list(runner.rounds(0, 3))
    assert threading.active_count() == threads
    assert gc.isenabled()


def test_rejects_bad_arguments(trees: dict[Arm, Path], tmp_path: Path) -> None:
    spec = make_spec()
    with pytest.raises(ValueError, match="candidate"):
        Runner(spec, {"baseline": trees["baseline"]}, tmp_path, order_seed=0)
    with pytest.raises(ValueError, match="seed"):
        Runner(spec, trees, tmp_path, order_seed=-1)
    missing: dict[Arm, Path] = {**trees, "candidate": tmp_path / "nope"}
    with pytest.raises(ValueError, match="not a directory"):
        Runner(spec, missing, tmp_path, order_seed=0)
    runner = make_runner(spec, trees, tmp_path)
    with pytest.raises(ValueError, match="round"):
        runner.run_round(20)
    with pytest.raises(ValueError, match="round"):
        runner.run_round(-1)


@pytest.mark.skipif(
    sys.platform == "linux", reason="checks the non-Linux refusal"
)
def test_counter_is_unavailable_off_linux(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    reason = counter_unavailable_reason("instructions")
    assert reason is not None
    assert "Linux" in reason
    spec = make_spec(kind="counter", event="instructions")
    with pytest.raises(MetricUnavailableError, match="Linux") as info:
        make_runner(spec, trees, tmp_path)
    assert info.value.reason == reason


@pytest.mark.integration
def test_counter_counts_instructions_with_perf(
    trees: dict[Arm, Path], tmp_path: Path
) -> None:
    reason = counter_unavailable_reason("instructions")
    if reason is not None:
        pytest.skip(f"perf stat unavailable: {reason}")
    small = make_spec("pass", kind="counter", event="instructions")
    loop = make_spec(
        "x = 0\nfor i in range(3_000_000): x += i",
        kind="counter",
        event="instructions",
    )
    low = make_runner(small, trees, tmp_path).run_round(0)
    high = Runner(loop, trees, tmp_path / "loop", order_seed=0).run_round(0)
    for rnd in (low, high):
        for ex, sample in zip(rnd.executions, rnd.samples, strict=True):
            assert ex.metric_output is not None
            assert ex.metric_output.exists()
            assert sample.value >= 1
            assert sample.value == int(sample.value)
            assert sample.wall_seconds > 0
            assert ex.launch.argv[1:3] == ("stat", "-x,")
    assert min(s.value for s in high.samples) > max(
        s.value for s in low.samples
    )


def run_helper_by_hand(
    tmp_path: Path,
    code: str,
    *,
    timeout_s: float,
    fork_exec: bool,
    close_lifeline_when: Path | None = None,
    send_stop: bool = True,
    program: str = PY,
) -> dict[str, object]:
    """Speak the launch helper's protocol directly, without the runner.

    Starts the helper on a benchmark running ``code`` (``program`` is
    the executable; the argv is the interpreter's), on the ``fork``
    path or the one it picks by itself, closes the lifeline once the
    file ``close_lifeline_when`` exists (never when ``None``), and
    returns the helper's report.
    """
    out = (tmp_path / "out").open("wb")
    err = (tmp_path / "err").open("wb")
    life_r, life_w = os.pipe()
    request = marshal.dumps(
        {
            "program": program,
            "argv": [PY, "-c", code],
            "cwd": str(tmp_path),
            "env": dict(os.environ),
            "timeout_ns": int(timeout_s * 1e9),
            "stdout_fd": out.fileno(),
            "stderr_fd": err.fileno(),
            "lifeline_fd": life_r,
            "fork_exec": fork_exec,
        }
    )
    helper = Path(runner_module.__file__).with_name("launch_helper.py")
    proc = subprocess.Popen(
        [PY, "-S", "-I", str(helper)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        pass_fds=(out.fileno(), err.fileno(), life_r),
    )
    os.close(life_r)
    try:
        assert proc.stdin is not None
        proc.stdin.write(request)
        proc.stdin.close()
        if close_lifeline_when is not None:
            deadline = time.monotonic() + 30
            while not close_lifeline_when.exists():
                assert time.monotonic() < deadline
                time.sleep(0.05)
            if send_stop:
                os.write(life_w, b"x")
            os.close(life_w)
            life_w = -1
        assert proc.stdout is not None
        report = proc.stdout.read()
        proc.stdout.close()
        proc.wait(timeout=30)
    finally:
        if life_w >= 0:
            os.close(life_w)
        out.close()
        err.close()
    assert proc.returncode == 0
    return cast("dict[str, object]", marshal.loads(report))  # noqa: S302


@pytest.fixture(params=["auto", "fork"])
def fork_exec(request: pytest.FixtureRequest, launch_path: str) -> bool:
    """Run a test of the helper's protocol once per spawn path.

    ``auto`` is the path the helper picks on this host and ``fork`` the
    one it falls back to under emulation. The tests speak to the helper
    directly, so the runner's own launch paths do not apply.
    """
    if launch_path != "helper":
        pytest.skip("speaks to the helper, not through the runner")
    return request.param == "fork"


def process_is_gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_a_stop_byte_on_the_lifeline_kills_the_benchmark_group(
    tmp_path: Path, fork_exec: bool
) -> None:
    pid_file = tmp_path / "pid"
    code = (
        "import os, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)'])\n"
        # Written then renamed: the lifeline closes once the file
        # exists, so it must never exist empty.
        f"open({str(pid_file)!r} + '.tmp', 'w').write(str(child.pid))\n"
        f"os.replace({str(pid_file)!r} + '.tmp', {str(pid_file)!r})\n"
        "time.sleep(60)"
    )
    report = run_helper_by_hand(
        tmp_path,
        code,
        timeout_s=60,
        fork_exec=fork_exec,
        close_lifeline_when=pid_file,
    )
    assert report == {"interrupted": True}
    grandchild = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while not process_is_gone(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert process_is_gone(grandchild)


def test_a_runner_that_dies_does_not_stop_the_benchmark(
    tmp_path: Path, fork_exec: bool
) -> None:
    marker = tmp_path / "started"
    code = (
        f"open({str(marker)!r}, 'w').write('x')\nimport time; time.sleep(1.5)"
    )
    report = run_helper_by_hand(
        tmp_path,
        code,
        timeout_s=60,
        fork_exec=fork_exec,
        close_lifeline_when=marker,
        send_stop=False,
    )
    assert report["status"] == 0
    assert report["timed_out"] is False


def test_the_helper_reports_a_timeout_and_kills_the_group(
    tmp_path: Path, fork_exec: bool
) -> None:
    report = run_helper_by_hand(
        tmp_path,
        "import time; time.sleep(60)",
        timeout_s=0.5,
        fork_exec=fork_exec,
    )
    assert report["timed_out"] is True
    assert report["status"] == signal.SIGKILL
    wall = cast("int", report["wall_ns"])
    assert 0.5e9 <= wall < 5e9


def test_the_helper_runs_the_benchmark_in_its_own_group(
    tmp_path: Path, fork_exec: bool
) -> None:
    marker = tmp_path / "group"
    code = (
        "import os\n"
        f"open({str(marker)!r}, 'w').write("
        "f'{os.getpgrp()} {os.getpid()}')"
    )
    report = run_helper_by_hand(
        tmp_path, code, timeout_s=30, fork_exec=fork_exec
    )
    assert report["status"] == 0
    group, pid = marker.read_text().split()
    assert group == pid
    assert int(group) != os.getpgrp()


def test_the_helper_gives_the_benchmark_its_streams_and_nothing_else(
    tmp_path: Path, fork_exec: bool
) -> None:
    if sys.platform != "linux":
        pytest.skip("reads /proc/self/fd")
    code = (
        "import os, sys\n"
        "sys.stdout.write('out'); sys.stderr.write('err')\n"
        "sys.stdout.flush(); sys.stderr.flush()\n"
        "held = []\n"
        "for fd in range(3, 64):\n"
        "    try:\n"
        "        held.append(os.readlink(f'/proc/self/fd/{fd}'))\n"
        "    except OSError:\n"
        "        pass\n"
        "open('held', 'w').write('\\n'.join(held))"
    )
    report = run_helper_by_hand(
        tmp_path, code, timeout_s=30, fork_exec=fork_exec
    )
    assert report["status"] == 0
    assert (tmp_path / "out").read_text() == "out"
    assert (tmp_path / "err").read_text() == "err"
    # What a plain child of this process holds (on an emulated host the
    # emulator's own descriptors): the helper adds nothing to it, and
    # in particular never the output files or the lifeline.
    plain = tmp_path / "plain"
    plain.mkdir()
    subprocess.run([PY, "-c", code], cwd=plain, check=True, timeout=30)
    held = set((tmp_path / "held").read_text().split("\n"))
    expected = set((plain / "held").read_text().split("\n"))
    extra = {
        t
        for t in held - expected
        if not t.endswith("/held") and not t.startswith("/dev/pts")
    }
    assert not extra


def test_the_helper_reports_why_a_program_cannot_start(
    tmp_path: Path, fork_exec: bool
) -> None:
    missing = str(tmp_path / "missing")
    report = run_helper_by_hand(
        tmp_path, "pass", timeout_s=30, fork_exec=fork_exec, program=missing
    )
    fault = cast("dict[str, object]", report["error"])
    assert fault["errno"] == 2
    assert fault["filename"] == missing
    script = tmp_path / "script"
    script.write_text("echo hi\n")
    script.chmod(0o755)
    report = run_helper_by_hand(
        tmp_path,
        "pass",
        timeout_s=30,
        fork_exec=fork_exec,
        program=str(script),
    )
    fault = cast("dict[str, object]", report["error"])
    assert fault["errno"] == 8
    assert fault["filename"] == str(script)
