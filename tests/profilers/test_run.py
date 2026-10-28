import ast
import dataclasses
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

import pytest

from perfproof.cli import main
from perfproof.commands import profile as profile_command
from perfproof.profilers.folded import rank_file, summarise_file
from perfproof.profilers.run import (
    PYINSTRUMENT_VERSION,
    Profile,
    ProfileError,
    ProfilerName,
    SessionFormatError,
    Timing,
    default_profiler,
    profile_benchmark,
    pyinstrument_to_folded,
)
from perfproof.schema import BenchmarkSpec, Hotspot

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
FIXTURES = ROOT / "tests" / "fixtures"
PYSPY = FIXTURES / "py-spy"
PYINSTRUMENT = FIXTURES / "pyinstrument"

PYSPY_ROOT = {
    "rolling": Path("/srv/rolling"),
    "membership": Path("/srv/membership"),
}
"""Project roots when py-spy recorded the example fixtures."""
PYINSTRUMENT_ROOT = {
    ex: Path(
        "/Users/faras/code/perfproof/.claude/worktrees/"
        f"agent-a0cbb9b58dc2a1d26/scratch/fixture-repos/{ex}"
    )
    for ex in ("rolling", "membership")
}
"""Project roots, and working directories, of the pyinstrument
fixtures, as their SOURCE.md records them."""
BOTTLENECK = {
    "rolling": ("rolling.py", "rolling_sum"),
    "membership": ("orders.py", "held_orders"),
}
"""The known bottleneck of each example, from examples/README.md."""
SPEC_NAME = {"rolling": "rolling-sum", "membership": "held-orders"}

PYTHON_DIR = str(Path(sys.executable).parent)


def make_spec(command: list[str], **update: Any) -> BenchmarkSpec:
    fields: dict[str, Any] = {
        "name": "work",
        "command": command,
        "metric": {
            "kind": "wall",
            "direction": "lower_is_better",
            "unit": "s",
        },
        "correctness": {
            "command": [sys.executable, "-c", "pass"],
            "timeout_seconds": 30.0,
        },
        "warmup": 0,
        "budget": {"max_rounds": 20, "max_seconds": 60.0},
        "looks": {"n0": 10, "k": 10, "max_looks": 2},
        "timeout_seconds": 60.0,
        "harness": ["work.py"],
    }
    fields.update(update)
    return BenchmarkSpec.model_validate(fields)


def process_env(**extra: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k in ("HOME", "PATH", "TMPDIR", "USER", "LANG")
    }
    env["PATH"] = f"{PYTHON_DIR}{os.pathsep}{env.get('PATH', os.defpath)}"
    env.update(extra)
    return env


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def function_lines(path: Path, name: str) -> range:
    """Lines of a function's definition in a source file."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            assert node.end_lineno is not None
            return range(node.lineno, node.end_lineno + 1)
    raise AssertionError(name)


def assert_bottleneck_in_top_3(
    hotspots: tuple[Hotspot, ...], example: str
) -> None:
    file, function = BOTTLENECK[example]
    lines = function_lines(EXAMPLES / example / file, function)
    top = hotspots[:3]
    found = [
        h
        for h in top
        if h.file == file and h.function == function and not h.is_library
    ]
    assert found, [(h.function, h.file, h.line) for h in top]
    assert all(h.line in lines for h in found), found


def convert(path: Path, cwd: Path, chunk_chars: int = 1 << 16) -> str:
    out = io.StringIO()
    with path.open(encoding="utf-8") as session:
        pyinstrument_to_folded(
            session, out, cwd=str(cwd), chunk_chars=chunk_chars
        )
    return out.getvalue()


def convert_text(text: str, chunk_chars: int = 1 << 16) -> str:
    out = io.StringIO()
    pyinstrument_to_folded(
        io.StringIO(text), out, cwd="/w", chunk_chars=chunk_chars
    )
    return out.getvalue()


def session_of(example: str) -> dict[str, Any]:
    path = PYINSTRUMENT / f"{example}.pyisession"
    return cast("dict[str, Any]", json.loads(path.read_text("utf-8")))


# Captured py-spy fixtures


@pytest.mark.parametrize("example", ["rolling", "membership"])
def test_py_spy_fixture_ranks_the_known_bottleneck_first(
    example: str,
) -> None:
    hotspots = summarise_file(
        PYSPY / f"{example}.txt", project_root=PYSPY_ROOT[example]
    )
    file, function = BOTTLENECK[example]
    assert (hotspots[0].file, hotspots[0].function) == (file, function)
    assert hotspots[0].frame_kind == "python"
    assert not hotspots[0].is_library
    assert_bottleneck_in_top_3(hotspots, example)


def test_py_spy_subprocess_root_frame_is_kept_apart() -> None:
    hotspots = summarise_file(
        PYSPY / "membership.txt",
        project_root=PYSPY_ROOT["membership"],
        top_n=100,
    )
    roots = [h for h in hotspots if h.function.startswith("process ")]
    assert len(roots) == 1
    assert roots[0].frame_kind == "native"
    assert roots[0].is_library
    assert roots[0].total_fraction == 1.0


# pyinstrument sessions


@pytest.mark.parametrize("example", ["rolling", "membership"])
def test_pyinstrument_fixture_ranks_the_known_bottleneck_first(
    example: str, tmp_path: Path
) -> None:
    root = PYINSTRUMENT_ROOT[example]
    folded = write(
        tmp_path / "folded.txt",
        convert(PYINSTRUMENT / f"{example}.pyisession", root),
    )
    hotspots = summarise_file(folded, project_root=root)
    file, function = BOTTLENECK[example]
    assert (hotspots[0].file, hotspots[0].function) == (file, function)
    assert_bottleneck_in_top_3(hotspots, example)


@pytest.mark.parametrize("example", ["rolling", "membership"])
def test_conversion_keeps_every_record_and_its_time(example: str) -> None:
    session = session_of(example)
    records: list[tuple[list[str], float]] = session["frame_records"]
    lines = convert(
        PYINSTRUMENT / f"{example}.pyisession", PYINSTRUMENT_ROOT[example]
    ).splitlines()
    assert len(lines) == len(records)
    for line, (stack, seconds) in zip(lines, records, strict=True):
        text, _, count = line.rpartition(" ")
        assert int(count) == round(seconds * 1_000_000)
        assert len(text.split(";")) == len(stack)
    total_us = sum(int(line.rpartition(" ")[2]) for line in lines)
    exact_us = sum(s for _, s in records) * 1_000_000
    assert abs(total_us - exact_us) <= 0.5 * len(records)


@pytest.mark.parametrize("chunk", [1, 7, 4096])
def test_conversion_does_not_depend_on_chunk_size(chunk: int) -> None:
    path = PYINSTRUMENT / "rolling.pyisession"
    cwd = PYINSTRUMENT_ROOT["rolling"]
    assert convert(path, cwd, chunk) == convert(path, cwd)


def test_conversion_frame_forms() -> None:
    root = PYINSTRUMENT_ROOT["rolling"]
    folded = convert(PYINSTRUMENT / "rolling.pyisession", root)
    stacks = [
        line.rpartition(" ")[0].split(";") for line in folded.split("\n")
    ]
    frames = {f for stack in stacks for f in stack}
    assert all(s[0] == "MainThread (<thread>)" for s in stacks if s != [""])
    assert f"rolling_sum ({root}/rolling.py:51)" in frames
    relative = [f for f in frames if f.startswith("<module> (bench.py")]
    assert relative == []
    assert any(f.startswith(f"<module> ({root}/bench.py:") for f in frames)
    assert any(f.startswith("SourceFileLoader.get_code (") for f in frames)
    assert "loads (<built-in>)" in frames
    assert any(f.startswith("_run_code (<frozen runpy>:") for f in frames)


def test_synthetic_frames_get_a_pseudo_file() -> None:
    session = session_of("rolling")
    stack, seconds = session["frame_records"][0]
    session["frame_records"] = [[[*stack, "[await]"], seconds]]
    folded = convert_text(json.dumps(session))
    assert folded.split(" ")[-2:] == [
        "(<pyinstrument>)",
        f"{round(seconds * 1e6)}\n",
    ]
    assert folded.rpartition(";")[2].startswith("[await] (<pyinstrument>)")


def test_separators_inside_a_frame_are_escaped() -> None:
    session = session_of("rolling")
    stack, seconds = session["frame_records"][0]
    session["frame_records"] = [
        [[*stack[:-1], "f;g\x00/w/a\nb.py\x0012"], seconds]
    ]
    line = convert_text(json.dumps(session))
    assert line.count("\n") == 1
    assert line.rpartition(";")[2].startswith("f\\x3bg (/w/a\\nb.py:12)")


def test_an_empty_record_list_converts_to_nothing() -> None:
    session = session_of("rolling")
    session["frame_records"] = []
    assert convert_text(json.dumps(session)) == ""


def fixture_text() -> str:
    return (PYINSTRUMENT / "rolling.pyisession").read_text("utf-8")


def with_record(record: object) -> str:
    session = session_of("rolling")
    session["frame_records"] = [record]
    return json.dumps(session)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (fixture_text()[: len(fixture_text()) // 2], "invalid JSON"),
        (fixture_text() + "{}", "data after the session object"),
        (
            json.dumps(
                {
                    k: v
                    for k, v in session_of("rolling").items()
                    if k != "frame_records"
                }
            ),
            "no frame_records",
        ),
        (
            '{"frame_records": [], ' + fixture_text().lstrip()[1:],
            "more than once",
        ),
        ("[]", "expected '{'"),
        ("", "the end of the input"),
        (with_record([["MainThread\x00<thread>\x001"], -1.0]), "record 0"),
        (with_record([["MainThread\x00<thread>\x001"], True]), "record 0"),
        (with_record([["MainThread\x00<thread>\x001"]]), "record 0"),
        (with_record([[1], 0.001]), "record 0"),
        (with_record([["f\x00a.py"], 0.001]), "function, file and line"),
        (with_record([["f\x00a.py\x00x"], 0.001]), "line number"),
        (with_record([["f\x00a.py\x001\x01lx"], 0.001]), "line number"),
        (
            fixture_text().replace('"duration": ', '"duration": NaN, "d": '),
            "not a JSON number",
        ),
    ],
    ids=[
        "truncated",
        "trailing",
        "no-records",
        "two-record-lists",
        "not-an-object",
        "empty",
        "negative-time",
        "boolean-time",
        "no-time",
        "frame-not-a-string",
        "two-part-frame",
        "bad-first-line",
        "bad-current-line",
        "nan",
    ],
)
def test_malformed_sessions_are_refused(text: str, message: str) -> None:
    with pytest.raises(SessionFormatError, match=message):
        convert_text(text, chunk_chars=97)


def test_chunk_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="chunk_chars"):
        convert_text("{}", chunk_chars=0)


# Choosing a profiler


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("linux", "py-spy"), ("darwin", "pyinstrument")],
)
def test_default_profiler(platform: str, expected: str) -> None:
    assert default_profiler(platform) == expected


def test_no_default_profiler_elsewhere() -> None:
    with pytest.raises(ProfileError) as caught:
        default_profiler("win32")
    assert caught.value.code == "profiler_unavailable"


# Running


COPY_PROFILER = """\
import shutil, subprocess, sys
fixture, output, *command = sys.argv[1:]
subprocess.run(command, check=True)
shutil.copyfile(fixture, output)
"""
"""A stand-in profiler that runs the workload, then emits a captured
py-spy fixture, to exercise the plumbing of a command profiler."""


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    write(root / "work.py", "import sys\nsys.exit(0)\n")
    write(root / "copy_profiler.py", COPY_PROFILER)
    return root


def run_profile(
    spec: BenchmarkSpec,
    tree: Path,
    *,
    profiler: ProfilerName = "command",
    template: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> tuple[Profile, Path]:
    out = tree.parent / "out"
    out.mkdir(exist_ok=True)
    profile = profile_benchmark(
        spec,
        tree,
        out,
        profiler=profiler,
        process_env=process_env() if env is None else env,
        template=template,
        timeout_seconds=timeout,
    )
    return profile, out


def copy_template(tree: Path) -> tuple[str, ...]:
    return (
        sys.executable,
        str(tree / "copy_profiler.py"),
        str(PYSPY / "rolling.txt"),
        "{output}",
        "{command}",
    )


def profile_error(**kwargs: Any) -> ProfileError:
    with pytest.raises(ProfileError) as caught:
        run_profile(**kwargs)
    return caught.value


def test_command_profiler_writing_a_file(tree: Path) -> None:
    spec = make_spec([sys.executable, "work.py"])
    profile, out = run_profile(spec, tree, template=copy_template(tree))
    assert profile.profiler == "command"
    assert (profile.basis, profile.weight_unit) == ("unknown", "unknown")
    assert profile.tool_version is None
    assert (
        profile.raw_path == profile.folded_path == out / "command.folded.txt"
    )
    assert (
        profile.raw_path.read_bytes() == (PYSPY / "rolling.txt").read_bytes()
    )
    assert profile.hotspots == summarise_file(
        PYSPY / "rolling.txt", project_root=tree
    )
    assert (
        profile.frames_by_total
        == rank_file(PYSPY / "rolling.txt", project_root=tree).by_total
    )
    assert profile.argv[-2:] == (sys.executable, "work.py")
    assert profile.argv[3] == str(out / "command.folded.txt")
    assert profile.unprofiled.wall_seconds > 0
    assert profile.side_effects.status == "passed"
    assert sorted(p.name for p in out.iterdir()) == [
        "command.folded.txt",
        "profiled.stderr",
        "profiled.stdout",
        "unprofiled.stderr",
        "unprofiled.stdout",
    ]
    assert set(profile.log_paths) == {
        out / n
        for n in (
            "profiled.stderr",
            "profiled.stdout",
            "unprofiled.stderr",
            "unprofiled.stdout",
        )
    }


PRINTS_A_FOLDED_LINE = "print('rows 700000')\n"
"""A benchmark whose output is itself a valid folded-stack line."""


def test_a_template_without_output_is_refused(tree: Path) -> None:
    write(tree / "work.py", PRINTS_A_FOLDED_LINE)
    spec = make_spec([sys.executable, "work.py"])
    template = (
        sys.executable,
        "-c",
        "import subprocess, sys; subprocess.run(sys.argv[1:], check=True)",
        "{command}",
    )
    error = profile_error(spec=spec, tree=tree, template=template)
    assert error.code == "invalid_command"
    assert "{output}" in error.message
    assert list((tree.parent / "out").iterdir()) == []


def test_benchmark_stdout_never_enters_the_profile(tree: Path) -> None:
    write(tree / "work.py", PRINTS_A_FOLDED_LINE)
    spec = make_spec([sys.executable, "work.py"])
    profile, out = run_profile(spec, tree, template=copy_template(tree))
    assert (out / "profiled.stdout").read_text() == "rows 700000\n"
    assert profile.hotspots == summarise_file(
        PYSPY / "rolling.txt", project_root=tree
    )
    assert all(h.function != "rows" for h in profile.hotspots)


def test_command_profiler_found_on_the_benchmark_path(tree: Path) -> None:
    spec = make_spec([sys.executable, "work.py"])
    template = ("python", *copy_template(tree)[1:])
    profile, _ = run_profile(spec, tree, template=template)
    assert Path(profile.argv[0]).parent == Path(PYTHON_DIR).absolute()


def test_command_profiler_needs_the_command(tree: Path) -> None:
    spec = make_spec([sys.executable, "work.py"])
    error = profile_error(
        spec=spec, tree=tree, template=(sys.executable, "-c", "pass")
    )
    assert error.code == "invalid_command"
    assert list((tree.parent / "out").iterdir()) == []


def test_a_missing_profiler_is_reported(tree: Path) -> None:
    spec = make_spec([sys.executable, "work.py"])
    error = profile_error(
        spec=spec,
        tree=tree,
        template=("no-such-profiler", "{output}", "{command}"),
    )
    assert error.code == "profiler_unavailable"
    assert "no-such-profiler" in error.message


def test_a_failing_profiled_run_is_reported(tree: Path) -> None:
    spec = make_spec([sys.executable, "work.py"])
    template = (
        sys.executable,
        "-c",
        "import sys; sys.exit(4)",
        "{output}",
        "{command}",
    )
    error = profile_error(spec=spec, tree=tree, template=template)
    assert error.code == "profiler_failed"
    assert "exited 4" in error.message


def test_a_profiler_that_writes_nothing_is_reported(tree: Path) -> None:
    spec = make_spec([sys.executable, "work.py"])
    template = (sys.executable, "-c", "pass", "{output}", "{command}")
    error = profile_error(spec=spec, tree=tree, template=template)
    assert error.code == "profiler_failed"
    assert "wrote no output" in error.message


def test_output_that_is_not_folded_stacks_is_reported(tree: Path) -> None:
    spec = make_spec([sys.executable, "work.py"])
    template = (
        sys.executable,
        "-c",
        "import sys; open(sys.argv[1], 'w').write('not folded')",
        "{output}",
        "{command}",
    )
    error = profile_error(spec=spec, tree=tree, template=template)
    assert error.code == "invalid_profile"


def test_a_failing_workload_stops_before_profiling(tree: Path) -> None:
    write(
        tree / "work.py",
        "import sys\nprint('bad', file=sys.stderr)\nsys.exit(3)\n",
    )
    spec = make_spec([sys.executable, "work.py"])
    error = profile_error(spec=spec, tree=tree, template=copy_template(tree))
    assert error.code == "workload_failed"
    assert "exited 3" in error.message
    assert error.message.endswith("bad")
    names = sorted(p.name for p in (tree.parent / "out").iterdir())
    assert names == ["unprofiled.stderr", "unprofiled.stdout"]


def test_a_missing_executable_is_reported(tree: Path) -> None:
    spec = make_spec(["no-such-program", "work.py"])
    error = profile_error(spec=spec, tree=tree, template=copy_template(tree))
    assert error.code == "workload_failed"
    assert "no-such-program" in error.message


def test_a_timeout_kills_the_whole_group(tree: Path) -> None:
    write(
        tree / "work.py",
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)'])\n"
        "open('child.pid', 'w').write(str(child.pid))\n"
        "time.sleep(60)\n",
    )
    spec = make_spec([sys.executable, "work.py"])
    start = time.monotonic()
    error = profile_error(
        spec=spec, tree=tree, template=copy_template(tree), timeout=1.0
    )
    assert time.monotonic() - start < 30
    assert error.code == "workload_failed"
    assert "timed out after 1.0 s" in error.message
    pid = int((tree / "child.pid").read_text())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"grandchild {pid} outlived the timeout")


def test_wall_time_includes_waiting_and_cpu_time_does_not(
    tree: Path,
) -> None:
    write(tree / "work.py", "import time\ntime.sleep(0.5)\n")
    spec = make_spec([sys.executable, "work.py"])
    profile, _ = run_profile(spec, tree, template=copy_template(tree))
    plain = profile.unprofiled
    assert plain.wall_seconds >= 0.5
    assert plain.cpu_seconds < plain.wall_seconds / 2
    assert profile.profiled_wall_seconds >= 0.5


def test_cpu_time_counts_waited_for_children(tree: Path) -> None:
    write(
        tree / "work.py",
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-c', "
        "'import time\\nend = time.process_time() + 0.3\\n"
        "while time.process_time() < end: pass'], check=True)\n",
    )
    spec = make_spec([sys.executable, "work.py"])
    profile, _ = run_profile(spec, tree, template=copy_template(tree))
    assert profile.unprofiled.cpu_seconds >= 0.3


def test_runs_get_the_benchmark_environment(tree: Path) -> None:
    write(
        tree / "work.py",
        "import json, os, sys\n"
        "with open(sys.argv[1], 'w') as f:\n"
        "    json.dump(dict(os.environ), f)\n",
    )
    spec = make_spec(
        [sys.executable, "work.py", "env.json"], env={"DECLARED": "yes"}
    )
    env = process_env(AGENT_ONLY="1", OPENAI_API_BASE="x")
    run_profile(spec, tree, template=copy_template(tree), env=env)
    seen = json.loads((tree / "env.json").read_text())
    assert seen["DECLARED"] == "yes"
    assert "AGENT_ONLY" not in seen
    assert "OPENAI_API_BASE" not in seen
    assert seen["PATH"] == env["PATH"]


def test_a_remote_service_is_refused_before_anything_runs(
    tree: Path,
) -> None:
    spec = make_spec(
        [sys.executable, "work.py"],
        env={"DATABASE_URL": "postgresql://db.example.com:5432/orders"},
    )
    error = profile_error(spec=spec, tree=tree, template=copy_template(tree))
    assert error.code == "side_effects"
    assert error.gate is not None
    assert error.gate.status == "failed"
    assert list((tree.parent / "out").iterdir()) == []


def test_a_waived_remote_service_runs_and_says_so(tree: Path) -> None:
    spec = make_spec(
        [sys.executable, "work.py"],
        env={"DATABASE_URL": "postgresql://db.example.com:5432/orders"},
        allow_remote_services=True,
    )
    profile, _ = run_profile(spec, tree, template=copy_template(tree))
    assert profile.side_effects.status == "waived"


def test_the_reset_runs_before_each_run(tree: Path) -> None:
    reset = [sys.executable, "-c", "open('resets', 'a').write('r')"]
    spec = make_spec(
        [sys.executable, "work.py"],
        reset={"command": reset, "timeout_seconds": 30.0},
    )
    run_profile(spec, tree, template=copy_template(tree))
    assert (tree / "resets").read_text() == "rr"


def test_a_failing_reset_stops_the_profile(tree: Path) -> None:
    spec = make_spec(
        [sys.executable, "work.py"],
        reset={
            "command": [sys.executable, "-c", "raise SystemExit(2)"],
            "timeout_seconds": 30.0,
        },
    )
    error = profile_error(spec=spec, tree=tree, template=copy_template(tree))
    assert error.code == "reset_failed"
    assert error.gate is not None
    assert list((tree.parent / "out").iterdir()) == []


def test_existing_files_are_never_overwritten(tree: Path) -> None:
    out = tree.parent / "out"
    write(out / "unprofiled.stdout", "keep")
    spec = make_spec([sys.executable, "work.py"])
    error = profile_error(spec=spec, tree=tree, template=copy_template(tree))
    assert error.code == "workload_failed"
    assert "already exists" in error.message
    assert (out / "unprofiled.stdout").read_text() == "keep"


def test_py_spy_unavailable_is_reported(tree: Path) -> None:
    spec = make_spec([sys.executable, "work.py"])
    empty = tree.parent / "empty"
    empty.mkdir()
    error = profile_error(
        spec=spec,
        tree=tree,
        profiler="py-spy",
        env=process_env(PATH=str(empty)),
    )
    assert error.code == "profiler_unavailable"
    if sys.platform == "darwin" and os.geteuid() != 0:
        assert "needs root on macOS" in error.message
    else:
        assert "py-spy is not on PATH" in error.message
    assert list((tree.parent / "out").iterdir()) == []


@pytest.mark.parametrize(
    ("command", "message"),
    [
        (["/bin/sh", "work.py"], "is not one"),
        ([sys.executable, "-c", "pass"], "gives the interpreter '-c'"),
        ([sys.executable], "gives the interpreter 'nothing'"),
        ([sys.executable, "-m"], "gives the interpreter '-m'"),
    ],
    ids=["not-python", "dash-c", "no-script", "no-module"],
)
def test_pyinstrument_wraps_only_a_script_or_module(
    tree: Path, command: list[str], message: str
) -> None:
    error = profile_error(
        spec=make_spec(command), tree=tree, profiler="pyinstrument"
    )
    assert error.code == "profiler_unavailable"
    assert message in error.message


def test_pyinstrument_needs_uv(tree: Path) -> None:
    empty = tree.parent / "empty"
    empty.mkdir()
    error = profile_error(
        spec=make_spec([sys.executable, "work.py"]),
        tree=tree,
        profiler="pyinstrument",
        env=process_env(PATH=str(empty)),
    )
    assert error.code == "profiler_unavailable"
    assert "uv is not on PATH" in error.message


def test_arguments_are_checked(tree: Path) -> None:
    spec = make_spec([sys.executable, "work.py"])
    out = tree.parent / "out"
    out.mkdir()
    for kwargs in ({"top_n": 0}, {"timeout_seconds": 0.0}):
        with pytest.raises(ValueError, match="must be"):
            profile_benchmark(
                spec,
                tree,
                out,
                profiler="command",
                process_env=process_env(),
                template=copy_template(tree),
                **kwargs,  # pyright: ignore[reportArgumentType]
            )


# The command line


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        timeout=30,
    )


def example_repo(tmp_path: Path, example: str) -> Path:
    repo = tmp_path / example
    shutil.copytree(
        EXAMPLES / example, repo, ignore=shutil.ignore_patterns("candidates")
    )
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(
        repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"
    )
    return repo


def cli_json(
    capsys: pytest.CaptureFixture[str], *argv: str
) -> tuple[int, dict[str, Any]]:
    status = main([*argv, "--json"])
    out, _ = capsys.readouterr()
    return status, cast("dict[str, Any]", json.loads(out))


@pytest.fixture
def repo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    repo = example_repo(tmp_path, "rolling")
    status, _ = cli_json(
        capsys,
        "bench",
        "define",
        "rolling-sum",
        "--spec",
        str(repo / "spec.json"),
        "--repo",
        str(repo),
    )
    assert status == 0
    return repo


@pytest.fixture
def benchmark_path(monkeypatch: pytest.MonkeyPatch) -> None:
    path = os.environ.get("PATH", os.defpath)
    monkeypatch.setenv("PATH", f"{PYTHON_DIR}{os.pathsep}{path}")


@pytest.mark.usefixtures("benchmark_path")
def test_cli_profiles_with_a_command_profiler(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo / "copy_profiler.py", COPY_PROFILER)
    template = json.dumps(copy_template(repo))
    status, document = cli_json(
        capsys,
        "profile",
        "rolling-sum",
        "--profiler-command",
        template,
        "--repo",
        str(repo),
        "--top",
        "3",
    )
    assert status == 0, document
    result = document["result"]
    assert set(result) == {
        "name",
        "spec_hash",
        "profiler",
        "tool_version",
        "basis",
        "weight_unit",
        "hotspots",
        "frames_by_total",
        "project_frames_above_bar",
        "next_step",
        "unprofiled",
        "profiled_wall_seconds",
        "load_average",
        "cpu_count",
        "side_effects",
        "summary",
        "evidence",
        "hypothesis",
        "argv",
        "min_effect",
        "own_modules",
        "run_id",
        "finished_at",
        "directory",
        "raw",
        "folded",
        "logs",
    }
    assert result["profiler"] == "command"
    assert len(result["hotspots"]) == 3
    assert result["frames_by_total"] == [
        h.model_dump(mode="json")
        for h in rank_file(
            PYSPY / "rolling.txt", project_root=repo, top_n=3
        ).by_total
    ]
    assert 0 < len(result["frames_by_total"]) <= 3
    summary = result["summary"]
    assert summary["labels"]["cap"].startswith("bound")
    assert len(json.dumps(summary, separators=(",", ":")).encode()) <= 1800
    top = summary["frames"][0]
    assert top["cap"] == pytest.approx(1 / (1 - top["total_fraction"]))
    assert summary["time"]["wall_seconds"] == pytest.approx(
        result["unprofiled"]["wall_seconds"]
    )
    evidence = Path(result["evidence"]["path"])
    assert evidence.parent == repo / ".perfproof" / "evidence" / "rolling-sum"
    stored = json.loads(evidence.read_text())
    assert (stored["kind"], stored["claim"]) == ("profile", "estimate")
    assert stored["result"]["summary"] == summary
    assert result["hypothesis"] is None
    directory = Path(result["directory"])
    assert directory.parent == repo / ".perfproof" / "profiles" / "rolling-sum"
    assert Path(result["raw"]).parent == directory
    saved = directory / "result.json"
    assert json.loads(saved.read_text()) == result
    assert result["run_id"] == directory.name
    assert result["finished_at"].endswith("Z")
    assert sorted(p.name for p in directory.glob(".*")) == []
    plain = result["unprofiled"]
    assert plain["cpu_fraction"] == pytest.approx(
        plain["cpu_seconds"] / plain["wall_seconds"]
    )
    status = main(
        [
            "profile",
            "rolling-sum",
            "--profiler-command",
            template,
            "--repo",
            str(repo),
        ]
    )
    text, _ = capsys.readouterr()
    assert status == 0
    assert text.startswith("Profile of rolling-sum (spec ")
    assert text.index("Top frames by self time") < text.index(
        "Top frames by total time"
    )
    assert "Unprofiled run: wall " in text
    assert ".perfproof/profiles/rolling-sum/" in text
    runs = sorted((repo / ".perfproof" / "profiles" / "rolling-sum").iterdir())
    assert len(runs) == 2
    again = json.loads((runs[1] / "result.json").read_text())
    assert again["run_id"] == runs[1].name
    assert again["hotspots"]


def rooted_template(tree: Path) -> tuple[str, ...]:
    """A copy profiler whose capture is the rolling fixture with its
    paths moved from where py-spy saw the project to ``tree``."""
    moved = tree.parent / "rolling-moved.txt"
    text = (PYSPY / "rolling.txt").read_text(encoding="utf-8")
    moved.write_text(
        text.replace(str(PYSPY_ROOT["rolling"]), str(tree)), encoding="utf-8"
    )
    return (
        sys.executable,
        str(tree / "copy_profiler.py"),
        str(moved),
        "{output}",
        "{command}",
    )


def test_project_frames_above_the_bar_are_not_cut_to_top(
    tree: Path,
) -> None:
    spec = make_spec([sys.executable, "work.py"], min_effect=0.1)
    out = tree.parent / "out"
    out.mkdir()
    profile = profile_benchmark(
        spec,
        tree,
        out,
        profiler="command",
        process_env=process_env(),
        template=rooted_template(tree),
        top_n=1,
    )
    assert len(profile.frames_by_total) == 1
    assert len(profile.hotspots) == 1
    names = [h.function for h in profile.project_frames_above_bar]
    assert names == ["main", "rolling_sum"]
    assert all(not h.is_library for h in profile.project_frames_above_bar)
    assert profile.hotspots == summarise_file(
        out / "command.folded.txt", project_root=tree, top_n=1
    )


@pytest.mark.usefixtures("benchmark_path")
def test_cli_show_finds_a_function_the_profile_did_not_print(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo / "copy_profiler.py", COPY_PROFILER)
    template = json.dumps(copy_template(repo))
    where = ["--repo", str(repo)]
    status, ran = cli_json(
        capsys,
        "profile",
        "rolling-sum",
        "--profiler-command",
        template,
        "--top",
        "1",
        *where,
    )
    assert status == 0, ran
    printed = {h["function"] for h in ran["result"]["frames_by_total"]}
    assert "_find_and_load" not in printed
    status, document = cli_json(
        capsys,
        "profile",
        "show",
        "rolling-sum",
        "--function",
        "_find_and_load",
        *where,
    )
    assert status == 0, document
    result = document["result"]
    assert result["run_id"] == ran["result"]["run_id"]
    assert result["found"] is True
    assert result["reason"] is None
    assert result["samples"] == 88
    (entry,) = result["functions"]
    assert entry["function"] == "_find_and_load"
    assert entry["seen"] is True
    assert entry["total_fraction"] == pytest.approx(24 / 88)
    assert entry["total_samples"] == 24
    assert entry["cap"] == pytest.approx(1 / (1 - 24 / 88))
    assert entry["cap_above_bar"] is True
    assert result["min_effect"] == ran["result"]["min_effect"]
    status = main(
        ["profile", "show", "rolling-sum", "--function", "_find", *where]
    )
    text, _ = capsys.readouterr()
    assert status == 0
    assert "not seen in this profile" in text


@pytest.mark.usefixtures("benchmark_path")
def test_cli_show_reports_an_absent_function_as_share_zero(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo / "copy_profiler.py", COPY_PROFILER)
    template = json.dumps(copy_template(repo))
    where = ["--repo", str(repo)]
    status, _ = cli_json(
        capsys,
        "profile",
        "rolling-sum",
        "--profiler-command",
        template,
        *where,
    )
    assert status == 0
    status, document = cli_json(
        capsys,
        "profile",
        "show",
        "rolling-sum",
        "--function",
        "createChildLogger",
        *where,
    )
    assert status == 0, document
    result = document["result"]
    assert result["found"] is False
    assert result["reason"].startswith("not seen in this profile")
    (entry,) = result["functions"]
    assert entry["seen"] is False
    assert (entry["self_fraction"], entry["total_fraction"]) == (0.0, 0.0)
    assert (entry["self_samples"], entry["total_samples"]) == (0, 0)
    assert entry["reason"] == result["reason"]


@pytest.mark.usefixtures("benchmark_path")
def test_cli_show_reads_the_latest_run_or_the_one_named(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo / "copy_profiler.py", COPY_PROFILER)
    template = json.dumps(copy_template(repo))
    where = ["--repo", str(repo)]
    for _ in range(2):
        status, _ = cli_json(
            capsys,
            "profile",
            "rolling-sum",
            "--profiler-command",
            template,
            *where,
        )
        assert status == 0
    first, second = sorted(
        p.name
        for p in (repo / ".perfproof" / "profiles" / "rolling-sum").iterdir()
    )
    ask = ["profile", "show", "rolling-sum"]
    function = ["--function", "rolling_sum"]
    _, latest = cli_json(capsys, *ask, *function, *where)
    assert latest["result"]["run_id"] == second
    _, named = cli_json(capsys, *ask, first, *function, *where)
    assert named["result"]["run_id"] == first
    _, prefix = cli_json(capsys, *ask, first[:20], *function, *where)
    assert prefix["result"]["run_id"] == first
    _, shared = cli_json(capsys, *ask, first[:8], *function, *where)
    assert shared["error"]["code"] == "ambiguous_run"


@pytest.mark.usefixtures("benchmark_path")
def test_a_benchmark_named_show_is_still_profiled(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = (repo / "spec.json").read_text(encoding="utf-8")
    write(
        repo / "spec-show.json",
        spec.replace('"name": "rolling-sum"', '"name": "show"'),
    )
    where = ["--repo", str(repo)]
    status, defined = cli_json(
        capsys,
        "bench",
        "define",
        "show",
        "--spec",
        str(repo / "spec-show.json"),
        *where,
    )
    assert status == 0, defined
    write(repo / "copy_profiler.py", COPY_PROFILER)
    template = json.dumps(copy_template(repo))
    status, document = cli_json(
        capsys, "profile", "show", "--profiler-command", template, *where
    )
    assert status == 0, document
    assert document["result"]["name"] == "show"
    assert document["result"]["hotspots"]


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (["profile", "show", "rolling-sum"], "invalid_argument"),
        (["profile", "show", "--function", "f"], "invalid_argument"),
        (
            ["profile", "show", "a", "b", "c", "--function", "f"],
            "invalid_argument",
        ),
        (["profile", "show", "../x", "--function", "f"], "invalid_name"),
        (
            ["profile", "show", "rolling-sum", "--function", "  "],
            "invalid_pattern",
        ),
        (["profile", "show", "rolling-sum", "--function", "f"], "not_found"),
        (
            ["profile", "show", "rolling-sum", "a/b", "--function", "f"],
            "invalid_argument",
        ),
        (["profile", "rolling-sum", "--function", "f"], "invalid_argument"),
    ],
)
def test_cli_show_errors(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    code: str,
) -> None:
    status, document = cli_json(capsys, *argv, "--repo", str(repo))
    assert status == 1
    assert document["error"]["code"] == code


@pytest.mark.usefixtures("benchmark_path")
def test_cli_reports_a_failed_result_write(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write(repo / "copy_profiler.py", COPY_PROFILER)
    template = json.dumps(copy_template(repo))
    original = Path.replace

    def refuse(self: Path, target: Path | str) -> Path:
        if self.name == ".result.json.tmp":
            raise PermissionError(13, "Permission denied", str(self))
        return original(self, target)

    monkeypatch.setattr(Path, "replace", refuse)
    status, document = cli_json(
        capsys,
        "profile",
        "rolling-sum",
        "--profiler-command",
        template,
        "--repo",
        str(repo),
    )
    assert status == 1
    assert document["error"]["code"] == "state_write_failed"
    (directory,) = (repo / ".perfproof" / "profiles" / "rolling-sum").iterdir()
    assert not (directory / "result.json").exists()
    assert not (directory / ".result.json.tmp").exists()


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (["profile", "nothing-here"], "not_found"),
        (["profile", "../x"], "invalid_name"),
        (["profile", "rolling-sum", "--spec-hash", "zz"], "not_found"),
        (["profile", "rolling-sum", "--spec-hash", "ffff"], "not_found"),
        (
            ["profile", "rolling-sum", "--profiler-command", "[1]"],
            "invalid_command",
        ),
        (
            ["profile", "rolling-sum", "--profiler-command", '["x"]'],
            "invalid_command",
        ),
        (
            ["profile", "rolling-sum", "--profiler-command", '["{command}"]'],
            "invalid_command",
        ),
        (
            ["profile", "rolling-sum", "--profiler-command", "{"],
            "invalid_command",
        ),
    ],
)
def test_cli_errors(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    code: str,
) -> None:
    status, document = cli_json(capsys, *argv, "--repo", str(repo))
    assert status == 1
    assert document["error"]["code"] == code
    assert not (repo / ".perfproof" / "profiles").exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["--top", "0"],
        ["--timeout", "-1"],
        ["--timeout", "nan"],
        ["--profiler", "perf"],
        ["--profiler", "py-spy", "--profiler-command", '["{command}"]'],
    ],
)
def test_cli_usage_errors(
    repo: Path, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    status, document = cli_json(
        capsys, "profile", "rolling-sum", *argv, "--repo", str(repo)
    )
    assert status == 2
    assert document["error"]["code"] == "usage"


def test_cli_ambiguous_spec(
    repo: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    spec = json.loads((repo / "spec.json").read_text())
    spec["warmup"] = 2
    other = write(tmp_path / "other.json", json.dumps(spec))
    status, _ = cli_json(
        capsys,
        "bench",
        "define",
        "rolling-sum",
        "--spec",
        str(other),
        "--repo",
        str(repo),
    )
    assert status == 0
    status, document = cli_json(
        capsys, "profile", "rolling-sum", "--repo", str(repo)
    )
    assert status == 1
    assert document["error"]["code"] == "ambiguous_hash"
    assert len(document["error"]["details"]["matches"]) == 2


def test_cli_outside_a_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, document = cli_json(
        capsys, "profile", "x", "--repo", str(tmp_path)
    )
    assert status == 1
    assert document["error"]["code"] == "not_a_repository"


# Integration: the real profilers on the examples


def example_tree(tmp_path: Path, example: str) -> tuple[BenchmarkSpec, Path]:
    tree = tmp_path / example
    shutil.copytree(
        EXAMPLES / example, tree, ignore=shutil.ignore_patterns("candidates")
    )
    spec = BenchmarkSpec.model_validate_json(
        (tree / "spec.json").read_text("utf-8")
    )
    return spec, tree


def py_spy_missing() -> str | None:
    if not sys.platform.startswith("linux"):
        return "py-spy runs without root only on Linux"
    if shutil.which("py-spy") is None:
        return "py-spy is not on PATH"
    return None


def uv_missing() -> str | None:
    return None if shutil.which("uv") else "uv is not on PATH"


@pytest.mark.integration
@pytest.mark.parametrize("example", ["rolling", "membership"])
def test_py_spy_finds_the_known_bottleneck(
    example: str, tmp_path: Path
) -> None:
    reason = py_spy_missing()
    if reason is not None:
        pytest.skip(reason)
    spec, tree = example_tree(tmp_path, example)
    profile, _ = run_profile(spec, tree, profiler="py-spy")
    assert profile.basis == "active"
    assert profile.tool_version is not None
    assert_bottleneck_in_top_3(profile.hotspots, example)


@pytest.mark.integration
@pytest.mark.parametrize("example", ["rolling", "membership"])
def test_pyinstrument_finds_the_known_bottleneck(
    example: str, tmp_path: Path
) -> None:
    reason = uv_missing()
    if reason is not None:
        pytest.skip(reason)
    spec, tree = example_tree(tmp_path, example)
    profile, out = run_profile(spec, tree, profiler="pyinstrument")
    assert profile.tool_version == PYINSTRUMENT_VERSION
    assert (profile.basis, profile.weight_unit) == ("wall", "microseconds")
    assert profile.raw_path == out / "pyinstrument.pyisession"
    assert profile.folded_path == out / "pyinstrument.folded.txt"
    assert_bottleneck_in_top_3(profile.hotspots, example)


@pytest.mark.integration
def test_pyinstrument_profiles_a_module(tmp_path: Path) -> None:
    reason = uv_missing()
    if reason is not None:
        pytest.skip(reason)
    tree = tmp_path / "tree"
    write(
        tree / "spin.py",
        "import sys\n\n\ndef spin(n):\n    total = 0\n"
        "    for i in range(n):\n        total += i % 7\n"
        "    return total\n\n\n"
        "if __name__ == '__main__':\n    spin(int(sys.argv[1]))\n",
    )
    spec = make_spec([sys.executable, "-m", "spin", "3000000"])
    profile, _ = run_profile(spec, tree, profiler="pyinstrument")
    top = profile.hotspots[0]
    assert (top.function, top.file, top.is_library) == (
        "spin",
        "spin.py",
        False,
    )


@pytest.mark.integration
@pytest.mark.usefixtures("benchmark_path")
def test_cli_profiles_with_the_default_profiler(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reason = (
        py_spy_missing() if default_profiler() == "py-spy" else uv_missing()
    )
    if reason is not None:
        pytest.skip(reason)
    status, document = cli_json(
        capsys, "profile", "rolling-sum", "--repo", str(repo)
    )
    assert status == 0, document
    hotspots = tuple(
        Hotspot.model_validate(h) for h in document["result"]["hotspots"]
    )
    assert document["result"]["profiler"] == default_profiler()
    assert_bottleneck_in_top_3(hotspots, "rolling")


@pytest.mark.parametrize(
    ("cpu", "says", "never"),
    [
        (
            1.71,
            "CPU time is 1.71x wall time: other threads used CPU alongside "
            "the main one",
            "parallel",
        ),
        (0.25, "For at least 75% of wall time no thread", "other threads"),
    ],
    ids=["cpu-above-wall", "cpu-below-wall"],
)
def test_text_reads_cpu_time_as_summed_over_threads(
    tree: Path, cpu: float, says: str, never: str
) -> None:
    spec = make_spec([sys.executable, "work.py"])
    profile, out = run_profile(spec, tree, template=copy_template(tree))
    shown = dataclasses.replace(profile, unprofiled=Timing(1.0, cpu))
    text = profile_command._text(spec, shown, out)  # pyright: ignore[reportPrivateUsage]
    assert "(summed over all threads and processes)" in text
    assert says in " ".join(text.split())
    assert never not in text


HINT = "No function of this project is at or above the bar in this workload."


@pytest.mark.parametrize(
    ("min_effect", "hinted"),
    [(0.1, False), (5.0, True)],
    ids=["above", "none"],
)
def test_next_step_is_set_only_without_a_project_frame_above_the_bar(
    tree: Path, min_effect: float, hinted: bool
) -> None:
    spec = make_spec([sys.executable, "work.py"], min_effect=min_effect)
    profile, out = run_profile(spec, tree, template=rooted_template(tree))
    assert bool(profile.project_frames_above_bar) is not hinted
    result = profile_command._result(spec, profile, out)  # pyright: ignore[reportPrivateUsage]
    text = profile_command._text(spec, profile, out)  # pyright: ignore[reportPrivateUsage]
    step = cast("dict[str, str] | None", result["next_step"])
    if hinted:
        assert step is not None
        assert step["code"] == "no_project_frame_above_bar"
        assert step["message"].startswith(HINT)
        assert text.splitlines().count(step["message"]) == 1
    else:
        assert step is None
        assert HINT not in text


MODULE_HINT = "Rerun with --own-module <name>"
PATH_HINT = "is under this project's root ("
RUST = FIXTURES / "rust" / "fd-threads.folded.txt"
PYTHON = PYSPY / "rolling.txt"


@pytest.mark.parametrize(
    ("fixture", "own_modules", "min_effect", "code", "says"),
    [
        (RUST, (), 0.1, "project_frames_unknown", MODULE_HINT),
        (RUST, ("fd",), 5.0, "no_project_frame_above_bar", HINT),
        (RUST, ("fd",), 0.001, None, ""),
        (PYTHON, (), 0.1, "project_frames_unknown", PATH_HINT),
    ],
    ids=[
        "rust-unclassified",
        "rust-classified-below-bar",
        "rust-classified-above-bar",
        "python-outside-root",
    ],
)
def test_next_step_tells_an_unknown_project_from_one_below_the_bar(
    tree: Path,
    fixture: Path,
    own_modules: tuple[str, ...],
    min_effect: float,
    code: str | None,
    says: str,
) -> None:
    spec = make_spec([sys.executable, "work.py"], min_effect=min_effect)
    out = tree.parent / "out"
    out.mkdir()
    template = (
        sys.executable,
        str(tree / "copy_profiler.py"),
        str(fixture),
        "{output}",
        "{command}",
    )
    profile = profile_benchmark(
        spec,
        tree,
        out,
        profiler="command",
        process_env=process_env(),
        template=template,
        own_modules=own_modules,
    )
    result = profile_command._result(spec, profile, out)  # pyright: ignore[reportPrivateUsage]
    text = profile_command._text(spec, profile, out)  # pyright: ignore[reportPrivateUsage]
    step = cast("dict[str, str] | None", result["next_step"])
    assert (step or {}).get("code") == code
    if step is None:
        assert HINT not in text
        assert MODULE_HINT not in text
        assert PATH_HINT not in text
        return
    assert says in step["message"]
    assert step["message"] in text.splitlines()
    if code == "project_frames_unknown" and fixture == PYTHON:
        assert str(tree) in step["message"]
        assert "--own-module" not in step["message"]


@pytest.mark.usefixtures("benchmark_path")
def test_cli_carries_the_next_step_in_json_and_text(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo / "copy_profiler.py", COPY_PROFILER)
    template = json.dumps(copy_template(repo))
    status, document = cli_json(
        capsys,
        "profile",
        "rolling-sum",
        "--profiler-command",
        template,
        "--repo",
        str(repo),
    )
    assert status == 0, document
    result = document["result"]
    assert result["project_frames_above_bar"] == []
    assert result["next_step"]["code"] == "project_frames_unknown"
    saved = Path(result["directory"]) / "result.json"
    assert json.loads(saved.read_text())["next_step"] == result["next_step"]
    status = main(
        [
            "profile",
            "rolling-sum",
            "--profiler-command",
            template,
            "--repo",
            str(repo),
        ]
    )
    text, _ = capsys.readouterr()
    assert status == 0
    assert result["next_step"]["message"] in text.splitlines()


@pytest.mark.usefixtures("benchmark_path")
def test_a_result_saved_before_next_step_still_loads(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo / "copy_profiler.py", COPY_PROFILER)
    template = json.dumps(copy_template(repo))
    status, ran = cli_json(
        capsys,
        "profile",
        "rolling-sum",
        "--profiler-command",
        template,
        "--repo",
        str(repo),
    )
    assert status == 0, ran
    saved = Path(ran["result"]["directory"]) / "result.json"
    old = json.loads(saved.read_text())
    del old["next_step"]
    saved.write_text(json.dumps(old), encoding="utf-8")
    status, document = cli_json(
        capsys,
        "profile",
        "show",
        "rolling-sum",
        "--function",
        "_find_and_load",
        "--repo",
        str(repo),
    )
    assert status == 0, document
    assert document["result"]["found"] is True
