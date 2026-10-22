"""Go coverage profiles as lcov, from captures of the real tools.

``cover-set.out`` and ``cover-count.out`` are ``go test -coverprofile``
output, ``covdata.txt`` is ``go tool covdata textfmt`` of a program
built with ``go build -cover``, and ``cover-func.txt`` is ``go tool
cover -func`` of the first: the independent reading the converter's
block counts are checked against (``tests/fixtures/go/SOURCE.md``).
"""

import io
import re
import shutil
from pathlib import Path

import pytest

from perfproof.bench.go_coverage import CoverageError, convert, main

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "go"
PROG = FIXTURES / "prog"

_BLOCK = re.compile(
    r".+:(?P<sl>\d+)\.\d+,\d+\.\d+ (?P<stmts>\d+) (?P<count>\d+)"
)
_FUNC = re.compile(r".+\.go:(?P<line>\d+):\s+(?P<name>\w+)\s+(?P<pct>[\d.]+)%")


def lcov(profile: str, root: Path = PROG) -> dict[str, dict[int, int]]:
    """Convert and read the report back: file to line to count."""
    out = io.StringIO()
    convert(FIXTURES / profile, root, out)
    files: dict[str, dict[int, int]] = {}
    current = ""
    for row in out.getvalue().splitlines():
        if row.startswith("SF:"):
            current = row[3:]
            files[current] = {}
        elif row.startswith("DA:"):
            line, count = row[3:].split(",")
            files[current][int(line)] = int(count)
    return files


def test_block_counts_agree_with_go_tool_cover_per_function() -> None:
    """Statements run, over statements, in each function's lines."""
    starts = [
        (int(m["line"]), m["name"], float(m["pct"]))
        for row in (FIXTURES / "cover-func.txt").read_text().splitlines()
        if (m := _FUNC.fullmatch(row))
    ]
    assert len(starts) == 7
    blocks = [
        (int(m["sl"]), int(m["stmts"]), int(m["count"]))
        for row in (FIXTURES / "cover-set.out").read_text().splitlines()[1:]
        if (m := _BLOCK.fullmatch(row))
    ]
    for i, (start, name, percent) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else 10**9
        mine = [(n, c) for sl, n, c in blocks if start <= sl < end]
        ran = sum(n for n, c in mine if c > 0)
        total = sum(n for n, _ in mine)
        assert f"{100 * ran / total:.1f}" == f"{percent:.1f}", name


def test_the_report_names_the_file_and_the_lines_that_ran() -> None:
    report = lcov("cover-set.out")
    assert set(report) == {"work.go"}
    lines = report["work.go"]
    # Join, Count and Sign's first and last statements ran; Words,
    # Contend and Unused never did.
    assert {lines[n] for n in (14, 15, 16, 17, 19, 21)} == {1}
    assert {lines[n] for n in (26, 27, 28, 30)} == {1}
    assert {lines[n] for n in (35, 36, 37, 39)} == {0}
    assert {lines[n] for n in range(45, 58) if n in lines} == {0}
    assert lines[78] == 0


def test_a_line_whose_body_never_ran_is_not_covered_by_its_condition() -> None:
    """``if n < 0 {`` ran; ``return -1`` on the next line did not."""
    lines = lcov("cover-set.out")["work.go"]
    assert (lines[67], lines[68]) == (1, 0)
    assert (lines[70], lines[71]) == (1, 0)
    assert lines[73] == 1


def test_a_one_line_if_with_an_unreached_body_is_not_covered() -> None:
    """``if n < 0 { return 0 }`` is two blocks on one line."""
    source = (PROG / "work.go").read_text().splitlines()
    clamp = next(
        i for i, row in enumerate(source, 1) if "if n < 0 { return 0 }" in row
    )
    lines = lcov("cover-set.out")["work.go"]
    assert lines[clamp] == 0
    assert lines[clamp + 1] == 1


def test_lines_with_no_statement_have_no_record() -> None:
    lines = lcov("cover-set.out")["work.go"]
    source = (PROG / "work.go").read_text().splitlines()
    for number, row in enumerate(source, 1):
        stripped = row.strip()
        if stripped in ("}", "") or stripped.startswith("//"):
            assert number not in lines, (number, row)
        if stripped.startswith(("package ", "import")):
            assert number not in lines


def test_the_totals_in_the_report_match_its_records() -> None:
    out = io.StringIO()
    summary = convert(FIXTURES / "cover-set.out", PROG, out)
    report = lcov("cover-set.out")["work.go"]
    assert summary.files == 1
    assert summary.lines == len(report)
    assert summary.covered == sum(1 for c in report.values() if c > 0)
    assert f"LF:{summary.lines}\nLH:{summary.covered}\n" in out.getvalue()
    assert summary.skipped == ()


def test_count_mode_gives_the_same_covered_lines() -> None:
    sets = lcov("cover-set.out")["work.go"]
    counts = lcov("cover-count.out")["work.go"]
    assert sets.keys() == counts.keys()
    assert {n for n, c in sets.items() if c} == {
        n for n, c in counts.items() if c
    }
    assert max(counts.values()) > 1


def test_a_program_built_with_cover_gives_lines_of_its_files() -> None:
    report = lcov("covdata.txt")
    assert "cmd/hot/main.go" in report
    assert set(report["cmd/hot/main.go"].values()) == {1}
    helper = report["cmd/hot/profile.go"]
    assert 0 in helper.values()
    assert 1 in helper.values()


def test_a_file_outside_the_module_is_skipped_and_named(
    tmp_path: Path,
) -> None:
    (tmp_path / "go.mod").write_text("module other.example/x\n")
    out = io.StringIO()
    summary = convert(FIXTURES / "cover-set.out", tmp_path, out)
    assert out.getvalue() == ""
    assert summary.files == 0
    assert summary.skipped == ("example.com/prog/work.go",)


def test_without_the_source_every_line_of_a_block_is_charged(
    tmp_path: Path,
) -> None:
    """With only ``go.mod`` here, a brace line cannot be told apart."""
    shutil.copy(PROG / "go.mod", tmp_path / "go.mod")
    lines = lcov("cover-set.out", root=tmp_path)["work.go"]
    # The body block of ``if n < 0 {`` starts on that line.
    assert lines[67] == 0
    assert 18 in lines


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "line 1"),
        ("not a mode line\n", "line 1"),
        ("mode: weird\n", "line 1"),
        ("mode: set\nexample.com/prog/work.go:1.1,2.2 x 1\n", "line 2"),
        ("mode: set\nexample.com/prog/work.go:1.1,2.2 1\n", "line 2"),
    ],
)
def test_a_bad_profile_is_refused_with_its_line(
    tmp_path: Path, text: str, message: str
) -> None:
    bad = tmp_path / "bad.out"
    bad.write_text(text)
    with pytest.raises(CoverageError, match=message):
        convert(bad, PROG, io.StringIO())


def test_a_root_with_no_module_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CoverageError, match="no module path"):
        convert(FIXTURES / "cover-set.out", tmp_path, io.StringIO())


def test_the_command_writes_the_report(tmp_path: Path) -> None:
    out = tmp_path / "lcov.info"
    code = main(
        [
            "--profile",
            str(FIXTURES / "cover-set.out"),
            "--root",
            str(PROG),
            "--output",
            str(out),
        ]
    )
    assert code == 0
    text = out.read_text()
    assert text.startswith("TN:\nSF:work.go\nDA:14,1\n")
    assert text.endswith("end_of_record\n")


def test_the_command_says_why_it_failed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "--profile",
            str(tmp_path / "missing.out"),
            "--root",
            str(PROG),
            "--output",
            str(tmp_path / "x"),
        ]
    )
    assert code == 6
    assert "perfproof.bench.go_coverage:" in capsys.readouterr().err


def test_blank_and_comment_lines_inside_a_block_get_no_record(
    tmp_path: Path,
) -> None:
    """A block's range spans them; Go does not count them."""
    (tmp_path / "go.mod").write_text("module example.com/c\n")
    (tmp_path / "f.go").write_text(
        "package c\n"  # 1
        "\n"  # 2
        "func F(x int) int {\n"  # 3
        "\t// a comment\n"  # 4
        "\n"  # 5
        "\ty := x + 1\n"  # 6
        "\tif y > 1 {\n"  # 7
        "\t\treturn 0\n"  # 8
        "\t}\n"  # 9
        "\treturn y\n"  # 10
        "}\n"  # 11
    )
    profile = tmp_path / "cover.out"
    profile.write_text(
        "mode: set\n"
        "example.com/c/f.go:3.19,7.11 2 1\n"
        "example.com/c/f.go:7.11,9.3 1 0\n"
        "example.com/c/f.go:10.2,10.10 1 1\n"
    )
    out = io.StringIO()
    convert(profile, tmp_path, out)
    rows = [r for r in out.getvalue().splitlines() if r.startswith("DA:")]
    assert rows == ["DA:6,1", "DA:7,1", "DA:8,0", "DA:10,1"]
