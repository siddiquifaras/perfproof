"""SimpleCov results to lcov, checked against simplecov-lcov itself.

``tests/fixtures/ruby/resultset.json`` is the ``.resultset.json`` of a
real SimpleCov 1.3.2 run of the fixture program's Minitest suite, and
``simplecov.lcov`` is the lcov report the simplecov-lcov gem wrote from
the same run (``tests/fixtures/ruby/SOURCE.md``). The converter is
correct where it gives the same per-line counts as that gem.
"""

import json
from pathlib import Path

import pytest

from perfproof.bench import ruby_coverage
from perfproof.bench.ruby_coverage import CoverageError, convert

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ruby"
RESULT = FIXTURES / "resultset.json"
REFERENCE = FIXTURES / "simplecov.lcov"


def parse(text: str) -> dict[str, dict[int, int]]:
    """Per file, per line, the count of an lcov report's DA records."""
    files: dict[str, dict[int, int]] = {}
    current: dict[int, int] = {}
    for row in text.splitlines():
        if row.startswith("SF:"):
            current = files.setdefault(row[3:].removeprefix("./"), {})
        elif row.startswith("DA:"):
            line, count = row[3:].split(",")[:2]
            current[int(line)] = int(count)
    return files


def test_the_counts_match_simplecov_lcov() -> None:
    mine = parse(convert(RESULT, Path("/src")))

    assert mine == parse(REFERENCE.read_text())
    assert mine["lib/work.rb"][28] == 0
    assert mine["lib/work.rb"][17] == 99


def test_the_summary_lines_agree_with_the_records() -> None:
    text = convert(RESULT, Path("/src"))
    rows = [row for row in text.splitlines() if row.startswith("DA:")]
    assert f"LF:{len(rows)}" in text.splitlines()
    covered = sum(1 for row in rows if not row.endswith(",0"))
    assert f"LH:{covered}" in text.splitlines()
    assert text.endswith("end_of_record\n")


def test_paths_outside_the_root_are_kept_as_written() -> None:
    assert "SF:/src/lib/work.rb" in convert(RESULT, Path("/elsewhere"))
    assert "SF:/src/lib/work.rb" in convert(RESULT)


def write(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data))
    return path


def test_suites_are_merged_by_adding_counts(tmp_path: Path) -> None:
    result = write(
        tmp_path / "r.json",
        {
            "a": {"coverage": {"/p/x.rb": {"lines": [None, 1, 0, 0]}}},
            "b": {"coverage": {"/p/x.rb": {"lines": [None, 2, 0, 3]}}},
        },
    )
    assert parse(convert(result, Path("/p"))) == {"x.rb": {2: 3, 3: 0, 4: 3}}


def test_the_older_bare_list_form_is_read(tmp_path: Path) -> None:
    result = write(
        tmp_path / "r.json", {"a": {"coverage": {"/p/x.rb": [1, None, 0]}}}
    )
    assert parse(convert(result, Path("/p"))) == {"x.rb": {1: 1, 3: 0}}


def test_a_result_with_no_files_is_an_empty_report(tmp_path: Path) -> None:
    assert convert(write(tmp_path / "r.json", {}), None) == ""


@pytest.mark.parametrize(
    "bad",
    [
        [],
        {"a": []},
        {"a": {"coverage": []}},
        {"a": {"coverage": {"/p/x.rb": "no"}}},
        {"a": {"coverage": {"/p/x.rb": {"lines": [1, -1]}}}},
        {"a": {"coverage": {"/p/x.rb": {"lines": [1, True]}}}},
        {"a": {"coverage": {"/p/x.rb": {"lines": [1.5]}}}},
        {
            "a": {"coverage": {"/p/x.rb": [1]}},
            "b": {"coverage": {"/p/x.rb": [1, 2]}},
        },
    ],
)
def test_a_result_of_the_wrong_shape_is_refused(
    tmp_path: Path, bad: object
) -> None:
    with pytest.raises(CoverageError):
        convert(write(tmp_path / "r.json", bad))


def test_text_that_is_not_json_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text("{not json")
    with pytest.raises(CoverageError, match="not JSON"):
        convert(path)


def test_a_result_over_the_size_limit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ruby_coverage, "MAX_RESULT_BYTES", 10)
    with pytest.raises(CoverageError, match="over"):
        convert(write(tmp_path / "r.json", {"a": {"coverage": {}}}))


def test_the_command_line_writes_the_report(tmp_path: Path) -> None:
    out = tmp_path / "lcov.info"
    status = ruby_coverage.main(
        [str(RESULT), "--output", str(out), "--root", "/src"]
    )
    assert status == 0
    assert parse(out.read_text()) == parse(REFERENCE.read_text())


def test_the_command_line_reports_an_unreadable_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = ruby_coverage.main(
        [str(tmp_path / "missing.json"), "--output", str(tmp_path / "o")]
    )
    assert status == 2
    assert "missing.json" in capsys.readouterr().err
