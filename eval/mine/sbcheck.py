"""Check tasks through the eval user's real setup path (no agent run).

Usage: ``uv run python eval/mine/sbcheck.py <tasks-file> <id>...``

Stages the harness bundle for the eval user as ``skill_eval.py run``
does, then in the guest, for each manifest id, seeds the repository
from the cache and calls ``runner.build_real_repo`` (the build steps
behind the sandbox profile, planted files removed, the oracle run on
the untouched tree), then runs the task's speed check once. No model
is called. Prints one ``RESULT {json}`` line per task: ``ok``, the
tail of the failing step in ``reason``, ``setup_s`` and the speed
check's output. ``ok`` and ``reason`` are the ``builds`` input of
``heldout-mixed-draw.json`` (see ``draw_mixed_heldout.py``). The check
holds the eval user's lock, so it fails when another eval runs. The
tasks file must name every manifest id given.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

SKILL = str(Path(__file__).resolve().parents[1] / "skill")
if SKILL not in sys.path:
    sys.path.insert(0, SKILL)
# The harness modules live in eval/skill, a sibling package; loaded by
# name as realrepo.py loads the mine modules.
eval_user: Any = importlib.import_module("eval_user")
runner: Any = importlib.import_module("runner")

GUEST = r"""
import json, shutil, sys, time
from pathlib import Path

bundle, work_root, manifest = (Path(sys.argv[1]), Path(sys.argv[2]),
                               sys.argv[3])
sys.path[:0] = [str(bundle / "eval" / "skill"), str(bundle / "eval" / "mine")]
import realrepo, runner, sandbox, taskset

ts = taskset.load(bundle / "tasks-file.json")
task = next(t for t in ts.tasks if t.repo and t.repo.manifest == manifest)
found = sorted((bundle / "eval" / "tasks").glob(f"*/{manifest}.json"))
data = json.loads(found[0].read_text())
checks = json.loads(realrepo.CHECKS_FILE.read_text())[manifest]
change = data["reference_change"]
url = str(change["url"])
spec = realrepo.RepoSpec(
    repository=data["repository"],
    task_commit=data["task_commit"],
    reference_commit=change["commit"],
    reference_number=url.rsplit("/", 1)[-1] if "/pull/" in url else "",
    reference_title=str(change.get("title", "")),
    setup=[c.replace(realrepo._WORKLOADS_RELATIVE, str(realrepo.WORKLOADS))
           for c in checks["setup"]],
    env=dict(checks.get("env", {})),
    oracle=(data.get("oracle") or {}).get("command"),
    submodules=list(checks.get("submodules", [])),
    planted=list(checks.get("planted", [])),
)
work = work_root / manifest
shutil.rmtree(work, ignore_errors=True)
path_dir = work / "bin"
path_dir.mkdir(parents=True)
repo = work / "repos" / task.id
sandbox.reset_tmp(work / "sandbox" / task.id / "private")
toolchains = realrepo.toolchain_dirs(task.repo)
extra_path = [*toolchains, path_dir]
missing = realrepo.missing_requirements(task.repo)
result = {"id": manifest, "ok": False, "reason": "", "setup_s": None,
          "speed": None}
if missing:
    result["reason"] = "missing on this machine: " + ", ".join(missing)
    print("RESULT " + json.dumps(result))
    raise SystemExit(0)
t0 = time.monotonic()
try:
    runner.build_real_repo(
        task, spec, repo, None, work_root / "cache", extra_path,
        runner.before_setup_for(task, spec, repo, extra_path,
                                bool(toolchains)),
    )
except realrepo.SetupError as exc:
    result["reason"] = str(exc)[-9000:]
    result["setup_s"] = round(time.monotonic() - t0, 1)
    print("RESULT " + json.dumps(result))
    raise SystemExit(0)
result["setup_s"] = round(time.monotonic() - t0, 1)
result["ok"] = True
check = task.speed_check
if check is not None:
    env = realrepo.task_env(spec, repo, extra_path)
    read = [*extra_path, realrepo.WORKLOADS]
    speed = {}
    if check.build:
        code, tail = realrepo.run_command(
            check.build, repo, env, 1800, confine=True, read=read)
        speed["build"] = {"exit": code, "tail": tail[-300:]}
    t1 = time.monotonic()
    code, tail = realrepo.run_command(
        check.command, repo, env, 900, confine=True, read=read)
    speed["command"] = {"exit": code, "tail": tail[-300:],
                        "wall_s": round(time.monotonic() - t1, 2)}
    result["speed"] = speed
print("RESULT " + json.dumps(result))
"""


def main(argv: list[str]) -> int:
    """Stage the bundle, check each id, print the results.

    Parameters
    ----------
    argv : list of str
        The tasks file, then the manifest ids, in the order to check.

    Returns
    -------
    int
        0.
    """
    tasks_file = Path(argv[0]).resolve()
    ids = argv[1:]
    user = eval_user.require()
    with eval_user.exclusive():
        eval_user.sweep(user)
        staged = eval_user.stage(
            user,
            runner.REPO_ROOT,
            runner.SKILL_SOURCE,
            tasks_file,
            Path("scratch/stage").resolve(),
        )
        sys.stdout.write(f"staged {staged.wheel}\n")
        work_root = user.work / "work" / "sbcheck"
        for ident in ids:
            done = eval_user.run(
                user,
                [
                    user.tool_venv / "bin" / "python",
                    "-c",
                    GUEST,
                    str(user.bundle),
                    str(work_root),
                    ident,
                ],
                env={
                    **eval_user.guest_env(user),
                    "PERFPROOF_EVAL_VENV": str(user.tool_venv),
                },
                timeout=7200,
            )
            out = done.stdout.decode()
            line = next(
                (ln for ln in out.splitlines() if ln.startswith("RESULT ")),
                None,
            )
            if line is None:
                tail = (out + done.stderr.decode())[-1500:]
                line = "RESULT " + json.dumps(
                    {
                        "id": ident,
                        "ok": False,
                        "reason": f"guest exit {done.returncode}: {tail}",
                    }
                )
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
            eval_user.sweep(user)
            eval_user.remove(user, work_root / ident)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
