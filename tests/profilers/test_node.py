import json
import os
import shutil
import subprocess
import sys
import tracemalloc
from pathlib import Path
from typing import Any

import pytest

from perfproof.profilers import node as node_module
from perfproof.profilers.folded import summarise_file
from perfproof.profilers.node import (
    PRELOAD,
    NodeProfileError,
    SourceMap,
    convert_profiles,
    decode_mappings,
    decode_vlq,
    main,
)
from perfproof.profilers.run import profile_benchmark
from perfproof.schema import BenchmarkSpec, Hotspot

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "node"
TSX_PROFILE = FIXTURES / "hono-tsx.nodeprof.json"
TSX_RAW = FIXTURES / "hono-tsx.cpuprofile"
BUNDLE_RAW = FIXTURES / "hono-bundle.cpuprofile"
HONO_ROOT = Path(
    "/Users/faras/code/perfproof/.claude/worktrees/"
    "agent-aacddb73ec949121b/scratch/node/hono"
)
"""Project root and working directory of the captured runs."""

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


def convert(path: Path, tmp_path: Path, **kwargs: Any) -> Path:
    out = tmp_path / "out.folded"
    convert_profiles([path], out, **kwargs)
    return out


def hotspots(path: Path, top_n: int = 200) -> tuple[Hotspot, ...]:
    return summarise_file(path, project_root=HONO_ROOT, top_n=top_n)


def frames(folded: Path) -> set[str]:
    found: set[str] = set()
    for line in folded.read_text(encoding="utf-8").splitlines():
        stack, _, _ = line.rpartition(" ")
        found.update(stack.split(";"))
    return found


def test_tsx_profile_maps_frames_to_typescript_lines(tmp_path: Path) -> None:
    ranked = hotspots(convert(TSX_PROFILE, tmp_path))
    own = {(h.function, h.file, h.line) for h in ranked if not h.is_library}
    # tsx compiles every file onto its first line; these are the
    # positions the inline source maps give in the TypeScript.
    assert ("Context", "src/context.ts", 300) in own
    assert ("(anonymous)", "src/hono-base.ts", 499) in own
    wrong = [
        h
        for h in ranked
        if h.file is not None
        and h.file.startswith("src/")
        and h.line == 1
        and h.function != "__name"
    ]
    assert wrong == []


def test_frames_in_node_modules_and_node_internals_are_library(
    tmp_path: Path,
) -> None:
    ranked = hotspots(convert(TSX_PROFILE, tmp_path))
    by_file = {h.file: h for h in ranked if h.file}
    assert by_file["node:internal/bootstrap/realm"].is_library
    loader = [h for h in ranked if h.file and "node_modules/tsx" in h.file]
    assert loader
    assert all(h.is_library for h in loader)
    assert not by_file["src/hono-base.ts"].is_library


def test_a_raw_cpuprofile_of_typescript_has_no_line(tmp_path: Path) -> None:
    if not (HONO_ROOT / "src" / "hono-base.ts").exists():
        pytest.skip("the captured project is gone, so the file check is idle")
    # Without the source maps V8 positions are in tsx's output, so a
    # line would be wrong; the frame is shown without one.
    out = convert(TSX_RAW, tmp_path)
    text = out.read_text(encoding="utf-8")
    assert "hono-base.ts)" in text
    assert "hono-base.ts:" not in text
    ranked = hotspots(out)
    assert [h for h in ranked if h.file == "src/hono-base.ts"] == []


def test_a_raw_cpuprofile_of_plain_javascript_keeps_its_lines(
    tmp_path: Path,
) -> None:
    out = convert(BUNDLE_RAW, tmp_path)
    ranked = hotspots(out)
    mine = [h for h in ranked if h.file == "dist/wl.mjs"]
    assert mine
    assert all(h.line is not None and h.line > 1 for h in mine)


def test_weights_are_microseconds_for_the_preload_and_samples_for_raw(
    tmp_path: Path,
) -> None:
    profile = json.loads(TSX_PROFILE.read_text(encoding="utf-8"))
    own = sum(
        n["selfMicros"]
        for n in profile["nodes"]
        if n["callFrame"]["functionName"] not in ("(root)", "(idle)")
    )
    out = tmp_path / "out.folded"
    written = convert_profiles([TSX_PROFILE], out)
    assert written["weight"] == own
    raw = json.loads(BUNDLE_RAW.read_text(encoding="utf-8"))
    hits = sum(
        n["hitCount"]
        for n in raw["nodes"]
        if n["callFrame"]["functionName"] not in ("(root)", "(idle)")
    )
    assert convert_profiles([BUNDLE_RAW], out)["weight"] == hits


def test_the_root_node_is_dropped(tmp_path: Path) -> None:
    quiet = frames(convert(TSX_PROFILE, tmp_path))
    assert "(root)" not in quiet
    assert "(idle)" not in quiet


def test_the_root_frame_names_the_process(tmp_path: Path) -> None:
    out = convert(TSX_PROFILE, tmp_path)
    roots = {line.split(";")[0] for line in out.read_text().splitlines()}
    assert len(roots) == 1
    assert next(iter(roots)).startswith("process ")
    assert "wl.ts" in next(iter(roots))


def test_reading_in_tiny_chunks_gives_the_same_stacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    whole = convert(TSX_PROFILE, tmp_path).read_text(encoding="utf-8")
    monkeypatch.setattr(node_module, "_CHUNK_CHARS", 97)
    small = tmp_path / "small.folded"
    convert_profiles([TSX_PROFILE], small)
    assert small.read_text(encoding="utf-8") == whole
    raw = tmp_path / "raw.folded"
    convert_profiles([BUNDLE_RAW], raw)
    monkeypatch.undo()
    assert raw.read_text() == convert(BUNDLE_RAW, tmp_path).read_text()


def test_the_output_is_valid_folded_stacks(tmp_path: Path) -> None:
    out = convert(TSX_PROFILE, tmp_path)
    total = 0
    for line in out.read_text(encoding="utf-8").splitlines():
        stack, _, count = line.rpartition(" ")
        assert stack
        assert count.isdigit()
        assert all(stack.split(";"))
        total += int(count)
    assert total > 0


def test_two_files_are_merged(tmp_path: Path) -> None:
    out = tmp_path / "out.folded"
    one = convert_profiles([BUNDLE_RAW], tmp_path / "one.folded")
    two = convert_profiles([BUNDLE_RAW, BUNDLE_RAW], out)
    assert two["weight"] == 2 * one["weight"]
    assert two["processes"] == 2


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "expected '{'"),
        ("[1]", "expected '{'"),
        ('{"nodes": [1]}', "not an object"),
        ('{"nodes": []}', "holds no profile nodes"),
        ('{"nodes": [{"id": 1}]}', "malformed"),
        ('{"nodes": [{"id": 1, "callFrame": {}, "children": [9]}]}', "9"),
        ('{"nodes": [{"id": 1, "callFrame": {}', "not valid JSON"),
    ],
)
def test_a_file_that_is_not_a_profile_is_refused(
    tmp_path: Path, text: str, message: str
) -> None:
    bad = tmp_path / "bad.cpuprofile"
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(NodeProfileError, match=message):
        convert_profiles([bad], tmp_path / "out.folded")


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(NodeProfileError, match="cannot be read"):
        convert_profiles([tmp_path / "none"], tmp_path / "out.folded")


def test_a_profile_with_no_time_is_refused(tmp_path: Path) -> None:
    empty = tmp_path / "empty.cpuprofile"
    empty.write_text(
        '{"nodes": [{"id": 1, "callFrame": {"functionName": "(root)",'
        ' "scriptId": "0", "url": "", "lineNumber": -1,'
        ' "columnNumber": -1}, "hitCount": 0}]}',
        encoding="utf-8",
    )
    with pytest.raises(NodeProfileError, match="no samples"):
        convert_profiles([empty], tmp_path / "out.folded")


VLQ_EXAMPLE = "AAAA;AACA,CAAC;;AAGA"
"""Mappings of a small source map: segments are (generated column,
source, line, column); ``;`` starts the next generated line."""


def test_mappings_decode_with_state_carried_across_lines() -> None:
    lines = decode_mappings(VLQ_EXAMPLE)
    assert lines == [
        [(0, 0, 0, 0)],
        [(0, 0, 1, 0), (1, 0, 1, 1)],
        [],
        [(0, 0, 4, 1)],
    ]


@pytest.mark.parametrize(
    ("segment", "values"),
    [("A", [0]), ("C", [1]), ("D", [-1]), ("gB", [16]), ("hB", [-16])],
)
def test_vlq_decodes_signed_values(segment: str, values: list[int]) -> None:
    assert decode_vlq(segment) == values


def test_a_lookup_takes_the_segment_at_or_before_the_column() -> None:
    document = {
        "sources": ["a.ts"],
        "mappings": VLQ_EXAMPLE,
    }
    smap = SourceMap(document, "/p")
    assert smap.lookup(1, 0) == ("/p/a.ts", 2)
    assert smap.lookup(1, 5) == ("/p/a.ts", 2)
    assert smap.lookup(2, 0) is None
    assert smap.lookup(99, 0) is None


def write_tiny(directory: Path) -> Path:
    """A JavaScript file run as written, with an external source map.

    The map puts generated line n of ``tiny.js`` on line n + 9 of
    ``tiny.ts``, so the hot loop, on generated line 4, is on line 13.
    """
    script = directory / "tiny.js"
    script.write_text(
        "'use strict';\n"
        "function hot() {\n"
        "  let total = 0;\n"
        "  for (let i = 0; i < 4e7; i++) total += i % 7;\n"
        "  return total;\n"
        "}\n"
        "console.log(hot());\n"
        "//# sourceMappingURL=tiny.js.map\n",
        encoding="utf-8",
    )
    (directory / "tiny.js.map").write_text(
        json.dumps(
            {
                "version": 3,
                "file": "tiny.js",
                "sources": ["tiny.ts"],
                # Generated line index g (1 to 6) is original line
                # index g + 9; line 0 maps to nothing.
                "mappings": ";AAUA" + ";AACA" * 5,
            }
        ),
        encoding="utf-8",
    )
    return script


@pytest.mark.integration
@needs_node
def test_run_follows_an_external_source_map(tmp_path: Path) -> None:
    script = write_tiny(tmp_path)
    out = tmp_path / "out.folded"
    code = main(["run", "--output", str(out), "--", NODE or "", str(script)])
    assert code == 0
    ranked = summarise_file(out, project_root=tmp_path, top_n=10)
    hot = [h for h in ranked if h.function == "hot"]
    assert hot
    assert hot[0].file == "tiny.ts"
    assert hot[0].line is not None
    assert hot[0].line == 13


IDLE_JS = (
    "setTimeout(() => {}, 400);\n"
    "let t = 0; for (let i = 0; i < 1e7; i++) t += i % 3;\n"
)
"""Work, then wait on a timer: the event loop idles for most of it."""


@pytest.mark.integration
@needs_node
def test_idle_is_dropped_unless_asked_for(tmp_path: Path) -> None:
    script = tmp_path / "idle.js"
    script.write_text(IDLE_JS, encoding="utf-8")
    quiet = tmp_path / "quiet.folded"
    loud = tmp_path / "loud.folded"
    command = ["--", NODE or "", str(script)]
    assert main(["run", "--output", str(quiet), *command]) == 0
    assert main(["run", "--idle", "--output", str(loud), *command]) == 0
    assert "(idle)" not in frames(quiet)
    assert "(idle)" in frames(loud)
    weights = {
        path.name: sum(
            int(line.rpartition(" ")[2])
            for line in path.read_text().splitlines()
        )
        for path in (quiet, loud)
    }
    # The wait is most of the run, and only the loud profile has it.
    assert weights["loud.folded"] > 2 * weights["quiet.folded"]


@pytest.mark.integration
@needs_node
def test_run_keeps_the_raw_profiles_and_the_command_status(
    tmp_path: Path,
) -> None:
    script = tmp_path / "w.js"
    script.write_text("let t = 0; for (let i = 0; i < 2e7; i++) t += i % 3;")
    out = tmp_path / "out.folded"
    assert (
        main(["run", "--output", str(out), "--", NODE or "", str(script)]) == 0
    )
    raw = sorted((tmp_path / "out.folded.raw").glob("*.nodeprof.json"))
    assert len(raw) == 1
    saved = json.loads(raw[0].read_text(encoding="utf-8"))
    assert saved["meta"]["argv"][-1] == str(script)
    failing = tmp_path / "f.js"
    failing.write_text("process.exit(7)")
    code = main(
        [
            "run",
            "--output",
            str(tmp_path / "f"),
            "--",
            NODE or "",
            str(failing),
        ]
    )
    assert code == 7


@pytest.mark.integration
@needs_node
def test_run_without_node_or_command_says_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "run",
            "--output",
            str(tmp_path / "py"),
            "--",
            sys.executable,
            "-c",
            "pass",
        ]
    )
    assert code == 3
    assert "wrote no Node profile" in capsys.readouterr().err
    assert main(["run", "--output", str(tmp_path / "x"), "--"]) == 2
    (tmp_path / "again.folded.raw").mkdir()
    assert (
        main(["run", "--output", str(tmp_path / "again.folded"), "--", "x"])
        == 2
    )


@pytest.mark.integration
@needs_node
def test_run_keeps_the_callers_node_options(tmp_path: Path) -> None:
    script = tmp_path / "o.js"
    script.write_text(
        "process.stdout.write(process.env.NODE_OPTIONS + '\\n');"
        "let t = 0; for (let i = 0; i < 1e7; i++) t += i;"
    )
    out = tmp_path / "out.folded"
    env = {**os.environ, "NODE_OPTIONS": "--max-old-space-size=256"}
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "perfproof.profilers.node",
            "run",
            "--output",
            str(out),
            "--",
            NODE or "",
            str(script),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    assert "--max-old-space-size=256" in done.stdout
    assert PRELOAD.as_uri() in done.stdout


def test_convert_command_line(tmp_path: Path) -> None:
    out = tmp_path / "out.folded"
    assert main(["convert", "--output", str(out), str(BUNDLE_RAW)]) == 0
    assert out.read_text(encoding="utf-8")
    bad = tmp_path / "bad"
    bad.write_text("{", encoding="utf-8")
    assert main(["convert", "--output", str(out), str(bad)]) == 3


WORK_JS = """\
function cold() { return 1; }

function hotLoop(n) {
  let total = 0;
  for (let i = 0; i < n; i++) total += i % 7;
  return total;
}

let sum = 0;
for (let r = 0; r < 20; r++) sum += hotLoop(2e6) + cold();
console.log(sum);
"""


@pytest.mark.integration
@needs_node
def test_profile_command_template_ranks_the_hot_function(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "work.js").write_text(WORK_JS, encoding="utf-8")
    spec = BenchmarkSpec.model_validate(
        {
            "name": "work",
            "command": [NODE, "work.js"],
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
            "harness": ["work.js"],
        }
    )
    out = tmp_path / "out"
    out.mkdir()
    env = {
        k: v
        for k, v in os.environ.items()
        if k in ("HOME", "PATH", "TMPDIR", "USER", "LANG")
    }
    profile = profile_benchmark(
        spec,
        tree,
        out,
        profiler="command",
        process_env=env,
        template=(
            sys.executable,
            "-m",
            "perfproof.profilers.node",
            "run",
            "--output",
            "{output}",
            "--",
            "{command}",
        ),
    )
    top = profile.hotspots[0]
    assert (top.function, top.file, top.is_library) == (
        "hotLoop",
        "work.js",
        False,
    )
    # The loop is on source line 5; V8 reports the line the samples hit.
    assert top.line == 5
    assert top.self_fraction > 0.5


TYPED_TS = """\
interface Shape {
  n: number
}

function hotLoop(shape: Shape): number {
  let total = 0
  for (let i = 0; i < shape.n; i++) {
    total += (i * 31) % 7
  }
  return total
}

let sum = 0
for (let r = 0; r < 12; r++) sum += hotLoop({ n: 4e6 })
console.log(sum)
"""
"""TypeScript whose types, once stripped, would move every line."""


def runs_typescript_natively(directory: Path) -> bool:
    """Whether this node runs a ``.ts`` file with no loader."""
    probe = directory / "probe.ts"
    probe.write_text("const x: number = 1\nconsole.log(x)\n")
    done = subprocess.run(
        [NODE or "node", str(probe)],
        capture_output=True,
        check=False,
        timeout=60,
    )
    return done.returncode == 0


@pytest.mark.integration
@needs_node
def test_typescript_node_runs_as_written_keeps_its_lines(
    tmp_path: Path,
) -> None:
    if not runs_typescript_natively(tmp_path):
        pytest.skip("this node does not run .ts files itself")
    script = tmp_path / "hot.ts"
    script.write_text(TYPED_TS, encoding="utf-8")
    out = tmp_path / "out.folded"
    code = main(["run", "--output", str(out), "--", NODE or "", str(script)])
    assert code == 0
    ranked = summarise_file(out, project_root=tmp_path, top_n=10)
    hot = [h for h in ranked if h.function == "hotLoop"]
    assert hot
    assert hot[0].file == "hot.ts"
    # Lines 7 and 8 are the loop; stripped output read without the
    # file would put them where the types used to be.
    assert hot[0].line in (7, 8)


def write_big_profile(path: Path, nodes: int) -> None:
    """A profile file of many small nodes, written a node at a time."""
    frame = (
        '{"functionName":"f","scriptId":"1","url":"file:///a.js",'
        '"lineNumber":1,"columnNumber":1}'
    )
    with path.open("w", encoding="utf-8") as sink:
        sink.write('{"meta":{},"scripts":{},"nodes":[')
        for i in range(nodes):
            sep = "," if i else ""
            sink.write(
                f'{sep}{{"id":{i + 1},"callFrame":{frame},'
                f'"selfMicros":1,"children":[]}}\n'
            )
        sink.write("]}\n")


def test_the_reader_streams_a_large_profile(tmp_path: Path) -> None:
    big = tmp_path / "big.nodeprof.json"
    write_big_profile(big, 250_000)
    size = big.stat().st_size
    assert size > 30_000_000
    count = 0
    tracemalloc.start()
    try:
        with big.open(encoding="utf-8") as handle:
            reader = node_module._Reader(handle)  # pyright: ignore[reportPrivateUsage]
            for item in reader.items():
                count += item[0] == "nodes"
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert count == 250_000
    # Reading the whole file into one string would peak above its size.
    assert peak < size // 4


def test_one_value_beyond_the_buffer_limit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(node_module, "_MAX_BUFFER_CHARS", 10_000)
    monkeypatch.setattr(node_module, "_CHUNK_CHARS", 1_000)
    huge = tmp_path / "huge.cpuprofile"
    huge.write_text(
        '{"meta": {"x": "' + "a" * 50_000 + '"}, "nodes": []}',
        encoding="utf-8",
    )
    with pytest.raises(NodeProfileError, match="larger than the reader"):
        convert_profiles([huge], tmp_path / "out.folded")


def listing() -> str:
    """The arguments of every process on the machine."""
    return subprocess.run(
        ["ps", "-A", "-o", "args="],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout


def test_a_timeout_kills_the_commands_descendants(tmp_path: Path) -> None:
    marker = "47213"
    code = main(
        [
            "run",
            "--timeout",
            "1",
            "--output",
            str(tmp_path / "out.folded"),
            "--",
            "sh",
            "-c",
            f"sleep {marker} & wait",
        ]
    )
    assert code == 2
    assert f"sleep {marker}" not in listing()


def test_a_command_that_keeps_forking_leaves_nothing_behind(
    tmp_path: Path,
) -> None:
    marker = "47215"
    loop = f"while true; do sleep {marker} & sleep 0.01; done"
    for i in range(100):
        code = main(
            [
                "run",
                "--timeout",
                "0.2",
                "--output",
                str(tmp_path / f"out{i}.folded"),
                "--",
                "sh",
                "-c",
                loop,
            ]
        )
        assert code == 2
    assert f"sleep {marker}" not in listing()
