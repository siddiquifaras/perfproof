"""The .NET profiler wrapper: dotnet-trace Speedscope to folded stacks.

``trace.speedscope.json`` is what dotnet-trace 10.0.750501 wrote for
the fixture program on .NET 10.0.13
(``tests/fixtures/dotnet/SOURCE.md``).
The integration tests run the real ``dotnet`` and ``dotnet-trace`` and
skip with their reason where either is missing (this Mac has neither;
they run in the Linux check in the official SDK image).
"""

import io
import json
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

from perfproof.profilers import dotnet
from perfproof.profilers.dotnet import (
    SourceIndex,
    SpeedscopeFormatError,
    fold_speedscope,
)
from perfproof.profilers.folded import summarise, summarise_file

FIXTURES = Path(__file__).parent.parent / "fixtures" / "dotnet"
PROG = FIXTURES / "prog"
TRACE = FIXTURES / "trace.speedscope.json"


def fold(path: Path, index: SourceIndex, size: int = 1 << 20) -> list[str]:
    text = path.read_text(encoding="utf-8")
    chunks = (text[i : i + size] for i in range(0, len(text), size))
    out = io.StringIO()
    fold_speedscope(chunks, index, out)
    return out.getvalue().splitlines()


def weights(lines: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for line in lines:
        stack, _, count = line.rpartition(" ")
        found[stack] = found.get(stack, 0) + int(count)
    return found


def test_the_captured_trace_ranks_the_known_hot_methods() -> None:
    lines = fold(TRACE, SourceIndex(PROG))

    top = summarise(lines, project_root=PROG, top_n=12, own_modules={"App"})

    mine = {h.function: h for h in top if h.function.startswith("Prog.")}
    assert {"Prog.Work.Histogram", "Prog.Work.Checksum"} <= set(mine)
    histogram = mine["Prog.Work.Histogram"]
    assert (histogram.file, histogram.line) == ("App/Work.cs", 18)
    assert histogram.is_library is False
    assert histogram.self_fraction > 0.05
    assert any(h.function == "[unmanaged code]" for h in top)


def test_the_streamed_parse_matches_a_plain_json_walk() -> None:
    """The streaming reader adds up the same time as json.load does."""
    data = json.loads(TRACE.read_text(encoding="utf-8"))
    total = 0
    for profile in data["profiles"]:
        depth = 0
        previous = profile["startValue"]
        for event in profile["events"]:
            micros = round((event["at"] - previous) * 1000)
            if micros > 0 and depth:
                total += micros
            previous = event["at"]
            depth += 1 if event["type"] == "O" else -1
        assert depth == 0

    got = weights(fold(TRACE, SourceIndex(PROG)))

    assert sum(got.values()) == total
    assert all(w > 0 for w in got.values())
    first = data["profiles"][0]
    duration = first["endValue"] - first["startValue"]
    assert sum(got.values()) == pytest.approx(duration * 1000, rel=0.001)


@pytest.mark.parametrize("size", [7, 100, 4096])
def test_chunk_boundaries_do_not_change_the_result(size: int) -> None:
    index = SourceIndex(PROG)
    assert weights(fold(TRACE, index, size)) == weights(fold(TRACE, index))


def test_frames_at_the_roots_and_the_sample_mark_are_dropped() -> None:
    lines = fold(TRACE, SourceIndex(PROG))
    text = "\n".join(lines)
    assert "Process64" not in text
    assert "CPU_TIME" not in text
    assert "(Non-Activities)" not in text
    assert "Thread (" not in text
    assert "Prog.Work.JoinSlow (App/Work.cs:8)" in text


def test_memory_stays_bounded_in_the_number_of_distinct_stacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dotnet, "FLUSH_STACKS", 3)
    index = SourceIndex(PROG)
    flushed = fold(TRACE, index)
    monkeypatch.setattr(dotnet, "FLUSH_STACKS", 10**9)
    assert weights(flushed) == weights(fold(TRACE, index))


def layout(events: str, unit: str = "milliseconds") -> str:
    return (
        '{ "shared": { "frames": [ { "name": "A!X.M()" }, '
        '{ "name": "A!X.N()" }] }, "profiles": [ { "type": "evented", '
        f'"name": "T", "unit": "{unit}", "startValue": 0, "endValue": 9, '
        f'"events": [ {events}]}}] }}'
    )


def open_close(frame: int, at: int, kind: str) -> str:
    return f'{{ "type": "{kind}", "frame": {frame}, "at": {at} }}'


def fold_text(text: str) -> list[str]:
    out = io.StringIO()
    fold_speedscope(iter([text]), SourceIndex(Path("/nonexistent")), out)
    return out.getvalue().splitlines()


def test_time_goes_to_the_stack_open_before_the_event() -> None:
    events = ", ".join(
        [
            open_close(0, 1, "O"),
            open_close(1, 3, "O"),
            open_close(1, 6, "C"),
            open_close(0, 10, "C"),
        ]
    )
    assert fold_text(layout(events)) == [
        "A`X.M 6000",
        "A`X.M;A`X.N 3000",
    ]


@pytest.mark.parametrize(
    ("events", "unit", "message"),
    [
        (open_close(5, 1, "O"), "milliseconds", "was not defined"),
        (open_close(0, 1, "C"), "milliseconds", "not on top"),
        (
            ", ".join(
                [
                    open_close(0, 1, "O"),
                    open_close(1, 2, "O"),
                    open_close(0, 3, "C"),
                ]
            ),
            "milliseconds",
            "not on top",
        ),
        (open_close(0, 1, "O"), "nanoseconds", "not milliseconds"),
    ],
)
def test_a_layout_that_is_not_the_tools_is_refused(
    events: str, unit: str, message: str
) -> None:
    with pytest.raises(SpeedscopeFormatError, match=message):
        fold_text(layout(events, unit))


def test_a_frame_name_cannot_forge_an_event() -> None:
    forged = r"A!X.M(\"type\": \"O\", \"frame\": 0)"
    text = layout(open_close(0, 1, "O") + ", " + open_close(0, 4, "C"))
    text = text.replace("A!X.M()", forged)
    assert fold_text(text) == ["A`X.M 3000"]


def write_tree(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_the_index_finds_types_and_methods_in_the_fixture() -> None:
    index = SourceIndex(PROG)
    assert index.locate("Prog.Work", "JoinSlow") == ("App/Work.cs", 8)
    assert index.locate("Prog.Work", "Histogram") == ("App/Work.cs", 18)
    assert index.locate("Prog.Work", "Checksum") == ("App/Work.cs", 29)
    assert index.locate("Prog.Work", "NoSuchMethod") == ("App/Work.cs", 6)
    assert index.locate("Prog.Other", "JoinSlow") is None
    assert index.locate("System.String", "Concat") is None


def test_the_index_reads_block_namespaces_nesting_and_lambdas(
    tmp_path: Path,
) -> None:
    write_tree(
        tmp_path,
        "src/Shop.cs",
        "using System;\n"
        "namespace Shop.Core\n"
        "{\n"
        "    public sealed partial class Cart\n"
        "    {\n"
        "        public Cart() { }\n"
        "        public int Total() => 1;\n"
        "        private static void Helper<T>(T x) { }\n"
        "        class Inner { void Go() { } }\n"
        "    }\n"
        "    internal record struct Point(int X, int Y);\n"
        "}\n",
    )
    write_tree(
        tmp_path, "obj/Generated.cs", "namespace Gen { class Skip { } }"
    )
    index = SourceIndex(tmp_path)

    assert index.locate("Shop.Core.Cart", "Total") == ("src/Shop.cs", 7)
    assert index.locate("Shop.Core.Cart", ".ctor") == ("src/Shop.cs", 6)
    assert index.locate("Shop.Core.Cart", "Helper") == ("src/Shop.cs", 8)
    assert index.locate("Shop.Core.Cart+Inner", "Go") == ("src/Shop.cs", 9)
    assert index.locate("Shop.Core.Cart", "<Total>b__0_0") == (
        "src/Shop.cs",
        7,
    )
    assert index.locate("Shop.Core.Point", "Deconstruct") == (
        "src/Shop.cs",
        11,
    )
    assert index.locate("Gen.Skip", "X") is None


def test_a_file_over_the_size_limit_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_tree(tmp_path, "A.cs", "namespace N { class Big { } }")
    monkeypatch.setattr(dotnet, "MAX_SOURCE_BYTES", 5)
    assert SourceIndex(tmp_path).locate("N.Big", "M") is None


def test_frames_that_are_not_the_projects_stay_assembly_symbols() -> None:
    lines = fold(TRACE, SourceIndex(PROG))
    library = [
        h
        for h in summarise(lines, project_root=PROG, top_n=20)
        if h.function.startswith("System.Private.CoreLib`")
    ]
    assert library
    assert all(h.is_library and h.file is None for h in library)


def stand_in_tool(directory: Path, body: str) -> Path:
    path = directory / "dotnet-trace"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_a_missing_dotnet_trace_says_so(tmp_path: Path) -> None:
    status = dotnet.record(
        ["dotnet", "x.dll"],
        tmp_path / "o.folded",
        dotnet_trace=tmp_path / "missing",
    )
    assert status == 4
    assert not (tmp_path / "o.folded").exists()


def test_files_of_an_earlier_run_are_refused(tmp_path: Path) -> None:
    tool = stand_in_tool(tmp_path, "exit 0")
    leftover = tmp_path / "o.folded.dotnet-trace"
    leftover.mkdir()
    (leftover / "trace").write_text("x")
    assert dotnet.record(["x"], tmp_path / "o.folded", dotnet_trace=tool) == 7


def test_a_run_with_no_trace_keeps_the_tools_status(tmp_path: Path) -> None:
    tool = stand_in_tool(tmp_path, "exit 2")
    status = dotnet.record(["x"], tmp_path / "o.folded", dotnet_trace=tool)
    assert status == 2
    assert not (tmp_path / "o.folded").exists()


def test_a_run_with_no_trace_and_status_zero_is_an_error(
    tmp_path: Path,
) -> None:
    tool = stand_in_tool(tmp_path, "exit 0")
    assert dotnet.record(["x"], tmp_path / "o", dotnet_trace=tool) == 5


def test_the_command_reaches_the_tool_after_the_double_dash(
    tmp_path: Path,
) -> None:
    seen = tmp_path / "argv.txt"
    tool = stand_in_tool(tmp_path, f'echo "$@" > "{seen}"')
    dotnet.record(
        ["dotnet", "bin/App.dll", "3"], tmp_path / "o", dotnet_trace=tool
    )
    argv = seen.read_text().split()
    assert argv[:3] == ["collect", "--format", "Speedscope"]
    assert argv[argv.index("--") + 1 :] == ["dotnet", "bin/App.dll", "3"]


def env_seen_by_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, **env: str
) -> str | None:
    seen = tmp_path / "env.txt"
    tool = stand_in_tool(tmp_path, f'echo "${{DOTNET_ROOT-unset}}" > "{seen}"')
    monkeypatch.delenv("DOTNET_ROOT", raising=False)
    monkeypatch.setenv("PATH", path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    dotnet.record(["x"], tmp_path / "o", dotnet_trace=tool)
    return seen.read_text().strip()


def fake_dotnet(tmp_path: Path) -> tuple[Path, Path]:
    real = tmp_path / "real"
    real.mkdir()
    host = real / "dotnet"
    host.write_text("#!/bin/sh\n")
    host.chmod(0o755)
    links = tmp_path / "links"
    links.mkdir()
    (links / "dotnet").symlink_to(host)
    return links, real


def test_dotnet_root_is_the_directory_the_dotnet_symlink_points_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    links, real = fake_dotnet(tmp_path)
    seen = env_seen_by_tool(tmp_path, monkeypatch, str(links))
    assert seen == str(real.resolve())


def test_an_explicit_dotnet_root_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    links, _ = fake_dotnet(tmp_path)
    seen = env_seen_by_tool(
        tmp_path, monkeypatch, str(links), DOTNET_ROOT="/chosen/root"
    )
    assert seen == "/chosen/root"


def test_no_dotnet_on_the_path_adds_no_dotnet_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert env_seen_by_tool(tmp_path, monkeypatch, str(empty)) == "unset"


def test_a_trace_with_no_samples_is_an_error(tmp_path: Path) -> None:
    empty = layout("")
    tool = stand_in_tool(
        tmp_path,
        f"""out=""
while [ $# -gt 0 ]; do
  [ "$1" = "-o" ] && out=$2
  shift
done
printf '%s' '{empty}' > "$out.speedscope.json"
""",
    )
    status = dotnet.record(["x"], tmp_path / "o", dotnet_trace=tool)
    assert status == 6


def running(pid: int) -> bool:
    done = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    state = done.stdout.strip()
    return bool(state) and not state.startswith("Z")


def test_a_timeout_kills_the_whole_process_tree(tmp_path: Path) -> None:
    pidfile = tmp_path / "child.pid"
    tool = stand_in_tool(tmp_path, f'sleep 300 &\necho $! > "{pidfile}"\nwait')
    started = time.monotonic()

    status = dotnet.record(
        ["x"], tmp_path / "o", dotnet_trace=tool, timeout_seconds=1.0
    )

    assert status == 124
    assert time.monotonic() - started < 30
    time.sleep(0.2)
    assert not running(int(pidfile.read_text()))


def real_tools() -> tuple[str, str]:
    dotnet_exe = shutil.which("dotnet")
    tool = dotnet.find_dotnet_trace(None)
    if dotnet_exe is None or tool is None:
        pytest.skip("no dotnet and dotnet-trace on the PATH")
    return dotnet_exe, tool


@pytest.mark.integration
def test_the_real_dotnet_trace_profiles_a_built_program(
    tmp_path: Path,
) -> None:
    dotnet_exe, tool = real_tools()
    tree = tmp_path / "prog"
    shutil.copytree(PROG, tree)
    subprocess.run(
        [
            dotnet_exe,
            "build",
            "-c",
            "Release",
            "-o",
            str(tree / "bin"),
            str(tree / "App" / "App.csproj"),
        ],
        check=True,
        capture_output=True,
        timeout=600,
    )
    output = tmp_path / "hot.folded"

    status = dotnet.record(
        [dotnet_exe, str(tree / "bin" / "App.dll"), "1.5"],
        output,
        project_root=tree,
        dotnet_trace=Path(tool),
        timeout_seconds=300,
    )

    assert status == 0
    top = summarise_file(
        output, project_root=tree, top_n=12, own_modules={"App"}
    )
    mine = {h.function: h for h in top if h.function.startswith("Prog.")}
    assert mine
    assert all(h.file == "App/Work.cs" for h in mine.values())
    assert not any(h.is_library for h in mine.values())
    assert (tmp_path / "hot.folded.dotnet-trace" / "trace").is_file()
