import json
import os
import shutil
import subprocess
import sys
import time
import tracemalloc
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from perfproof.bench.correctness import (
    BASELINE_RUNS,
    EXIT_STATUS_ID,
    MAX_DIFFERENCES,
    OUTPUT_TAIL_BYTES,
    REPORT_ENV_VAR,
    RERUNS,
    BaselineBrokenError,
    check_tests,
    clear_outputs,
    compare_outputs,
    correctness_gate,
    run_tests,
)
from perfproof.schema import BenchmarkSpec, GateResult, OutputCheck

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
FLAKY_ID = "tests.test_rolling::test_last_window_is_at_least_average"
ARMS = ("baseline", "candidate")

# A test runner that reports to the path perfproof passes in its
# environment. Each tree counts its own runs in the state directory,
# and plan.json scripts each test's result per run: p(assed),
# f(ailed), e(rror), s(kipped) or a(bsent), passed past the plan's end.
# With "fresh", every candidate run also fails a test named after it.
RUNNER = """\
import json, os, sys
from pathlib import Path
from xml.sax.saxutils import quoteattr

tree = Path(__file__).parent.name
state = Path(os.environ["SUITE_STATE"])
counter = state / f"{tree}.runs"
run = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(run + 1))
plan = json.loads((state / "plan.json").read_text())
scripted = plan.get(tree, {})
child = {"f": "<failure/>", "e": "<error/>", "s": "<skipped/>", "p": ""}
cases, failing = [], False
for name in plan["tests"]:
    codes = scripted.get(name, "")
    code = codes[run] if run < len(codes) else "p"
    if code == "a":
        continue
    failing = failing or code in "fe"
    cases.append(
        f"<testcase classname='suite' name={quoteattr(name)}>"
        f"{child[code]}</testcase>"
    )
if tree == "candidate" and plan.get("fresh"):
    failing = True
    cases.append(
        f"<testcase classname='suite' name='fresh_{run}'><failure/></testcase>"
    )
report = os.environ.get("PERFPROOF_JUNIT_XML")
if report and plan.get("report", True):
    Path(report).write_text(
        "<testsuites><testsuite>" + "".join(cases)
        + "</testsuite></testsuites>"
    )
exits = scripted.get("exit", "")
if run < len(exits):
    sys.exit(int(exits[run]))
sys.exit(1 if failing else 0)
"""

# The same scripting for real pytest: conftest.py counts the runs and
# the test reads its planned result.
CONFTEST = """\
import os
from pathlib import Path

def pytest_sessionstart(session):
    state = Path(os.environ["SUITE_STATE"])
    counter = state / f"{Path(__file__).parent.name}.runs"
    run = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(run + 1))
"""
PYTEST_SUITE = """\
import json, os
from pathlib import Path

TREE = Path(__file__).parents[1].name
STATE = Path(os.environ["SUITE_STATE"])

def planned(name):
    run = int((STATE / f"{TREE}.runs").read_text()) - 1
    codes = json.loads((STATE / "plan.json").read_text())
    codes = codes.get(TREE, {}).get(name, "")
    return codes[run] if run < len(codes) else "p"

def test_stable():
    assert True

def test_flaky():
    assert planned("test_flaky") == "p"

def test_target():
    assert planned("test_target") == "p"

def test_known():
    assert planned("test_known") == "p"
"""


def make_spec(**correctness: Any) -> BenchmarkSpec:
    fields: dict[str, Any] = {
        "name": "bench-tile",
        "command": ("python", "bench.py"),
        "metric": {
            "kind": "wall",
            "direction": "lower_is_better",
            "unit": "s",
        },
        "correctness": correctness,
        "warmup": 0,
        "budget": {"max_rounds": 20, "max_seconds": 600.0},
        "looks": {"n0": 10, "k": 10, "max_looks": 3},
        "timeout_seconds": 30.0,
        "harness": ("bench.py",),
    }
    if "env" in correctness:
        fields["env"] = correctness.pop("env")
    return BenchmarkSpec.model_validate(fields)


def outputs_spec(*checks: dict[str, Any]) -> BenchmarkSpec:
    return make_spec(outputs=checks)


@pytest.fixture
def trees(tmp_path: Path) -> tuple[Path, Path]:
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    return baseline, candidate


def write_both(
    trees: tuple[Path, Path], rel: str, baseline: object, candidate: object
) -> None:
    for root, content in zip(trees, (baseline, candidate), strict=True):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        elif isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            np.save(path, np.asarray(content))


def compare(trees: tuple[Path, Path], *checks: dict[str, Any]) -> GateResult:
    return correctness_gate(
        outputs_spec(*checks),
        baseline_root=trees[0],
        candidate_root=trees[1],
        env=os.environ,
    )


class Suite:
    """Two trees running a scripted suite, with a run count per tree."""

    def __init__(self, root: Path, *, pytest_suite: bool = False) -> None:
        self.state = root / "state"
        self.state.mkdir()
        self.baseline, self.candidate = root / "baseline", root / "candidate"
        for tree in (self.baseline, self.candidate):
            tree.mkdir()
            if pytest_suite:
                (tree / "pytest.ini").write_text("[pytest]\n")
                (tree / "conftest.py").write_text(CONFTEST)
                (tree / "tests").mkdir()
                (tree / "tests" / "test_suite.py").write_text(PYTEST_SUITE)
            else:
                (tree / "runner.py").write_text(RUNNER)
        command = (
            (sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider")
            if pytest_suite
            else (sys.executable, "runner.py")
        )
        self.spec = make_spec(
            command=command,
            timeout_seconds=60.0,
            env={
                "SUITE_STATE": str(self.state),
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            },
        )

    def plan(
        self,
        tests: tuple[str, ...] = ("test_a", "test_b"),
        report: bool = True,
        fresh: bool = False,
        **trees: Mapping[str, str],
    ) -> None:
        plan = {
            "tests": list(tests),
            "report": report,
            "fresh": fresh,
            **trees,
        }
        (self.state / "plan.json").write_text(json.dumps(plan))

    def runs(self, arm: str) -> int:
        counter = self.state / f"{arm}.runs"
        return int(counter.read_text()) if counter.exists() else 0

    def gate(self) -> GateResult:
        return correctness_gate(
            self.spec,
            baseline_root=self.baseline,
            candidate_root=self.candidate,
            env=os.environ,
        )


@pytest.fixture
def suite(tmp_path: Path) -> Suite:
    return Suite(tmp_path)


# Declared outputs


@pytest.mark.parametrize(
    ("compare_as", "content"),
    [
        ("bytes", b"\x00\x01binary\xff"),
        ("text", "line one\nline two\n"),
        ("numbers", "loss 0.25 after 10 steps\n"),
    ],
)
def test_equal_outputs_pass(
    trees: tuple[Path, Path], compare_as: str, content: str | bytes
) -> None:
    write_both(trees, "out/result", content, content)

    result = compare(trees, {"path": "out/result", "compare": compare_as})

    assert result.status == "passed"
    assert result.differences == ()
    assert "1 declared outputs compared" in result.detail


def test_equal_arrays_pass(trees: tuple[Path, Path]) -> None:
    values = np.arange(12, dtype=np.int64).reshape(3, 4)
    write_both(trees, "sums.npy", values, values.copy())

    assert compare(
        trees, {"path": "sums.npy", "compare": "numbers"}
    ).status == ("passed")


def test_unequal_bytes_report_first_offset_and_sizes(
    trees: tuple[Path, Path],
) -> None:
    write_both(trees, "out.bin", b"abcdef", b"abXdefgh")

    result = compare(trees, {"path": "out.bin", "compare": "bytes"})

    assert result.status == "failed"
    assert [(d.item, d.baseline, d.candidate) for d in result.differences] == [
        ("out.bin at byte 2", "63 64 65 66", "58 64 65 66 67 68"),
        ("out.bin size", "6 bytes", "8 bytes"),
    ]
    assert "output out.bin differs (bytes)" in result.detail


def test_bytes_that_differ_only_in_length(trees: tuple[Path, Path]) -> None:
    write_both(trees, "out.bin", b"abc", b"abcd")

    result = compare(trees, {"path": "out.bin", "compare": "bytes"})

    assert [d.item for d in result.differences] == ["out.bin size"]


def test_bytes_differing_past_the_first_chunk(
    trees: tuple[Path, Path],
) -> None:
    size = (1 << 20) + 10
    baseline = bytes(size)
    candidate = bytearray(baseline)
    candidate[size - 3] = 1
    write_both(trees, "big.bin", baseline, bytes(candidate))

    result = compare(trees, {"path": "big.bin", "compare": "bytes"})

    assert [d.item for d in result.differences] == [
        f"big.bin at byte {size - 3}"
    ]


def test_unequal_text_lists_each_line(trees: tuple[Path, Path]) -> None:
    write_both(trees, "log.txt", "a\nb\nc\n", "a\nB\nc\nd\n")

    result = compare(trees, {"path": "log.txt", "compare": "text"})

    assert result.status == "failed"
    assert [(d.item, d.baseline, d.candidate) for d in result.differences] == [
        ("log.txt line 2", "b", "B"),
        ("log.txt line 4", None, "d"),
    ]


def test_text_ignores_line_endings_and_final_newline(
    trees: tuple[Path, Path],
) -> None:
    write_both(trees, "log.txt", b"a\r\nb\r\n", b"a\nb")

    assert compare(trees, {"path": "log.txt", "compare": "text"}).status == (
        "passed"
    )


def test_candidate_text_that_is_not_utf8_differs(
    trees: tuple[Path, Path],
) -> None:
    write_both(trees, "log.txt", "ok\n", b"ok\n\xff\xfe\n")

    result = compare(trees, {"path": "log.txt", "compare": "text"})

    assert result.status == "failed"
    assert "is not UTF-8 text" in result.detail


def test_baseline_text_that_is_not_utf8_breaks_the_run(
    trees: tuple[Path, Path],
) -> None:
    write_both(trees, "log.txt", b"\xff\n", "ok\n")

    with pytest.raises(BaselineBrokenError, match=r"log\.txt in the baseline"):
        compare(trees, {"path": "log.txt", "compare": "text"})


def test_unequal_arrays_report_indices_and_values(
    trees: tuple[Path, Path],
) -> None:
    baseline = np.arange(6, dtype=np.float64).reshape(2, 3)
    candidate = baseline.copy()
    candidate[1, 2] = 7.5
    write_both(trees, "out.npy", baseline, candidate)

    result = compare(trees, {"path": "out.npy", "compare": "numbers"})

    assert result.status == "failed"
    assert [(d.item, d.baseline, d.candidate) for d in result.differences] == [
        ("out.npy[1, 2]", "5.0", "7.5")
    ]


def test_fortran_order_arrays_index_the_element_that_differs(
    trees: tuple[Path, Path],
) -> None:
    baseline = np.asfortranarray(np.arange(6, dtype=np.int64).reshape(2, 3))
    candidate = baseline.copy(order="F")
    candidate[0, 2] = 99
    write_both(trees, "out.npy", baseline, candidate)

    result = compare(trees, {"path": "out.npy", "compare": "numbers"})

    assert [d.item for d in result.differences] == ["out.npy[0, 2]"]


def test_array_shape_and_dtype_must_match(trees: tuple[Path, Path]) -> None:
    write_both(trees, "a.npy", np.zeros(3), np.zeros(4))
    write_both(
        trees, "b.npy", np.zeros(3, np.float64), np.zeros(3, np.float32)
    )

    result = compare(
        trees,
        {"path": "a.npy", "compare": "numbers"},
        {"path": "b.npy", "compare": "numbers", "atol": 1.0},
    )

    assert [(d.item, d.baseline, d.candidate) for d in result.differences] == [
        ("a.npy shape", "(3,)", "(4,)"),
        ("b.npy dtype", "float64", "float32"),
    ]


def test_numbers_within_tolerance_pass_and_outside_fail(
    trees: tuple[Path, Path],
) -> None:
    write_both(trees, "a.npy", np.array([1.0, 100.0]), np.array([1.05, 101.0]))
    write_both(trees, "a.txt", "x=1.0 y=100\n", "x=1.05 y=101\n")
    within = {"rtol": 0.01, "atol": 0.05}

    passed = compare(
        trees,
        {"path": "a.npy", "compare": "numbers", **within},
        {"path": "a.txt", "compare": "numbers", **within},
    )
    exact = compare(
        trees,
        {"path": "a.npy", "compare": "numbers"},
        {"path": "a.txt", "compare": "numbers"},
    )

    assert passed.status == "passed"
    assert exact.status == "failed"
    assert [(d.item, d.baseline, d.candidate) for d in exact.differences] == [
        ("a.npy[0]", "1.0", "1.05"),
        ("a.npy[1]", "100.0", "101.0"),
        ("a.txt line 1, number 1", "1.0", "1.05"),
        ("a.txt line 1, number 2", "100", "101"),
    ]


def test_tolerance_is_relative_to_the_baseline(
    trees: tuple[Path, Path],
) -> None:
    # |c - b| <= rtol * |b|: 0.1 is within 10% of 1.0, not of 0.9.
    write_both(trees, "a.txt", "1.0\n", "0.9\n")
    write_both(trees, "b.txt", "0.9\n", "1.0\n")

    result = compare(
        trees,
        {"path": "a.txt", "compare": "numbers", "rtol": 0.1},
        {"path": "b.txt", "compare": "numbers", "rtol": 0.1},
    )

    assert [d.item for d in result.differences] == ["b.txt line 1, number 1"]


def test_text_between_numbers_must_match_up_to_whitespace(
    trees: tuple[Path, Path],
) -> None:
    write_both(trees, "ok.txt", "loss:  0.5\tacc 1\n", "loss: 0.5 acc 1\n")
    write_both(trees, "bad.txt", "loss: 0.5\n", "lost: 0.5\n")

    result = compare(
        trees,
        {"path": "ok.txt", "compare": "numbers"},
        {"path": "bad.txt", "compare": "numbers"},
    )

    assert [(d.item, d.baseline, d.candidate) for d in result.differences] == [
        ("bad.txt line 1", "loss: 0.5", "lost: 0.5")
    ]


def test_large_integers_compare_exactly(trees: tuple[Path, Path]) -> None:
    big = 2**60
    write_both(
        trees,
        "a.npy",
        np.array([big], dtype=np.int64),
        np.array([big + 1], dtype=np.int64),
    )
    write_both(trees, "a.txt", f"{big}\n", f"{big + 1}\n")

    result = compare(
        trees,
        {"path": "a.npy", "compare": "numbers"},
        {"path": "a.txt", "compare": "numbers"},
    )

    assert [d.item for d in result.differences] == [
        "a.npy[0]",
        "a.txt line 1, number 1",
    ]


@pytest.mark.parametrize("equal_nan", [False, True])
def test_nan_equals_nan_only_when_declared(
    trees: tuple[Path, Path], equal_nan: bool
) -> None:
    values = np.array([1.0, np.nan, np.inf])
    write_both(trees, "a.npy", values, values.copy())
    write_both(trees, "a.txt", "nan inf\n", "NaN inf\n")

    result = compare(
        trees,
        {"path": "a.npy", "compare": "numbers", "equal_nan": equal_nan},
        {"path": "a.txt", "compare": "numbers", "equal_nan": equal_nan},
    )

    if equal_nan:
        assert result.status == "passed"
    else:
        assert [d.item for d in result.differences] == [
            "a.npy[1]",
            "a.txt line 1, number 1",
        ]


def test_infinity_is_close_only_to_the_same_infinity(
    trees: tuple[Path, Path],
) -> None:
    write_both(trees, "a.txt", "inf\ninf\ninf\n-inf\n", "5\n-inf\ninf\n-inf\n")
    write_both(
        trees, "a.npy", np.array([np.inf, np.inf]), np.array([5.0, -np.inf])
    )
    loose = {"compare": "numbers", "rtol": 0.5, "atol": 1.0}

    result = compare(
        trees, {"path": "a.txt", **loose}, {"path": "a.npy", **loose}
    )

    assert [(d.item, d.baseline, d.candidate) for d in result.differences] == [
        ("a.txt line 1, number 1", "inf", "5"),
        ("a.txt line 2, number 1", "inf", "-inf"),
        ("a.npy[0]", "inf", "5.0"),
        ("a.npy[1]", "inf", "-inf"),
    ]


@pytest.mark.parametrize(
    ("baseline", "candidate"),
    [
        ("inf", "1e309"),
        ("-inf", "-1e400"),
        ("inf", str(2**1100)),
        ("inf", "9" * 5000),
        ("1e309", "inf"),
    ],
)
@pytest.mark.parametrize("rtol", [0.0, 0.5])
def test_finite_literal_beyond_float_range_is_not_infinity(
    trees: tuple[Path, Path], baseline: str, candidate: str, rtol: float
) -> None:
    write_both(trees, "a.txt", f"{baseline}\n", f"{candidate}\n")

    result = compare(
        trees, {"path": "a.txt", "compare": "numbers", "rtol": rtol}
    )

    assert [d.item for d in result.differences] == ["a.txt line 1, number 1"]


@pytest.mark.parametrize(
    ("baseline", "candidate"),
    [("inf", "+inf"), ("inf", "Infinity"), ("-inf", "-INF")],
)
def test_infinities_of_the_same_sign_are_equal(
    trees: tuple[Path, Path], baseline: str, candidate: str
) -> None:
    write_both(trees, "a.txt", f"{baseline}\n", f"{candidate}\n")

    assert compare(trees, {"path": "a.txt", "compare": "numbers"}).status == (
        "passed"
    )


def test_finite_number_is_not_close_to_infinity(
    trees: tuple[Path, Path],
) -> None:
    write_both(trees, "a.txt", "5\n", "inf\n")

    result = compare(
        trees, {"path": "a.txt", "compare": "numbers", "rtol": 0.5}
    )

    assert [d.item for d in result.differences] == ["a.txt line 1, number 1"]


def test_identical_nan_text_differs_without_equal_nan(
    trees: tuple[Path, Path],
) -> None:
    write_both(trees, "a.txt", "loss nan\n", "loss nan\n")

    strict = compare(trees, {"path": "a.txt", "compare": "numbers"})
    lenient = compare(
        trees, {"path": "a.txt", "compare": "numbers", "equal_nan": True}
    )

    assert [d.item for d in strict.differences] == ["a.txt line 1, number 1"]
    assert lenient.status == "passed"


def test_nul_bytes_in_text_are_compared_not_fatal(
    trees: tuple[Path, Path],
) -> None:
    write_both(trees, "n.txt", "a\0 1\n1\0\na\0\n", "a\0 2\n1\na 1\n")
    write_both(trees, "t.txt", "a\0b\n", "a\0c\n")

    result = compare(
        trees,
        {"path": "n.txt", "compare": "numbers"},
        {"path": "t.txt", "compare": "text"},
    )

    assert [(d.item, d.baseline, d.candidate) for d in result.differences] == [
        ("n.txt line 1, number 1", "1", "2"),
        ("n.txt line 2", "1\0", "1"),
        ("n.txt line 3", "a\0", "a 1"),
        ("t.txt line 1", "a\0b", "a\0c"),
    ]


def test_huge_integer_literals_compare_exactly(
    trees: tuple[Path, Path],
) -> None:
    big = 10**400
    write_both(trees, "a.txt", f"{big}\n", f"{big + 1}\n")

    near = compare(
        trees, {"path": "a.txt", "compare": "numbers", "rtol": 1e-9}
    )
    exact = compare(trees, {"path": "a.txt", "compare": "numbers"})

    assert near.status == "passed"
    assert [d.item for d in exact.differences] == ["a.txt line 1, number 1"]
    assert exact.differences[0].baseline is not None
    assert exact.differences[0].baseline.endswith("...")


def test_literals_too_long_to_evaluate_differ_unless_identical(
    trees: tuple[Path, Path],
) -> None:
    long = "7" * 5000
    write_both(trees, "same.txt", f"{long} 1e99999\n", f"{long} 1e99999\n")
    write_both(trees, "diff.txt", f"{long}\n1e99999\n", f"{long}8\n2e99999\n")
    loose = {"compare": "numbers", "rtol": 0.5}

    result = compare(
        trees, {"path": "same.txt", **loose}, {"path": "diff.txt", **loose}
    )

    assert [d.item for d in result.differences] == [
        "diff.txt line 1, number 1",
        "diff.txt line 2, number 1",
    ]


@pytest.mark.parametrize("length", [65535, 65536, 65537, 200_000])
def test_long_lines_compare_across_pieces(
    trees: tuple[Path, Path], length: int
) -> None:
    line = "x" * length
    changed = line[:-1] + "y"
    write_both(trees, "same.txt", f"{line}\nend\n", f"{line}\nend")
    write_both(trees, "tail.txt", f"{line}\nend\n", f"{changed}\nend\n")
    write_both(trees, "long.txt", f"{line}\nend\n", f"{line}z\nend\n")

    result = compare(
        trees,
        {"path": "same.txt", "compare": "text"},
        {"path": "tail.txt", "compare": "text"},
        {"path": "long.txt", "compare": "text"},
    )

    assert [d.item for d in result.differences] == [
        "tail.txt line 1",
        "long.txt line 1",
    ]
    assert result.differences[0].baseline == "x" * 200 + "..."


def test_one_long_text_line_compares_in_bounded_memory(
    trees: tuple[Path, Path],
) -> None:
    line = "0123456789" * 2_000_000
    write_both(trees, "big.txt", line + "a\n", line + "b\n")

    tracemalloc.start()
    try:
        result = compare(trees, {"path": "big.txt", "compare": "text"})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert [d.item for d in result.differences] == ["big.txt line 1"]
    assert peak < 4 << 20


def test_nan_never_equals_a_number(trees: tuple[Path, Path]) -> None:
    write_both(trees, "a.npy", np.array([np.nan]), np.array([0.0]))

    result = compare(
        trees, {"path": "a.npy", "compare": "numbers", "equal_nan": True}
    )

    assert [(d.baseline, d.candidate) for d in result.differences] == [
        ("nan", "0.0")
    ]


def test_missing_candidate_output_is_a_difference(
    trees: tuple[Path, Path],
) -> None:
    (trees[0] / "out.npy").write_bytes(b"12345")

    result = compare(trees, {"path": "out.npy", "compare": "numbers"})

    assert result.status == "failed"
    assert [(d.item, d.baseline, d.candidate) for d in result.differences] == [
        ("out.npy", "5 bytes", None)
    ]
    assert "out.npy is missing in the candidate tree" in result.detail


def test_missing_baseline_output_breaks_the_run(
    trees: tuple[Path, Path],
) -> None:
    (trees[1] / "out.txt").write_text("1\n")

    with pytest.raises(
        BaselineBrokenError, match=r"out\.txt is missing in the baseline tree"
    ):
        compare(trees, {"path": "out.txt", "compare": "text"})


def test_output_linked_out_of_its_tree_is_refused(
    trees: tuple[Path, Path],
) -> None:
    (trees[0] / "out.txt").write_text("1\n")
    (trees[1] / "out.txt").symlink_to(trees[0] / "out.txt")

    result = compare(trees, {"path": "out.txt", "compare": "text"})

    assert result.status == "failed"
    assert "resolves outside its tree" in result.detail


@pytest.mark.parametrize("content", [b"not an array", b""])
def test_candidate_array_that_cannot_be_read_differs(
    trees: tuple[Path, Path], content: bytes
) -> None:
    write_both(trees, "a.npy", np.zeros(2), content)

    result = compare(trees, {"path": "a.npy", "compare": "numbers"})

    assert result.status == "failed"
    assert "cannot be read as an array" in result.detail


def test_zero_dimensional_arrays_compare(trees: tuple[Path, Path]) -> None:
    write_both(trees, "a.npy", np.float64(3.0), np.float64(4.0))

    result = compare(trees, {"path": "a.npy", "compare": "numbers"})

    assert [(d.item, d.baseline, d.candidate) for d in result.differences] == [
        ("a.npy[()]", "3.0", "4.0")
    ]


def test_differences_are_capped_per_output(trees: tuple[Path, Path]) -> None:
    write_both(trees, "a.npy", np.zeros(500), np.ones(500))

    found = compare_outputs(
        [OutputCheck(path="a.npy", compare="numbers")],
        baseline_root=trees[0],
        candidate_root=trees[1],
        limit=7,
    )
    gate = compare(trees, {"path": "a.npy", "compare": "numbers"})

    assert len(found.differences) == 7
    assert found.truncated
    assert found.problems == (
        "output a.npy differs (numbers): 500 differences",
    )
    assert len(gate.differences) == MAX_DIFFERENCES
    assert gate.truncated


def test_large_arrays_compare_in_chunks(trees: tuple[Path, Path]) -> None:
    baseline = np.zeros(600_000)
    candidate = baseline.copy()
    candidate[[5, 599_999]] = 1.0
    write_both(trees, "a.npy", baseline, candidate)

    result = compare(trees, {"path": "a.npy", "compare": "numbers"})

    assert [d.item for d in result.differences] == [
        "a.npy[5]",
        "a.npy[599999]",
    ]


def test_clear_outputs_removes_stale_files(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "a.npy").write_bytes(b"stale")
    (tmp_path / "b.txt").symlink_to(tmp_path / "out" / "a.npy")
    checks = [
        OutputCheck(path="out/a.npy", compare="numbers"),
        OutputCheck(path="b.txt", compare="text"),
        OutputCheck(path="never/made.txt", compare="text"),
    ]

    clear_outputs(checks, tmp_path)

    assert not (tmp_path / "out" / "a.npy").exists()
    assert not (tmp_path / "b.txt").is_symlink()


# Tests: baseline runs and flakiness


def test_passing_suite_passes_after_three_baseline_runs(suite: Suite) -> None:
    suite.plan()

    result = suite.gate()

    assert result.status == "passed"
    assert result.quarantined == ()
    assert (suite.runs("baseline"), suite.runs("candidate")) == (3, 1)
    assert "2 tests judged over 3 baseline and 1 candidate runs" in (
        result.detail
    )


def test_flaky_test_is_quarantined_not_blamed(suite: Suite) -> None:
    suite.plan(baseline={"test_a": "pfp"}, candidate={"test_a": "f"})

    result = suite.gate()

    assert result.status == "passed"
    assert [(q.test_id, q.baseline_results) for q in result.quarantined] == [
        ("suite::test_a", ("passed", "failed", "passed"))
    ]
    assert (suite.runs("baseline"), suite.runs("candidate")) == (3, 1)
    assert "1 quarantined as flaky: suite::test_a" in result.detail


def test_flaky_test_stays_quarantined_when_the_candidate_breaks_another(
    suite: Suite,
) -> None:
    suite.plan(
        baseline={"test_a": "ffp"}, candidate={"test_a": "f", "test_b": "f"}
    )

    result = suite.gate()

    assert result.status == "failed"
    assert [q.test_id for q in result.quarantined] == ["suite::test_a"]
    assert [d.item for d in result.differences] == ["suite::test_b"]


KNOWN_DETAIL = "1 known failures, failing in every baseline run: "


@pytest.mark.parametrize("baseline_results", ["fff", "fef"])
def test_test_failing_every_baseline_run_is_a_known_failure(
    suite: Suite, baseline_results: str
) -> None:
    suite.plan(baseline={"test_b": baseline_results})

    result = suite.gate()

    assert result.status == "passed"
    assert result.quarantined == ()
    assert result.differences == ()
    assert KNOWN_DETAIL + "suite::test_b" in result.detail
    assert "1 tests judged" in result.detail
    assert (suite.runs("baseline"), suite.runs("candidate")) == (3, 1)


@pytest.mark.parametrize(
    ("candidate_results", "fate"),
    [
        ("p", "fixed"),
        ("f", "still failing"),
        ("e", "erroring"),
        ("s", "skipped"),
        ("a", "gone"),
    ],
)
def test_known_failure_counts_for_nothing_on_the_candidate(
    suite: Suite, candidate_results: str, fate: str
) -> None:
    suite.plan(
        baseline={"test_b": "fff"}, candidate={"test_b": candidate_results}
    )

    result = suite.gate()

    assert result.status == "passed", fate
    assert result.differences == ()
    assert KNOWN_DETAIL + "suite::test_b" in result.detail
    # No reruns: a known failure is never a suspect.
    assert (suite.runs("baseline"), suite.runs("candidate")) == (3, 1)


def test_known_failure_is_not_blamed_when_the_candidate_breaks_another(
    suite: Suite,
) -> None:
    suite.plan(
        baseline={"test_b": "ffffffff"},
        candidate={"test_a": "ffffff", "test_b": "pppppp"},
    )

    result = suite.gate()

    assert result.status == "failed"
    assert [d.item for d in result.differences] == ["suite::test_a"]
    assert KNOWN_DETAIL + "suite::test_b" in result.detail
    assert result.quarantined == ()


def test_known_failure_that_passes_on_a_baseline_rerun_is_flaky(
    suite: Suite,
) -> None:
    suite.plan(baseline={"test_b": "ffffp"}, candidate={"test_a": "ffffff"})

    result = suite.gate()

    assert [(q.test_id, q.baseline_results) for q in result.quarantined] == [
        ("suite::test_b", ("failed",) * 4 + ("passed",) * 4)
    ]
    assert "known failures" not in result.detail
    assert [d.item for d in result.differences] == ["suite::test_a"]


def test_exit_status_failing_every_baseline_run_leaves_the_tests_to_judge(
    suite: Suite,
) -> None:
    suite.plan(baseline={"exit": "333"}, candidate={"exit": "0"})

    result = suite.gate()

    assert result.status == "passed"
    assert KNOWN_DETAIL + EXIT_STATUS_ID in result.detail
    assert "2 tests judged" in result.detail


def test_tests_still_judge_when_the_exit_status_is_a_known_failure(
    suite: Suite,
) -> None:
    suite.plan(
        baseline={"exit": "33333333"},
        candidate={"exit": "333333", "test_a": "ffffff"},
    )

    result = suite.gate()

    assert result.status == "failed"
    assert [d.item for d in result.differences] == ["suite::test_a"]
    assert KNOWN_DETAIL + EXIT_STATUS_ID in result.detail


def test_known_failing_exit_status_without_a_report_is_not_available(
    suite: Suite,
) -> None:
    suite.plan(report=False, baseline={"exit": "111"})

    result = suite.gate()

    assert result.status == "not_available"
    assert result.quarantined == ()
    assert "every test is flaky or a known failure" in result.detail
    assert KNOWN_DETAIL + EXIT_STATUS_ID in result.detail
    assert (suite.runs("baseline"), suite.runs("candidate")) == (3, 1)


def test_candidate_only_failure_is_rerun_five_times_then_blamed(
    suite: Suite,
) -> None:
    suite.plan(candidate={"test_b": "ffffff"})

    result = suite.gate()

    assert result.status == "failed"
    assert (suite.runs("baseline"), suite.runs("candidate")) == (
        BASELINE_RUNS + RERUNS,
        1 + RERUNS,
    )
    assert [(d.item, d.baseline, d.candidate) for d in result.differences] == [
        (
            "suite::test_b",
            "passed in all 8 runs",
            ", ".join(["failed"] * 6),
        )
    ]
    assert result.output is not None
    assert result.output.exit_code == 1
    assert result.quarantined == ()
    assert result.detail.startswith(
        "1 tests fail on the candidate and never on the baseline in 8 runs"
    )


def test_candidate_failure_seen_once_is_still_blamed(suite: Suite) -> None:
    # The candidate fails once and then passes: the baseline never
    # failed in eight runs, so the failure is the candidate's.
    suite.plan(candidate={"test_b": "f"})

    result = suite.gate()

    assert result.status == "failed"
    assert result.differences[0].candidate == (
        "failed, passed, passed, passed, passed, passed"
    )


def test_test_failing_on_a_candidate_rerun_gets_its_own_reruns(
    suite: Suite,
) -> None:
    # The reviewer's case: test_s turns flaky on the first baseline
    # rerun just as test_u first fails on the candidate. The reruns go
    # on for test_u, and its fifth baseline run shows it flaky too.
    suite.plan(
        tests=("test_s", "test_u"),
        baseline={"test_s": "pppf", "test_u": "ppppf"},
        candidate={"test_s": "fp", "test_u": "pf"},
    )

    result = suite.gate()

    # Both are flaky, so nothing is blamed and nothing is left to judge.
    assert result.status == "not_available"
    assert result.differences == ()
    assert [q.test_id for q in result.quarantined] == [
        "suite::test_s",
        "suite::test_u",
    ]
    assert (suite.runs("baseline"), suite.runs("candidate")) == (5, 3)


def test_late_suspect_passing_every_baseline_run_is_blamed(
    suite: Suite,
) -> None:
    # test_u first fails on the candidate's first rerun. The reruns are
    # one shared budget, so it is judged on the eight baseline runs
    # there are, all passed, and blamed.
    suite.plan(
        tests=("test_s", "test_u"),
        baseline={"test_s": "pppf"},
        candidate={"test_s": "fp", "test_u": "pf"},
    )

    result = suite.gate()

    assert result.status == "failed"
    assert [(d.item, d.baseline, d.candidate) for d in result.differences] == [
        (
            "suite::test_u",
            "passed in all 8 runs",
            "passed, failed, passed, passed, passed, passed",
        )
    ]
    assert [q.test_id for q in result.quarantined] == ["suite::test_s"]
    assert (suite.runs("baseline"), suite.runs("candidate")) == (
        BASELINE_RUNS + RERUNS,
        1 + RERUNS,
    )


def test_new_failing_test_on_every_run_still_ends(suite: Suite) -> None:
    # Each candidate run fails a test id never seen before, so without
    # the shared budget a new suspect would appear on every rerun.
    suite.plan(fresh=True)

    result = suite.gate()

    assert (suite.runs("baseline"), suite.runs("candidate")) == (
        BASELINE_RUNS + RERUNS,
        1 + RERUNS,
    )
    assert result.status == "failed"
    assert [d.item for d in result.differences] == [
        f"suite::fresh_{run}" for run in range(1 + RERUNS)
    ]


def test_failure_that_recurs_on_a_baseline_rerun_is_quarantined(
    suite: Suite,
) -> None:
    suite.plan(baseline={"test_b": "ppppf"}, candidate={"test_b": "f"})

    result = suite.gate()

    assert result.status == "passed"
    assert [(q.test_id, q.baseline_results) for q in result.quarantined] == [
        ("suite::test_b", ("passed",) * 4 + ("failed",))
    ]
    # Every suspect is flaky after the second rerun, so reruns stop.
    assert (suite.runs("baseline"), suite.runs("candidate")) == (5, 3)


def test_candidate_skipping_a_passing_test_is_blamed(suite: Suite) -> None:
    suite.plan(candidate={"test_a": "ssssss", "test_b": "aaaaaa"})

    result = suite.gate()

    assert result.status == "failed"
    assert [(d.item, d.candidate) for d in result.differences] == [
        ("suite::test_a", ", ".join(["skipped"] * 6)),
        ("suite::test_b", ", ".join(["absent"] * 6)),
    ]


def test_new_test_failing_on_the_candidate_is_blamed(suite: Suite) -> None:
    suite.plan(
        tests=("test_a", "test_new"),
        baseline={"test_new": "aaaaaaaa"},
        candidate={"test_new": "ffffff"},
    )

    result = suite.gate()

    assert [(d.item, d.baseline) for d in result.differences] == [
        ("suite::test_new", "absent in all 8 runs")
    ]


def test_new_skipped_test_is_not_blamed(suite: Suite) -> None:
    suite.plan(
        tests=("test_a", "test_new"),
        baseline={"test_new": "aaa"},
        candidate={"test_new": "s"},
    )

    assert suite.gate().status == "passed"
    assert suite.runs("candidate") == 1


def test_exit_status_without_a_failing_test_is_judged(suite: Suite) -> None:
    suite.plan(candidate={"exit": "333333"})

    result = suite.gate()

    assert result.status == "failed"
    assert [d.item for d in result.differences] == [EXIT_STATUS_ID]
    assert result.output is not None
    assert result.output.exit_code == 3


def test_exit_status_alone_judges_a_runner_without_a_report(
    suite: Suite,
) -> None:
    suite.plan(report=False, candidate={"test_a": "ffffff"})

    result = suite.gate()

    assert result.status == "failed"
    assert [(d.item, d.candidate) for d in result.differences] == [
        (EXIT_STATUS_ID, ", ".join(["failed"] * 6))
    ]
    assert "1 tests judged" in result.detail


def test_nothing_left_to_judge_is_not_available(suite: Suite) -> None:
    suite.plan(report=False, baseline={"test_a": "pfp"})

    result = suite.gate()

    assert result.status == "not_available"
    assert [q.test_id for q in result.quarantined] == [EXIT_STATUS_ID]
    assert "nothing judged the candidate" in result.detail


def test_all_tests_quarantined_but_outputs_compared_passes(
    suite: Suite,
) -> None:
    suite.plan(tests=("test_a",), baseline={"test_a": "fpp"})
    for tree in (suite.baseline, suite.candidate):
        (tree / "out.txt").write_text("1\n")
    spec = suite.spec.model_copy(
        update={
            "correctness": suite.spec.correctness.model_copy(
                update={
                    "outputs": (OutputCheck(path="out.txt", compare="text"),)
                }
            )
        }
    )

    result = correctness_gate(
        spec,
        baseline_root=suite.baseline,
        candidate_root=suite.candidate,
        env=os.environ,
    )

    assert result.status == "passed"
    assert "0 tests judged" in result.detail


def test_report_path_reaches_the_runner_and_pytest(
    suite: Suite, tmp_path: Path
) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import json, os, sys\n"
        "json.dump({k: os.environ.get(k) for k in sys.argv[1:]}, "
        "sys.stdout)\n"
    )
    spec = make_spec(
        command=(sys.executable, str(probe), REPORT_ENV_VAR, "PYTEST_ADDOPTS"),
        timeout_seconds=30.0,
        env={"PYTEST_ADDOPTS": "-x"},
    )

    run = run_tests(spec, suite.baseline, os.environ)

    assert run.output is not None
    seen = json.loads(run.output.stdout)
    assert seen["PYTEST_ADDOPTS"] == f"-x --junitxml={seen[REPORT_ENV_VAR]}"
    assert not run.reported
    assert run.results == {EXIT_STATUS_ID: "passed"}


# Tests: runs that end abnormally


def sleeper_spec(tree: Path, seconds: float, timeout: float) -> BenchmarkSpec:
    (tree / "sleep.py").write_text(f"import time\ntime.sleep({seconds})\n")
    return make_spec(
        command=(sys.executable, "sleep.py"), timeout_seconds=timeout
    )


def test_candidate_timeout_fails_the_gate(trees: tuple[Path, Path]) -> None:
    (trees[0] / "sleep.py").write_text("")
    spec = sleeper_spec(trees[1], 30, 0.5)

    start = time.monotonic()
    result = correctness_gate(
        spec, baseline_root=trees[0], candidate_root=trees[1], env=os.environ
    )

    assert time.monotonic() - start < 10
    assert result.status == "failed"
    assert result.detail.startswith(
        "the correctness command timed out after 0.5 s in the candidate "
        "tree on its first run"
    )
    assert result.output is not None
    assert result.output.timed_out


def test_baseline_timeout_breaks_the_run(trees: tuple[Path, Path]) -> None:
    spec = sleeper_spec(trees[0], 30, 0.5)

    with pytest.raises(BaselineBrokenError, match="timed out") as broken:
        check_tests(
            spec,
            baseline_root=trees[0],
            candidate_root=trees[1],
            env=os.environ,
        )

    assert broken.value.output is not None
    assert broken.value.output.timed_out


def test_candidate_killed_by_a_signal_fails_the_gate(
    trees: tuple[Path, Path],
) -> None:
    (trees[0] / "run.py").write_text("")
    (trees[1] / "run.py").write_text(
        "import os, signal\nos.kill(os.getpid(), signal.SIGSEGV)\n"
    )
    spec = make_spec(command=(sys.executable, "run.py"), timeout_seconds=30.0)

    result = correctness_gate(
        spec, baseline_root=trees[0], candidate_root=trees[1], env=os.environ
    )

    assert result.status == "failed"
    assert "was killed by signal SIGSEGV" in result.detail
    assert result.output is not None
    assert result.output.exit_code == -11


def test_baseline_killed_by_a_signal_breaks_the_run(
    trees: tuple[Path, Path],
) -> None:
    (trees[0] / "run.py").write_text(
        "import os, signal\nos.kill(os.getpid(), signal.SIGSEGV)\n"
    )
    spec = make_spec(command=(sys.executable, "run.py"), timeout_seconds=30.0)

    with pytest.raises(BaselineBrokenError, match="killed by signal SIGSEGV"):
        check_tests(
            spec,
            baseline_root=trees[0],
            candidate_root=trees[1],
            env=os.environ,
        )


def test_command_that_cannot_start_in_the_candidate_fails_the_gate(
    trees: tuple[Path, Path],
) -> None:
    (trees[0] / "run.sh").write_text("#!/bin/sh\nexit 0\n")
    (trees[0] / "run.sh").chmod(0o755)
    (trees[1] / "run.sh").write_text("#!/bin/sh\nexit 0\n")
    spec = make_spec(command=("./run.sh",), timeout_seconds=30.0)

    result = correctness_gate(
        spec, baseline_root=trees[0], candidate_root=trees[1], env=os.environ
    )

    assert result.status == "failed"
    assert "could not be started" in result.detail
    assert result.output is None


def test_command_that_cannot_start_in_the_baseline_breaks_the_run(
    trees: tuple[Path, Path],
) -> None:
    spec = make_spec(
        command=("./run.sh",), cwd="missing", timeout_seconds=30.0
    )

    with pytest.raises(BaselineBrokenError, match="could not be started"):
        check_tests(
            spec,
            baseline_root=trees[0],
            candidate_root=trees[1],
            env=os.environ,
        )


def test_processes_left_behind_are_killed(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    (tmp_path / "run.py").write_text(
        "import subprocess, sys\n"
        "child = subprocess.Popen(['sleep', '30'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
    )
    spec = make_spec(command=(sys.executable, "run.py"), timeout_seconds=30.0)

    run_tests(spec, tmp_path, os.environ)

    child = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while alive(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(child)


FD_EXHAUSTION = """\
import errno, os, resource, sys
from pathlib import Path
from perfproof.bench.correctness import run_tests
from perfproof.schema import BenchmarkSpec

spec = BenchmarkSpec.model_validate_json(sys.argv[1])
_, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE, (64, hard))
held = []
try:
    while True:
        held.append(os.open(os.devnull, os.O_RDONLY))
except OSError:
    pass
for fd in held[-2:]:
    os.close(fd)
try:
    run_tests(spec, Path(sys.argv[2]), {})
except OSError as exc:
    sys.stdout.write(errno.errorcode[exc.errno])
    sys.exit(0)
sys.stdout.write("returned a result")
sys.exit(1)
"""


def test_running_out_of_descriptors_is_not_blamed_on_a_tree(
    tmp_path: Path,
) -> None:
    spec = make_spec(command=("true",), timeout_seconds=30.0)

    done = subprocess.run(
        [sys.executable, "-c", FD_EXHAUSTION, spec.model_dump_json(), "."],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert (done.returncode, done.stdout) == (0, "EMFILE"), done.stderr


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_long_output_keeps_its_tail(tmp_path: Path) -> None:
    (tmp_path / "run.py").write_text(
        "import sys\n"
        "sys.stdout.write('x' * 100000 + 'END')\n"
        "sys.stderr.write('oops')\n"
        "sys.exit(2)\n"
    )
    spec = make_spec(command=(sys.executable, "run.py"), timeout_seconds=30.0)

    run = run_tests(spec, tmp_path, os.environ)

    assert run.output is not None
    assert run.output.truncated
    assert len(run.output.stdout) == OUTPUT_TAIL_BYTES
    assert run.output.stdout.endswith("END")
    assert run.output.stderr == "oops"
    assert run.output.exit_code == 2
    assert run.results == {EXIT_STATUS_ID: "failed"}


# Real pytest


def test_real_pytest_flaky_known_and_candidate_failures(
    tmp_path: Path,
) -> None:
    suite = Suite(tmp_path, pytest_suite=True)
    suite.plan(
        tests=(),
        baseline={"test_flaky": "fpp", "test_known": "f" * 8},
        candidate={"test_flaky": "ffffff", "test_target": "f"},
    )

    result = suite.gate()

    assert result.status == "failed"
    assert [(q.test_id, q.baseline_results) for q in result.quarantined] == [
        ("tests.test_suite::test_flaky", ("failed",) + ("passed",) * 7)
    ]
    assert [(d.item, d.candidate) for d in result.differences] == [
        (
            "tests.test_suite::test_target",
            "failed, passed, passed, passed, passed, passed",
        )
    ]
    assert (suite.runs("baseline"), suite.runs("candidate")) == (8, 6)
    assert result.output is not None
    assert "test_target" in result.output.stdout
    # The candidate fixes the known failure; that counts for nothing.
    assert KNOWN_DETAIL + "tests.test_suite::test_known" in result.detail


# examples/rolling


def rolling_trees(tmp_path: Path, candidate: str) -> tuple[Path, Path]:
    source = EXAMPLES / "rolling"
    ignore = shutil.ignore_patterns("candidates", "__pycache__", "output")
    baseline = tmp_path / "baseline"
    fix = tmp_path / "candidate"
    shutil.copytree(source, baseline, ignore=ignore)
    shutil.copytree(source, fix, ignore=ignore)
    shutil.copy(source / "candidates" / candidate / "rolling.py", fix)
    for tree in (baseline, fix):
        subprocess.run(
            [sys.executable, "bench.py", "--output", "output/sums.npy"],
            cwd=tree,
            check=True,
            capture_output=True,
            timeout=300,
        )
    return baseline, fix


def flaky_seeds() -> tuple[int, int]:
    """Seeds on which the flaky rolling test passes and fails."""
    outcomes: dict[bool, int] = {}
    for seed in range(100):
        rng = np.random.default_rng(seed)
        values = rng.integers(0, 1000, 64, dtype=np.int64)
        outcomes.setdefault(bool(values[-8:].sum() >= 8 * 499.5), seed)
    return outcomes[True], outcomes[False]


def rolling_gate(tmp_path: Path, candidate: str) -> GateResult:
    """Gate the example with its flaky test passing, failing, passing
    on the baseline and failing on the candidate's first run."""
    baseline, fix = rolling_trees(tmp_path, candidate)
    passing, failing = flaky_seeds()
    seeds = tmp_path / "seeds.json"
    seeds.write_text(json.dumps([passing, failing, passing, failing]))
    wrapper = tmp_path / "suite.py"
    wrapper.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"seeds = Path({str(seeds)!r})\n"
        "queue = json.loads(seeds.read_text())\n"
        f"seed = queue.pop(0) if queue else {passing}\n"
        "seeds.write_text(json.dumps(queue))\n"
        "os.environ['ROLLING_FLAKY_SEED'] = str(seed)\n"
        "os.execv(sys.executable, [sys.executable, '-m', 'pytest', '-q'])\n"
    )
    spec = BenchmarkSpec.model_validate_json(
        (EXAMPLES / "rolling" / "spec.json").read_text()
    )
    spec = spec.model_copy(
        update={
            "correctness": spec.correctness.model_copy(
                update={"command": (sys.executable, str(wrapper))}
            ),
            "env": {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        }
    )
    return correctness_gate(
        spec, baseline_root=baseline, candidate_root=fix, env=os.environ
    )


@pytest.mark.integration
def test_rolling_fix_passes_with_its_flaky_test_quarantined(
    tmp_path: Path,
) -> None:
    result = rolling_gate(tmp_path, "fix")

    assert result.status == "passed", result.detail
    assert [(q.test_id, q.baseline_results) for q in result.quarantined] == [
        (FLAKY_ID, ("passed", "failed", "passed"))
    ]


@pytest.mark.integration
def test_rolling_wrong_output_fails_without_blaming_the_flaky_test(
    tmp_path: Path,
) -> None:
    result = rolling_gate(tmp_path, "wrong-output")

    assert result.status == "failed"
    assert [q.test_id for q in result.quarantined] == [FLAKY_ID]
    blamed = {d.item for d in result.differences}
    assert FLAKY_ID not in blamed
    assert "tests.test_rolling::test_one_window_when_width_equals_length" in (
        blamed
    )
    assert "output/sums.npy shape" in blamed
