import os
import shutil
import subprocess
import sys
import sysconfig
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

import coverage.parser
import pytest

from perfproof.bench.correctness import (
    EXIT_STATUS_ID,
    SuiteComparison,
    check_tests,
)
from perfproof.bench.coverage_gate import (
    COVERAGE_TIMEOUT_FACTOR,
    ChangedLines,
    Deletion,
    FileChange,
    Need,
    OtherChange,
    changed_lines,
    coverage_gate,
    untracked_files,
)
from perfproof.bench.side_effects import benchmark_environment
from perfproof.bench.snapshot import add_baseline_worktree, resolve_commit
from perfproof.schema import (
    Arm,
    BenchmarkSpec,
    Budget,
    CorrectnessSpec,
    GateResult,
    LookSchedule,
    MetricSpec,
    OutputCheck,
    QuarantinedTest,
)

PY = sys.executable

CALC = '''\
"""Arithmetic helpers."""


def double(x):
    return 2 * x


def classify(x):
    if x >= 0:
        return "non-negative"
    return "negative"


def shout(s):
    return s.upper()


def total(xs):
    result = 0
    for x in xs:
        result += x
    return result
'''

CHECKS = """\
from pkg import calc

assert calc.double(2) == 4
assert calc.classify(1) == "non-negative"
assert calc.total([1, 2]) == 3
"""

TESTS = """\
import subprocess
import sys

from pkg import calc


def test_double():
    assert calc.double(2) == 4


def test_classify():
    assert calc.classify(1) == "non-negative"


def test_shout():
    assert calc.shout("a") == "A"


def test_total_in_a_child():
    code = "from pkg import calc; assert calc.total([1, 2]) == 3"
    subprocess.run([sys.executable, "-c", code], check=True, timeout=60)


def test_not_traced():
    assert sys.gettrace() is None
"""

TEST_IDS = (
    "test_calc::test_double",
    "test_calc::test_classify",
    "test_calc::test_shout",
    "test_calc::test_total_in_a_child",
    "test_calc::test_not_traced",
    EXIT_STATUS_ID,
)
NOT_TRACED = ("test_calc::test_not_traced",)


@pytest.fixture(autouse=True)
def isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "gitconfig"
    config.write_text(
        "[user]\n\tname = Test\n\temail = test@example.invalid\n"
        "[commit]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return done.stdout.strip()


def make_trees(
    tmp_path: Path, files: Mapping[str, str] | None = None
) -> dict[Arm, Path]:
    """Commit the project, check the commit out as the baseline."""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    contents = {
        ".gitignore": "__pycache__/\n",
        "pkg/__init__.py": "",
        "pkg/calc.py": CALC,
        "checks.py": CHECKS,
        "test_calc.py": TESTS,
        "README.md": "calc\n",
        **(files or {}),
    }
    for name, text in contents.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "baseline")
    baseline = add_baseline_worktree(
        repo, resolve_commit(repo, "HEAD"), "baseline"
    )
    return {"baseline": baseline, "candidate": repo}


def edit(tree: Path, path: str, old: str, new: str) -> None:
    file = tree / path
    text = file.read_text()
    assert old in text
    file.write_text(text.replace(old, new, 1))


def make_spec(
    command: Sequence[str] | None = (PY, "checks.py"),
    *,
    timeout: float = 60.0,
    env: Mapping[str, str] | None = None,
    allow_unchecked_coverage: bool = False,
    outputs: Sequence[str] = (),
    coverage_command: Sequence[str] | None = None,
) -> BenchmarkSpec:
    checks = tuple(OutputCheck(path=p, compare="text") for p in outputs)
    cover: dict[str, Any] = {}
    if coverage_command is not None:
        cover = {
            "coverage_command": tuple(coverage_command),
            "coverage_timeout_seconds": timeout,
        }
    if command is None:
        correctness = CorrectnessSpec(
            outputs=checks or (OutputCheck(path="out.txt", compare="text"),),
            **cover,
        )
    else:
        correctness = CorrectnessSpec(
            command=tuple(command),
            timeout_seconds=timeout,
            outputs=checks,
            **cover,
        )
    return BenchmarkSpec(
        name="bench",
        command=(PY, "bench.py"),
        env=dict(env or {}),
        metric=MetricSpec(kind="wall", direction="lower_is_better", unit="s"),
        correctness=correctness,
        warmup=0,
        budget=Budget(max_rounds=20, max_seconds=600.0),
        looks=LookSchedule(n0=10, k=4, max_looks=3),
        timeout_seconds=30.0,
        harness=("bench.py",),
        allow_unchecked_coverage=allow_unchecked_coverage,
    )


PYTEST = (PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_calc.py")


def judged(
    quarantined: Sequence[str] = (), known: Sequence[str] = ()
) -> SuiteComparison:
    """The correctness gate's judgement, as `check_tests` returns it."""
    return SuiteComparison(
        quarantined=tuple(
            QuarantinedTest(test_id=t, baseline_results=("passed", "failed"))
            for t in quarantined
        ),
        known_failures=tuple(known),
        failures=(),
        problem=None,
        output=None,
        judged=1,
        baseline_runs=3,
        candidate_runs=1,
    )


def gate(
    trees: Mapping[Arm, Path],
    spec: BenchmarkSpec,
    *,
    quarantined: Sequence[str] = (),
    known: Sequence[str] = (),
    ids: Collection[str] = TEST_IDS,
    untracked: Collection[str] | None = None,
    timeout_factor: float = COVERAGE_TIMEOUT_FACTOR,
) -> GateResult:
    """Run the gate, judging by ``ids`` minus the tests left out."""
    left_out = {*quarantined, *known}
    return coverage_gate(
        spec,
        baseline_root=trees["baseline"],
        candidate_root=trees["candidate"],
        env=benchmark_environment(spec, os.environ),
        tests=judged(quarantined, known),
        judged_tests={t for t in ids if t not in left_out},
        untracked=untracked,
        timeout_factor=timeout_factor,
    )


def unverified(result: GateResult) -> dict[str, tuple[int, ...]]:
    return {u.path: u.lines for u in result.unverified}


def line_of(tree: Path, path: str, text: str) -> int:
    lines = (tree / path).read_text().splitlines()
    return lines.index(text) + 1


def statement(line: int, first: int | None = None) -> Need:
    return Need(line, "statement", line if first is None else first)


# --------------------------------------------------------------------
# Changed lines
# --------------------------------------------------------------------


def test_comments_blank_lines_and_docstrings_are_not_executable(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    cand = trees["candidate"]
    edit(cand, "pkg/calc.py", '"""Arithmetic helpers."""', '"""Helpers."""')
    edit(
        cand,
        "pkg/calc.py",
        "    return 2 * x\n",
        '    """Twice x."""\n\n    # cheaper than x + x\n    return x << 1\n',
    )
    changes = changed_lines(trees["baseline"], cand)
    shifted = line_of(cand, "pkg/calc.py", "    return x << 1")
    assert changes == ChangedLines(
        files=(FileChange("pkg/calc.py", (statement(shifted),), (), None),),
        not_checked=(),
        deleted=(),
    )


def test_a_continuation_line_stands_for_its_statement(tmp_path: Path) -> None:
    trees = make_trees(
        tmp_path, {"pkg/calc.py": CALC + "\nPAIR = (\n    1,\n    2,\n)\n"}
    )
    cand = trees["candidate"]
    edit(cand, "pkg/calc.py", "    2,\n)", "    3,\n)")
    (change,) = changed_lines(trees["baseline"], cand).files
    first = line_of(cand, "pkg/calc.py", "PAIR = (")
    assert change.needs == (statement(first + 2, first),)


BRANCHY = """

def lookup(key, cache):
    if key in cache:
        hits = 1
    else:
        cache[key] = key * 2
        hits = 0
    return cache[key], hits


def guarded(x):
    try:
        y = x + 1
    except TypeError:
        y = 0
    finally:
        log = []
        log.append(x)
    return y
"""


@pytest.mark.parametrize(
    ("old", "new", "site"),
    [
        # The end of a body: the statement before it in the same body,
        # not the deeper loop body.
        (
            "        result += x\n    return result\n",
            "        result += x\n",
            "    for x in xs:",
        ),
        # The start of a body: the statement after it.
        ("    result = 0\n    for", "    for", "    for x in xs:"),
        # A whole function: its neighbour at module level.
        (
            "\n\ndef shout(s):\n    return s.upper()\n",
            "",
            "def classify(x):",
        ),
        # An else clause: what is left of it, never the if branch.
        ("        cache[key] = key * 2\n", "", "        hits = 0"),
        # A finally clause: what is left of it, never the handler.
        ("        log = []\n", "", "        log.append(x)"),
    ],
    ids=["end-of-body", "start-of-body", "whole-function", "else", "finally"],
)
def test_a_deletion_is_sited_in_the_block_it_was_in(
    tmp_path: Path, old: str, new: str, site: str
) -> None:
    trees = make_trees(tmp_path, {"pkg/calc.py": CALC + BRANCHY})
    cand = trees["candidate"]
    edit(cand, "pkg/calc.py", old, new)
    (change,) = changed_lines(trees["baseline"], cand).files
    assert change.needs == ()
    (deletion,) = change.deletions
    assert deletion.needs == (statement(line_of(cand, "pkg/calc.py", site)),)


def test_a_block_with_nothing_left_cannot_be_checked(tmp_path: Path) -> None:
    trees = make_trees(tmp_path, {"pkg/calc.py": CALC + BRANCHY})
    cand = trees["candidate"]
    edit(cand, "pkg/calc.py", "    finally:\n        log = []\n", "")
    edit(cand, "pkg/calc.py", "        log.append(x)\n", "")
    (change,) = changed_lines(trees["baseline"], cand).files
    (deletion,) = change.deletions
    (need,) = deletion.needs
    assert need.kind == "unchecked"
    assert "no statement is left in the finalbody" in need.why
    result = gate(trees, make_spec(), ids=(EXIT_STATUS_ID,))
    assert result.status == "not_available"
    assert "line coverage cannot check every change" in result.detail


def test_deleted_comments_ask_for_nothing(tmp_path: Path) -> None:
    calc = CALC.replace("    return 2 * x", "    # two\n    return 2 * x")
    trees = make_trees(tmp_path, {"pkg/calc.py": calc})
    cand = trees["candidate"]
    edit(cand, "pkg/calc.py", "    # two\n", "")
    assert changed_lines(trees["baseline"], cand).files == ()


def test_new_deleted_untracked_and_non_python_files(tmp_path: Path) -> None:
    trees = make_trees(tmp_path, {"pkg/old.py": "X = 1\n"})
    cand = trees["candidate"]
    (cand / "pkg/old.py").unlink()
    (cand / "pkg/fast.py").write_text(
        '"""Fast."""\n\n\ndef f(x):\n    return (x +\n            1)\n'
    )
    (cand / "pkg/_fast.c").write_text("int f(int x) { return x << 1; }\n")
    (cand / ".gitattributes").write_text("*.py text\n")
    (cand / "out.txt").write_text("a declared output\n")
    edit(cand, "README.md", "calc", "calc, faster")
    changes = changed_lines(trees["baseline"], cand, outputs=("out.txt",))
    assert changes == ChangedLines(
        files=(
            FileChange(
                "pkg/fast.py",
                (statement(4), statement(5), statement(6, 5)),
                (),
                None,
            ),
        ),
        not_checked=(".gitattributes", "README.md", "pkg/_fast.c"),
        deleted=("pkg/old.py",),
        other=(
            OtherChange(".gitattributes", True),
            OtherChange("README.md", False),
            OtherChange("pkg/_fast.c", True),
        ),
    )


def test_a_minus_diff_attribute_does_not_hide_a_change(tmp_path: Path) -> None:
    trees = make_trees(tmp_path, {".gitattributes": "pkg/calc.py -diff\n"})
    cand = trees["candidate"]
    edit(cand, "pkg/calc.py", "s.upper()", "s.lower()")
    result = gate(trees, make_spec(), ids=(EXIT_STATUS_ID,))
    line = line_of(cand, "pkg/calc.py", "    return s.lower()")
    assert result.status == "not_available", result.detail
    assert unverified(result) == {"pkg/calc.py": (line,)}


def test_an_unparseable_file_is_not_available(tmp_path: Path) -> None:
    trees = make_trees(tmp_path)
    cand = trees["candidate"]
    edit(cand, "pkg/calc.py", "    return s.upper()", "    return s.upper(\n")
    (change,) = changed_lines(trees["baseline"], cand).files
    assert change.unparsed is not None
    assert change.unparsed.startswith("the candidate could not be parsed")
    result = gate(trees, make_spec(), ids=(EXIT_STATUS_ID,))
    assert result.status == "not_available"
    assert "pkg/calc.py: the candidate could not be parsed" in result.detail


def test_a_parser_that_breaks_leaves_the_file_unchecked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trees = make_trees(tmp_path)
    edit(trees["candidate"], "pkg/calc.py", "2 * x", "x + x")

    def broken(self: coverage.parser.PythonParser) -> None:
        raise AttributeError("no attribute 'show_tokens'")

    monkeypatch.setattr(coverage.parser.PythonParser, "parse_source", broken)
    result = gate(trees, make_spec(), ids=(EXIT_STATUS_ID,))
    assert result.status == "not_available"
    assert "pkg/calc.py: the candidate could not be parsed" in result.detail
    assert "AttributeError" in result.detail


# --------------------------------------------------------------------
# The gate, under real coverage.py
# --------------------------------------------------------------------


def test_a_fully_covered_change_passes(tmp_path: Path) -> None:
    trees = make_trees(tmp_path)
    edit(trees["candidate"], "pkg/calc.py", "2 * x", "x + x")
    result = gate(trees, make_spec(), ids=(EXIT_STATUS_ID,))
    assert result.status == "passed", result.detail
    assert "1 changed executable lines and 0 deletions" in result.detail


def test_a_change_in_a_branch_no_check_reaches_is_unverified(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    cand = trees["candidate"]
    edit(cand, "pkg/calc.py", 'return "negative"', 'return "NEGATIVE"')
    edit(cand, "pkg/calc.py", "2 * x", "x + x")
    result = gate(trees, make_spec(), ids=(EXIT_STATUS_ID,))
    line = line_of(cand, "pkg/calc.py", '    return "NEGATIVE"')
    assert result.status == "not_available"
    assert unverified(result) == {"pkg/calc.py": (line,)}
    assert f"pkg/calc.py lines {line} never ran" in result.detail


def test_a_deleted_line_needs_its_block_to_run(tmp_path: Path) -> None:
    calc = CALC.replace(
        "    return s.upper()\n", "    s = s.upper()\n    return s\n"
    ).replace("    result = 0\n", "    result = 0\n    x = 0\n")
    trees = make_trees(tmp_path, {"pkg/calc.py": calc + BRANCHY})
    cand = trees["candidate"]
    checks = CHECKS + "assert calc.lookup(1, {1: 2}) == (2, 1)\n"
    edit(cand, "checks.py", CHECKS, checks)
    git(cand, "commit", "-q", "-am", "checks reach lookup")
    trees["baseline"] = add_baseline_worktree(
        cand, resolve_commit(cand, "HEAD"), "checked"
    )
    spec, ids = make_spec(), (EXIT_STATUS_ID,)
    # Deleted where the checks run: total runs.
    edit(cand, "pkg/calc.py", "    x = 0\n", "")
    assert gate(trees, spec, ids=ids).status == "passed"
    # Deleted where no check runs: shout is never called, and lookup's
    # else clause never runs though its if branch does.
    edit(cand, "pkg/calc.py", "    s = s.upper()\n", "")
    edit(cand, "pkg/calc.py", "        cache[key] = key * 2\n", "")
    result = gate(trees, spec, ids=ids)
    shout = line_of(cand, "pkg/calc.py", "    return s")
    other = line_of(cand, "pkg/calc.py", "        hits = 0")
    assert result.status == "not_available", result.detail
    assert unverified(result) == {"pkg/calc.py": (shout, other)}
    assert f"deleted in the block of line {other}" in result.detail


LAMBDAS = """

HANDLERS = {
    "up": lambda s: s.upper(),
    "down": lambda s: (
        s.lower()
    ),
    "same": lambda s: s,
}
"""


def test_a_lambda_must_be_called_not_just_defined(tmp_path: Path) -> None:
    trees = make_trees(tmp_path, {"pkg/calc.py": CALC + LAMBDAS})
    cand = trees["candidate"]
    checks = CHECKS + 'assert calc.HANDLERS["same"]("a") == "a"\n'
    edit(cand, "checks.py", CHECKS, checks)
    git(cand, "commit", "-q", "-am", "checks call one lambda")
    trees["baseline"] = add_baseline_worktree(
        cand, resolve_commit(cand, "HEAD"), "checked"
    )
    edit(
        cand,
        "pkg/calc.py",
        "lambda s: s.upper()",
        "lambda s: s.upper().strip()",
    )
    edit(cand, "pkg/calc.py", "        s.lower()", "        s.lower().strip()")
    edit(cand, "pkg/calc.py", "lambda s: s,", "lambda s: s[:],")
    up = line_of(cand, "pkg/calc.py", '    "up": lambda s: s.upper().strip(),')
    down = line_of(cand, "pkg/calc.py", "        s.lower().strip()")
    (change,) = changed_lines(trees["baseline"], cand).files
    assert [n.kind for n in change.needs] == ["entry"] * 3
    result = gate(trees, make_spec(), ids=(EXIT_STATUS_ID,))
    assert result.status == "not_available", result.detail
    assert unverified(result) == {"pkg/calc.py": (up, down)}


def test_lambdas_sharing_a_line_cannot_be_checked(tmp_path: Path) -> None:
    calc = CALC + "\nPAIR = (lambda: 1, lambda: 2)\n"
    trees = make_trees(tmp_path, {"pkg/calc.py": calc})
    edit(trees["candidate"], "pkg/calc.py", "lambda: 2)", "lambda: 3)")
    (change,) = changed_lines(trees["baseline"], trees["candidate"]).files
    assert [n.kind for n in change.needs] == ["unchecked"]
    result = gate(trees, make_spec(), ids=(EXIT_STATUS_ID,))
    assert result.status == "not_available"


def test_a_change_reached_only_by_a_quarantined_test_is_unverified(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    cand = trees["candidate"]
    edit(cand, "pkg/calc.py", "s.upper()", "s.upper().strip()")
    edit(cand, "pkg/calc.py", "2 * x", "x + x")
    spec = make_spec(PYTEST)
    flaky = ("test_calc::test_shout", *NOT_TRACED)
    result = gate(trees, spec, quarantined=flaky)
    line = line_of(cand, "pkg/calc.py", "    return s.upper().strip()")
    assert result.status == "not_available", result.detail
    assert unverified(result) == {"pkg/calc.py": (line,)}
    assert "ran only under tests left out of the judgement" in result.detail
    # The same lines, with nothing quarantined, ran under passing tests.
    assert gate(trees, spec, known=NOT_TRACED).status == "passed"


def test_a_passing_test_the_judgement_did_not_use_verifies_nothing(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    edit(trees["candidate"], "pkg/calc.py", "s.upper()", "s.upper().strip()")
    ids = [t for t in TEST_IDS if t != "test_calc::test_shout"]
    result = gate(trees, make_spec(PYTEST), known=NOT_TRACED, ids=ids)
    assert result.status == "not_available", result.detail


def test_lines_a_test_runs_in_a_child_process_are_the_tests(
    tmp_path: Path,
) -> None:
    # The fast suite runs in perfproof's own environment, where
    # coverage.py's a1_coverage.pth starts it before the hook does.
    # The suite's fixture moves sys.prefix; exec_prefix is the real one.
    base = {"base": sys.exec_prefix, "platbase": sys.exec_prefix}
    purelib = Path(sysconfig.get_paths(vars=base)["purelib"])
    assert (purelib / "a1_coverage.pth").exists()
    trees = make_trees(tmp_path)
    edit(
        trees["candidate"], "pkg/calc.py", "result += x", "result = result + x"
    )
    spec = make_spec(PYTEST)
    # A test was left out, so unlabelled lines do not count: these pass
    # only because the child recorded them under its test's label.
    assert gate(trees, spec, known=NOT_TRACED).status == "passed"
    child = ("test_calc::test_total_in_a_child",)
    result = gate(trees, spec, quarantined=child, known=NOT_TRACED)
    assert result.status == "not_available", result.detail


SERVER = """\
import sys

from pkg import calc

for line in sys.stdin:
    name, arg = line.split()
    print(getattr(calc, name)(arg), flush=True)
"""

SERVER_TESTS = """\
import subprocess
import sys

import pytest

from pkg import calc


@pytest.fixture(scope="session")
def server():
    process = subprocess.Popen(
        [sys.executable, "server.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    yield process
    process.stdin.close()
    process.wait(timeout=60)


def test_double(server):
    assert calc.double(2) == 4


def test_shout_flaky(server):
    server.stdin.write("shout a\\n")
    server.stdin.flush()
    assert server.stdout.readline().strip() == "A"
"""


def test_a_child_that_outlives_its_test_is_unlabelled(tmp_path: Path) -> None:
    trees = make_trees(
        tmp_path, {"test_calc.py": SERVER_TESTS, "server.py": SERVER}
    )
    cand = trees["candidate"]
    edit(cand, "pkg/calc.py", "s.upper()", "s.upper().strip()")
    ids = ("test_calc::test_double", "test_calc::test_shout_flaky")
    result = gate(
        trees,
        make_spec(PYTEST),
        quarantined=("test_calc::test_shout_flaky",),
        ids=ids,
    )
    line = line_of(cand, "pkg/calc.py", "    return s.upper().strip()")
    assert result.status == "not_available", result.detail
    assert unverified(result) == {"pkg/calc.py": (line,)}
    # It ran, in the server, labelled with the test that started it.
    assert "processes that outlived them" in result.detail


def test_a_judged_test_failing_under_coverage_is_not_blamed(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    edit(trees["candidate"], "pkg/calc.py", 'return "negative"', "return 0")
    result = gate(trees, make_spec(PYTEST))
    assert result.status == "not_available"
    assert result.unverified == ()
    assert (
        "1 judged tests did not pass under coverage: "
        "test_calc::test_not_traced" in result.detail
    )


SPOIL = """\
import configparser
import os
from pathlib import Path

config = configparser.ConfigParser()
config.read(os.environ["COVERAGE_PROCESS_START"])
data = Path(config["run"]["data_file"])
data.with_name(data.name + ".spoilt").write_text("not a database")
"""


def test_unreadable_coverage_data_is_not_blamed(tmp_path: Path) -> None:
    trees = make_trees(tmp_path, {"spoil.py": SPOIL})
    edit(trees["candidate"], "pkg/calc.py", 'return "negative"', "return 0")
    result = gate(trees, make_spec((PY, "spoil.py")), ids=(EXIT_STATUS_ID,))
    assert result.status == "not_available"
    assert "coverage data cov.spoilt is unreadable" in result.detail


def test_unlabelled_lines_do_not_count_when_a_test_was_left_out(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    edit(trees["candidate"], "pkg/calc.py", "2 * x", "x + x")
    spec = make_spec()
    assert gate(trees, spec, ids=(EXIT_STATUS_ID,)).status == "passed"
    result = gate(trees, spec, known=(EXIT_STATUS_ID,), ids=(EXIT_STATUS_ID,))
    assert result.status == "not_available"
    assert "ran only under tests left out" in result.detail


def test_a_non_python_change_needs_the_opt_in(tmp_path: Path) -> None:
    trees = make_trees(tmp_path, {"native.c": "int f(void) { return 1; }\n"})
    cand = trees["candidate"]
    edit(cand, "native.c", "return 1", "return 2")
    ids = (EXIT_STATUS_ID,)
    result = gate(trees, make_spec(), ids=ids)
    assert result.status == "not_available"
    assert "coverage not checked for non-Python changes: native.c" in (
        result.detail
    )
    assert "allow_unchecked_coverage" in result.detail
    waived = gate(trees, make_spec(allow_unchecked_coverage=True), ids=ids)
    assert waived.status == "waived"
    assert "waived by allow_unchecked_coverage" in waived.detail
    # A Python line no check ran stays not_available, opt-in or not.
    edit(cand, "pkg/calc.py", "s.upper()", "s.lower()")
    failed = gate(trees, make_spec(allow_unchecked_coverage=True), ids=ids)
    assert failed.status == "not_available"


def test_an_untracked_file_is_part_of_the_candidate(tmp_path: Path) -> None:
    trees = make_trees(tmp_path)
    cand = trees["candidate"]
    edit(cand, "pkg/calc.py", "2 * x", "x + x")
    (cand / "out.txt").write_text("written by the benchmark\n")
    spec = make_spec(outputs=("out.txt",))
    assert gate(trees, spec, ids=(EXIT_STATUS_ID,)).status == "passed"
    (cand / "pkg/_fast.c").write_text("int f(int x) { return x << 1; }\n")
    result = gate(trees, spec, ids=(EXIT_STATUS_ID,))
    assert result.status == "not_available"
    assert "pkg/_fast.c" in result.detail


def test_files_the_runs_create_are_not_the_candidates(tmp_path: Path) -> None:
    # No __pycache__ ignore: the runs leave bytecode the tree never had.
    trees = make_trees(tmp_path, {".gitignore": ""})
    cand = trees["candidate"]
    # An untracked module the candidate brings is still its change.
    (cand / "pkg/fast.py").write_text("def one():\n    return 1\n")
    edit(
        cand,
        "pkg/calc.py",
        "\n\ndef double",
        "\nfrom pkg import fast\n\n\ndef double",
    )
    edit(cand, "pkg/calc.py", "2 * x", "fast.one() * 2 * x")
    before = untracked_files(cand)
    assert before == ("pkg/fast.py",)
    spec = make_spec(PYTEST)
    check_tests(
        spec,
        baseline_root=trees["baseline"],
        candidate_root=cand,
        env=benchmark_environment(spec, os.environ),
    )
    assert list(cand.rglob("__pycache__"))
    result = gate(trees, spec, known=NOT_TRACED, untracked=before)
    assert result.status == "passed", result.detail
    assert "in 2 Python files" in result.detail
    # Bytecode is generated, so listed after the runs it is no change.
    assert untracked_files(cand) == ("pkg/fast.py",)
    late = gate(trees, spec, known=NOT_TRACED)
    assert late.status == "passed", late.detail
    assert "__pycache__" not in late.detail


def test_no_change_passes_without_running_anything(tmp_path: Path) -> None:
    trees = make_trees(tmp_path)
    result = gate(trees, make_spec(("false",)))
    assert result.status == "passed"
    assert "0 changed executable lines and 0 deletions" in result.detail


def test_without_a_correctness_command_nothing_is_measured(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    edit(trees["candidate"], "pkg/calc.py", "2 * x", "x + x")
    spec = make_spec(None)
    result = coverage_gate(
        spec,
        baseline_root=trees["baseline"],
        candidate_root=trees["candidate"],
        env={},
        tests=None,
        judged_tests=None,
    )
    assert result.status == "not_available"
    assert "no correctness command" in result.detail


def test_a_run_that_times_out_under_coverage_is_not_available(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    edit(trees["candidate"], "pkg/calc.py", "2 * x", "x + x")
    spec = make_spec((PY, "-c", "import time; time.sleep(30)"), timeout=1.0)
    result = gate(trees, spec, ids=(EXIT_STATUS_ID,), timeout_factor=0.5)
    assert result.status == "not_available"
    assert "timed out after 0.5 s under coverage" in result.detail


def test_judgement_is_required_with_a_command(tmp_path: Path) -> None:
    trees = make_trees(tmp_path)
    with pytest.raises(ValueError, match="judgement is required"):
        coverage_gate(
            make_spec(),
            baseline_root=trees["baseline"],
            candidate_root=trees["candidate"],
            env={},
            tests=judged(),
            judged_tests=None,
        )


def test_an_unchecked_deletion_is_reported() -> None:
    need = Need(3, "unchecked", 3, "no statement is left in the orelse")
    change = FileChange("a.py", (), (Deletion((4, 5), (need,)),), None)
    result = coverage_gate(
        make_spec(),
        baseline_root=Path("unused"),
        candidate_root=Path("unused"),
        env={},
        tests=judged(),
        judged_tests=(),
        changes=ChangedLines((change,), (), ()),
    )
    assert result.status == "not_available"
    assert "a.py: no statement is left in the orelse" in result.detail


# --------------------------------------------------------------------
# Integration: the whole chain, forks, and another interpreter
# --------------------------------------------------------------------

FLAKY_TESTS = """\
import os
from pathlib import Path

from pkg import calc


def test_double():
    assert calc.double(2) == 4


def test_shout_flaky():
    counter = Path(os.environ["COUNTER"])
    runs = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(runs + 1))
    assert calc.shout("a") == "A"
    assert runs != 1
"""


@pytest.mark.integration
def test_a_really_flaky_test_does_not_verify_its_lines(tmp_path: Path) -> None:
    trees = make_trees(tmp_path, {"test_calc.py": FLAKY_TESTS})
    cand = trees["candidate"]
    edit(cand, "pkg/calc.py", "s.upper()", "s.upper().strip()")
    edit(cand, "pkg/calc.py", "2 * x", "x + x")
    spec = make_spec(PYTEST, env={"COUNTER": str(tmp_path / "counter")})
    env = benchmark_environment(spec, os.environ)
    tests = check_tests(
        spec,
        baseline_root=trees["baseline"],
        candidate_root=cand,
        env=env,
    )
    assert [q.test_id for q in tests.quarantined] == [
        "test_calc::test_shout_flaky"
    ]
    result = coverage_gate(
        spec,
        baseline_root=trees["baseline"],
        candidate_root=cand,
        env=env,
        tests=tests,
        judged_tests=("test_calc::test_double", EXIT_STATUS_ID),
    )
    line = line_of(cand, "pkg/calc.py", "    return s.upper().strip()")
    assert result.status == "not_available", result.detail
    assert unverified(result) == {"pkg/calc.py": (line,)}


FORK_TESTS = """\
import multiprocessing

from pkg import calc


def _work(queue):
    queue.put(calc.total([1, 2]))


def test_total_in_a_fork():
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    process = context.Process(target=_work, args=(queue,))
    process.start()
    assert queue.get(timeout=60) == 3
    process.join(60)
    assert process.exitcode == 0


def test_double():
    assert calc.double(2) == 4
"""


@pytest.mark.integration
def test_lines_a_forked_child_runs_are_its_tests(tmp_path: Path) -> None:
    trees = make_trees(tmp_path, {"test_calc.py": FORK_TESTS})
    edit(trees["candidate"], "pkg/calc.py", "result += x", "result += x * 1")
    spec = make_spec(PYTEST)
    ids = ("test_calc::test_total_in_a_fork", "test_calc::test_double")
    # With a test left out, unlabelled lines do not count.
    known = ("test_calc::test_double",)
    result = gate(trees, spec, known=known, ids=ids)
    assert result.status == "passed", result.detail
    fork = ("test_calc::test_total_in_a_fork",)
    result = gate(trees, spec, quarantined=fork, known=known, ids=ids)
    assert result.status == "not_available", result.detail


def _other_python() -> str | None:
    for name in ("python3.13", "python3.14", "python3.12"):
        found = shutil.which(name)
        if found is None:
            continue
        version = subprocess.run(
            [found, "-c", "import sys; print(sys.version_info[:2])"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout.strip()
        if version != str(sys.version_info[:2]):
            return found
    return None


@pytest.mark.integration
def test_coverage_runs_in_another_interpreter(tmp_path: Path) -> None:
    other = _other_python()
    if other is None:
        pytest.skip("no Python other than perfproof's own on PATH")
    trees = make_trees(tmp_path)
    cand = trees["candidate"]
    edit(cand, "pkg/calc.py", "2 * x", "x + x")
    spec = make_spec((other, "checks.py"))
    ids = (EXIT_STATUS_ID,)
    assert gate(trees, spec, ids=ids).status == "passed"
    edit(cand, "pkg/calc.py", 'return "negative"', "return 0")
    result = gate(trees, spec, ids=ids)
    line = line_of(cand, "pkg/calc.py", "    return 0")
    assert result.status == "not_available", result.detail
    assert unverified(result) == {"pkg/calc.py": (line,)}


WRITE_REPORT = """\
import os
import shutil

shutil.copy("report.info", os.environ["PERFPROOF_COVERAGE_REPORT"])
"""
NATIVE = "int f(void) { return 1; }\n"


def native_trees(tmp_path: Path, report: str | None) -> dict[Arm, Path]:
    """Make trees with native.c changed, and a report to write."""
    files = {"native.c": NATIVE, "report.py": WRITE_REPORT}
    if report is not None:
        files["report.info"] = report
    trees = make_trees(tmp_path, files)
    edit(trees["candidate"], "native.c", "return 1", "return 2")
    return trees


def report_spec(**changes: Any) -> BenchmarkSpec:
    return make_spec(coverage_command=(PY, "report.py"), **changes)


def test_a_changed_native_line_the_report_shows_ran_passes(
    tmp_path: Path,
) -> None:
    trees = native_trees(tmp_path, "SF:native.c\nDA:1,3\nend_of_record\n")
    result = gate(trees, report_spec(), ids=(EXIT_STATUS_ID,))
    assert result.status == "passed", result.detail
    assert "1 non-Python files checked from the coverage report" in (
        result.detail
    )


def test_a_changed_native_line_the_report_shows_never_ran(
    tmp_path: Path,
) -> None:
    trees = native_trees(tmp_path, "SF:native.c\nDA:1,0\nend_of_record\n")
    result = gate(trees, report_spec(), ids=(EXIT_STATUS_ID,))
    assert result.status == "not_available"
    assert unverified(result) == {"native.c": (1,)}
    assert "native.c lines 1 never ran" in result.detail


JACOCO = Path(__file__).resolve().parents[1] / "fixtures" / "java" / "jacoco"
JAVA_FILE = "src/main/java/com/example/Calc.java"


def java_trees(tmp_path: Path) -> dict[Arm, Path]:
    """Trees holding the fixture's Java source and its real report."""
    return make_trees(
        tmp_path,
        {
            JAVA_FILE: (JACOCO / "Calc.java").read_text(),
            "report.py": WRITE_REPORT,
            "report.info": (JACOCO / "report.xml").read_text(),
        },
    )


def test_a_changed_java_line_the_jacoco_report_shows_ran_passes(
    tmp_path: Path,
) -> None:
    trees = java_trees(tmp_path)
    edit(trees["candidate"], JAVA_FILE, "a + b", "b + a")
    result = gate(trees, report_spec(), ids=(EXIT_STATUS_ID,))
    assert result.status == "passed", result.detail
    assert "1 non-Python files checked from the coverage report" in (
        result.detail
    )


def test_a_changed_java_line_the_jacoco_report_shows_never_ran(
    tmp_path: Path,
) -> None:
    trees = java_trees(tmp_path)
    edit(trees["candidate"], JAVA_FILE, "x * 2", "2 * x")
    result = gate(trees, report_spec(), ids=(EXIT_STATUS_ID,))
    line = line_of(trees["candidate"], JAVA_FILE, "        return 2 * x;")
    assert result.status == "not_available", result.detail
    assert unverified(result) == {JAVA_FILE: (line,)}


def test_a_changed_line_the_report_does_not_list_is_not_executable(
    tmp_path: Path,
) -> None:
    trees = native_trees(tmp_path, "SF:native.c\nDA:2,0\nend_of_record\n")
    result = gate(trees, report_spec(), ids=(EXIT_STATUS_ID,))
    assert result.status == "passed", result.detail


def test_a_file_the_report_does_not_list_is_not_checked(
    tmp_path: Path,
) -> None:
    trees = native_trees(tmp_path, "SF:other.c\nDA:1,1\nend_of_record\n")
    result = gate(trees, report_spec(), ids=(EXIT_STATUS_ID,))
    assert result.status == "not_available"
    assert result.unverified == ()
    assert "coverage not checked for non-Python changes: native.c" in (
        result.detail
    )
    opted_in = report_spec(allow_unchecked_coverage=True)
    waived = gate(trees, opted_in, ids=(EXIT_STATUS_ID,))
    assert waived.status == "waived"


def test_no_coverage_command_means_no_extra_run(tmp_path: Path) -> None:
    ran = "from pathlib import Path\nPath('ran.marker').write_text('x')\n"
    trees = make_trees(tmp_path, {"native.c": NATIVE, "ran.py": ran})
    edit(trees["candidate"], "native.c", "return 1", "return 2")
    result = gate(trees, make_spec((PY, "ran.py")), ids=(EXIT_STATUS_ID,))
    assert result.status == "not_available"
    assert "no coverage command declared" in result.detail
    assert not (trees["candidate"] / "ran.marker").exists()


def test_a_coverage_command_that_writes_no_report_says_so(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path, {"native.c": NATIVE})
    edit(trees["candidate"], "native.c", "return 1", "return 2")
    spec = make_spec(coverage_command=(PY, "-c", "pass"))
    result = gate(trees, spec, ids=(EXIT_STATUS_ID,))
    assert result.status == "not_available"
    assert "wrote no report to $PERFPROOF_COVERAGE_REPORT" in result.detail


def test_a_failing_coverage_command_cannot_verify_lines(
    tmp_path: Path,
) -> None:
    trees = native_trees(tmp_path, "SF:native.c\nDA:1,3\nend_of_record\n")
    failing = (
        PY,
        "-c",
        "import os, shutil, sys\n"
        "shutil.copy('report.info', os.environ['PERFPROOF_COVERAGE_REPORT'])\n"
        "sys.exit(3)",
    )
    spec = make_spec(coverage_command=failing)
    result = gate(trees, spec, ids=(EXIT_STATUS_ID,))
    assert result.status == "not_available"
    assert "the coverage command exited non-zero" in result.detail


def test_a_coverage_command_that_times_out_is_not_available(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path, {"native.c": NATIVE})
    edit(trees["candidate"], "native.c", "return 1", "return 2")
    sleeper = (PY, "-c", "import time; time.sleep(30)")
    spec = make_spec(coverage_command=sleeper, timeout=1.0)
    result = gate(trees, spec, ids=(EXIT_STATUS_ID,))
    assert result.status == "not_available"
    assert "the coverage command timed out" in result.detail


def test_a_python_only_change_never_runs_the_coverage_command(
    tmp_path: Path,
) -> None:
    ran = "from pathlib import Path\nPath('ran.marker').write_text('x')\n"
    trees = make_trees(tmp_path, {"ran.py": ran})
    edit(trees["candidate"], "pkg/calc.py", "2 * x", "x + x")
    spec = make_spec(coverage_command=(PY, "ran.py"))
    result = gate(trees, spec, ids=(EXIT_STATUS_ID,))
    assert result.status == "passed", result.detail
    assert not (trees["candidate"] / "ran.marker").exists()


def test_an_unreadable_report_is_no_report(tmp_path: Path) -> None:
    trees = native_trees(tmp_path, "SF:native.c\nDA:one,1\nend_of_record\n")
    result = gate(trees, report_spec(), ids=(EXIT_STATUS_ID,))
    assert result.status == "not_available"
    assert "the coverage report is unreadable" in result.detail
    assert "coverage not checked for non-Python changes" in result.detail


def test_hits_do_not_verify_when_a_test_was_left_out(tmp_path: Path) -> None:
    trees = native_trees(tmp_path, "SF:native.c\nDA:1,3\nend_of_record\n")
    result = gate(
        trees,
        report_spec(),
        ids=(EXIT_STATUS_ID,),
        quarantined=("test_calc::test_shout",),
    )
    assert result.status == "not_available"
    assert unverified(result) == {"native.c": (1,)}
    assert "may have run only for those" in result.detail


PROBE = """\
import os
import sys

path = os.environ["PERFPROOF_COVERAGE_REPORT"]
sys.exit(0 if path != "/not/this" and not os.path.exists(path) else 1)
"""


def test_the_report_path_is_perfproofs_not_the_environments(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path, {"native.c": NATIVE, "probe.py": PROBE})
    edit(trees["candidate"], "native.c", "return 1", "return 2")
    spec = make_spec(
        coverage_command=(PY, "probe.py"),
        env={"PERFPROOF_COVERAGE_REPORT": "/not/this"},
    )
    result = gate(trees, spec, ids=(EXIT_STATUS_ID,))
    assert "no passing check" not in result.detail
    assert "exit" not in result.detail
    assert "wrote no report" in result.detail


def test_every_line_of_a_new_native_file_counts(tmp_path: Path) -> None:
    report = "SF:new.c\nDA:1,1\nDA:3,0\nend_of_record\n"
    files = {"report.py": WRITE_REPORT, "report.info": report}
    trees = make_trees(tmp_path, files)
    (trees["candidate"] / "new.c").write_text("a;\nb;\nc;")
    result = gate(trees, report_spec(), ids=(EXIT_STATUS_ID,))
    assert result.status == "not_available"
    assert unverified(result) == {"new.c": (3,)}


def test_a_python_file_name_the_record_cannot_hold_still_withholds(
    tmp_path: Path,
) -> None:
    odd = "pkg/back\\slash.py"
    body = "def one():\n    return 1\n"
    trees = make_trees(tmp_path, {odd: body})
    edit(trees["candidate"], odd, "return 1", "return 2")
    result = gate(trees, make_spec(), ids=(EXIT_STATUS_ID,))
    assert result.status == "not_available"
    assert result.unverified == ()
    assert "cannot be recorded" in result.detail
    assert "never ran" in result.detail


def test_a_native_file_name_the_record_cannot_hold_still_withholds(
    tmp_path: Path,
) -> None:
    odd = "na\\tive.c"
    files = {
        odd: NATIVE,
        "report.py": WRITE_REPORT,
        "report.info": f"SF:{odd}\nDA:1,0\nend_of_record\n",
    }
    trees = make_trees(tmp_path, files)
    edit(trees["candidate"], odd, "return 1", "return 2")
    result = gate(trees, report_spec(), ids=(EXIT_STATUS_ID,))
    assert result.status == "not_available"
    assert result.unverified == ()
    assert "cannot be recorded" in result.detail


def test_a_report_with_a_nul_path_is_no_report(tmp_path: Path) -> None:
    trees = native_trees(tmp_path, "SF:a\0b.c\nDA:1,1\nend_of_record\n")
    result = gate(trees, report_spec(), ids=(EXIT_STATUS_ID,))
    assert result.status == "not_available"
    assert "the coverage report is unreadable" in result.detail
