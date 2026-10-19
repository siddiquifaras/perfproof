import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast

import pytest

from perfproof.bench.evidence import (
    SCHEMA_VERSION,
    Claim,
    write_evidence,
)

DIRECTION_WORDS = re.compile(
    r"\b(faster|slower|speedups?|gains?|wins?|better|worse|regress\w*|"
    r"improve\w*|slows?|benefit|verdict|keep|reject)\b",
    re.IGNORECASE,
)


def write(
    root: Path, claim: Claim = "estimate", **extra: Any
) -> tuple[Path, str]:
    ref = write_evidence(
        root,
        "bench",
        "stub",
        claim,
        {"cap": 3.5, "note": "a bound"},
        hypothesis=extra.get("hypothesis"),
        spec_hash="ab" * 32,
    )
    return ref.path, ref.sha256


def test_the_record_holds_the_result_and_what_kind_of_number_it_is(
    tmp_path: Path,
) -> None:
    path, _ = write(tmp_path, "bound", hypothesis="H2")
    record = cast("dict[str, Any]", json.loads(path.read_text()))
    assert record["schema_version"] == SCHEMA_VERSION
    assert record["claim"] == "bound"
    assert record["calibrated"] is False
    assert record["hypothesis"] == "H2"
    assert record["workload"] == "bench"
    assert record["spec_hash"] == "ab" * 32
    assert record["result"] == {"cap": 3.5, "note": "a bound"}
    assert re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", record["recorded_utc"]
    )


def test_the_returned_hash_is_the_files(tmp_path: Path) -> None:
    path, sha = write(tmp_path)
    assert sha == hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_record_lives_under_the_workloads_evidence_directory(
    tmp_path: Path,
) -> None:
    path, _ = write(tmp_path)
    assert path.parent == tmp_path / ".perfproof" / "evidence" / "bench"
    assert re.fullmatch(r"\d{8}T\d{6}Z-stub-[0-9a-f]{6}\.json", path.name)


def test_two_records_never_overwrite_each_other(tmp_path: Path) -> None:
    paths = {write(tmp_path)[0] for _ in range(5)}
    assert len(paths) == 5


def test_no_temporary_file_is_left_behind(tmp_path: Path) -> None:
    path, _ = write(tmp_path)
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_a_hypothesis_may_be_absent(tmp_path: Path) -> None:
    path, _ = write(tmp_path)
    assert json.loads(path.read_text())["hypothesis"] is None


def test_a_record_has_no_verdict_and_no_direction(tmp_path: Path) -> None:
    path, _ = write(tmp_path)
    assert DIRECTION_WORDS.search(path.read_text()) is None


@pytest.mark.parametrize("claim", ["verdict", "speedup", "keep", ""])
def test_only_a_bound_or_an_estimate_can_be_claimed(
    tmp_path: Path, claim: str
) -> None:
    with pytest.raises(ValueError, match="claim must be one of"):
        write(tmp_path, cast("Claim", claim))
    assert not (tmp_path / ".perfproof").exists()


def test_a_result_that_is_not_json_is_refused_without_a_file(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="not JSON compliant"):
        write_evidence(
            tmp_path,
            "bench",
            "set",
            "estimate",
            {"x": float("nan")},
            hypothesis=None,
            spec_hash="00" * 32,
        )
    directory = tmp_path / ".perfproof" / "evidence" / "bench"
    assert not directory.exists() or not list(directory.iterdir())
