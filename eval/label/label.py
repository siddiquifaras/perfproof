"""Label evaluation tasks independently of perfproof.

Usage, from the repository root, with pyperf from
``eval/label/requirements.txt``: ``uv run --with-requirements
eval/label/requirements.txt python eval/label/label.py <command> ...``

Commands:

``machine --machine NAME``
    Print what the labeller records about this machine.
``quiet``
    Run the quiet check once and print its outcome.
``run --machine NAME [--aa] [--pilot] [--pairs N] ID ...``
    Build, measure and check each task, writing one JSON result per
    task under ``eval/label/results/<purpose>/<machine>/``. Re-running
    the same command resumes: finished results are skipped, a
    half-finished pair log continues with its recorded seed, and ready
    trees are reused.
``combine RESULT RESULT [-o OUT]``
    Give the final label from two machines' results.

The rule is in ``eval/label/README.md``. Nothing here imports
perfproof.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import analyse
import decide
import machine
import measure
import suites
import trees

HERE = Path(__file__).resolve().parent
README = HERE / "README.md"
RESULTS = HERE / "results"
SCHEMA = "perfproof-eval-label/1"
EXIT_QUIET = 3
GIT_TIMEOUT_S = 30.0


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _git(args: list[str], root: Path = HERE) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, proc.stdout.strip()


# Everything that decides a label: the written rule, the statistics and
# the pair order, the suite recipes behind correctness, the code that
# applies them, and the oracles and build recipes in eval/mine.
RULE_FILES = (
    "README.md",
    "decide.py",
    "boot.py",
    "analyse.py",
    "measure.py",
    "pyperf_stats.py",
    "suites.py",
    "suites.json",
    "label.py",
    "trees.py",
    "machine.py",
    "../mine/checks.json",
)


def rule_provenance(
    root: Path = HERE, files: tuple[str, ...] = RULE_FILES
) -> dict[str, Any]:
    """Return which decision rule a result was made under.

    Parameters
    ----------
    root : Path
        The directory holding the rule's files (a git work tree).
    files : tuple of str
        The files, relative to ``root``, that make up the rule.

    Returns
    -------
    dict
        ``readme_sha256`` of the README, ``files_sha256`` of every rule
        file, ``git_commit`` (HEAD, or None outside git) and
        ``committed_unchanged``: True when every file is tracked and
        has no uncommitted change.
    """
    shas = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in files
    }
    code, head = _git(["rev-parse", "HEAD"], root)
    unchanged = True
    for name in files:
        tracked, _ = _git(["ls-files", "--error-unmatch", name], root)
        dirty_code, dirty = _git(["status", "--porcelain", "--", name], root)
        if tracked != 0 or dirty_code != 0 or dirty:
            unchanged = False
    return {
        "readme_sha256": shas.get("README.md", ""),
        "files_sha256": shas,
        "git_commit": head if code == 0 else None,
        "committed_unchanged": unchanged,
    }


def result_path(
    purpose: str, machine_name: str, task_id: str, mode: str
) -> Path:
    """Return where a task's result is written.

    Parameters
    ----------
    purpose : {"label", "pilot"}
        Whether the run can give a label.
    machine_name : str
        The ``--machine`` name.
    task_id : str
        The task.
    mode : {"ab", "aa"}
        Reference against task tree, or task tree against itself.

    Returns
    -------
    Path
        ``results/<purpose>/<machine>/<task-id>.<mode>.json``.
    """
    return RESULTS / purpose / machine_name / f"{task_id}.{mode}.json"


def _write_json(path: Path, data: dict[str, Any]) -> None:
    # Written whole and renamed, so a result is never half there.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n")
    tmp.replace(path)


def _read_sidecar(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else None


def _correctness(
    task_id: str,
    spec: dict[str, Any],
    paths: dict[trees.Tree, Path],
    side: Path,
) -> dict[str, Any]:
    """Run the full suite and the oracle on each tree, once.

    Results are kept in ``side`` so a resumed run doesn't repeat them.
    """
    done = _read_sidecar(side)
    if done is not None:
        return cast(dict[str, Any], done)
    recipe = suites.recipes().get(task_id)
    prefix = trees.env_prefix(spec)
    per_tree: dict[str, Any] = {}
    for tree, path in paths.items():
        env = trees.tree_env(path, spec)
        entry: dict[str, Any] = {"suite": None, "oracle": None}
        if recipe is not None:
            timeout = float(recipe.get("timeout_s", suites.DEFAULT_TIMEOUT_S))
            setup_ok = True
            records: list[dict[str, Any]] = []
            for line in cast(list[str], recipe.get("setup", [])):
                rec = trees.run_logged(
                    "suite-setup", tree, line, path, env, timeout, prefix
                )
                records.append(rec.to_json())
                setup_ok = setup_ok and rec.exit_code == 0
                if not setup_ok:
                    break
            entry["suite_setup"] = records
            if setup_ok:
                (path / suites.JUNIT).unlink(missing_ok=True)
                log = path.parent / f"suite.{tree}.log"
                rec = trees.run_logged(
                    "suite",
                    tree,
                    str(recipe["command"]),
                    path,
                    env,
                    timeout,
                    prefix,
                    log=log,
                )
                read = suites.read_results(
                    str(recipe["reader"]), log.read_text(), path
                )
                if rec.exit_code == 124:
                    read = suites.SuiteResult(reason="the suite timed out")
                entry["suite"] = {
                    "run": rec.to_json(),
                    "results": read.to_json(),
                }
        oracle = trees.run_logged(
            "oracle",
            tree,
            str(spec["oracle"]),
            path,
            env,
            trees.check.TIMEOUT_S,
            prefix,
        )
        entry["oracle"] = oracle.to_json()
        per_tree[tree] = entry
    data = {
        "suite_recipe": recipe,
        "trees": per_tree,
        "label": _correctness_label(recipe, per_tree),
    }
    side.write_text(json.dumps(data))
    return data


def _failing(entry: dict[str, Any] | None) -> tuple[set[str] | None, bool]:
    # The failing ids, and whether the suite's results explain its exit.
    if entry is None or entry.get("suite") is None:
        return None, False
    results = entry["suite"]["results"]
    if results["failing"] is None:
        return None, False
    failing = set(cast(list[str], results["failing"]))
    exit_code = int(entry["suite"]["run"]["exit_code"])
    return failing, exit_code == 0 or bool(failing)


def _correctness_label(
    recipe: dict[str, Any] | None, per_tree: dict[str, Any]
) -> dict[str, str]:
    ref = per_tree.get("reference")
    ref_failing, ref_read = _failing(ref)
    task_failing, _ = _failing(per_tree.get("task"))
    oracle = None if ref is None else ref["oracle"]
    label, reason = decide.correctness_label(
        ref_failing,
        task_failing,
        ref_read,
        None if oracle is None else int(oracle["exit_code"]),
    )
    if recipe is None and label == "unknown":
        reason = "no full-suite recipe"
    return {"label": label, "reason": reason}


def _arm_trees(aa: bool) -> dict[str, str]:
    """Return which tree each arm runs.

    Parameters
    ----------
    aa : bool
        Whether this is the A/A mode.

    Returns
    -------
    dict
        Arm name to tree name: both arms the task tree in A/A mode,
        else A the task tree and B the reference tree.
    """
    return {"a": "task", "b": "task" if aa else "reference"}


class RefusedError(RuntimeError):
    """The labeller won't run this task as asked."""


def _guard(task: trees.Task, args: argparse.Namespace) -> None:
    if task.split == "held-out":
        if not args.held_out:
            msg = f"{task.id} is held out; pass --held-out to label it"
            raise RefusedError(msg)
        if args.pilot:
            msg = f"{task.id} is held out; a pilot never runs one"
            raise RefusedError(msg)
        if not rule_provenance()["committed_unchanged"]:
            msg = (
                "a file of the decision rule (RULE_FILES) is not "
                "committed unchanged"
            )
            raise RefusedError(msg)
    if not args.pilot and args.pairs < decide.MIN_PAIRS:
        msg = f"a label needs at least {decide.MIN_PAIRS} pairs"
        raise RefusedError(msg)


def _workload_perf(
    workload: str, probe: machine.PerfProbe
) -> tuple[bool, str]:
    argv = shlex.split(workload)
    if not probe.works:
        return False, probe.reason
    if argv and argv[0] == "docker":
        return False, (
            "a Docker workload: the container's processes are not "
            "children of perf stat"
        )
    return True, ""


def _valid(
    pairs: list[dict[str, Any]],
    n: int,
    segments: list[dict[str, Any]],
    pilot: bool,
) -> tuple[bool, list[str]]:
    problems: list[str] = []
    if pilot:
        problems.append("a pilot run, for timing only")
    if len(pairs) < max(n, decide.MIN_PAIRS):
        problems.append(f"{len(pairs)} pairs, fewer than needed")
    if any(p[arm]["exit_code"] != 0 for p in pairs for arm in ("a", "b")):
        problems.append("an execution exited non-zero")
    if not all(s["quiet"]["passed"] for s in segments):
        problems.append("a segment started without passing the quiet check")
    return not problems, problems


# Problems a resumed run cannot fix by adding pairs: its pair log is
# set aside and the run starts over. A pilot's own note is not one.
_PILOT_NOTE = "a pilot run, for timing only"


def _rerun_reason(out: Path) -> str | None:
    # None when the result stands; else why it is invalid.
    try:
        data = json.loads(out.read_text())
    except (OSError, ValueError):
        return "the result file is unreadable"
    if data.get("valid") is True:
        return None
    problems = [p for p in data.get("problems", []) if p != _PILOT_NOTE]
    if data.get("purpose") == "pilot" and not problems:
        return None
    return "; ".join(problems) or "marked invalid"


def _set_aside(out: Path, run_dir: Path) -> None:
    # Keeps the invalid evidence, and clears the logs that would make
    # the run resume into the same invalid state.
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out.rename(out.with_name(f"{out.stem}.invalid-{stamp}.json"))
    for name in ("pairs.jsonl", "segments.json"):
        path = run_dir / name
        if path.exists():
            path.rename(path.with_name(f"{name}.invalid-{stamp}"))


def run_task(task_id: str, args: argparse.Namespace) -> int:
    """Build, measure and check one task, and write its result.

    Parameters
    ----------
    task_id : str
        The task.
    args : argparse.Namespace
        The ``run`` command's options.

    Returns
    -------
    int
        0 when a result was written or a valid one already existed,
        `EXIT_QUIET` when the quiet check refused, 1 otherwise.
    """
    mode = "aa" if args.aa else "ab"
    purpose = "pilot" if args.pilot else "label"
    out = result_path(purpose, args.machine, task_id, mode)
    task = trees.task_by_id(task_id)
    try:
        _guard(task, args)
        spec = trees.spec_for(task_id)
    except RefusedError as exc:
        _out(f"{task_id}: refused: {exc}")
        return 1
    except KeyError:
        _out(f"{task_id}: refused: no recipe in eval/mine/checks.json")
        return 1
    if out.exists():
        why = _rerun_reason(out)
        if why is None:
            _out(
                f"{task_id}: result exists, skipped ({out.relative_to(HERE)})"
            )
            return 0
        _out(f"{task_id}: re-running, the existing result is invalid: {why}")
        _set_aside(
            out, trees.WORK / task_id / f"{mode}.{purpose}.{args.machine}"
        )
    _out(f"{task_id} ({mode}, {purpose}, {args.machine})")
    started = time.monotonic()
    host = machine.describe(args.machine)
    want: list[trees.Tree] = ["task"] if args.aa else ["task", "reference"]
    run_dir = trees.WORK / task_id / f"{mode}.{purpose}.{args.machine}"
    run_dir.mkdir(parents=True, exist_ok=True)
    base: dict[str, Any] = {
        "schema": SCHEMA,
        "task_id": task_id,
        "split": task.split,
        "family": task.family,
        "target_class": task.target_class,
        "mode": mode,
        "purpose": purpose,
        "machine": host.to_json(),
        "rule": rule_provenance(),
        "task_commit": task.task_commit,
        "reference_commit": None if args.aa else task.reference_change.commit,
        "arm_trees": _arm_trees(args.aa),
        "reference_kind": task.reference_change.kind,
        "workload": spec["workload"],
        "recipe_sha256": trees.digest(spec),
    }
    try:
        t0 = time.monotonic()
        paths, setup, notes = trees.prepare(task, spec, want)
        setup_s = time.monotonic() - t0
    except trees.SetupError as exc:
        trees.teardown(spec)
        _write_json(
            out,
            {
                **base,
                "valid": False,
                "problems": ["setup failed"],
                "setup": [r.to_json() for r in exc.records],
                "label": None,
            },
        )
        _out(f"  -> setup failed: {exc}")
        return 1
    try:
        return _measure_and_check(
            task_id,
            args,
            spec,
            paths,
            base,
            out,
            run_dir,
            started,
            {
                "records": [r.to_json() for r in setup],
                "notes": notes,
                "seconds": round(setup_s, 1),
            },
        )
    finally:
        trees.teardown(spec)


def _measure_and_check(
    task_id: str,
    args: argparse.Namespace,
    spec: dict[str, Any],
    paths: dict[trees.Tree, Path],
    base: dict[str, Any],
    out: Path,
    run_dir: Path,
    started: float,
    setup: dict[str, Any],
) -> int:
    host = base["machine"]
    arm_b_tree = cast(trees.Tree, _arm_trees(args.aa)["b"])
    arm_list = (
        measure.Arm(
            "a", "task", paths["task"], trees.tree_env(paths["task"], spec)
        ),
        measure.Arm(
            "b",
            arm_b_tree,
            paths[arm_b_tree],
            trees.tree_env(paths[arm_b_tree], spec),
        ),
    )
    header = {
        "task_id": task_id,
        "mode": base["mode"],
        "purpose": base["purpose"],
        "machine": args.machine,
        "task_commit": base["task_commit"],
        "reference_commit": base["reference_commit"],
        "workload": spec["workload"],
        "pairs": args.pairs,
    }
    log = run_dir / "pairs.jsonl"
    try:
        found, pairs = measure.read_log(log, header)
    except measure.LogMismatchError as exc:
        _out(f"  refused: {exc}")
        return 1
    if found is None:
        seed = args.seed if args.seed is not None else secrets.randbits(64)
    else:
        seed = int(found["seed"])
        if args.seed is not None and args.seed != seed:
            _out(f"  refused: the log's seed is {seed}, not {args.seed}")
            return 1
    # Rewritten clean, so a line cut off by a crash isn't appended to.
    log.write_text(
        "".join(
            json.dumps(x) + "\n" for x in [{**header, "seed": seed}, *pairs]
        )
    )
    seg_path = run_dir / "segments.json"
    segments = cast(list[dict[str, Any]], _read_sidecar(seg_path) or [])
    failed = any(p[a]["exit_code"] != 0 for p in pairs for a in ("a", "b"))
    use_perf, perf_reason = _workload_perf(
        spec["workload"], machine.PerfProbe(**host["perf"])
    )
    if len(pairs) < args.pairs and not failed:
        quiet = machine.quiet_check(
            wait_s=0.0 if args.pilot else args.quiet_wait
        )
        if not quiet.passed and not args.pilot:
            _out(f"  quiet check refused: {quiet.reason}")
            return EXIT_QUIET
        seg: dict[str, Any] = {
            "started": _now(),
            "quiet": {
                "passed": quiet.passed,
                "threshold": quiet.threshold,
                "samples": quiet.samples,
                "reason": quiet.reason,
            },
            "load1_before": machine.load1(),
        }
        t0 = time.monotonic()

        def progress(done: int, n: int) -> None:
            if done % 20 == 0 or done == n:
                _out(f"  pair {done}/{n}")

        seg.update(
            measure.run_pairs(
                arm_list,
                spec["workload"],
                args.pairs,
                seed,
                log,
                len(pairs),
                use_perf,
                progress,
            )
        )
        seg["seconds"] = round(time.monotonic() - t0, 1)
        seg["load1_after"] = machine.load1()
        segments.append(seg)
        seg_path.write_text(json.dumps(segments))
        _, pairs = measure.read_log(log, header)
    pairs_s = sum(float(s["seconds"]) for s in segments)
    correctness: dict[str, Any] | None = None
    t0 = time.monotonic()
    if args.aa:
        correctness = {
            "label": {"label": "not-applicable", "reason": "A/A mode"}
        }
    elif not args.skip_correctness:
        correctness = _correctness(
            task_id, spec, paths, run_dir / "correctness.json"
        )
    correctness_s = time.monotonic() - t0
    valid, problems = _valid(pairs, args.pairs, segments, args.pilot)
    ok = pairs and all(
        p[a]["exit_code"] == 0 for p in pairs for a in ("a", "b")
    )
    analysis = (
        analyse.analyse(pairs, seed, out.parent, f"{task_id}.{base['mode']}")
        if ok and len(pairs) >= 2
        else None
    )
    label = None
    if valid and analysis is not None:
        label = analysis["machine_label"]["label"]
    result = {
        **base,
        "valid": valid,
        "problems": problems,
        "label": label,
        "correctness": correctness,
        "setup": setup,
        "performance": {
            "seed": seed,
            "pairs_requested": args.pairs,
            "pairs_done": len(pairs),
            "warmup_per_arm": measure.WARMUP,
            "perf_counters": {"used": use_perf, "reason": perf_reason},
            "segments": segments,
            "analysis": analysis,
            "pairs": pairs,
        },
        "timing_s": {
            "setup": setup["seconds"],
            "pairs": round(pairs_s, 1),
            "correctness": round(correctness_s, 1),
            "total_this_invocation": round(time.monotonic() - started, 1),
        },
        "written": _now(),
    }
    _write_json(out, result)
    shown = (
        label
        if label is not None
        else "no label (" + "; ".join(problems) + ")"
    )
    ml = analysis["machine_label"] if analysis else None
    _out(f"  -> {shown}; analysis: {ml}")
    if correctness is not None:
        _out(f"  -> correctness: {correctness['label']}")
    return 0


def combine(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """Give the final label from two machines' results.

    Parameters
    ----------
    first, second : dict
        Two result files' contents.

    Returns
    -------
    dict
        The final performance and correctness labels with each
        machine's.

    Raises
    ------
    RefusedError
        If the results are not two valid label runs of the same task,
        mode and commits from two differently named machines.
    """
    problems: list[str] = []
    for r in (first, second):
        name = r["machine"]["name"]
        if r.get("schema") != SCHEMA:
            problems.append(f"{name}: not a labeller result")
        if r.get("purpose") != "label":
            problems.append(f"{name}: a {r.get('purpose')} run")
        if not r.get("valid"):
            problems.append(
                f"{name}: invalid ({'; '.join(r.get('problems', []))})"
            )
    problems += [
        f"{key} differs"
        for key in ("task_id", "mode", "task_commit", "reference_commit")
        if first.get(key) != second.get(key)
    ]
    if first["machine"]["name"] == second["machine"]["name"]:
        problems.append("both results are from the same machine name")
    if problems:
        raise RefusedError("; ".join(problems))

    def perf_of(r: dict[str, Any]) -> tuple[str, decide.Label, str]:
        ml = r["performance"]["analysis"]["machine_label"]
        return r["machine"]["name"], ml["label"], ml["reason"]

    label, reason = decide.combine_performance(perf_of(first), perf_of(second))

    def corr_of(r: dict[str, Any]) -> str:
        c = r.get("correctness")
        return "unknown" if c is None else str(c["label"]["label"])

    c1, c2 = corr_of(first), corr_of(second)
    corr: str = (
        c1
        if c1 == c2 == "not-applicable"
        else decide.combine_correctness(
            cast(decide.Correctness, c1), cast(decide.Correctness, c2)
        )
    )
    return {
        "schema": SCHEMA + "/combined",
        "task_id": first["task_id"],
        "mode": first["mode"],
        "task_commit": first["task_commit"],
        "reference_commit": first["reference_commit"],
        "performance": {"label": label, "reason": reason},
        "correctness": corr,
        "machines": [
            {
                "name": r["machine"]["name"],
                "arch": r["machine"]["arch"],
                "cpu_model": r["machine"]["cpu_model"],
                "performance": r["performance"]["analysis"]["machine_label"],
                "estimate": r["performance"]["analysis"]["bootstrap"][
                    "estimate"
                ],
                "correctness": corr_of(r),
                "rule": r["rule"],
            }
            for r in (first, second)
        ],
    }


def _ids(args: argparse.Namespace) -> list[str]:
    ids = list(args.ids)
    if args.tasks_file:
        for line in Path(args.tasks_file).read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                ids.append(line)
    return ids


def main(argv: list[str] | None = None) -> int:
    """Run the labeller's command line.

    Parameters
    ----------
    argv : list of str, optional
        Arguments; defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        The exit status.
    """
    parser = argparse.ArgumentParser(description="Label evaluation tasks.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_machine = sub.add_parser("machine")
    p_machine.add_argument("--machine", required=True)
    p_quiet = sub.add_parser("quiet")
    p_quiet.add_argument("--wait", type=float, default=0.0)
    p_run = sub.add_parser("run")
    p_run.add_argument("ids", nargs="*")
    p_run.add_argument("--tasks-file")
    p_run.add_argument("--machine", required=True)
    p_run.add_argument("--aa", action="store_true")
    p_run.add_argument("--pilot", action="store_true")
    p_run.add_argument("--held-out", action="store_true")
    p_run.add_argument("--pairs", type=int, default=decide.MIN_PAIRS)
    p_run.add_argument("--seed", type=int)
    p_run.add_argument(
        "--quiet-wait", type=float, default=machine.QUIET_WAIT_S
    )
    p_run.add_argument("--skip-correctness", action="store_true")
    p_comb = sub.add_parser("combine")
    p_comb.add_argument("results", nargs=2)
    p_comb.add_argument("-o", "--output")
    args = parser.parse_args(argv)
    if args.command == "machine":
        _out(json.dumps(machine.describe(args.machine).to_json(), indent=1))
        return 0
    if args.command == "quiet":
        q = machine.quiet_check(wait_s=args.wait)
        _out(
            json.dumps(
                {
                    "passed": q.passed,
                    "threshold": q.threshold,
                    "samples": q.samples,
                    "reason": q.reason,
                }
            )
        )
        return 0 if q.passed else EXIT_QUIET
    if args.command == "combine":
        a, b = (json.loads(Path(p).read_text()) for p in args.results)
        try:
            final = combine(a, b)
        except RefusedError as exc:
            _out(f"refused: {exc}")
            return 1
        if args.output:
            _write_json(Path(args.output), final)
        _out(json.dumps(final, indent=1))
        return 0
    status = 0
    for task_id in _ids(args):
        code = run_task(task_id, args)
        if code == EXIT_QUIET:
            return EXIT_QUIET
        status = status or code
    return status


if __name__ == "__main__":
    raise SystemExit(main())
