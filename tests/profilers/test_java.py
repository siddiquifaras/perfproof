"""The JVM profiler bridge: JFR text to folded stacks."""

import io
import os
import shutil
import subprocess
import sys
import time
import tracemalloc
from collections.abc import Iterator
from pathlib import Path

import pytest

from perfproof.profilers.folded import summarise
from perfproof.profilers.java import (
    JfrFormatError,
    SourceIndex,
    fold_jfr_text,
    main,
    record,
    recording_options,
    run_command,
    source_path,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "java"
JFR_PRINT = FIXTURES / "jfr-print.txt"
ASYNC_PRINT = FIXTURES / "async-profiler-print.txt"
JFR_PRINT_LINUX = FIXTURES / "jfr-print-linux.txt"
ASYNC_PRINT_LINUX = FIXTURES / "async-profiler-print-linux.txt"
BASE64 = "src/main/java/tools/jackson/core/Base64Variant.java"


def project(tmp_path: Path) -> Path:
    """A tree holding the one project class the fixtures profile."""
    path = tmp_path / BASE64
    path.parent.mkdir(parents=True)
    path.write_text("package tools.jackson.core;\n")
    return tmp_path


def fold(text: str, root: Path) -> list[str]:
    out = io.StringIO()
    fold_jfr_text(text.splitlines(), SourceIndex(root), out)
    return out.getvalue().splitlines()


def sample_count(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    return text.count("jdk.ExecutionSample {")


def total(lines: list[str]) -> int:
    return sum(int(line.rpartition(" ")[2]) for line in lines)


@pytest.mark.parametrize(
    "fixture", [JFR_PRINT, ASYNC_PRINT, JFR_PRINT_LINUX, ASYNC_PRINT_LINUX]
)
def test_every_sample_is_counted_once(fixture: Path, tmp_path: Path) -> None:
    lines = fold(fixture.read_text(encoding="utf-8"), project(tmp_path))

    assert total(lines) == sample_count(fixture)


def test_stacks_run_root_first_and_name_project_files(
    tmp_path: Path,
) -> None:
    root = project(tmp_path)
    lines = fold(JFR_PRINT.read_text(encoding="utf-8"), root)

    stacks = [line.rpartition(" ")[0].split(";") for line in lines]
    encode = [s for s in stacks if any("encodeBase64Chunk" in f for f in s)]
    assert encode
    first = encode[0]
    assert first[0].startswith("Base64Workload.main (")
    chunk = next(f for f in first if "encodeBase64Chunk" in f)
    assert chunk.startswith(
        f"tools.jackson.core.Base64Variant.encodeBase64Chunk ({BASE64}:4"
    )
    leaf = first[-1]
    assert leaf.startswith("java.")
    assert "(java/" in leaf or "(jdk/" in leaf


def test_summary_separates_project_code_from_the_jdk(tmp_path: Path) -> None:
    root = project(tmp_path)
    lines = fold(JFR_PRINT.read_text(encoding="utf-8"), root)

    hotspots = summarise(lines, project_root=root, top_n=50)

    own = {h.function for h in hotspots if not h.is_library}
    library = {h.function for h in hotspots if h.is_library}
    assert "tools.jackson.core.Base64Variant.encodeBase64Chunk" in own
    assert any(f.startswith("java.lang.") for f in library)
    for hotspot in hotspots:
        if hotspot.function.startswith("tools.jackson."):
            assert hotspot.file == BASE64
            assert hotspot.line is not None
    assert sum(h.self_fraction for h in hotspots) <= 1.0 + 1e-9


def test_async_profiler_native_frames_are_native_library_frames(
    tmp_path: Path,
) -> None:
    root = project(tmp_path)
    lines = fold(ASYNC_PRINT.read_text(encoding="utf-8"), root)

    assert any("libjvm.dylib`" in line for line in lines)
    hotspots = summarise(lines, project_root=root, top_n=500)
    native = [h for h in hotspots if h.function.startswith("libjvm.dylib`")]
    assert native
    assert all(h.is_library and h.file is None for h in native)


def test_frames_of_no_known_shape_become_native_symbols() -> None:
    # The form async-profiler wrote for a JVM stub in one real run:
    # no class, so no field of Class.method(params) line: n fits.
    text = (
        "jdk.ExecutionSample {\n"
        "  stackTrace = [\n"
        "    .arrayof_jlong_disjoint_arraycopy() line: 0\n"
        "    a.B.c(int) line: 7\n"
        "  ]\n"
        "}\n"
    )

    lines = fold(text, Path("/nonexistent"))

    assert lines == ["a.B.c (a/B.java:7);arrayof_jlong_disjoint_arraycopy() 1"]


def test_truncated_and_missing_stacks() -> None:
    text = (
        "jdk.ExecutionSample {\n"
        "  stackTrace = [\n"
        "    a.B.leaf() line: 3\n"
        "    ...\n"
        "  ]\n"
        "}\n"
        "jdk.ExecutionSample {\n"
        "  stackTrace = null\n"
        "}\n"
    )

    lines = fold(text, Path("/nonexistent"))

    assert lines == ["a.B.leaf (a/B.java:3) 1", " 1"]
    hotspots = summarise(lines, project_root=Path("/nonexistent"))
    assert hotspots[0].self_fraction == pytest.approx(0.5)


def test_equal_stacks_are_merged() -> None:
    sample = (
        "jdk.ExecutionSample {\n  stackTrace = [\n"
        "    a.B.leaf() line: 3\n  ]\n}\n"
    )

    assert fold(sample * 4, Path("/nonexistent")) == [
        "a.B.leaf (a/B.java:3) 4"
    ]


def test_a_line_of_zero_or_less_is_shown_as_zero() -> None:
    sample = (
        "jdk.ExecutionSample {\n  stackTrace = [\n"
        "    a.B.bridge() line: -1\n  ]\n}\n"
    )

    assert fold(sample, Path("/nonexistent")) == ["a.B.bridge (a/B.java:0) 1"]


def test_input_that_ends_inside_a_sample_is_refused() -> None:
    cut = "jdk.ExecutionSample {\n  stackTrace = [\n    a.B.c() line: 1\n"

    with pytest.raises(JfrFormatError, match="ends inside a sample"):
        fold(cut, Path("/nonexistent"))


class _CountingSink(io.StringIO):
    """A sink that keeps the number of samples written, not the text."""

    def __init__(self) -> None:
        super().__init__()
        self.samples = 0
        self.lines = 0

    def write(self, text: str) -> int:
        self.lines += 1
        self.samples += int(text.rpartition(" ")[2])
        return len(text)


def test_a_stream_is_read_once_and_held_in_a_byte_budget(
    tmp_path: Path,
) -> None:
    sample = (
        "jdk.ExecutionSample {\n  stackTrace = [\n"
        "    a.B.f{n}() line: {n}\n  ]\n}\n"
    )

    def generate() -> Iterator[str]:
        for n in range(120_000):
            yield from sample.replace("{n}", str(n)).splitlines()

    sink = _CountingSink()
    tracemalloc.start()
    try:
        count = fold_jfr_text(
            generate(), SourceIndex(tmp_path), sink, flush_bytes=1 << 20
        )
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert count == 120_000
    assert sink.samples == 120_000
    # Holding all 120,000 stacks takes over 15 MiB; the budget is 1.
    assert peak < 8 << 20


def test_source_path_belongs_to_the_outer_class() -> None:
    assert source_path("org.x.Outer") == "org/x/Outer.java"
    assert source_path("org.x.Outer$Inner$1") == "org/x/Outer.java"
    assert source_path("org.x.Outer$$Lambda/0x0000a1b2") == "org/x/Outer.java"
    assert source_path("Main") == "Main.java"


def test_source_index_prefers_main_and_skips_build_output(
    tmp_path: Path,
) -> None:
    for rel in (
        "a/src/test/java/org/x/Thing.java",
        "a/src/main/java/org/x/Thing.java",
        "a/target/classes/org/x/Thing.java",
        "a/.hidden/org/x/Thing.java",
        "b/src/main/java/org/y/Thing.java",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    index = SourceIndex(tmp_path)

    assert index.locate("org.x.Thing$Inner") == (
        "a/src/main/java/org/x/Thing.java"
    )
    assert index.locate("org.y.Thing") == "b/src/main/java/org/y/Thing.java"
    assert index.locate("org.z.Thing") is None
    assert index.locate("java.util.HashMap") is None


def test_source_index_honours_source_roots(tmp_path: Path) -> None:
    for rel in ("one/org/x/A.java", "two/org/x/A.java"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True)
        path.write_text("")

    index = SourceIndex(tmp_path, [tmp_path / "two"])

    assert index.locate("org.x.A") == "two/org/x/A.java"


def test_record_without_a_jfr_says_why(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("JAVA_HOME", raising=False)

    status = record(["java", "-version"], tmp_path / "out.folded")

    assert status == 4
    assert "jfr was not found" in capsys.readouterr().err
    assert not (tmp_path / "out.folded").exists()


def test_record_refuses_a_path_java_cannot_carry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spaced = tmp_path / "a b"
    spaced.mkdir()

    status = record(["java"], spaced / "out.folded")

    assert status == 4
    assert "whitespace" in capsys.readouterr().err


def test_record_refuses_a_missing_async_library(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = record(["java"], tmp_path / "out", async_lib=tmp_path / "nope.so")

    assert status == 4
    assert "does not exist" in capsys.readouterr().err


def test_record_rejects_bad_numbers(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        record(["java"], tmp_path / "out", interval_ms=0)


def test_main_needs_a_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        main(["record", "--output", str(tmp_path / "o"), "--"])

    assert "give the command" in capsys.readouterr().err


HOT = """\
public final class Hot {
    static long sink;

    static void spin(long n) {
        for (long i = 0; i < n; i++) {
            sink += i ^ (sink >>> 3);
        }
    }

    public static void main(String[] args) {
        long end = System.nanoTime() + 1_500_000_000L;
        while (System.nanoTime() < end) {
            spin(10_000_000L);
        }
        System.out.println(sink);
    }
}
"""


def jdk_tools() -> tuple[str, str, str]:
    java, javac, jfr = (shutil.which(n) for n in ("java", "javac", "jfr"))
    if java is None or javac is None or jfr is None:
        pytest.skip("no JDK with java, javac and jfr on the PATH")
    probe = subprocess.run(
        [java, "-version"], capture_output=True, check=False, timeout=60
    )
    if probe.returncode != 0:
        pytest.skip("java on the PATH is not a working JDK")
    return java, javac, jfr


@pytest.mark.integration
def test_flight_recorder_end_to_end(tmp_path: Path) -> None:
    java, javac, _ = jdk_tools()
    src = tmp_path / "proj" / "src" / "main" / "java"
    src.mkdir(parents=True)
    (src / "Hot.java").write_text(HOT)
    classes = tmp_path / "proj" / "target" / "classes"
    classes.mkdir(parents=True)
    subprocess.run(
        [javac, "-d", str(classes), str(src / "Hot.java")],
        check=True,
        timeout=120,
    )
    output = tmp_path / "out.folded"

    status = main(
        [
            "record",
            "--output",
            str(output),
            "--project-root",
            str(tmp_path / "proj"),
            "--",
            java,
            "-cp",
            str(classes),
            "Hot",
        ]
    )

    assert status == 0
    assert list(tmp_path.glob("out.folded.*.jfr"))
    hotspots = summarise(
        output.read_text(encoding="utf-8").splitlines(),
        project_root=tmp_path / "proj",
    )
    top = hotspots[0]
    assert top.function == "Hot.spin"
    assert top.file == "src/main/java/Hot.java"
    assert top.line in (5, 6)
    assert not top.is_library
    assert top.self_fraction > 0.5


@pytest.mark.integration
def test_the_wrapper_runs_as_a_module(tmp_path: Path) -> None:
    java, javac, _ = jdk_tools()
    (tmp_path / "Hot.java").write_text(HOT)
    subprocess.run(
        [javac, "-d", str(tmp_path), str(tmp_path / "Hot.java")],
        check=True,
        timeout=120,
    )
    output = tmp_path / "out.folded"

    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "perfproof.profilers.java",
            "record",
            "--output",
            str(output),
            "--project-root",
            str(tmp_path),
            "--",
            java,
            "-cp",
            str(tmp_path),
            "Hot",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**os.environ},
    )

    assert done.returncode == 0, done.stderr
    assert "Hot.spin (Hot.java:" in output.read_text(encoding="utf-8")


@pytest.mark.integration
def test_a_failing_command_keeps_its_status_and_its_profile(
    tmp_path: Path,
) -> None:
    java, javac, _ = jdk_tools()
    (tmp_path / "Hot.java").write_text(
        HOT.replace("System.out.println(sink);", "System.exit(3);")
    )
    subprocess.run(
        [javac, "-d", str(tmp_path), str(tmp_path / "Hot.java")],
        check=True,
        timeout=120,
    )

    status = record(
        [java, "-cp", str(tmp_path), "Hot"],
        tmp_path / "out.folded",
        project_root=tmp_path,
    )

    assert status == 3
    assert (tmp_path / "out.folded").stat().st_size > 0


def test_jfr_settings_are_added_to_therecording_options() -> None:
    out = Path("/r/out.folded")

    plain = recording_options(out, 2, 64)
    extra = recording_options(
        out, 2, 64, jfr_settings=["jdk.JavaMonitorEnter#threshold=0ms"]
    )

    assert plain.count("-XX:StartFlightRecording:") == 1
    assert "-XX:+DebugNonSafepoints" in plain
    assert "-XX:FlightRecorderOptions:stackdepth=64 " in plain
    assert "stackdepth" not in plain.split("StartFlightRecording")[1]
    assert "filename=/r/out.folded.%p.jfr" in plain
    assert "jdk.ExecutionSample#period=2ms" in plain
    assert extra == plain + ",jdk.JavaMonitorEnter#threshold=0ms"


def test_async_profiler_options_name_the_agent_and_a_jfr_file() -> None:
    options = recording_options(
        Path("/r/out.folded"), 2, 64, Path("/lib/libasyncProfiler.so")
    )

    assert "-agentpath:/lib/libasyncProfiler.so=start,event=cpu," in options
    assert "file=/r/out.folded.%p.jfr,jfr" in options
    assert "StartFlightRecording" not in options


@pytest.mark.parametrize(
    "bad", ["no-hash", "a#b", "a#b=c d", "a#b=c,d", "a#b=c;rm"]
)
def test_record_rejects_a_setting_that_could_carry_other_options(
    bad: str, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="event#key=value"):
        record(["java"], tmp_path / "out", jfr_settings=[bad])


def test_memory_is_bounded_by_bytes_not_by_stack_count() -> None:
    sample = (
        "jdk.ExecutionSample {\n  stackTrace = [\n"
        "    a.B.{n}() line: 1\n  ]\n}\n"
    )
    lines = [
        row
        for n in ("x", "y", "x", "y", "x")
        for row in sample.replace("{n}", n).splitlines()
    ]
    out = io.StringIO()

    count = fold_jfr_text(lines, SourceIndex(Path("/nonexistent")), out, 1)

    written = out.getvalue().splitlines()
    assert count == 5
    assert len(written) == 5
    assert total(written) == 5


def test_recordings_of_an_earlier_run_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "out.folded.123.jfr").write_bytes(b"")

    status = record(["java"], tmp_path / "out.folded")

    assert status == 7
    assert "earlier run" in capsys.readouterr().err


SLOW = """\
public final class Slow {
    public static void main(String[] args) throws Exception {
        Thread.sleep(60_000);
    }
}
"""

DEEP = """\
public final class Deep {
    static long sink;

    static long down(int n) {
        if (n == 0) {
            long end = System.nanoTime() + 800_000_000L;
            long x = 0;
            while (System.nanoTime() < end) {
                x += System.nanoTime() & 3;
            }
            return x;
        }
        return down(n - 1) + 1;
    }

    public static void main(String[] args) {
        System.out.println(down(200));
    }
}
"""


def compile_one(tmp_path: Path, name: str, source: str) -> Path:
    _, javac, _ = jdk_tools()
    (tmp_path / f"{name}.java").write_text(source)
    subprocess.run(
        [javac, "-d", str(tmp_path), str(tmp_path / f"{name}.java")],
        check=True,
        timeout=120,
    )
    return tmp_path


@pytest.mark.integration
def test_stacks_deeper_than_the_jvm_default_are_kept(tmp_path: Path) -> None:
    java, _, _ = jdk_tools()
    classes = compile_one(tmp_path, "Deep", DEEP)

    status = record(
        [java, "-cp", str(classes), "Deep"],
        tmp_path / "out.folded",
        project_root=tmp_path,
        stack_depth=256,
    )

    assert status == 0
    depths = [
        len(line.rpartition(" ")[0].split(";"))
        for line in (tmp_path / "out.folded").read_text().splitlines()
    ]
    assert max(depths) > 200


@pytest.mark.integration
def test_a_command_past_its_timeout_is_killed(tmp_path: Path) -> None:
    java, _, _ = jdk_tools()
    classes = compile_one(tmp_path, "Slow", SLOW)

    status = record(
        [java, "-cp", str(classes), "Slow"],
        tmp_path / "out.folded",
        timeout_seconds=2,
    )

    assert status == 124


def test_linux_async_profiler_native_frames_are_library_frames(
    tmp_path: Path,
) -> None:
    root = project(tmp_path)
    lines = fold(ASYNC_PRINT_LINUX.read_text(encoding="utf-8"), root)

    assert any("libjvm.so`" in line for line in lines)
    hotspots = summarise(lines, project_root=root, top_n=500)
    native = [h for h in hotspots if h.function.startswith("libjvm.so`")]
    assert native
    assert all(h.is_library for h in native)


def test_linux_flight_recorder_names_project_files(tmp_path: Path) -> None:
    root = project(tmp_path)
    lines = fold(JFR_PRINT_LINUX.read_text(encoding="utf-8"), root)

    hotspots = summarise(lines, project_root=root, top_n=50)

    own = {h.function for h in hotspots if not h.is_library}
    assert "tools.jackson.core.Base64Variant.encodeBase64Chunk" in own


def test_a_timeout_kills_the_commands_descendants(tmp_path: Path) -> None:
    marker = "47113"
    started = time.monotonic()

    status, timed_out = run_command(
        ["sh", "-c", f"sleep {marker} & wait"], dict(os.environ), 1.0
    )

    assert (status, timed_out) == (124, True)
    assert time.monotonic() - started < 20
    listing = subprocess.run(
        ["ps", "-A", "-o", "args="],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout
    assert f"sleep {marker}" not in listing


def test_a_command_that_keeps_forking_leaves_nothing_behind() -> None:
    marker = "47115"
    loop = f"while true; do sleep {marker} & sleep 0.01; done"
    for _ in range(100):
        run_command(["sh", "-c", loop], dict(os.environ), 0.2)

    listing = subprocess.run(
        ["ps", "-A", "-o", "args="],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout
    assert f"sleep {marker}" not in listing


def test_the_recording_options_are_appended_to_the_callers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "jfr"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    seen = tmp_path / "seen"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("JAVA_TOOL_OPTIONS", "-Dcaller=1")

    status = record(
        ["sh", "-c", f'printf %s "$JAVA_TOOL_OPTIONS" > {seen}'],
        tmp_path / "out.folded",
    )

    assert status == 5
    options = seen.read_text()
    assert options.startswith("-Dcaller=1 -XX:+UnlockDiagnosticVMOptions")
    assert options.count("-XX:StartFlightRecording:") == 1


@pytest.mark.integration
def test_recordings_of_two_jvms_are_merged(tmp_path: Path) -> None:
    java, _, _ = jdk_tools()
    classes = compile_one(tmp_path, "Deep", DEEP)
    output = tmp_path / "out.folded"

    status = record(
        [
            "sh",
            "-c",
            f"{java} -cp {classes} Deep; {java} -cp {classes} Deep",
        ],
        output,
        project_root=tmp_path,
    )

    assert status == 0
    assert len(list(tmp_path.glob("out.folded.*.jfr"))) == 2
    single = tmp_path / "single.folded"
    assert (
        record(
            [java, "-cp", str(classes), "Deep"], single, project_root=tmp_path
        )
        == 0
    )
    merged = sum(
        int(row.rpartition(" ")[2]) for row in output.read_text().splitlines()
    )
    one = sum(
        int(row.rpartition(" ")[2]) for row in single.read_text().splitlines()
    )
    assert merged > one
