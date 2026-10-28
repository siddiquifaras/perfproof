"""A real program with a planted quadratic, profiled at three sizes.

These run the real profiler (pyinstrument through uv) on a small
program, so they carry the ``integration`` marker and skip when uv is
missing. Nothing here is hand-written profiler output.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from perfproof.cli import main

WORK = '''\
"""A linear function, a quadratic one and the entry point."""

import argparse


def linear(n):
    total = 0
    for i in range(n * 4000):
        total += i % 7
    return total


def quadratic(n):
    total = 0
    for i in range(n):
        for j in range(n):
            total += (i ^ j) & 3
    return total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=1000)
    n = parser.parse_args().n
    linear(n)
    quadratic(n)


if __name__ == "__main__":
    main()
'''
SPEC = {
    "name": "planted",
    "command": [sys.executable, "work.py"],
    "metric": {
        "kind": "wall",
        "direction": "lower_is_better",
        "unit": "s",
    },
    "correctness": {
        "command": [sys.executable, "-c", "pass"],
        "timeout_seconds": 30.0,
    },
    "warmup": 0,
    "budget": {"max_rounds": 20, "max_seconds": 60.0},
    "looks": {"n0": 10, "k": 10, "max_looks": 2},
    "timeout_seconds": 120.0,
    "harness": ["work.py"],
}


def git(repo: Path, *argv: str) -> None:
    identity = ("-c", "user.name=t", "-c", "user.email=t@t")
    subprocess.run(
        ["git", "-C", str(repo), *identity, *argv],
        check=True,
        capture_output=True,
    )


def cli(
    capsys: pytest.CaptureFixture[str], *argv: str
) -> tuple[int, dict[str, Any]]:
    status = main([*argv, "--json"])
    out, _ = capsys.readouterr()
    return status, cast("dict[str, Any]", json.loads(out))


@pytest.fixture
def planted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    if shutil.which("uv") is None:
        pytest.skip("uv is not on PATH")
    repo = tmp_path / "planted"
    repo.mkdir()
    (repo / "work.py").write_text(WORK, encoding="utf-8")
    (repo / "unused.py").write_text("def never():\n    return 1\n", "utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_work.py").write_text(
        "def test_x(): ...\n", "utf-8"
    )
    (repo / "spec.json").write_text(json.dumps(SPEC), encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "i")
    status, document = cli(
        capsys,
        "bench",
        "define",
        "planted",
        "--spec",
        str(repo / "spec.json"),
        "--repo",
        str(repo),
    )
    assert status == 0, document
    return repo


@pytest.mark.integration
def test_planted_quadratic_grows_faster_than_the_workload(
    planted: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, document = cli(
        capsys,
        "profile",
        "planted",
        "--sizes",
        "1,2,4,8",
        "--size-arg=--n=1000",
        "--profiler",
        "pyinstrument",
        "--repo",
        str(planted),
    )
    assert status == 0, document
    result = document["result"]
    assert result["sizes"] == [1000, 2000, 4000, 8000]
    functions = {f["function"]: f for f in result["functions"]}
    quad = functions["quadratic"]
    lin = functions["linear"]
    assert quad["fit"]["n"] == 4
    assert 1.6 < quad["fit"]["exponent"] < 2.4
    assert 0.6 < lin["fit"]["exponent"] < 1.4
    # Timing noise can turn a shown result into a lead, never into none.
    assert quad["evidence"] in ("shown", "lead")
    assert lin["evidence"] == "none"
    assert ("interval_overlaps_workload" in quad["notes"]) == (
        quad["evidence"] == "lead"
    )
    flagged = [f["function"] for f in result["faster_than_workload"]]
    assert "quadratic" in flagged
    assert "linear" not in flagged
    # One profile per size was saved, each marked with its size.
    runs = sorted((planted / ".perfproof" / "profiles" / "planted").iterdir())
    saved = [
        json.loads((r / "result.json").read_text("utf-8"))
        for r in runs
        if r.is_dir()
    ]
    assert sorted(s["size"]["value"] for s in saved) == [
        1000,
        2000,
        4000,
        8000,
    ]
    assert {s["size"]["group"] for s in saved} == {result["size_group"]}
    assert all(
        s["argv"][-2:] == ["--n", str(s["size"]["value"])] for s in saved
    )


@pytest.mark.integration
def test_rank_flags_the_quadratic_and_lists_unsampled_files(
    planted: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, _ = cli(
        capsys,
        "profile",
        "planted",
        "--sizes",
        "1,2,4,8",
        "--size-arg=--n=1000",
        "--profiler",
        "pyinstrument",
        "--repo",
        str(planted),
    )
    assert status == 0
    status, document = cli(
        capsys, "profile", "show", "--rank", "--repo", str(planted)
    )
    assert status == 0, document
    result = document["result"]
    rows = {f["function"]: f for f in result["functions"]}
    quad, lin = rows["quadratic"], rows["linear"]
    assert len(quad["seen_in"]) == 4
    assert quad["ranked_in"]["size"] == 8000
    shown = quad["growth"]["evidence"] == "shown"
    assert quad["ceiling_rises_with_size"] is (True if shown else None)
    assert lin["ceiling_rises_with_size"] is False
    coverage = result["coverage"]
    assert coverage["files"] == ["unused.py"]
    assert (coverage["source_files"], coverage["sampled"]) == (2, 1)
    assert coverage["never_sampled"] == 1
    assert coverage["never_sampled_by_suffix"] == {".py": 1}
    main_row = rows["main"]
    assert main_row["wrapper"] is True
    assert not quad["wrapper"]
    assert not lin["wrapper"]
    status = main(["profile", "show", "--rank", "--repo", str(planted)])
    text = capsys.readouterr().out
    assert status == 0
    assert text.index(". quadratic") < text.index(". main (a wrapper")
    assert text.index(". linear") < text.index(". main (a wrapper")
