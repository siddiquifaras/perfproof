import os
import pickle
import subprocess
import sys
import textwrap
import time
from collections.abc import Mapping
from fractions import Fraction
from pathlib import Path

import pytest

from perfproof.bench import differential
from perfproof.bench.differential import (
    CaptureError,
    ChangedFunction,
    DifferentialResult,
    NotChecked,
    _is_library,  # pyright: ignore[reportPrivateUsage]
    changed_functions,
    check,
)
from perfproof.bench.runner import Runner
from perfproof.bench.snapshot import add_baseline_worktree, resolve_commit
from perfproof.schema import (
    Arm,
    BenchmarkSpec,
    Budget,
    CorrectnessSpec,
    DifferentialCheck,
    GateResult,
    LookSchedule,
    MetricSpec,
    SkippedCalls,
)

PY = sys.executable

CALC = """\
import random
from fractions import Fraction


def square(x: int) -> int:
    return x * x


def absval(x: int) -> int:
    return x if x >= 0 else -x


def head(fh) -> str:
    return fh.readline()


class Holder:
    def __init__(self):
        self.fh = open(__file__)

    def size(self) -> int:
        return 1


class Tally:
    def __init__(self):
        self.n = 0

    def bump(self, k: int) -> int:
        self.n += k
        return self.n


def evens(n: int):
    for i in range(n):
        if i % 2 == 0:
            yield i


def save(path: str, value: int) -> None:
    with open(path, "w") as f:
        f.write(str(value))


def append_one(xs: list) -> None:
    xs.append(1)


def noisy() -> float:
    return random.random()


class Box:
    def __init__(self, v):
        self.v = v


def boxed(x: int) -> Box:
    return Box(x)


def lazy_double(x: int) -> int:
    from pkg import helper

    return helper.double(x)


def libc_abs(x: int) -> int:
    import ctypes

    return ctypes.CDLL(None).abs(x)


def half(n: int) -> Fraction:
    return Fraction(1, 2)
"""

BENCH = """\
import os
import sys

from pkg import calc

mark = os.environ.get("MARK")
if mark:
    with open(mark, "a") as f:
        f.write(f"{'_perfproof_differential' in sys.modules}\\n")
for i in range(200):
    calc.square(i)
for i in range(30):
    calc.absval(i)
with open("data.txt") as fh:
    for _ in range(3):
        calc.head(fh)
calc.Holder().size()
tally = calc.Tally()
for k in range(5):
    tally.bump(k)
for _ in range(4):
    list(calc.evens(6))
calc.save("out.txt", 3)
calc.append_one([0])
calc.noisy()
for i in range(5):
    calc.boxed(i)
calc.lazy_double(3)
calc.libc_abs(-3)
for n in range(1, 4):
    calc.half(n)
"""


NUMPY_FILES = {
    "pkg/arr.py": "def total(xs):\n    return xs.sum()\n",
    "pkg/means.py": (
        "import numpy as np\n\n\n"
        "def mean(xs: list) -> float:\n"
        "    return float(np.mean(xs))\n"
    ),
    "bench.py": (
        "import numpy as np\n\n"
        "from pkg import arr, means\n\n"
        "for i in range(10):\n"
        "    arr.total(np.arange(i + 1))\n"
        "    means.mean([1.0, 2.0, float(i)])\n"
    ),
}


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
        "pkg/__init__.py": "",
        "pkg/calc.py": CALC,
        "pkg/helper.py": "def double(x):\n    return 2 * x\n",
        "bench.py": BENCH,
        "data.txt": "a\nb\nc\n",
        **(files or {}),
    }
    for name, text in contents.items():
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
    command: tuple[str, ...] = (PY, "bench.py"),
    env: Mapping[str, str] | None = None,
    timeout: float = 30.0,
) -> BenchmarkSpec:
    return BenchmarkSpec(
        name="bench",
        command=command,
        env=dict(env or {}),
        metric=MetricSpec(kind="wall", direction="lower_is_better", unit="s"),
        correctness=CorrectnessSpec(command=("true",), timeout_seconds=10.0),
        warmup=0,
        budget=Budget(max_rounds=20, max_seconds=600.0),
        looks=LookSchedule(n0=10, k=10, max_looks=3),
        timeout_seconds=timeout,
        harness=("bench.py",),
    )


def by_function(result: DifferentialResult) -> dict[str, DifferentialCheck]:
    return {c.function: c for c in result.checks}


def skips(c: DifferentialCheck, stage: str) -> dict[str, int]:
    return {s.reason: s.count for s in c.skipped if s.stage == stage}


def test_changed_functions_maps_diff_lines_to_qualified_names(
    tmp_path: Path,
) -> None:
    source = textwrap.dedent(
        """\
        import functools

        LIMIT = 3


        def outer(x):
            def inner(y):
                return y + 1

            return inner(x)


        class Box:
            size = 1

            @staticmethod
            def make(v):
                return v

            def get(self):
                return self


        def gone():
            return 0


        def same():
            return 1
        """
    )
    trees = make_trees(tmp_path, {"pkg/mod.py": source})
    cand = trees["candidate"]
    edit(cand, "pkg/mod.py", "return y + 1", "return 1 + y")
    edit(cand, "pkg/mod.py", "LIMIT = 3", "LIMIT = 4")
    edit(cand, "pkg/mod.py", "    @staticmethod\n", "    @staticmethod  # s\n")
    edit(cand, "pkg/mod.py", "return self\n", "return (self)\n")
    edit(cand, "pkg/mod.py", "size = 1", "size = 2")
    edit(
        cand,
        "pkg/mod.py",
        "def gone():\n    return 0\n",
        "def added():\n    return 0\n",
    )
    (cand / "pkg" / "new.py").write_text("def f():\n    return 1\n")
    git(cand, "add", "pkg/new.py")
    (cand / "data.txt").write_text("changed\n")

    changes = changed_functions(trees["baseline"], cand)

    assert changes.functions == (
        ChangedFunction("pkg/mod.py", "Box.get"),
        ChangedFunction("pkg/mod.py", "Box.make"),
        ChangedFunction("pkg/mod.py", "outer"),
    )
    assert set(changes.not_checked) == {
        NotChecked(
            "pkg/mod.py",
            "added",
            "only in the candidate; no baseline version to compare",
        ),
        NotChecked("pkg/mod.py", "gone", "removed in the candidate"),
        NotChecked(
            "pkg/mod.py",
            "candidate lines 3",
            "module or class level code, outside any function",
        ),
        NotChecked(
            "pkg/mod.py",
            "candidate lines 14",
            "module or class level code, outside any function",
        ),
        NotChecked(
            "pkg/mod.py",
            "baseline lines 3",
            "module or class level code, outside any function",
        ),
        NotChecked(
            "pkg/mod.py",
            "baseline lines 14",
            "module or class level code, outside any function",
        ),
        NotChecked(
            "pkg/new.py",
            "whole file",
            "new in the candidate; no baseline version to compare",
        ),
    }


def test_replayed_real_calls_catch_a_bug_on_one_input_in_fifty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trees = make_trees(tmp_path)
    edit(
        trees["candidate"],
        "pkg/calc.py",
        "    return x * x\n",
        "    return x * x + (x % 50 == 49)\n",
    )

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("perfproof's process unpickled captured data")

    monkeypatch.setattr(pickle, "loads", refuse)
    monkeypatch.setattr(pickle, "load", refuse)
    monkeypatch.setattr(pickle, "Unpickler", refuse)

    result = check(make_spec(), trees, tmp_path / "work", examples=0)

    c = by_function(result)["pkg.calc:square"]
    assert (c.captured, c.replayed, c.mismatches) == (100, 100, 2)
    assert skips(c, "capture") == {
        "beyond the capture limit of 100 calls": 100
    }
    assert skips(c, "replay") == {}
    first = c.differences[0]
    assert first.item == "pkg.calc:square call square(49): return value"
    assert (first.baseline, first.candidate) == ("2401", "2402")
    assert "square(99)" in c.differences[1].item
    gate = GateResult(
        gate="correctness",
        status="failed",
        detail="differential mismatch",
        differential=result.checks,
    )
    assert gate.differential[0].mismatches == 2


def test_an_equivalent_change_passes_on_real_and_generated_inputs(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    edit(trees["candidate"], "pkg/calc.py", "return x * x", "return x**2")

    result = check(make_spec(), trees, tmp_path / "work", examples=50)

    c = by_function(result)["pkg.calc:square"]
    assert (c.captured, c.replayed, c.mismatches) == (100, 100, 0)
    assert c.generated > 0
    assert result.mismatches == 0


def test_generated_inputs_catch_a_bug_the_benchmark_never_reaches(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    edit(
        trees["candidate"],
        "pkg/calc.py",
        "return x if x >= 0 else -x",
        "return x",
    )

    result = check(make_spec(), trees, tmp_path / "work", examples=50)

    c = by_function(result)["pkg.calc:absval"]
    assert (c.captured, c.replayed) == (30, 30)
    assert c.generated > 0
    assert c.mismatches > 0
    assert all("generated input" in d.item for d in c.differences)


def test_calls_that_cannot_be_captured_are_counted_with_their_reason(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    cand = trees["candidate"]
    for old, new in (
        ("return fh.readline()", "return fh.readline() + ''"),
        ("        return 1\n", "        return 2 - 1\n"),
        ("if i % 2 == 0:", "if not i % 2:"),
        ("f.write(str(value))", "f.write(f'{value}')"),
        ("return random.random()", "return random.random() + 0.0"),
        (
            "return ctypes.CDLL(None).abs(x)",
            "return int(ctypes.CDLL(None).abs(x))",
        ),
    ):
        edit(cand, "pkg/calc.py", old, new)

    result = check(make_spec(), trees, tmp_path / "work", examples=0)
    checks = by_function(result)

    head = checks["pkg.calc:head"]
    assert (head.captured, head.replayed, head.mismatches) == (0, 0, 0)
    assert skips(head, "capture") == {
        "argument 'fh' could not be pickled (_io.TextIOWrapper)": 3
    }
    size = checks["pkg.calc:Holder.size"]
    assert skips(size, "capture") == {
        "argument 'self' could not be pickled (pkg.calc.Holder)": 1
    }
    evens = checks["pkg.calc:evens"]
    assert skips(evens, "capture") == {
        "a generator or coroutine function; its results are produced "
        "after the call returns": 4
    }
    save = checks["pkg.calc:save"]
    assert skips(save, "capture") == {
        "side effect during the call (open for writing)": 1
    }
    noisy = checks["pkg.calc:noisy"]
    assert (noisy.captured, noisy.replayed, noisy.mismatches) == (1, 0, 0)
    assert skips(noisy, "replay") == {
        "the baseline gave a different return value when replayed in a "
        "fresh process": 1
    }
    libc = checks["pkg.calc:libc_abs"]
    assert skips(libc, "capture") == {
        "side effect during the call (ctypes.dlopen)": 1
    }
    assert result.mismatches == 0


def test_a_method_is_replayed_with_its_captured_self(tmp_path: Path) -> None:
    trees = make_trees(tmp_path)
    edit(trees["candidate"], "pkg/calc.py", "self.n += k", "self.n += 2 * k")

    result = check(make_spec(), trees, tmp_path / "work", examples=0)

    c = by_function(result)["pkg.calc:Tally.bump"]
    assert (c.captured, c.replayed) == (5, 5)
    assert c.mismatches == 4
    assert c.differences[0].item.endswith(": return value")


def test_an_argument_mutated_in_place_is_compared_after_the_call(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    edit(trees["candidate"], "pkg/calc.py", "xs.append(1)", "xs = [*xs, 1]")

    result = check(make_spec(), trees, tmp_path / "work", examples=0)

    c = by_function(result)["pkg.calc:append_one"]
    assert c.mismatches == 1
    first = c.differences[0]
    assert first.item.endswith(": argument 0 after the call length")
    assert (first.baseline, first.candidate) == ("2", "1")


def test_a_candidate_that_crashes_on_one_call_mismatches_that_call(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    edit(
        trees["candidate"],
        "pkg/calc.py",
        "    return x * x\n",
        "    if x == 7:\n        import os\n        os._exit(3)\n"
        "    return x * x\n",
    )

    result = check(make_spec(), trees, tmp_path / "work", examples=0)

    c = by_function(result)["pkg.calc:square"]
    assert (c.captured, c.replayed, c.mismatches) == (100, 100, 1)
    assert c.differences[0].candidate == (
        "the replay process exited 3 during this call"
    )


def test_no_capture_code_runs_during_measured_rounds(tmp_path: Path) -> None:
    trees = make_trees(tmp_path)
    edit(trees["candidate"], "pkg/calc.py", "return x * x", "return x**2")
    mark = tmp_path / "mark.txt"
    spec = make_spec(env={"MARK": str(mark)})
    work = tmp_path / "work"

    check(spec, trees, work, base_env=os.environ, examples=0)
    events = sorted((work / "capture" / "events").iterdir())
    sizes = [p.stat().st_size for p in events]
    assert mark.read_text() == "True\n"

    runner = Runner(
        spec, trees, tmp_path / "logs", order_seed=1, base_env=os.environ
    )
    for _ in runner.rounds(0, 3):
        pass

    assert mark.read_text() == "True\n" + "False\n" * 6
    assert sorted((work / "capture" / "events").iterdir()) == events
    assert [p.stat().st_size for p in events] == sizes


def test_a_benchmark_that_ignores_pythonpath_is_reported(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    edit(trees["candidate"], "pkg/calc.py", "return x * x", "return x**2")

    result = check(
        make_spec(command=(PY, "-E", "bench.py")),
        trees,
        tmp_path / "work",
        examples=0,
    )

    assert result.checks == ()
    assert (
        NotChecked(
            "pkg/calc.py",
            "square",
            "no benchmark process loaded the capture hook",
        )
        in result.not_checked
    )
    assert any("PYTHONPATH" in n for n in result.notes)


def test_an_existing_work_dir_is_refused(tmp_path: Path) -> None:
    trees = make_trees(tmp_path)
    (tmp_path / "work").mkdir()

    with pytest.raises(ValueError, match="already exists"):
        check(make_spec(), trees, tmp_path / "work")


def test_a_baseline_that_fails_under_capture_raises(tmp_path: Path) -> None:
    trees = make_trees(tmp_path)
    edit(trees["candidate"], "pkg/calc.py", "return x * x", "return x**2")
    spec = make_spec(command=(PY, "-c", "import sys; sys.exit(4)"))

    with pytest.raises(CaptureError, match="exited 4"):
        check(spec, trees, tmp_path / "work", examples=0)


def test_a_candidate_that_patches_the_comparison_still_mismatches(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    cand = trees["candidate"]
    edit(
        cand,
        "pkg/calc.py",
        "import random\n",
        "import random\n\nimport __main__ as _main\n\n"
        "for _name in ('_compare', '_first_difference'):\n"
        "    if hasattr(_main, _name):\n"
        "        setattr(_main, _name, lambda *a, **k: None)\n",
    )
    edit(
        cand,
        "pkg/calc.py",
        "    return x * x\n",
        "    return x * x + (x % 50 == 49)\n",
    )

    result = check(make_spec(), trees, tmp_path / "work", examples=0)

    c = by_function(result)["pkg.calc:square"]
    assert (c.captured, c.replayed, c.mismatches) == (100, 100, 2)


def test_a_lenient_eq_in_the_candidate_does_not_hide_a_wrong_result(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    cand = trees["candidate"]
    edit(
        cand,
        "pkg/calc.py",
        "        self.v = v\n",
        "        self.v = v\n\n"
        "    def __eq__(self, other):\n"
        "        return True\n\n"
        "    __hash__ = None\n",
    )
    edit(cand, "pkg/calc.py", "return Box(x)", "return Box(x + 1)")

    result = check(make_spec(), trees, tmp_path / "work", examples=0)

    c = by_function(result)["pkg.calc:boxed"]
    assert (c.captured, c.replayed, c.mismatches) == (5, 5, 5)
    first = c.differences[0]
    assert first.item.startswith("pkg.calc:boxed call boxed(0): return value")
    assert (first.baseline, first.candidate) == ("0", "1")


def test_a_result_whose_class_the_baseline_lacks_mismatches(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    edit(
        trees["candidate"],
        "pkg/calc.py",
        "def boxed(x: int) -> Box:\n    return Box(x)\n",
        "class Fresh(Box):\n    pass\n\n\n"
        "def boxed(x: int) -> Box:\n    return Fresh(x)\n",
    )

    result = check(make_spec(), trees, tmp_path / "work", examples=0)

    c = by_function(result)["pkg.calc:boxed"]
    assert (c.replayed, c.mismatches) == (5, 5)
    assert (c.differences[0].candidate or "").startswith(
        "its result could not be loaded against the baseline: AttributeError"
    )


def test_an_import_inside_the_call_is_not_a_side_effect(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    edit(
        trees["candidate"],
        "pkg/calc.py",
        "return helper.double(x)",
        "return helper.double(x) + 0",
    )
    env = {
        k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"
    }

    result = check(
        make_spec(), trees, tmp_path / "work", base_env=env, examples=0
    )

    c = by_function(result)["pkg.calc:lazy_double"]
    assert (c.captured, c.replayed, c.mismatches) == (1, 1, 0)
    assert c.skipped == ()


def test_a_candidate_calling_into_ctypes_is_blocked_and_mismatches(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    marker = tmp_path / "touched"
    edit(
        trees["candidate"],
        "pkg/calc.py",
        "    return x * x\n",
        "    import ctypes\n\n"
        f"    ctypes.CDLL(None).system(b'touch {marker}')\n"
        "    return x * x\n",
    )

    result = check(make_spec(), trees, tmp_path / "work", examples=0)

    c = by_function(result)["pkg.calc:square"]
    assert (c.replayed, c.mismatches) == (100, 100)
    assert c.differences[0].candidate == (
        "attempted a side effect (ctypes.dlopen)"
    )
    assert not marker.exists()


def test_numpy_arguments_to_a_module_without_numpy_replay_cleanly(
    tmp_path: Path,
) -> None:
    pytest.importorskip("numpy")
    trees = make_trees(tmp_path, NUMPY_FILES)
    edit(
        trees["candidate"],
        "pkg/arr.py",
        "return xs.sum()",
        "return xs.sum() + 0",
    )

    result = check(make_spec(), trees, tmp_path / "work", examples=0)

    c = by_function(result)["pkg.arr:total"]
    assert (c.captured, c.replayed, c.mismatches) == (10, 10, 0)
    assert c.skipped == ()


def test_moving_import_numpy_into_the_function_is_not_a_side_effect(
    tmp_path: Path,
) -> None:
    pytest.importorskip("numpy")
    trees = make_trees(tmp_path, NUMPY_FILES)
    (trees["candidate"] / "pkg" / "means.py").write_text(
        "def mean(xs: list) -> float:\n"
        "    import numpy as np\n\n"
        "    return float(np.mean(xs))\n"
    )

    result = check(make_spec(), trees, tmp_path / "work", examples=0)

    c = by_function(result)["pkg.means:mean"]
    assert (c.captured, c.replayed, c.mismatches) == (10, 10, 0)
    assert c.skipped == ()


@pytest.mark.skipif(
    not hasattr(Fraction, "_from_coprime_ints"),
    reason="Fraction._from_coprime_ints is new in Python 3.12",
)
def test_a_result_that_pickling_would_normalise_still_mismatches(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    edit(
        trees["candidate"],
        "pkg/calc.py",
        "return Fraction(1, 2)",
        "return Fraction._from_coprime_ints(n, 2 * n)",
    )

    result = check(make_spec(), trees, tmp_path / "work", examples=0)

    c = by_function(result)["pkg.calc:half"]
    assert (c.captured, c.replayed) == (3, 3)
    assert c.mismatches == 2
    first = c.differences[0]
    assert first.item == (
        "pkg.calc:half call half(2): return value "
        "(as compared in the candidate's process)"
    )
    assert (first.baseline, first.candidate) == (
        "Fraction(1, 2)",
        "Fraction(2, 4)",
    )


def test_a_candidate_using_a_spawn_pool_ends_within_the_limit(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    edit(
        trees["candidate"],
        "pkg/calc.py",
        "    return x * x\n",
        "    import multiprocessing\n\n"
        "    pool = multiprocessing.get_context('spawn').Pool(1)\n"
        "    return pool.apply(abs, (x * x,))\n",
    )
    limit = 5.0

    start = time.monotonic()
    result = check(
        make_spec(timeout=limit),
        trees,
        tmp_path / "work",
        examples=0,
        capture_limit=3,
    )
    elapsed = time.monotonic() - start

    c = by_function(result)["pkg.calc:square"]
    assert (c.replayed, c.mismatches) == (3, 3)
    assert elapsed < limit + 10.0


def test_slots_added_by_the_candidate_are_judged_in_the_baseline_tree(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path)
    cand = trees["candidate"]
    edit(
        cand,
        "pkg/calc.py",
        "class Box:\n",
        "class Box:\n    __slots__ = ('v',)\n\n",
    )
    edit(cand, "pkg/calc.py", "return Box(x)", "return Box(x + 0)")

    result = check(make_spec(), trees, tmp_path / "work", examples=0)

    c = by_function(result)["pkg.calc:boxed"]
    assert (c.captured, c.replayed, c.mismatches) == (5, 5, 0)
    assert any(
        n.startswith(
            "pkg.calc:boxed: 5 calls compared only in the baseline tree"
        )
        for n in result.notes
    )


WRITER = (
    "import os\n\n"
    "with open(os.path.join(os.path.dirname(__file__), 'written.txt'),"
    " 'w') as f:\n"
    "    f.write('x')\n\n\n"
    "def value() -> int:\n"
    "    return 1\n"
)

LAZY_WRITER = (
    "    from pkg import writer\n\n    return x * x * writer.value()\n"
)


SETUP_DIRS = (
    "import os\n\n"
    "os.makedirs(os.path.join(os.path.dirname(__file__), 'cache'),"
    " exist_ok=True)\n"
    "os.environ.setdefault('OMP_NUM_THREADS', '1')\n\n\n"
    "def value() -> int:\n"
    "    return 1\n"
)


@pytest.mark.parametrize(
    "module", [WRITER, SETUP_DIRS], ids=["file_write", "makedirs_omp"]
)
def test_moving_an_import_of_a_loaded_tree_module_into_the_call_passes(
    tmp_path: Path, module: str
) -> None:
    trees = make_trees(
        tmp_path,
        {
            "pkg/writer.py": module,
            "bench.py": "from pkg import writer\n" + BENCH,
        },
    )
    edit(trees["candidate"], "pkg/calc.py", "    return x * x\n", LAZY_WRITER)

    result = check(
        make_spec(), trees, tmp_path / "work", examples=0, capture_limit=5
    )

    c = by_function(result)["pkg.calc:square"]
    assert (c.captured, c.replayed, c.mismatches) == (5, 5, 0)
    assert c.skipped == (
        SkippedCalls(
            stage="capture",
            reason="beyond the capture limit of 5 calls",
            count=195,
        ),
    )


def test_a_lazily_imported_loaded_module_still_lets_a_bug_show(
    tmp_path: Path,
) -> None:
    trees = make_trees(
        tmp_path,
        {
            "pkg/writer.py": WRITER,
            "bench.py": "from pkg import writer\n" + BENCH,
        },
    )
    edit(
        trees["candidate"],
        "pkg/calc.py",
        "    return x * x\n",
        "    from pkg import writer\n\n"
        "    return x * x * writer.value() + (x == 3)\n",
    )

    result = check(
        make_spec(), trees, tmp_path / "work", examples=0, capture_limit=5
    )

    c = by_function(result)["pkg.calc:square"]
    assert (c.captured, c.replayed, c.mismatches) == (5, 5, 1)
    first = c.differences[0]
    assert first.item.startswith("pkg.calc:square call square(3)")
    assert (first.baseline, first.candidate) == ("9", "10")


def test_a_result_of_a_loaded_module_the_recording_lacks_mismatches(
    tmp_path: Path,
) -> None:
    models = WRITER + (
        "\n\nclass Wrapped:\n    def __init__(self, v):\n        self.v = v\n"
    )
    trees = make_trees(
        tmp_path,
        {"pkg/models.py": models, "bench.py": "import pkg.models\n" + BENCH},
    )
    cand = trees["candidate"]
    edit(
        cand,
        "pkg/calc.py",
        "import random\n",
        "import random\n\nfrom pkg import models\n",
    )
    edit(cand, "pkg/calc.py", "return x * x", "return models.Wrapped(x * x)")

    result = check(
        make_spec(), trees, tmp_path / "work", examples=0, capture_limit=5
    )

    c = by_function(result)["pkg.calc:square"]
    assert (c.captured, c.replayed, c.mismatches) == (5, 5, 5)
    assert c.differences[0].item.endswith(": return value type")


def test_a_tree_module_the_benchmark_never_loaded_is_still_guarded(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path, {"pkg/writer.py": WRITER})
    cand = trees["candidate"]
    edit(cand, "pkg/calc.py", "    return x * x\n", LAZY_WRITER)

    result = check(
        make_spec(), trees, tmp_path / "work", examples=0, capture_limit=5
    )

    c = by_function(result)["pkg.calc:square"]
    assert (c.replayed, c.mismatches) == (5, 5)
    assert c.differences[0].candidate == (
        "attempted a side effect (open for writing)"
    )
    assert not (cand / "pkg" / "written.txt").exists()
    assert not (trees["baseline"] / "pkg" / "written.txt").exists()


def test_a_tree_under_the_interpreter_prefix_is_tree_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = tmp_path / "usr" / "src" / "app"
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "usr"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "usr"))
    monkeypatch.setattr(sys, "exec_prefix", str(tmp_path / "usr"))
    trees = [str(tree)]

    assert not _is_library(str(tree / "pkg" / "mod.py"), trees)
    assert _is_library(
        str(tree / ".venv" / "lib" / "site-packages" / "x.py"), trees
    )
    assert _is_library(str(tmp_path / "usr" / "lib" / "os.py"), trees)
    assert _is_library("built-in", trees)


def test_capture_does_not_load_a_lazy_module(tmp_path: Path) -> None:
    mark = tmp_path / "lazy.txt"
    body = (
        "import os\n\n"
        "with open(os.environ['LAZY_MARK'], 'a') as f:\n"
        "    f.write('loaded\\n')\n"
    )
    lazy = (
        "import importlib.util\n"
        "import sys\n\n"
        "spec = importlib.util.find_spec('pkg.lazy_body')\n"
        "spec.loader = importlib.util.LazyLoader(spec.loader)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules['pkg.lazy_body'] = module\n"
        "spec.loader.exec_module(module)\n"
    )
    trees = make_trees(
        tmp_path, {"pkg/lazy_body.py": body, "bench.py": lazy + BENCH}
    )
    edit(trees["candidate"], "pkg/calc.py", "return x * x", "return x**2")

    result = check(
        make_spec(env={"LAZY_MARK": str(mark)}),
        trees,
        tmp_path / "work",
        examples=0,
        capture_limit=5,
    )

    assert by_function(result)["pkg.calc:square"].replayed == 5
    assert not mark.exists()


def test_import_time_work_in_the_harness_does_not_rerun_in_replay(
    tmp_path: Path,
) -> None:
    mark = tmp_path / "harness.txt"
    setup = (
        "import os\n\n"
        "with open(os.environ['HARNESS_MARK'], 'a') as f:\n"
        "    f.write('ran\\n')\n"
    )
    trees = make_trees(
        tmp_path,
        {
            "pkg/setup_work.py": setup,
            "bench.py": "import pkg.setup_work\n" + BENCH,
        },
    )
    edit(trees["candidate"], "pkg/calc.py", "return x * x", "return x**2")

    result = check(
        make_spec(env={"HARNESS_MARK": str(mark)}),
        trees,
        tmp_path / "work",
        examples=0,
    )

    assert by_function(result)["pkg.calc:square"].mismatches == 0
    assert mark.read_text() == "ran\n"


def test_a_library_the_benchmark_never_loaded_imports_in_the_call(
    tmp_path: Path,
) -> None:
    pytest.importorskip("numpy")
    trees = make_trees(tmp_path)
    edit(
        trees["candidate"],
        "pkg/calc.py",
        "    return x * x\n",
        "    import numpy as np\n\n    return int(np.int64(x) * x)\n",
    )

    result = check(make_spec(), trees, tmp_path / "work", examples=0)

    c = by_function(result)["pkg.calc:square"]
    assert (c.captured, c.replayed, c.mismatches) == (100, 100, 0)
    assert skips(c, "replay") == {}


LOADED_WRITER_BENCH = {
    "pkg/writer.py": WRITER,
    "bench.py": "from pkg import writer\n" + BENCH,
}


def test_a_lazyloader_import_of_a_loaded_module_passes(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path, LOADED_WRITER_BENCH)
    cand = trees["candidate"]
    edit(
        cand,
        "pkg/calc.py",
        "import random\n",
        "import importlib.util\nimport random\nimport sys\n\n\n"
        "def _lazy(name):\n"
        "    spec = importlib.util.find_spec(name)\n"
        "    loader = importlib.util.LazyLoader(spec.loader)\n"
        "    spec.loader = loader\n"
        "    module = importlib.util.module_from_spec(spec)\n"
        "    sys.modules[name] = module\n"
        "    loader.exec_module(module)\n"
        "    return module\n\n\n"
        "writer = _lazy('pkg.writer')\n",
    )
    edit(cand, "pkg/calc.py", "return x * x", "return x * x * writer.value()")

    result = check(
        make_spec(), trees, tmp_path / "work", examples=0, capture_limit=5
    )

    c = by_function(result)["pkg.calc:square"]
    assert (c.captured, c.replayed, c.mismatches) == (5, 5, 0)


def test_a_loaded_module_that_registers_itself_imports_in_the_call(
    tmp_path: Path,
) -> None:
    registry = (
        "NAMES = []\n\n\n"
        "def register(name):\n"
        "    if name in NAMES:\n"
        "        raise ValueError(f'{name} is already registered')\n"
        "    NAMES.append(name)\n"
    )
    plugin = (
        "import os\n\n"
        "from pkg import registry\n\n"
        "registry.register('plugin')\n"
        "os.makedirs(os.path.join(os.path.dirname(__file__), 'cache'),"
        " exist_ok=True)\n\n\n"
        "def value() -> int:\n"
        "    return 1\n"
    )
    trees = make_trees(
        tmp_path,
        {
            "pkg/registry.py": registry,
            "pkg/plugin.py": plugin,
            "bench.py": "from pkg import plugin\n" + BENCH,
        },
    )
    edit(
        trees["candidate"],
        "pkg/calc.py",
        "    return x * x\n",
        "    from pkg import plugin\n\n    return x * x * plugin.value()\n",
    )

    result = check(
        make_spec(), trees, tmp_path / "work", examples=0, capture_limit=5
    )

    c = by_function(result)["pkg.calc:square"]
    assert (c.captured, c.replayed, c.mismatches) == (5, 5, 0)


def test_re_importing_a_loaded_module_inside_the_call_is_guarded(
    tmp_path: Path,
) -> None:
    trees = make_trees(tmp_path, LOADED_WRITER_BENCH)
    cand = trees["candidate"]
    edit(
        cand,
        "pkg/calc.py",
        "import random\n",
        "import importlib\nimport random\nimport sys\n\n"
        "from pkg import writer\n",
    )
    edit(
        cand,
        "pkg/calc.py",
        "    return x * x\n",
        "    sys.modules.pop('pkg.writer', None)\n"
        "    importlib.import_module('pkg.writer')\n"
        "    return x * x * writer.value()\n",
    )

    result = check(
        make_spec(), trees, tmp_path / "work", examples=0, capture_limit=5
    )

    c = by_function(result)["pkg.calc:square"]
    assert (c.replayed, c.mismatches) == (5, 5)
    assert c.differences[0].candidate == (
        "attempted a side effect (open for writing)"
    )


SLOW_IMPORTS = {
    "pkg/slow1.py": "import time\n\ntime.sleep(0.3)\n",
    "pkg/slow2.py": "import time\n\ntime.sleep(0.6)\n",
    "bench.py": "import pkg.slow1\nimport pkg.slow2\n" + BENCH,
}

THREADED_IMPORTS = (
    "    import importlib\n"
    "    import threading\n"
    "    import time\n\n"
    "    workers = [\n"
    "        threading.Thread(\n"
    "            target=importlib.import_module, args=(f'pkg.slow{i}',)\n"
    "        )\n"
    "        for i in (1, 2)\n"
    "    ]\n"
    "    workers[0].start()\n"
    "    time.sleep(0.1)\n"
    "    workers[1].start()\n"
    "    for worker in workers:\n"
    "        worker.join()\n"
)


def test_overlapping_imports_in_threads_leave_the_guard_on(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "touched"
    trees = make_trees(tmp_path, SLOW_IMPORTS)
    edit(
        trees["candidate"],
        "pkg/calc.py",
        "    return x * x\n",
        THREADED_IMPORTS
        + f"    open({str(marker)!r}, 'w').close()\n"
        + "    return x * x\n",
    )

    result = check(
        make_spec(), trees, tmp_path / "work", examples=0, capture_limit=5
    )

    c = by_function(result)["pkg.calc:square"]
    assert (c.captured, c.replayed, c.mismatches) == (5, 5, 5)
    assert c.differences[0].candidate == (
        "attempted a side effect (open for writing)"
    )
    assert not marker.exists()


def test_overlapping_honest_imports_in_threads_pass(tmp_path: Path) -> None:
    files = {
        **SLOW_IMPORTS,
        "pkg/slow2.py": (
            "import os\nimport time\n\ntime.sleep(0.6)\n"
            "os.makedirs(os.path.join(os.path.dirname(__file__), 'cache'),"
            " exist_ok=True)\n"
        ),
    }
    trees = make_trees(tmp_path, files)
    edit(
        trees["candidate"],
        "pkg/calc.py",
        "    return x * x\n",
        THREADED_IMPORTS + "    return x * x\n",
    )

    result = check(
        make_spec(), trees, tmp_path / "work", examples=0, capture_limit=5
    )

    c = by_function(result)["pkg.calc:square"]
    assert (c.captured, c.replayed, c.mismatches) == (5, 5, 0)


def test_an_import_in_a_worker_thread_does_not_unguard_the_call(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "touched"
    trees = make_trees(tmp_path, SLOW_IMPORTS)
    edit(
        trees["candidate"],
        "pkg/calc.py",
        "    return x * x\n",
        "    import importlib\n"
        "    import threading\n"
        "    import time\n\n"
        "    worker = threading.Thread(\n"
        "        target=importlib.import_module, args=('pkg.slow2',)\n"
        "    )\n"
        "    worker.start()\n"
        "    time.sleep(0.2)\n"
        f"    open({str(marker)!r}, 'w').close()\n"
        "    worker.join()\n"
        "    return x * x\n",
    )

    result = check(
        make_spec(), trees, tmp_path / "work", examples=0, capture_limit=5
    )

    c = by_function(result)["pkg.calc:square"]
    assert (c.captured, c.replayed, c.mismatches) == (5, 5, 5)
    assert not marker.exists()


def test_changed_functions_runs_one_diff_for_many_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = [f"pkg/m{i}.py" for i in range(12)]
    names += ["pkg/sp ace.py", "pkg/é.py"]
    files = dict.fromkeys(names, "def f():\n    return 1\n")
    trees = make_trees(tmp_path, files)
    for n in names:
        edit(trees["candidate"], n, "return 1", "return 2")
    diffs: list[tuple[str, ...]] = []
    real = differential._git  # pyright: ignore[reportPrivateUsage]

    def counting(cwd: Path, *args: str, **kwargs: object) -> bytes:
        if "-U0" in args:
            diffs.append(args)
        return real(cwd, *args, **kwargs)  # pyright: ignore

    monkeypatch.setattr(differential, "_git", counting)

    changes = changed_functions(trees["baseline"], trees["candidate"])

    assert len(diffs) == 1
    assert {c.path for c in changes.functions} == set(names)
    assert not changes.not_checked
