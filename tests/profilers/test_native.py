"""The native profiler wrapper: sample and perf text to folded stacks.

The fixtures under ``tests/fixtures/native`` are captures of the real
tools (see ``SOURCE.md`` there). The independent readings the converters
are checked against are the tools' own summaries: ``sample``'s "Sort by
top of stack" table, and the sample count ``perf record`` printed.
"""

import re
import shutil
import subprocess
import sys
import tracemalloc
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest

from perfproof.profilers import native
from perfproof.profilers._kill import descendants
from perfproof.profilers.folded import summarise_file
from perfproof.profilers.native import (
    SourceIndex,
    fold_perf_script,
    fold_sample_text,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "native"
PROG = FIXTURES / "prog"


def sample_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    with (FIXTURES / "sample.txt").open() as handle:
        fold_sample_text(handle, SourceIndex(PROG), counts)
    return counts


def perf_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    with (FIXTURES / "perf-script.txt").open() as handle:
        fold_perf_script(handle, SourceIndex(PROG), counts)
    return counts


def leaves(counts: dict[str, int]) -> Counter[str]:
    """Samples by the function name the stack ends in."""
    found: Counter[str] = Counter()
    for stack, count in counts.items():
        leaf = stack.rsplit(";", 1)[-1]
        found[re.sub(r"^.*`|\s\(.*\)$", "", leaf)] += count
    return found


def test_sample_self_counts_are_the_ones_sample_prints() -> None:
    """The table ``sample`` ends with counts each function's own."""
    table = (
        (FIXTURES / "sample.txt").read_text().split("Sort by top of stack")[1]
    )
    printed = {
        m["name"]: int(m["count"])
        for m in re.finditer(
            r"^\s+(?P<name>\S.*?)  \(in [^)]*\)\s+(?P<count>\d+)$",
            table,
            re.MULTILINE,
        )
    }
    assert printed["checksum"] > 0

    found = leaves(sample_counts())

    for name, count in printed.items():
        assert found[name] == count, name


def test_sample_frames_name_the_project_file_and_line() -> None:
    counts = sample_counts()

    hot = max(counts, key=lambda s: counts[s])

    assert hot.endswith("checksum (work.c:7)")
    assert ";main (hot.c:25);" in hot


def test_a_frame_without_source_names_its_image() -> None:
    counts = sample_counts()

    assert any(stack.startswith("dyld`") for stack in counts)


def test_perf_total_is_the_samples_perf_recorded() -> None:
    recorded = re.search(
        r"\((\d+) samples\)", (FIXTURES / "perf-record.txt").read_text()
    )
    assert recorded is not None

    assert sum(perf_counts().values()) == int(recorded[1])


def test_perf_stacks_are_root_first_with_the_project_file_and_line() -> None:
    counts = perf_counts()

    hot = max(counts, key=lambda s: counts[s])

    assert hot.startswith("hot-gcc`_start;")
    assert hot.endswith(";main (hot.c:25);checksum (work.c:7)")
    assert "libc.so.6`__libc_start_main" in hot


@pytest.mark.parametrize("which", ["sample", "perf"])
def test_the_summary_ranks_the_checksum_loop_first(
    tmp_path: Path, which: str
) -> None:
    counts = sample_counts() if which == "sample" else perf_counts()
    folded = tmp_path / "out.folded"
    with folded.open("w") as out:
        native._write(counts, out)  # pyright: ignore[reportPrivateUsage]

    top = summarise_file(folded, project_root=PROG)[0]

    assert top.function == "checksum"
    assert top.file == "work.c"
    assert top.line == 7
    assert top.is_library is False
    assert top.self_fraction > 0.5


def test_a_base_name_with_two_files_is_not_located(
    tmp_path: Path,
) -> None:
    (tmp_path / "a" / "deep").mkdir(parents=True)
    (tmp_path / "util.c").write_text("")
    (tmp_path / "a" / "util.c").write_text("")
    (tmp_path / "a" / "deep" / "util.c").write_text("")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "only.c").write_text("")
    index = SourceIndex(tmp_path)

    (tmp_path / "unique.c").write_text("")
    (tmp_path / "a" / "deep" / "single.c").write_text("")

    assert index.locate("util.c") is None
    assert index.locate("unique.c") == "unique.c"
    assert index.locate("single.c") == "a/deep/single.c"
    assert index.locate("only.c") is None
    assert index.locate("missing.c") is None


def test_a_file_name_no_file_has_is_kept_as_printed() -> None:
    text = (
        "Call graph:\n"
        "    5 Thread_1: Main Thread\n"
        "    + 5 main  (in a.out) + 4  [0x1]  elsewhere.c:9\n"
        "\n"
        "Total number in stack (recursive counted multiple, when >=5):\n"
    )
    counts: dict[str, int] = {}

    fold_sample_text(text.splitlines(), SourceIndex(PROG), counts)

    assert counts == {"main (elsewhere.c:9)": 5}


def test_a_demangled_cxx_name_with_parentheses_is_one_frame() -> None:
    text = (
        "Call graph:\n"
        "    3 Thread_1: Main Thread\n"
        "    + 3 foo(int, (anonymous namespace)::Bar)  (in a.out) + 4  "
        "[0x1]  work.c:4\n"
    )
    counts: dict[str, int] = {}

    fold_sample_text(text.splitlines(), SourceIndex(PROG), counts)

    assert counts == {
        "foo(int, (anonymous namespace)::Bar) (work.c:4)": 3,
    }


def test_a_sample_location_with_line_zero_is_not_a_location() -> None:
    text = (
        "Call graph:\n"
        "    5 Thread_1: Main Thread\n"
        "    + 5 f  (in a.out) + 4  [0x1]  work.c:0\n"
    )
    counts: dict[str, int] = {}

    fold_sample_text(text.splitlines(), SourceIndex(PROG), counts)

    assert counts == {"a.out`f": 5}


def synthetic_perf(samples: int) -> Iterator[str]:
    for i in range(samples):
        yield "\n"
        yield "\t  400850 checksum (/b/hot)\n"
        yield "  work.c:7\n"
        yield f"\t  400757 main{i % 3} (/b/hot)\n"
        yield "  hot.c:25\n"


def test_converting_many_samples_uses_bounded_memory() -> None:
    counts: dict[str, int] = {}
    tracemalloc.start()
    try:
        added = fold_perf_script(
            synthetic_perf(200_000), SourceIndex(PROG), counts
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert added == 200_000
    assert len(counts) == 3
    assert peak < 1 << 20


def test_a_missing_tool_is_reported_with_its_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:

    def nothing(name: str) -> None:
        del name

    monkeypatch.setattr(shutil, "which", nothing)
    out = tmp_path / "out.folded"

    code = native.record(["true"], out, tool="perf")

    assert code == 4
    assert "perf was not found" in capsys.readouterr().err
    assert not out.exists()


def test_an_unknown_tool_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="tool must be"):
        native.record(["true"], tmp_path / "o", tool="gprof")


def test_the_whole_process_tree_is_killed_at_the_limit() -> None:
    process = subprocess.Popen(["sh", "-c", "sleep 60 & sleep 60 & wait"])
    try:
        seen: list[int] = []
        for _ in range(100):
            seen = descendants(process.pid) or []
            if len(seen) >= 2:
                break
        assert len(seen) >= 2

        status = native._wait(process, 0.5)  # pyright: ignore[reportPrivateUsage]

        assert status == 124
        assert not set(seen) & set(descendants(1) or [])
    finally:
        process.kill()


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("sample") is None,
    reason="sample is macOS-only",
)
def test_a_real_sample_run_names_the_checksum_loop(tmp_path: Path) -> None:
    clang = shutil.which("clang")
    if clang is None:
        pytest.skip("no clang on the PATH")
    hot = tmp_path / "hot"
    subprocess.run(
        [
            clang,
            "-O1",
            "-g",
            "-fno-omit-frame-pointer",
            "-I",
            str(PROG),
            str(PROG / "hot.c"),
            str(PROG / "work.c"),
            "-o",
            str(hot),
        ],
        check=True,
        capture_output=True,
        timeout=300,
    )
    out = tmp_path / "out.folded"

    code = native.main(
        [
            "record",
            "--output",
            str(out),
            "--root",
            str(PROG),
            "--",
            str(hot),
            "2",
        ]
    )

    assert code == 0
    top = summarise_file(out, project_root=PROG)[0]
    assert (top.function, top.file, top.line) == ("checksum", "work.c", 7)
    assert (tmp_path / "out.folded.sample.txt").is_file()


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("sample") is None,
    reason="sample is macOS-only",
)
def test_a_command_that_cannot_start_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out.folded"

    code = native.main(
        ["record", "--output", str(out), "--", str(tmp_path / "missing")]
    )

    assert code == 5
    assert "could not start" in capsys.readouterr().err
    assert not out.exists()
