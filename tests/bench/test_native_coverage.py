"""gcov and llvm-cov coverage as lcov.

The fixtures under ``tests/fixtures/native`` are real output of gcov and
llvm-cov on the fixture program (``SOURCE.md`` there). The independent
readings the converter is checked against are the tools' own text
reports: ``gcov``'s ``.gcov`` file and ``llvm-cov show``.
"""

import io
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from perfproof.bench import native_coverage
from perfproof.bench.native_coverage import (
    CoverageError,
    add_gcov_json,
    add_lcov,
    main,
    write_lcov,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "native"
BUILD = Path("/build")


def gcov_counts() -> native_coverage.Counts:
    counts: native_coverage.Counts = {}
    for document in (FIXTURES / "gcov.json").read_text().splitlines():
        add_gcov_json(document, BUILD, counts)
    return counts


def printed_by_gcov() -> dict[int, int]:
    """Line counts as ``gcov`` wrote them in ``work.c.gcov``."""
    found: dict[int, int] = {}
    for row in (FIXTURES / "work.c.gcov").read_text().splitlines():
        match = re.match(r"\s*(?P<count>\d+|#####|-):\s*(?P<line>\d+):", row)
        assert match is not None, row
        if match["line"] == "0" or match["count"] == "-":
            continue
        found[int(match["line"])] = (
            0 if match["count"] == "#####" else int(match["count"])
        )
    return found


def printed_by_llvm_show(name: str) -> dict[int, int]:
    """Line counts as ``llvm-cov show`` wrote them for one file."""
    text = (FIXTURES / "llvm-mac-show.txt").read_text()
    block = next(
        b for b in text.split("\n\n") if b.splitlines()[0].endswith(name + ":")
    )
    found: dict[int, int] = {}
    for row in block.splitlines()[1:]:
        match = re.match(r"\s*(?P<line>\d+)\|\s*(?P<count>\d+)\|", row)
        if match is not None:
            found[int(match["line"])] = int(match["count"])
    return found


def test_gcov_counts_are_the_ones_gcov_prints() -> None:
    counts = gcov_counts()

    assert set(counts) == {"prog/check.c", "prog/work.c"}
    assert counts["prog/work.c"] == printed_by_gcov()


def test_llvm_counts_are_the_ones_llvm_cov_show_prints() -> None:
    first = next(
        line
        for line in (FIXTURES / "llvm-mac.lcov").read_text().splitlines()
        if line.startswith("SF:")
    )
    root = Path(first[3:]).parent.parent
    counts: native_coverage.Counts = {}
    with (FIXTURES / "llvm-mac.lcov").open() as handle:
        add_lcov(handle, root.resolve(), counts)

    assert set(counts) == {"prog/check.c", "prog/work.c"}
    for name in ("check.c", "work.c"):
        shown = printed_by_llvm_show(name)
        assert {
            k: v for k, v in counts[f"prog/{name}"].items() if k in shown
        } == shown


def test_lines_of_a_function_no_test_ran_are_zero() -> None:
    counts = gcov_counts()["prog/work.c"]

    never = [n for n in range(27, 33) if n in counts]
    assert never
    assert all(counts[n] == 0 for n in never)
    assert counts[7] > 0


def test_files_outside_the_root_are_left_out() -> None:
    counts: native_coverage.Counts = {}
    for document in (FIXTURES / "gcov.json").read_text().splitlines():
        add_gcov_json(document, Path("/somewhere/else"), counts)

    assert counts == {}


def test_a_line_in_two_objects_is_the_sum() -> None:
    counts: native_coverage.Counts = {}
    document = (FIXTURES / "gcov.json").read_text().splitlines()[1]

    add_gcov_json(document, BUILD, counts)
    add_gcov_json(document, BUILD, counts)

    once: native_coverage.Counts = {}
    add_gcov_json(document, BUILD, once)
    assert counts["prog/work.c"][7] == 2 * once["prog/work.c"][7]


def test_the_report_is_lcov() -> None:
    out = io.StringIO()

    summary = write_lcov(gcov_counts(), out)

    text = out.getvalue()
    assert text.startswith("SF:prog/check.c\nDA:7,1\n")
    assert text.count("end_of_record\n") == summary.files == 2
    work = text.split("SF:prog/work.c\n")[1]
    assert "DA:27,0\n" in work
    printed = printed_by_gcov()
    assert f"LF:{len(printed)}\n" in work
    assert f"LH:{sum(1 for c in printed.values() if c)}\n" in work
    assert summary.lines == sum(len(v) for v in gcov_counts().values())
    assert summary.covered == sum(
        1 for v in gcov_counts().values() for c in v.values() if c
    )


@pytest.mark.parametrize(
    "document",
    ["", "[]", "{}", '{"files": [], "current_working_directory": 3}'],
)
def test_a_document_that_is_not_gcov_json_is_refused(document: str) -> None:
    with pytest.raises(CoverageError):
        add_gcov_json(document, BUILD, {})


def test_a_malformed_da_record_is_refused() -> None:
    with pytest.raises(CoverageError, match="line 2"):
        add_lcov(["SF:/r/a.c", "DA:x,1"], Path("/r"), {})


def test_no_profile_files_is_reported_not_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "report.lcov"

    code = main(
        ["gcov", "--root", str(tmp_path), "--output", str(out), str(tmp_path)]
    )

    assert code == 2
    assert "no .gcda files" in capsys.readouterr().err
    assert not out.exists()


def clang() -> str:
    found = shutil.which("clang")
    if found is None:
        pytest.skip("no clang on the PATH")
    return found


@pytest.mark.integration
def test_a_real_gcc_build_gives_the_lines_the_tests_ran(
    tmp_path: Path,
) -> None:
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("no gcc on the PATH")
    banner = subprocess.run(
        [gcc, "--version"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout
    if "clang" in banner or "Apple" in banner:
        pytest.skip("gcc here is clang, which has no gcov JSON output")
    src = tmp_path / "src"
    shutil.copytree(FIXTURES / "prog", src)
    check = tmp_path / "check"
    subprocess.run(
        [
            gcc,
            "-O0",
            "--coverage",
            "-I",
            str(src),
            str(src / "check.c"),
            str(src / "work.c"),
            "-o",
            str(check),
        ],
        check=True,
        capture_output=True,
        timeout=300,
        cwd=tmp_path,
    )
    subprocess.run(
        [str(check)], check=True, capture_output=True, timeout=60, cwd=tmp_path
    )
    out = tmp_path / "report.lcov"

    code = main(
        ["gcov", "--root", str(tmp_path), "--output", str(out), str(tmp_path)]
    )

    assert code == 0
    records = out.read_text().split("end_of_record\n")
    work = next(r for r in records if r.startswith("SF:src/work.c\n"))
    counts = {
        int(a): int(b)
        for a, b in (
            row[3:].split(",") for row in work.splitlines() if row[:3] == "DA:"
        )
    }
    assert counts[7] > 0
    assert all(counts[n] == 0 for n in range(27, 31))


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("xcrun") is None,
    reason="llvm-cov is found through xcrun here",
)
def test_a_real_llvm_build_gives_the_lines_the_tests_ran(
    tmp_path: Path,
) -> None:
    src = tmp_path / "src"
    shutil.copytree(FIXTURES / "prog", src)
    check = tmp_path / "check"
    subprocess.run(
        [
            clang(),
            "-O0",
            "-fprofile-instr-generate",
            "-fcoverage-mapping",
            "-I",
            str(src),
            str(src / "check.c"),
            str(src / "work.c"),
            "-o",
            str(check),
        ],
        check=True,
        capture_output=True,
        timeout=300,
    )
    subprocess.run(
        [str(check)],
        check=True,
        capture_output=True,
        timeout=60,
        env={"LLVM_PROFILE_FILE": str(tmp_path / "check.profraw")},
    )
    out = tmp_path / "report.lcov"

    code = main(
        [
            "llvm",
            "--root",
            str(tmp_path),
            "--output",
            str(out),
            "--object",
            str(check),
            str(tmp_path),
        ]
    )

    assert code == 0
    records = out.read_text().split("end_of_record\n")
    work = next(r for r in records if r.startswith("SF:src/work.c\n"))
    counts = {
        int(a): int(b)
        for a, b in (
            row[3:].split(",") for row in work.splitlines() if row[:3] == "DA:"
        )
    }
    assert counts[7] > 0
    assert all(counts[n] == 0 for n in range(27, 31))
    assert not any(r.startswith("SF:/") for r in records if r)
