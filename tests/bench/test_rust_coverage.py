"""Rust coverage as lcov: filtering, and the real LLVM tools."""

import shutil
import subprocess
from pathlib import Path

import pytest

from perfproof.bench.rust_coverage import (
    filter_lcov,
    llvm_tools,
    main,
    record,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "rust"
EXPORT = FIXTURES / "llvm-cov-export.lcov"
SOURCE = FIXTURES / "coverage-main.rs"


def source_line(text: str) -> int:
    """The 1-based line of the fixture program that starts with text."""
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    return next(i for i, line in enumerate(lines, 1) if line.startswith(text))


def test_the_projects_file_keeps_its_counts_with_a_relative_path() -> None:
    kept = list(
        filter_lcov(
            EXPORT.read_text(encoding="utf-8").splitlines(), Path("/work/cov")
        )
    )

    assert kept[0] == "SF:main.rs"
    assert kept[-1] == "end_of_record"
    assert kept[1:] == EXPORT.read_text(encoding="utf-8").splitlines()[1:]
    never = source_line("fn never_called")
    covered = source_line("fn covered")
    assert f"DA:{never},0" in kept
    assert f"DA:{covered},1" in kept


def test_files_outside_the_project_are_dropped() -> None:
    kept = list(
        filter_lcov(
            EXPORT.read_text(encoding="utf-8").splitlines(), Path("/elsewhere")
        )
    )

    assert kept == []


def test_only_the_records_under_the_root_survive_a_mixed_report() -> None:
    report = [
        "SF:/repo/src/a.rs",
        "DA:1,1",
        "end_of_record",
        "SF:/home/u/.cargo/registry/src/x-1.0/src/lib.rs",
        "DA:5,3",
        "end_of_record",
        "SF:/rustc/abc/library/core/src/option.rs",
        "DA:9,2",
        "end_of_record",
        "SF:/repo/crates/b/src/b.rs",
        "DA:2,0",
        "end_of_record",
    ]

    kept = list(filter_lcov(report, Path("/repo")))

    assert kept == [
        "SF:src/a.rs",
        "DA:1,1",
        "end_of_record",
        "SF:crates/b/src/b.rs",
        "DA:2,0",
        "end_of_record",
    ]


def _not_found(command: str, **options: object) -> None:
    return None


def test_missing_llvm_tools_are_named(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(shutil, "which", _not_found)

    status = record(["x"], tmp_path / "out.lcov", project_root=tmp_path)

    assert status == 4
    assert "rustup component add llvm-tools-preview" in capsys.readouterr().err


def test_an_existing_output_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "out.lcov").write_text("")

    status = record(["x"], tmp_path / "out.lcov")

    assert status == 7
    assert "exists" in capsys.readouterr().err


def test_main_needs_a_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--output", "x"])

    assert raised.value.code == 2
    assert "give the command after --" in capsys.readouterr().err


def llvm_tools_present() -> bool:
    return llvm_tools(Path.cwd()) is not None


@pytest.fixture
def instrumented(tmp_path: Path) -> Path:
    """The fixture program built with ``-C instrument-coverage``."""
    if shutil.which("rustc") is None or not llvm_tools_present():
        pytest.skip("rustc with llvm-tools-preview is needed")
    source = tmp_path / "main.rs"
    shutil.copy(SOURCE, source)
    binary = tmp_path / "prog"
    subprocess.run(
        ["rustc", "-C", "instrument-coverage", "-o", str(binary), str(source)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    return binary


@pytest.mark.integration
def test_the_real_tools_mark_the_line_nothing_ran(
    instrumented: Path, tmp_path: Path
) -> None:
    output = tmp_path / "out.lcov"

    status = record([str(instrumented)], output, project_root=tmp_path)

    assert status == 0
    lines = output.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "SF:main.rs"
    assert f"DA:{source_line('fn never_called')},0" in lines
    assert f"DA:{source_line('fn covered')},1" in lines


@pytest.mark.integration
def test_a_binary_without_instrumentation_writes_no_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    if shutil.which("rustc") is None or not llvm_tools_present():
        pytest.skip("rustc with llvm-tools-preview is needed")
    source = tmp_path / "plain.rs"
    source.write_text("fn main() {}\n")
    binary = tmp_path / "plain"
    subprocess.run(
        ["rustc", "-o", str(binary), str(source)],
        check=True,
        capture_output=True,
        timeout=300,
    )

    status = record(
        [str(binary)], tmp_path / "out.lcov", project_root=tmp_path
    )

    assert status == 5
    assert "-C instrument-coverage" in capsys.readouterr().err
    assert not (tmp_path / "out.lcov").exists()


@pytest.mark.integration
def test_a_timeout_kills_the_command(
    instrumented: Path, tmp_path: Path
) -> None:
    marker = "47141"

    status = record(
        ["sh", "-c", f"sleep {marker} & wait"],
        tmp_path / "out.lcov",
        project_root=tmp_path,
        timeout_seconds=1,
    )

    listing = subprocess.run(
        ["ps", "-A", "-o", "args="],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout
    assert status == 124
    assert f"sleep {marker}" not in listing
