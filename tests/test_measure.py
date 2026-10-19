import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from perfproof.cli import main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
DIRECTION_WORDS = re.compile(
    r"\b(faster|slower|speedups?|gains?|wins?|better|worse|regress\w*|"
    r"improve\w*|slows?|benefit|verdict|didn't work|points to|suggests)\b",
    re.IGNORECASE,
)


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        timeout=60,
    )


def cli_json(
    capsys: pytest.CaptureFixture[str], *argv: str
) -> tuple[int, dict[str, Any]]:
    status = main([*argv, "--json"])
    out, _ = capsys.readouterr()
    return status, cast("dict[str, Any]", json.loads(out))


def make_repo(
    tmp_path: Path, example: str, name: str, capsys: pytest.CaptureFixture[str]
) -> Path:
    repo = tmp_path / example
    shutil.copytree(
        EXAMPLES / example,
        repo,
        ignore=shutil.ignore_patterns("candidates", "__pycache__"),
    )
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-qm",
        "i",
    )
    status, document = cli_json(
        capsys,
        "bench",
        "define",
        name,
        "--spec",
        str(repo / "spec.json"),
        "--repo",
        str(repo),
    )
    assert status == 0, document
    return repo


@pytest.fixture(autouse=True)
def benchmark_path(monkeypatch: pytest.MonkeyPatch) -> None:
    path = os.environ.get("PATH", os.defpath)
    monkeypatch.setenv(
        "PATH", f"{Path(sys.executable).parent}{os.pathsep}{path}"
    )


@pytest.fixture
def growth(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    return make_repo(tmp_path, "growth", "pair-count", capsys)


@pytest.fixture
def settings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    return make_repo(tmp_path, "settings", "flush-writer", capsys)


def no_direction(document: object, text: str, repo: Path) -> None:
    shown = json.dumps(document) + "\n" + text
    shown = shown.replace(str(repo), "REPO").replace(
        str(repo.resolve()), "REPO"
    )
    assert DIRECTION_WORDS.search(shown) is None, DIRECTION_WORDS.search(shown)


def check_common(
    result: dict[str, Any], repo: Path, kind: str, claim: str
) -> dict[str, Any]:
    assert (result["kind"], result["claim"]) == (kind, claim)
    assert result["calibrated"] is False
    assert "not a calibrated measurement" in result["label"]
    assert result["label"].startswith(claim)
    machine = result["machine"]
    assert set(machine) == {
        "load_average_start",
        "load_average_end",
        "cpu_count",
    }
    assert machine["cpu_count"] == os.cpu_count()
    assert result["metric"] == {"kind": "reported", "unit": "s"}
    path = Path(result["evidence"]["path"])
    assert path.parent == repo / ".perfproof" / "evidence" / result["name"]
    data = path.read_bytes()
    assert result["evidence"]["sha256"] == hashlib.sha256(data).hexdigest()
    stored = cast("dict[str, Any]", json.loads(data))
    assert stored["claim"] == claim
    assert stored["kind"] == kind
    assert stored["spec_hash"] == result["spec_hash"]
    assert stored["hypothesis"] == result["hypothesis"]
    assert "verdict" not in stored
    return stored


def test_sizes_reports_the_exponent_as_an_estimate(
    growth: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, document = cli_json(
        capsys,
        "measure",
        "pair-count",
        "--sizes",
        "1,2,4",
        "--size-arg=--n=500",
        "--rounds",
        "3",
        "--hypothesis",
        "H1",
        "--seed",
        "4",
        "--repo",
        str(growth),
    )
    assert status == 0, document
    result = document["result"]
    stored = check_common(result, growth, "sizes", "estimate")
    assert result["hypothesis"] == "H1"
    assert result["seed"] == 4
    block = result["sizes"]
    assert block["sizes"] == [500, 1000, 2000]
    assert block["factors"] == [1, 2, 4]
    assert block["knob"] == {"kind": "arg", "name": "--n", "base": 500}
    assert block["measurements"] == 9
    assert 1.6 < block["exponent"] < 2.4
    assert (
        block["interval"]["low"]
        < block["exponent"]
        < block["interval"]["high"]
    )
    assert block["interval"]["level"] == 0.95
    assert len(block["local_exponents"]) == 2
    assert stored["result"]["sizes"] == block
    no_direction(document, "", growth)


def test_sizes_text_names_the_estimate_and_the_machine(
    growth: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = main(
        [
            "measure",
            "pair-count",
            "--sizes",
            "1x,2x,4x",
            "--size-arg=--n=500",
            "--rounds",
            "3",
            "--repo",
            str(growth),
        ]
    )
    text, _ = capsys.readouterr()
    assert status == 0
    assert text.startswith("Growth of the reported metric with --n: ")
    assert "An estimate." in text
    assert re.search(r"Load average [\d.]+ at the start", text)
    assert "not a calibrated measurement" in text
    assert "Stored as estimate evidence: " in text
    no_direction({}, text, growth)


def test_stub_reports_a_cap_as_a_bound(
    growth: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, document = cli_json(
        capsys,
        "measure",
        "pair-count",
        "--stub",
        "pairs:count_pairs",
        "--rounds",
        "3",
        "--repo",
        str(growth),
    )
    assert status == 0, document
    result = document["result"]
    check_common(result, growth, "stub", "bound")
    block = result["stub"]
    assert block["function"] == "pairs:count_pairs"
    assert block["cap"]["value"] > 1
    assert block["cap"]["low"] < block["cap"]["value"] < block["cap"]["high"]
    assert block["cap_uncorrected"]["value"] <= block["cap"]["value"]
    assert block["calls_per_run"] == block["replayed_per_run"] == 1
    assert block["real_per_run"] == 0
    assert block["baseline"]["value"] > block["remaining"]["value"]
    assert "bound" in result["label"]
    no_direction(document, "", growth)


def test_stub_text_calls_the_cap_a_bound_and_not_an_effect(
    growth: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = main(
        [
            "measure",
            "pair-count",
            "--stub",
            "pairs:count_pairs",
            "--rounds",
            "3",
            "--repo",
            str(growth),
        ]
    )
    text, _ = capsys.readouterr()
    assert status == 0
    assert text.startswith("Bound for pairs:count_pairs, replayed")
    assert "a bound and not a measured effect of a change." in text
    assert "Stored as bound evidence" in text
    no_direction({}, text, growth)


def test_set_reports_both_sides_and_their_ratio(
    settings: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, document = cli_json(
        capsys,
        "measure",
        "flush-writer",
        "--set",
        "FLUSH_EVERY=100",
        "--rounds",
        "3",
        "--repo",
        str(settings),
    )
    assert status == 0, document
    result = document["result"]
    check_common(result, settings, "set", "estimate")
    block = result["setting"]
    assert (block["name"], block["value"]) == ("FLUSH_EVERY", "100")
    assert block["declared_in_spec"] is None
    ratio = block["ratio_unchanged_over_changed"]
    assert ratio["value"] > 3
    assert block["unchanged"]["value"] > block["changed"]["value"]
    no_direction(document, "", settings)


def test_a_setting_text_says_the_spec_value_is_unknown(
    settings: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = main(
        [
            "measure",
            "flush-writer",
            "--set",
            "FLUSH_EVERY=100",
            "--rounds",
            "3",
            "--repo",
            str(settings),
        ]
    )
    text, _ = capsys.readouterr()
    assert status == 0
    assert "its value in the spec is unknown to perfproof" in text
    assert "Unchanged over changed: " in text
    no_direction({}, text, settings)


def failure(
    capsys: pytest.CaptureFixture[str], *argv: str
) -> tuple[int, dict[str, Any]]:
    status, document = cli_json(capsys, *argv)
    assert document["ok"] is False
    return status, cast("dict[str, Any]", document["error"])


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--sizes", "1,2"],
        ["--sizes", "1,1,1"],
        ["--sizes", "0,1,2"],
        ["--sizes", "a,b,c"],
        ["--stub", "a:b", "--set", "X=1"],
        ["--stub", "a:b", "--rounds", "0"],
        ["--stub", "a:b", "--timeout", "-1"],
        ["--stub", "a:b", "--seed", "-1"],
        ["--sizes", "1,2,4", "--size-arg=--n=1", "--size-env=N=1"],
    ],
)
def test_bad_arguments_are_usage_errors(
    growth: Path, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    status, error = failure(
        capsys, "measure", "pair-count", *argv, "--repo", str(growth)
    )
    assert status == 2
    assert error["code"] == "usage"


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (["--sizes", "1,2,4"], "invalid_arguments"),
        (["--sizes", "1,2,4", "--size-arg=n=5"], "invalid_arguments"),
        (["--sizes", "1,2,4", "--size-env=1X=5"], "invalid_arguments"),
        (["--set", "NOEQUALS"], "invalid_arguments"),
        (["--set", "PYTHONPATH=/x"], "invalid_arguments"),
        (["--stub", "nocolon"], "invalid_target"),
        (["--stub", "pairs:absent"], "target_not_found"),
        (
            ["--set", "DATABASE_URL=postgresql://db.example.com:5432/x"],
            "side_effects",
        ),
    ],
)
def test_refusals_carry_stable_codes_and_run_nothing_unsafe(
    growth: Path,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    code: str,
) -> None:
    status, error = failure(
        capsys,
        "measure",
        "pair-count",
        *argv,
        "--rounds",
        "3",
        "--repo",
        str(growth),
    )
    assert status == 1
    assert error["code"] == code


def test_an_unknown_benchmark_is_not_found(
    growth: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, error = failure(
        capsys, "measure", "nope", "--stub", "a:b", "--repo", str(growth)
    )
    assert (status, error["code"]) == (1, "not_found")


def test_outside_a_repository_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, error = failure(
        capsys,
        "measure",
        "x",
        "--stub",
        "a:b",
        "--repo",
        str(tmp_path),
    )
    assert (status, error["code"]) == (1, "not_a_repository")


def test_the_state_directory_is_ignored_by_git(
    growth: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, _ = cli_json(
        capsys,
        "measure",
        "pair-count",
        "--stub",
        "pairs:count_pairs",
        "--rounds",
        "3",
        "--repo",
        str(growth),
    )
    assert status == 0
    done = subprocess.run(
        ["git", "-C", str(growth), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert ".perfproof" not in done.stdout


@pytest.mark.parametrize(
    "hypothesis", ["", "-H1", ".h", "H 1", "H/1", "h\u00e9"]
)
@pytest.mark.parametrize("command", ["measure", "profile"])
def test_a_hypothesis_id_the_log_cannot_cite_is_a_usage_error(
    growth: Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
    hypothesis: str,
) -> None:
    extra = ["--stub", "pairs:count_pairs"] if command == "measure" else []
    status, error = failure(
        capsys,
        command,
        "pair-count",
        *extra,
        f"--hypothesis={hypothesis}",
        "--repo",
        str(growth),
    )
    assert status == 2
    assert error["code"] == "usage"
    assert not (growth / ".perfproof" / "evidence").exists()


@pytest.mark.parametrize("hypothesis", ["H1", "h-2.a_b", "0x"])
def test_a_valid_hypothesis_id_is_kept(
    growth: Path, capsys: pytest.CaptureFixture[str], hypothesis: str
) -> None:
    status, document = cli_json(
        capsys,
        "measure",
        "pair-count",
        "--stub",
        "pairs:count_pairs",
        "--rounds",
        "3",
        f"--hypothesis={hypothesis}",
        "--repo",
        str(growth),
    )
    assert status == 0, document
    assert document["result"]["hypothesis"] == hypothesis
