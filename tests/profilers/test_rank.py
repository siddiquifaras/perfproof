"""The ranking across saved profiles and the never-sampled files."""

import json
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from perfproof.cli import main
from perfproof.commands import profile as profile_command
from perfproof.commands._profile_rank import GROWTH_PREFIX
from perfproof.profilers.growth import (
    FunctionKey,
    SizePoint,
    function_key,
    project_functions,
)
from perfproof.profilers.rank import (
    SavedRun,
    never_sampled,
    rank_functions,
    suffixes_for,
)
from perfproof.schema import Hotspot

FIXTURES = Path(__file__).parent.parent / "fixtures"
SHORT = FIXTURES / "py-spy" / "workload-native.txt"
LONG = FIXTURES / "py-spy" / "workload-native-long.txt"
REL = FIXTURES / "py-spy" / "workload.txt"
"""Relative paths: the project's if the file exists under the root."""
ROOT = Path("/srv/project")
"""Where the workload was mounted when py-spy recorded the fixtures."""


def ranked(
    runs: list[SavedRun], growth: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", rank_functions(runs, growth))


def saved(
    name: str, path: Path, run_id: str, size: int | None = None
) -> SavedRun:
    functions, _ = project_functions(path, ROOT, [], weight_unit="samples")
    return SavedRun(
        benchmark=name,
        run_id=run_id,
        size=size,
        factor=None if size is None else size // 1000,
        min_effect=0.03,
        functions=functions,
    )


def test_functions_are_ranked_by_cap_at_the_largest_size() -> None:
    rows = ranked([saved("a", SHORT, "r1"), saved("b", LONG, "r2")], {})
    caps = [float("inf") if r["cap"] is None else r["cap"] for r in rows]
    assert caps == sorted(caps, reverse=True)
    assert len(rows) == len(
        {(r["frame_kind"], r["file"], r["function"]) for r in rows}
    )
    both = [r for r in rows if len(r["seen_in"]) == 2]
    assert both
    for row in both:
        # Plain profiles are each their own largest: the cap is the
        # larger of the two profiles' caps.
        assert row["cap"] == max(
            s["cap"] or float("inf") for s in row["seen_in"]
        )
        assert row["cap"] == row["best_any_size"]["cap"]
        where = row["ranked_in"]
        winner = next(
            s
            for s in row["seen_in"]
            if (s["benchmark"], s["run_id"])
            == (where["benchmark"], where["run_id"])
        )
        assert winner["total_fraction"] == row["total_fraction"]


def test_the_order_is_deterministic_and_input_order_free() -> None:
    first = ranked([saved("a", SHORT, "r1"), saved("b", LONG, "r2")], {})
    second = ranked([saved("b", LONG, "r2"), saved("a", SHORT, "r1")], {})
    assert [(r["file"], r["line"], r["function"]) for r in first] == [
        (r["file"], r["line"], r["function"]) for r in second
    ]


def test_more_profiles_break_a_tie_in_cap() -> None:
    once = saved("a", SHORT, "r1")
    twice = [saved("a", SHORT, "r1"), saved("a", SHORT, "r2")]
    solo = {r["function"]: len(r["seen_in"]) for r in ranked([once], {})}
    pair = {r["function"]: len(r["seen_in"]) for r in ranked(twice, {})}
    assert set(pair.values()) == {2}
    assert set(solo.values()) == {1}
    rows = ranked([saved("a", SHORT, "r1"), saved("b", LONG, "r2")], {})
    seen = [len(r["seen_in"]) for r in rows]
    caps = [r["cap"] for r in rows]
    for i in range(len(rows) - 1):
        if caps[i] == caps[i + 1]:
            assert seen[i] >= seen[i + 1]


def test_whole_profile_functions_come_first_with_no_cap() -> None:
    run = saved("a", SHORT, "r1")
    whole = Hotspot(
        function="entry",
        file="app/entry.py",
        line=1,
        frame_kind="python",
        is_library=False,
        self_fraction=0.0,
        total_fraction=1.0,
    )
    functions = dict(run.functions)
    functions[function_key(whole)] = (whole, SizePoint(1.0, 512.0))
    rows = ranked([replace(run, functions=functions)], {})
    assert rows[0]["function"] == "entry"
    assert rows[0]["cap"] is None
    assert rows[0]["cap_above_bar"] is True
    assert rows[1]["cap"] is not None


def growth_doc(name: str, **entry: Any) -> dict[str, Any]:
    fit = entry.pop("fit")
    return {
        "group": "g1",
        "sizes": [1000, 2000, 4000],
        "workload": {"exponent": 1.0, "n": 3},
        "functions": [
            {
                "frame_kind": "python",
                "file": "app/kernels.py",
                "function": name,
                "fit": fit,
                "evidence": entry.get("evidence"),
                "appears_at_larger_sizes": entry.get("appears", False),
                "notes": entry.get("notes", []),
            }
        ],
    }


def test_growth_attaches_and_flags_a_rising_ceiling() -> None:
    run = saved("a", SHORT, "r1", size=1000)
    doc = growth_doc(
        "slow_sum",
        fit={"exponent": 2.0, "low": 1.5, "high": 2.5, "n": 3},
        evidence="shown",
        notes=["few_points"],
    )
    rows = {r["function"]: r for r in ranked([run], {"a": [doc]})}
    slow = rows["slow_sum"]
    assert slow["ceiling_rises_with_size"] is True
    assert slow["growth"]["exponent"] == 2.0
    assert slow["growth"]["workload_exponent"] == 1.0
    assert slow["growth"]["notes"] == ["few_points"]
    others = [r for n, r in rows.items() if n != "slow_sum"]
    assert all(r["growth"] is None for r in others)
    assert all(r["ceiling_rises_with_size"] is None for r in others)


def test_a_fit_that_is_not_faster_says_the_ceiling_does_not_rise() -> None:
    run = saved("a", SHORT, "r1", size=1000)
    doc = growth_doc(
        "slow_sum",
        fit={"exponent": 1.0, "low": 0.9, "high": 1.1, "n": 4},
        evidence="none",
    )
    rows = ranked([run], {"a": [doc]})
    slow = next(r for r in rows if r["function"] == "slow_sum")
    assert slow["ceiling_rises_with_size"] is False


def test_a_function_seen_only_at_larger_sizes_is_a_lead_not_shown() -> None:
    run = saved("a", SHORT, "r1", size=1000)
    doc = growth_doc("slow_sum", fit=None, appears=True)
    rows = ranked([run], {"a": [doc]})
    slow = next(r for r in rows if r["function"] == "slow_sum")
    assert slow["ceiling_rises_with_size"] is None
    assert slow["growth"]["appears_at_larger_sizes"] is True


def test_a_lead_makes_no_claim_about_the_ceiling() -> None:
    run = saved("a", SHORT, "r1", size=1000)
    doc = growth_doc(
        "slow_sum",
        fit={"exponent": 1.7, "low": 1.2, "high": 2.2, "n": 3},
        evidence="lead",
        notes=["few_points", "interval_overlaps_workload"],
    )
    rows = ranked([run], {"a": [doc]})
    slow = next(r for r in rows if r["function"] == "slow_sum")
    assert slow["ceiling_rises_with_size"] is None
    assert slow["growth"]["evidence"] == "lead"
    assert "interval_overlaps_workload" in slow["growth"]["notes"]


def test_wrappers_are_flagged_and_keep_their_place_in_the_json() -> None:
    run = saved("a", SHORT, "r1")
    entry = Hotspot(
        function="entry",
        file="app/entry.py",
        line=1,
        frame_kind="python",
        is_library=False,
        self_fraction=0.0,
        total_fraction=1.0,
    )
    functions = dict(run.functions)
    functions[function_key(entry)] = (entry, SizePoint(1.0, 512.0))
    rows = ranked([replace(run, functions=functions)], {})
    assert [r["function"] for r in rows if r["wrapper"]] == ["entry"]
    assert rows[0]["function"] == "entry"
    assert not any(r["wrapper"] for r in rows[1:])


def test_the_growth_of_another_benchmark_is_not_used() -> None:
    run = saved("a", SHORT, "r1", size=1000)
    doc = growth_doc(
        "slow_sum",
        fit={"exponent": 2.0, "low": 1.5, "high": 2.5, "n": 3},
        evidence="shown",
    )
    rows = ranked([run], {"other": [doc]})
    assert all(r["growth"] is None for r in rows)


# Never sampled

FILES = [
    "app/a.py",
    "app/b.py",
    "app/sub/c.py",
    "core/x.c",
    "core/x.h",
    "tests/test_a.py",
    "app/test_b.py",
    "app/b_test.py",
    "app/conftest.py",
    "docs/readme.md",
    "app/tests/helper.py",
]


def test_never_sampled_lists_source_files_no_profile_reached() -> None:
    result = never_sampled(
        FILES, False, suffixes_for(["python_cpu"], []), {"app/a.py"}
    )
    assert result.files == ("app/b.py", "app/sub/c.py")
    assert (result.source_files, result.sampled, result.count) == (3, 1, 2)
    assert result.by_suffix == {".py": 2}
    assert result.truncated is False


def test_native_targets_count_native_suffixes_not_python() -> None:
    result = never_sampled(FILES, True, suffixes_for(["native"], []), set())
    assert result.files == ("core/x.c", "core/x.h")
    assert result.by_suffix == {".c": 1, ".h": 1}
    assert result.truncated is True


def test_a_sampled_file_adds_its_suffix_without_a_project_model() -> None:
    suffixes = suffixes_for([], ["core/x.c"])
    assert suffixes == {".c"}
    result = never_sampled(FILES, False, suffixes, {"core/x.c"})
    assert result.files == ()
    assert (result.source_files, result.sampled) == (1, 1)


# The command


def run_cli(
    capsys: pytest.CaptureFixture[str], *argv: str
) -> tuple[int, dict[str, Any]]:
    status = main([*argv, "--json"])
    out, _ = capsys.readouterr()
    return status, cast("dict[str, Any]", json.loads(out))


def save_run(
    root: Path,
    name: str,
    run_id: str,
    fixture: Path,
    size: dict[str, Any] | None = None,
) -> Path:
    run = root / ".perfproof" / "profiles" / name / run_id
    run.mkdir(parents=True)
    shutil.copyfile(fixture, run / "folded.txt")
    document: dict[str, Any] = {
        "folded": str(run / "folded.txt"),
        "weight_unit": "samples",
        "min_effect": 0.03,
        "unprofiled": {"wall_seconds": 1.0},
    }
    if size is not None:
        document["size"] = size
    (run / "result.json").write_text(json.dumps(document), "utf-8")
    return run


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "app").mkdir()
    (root / "run.py").write_text("x = 1\n", "utf-8")
    (root / "app" / "kernels.py").write_text("x = 1\n", "utf-8")
    (root / "app" / "idle.py").write_text("x = 1\n", "utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_k.py").write_text("x = 1\n", "utf-8")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    return root


def test_cli_ranks_across_every_saved_run(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    save_run(project, "w", "20261028T000000Z-aaaaaa", REL)
    save_run(
        project,
        "w",
        "20261028T000001Z-bbbbbb",
        REL,
        {
            "kind": "arg",
            "name": "--n",
            "base": 1000,
            "factor": 2,
            "value": 2000,
        },
    )
    status, document = run_cli(
        capsys, "profile", "show", "--rank", "--repo", str(project)
    )
    assert status == 0, document
    result = document["result"]
    assert result["benchmarks"] == ["w"]
    assert [r["run_id"] for r in result["runs"]] == [
        "20261028T000000Z-aaaaaa",
        "20261028T000001Z-bbbbbb",
    ]
    assert [r["size"] for r in result["runs"]] == [None, 2000]
    assert result["skipped_runs"] == []
    assert result["labels"]["cap"].startswith("bound")
    assert result["order"].startswith("cap at the largest size measured")


def test_cli_names_pick_benchmarks(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    save_run(project, "w", "20261028T000000Z-aaaaaa", REL)
    save_run(project, "v", "20261028T000000Z-cccccc", REL)
    _, only = run_cli(
        capsys, "profile", "show", "--rank", "v", "--repo", str(project)
    )
    assert only["result"]["benchmarks"] == ["v"]
    assert {r["benchmark"] for r in only["result"]["runs"]} == {"v"}
    _, both = run_cli(
        capsys, "profile", "show", "--rank", "--repo", str(project)
    )
    assert both["result"]["benchmarks"] == ["v", "w"]


def test_cli_lists_files_no_saved_profile_sampled(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    save_run(project, "w", "20261028T000000Z-aaaaaa", REL)
    status, document = run_cli(
        capsys, "profile", "show", "--rank", "--repo", str(project)
    )
    assert status == 0, document
    coverage = document["result"]["coverage"]
    assert coverage["files"] == ["app/idle.py"]
    assert coverage["sampled"] == 2
    assert coverage["never_sampled"] == 1
    assert coverage["suffixes"] == [".py"]
    assert "never sampled" in coverage["label"]
    assert "not line coverage" in coverage["label"]


def test_cli_text_is_the_top_rows_in_words(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    save_run(project, "w", "20261028T000000Z-aaaaaa", REL)
    status = main(
        ["profile", "show", "--rank", "--repo", str(project), "--top", "2"]
    )
    out = capsys.readouterr().out
    assert status == 0
    lines = out.splitlines()
    assert lines[1].startswith("1. ")
    assert lines[2].startswith("2. ")
    assert "more in the JSON result." in out
    assert "Never sampled: 1 of 3 source files" in out
    assert "  app/idle.py" in out


def test_cli_reports_an_unreadable_run_and_keeps_going(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    save_run(project, "w", "20261028T000000Z-aaaaaa", REL)
    broken = save_run(project, "w", "20261028T000001Z-bbbbbb", REL)
    (broken / "folded.txt").unlink()
    status, document = run_cli(
        capsys, "profile", "show", "--rank", "--repo", str(project)
    )
    assert status == 0, document
    (skipped,) = document["result"]["skipped_runs"]
    assert skipped["run_id"] == "20261028T000001Z-bbbbbb"
    assert skipped["reason"]
    assert len(document["result"]["runs"]) == 1


def test_cli_uses_saved_growth_documents(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    save_run(
        project,
        "w",
        "20261028T000000Z-aaaaaa",
        REL,
        {
            "kind": "arg",
            "name": "--n",
            "base": 1000,
            "factor": 1,
            "value": 1000,
        },
    )
    doc = growth_doc(
        "membership",
        fit={"exponent": 2.0, "low": 1.5, "high": 2.5, "n": 3},
        evidence="shown",
    )
    directory = project / ".perfproof" / "profiles" / "w"
    (directory / f"{GROWTH_PREFIX}g1.json").write_text(
        json.dumps(doc), "utf-8"
    )
    _, document = run_cli(
        capsys, "profile", "show", "--rank", "--repo", str(project)
    )
    rows = {f["function"]: f for f in document["result"]["functions"]}
    assert rows["membership"]["ceiling_rises_with_size"] is True
    assert (directory / f"{GROWTH_PREFIX}g1.json").is_file()


def test_cli_without_saved_profiles_is_not_found(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, document = run_cli(
        capsys, "profile", "show", "--rank", "--repo", str(project)
    )
    assert status == 1
    assert document["error"]["code"] == "not_found"


def test_cli_refuses_a_bad_name(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, document = run_cli(
        capsys, "profile", "show", "--rank", "../x", "--repo", str(project)
    )
    assert status == 1
    assert document["error"]["code"] == "invalid_name"


@pytest.mark.parametrize(
    "argv",
    [
        ("profile", "w", "--rank"),
        ("profile", "show", "--rank", "--function", "f"),
        ("profile", "w", "--size-arg=--n=1"),
        ("profile", "w", "--sizes", "1,2,4"),
    ],
    ids=("rank-not-show", "rank-and-function", "knob-alone", "sizes-alone"),
)
def test_cli_refuses_misused_options(
    argv: tuple[str, ...], project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, document = run_cli(capsys, *argv, "--repo", str(project))
    assert status == 1
    assert document["error"]["code"] == "invalid_argument"


def test_cli_wants_three_distinct_sizes(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, document = run_cli(
        capsys,
        "profile",
        "w",
        "--sizes",
        "1,2",
        "--size-arg=--n=1",
        "--repo",
        str(project),
    )
    assert status == 2
    assert document["error"]["code"] == "usage"


# profile --sizes, with a command profiler that emits a captured profile

COPY_PROFILER = """\
import shutil, subprocess, sys
fixture, output, *command = sys.argv[1:]
subprocess.run(command, check=True)
shutil.copyfile(fixture, output)
"""
SIZES_SPEC = {
    "name": "w",
    "command": [sys.executable, "run.py"],
    "metric": {"kind": "wall", "direction": "lower_is_better", "unit": "s"},
    "correctness": {
        "command": [sys.executable, "-c", "pass"],
        "timeout_seconds": 30.0,
    },
    "warmup": 0,
    "budget": {"max_rounds": 20, "max_seconds": 60.0},
    "looks": {"n0": 10, "k": 10, "max_looks": 2},
    "timeout_seconds": 60.0,
    "harness": ["run.py"],
}


def define_project(
    project: Path, capsys: pytest.CaptureFixture[str], script: str
) -> Path:
    (project / "run.py").write_text(script, "utf-8")
    (project / "copy_profiler.py").write_text(COPY_PROFILER, "utf-8")
    (project / "spec.json").write_text(json.dumps(SIZES_SPEC), "utf-8")
    identity = ("-c", "user.name=t", "-c", "user.email=t@t")
    for argv in (("add", "-A"), (*identity, "commit", "-qm", "i")):
        subprocess.run(["git", "-C", str(project), *argv], check=True)
    status, document = run_cli(
        capsys,
        "bench",
        "define",
        "w",
        "--spec",
        str(project / "spec.json"),
        "--repo",
        str(project),
    )
    assert status == 0, document
    return project


@pytest.fixture
def defined(project: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    return define_project(project, capsys, "import sys\nsys.exit(0)\n")


@pytest.fixture
def failing_at_size(project: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    script = (
        "import os, sys\n"
        "sys.exit(3 if int(os.environ.get('N', '0')) >= 40 else 0)\n"
    )
    return define_project(project, capsys, script)


def sizes_argv(project: Path, *more: str) -> tuple[str, ...]:
    return (
        "profile",
        "w",
        "--sizes",
        "1,2,4",
        "--profiler-command",
        template_for(project),
        "--repo",
        str(project),
        *more,
    )


def template_for(project: Path) -> str:
    return json.dumps(
        [
            sys.executable,
            str(project / "copy_profiler.py"),
            str(REL),
            "{output}",
            "{command}",
        ]
    )


def test_sizes_profiles_each_size_and_saves_the_fits(
    defined: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, document = run_cli(
        capsys, *sizes_argv(defined, "--size-arg=--n=1000")
    )
    assert status == 0, document
    result = document["result"]
    assert result["sizes"] == [1000, 2000, 4000]
    assert [r["size"] for r in result["runs"]] == [1000, 2000, 4000]
    assert result["knob"] == {"kind": "arg", "name": "--n", "base": 1000}
    saved_runs = [
        json.loads((Path(r["directory"]) / "result.json").read_text("utf-8"))
        for r in result["runs"]
    ]
    assert [s["size"]["value"] for s in saved_runs] == [1000, 2000, 4000]
    assert {s["size"]["group"] for s in saved_runs} == {result["size_group"]}
    for s, size in zip(saved_runs, (1000, 2000, 4000), strict=True):
        assert s["argv"][-2:] == ["--n", str(size)]
        assert s["spec_hash"] == result["spec_hash"]
    growth = json.loads(Path(result["growth_file"]).read_text("utf-8"))
    assert growth["functions"] == result["functions"]
    names = {f["function"] for f in result["functions"]}
    assert {"membership", "main"} <= names
    for f in result["functions"]:
        assert len(f["costs"]) == 3
        assert f["fit"]["n"] == 3


def test_nothing_but_profiling_runs_between_the_profiled_runs(
    defined: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    for attribute, label in (
        ("_new_directory", "directory"),
        ("profile_benchmark", "profile"),
        ("_finish", "finish"),
    ):
        original: Any = getattr(profile_command, attribute)

        def wrapped(
            *args: Any, original: Any = original, label: str = label, **kw: Any
        ) -> Any:
            events.append(label)
            return original(*args, **kw)

        monkeypatch.setattr(profile_command, attribute, wrapped)
    status, document = run_cli(capsys, *sizes_argv(defined, "--size-env=N=10"))
    assert status == 0, document
    assert events == ["directory"] * 3 + ["profile"] * 3 + ["finish"] * 3


def test_sizes_env_knob_sets_the_variable_not_the_argv(
    defined: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, document = run_cli(capsys, *sizes_argv(defined, "--size-env=N=10"))
    assert status == 0, document
    first = Path(document["result"]["runs"][0]["directory"])
    saved_run = json.loads((first / "result.json").read_text("utf-8"))
    assert saved_run["argv"][-1] != "10"
    assert saved_run["size"] == {
        "kind": "env",
        "name": "N",
        "base": 10,
        "factor": 1,
        "value": 10,
        "group": document["result"]["size_group"],
    }


def test_a_plain_profile_carries_no_size(
    defined: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, document = run_cli(
        capsys,
        "profile",
        "w",
        "--profiler-command",
        template_for(defined),
        "--repo",
        str(defined),
    )
    assert status == 0, document
    assert "size" not in document["result"]
    growth = list((defined / ".perfproof" / "profiles" / "w").glob("growth-*"))
    assert growth == []


def test_malformed_growth_entries_are_skipped() -> None:
    run = saved("a", SHORT, "r1", size=1000)
    doc = growth_doc(
        "slow_sum",
        fit={"exponent": 2.0, "low": 1.5, "high": 2.5, "n": 3},
        evidence="shown",
    )
    doc["functions"].append({"function": "slow_sum"})
    doc["functions"].append("not an entry")
    rows = ranked([run], {"a": [doc]})
    slow = next(r for r in rows if r["function"] == "slow_sum")
    assert slow["ceiling_rises_with_size"] is True


def test_ranking_many_functions_with_growth_is_not_quadratic() -> None:
    count = 8000
    hotspots = [
        Hotspot(
            function=f"f{i}",
            file="app/m.py",
            line=1,
            frame_kind="python",
            is_library=False,
            self_fraction=0.001,
            total_fraction=0.002,
        )
        for i in range(count)
    ]
    functions = {
        function_key(h): (h, SizePoint(0.002, 100.0)) for h in hotspots
    }
    run = SavedRun("a", "r1", 1000, 1, 0.03, functions)
    doc: dict[str, Any] = {
        "group": "g",
        "workload": {"exponent": 1.0},
        "functions": [
            {
                "frame_kind": "python",
                "file": "app/m.py",
                "function": f"f{i}",
                "fit": {"exponent": 1.0, "n": 3},
                "evidence": "none",
                "appears_at_larger_sizes": False,
                "notes": [],
            }
            for i in range(count)
        ],
    }
    started = time.perf_counter()
    rows = ranked([run], {"a": [doc]})
    elapsed = time.perf_counter() - started
    assert len(rows) == count
    assert all(r["ceiling_rises_with_size"] is False for r in rows)
    assert elapsed < 1.5


def test_latest_run_after_sizes_is_the_last_one_created(
    defined: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, document = run_cli(capsys, *sizes_argv(defined, "--size-env=N=10"))
    assert status == 0, document
    runs = document["result"]["runs"]
    status, shown = run_cli(
        capsys,
        "profile",
        "show",
        "w",
        "--function",
        "membership",
        "--repo",
        str(defined),
    )
    assert status == 0, shown
    assert shown["result"]["run_id"] == runs[-1]["run_id"]
    assert [r["run_id"] for r in runs] == sorted(r["run_id"] for r in runs)
    assert runs[-1]["size"] == 40


def test_run_directory_names_sort_in_creation_order(
    defined: Path,
) -> None:
    make = profile_command._new_directory  # pyright: ignore[reportPrivateUsage]
    made = [make(defined, "w") for _ in range(25)]
    assert [p.name for p in made] == sorted(p.name for p in made)
    assert len({p.name for p in made}) == 25


def test_a_failing_size_is_named_in_the_error(
    failing_at_size: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, document = run_cli(
        capsys, *sizes_argv(failing_at_size, "--size-env=N=10")
    )
    assert status == 1
    error = document["error"]
    assert error["code"] == "workload_failed"
    assert "at size 40" in error["message"]
    assert error["details"]["size"] == 40


def test_text_calls_a_lead_a_lead_and_never_claims_a_rise(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    save_run(
        project,
        "w",
        "20261028T000000Z-aaaaaa",
        REL,
        {
            "kind": "arg",
            "name": "--n",
            "base": 1000,
            "factor": 1,
            "value": 1000,
        },
    )
    doc = growth_doc(
        "membership",
        fit={"exponent": 1.7, "low": 1.2, "high": 2.2, "n": 3},
        evidence="lead",
        notes=["few_points", "interval_overlaps_workload"],
    )
    directory = project / ".perfproof" / "profiles" / "w"
    (directory / f"{GROWTH_PREFIX}g1.json").write_text(
        json.dumps(doc), "utf-8"
    )
    status = main(["profile", "show", "--rank", "--repo", str(project)])
    out = capsys.readouterr().out
    assert status == 0
    assert "a lead, not shown at these sizes" in out
    assert "so its ceiling rises" not in out


@pytest.mark.parametrize(
    ("appears", "said", "never"),
    [
        (True, "Seen only at the larger sizes", "Growth not fitted"),
        (False, "Growth not fitted", "Seen only at the larger sizes"),
    ],
)
def test_text_says_why_there_is_no_fit(
    project: Path,
    capsys: pytest.CaptureFixture[str],
    appears: bool,
    said: str,
    never: str,
) -> None:
    save_run(
        project,
        "w",
        "20261028T000000Z-aaaaaa",
        REL,
        {
            "kind": "arg",
            "name": "--n",
            "base": 1000,
            "factor": 1,
            "value": 1000,
        },
    )
    doc = growth_doc(
        "membership",
        fit=None,
        appears=appears,
        notes=["missing_sizes", "no_fit"],
    )
    directory = project / ".perfproof" / "profiles" / "w"
    (directory / f"{GROWTH_PREFIX}g1.json").write_text(
        json.dumps(doc), "utf-8"
    )
    status = main(["profile", "show", "--rank", "--repo", str(project)])
    out = capsys.readouterr().out
    assert status == 0
    assert said in out
    assert never not in out


# The cap at scale

SIZES = (1000, 2000, 4000, 8000)


def fn(name: str, fraction: float) -> Hotspot:
    return Hotspot(
        function=name,
        file="app/m.py",
        line=1,
        frame_kind="python",
        is_library=False,
        self_fraction=fraction,
        total_fraction=fraction,
    )


def sized_run(benchmark: str, size: int, shares: dict[str, float]) -> SavedRun:
    functions: dict[FunctionKey, tuple[Hotspot, SizePoint]] = {}
    for name, share in shares.items():
        h = fn(name, share)
        functions[function_key(h)] = (h, SizePoint(share, 1000.0 * share))
    return SavedRun(benchmark, f"r{size}", size, size // 1000, 0.03, functions)


def planted_runs() -> list[SavedRun]:
    """The planted repo's measured shares at four sizes."""
    linear = (0.759, 0.599, 0.421, 0.265)
    quad = (0.232, 0.397, 0.578, 0.735)
    return [
        sized_run("p", size, {"linear": lin, "quadratic": q})
        for size, lin, q in zip(SIZES, linear, quad, strict=True)
    ]


def test_a_rising_function_outranks_a_falling_one_at_scale() -> None:
    rows = ranked(planted_runs(), {})
    assert [r["function"] for r in rows] == ["quadratic", "linear"]
    quad, lin = rows
    assert quad["cap"] == pytest.approx(1 / (1 - 0.735))
    assert lin["cap"] == pytest.approx(1 / (1 - 0.265))
    assert quad["ranked_in"]["size"] == lin["ranked_in"]["size"] == 8000
    assert quad["cap_known"]
    assert lin["cap_known"]


def test_the_best_any_size_cap_is_informational_only() -> None:
    rows = {r["function"]: r for r in ranked(planted_runs(), {})}
    lin = rows["linear"]
    best = lin["best_any_size"]
    assert best["cap"] == pytest.approx(1 / (1 - 0.759))
    assert best["size"] == 1000
    assert "not used to rank" in best["label"]
    assert lin["cap"] < best["cap"]
    # Ranked by it, linear would be first; it is not.
    assert best["cap"] > rows["quadratic"]["cap"]


def test_an_unseen_function_at_the_largest_size_has_unknown_cap() -> None:
    runs = planted_runs()
    runs[0] = sized_run(
        "p", 1000, {"linear": 0.759, "quadratic": 0.232, "gone": 0.9}
    )
    rows = ranked(runs, {})
    gone = rows[-1]
    assert gone["function"] == "gone"
    assert gone["cap_known"] is False
    assert gone["cap"] is None
    assert gone["total_fraction"] is None
    assert gone["ranked_in"] is None
    assert gone["wrapper"] is False
    assert gone["at_largest"] == [
        {
            "benchmark": "p",
            "size": 8000,
            "seen": False,
            "run_id": None,
            "total_fraction": None,
            "cap": None,
        }
    ]
    assert gone["best_any_size"]["cap"] == pytest.approx(1 / (1 - 0.9))


def test_the_largest_cap_across_benchmarks_counts() -> None:
    other = [
        sized_run("q", 1000, {"linear": 0.9, "quadratic": 0.1}),
        sized_run("q", 2000, {"linear": 0.5, "quadratic": 0.2}),
    ]
    rows = {r["function"]: r for r in ranked([*planted_runs(), *other], {})}
    lin = rows["linear"]
    assert lin["cap"] == pytest.approx(2.0)
    assert lin["ranked_in"]["benchmark"] == "q"
    assert {a["benchmark"]: a["size"] for a in lin["at_largest"]} == {
        "p": 8000,
        "q": 2000,
    }


def test_a_single_size_profile_is_its_own_largest() -> None:
    run = saved("a", SHORT, "r1")
    for row in ranked([run], {}):
        assert row["cap"] == row["best_any_size"]["cap"]
        assert row["cap_known"] is True
        assert row["ranked_in"]["run_id"] == "r1"
        assert row["at_largest"][0]["size"] is None
