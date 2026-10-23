"""Run golden-set comparisons with injection (real compare).

For each workload a fixture repository holds the target and
``golden.arm`` (``A``); the candidate tree is a copy of it with
``golden.arm`` set to ``B``. A replicate sets ``GOLDEN_SPIN_A`` to the
cell's fraction ``f`` (the baseline is slowed, so the candidate is
faster by ``1 + f``), runs ``perfproof compare`` once to its maximum
rounds, and joins perfproof's ``samples.npz`` with the wrapper's
per-execution log. Output is one CSV row per execution in
``<out>/<workload>.csv``; ``<out>/<workload>.done`` lists finished
replicates, so a stopped campaign resumes.

Usage::

    python campaign.py --work DIR --out DIR --workloads python native
        --blocks 10

Cells and sizes are in `CELLS`. Cells are interleaved in blocks of
``--blocks`` replicates so a slow period of the host falls on all.
"""

import argparse
import csv
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np

HERE: Final = Path(__file__).resolve().parent
CELLS: Final = {
    "1.00": (0.0, 200),
    "1.02": (0.02, 100),
    "1.03": (0.03, 200),
    "1.05": (0.05, 100),
    "1.10": (0.10, 100),
}
"""Cell label to (fraction ``f``, comparisons)."""
ROUNDS: Final = {"python": 120, "native": 120, "node": 60, "java": 60}
"""Maximum rounds per workload."""
COMMANDS: Final[dict[str, list[str]]] = {
    "python": ["python3", "bench.py"],
    "native": ["./bench"],
    "node": ["node", "bench.js"],
    "java": ["java", "-cp", ".", "Bench"],
}
SETUP: Final[dict[str, list[list[str]]]] = {
    "native": [["gcc", "-O2", "-o", "bench", "bench.c"]],
    "java": [["javac", "Bench.java"]],
}
COLUMNS: Final = (
    "workload", "cell", "rep", "round", "pos", "arm", "reported_s",
    "perf_wall_s", "perf_cpu_s", "work_s", "spin_s", "user_s", "sys_s",
    "instructions", "cycles", "task_clock_ms", "cpu_migrations", "load1",
    "ticks", "steal_ticks",
)  # fmt: skip
GIT_ENV: Final = {
    "GIT_AUTHOR_NAME": "golden",
    "GIT_AUTHOR_EMAIL": "golden@example.invalid",
    "GIT_COMMITTER_NAME": "golden",
    "GIT_COMMITTER_EMAIL": "golden@example.invalid",
}


@dataclass(frozen=True, slots=True)
class Fixture:
    """A workload's fixture repository and candidate tree."""

    repo: Path
    candidate: Path
    spec: Path


def run(argv: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    """Run ``argv``; return stdout, or raise with the stderr tail."""
    done = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
    )
    if done.returncode != 0:
        tail = done.stdout[-800:] + done.stderr[-800:]
        msg = f"{argv} failed ({done.returncode}): {tail}"
        raise RuntimeError(msg)
    return done.stdout


def make_spec(workload: str, cfg: Path) -> dict[str, Any]:
    """Return the benchmark spec of a workload."""
    rounds = ROUNDS[workload]
    n0 = 20 if rounds == 120 else 10
    k = 20 if rounds == 120 else 10
    fields: dict[str, Any] = {
        "name": f"golden-{workload}",
        "command": [
            "python3",
            ".perfproof/harness/golden_wrap.py",
            "--",
            *COMMANDS[workload],
        ],
        "env": {"GOLDEN_CFG": str(cfg)},
        "metric": {
            "kind": "reported",
            "direction": "lower_is_better",
            "unit": "s",
        },
        "correctness": {},
        "warmup": 2,
        "budget": {"max_rounds": rounds, "max_seconds": 7200.0},
        "looks": {"n0": n0, "k": k, "max_looks": 6},
        "timeout_seconds": 60.0,
        "harness": [".perfproof/harness/golden_wrap.py"],
        "allow_unchecked_coverage": True,
    }
    if workload in SETUP:
        fields["setup"] = {
            "commands": SETUP[workload],
            "timeout_seconds": 120.0,
        }
    return fields


def build_fixture(workload: str, work: Path) -> Fixture:
    """Create the fixture repo, candidate tree and spec for a workload.

    Idempotent: an existing fixture is reused.
    """
    repo = work / "fixtures" / workload
    candidate = work / "candidates" / workload
    spec = work / "specs" / f"{workload}.json"
    if not (repo / ".git").exists():
        repo.mkdir(parents=True)
        for src in (HERE / "targets" / workload).iterdir():
            shutil.copy(src, repo / src.name)
        (repo / "golden.arm").write_text("A\n")
        env = {**os.environ, **GIT_ENV}
        run(["git", "init", "-q", "-b", "main"], repo, env)
        run(["git", "add", "-A"], repo, env)
        run(["git", "commit", "-q", "-m", "fixture"], repo, env)
        harness = repo / ".perfproof" / "harness"
        harness.mkdir(parents=True)
        shutil.copy(HERE / "wrap.py", harness / "golden_wrap.py")
    if not candidate.exists():
        candidate.mkdir(parents=True)
        for src in (HERE / "targets" / workload).iterdir():
            shutil.copy(src, candidate / src.name)
        (candidate / "golden.arm").write_text("B\n")
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(json.dumps(make_spec(workload, work / "cfg.json")))
    out = run(
        ["perfproof", "bench", "define", f"golden-{workload}", "--spec",
         str(spec), "--repo", str(repo), "--json"],
        repo,
    )  # fmt: skip
    if not json.loads(out)["ok"]:
        msg = f"bench define failed: {out}"
        raise RuntimeError(msg)
    return Fixture(repo, candidate, spec)


def join(
    samples: Any, log: Sequence[dict[str, Any]]
) -> list[tuple[int, dict[str, Any]]]:
    """Pair each perfproof sample with its wrapper log line, in order.

    Warm-up and gate executions have log lines but no sample, so the
    pointer skips log lines until the arm and the reported value match.

    Raises
    ------
    RuntimeError
        If a sample has no matching log line.
    """
    pairs: list[tuple[int, dict[str, Any]]] = []
    at = 0
    for i in range(len(samples["value"])):
        arm = "B" if int(samples["candidate"][i]) else "A"
        value = float(samples["value"][i])
        while at < len(log) and not (
            log[at]["arm"] == arm and abs(log[at]["reported_s"] - value) < 2e-9
        ):
            at += 1
        if at == len(log):
            msg = f"sample {i} ({arm}, {value}) not in the wrapper log"
            raise RuntimeError(msg)
        pairs.append((i, log[at]))
        at += 1
    return pairs


def replicate(
    workload: str,
    cell: str,
    rep: int,
    fix: Fixture,
    work: Path,
    pin: str | None,
) -> list[list[object]]:
    """Run one comparison and return its execution rows."""
    fraction = CELLS[cell][0]
    log_path = work / "logs" / f"{workload}-{cell}-{rep}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.unlink(missing_ok=True)
    (work / "cfg.json").write_text(
        json.dumps({"spin_A": fraction, "log": str(log_path), "pin": pin})
    )
    out = run(
        ["perfproof", "compare", f"golden-{workload}", "--candidate",
         str(fix.candidate), "--repo", str(fix.repo), "--json"],
        fix.repo,
    )  # fmt: skip
    doc = json.loads(out)
    run_dir = Path(doc["result"]["run_dir"])
    samples = np.load(run_dir / "samples.npz", allow_pickle=False)
    log = [json.loads(line) for line in log_path.read_text().splitlines()]
    rows: list[list[object]] = []
    for i, entry in join(samples, log):
        rows.append(
            [
                workload, cell, rep, int(samples["round"][i]),
                int(samples["position"][i]), entry["arm"],
                f"{entry['reported_s']:.9f}",
                f"{float(samples['wall_seconds'][i]):.9f}",
                f"{float(samples['cpu_seconds'][i]):.6f}",
                f"{entry['work_s']:.9f}", f"{entry['spin_s']:.9f}",
                entry["user_s"], entry["sys_s"], entry["instructions"],
                entry["cycles"], entry["task_clock_ms"],
                entry["cpu_migrations"], entry["load1"], entry["ticks"],
                entry["steal_ticks"],
            ]
        )  # fmt: skip
    expected = ROUNDS[workload] * 2
    if len(rows) != expected:
        msg = f"{workload} {cell} {rep}: {len(rows)} rows, not {expected}"
        raise RuntimeError(msg)
    shutil.rmtree(run_dir)
    log_path.unlink()
    return rows


def host_info() -> dict[str, str]:
    """Return what identifies this host, for the record."""
    info = {
        "node": platform.node(),
        "system": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
    }
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text().splitlines():
            if line.startswith("model name"):
                info["cpu"] = line.split(":", 1)[1].strip()
                break
    return info


def main(argv: Sequence[str]) -> int:
    """Run the campaign; return the exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workloads", nargs="+", default=list(ROUNDS))
    parser.add_argument("--cells", nargs="+", default=list(CELLS))
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--blocks", type=int, default=10)
    parser.add_argument("--pin", default=None)
    args = parser.parse_args(argv)
    args.work = args.work.resolve()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "host.json").write_text(json.dumps(host_info(), indent=1))
    fixtures = {w: build_fixture(w, args.work) for w in args.workloads}
    done: dict[str, set[str]] = {
        w: set((args.out / f"{w}.done").read_text().split())
        if (args.out / f"{w}.done").exists()
        else set()
        for w in args.workloads
    }
    sizes = {c: max(1, round(CELLS[c][1] * args.scale)) for c in args.cells}
    top = max(sizes.values())
    started = time.monotonic()
    for start in range(0, top, args.blocks):
        for workload in args.workloads:
            for cell in args.cells:
                for rep in range(start, min(start + args.blocks, sizes[cell])):
                    key = f"{cell}/{rep}"
                    if key in done[workload]:
                        continue
                    rows = replicate(
                        workload, cell, rep, fixtures[workload], args.work,
                        args.pin,
                    )  # fmt: skip
                    path = args.out / f"{workload}.csv"
                    fresh = not path.exists()
                    with path.open("a", newline="") as sink:
                        writer = csv.writer(sink, lineterminator="\n")
                        if fresh:
                            writer.writerow(COLUMNS)
                        writer.writerows(rows)
                    with (args.out / f"{workload}.done").open("a") as sink:
                        sink.write(key + "\n")
                    done[workload].add(key)
                    elapsed = time.monotonic() - started
                    sys.stdout.write(
                        f"{elapsed:8.0f}s {workload} {cell} rep {rep}\n"
                    )
                    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
