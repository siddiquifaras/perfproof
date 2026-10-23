from pathlib import Path

import pytest

from perfproof.bench import coverage_report
from perfproof.bench.coverage_report import ReportError, read_report

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "coverage-py"
RECORDED_SOURCE = next(
    line.split("<source>")[1].split("</source>")[0]
    for line in (FIXTURES / "cobertura.xml").read_text().splitlines()
    if "<source>" in line
)
CALC = {1: 1, 2: 1, 5: 1, 6: 0}
TEST_CALC = {1: 1, 4: 1, 5: 1}


def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    root.mkdir()
    (root / "calc.py").write_text("")
    (root / "test_calc.py").write_text("")
    return root


def test_a_real_lcov_report_from_coverage_py(tmp_path: Path) -> None:
    root = tree(tmp_path)
    report = read_report(FIXTURES / "lcov.info", root)
    assert report == {"calc.py": CALC, "test_calc.py": TEST_CALC}


def test_a_real_cobertura_report_from_coverage_py(tmp_path: Path) -> None:
    root = tree(tmp_path)
    text = (FIXTURES / "cobertura.xml").read_text(encoding="utf-8")
    assert RECORDED_SOURCE in text
    path = tmp_path / "cobertura.xml"
    path.write_text(text.replace(RECORDED_SOURCE, str(root)))
    assert read_report(path, root) == {
        "calc.py": CALC,
        "test_calc.py": TEST_CALC,
    }


def test_both_formats_agree_on_the_same_run(tmp_path: Path) -> None:
    root = tree(tmp_path)
    text = (FIXTURES / "cobertura.xml").read_text(encoding="utf-8")
    path = tmp_path / "cobertura.xml"
    path.write_text(text.replace(RECORDED_SOURCE, str(root)))
    assert read_report(path, root) == read_report(FIXTURES / "lcov.info", root)


def test_a_missing_report_is_none(tmp_path: Path) -> None:
    assert read_report(tmp_path / "absent", tree(tmp_path)) is None


def test_relative_paths_are_relative_to_the_command_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "tree"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "calc.py").write_text("")
    report = tmp_path / "r.info"
    report.write_text("SF:calc.py\nDA:3,2\nend_of_record\n")
    assert read_report(report, root, "sub") == {"sub/calc.py": {3: 2}}


def test_absolute_paths_inside_the_tree_match_and_outside_are_ignored(
    tmp_path: Path,
) -> None:
    root = tree(tmp_path)
    outside = tmp_path / "elsewhere.py"
    outside.write_text("")
    report = tmp_path / "r.info"
    report.write_text(
        f"SF:{root / 'calc.py'}\nDA:1,1\nend_of_record\n"
        f"SF:{outside}\nDA:1,0\nend_of_record\n"
        "SF:../elsewhere.py\nDA:1,0\nend_of_record\n"
    )
    assert read_report(report, root) == {"calc.py": {1: 1}}


def test_a_listed_file_with_no_executable_line_is_still_listed(
    tmp_path: Path,
) -> None:
    root = tree(tmp_path)
    report = tmp_path / "r.info"
    report.write_text("SF:calc.py\nend_of_record\n")
    assert read_report(report, root) == {"calc.py": {}}


def test_a_line_listed_twice_keeps_the_larger_count(tmp_path: Path) -> None:
    root = tree(tmp_path)
    report = tmp_path / "r.info"
    report.write_text(
        "SF:calc.py\nDA:1,0\nend_of_record\nSF:calc.py\nDA:1,3,abc\n"
        "end_of_record\n"
    )
    assert read_report(report, root) == {"calc.py": {1: 3}}


def test_cobertura_sources_are_tried_before_the_command_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "tree"
    (root / "src").mkdir(parents=True)
    (root / "src" / "calc.py").write_text("")
    report = tmp_path / "c.xml"
    report.write_text(
        f"<coverage><sources><source>{root / 'src'}</source></sources>"
        '<packages><package><classes><class filename="calc.py"><lines>'
        '<line number="2" hits="0"/></lines></class></classes></package>'
        "</packages></coverage>"
    )
    assert read_report(report, root) == {"src/calc.py": {2: 0}}


@pytest.mark.parametrize(
    "text",
    [
        "SF:calc.py\nDA:1\nend_of_record\n",
        "SF:calc.py\nDA:x,1\nend_of_record\n",
        "SF:calc.py\nDA:1,-1\nend_of_record\n",
        "SF:calc.py\nDA:0,1\nend_of_record\n",
        "DA:1,1\n",
        "<coverage",
        '<coverage><class filename="calc.py"><lines><line number="1"/>'
        "</lines></class></coverage>",
        '<coverage><class><lines><line number="1" hits="1"/></lines>'
        "</class></coverage>",
        '<!DOCTYPE c [<!ENTITY x "y">]><coverage/>',
    ],
)
def test_a_malformed_report_is_refused(tmp_path: Path, text: str) -> None:
    report = tmp_path / "r"
    report.write_text(text)
    with pytest.raises(ReportError):
        read_report(report, tree(tmp_path))


def test_a_report_that_is_not_utf8_is_refused(tmp_path: Path) -> None:
    report = tmp_path / "r"
    report.write_bytes(b"SF:calc.py\n\xff\xfe\n")
    with pytest.raises(ReportError, match="UTF-8"):
        read_report(report, tree(tmp_path))


def test_a_report_over_the_size_bound_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(coverage_report, "MAX_REPORT_BYTES", 10)
    report = tmp_path / "r"
    report.write_text("SF:calc.py\nDA:1,1\nend_of_record\n")
    with pytest.raises(ReportError, match="larger than"):
        read_report(report, tree(tmp_path))


def test_a_nul_byte_in_a_path_is_a_report_error(tmp_path: Path) -> None:
    report = tmp_path / "r.info"
    report.write_bytes(b"SF:a\x00b.c\nDA:1,1\nend_of_record\n")
    with pytest.raises(ReportError, match="NUL"):
        read_report(report, tree(tmp_path))
