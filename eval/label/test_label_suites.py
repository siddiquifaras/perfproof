"""Suite readers, on output the real tools write.

pytest and unittest run here on a small test module with a pass, a
failure, an error and a skip. cargo's output is captured from a real
run (eval/label/fixtures/README.md says how and with which version).
"""

import json
import subprocess
import sys
from pathlib import Path

import suites

FIXTURES = Path(__file__).resolve().parent / "fixtures"

MODULE = """\
import unittest


class Cases(unittest.TestCase):
    def test_passes(self):
        pass

    def test_fails(self):
        self.assertEqual(1, 2)

    def test_errors(self):
        raise RuntimeError("boom")

    @unittest.skip("not here")
    def test_skipped(self):
        pass
"""


def _write_module(tmp_path: Path) -> None:
    (tmp_path / "test_cases.py").write_text(MODULE)
    # Stops pytest's config search at this directory.
    (tmp_path / "pytest.ini").write_text("[pytest]\n")


def test_junit_from_a_real_pytest_run(tmp_path: Path) -> None:
    _write_module(tmp_path)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--junitxml={suites.JUNIT}",
            "test_cases.py",
        ],
        cwd=tmp_path,
        capture_output=True,
        timeout=120,
        check=False,
    )
    got = suites.read_results("pytest-junit", "", tmp_path)
    # pytest reports an exception in a test body as a failure; an
    # error is one raised in setup or teardown.
    assert (got.passed, got.failed, got.errors, got.skipped) == (1, 2, 0, 1)
    assert got.failing == [
        "test_cases.Cases::test_errors",
        "test_cases.Cases::test_fails",
    ]


def test_missing_junit_report_is_unread(tmp_path: Path) -> None:
    got = suites.read_results("pytest-junit", "", tmp_path)
    assert not got.read
    assert got.reason == f"no {suites.JUNIT} written"
    assert not suites.read_junit("<not xml").read


def test_unittest_from_a_real_run(tmp_path: Path) -> None:
    _write_module(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "test_cases"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    got = suites.read_unittest(proc.stdout + proc.stderr)
    assert (got.passed, got.failed, got.errors, got.skipped) == (1, 1, 1, 1)
    assert got.failing == [
        "test_cases.Cases.test_errors",
        "test_cases.Cases.test_fails",
    ]


def test_unittest_without_a_summary_is_unread() -> None:
    got = suites.read_unittest("Traceback (most recent call last):\n")
    assert not got.read


def test_cargo_from_a_captured_run() -> None:
    text = (FIXTURES / "cargo-test-failing.txt").read_text()
    got = suites.read_cargo(text)
    assert (got.passed, got.failed, got.errors, got.skipped) == (1, 1, 0, 1)
    assert got.failing == ["tests::fails"]
    assert not suites.read_cargo("error: could not compile").read


def test_every_recipe_names_a_reader() -> None:
    for task_id, recipe in suites.recipes().items():
        assert recipe["reader"] in suites.READERS, task_id
        assert recipe["command"], task_id
        assert recipe["source"], task_id
    json.dumps(suites.recipes())


def test_a_run_with_no_passing_test_is_unread() -> None:
    cargo = (
        "running 0 tests\n\n"
        "test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured\n"
    )
    got = suites.read_cargo(cargo)
    assert not got.read
    assert "zero tests passed" in got.reason
    unittest = "Ran 0 tests in 0.000s\n\nNO TESTS RAN\nOK\n"
    assert not suites.read_unittest(unittest).read
    all_failing = (
        "Ran 1 test in 0.001s\n\nFAIL: test_a (m.C.test_a)\n\n"
        "FAILED (failures=1)\n"
    )
    assert not suites.read_unittest(all_failing).read
    junit = (
        '<testsuite><testcase classname="c" name="n"><failure/>'
        "</testcase></testsuite>"
    )
    assert not suites.read_junit(junit).read
