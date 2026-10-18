"""``combine`` and the run guards, on synthetic result files."""

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import label
import trees

HERE = Path(__file__).resolve().parent


def _result(
    machine: str,
    perf: str,
    reason: str = "",
    correctness: str = "pass",
    **overrides: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": label.SCHEMA,
        "task_id": "some-task",
        "mode": "ab",
        "purpose": "label",
        "valid": True,
        "problems": [],
        "task_commit": "a" * 40,
        "reference_commit": "b" * 40,
        "machine": {"name": machine, "arch": "x", "cpu_model": "cpu"},
        "rule": {"readme_sha256": "0" * 64},
        "correctness": {"label": {"label": correctness, "reason": ""}},
        "performance": {
            "analysis": {
                "machine_label": {"label": perf, "reason": reason},
                "bootstrap": {"estimate": 1.1},
            }
        },
    }
    result.update(overrides)
    return result


def test_combine_agree() -> None:
    got = label.combine(
        _result("mac", "improvement"), _result("vm", "improvement")
    )
    assert got["performance"] == {"label": "improvement", "reason": ""}
    assert got["correctness"] == "pass"
    assert [m["name"] for m in got["machines"]] == ["mac", "vm"]


def test_combine_disagree_is_unstable() -> None:
    got = label.combine(
        _result("mac", "improvement"),
        _result("vm", "below-bar", correctness="fail"),
    )
    assert got["performance"]["label"] == "unstable"
    assert "machines disagree" in got["performance"]["reason"]
    assert got["correctness"] == "fail"


def test_combine_one_unstable_is_unstable() -> None:
    got = label.combine(
        _result("mac", "regression"),
        _result("vm", "unstable", reason="not-reproduced"),
    )
    assert got["performance"]["label"] == "unstable"
    assert "vm: unstable (not-reproduced)" in got["performance"]["reason"]


def test_combine_aa_correctness_is_not_applicable() -> None:
    first = _result("mac", "below-bar", correctness="not-applicable")
    second = _result("vm", "below-bar", correctness="not-applicable")
    assert label.combine(first, second)["correctness"] == "not-applicable"


@pytest.mark.parametrize(
    ("second", "problem"),
    [
        (_result("mac", "improvement"), "same machine name"),
        (_result("vm", "improvement", purpose="pilot"), "a pilot run"),
        (_result("vm", "improvement", valid=False), "invalid"),
        (_result("vm", "improvement", task_id="other"), "task_id differs"),
        (_result("vm", "improvement", mode="aa"), "mode differs"),
        (
            _result("vm", "improvement", reference_commit="c" * 40),
            "reference_commit differs",
        ),
        (_result("vm", "improvement", schema="other"), "not a labeller"),
    ],
)
def test_combine_refuses(second: dict[str, Any], problem: str) -> None:
    with pytest.raises(label.RefusedError, match=problem):
        label.combine(_result("mac", "improvement"), second)


def _args(**kw: Any) -> argparse.Namespace:
    base = {"held_out": False, "pilot": False, "pairs": 200}
    base.update(kw)
    return argparse.Namespace(**base)


def _task(split: str) -> trees.Task:
    task = next(t for t in trees.load_tasks() if t.split == "development")
    return task.model_copy(update={"split": split})


def test_held_out_needs_the_flag_and_no_pilot() -> None:
    held = _task("held-out")
    with pytest.raises(label.RefusedError, match="pass --held-out"):
        label._guard(held, _args())  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(label.RefusedError, match="a pilot never"):
        label._guard(  # pyright: ignore[reportPrivateUsage]
            held, _args(held_out=True, pilot=True)
        )


def test_a_label_needs_two_hundred_pairs() -> None:
    dev = _task("development")
    label._guard(dev, _args())  # pyright: ignore[reportPrivateUsage]
    label._guard(dev, _args(pilot=True, pairs=10))  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(label.RefusedError, match="at least 200"):
        label._guard(dev, _args(pairs=199))  # pyright: ignore[reportPrivateUsage]


def test_rule_provenance_names_the_readme() -> None:
    got = label.rule_provenance()
    assert len(got["readme_sha256"]) == 64
    assert isinstance(got["committed_unchanged"], bool)


def test_validity() -> None:
    ok = {"exit_code": 0}
    pairs = [{"a": ok, "b": ok}] * 200
    quiet = [{"quiet": {"passed": True}}]
    valid = label._valid  # pyright: ignore[reportPrivateUsage]
    assert valid(pairs, 200, quiet, pilot=False) == (True, [])
    assert not valid(pairs[:199], 200, quiet, pilot=False)[0]
    assert not valid(pairs, 200, quiet, pilot=True)[0]
    loud = [{"quiet": {"passed": False}}]
    assert not valid(pairs, 200, loud, pilot=False)[0]
    bad = [*pairs, {"a": ok, "b": {"exit_code": 1}}]
    assert not valid(bad, 200, quiet, pilot=False)[0]


def test_nothing_imports_perfproof() -> None:
    banned_calls = {"__import__", "exec"}
    for path in sorted(HERE.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in {"perfproof", "importlib"}, (
                    path.name
                )
            if isinstance(node, ast.Name):
                assert node.id not in banned_calls, (path.name, node.id)
            if isinstance(node, ast.Attribute):
                assert node.attr != "import_module", path.name


def test_no_perfproof_module_loads_at_runtime() -> None:
    pytest.importorskip("pyperf")
    modules = sorted(
        p.stem
        for p in HERE.glob("*.py")
        if not p.name.startswith("test_") and p.name != "conftest.py"
    )
    code = (
        "import sys, importlib\n"
        f"sys.path.insert(0, {str(HERE)!r})\n"
        f"for m in {modules!r}:\n"
        "    importlib.import_module(m)\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] == "
        "'perfproof')\n"
        "print(__import__('json').dumps(bad))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.splitlines()[-1]) == []


def _entry(oracle: int, failing: list[str] | None = None) -> dict[str, Any]:
    return {
        "oracle": {"exit_code": oracle},
        "suite": None
        if failing is None
        else {
            "run": {"exit_code": 1 if failing else 0},
            "results": {"failing": failing},
        },
    }


def test_oracle_decides_without_a_suite_recipe() -> None:
    correct = label._correctness_label  # pyright: ignore[reportPrivateUsage]
    failing = correct(None, {"reference": _entry(1), "task": _entry(1)})
    assert failing["label"] == "fail"
    assert "oracle exits 1" in failing["reason"]
    passing = correct(None, {"reference": _entry(0), "task": _entry(1)})
    assert passing == {"label": "unknown", "reason": "no full-suite recipe"}


def _git_repo(root: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                *args,
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )

    git("init", "-q")
    for name in ("README.md", "decide.py"):
        (root / name).write_text(name)
    git("add", "-A")
    git("commit", "-qm", "rule")


def test_held_out_runs_once_every_rule_file_is_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git_repo(tmp_path)
    files = ("README.md", "decide.py")
    real = label.rule_provenance
    clean = real(tmp_path, files)
    assert clean["committed_unchanged"] is True
    assert set(clean["files_sha256"]) == set(files)
    monkeypatch.setattr(
        label,
        "rule_provenance",
        lambda: real(tmp_path, files),
    )
    label._guard(  # pyright: ignore[reportPrivateUsage]
        _task("held-out"), _args(held_out=True)
    )
    (tmp_path / "decide.py").write_text("changed")
    assert real(tmp_path, files)["committed_unchanged"] is False
    with pytest.raises(label.RefusedError, match="not committed unchanged"):
        label._guard(  # pyright: ignore[reportPrivateUsage]
            _task("held-out"), _args(held_out=True)
        )


def test_the_guard_covers_the_code_and_recipes() -> None:
    assert {
        "decide.py",
        "boot.py",
        "analyse.py",
        "suites.json",
        "label.py",
        "trees.py",
        "machine.py",
        "../mine/checks.json",
    } <= set(label.RULE_FILES)
    for name in label.RULE_FILES:
        assert (HERE / name).exists()


def _result_file(tmp_path: Path, **data: Any) -> Path:
    out = tmp_path / "t.ab.json"
    out.write_text(json.dumps(data))
    return out


def test_valid_and_pilot_results_are_kept(tmp_path: Path) -> None:
    rerun = label._rerun_reason  # pyright: ignore[reportPrivateUsage]
    assert rerun(_result_file(tmp_path, valid=True, problems=[])) is None
    pilot = _result_file(
        tmp_path,
        valid=False,
        purpose="pilot",
        problems=["a pilot run, for timing only"],
    )
    assert rerun(pilot) is None


def test_invalid_results_are_rerun(tmp_path: Path) -> None:
    rerun = label._rerun_reason  # pyright: ignore[reportPrivateUsage]
    setup = _result_file(
        tmp_path, valid=False, purpose="label", problems=["setup failed"]
    )
    assert rerun(setup) == "setup failed"
    few = _result_file(
        tmp_path, valid=False, purpose="label", problems=["5 pairs, few"]
    )
    assert rerun(few) is not None
    assert rerun(tmp_path / "missing.json") is not None


def test_set_aside_keeps_the_evidence_and_clears_the_logs(
    tmp_path: Path,
) -> None:
    out = _result_file(tmp_path, valid=False)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "pairs.jsonl").write_text("x")
    label._set_aside(out, run_dir)  # pyright: ignore[reportPrivateUsage]
    assert not out.exists()
    assert not (run_dir / "pairs.jsonl").exists()
    assert len(list(tmp_path.glob("t.ab.invalid-*.json"))) == 1
    assert len(list(run_dir.glob("pairs.jsonl.invalid-*"))) == 1


def test_aa_runs_the_task_tree_on_both_arms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert label._arm_trees(True) == {  # pyright: ignore[reportPrivateUsage]
        "a": "task",
        "b": "task",
    }
    assert label._arm_trees(False) == {  # pyright: ignore[reportPrivateUsage]
        "a": "task",
        "b": "reference",
    }
    seen: list[tuple[str, ...]] = []

    class StopError(Exception):
        pass

    def fake_run_pairs(arms: Any, *_: Any, **__: Any) -> None:
        seen.append(tuple(f"{a.name}:{a.tree}:{a.path}" for a in arms))
        raise StopError

    monkeypatch.setattr(label.measure, "run_pairs", fake_run_pairs)

    def fake_quiet(**_kw: float) -> label.machine.Quiet:
        return label.machine.Quiet(True, 1.0, [], "")

    monkeypatch.setattr(label.machine, "quiet_check", fake_quiet)
    task_dir = tmp_path / "task"
    ref_dir = tmp_path / "ref"
    paths: dict[trees.Tree, Path] = {
        "task": task_dir,
        "reference": ref_dir,
    }
    base = {
        "machine": {"perf": {"works": False, "reason": "t", "version": None}},
        "mode": "aa",
        "purpose": "pilot",
        "task_commit": "a" * 40,
        "reference_commit": None,
    }
    args = _args(
        aa=True, pilot=True, pairs=4, seed=1, machine="m", quiet_wait=0.0
    )
    (tmp_path / "run").mkdir()
    spec = {"workload": "true"}
    with pytest.raises(StopError):
        label._measure_and_check(  # pyright: ignore[reportPrivateUsage]
            "t",
            args,
            spec,
            paths,
            base,
            tmp_path / "o.json",
            tmp_path / "run",
            0.0,
            {},
        )
    assert seen == [(f"a:task:{task_dir}", f"b:task:{task_dir}")]


def _task_for_run(split: str, monkeypatch: pytest.MonkeyPatch) -> trees.Task:
    task = _task(split)

    def by_id(_id: str) -> trees.Task:
        return task

    def spec_for(_id: str) -> dict[str, str]:
        return {"workload": "true"}

    monkeypatch.setattr(trees, "task_by_id", by_id)
    monkeypatch.setattr(trees, "spec_for", spec_for)
    return task


def test_a_refused_held_out_run_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task_for_run("held-out", monkeypatch)
    monkeypatch.setattr(label, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(trees, "WORK", tmp_path / "work")
    args = _args(aa=False, machine="m")
    out = label.result_path("label", "m", task.id, "ab")
    out.parent.mkdir(parents=True)
    out.write_text(json.dumps({"valid": False, "problems": ["setup failed"]}))
    run_dir = tmp_path / "work" / task.id / "ab.label.m"
    run_dir.mkdir(parents=True)
    (run_dir / "pairs.jsonl").write_text("x")
    assert label.run_task(task.id, args) == 1
    assert out.exists()
    assert (run_dir / "pairs.jsonl").read_text() == "x"
    assert not list(out.parent.glob("*.invalid-*"))


def test_run_task_reruns_an_invalid_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task_for_run("development", monkeypatch)
    monkeypatch.setattr(label, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(label, "HERE", tmp_path)
    monkeypatch.setattr(trees, "WORK", tmp_path / "work")
    args = _args(aa=False, machine="m")
    out = label.result_path("label", "m", task.id, "ab")
    out.parent.mkdir(parents=True)
    out.write_text(json.dumps({"valid": False, "problems": ["setup failed"]}))
    run_dir = tmp_path / "work" / task.id / "ab.label.m"
    run_dir.mkdir(parents=True)
    (run_dir / "pairs.jsonl").write_text("x")

    class ReachedError(Exception):
        pass

    def reached(*_a: Any, **_k: Any) -> Any:
        raise ReachedError

    monkeypatch.setattr(label.machine, "describe", reached)
    with pytest.raises(ReachedError):
        label.run_task(task.id, args)
    assert not out.exists()
    assert len(list(out.parent.glob("*.invalid-*.json"))) == 1
    assert not (run_dir / "pairs.jsonl").exists()
    # A valid result, by contrast, is skipped and the run never starts.
    out.write_text(json.dumps({"valid": True, "problems": []}))
    assert label.run_task(task.id, args) == 0
