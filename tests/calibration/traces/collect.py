"""Record one raw A/A trace: the same program against itself.

``collect.py`` runs a command in two directory copies, ``A`` (the
baseline tree) and ``B`` (the candidate tree), as perfproof's two
worktrees are. Rounds are pairs: both copies run once, each as a fresh
process, in an order drawn per pair from a recorded seed by the same
rule as ``perfproof.bench.runner.round_orders``. Nothing here changes
the procedure; it only records.

The output is ``<out>.jsonl`` (one object per execution) and
``<out>.header.json`` (tool versions, machine, command, seed, and which
fields are null and why). Run it on the host or inside a Linux
container; the container has no ``/usr/bin/time -l``, ``pmset`` or
host thermal state, so those fields are null there and the header says
so.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import re
import resource
import shlex
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

TIME_BIN = "/usr/bin/time"
IDLE_LOAD = 3.0
IDLE_CHECK_SECONDS = 20.0
BUSY_PATTERNS = (
    "verify/linux.sh ",
    "skill_eval",
    "claude -p",
    "run_calibration",
    "multiprocessing",
    "mvn",
    "gradle",
    "docker run",
)
ABORT_EXIT = 75
GUARD_LOAD_SECONDS = 60.0
GUARD_BUSY_EVERY = 10
REPORTED = re.compile(r"PERFPROOF (\{.*\})")
INSTR = re.compile(r"^\s*(\d+)\s+instructions retired", re.MULTILINE)
CYCLES = re.compile(r"^\s*(\d+)\s+cycles elapsed", re.MULTILINE)
MAXRSS = re.compile(r"^\s*(\d+)\s+maximum resident set size", re.MULTILINE)
SPEED = re.compile(r"CPU_Speed_Limit\s*=\s*(\d+)")
TIMEOUT = 120.0


def round_orders(seed: int, rounds: int) -> list[str]:
    """Return which arm runs first in each pair.

    The rule of ``perfproof.bench.runner.round_orders``: pair ``j`` of
    rounds ``2j`` and ``2j + 1`` runs in opposite orders and the top bit
    of the ``j``-th raw PCG64 output picks round ``2j``'s first arm
    (``A`` for 0).

    Parameters
    ----------
    seed : int
        Order seed, non-negative.
    rounds : int
        Number of rounds.

    Returns
    -------
    list of str
        ``"A"`` or ``"B"`` per round.
    """
    raw = np.random.PCG64(seed).random_raw((rounds + 1) // 2)
    bits = (raw >> np.uint64(63)).tolist()
    return ["AB"[int(bits[i // 2]) ^ (i % 2)] for i in range(rounds)]


def busy_processes() -> list[str]:
    """Return the patterns of the other agents' jobs now running."""
    found: list[str] = []
    for pattern in BUSY_PATTERNS:
        done = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        pids = [p for p in done.stdout.split() if int(p) != os.getpid()]
        if pids:
            found.append(pattern)
    java = subprocess.run(
        ["pgrep", "-x", "java"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if java.stdout.split():
        found.append("java")
    return found


def power_source() -> str | None:
    """Return the macOS power source, or None when it cannot be read.

    Parses the ``Now drawing from '<source>'`` line of ``pmset -g batt``
    (``AC Power`` or ``Battery Power``). A missing ``pmset``, a timeout
    or output without that line gives None, never a guess.
    """
    try:
        out = subprocess.run(
            ["pmset", "-g", "batt"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"drawing from '([^']+)'", out)
    return match.group(1) if match else None


def on_ac_power() -> bool:
    """Return whether this Mac is on AC power; fail closed otherwise.

    Battery power changes CPU frequency behaviour, so a trace taken on
    it would be mislabelled. Anything but a readable ``AC Power`` is
    treated as not quiet.
    """
    return power_source() == "AC Power"


def _source_if_mac() -> str | None:
    """Return the power source on macOS, None elsewhere."""
    return power_source() if platform.system() == "Darwin" else None


def wait_idle(max_hours: float, sustain_s: float = 0.0) -> dict[str, Any]:
    """Wait until the machine is quiet enough to start.

    Quiet means a 1-minute load average below `IDLE_LOAD` and none of
    `BUSY_PATTERNS` (or a ``java`` process) running, held for
    ``sustain_s`` seconds without a break. On macOS it must also be on
    AC power (see `on_ac_power`). No process is touched.

    Parameters
    ----------
    max_hours : float
        Give up after this long and start anyway, recording it.
    sustain_s : float
        How long it must stay quiet without a break.

    Returns
    -------
    dict
        ``waited_seconds``, ``load1_at_start``, ``gave_up``,
        ``power_source`` (None off macOS or when unreadable).
    """
    start = time.monotonic()
    quiet_since: float | None = None
    while True:
        load = os.getloadavg()[0]
        busy = busy_processes()
        waited = time.monotonic() - start
        mains = platform.system() != "Darwin" or on_ac_power()
        if load < IDLE_LOAD and not busy and mains:
            if quiet_since is None:
                quiet_since = time.monotonic()
        else:
            quiet_since = None
        if quiet_since is not None and (
            time.monotonic() - quiet_since >= sustain_s
        ):
            return {
                "waited_seconds": round(waited, 1),
                "load1_at_start": load,
                "gave_up": False,
                "power_source": _source_if_mac(),
            }
        if waited > max_hours * 3600:
            return {
                "waited_seconds": round(waited, 1),
                "load1_at_start": load,
                "gave_up": True,
                "power_source": _source_if_mac(),
            }
        time.sleep(IDLE_CHECK_SECONDS)


def host_state() -> tuple[int | None, str | None]:
    """Return the CPU speed limit and the power source, or None.

    Reads ``pmset -g therm`` and ``pmset -g batt``; ``(None, None)``
    where ``pmset`` does not exist. ``pmset`` prints no
    ``CPU_Speed_Limit`` line when nothing has throttled, which is
    reported as 100 (the absence of a recorded limit).
    """
    if platform.system() != "Darwin":
        return None, None
    therm = subprocess.run(
        ["pmset", "-g", "therm"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    ).stdout
    batt = subprocess.run(
        ["pmset", "-g", "batt"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    ).stdout
    match = SPEED.search(therm)
    speed = int(match.group(1)) if match else 100
    source = re.search(r"drawing from '([^']+)'", batt)
    return speed, source.group(1) if source else None


def proc_stat_ticks() -> tuple[int, int]:
    """Return (all-CPU ticks, steal ticks) from /proc/stat.

    Ticks are USER_HZ (normally 1/100 s) summed over all vCPUs; steal is
    the time the hypervisor ran something else while a vCPU wanted to
    run.
    """
    with open("/proc/stat") as f:
        parts = f.readline().split()[1:]
    values = [int(v) for v in parts]
    return sum(values[:8]), values[7]


def sample_cpu_of(pid: int) -> int | None:
    """Return the CPU the benchmark ran on, sampled once, or None.

    ``pid`` is the wrapper (``perf``, or ``taskset`` that exec'd it);
    the benchmark is its child. Reads field 39 (``processor``) of
    ``/proc/<child>/stat``: the CPU the process last ran on at the
    instant of the read, one sample per run.
    """
    try:
        children = Path(f"/proc/{pid}/task/{pid}/children").read_text()
        child = int(children.split()[0])
        stat = Path(f"/proc/{child}/stat").read_text()
        return int(stat.rsplit(")", 1)[1].split()[36])
    except (OSError, ValueError, IndexError):
        return None


def parse_perf(path: Path) -> dict[str, int | None]:
    """Parse ``perf stat -x,`` output; None for an uncounted event."""
    found: dict[str, int | None] = {}
    for line in path.read_text().splitlines():
        cols = line.split(",")
        if len(cols) < 3 or line.startswith("#"):
            continue
        event = cols[2].split(":")[0]
        found[event] = int(cols[0]) if cols[0].isdigit() else None
    return found


def watched_cpu_seconds(name: str) -> float | None:
    """Return user+sys CPU seconds so far of processes called ``name``.

    For a server that does the work a client process only requests, such
    as Ollama: the delta across a run is the server's CPU cost of it.
    Linux reads ``/proc/<pid>/stat`` (clock ticks); macOS reads ``ps``
    (10 ms resolution). None when no such process exists.
    """
    found = subprocess.run(
        ["pgrep", "-x", name],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    pids = found.stdout.split()
    if not pids:
        return None
    total = 0.0
    if Path("/proc/self/stat").exists():
        tick = float(os.sysconf("SC_CLK_TCK"))
        for pid in pids:
            try:
                tail = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1]
            except OSError:
                continue
            fields = tail.split()
            total += (int(fields[11]) + int(fields[12])) / tick
        return total
    ps = subprocess.run(
        ["ps", "-o", "cputime=", "-p", ",".join(pids)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    for text in ps.stdout.split():
        minutes, _, seconds = text.partition(":")
        total += 60 * int(minutes) + float(seconds)
    return total


def execute(
    command: list[str],
    cwd: Path,
    use_time: bool,
    linux: bool = False,
    taskset: str | None = None,
    watch: str | None = None,
) -> dict[str, Any]:
    """Run one fresh process and measure it.

    Wall time spans the spawn to the end of the wait, as in perfproof's
    runner. CPU seconds come from the RUSAGE_CHILDREN delta and, on
    macOS, instructions and cycles from ``/usr/bin/time -l``, which
    wraps the command (a constant cost in both arms).

    Parameters
    ----------
    command : list of str
        The program and its arguments.
    cwd : pathlib.Path
        Working directory, the arm's tree.
    use_time : bool
        Wrap in ``/usr/bin/time -l``.

    Returns
    -------
    dict
        Measurement fields of one execution.

    Raises
    ------
    RuntimeError
        If the command exits non-zero, or runs over ``TIMEOUT``
        seconds (its process group is killed first).
    """
    argv = [TIME_BIN, "-l", *command] if use_time else command
    ticks_before = (0, 0)
    load_start = 0.0
    instr_n: int | None = None
    cycles_n: int | None = None
    perf_file = Path(
        f"/dev/shm/perfproof-trace-{os.getpid()}.perf"  # noqa: S108
    )
    if linux:
        argv = [
            "perf", "stat", "-x,", "-o", str(perf_file),
            "-e", "instructions:u,cycles:u,cpu-migrations",
            "--", *command,
        ]  # fmt: skip
        if taskset is not None:
            argv = ["taskset", "-c", taskset, *argv]
        ticks_before = proc_stat_ticks()
        load_start = float(Path("/proc/loadavg").read_text().split()[0])
    watch_before = watched_cpu_seconds(watch) if watch else None
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start_ns = time.perf_counter_ns()
    wall_start = datetime.now(UTC).isoformat(timespec="microseconds")
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    cpu_seen: int | None = None
    if linux:
        time.sleep(0.1)
        cpu_seen = sample_cpu_of(proc.pid)
    try:
        out_b, err_b = proc.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        msg = f"{argv} in {cwd} ran over {TIMEOUT} s"
        raise RuntimeError(msg) from None
    end_ns = time.perf_counter_ns()
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    watch_after = watched_cpu_seconds(watch) if watch else None
    out = out_b.decode(errors="replace")
    err = err_b.decode(errors="replace")
    if proc.returncode != 0:
        msg = f"{argv} in {cwd} exited {proc.returncode}: {err[-300:]}"
        raise RuntimeError(msg)
    instr = INSTR.search(err)
    cycles = CYCLES.search(err)
    rss = MAXRSS.search(err)
    reported = REPORTED.search(out)
    parsed: dict[str, float] = (
        json.loads(reported.group(1)) if reported else {}
    )
    value = float(parsed["value"]) if "value" in parsed else None
    extra: dict[str, Any] = {}
    if linux:
        ticks_after = proc_stat_ticks()
        counts = parse_perf(perf_file)
        perf_file.unlink(missing_ok=True)
        extra = {
            "cpu_migrations": counts.get("cpu-migrations"),
            "cpu_sampled_at_100ms": cpu_seen,
            "all_cpu_ticks": ticks_after[0] - ticks_before[0],
            "steal_ticks": ticks_after[1] - ticks_before[1],
            "loadavg_start": load_start,
            "loadavg_end": float(Path("/proc/loadavg").read_text().split()[0]),
        }
        instr_n = counts.get("instructions")
        cycles_n = counts.get("cycles")
    return {
        "t_utc": wall_start,
        "mono_ns": start_ns,
        "wall_s": (end_ns - start_ns) / 1e9,
        "user_s": usage_after.ru_utime - usage_before.ru_utime,
        "sys_s": usage_after.ru_stime - usage_before.ru_stime,
        "instructions": instr_n
        if linux
        else (int(instr.group(1)) if instr else None),
        "cycles": cycles_n
        if linux
        else (int(cycles.group(1)) if cycles else None),
        "max_rss_bytes": int(rss.group(1)) if rss else None,
        "reported_s": value,
        "metrics": parsed if len(parsed) > 1 else None,
        "watched_cpu_s": None
        if watch_before is None or watch_after is None
        else watch_after - watch_before,
    } | extra


def tool_versions(specs: list[str]) -> dict[str, str | None]:
    """Return the first output line of each ``NAME=COMMAND`` spec."""
    versions: dict[str, str | None] = {}
    for spec in specs:
        name, _, command = spec.partition("=")
        done = subprocess.run(
            shlex.split(command),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        text = (done.stdout or done.stderr).strip().splitlines()
        versions[name] = text[0] if text else None
    return versions


def machine_info() -> dict[str, Any]:
    """Describe the machine this process runs on."""
    info: dict[str, Any] = {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
    }
    if platform.system() == "Darwin":
        for key in ("machdep.cpu.brand_string", "hw.model", "hw.memsize"):
            done = subprocess.run(
                ["sysctl", "-n", key],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            info[key] = done.stdout.strip()
        info["macos"] = platform.mac_ver()[0]
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text().splitlines():
            if line.startswith("model name"):
                info["cpu_model"] = line.split(":", 1)[1].strip()
                break
    return info


def completed_pairs(path: Path) -> int:
    """Return how many whole pairs (warm-up excluded) a file holds."""
    if not path.exists():
        return 0
    seen: dict[int, int] = {}
    with path.open() as f:
        for line in f:
            row = json.loads(line)
            if row["warmup"]:
                continue
            seen[row["pair"]] = seen.get(row["pair"], 0) + 1
    return sum(1 for count in seen.values() if count == 2)


def main(argv: list[str] | None = None) -> int:
    """Record the trace described by the command line."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--dir-a", type=Path, required=True)
    p.add_argument("--dir-b", type=Path, required=True)
    p.add_argument("--cmd", required=True, help="shlex-split argv")
    p.add_argument("--rounds", type=int, default=1000)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--note", default="")
    p.add_argument("--tool", action="append", default=[])
    p.add_argument("--wait-idle-hours", type=float, default=12.0)
    p.add_argument("--no-time", action="store_true")
    p.add_argument("--no-pmset", action="store_true")
    p.add_argument("--no-wait", action="store_true")
    p.add_argument(
        "--linux",
        action="store_true",
        help="wrap in perf stat; record steal ticks and sampled CPU",
    )
    p.add_argument("--taskset", help="pin every run to these CPUs")
    p.add_argument(
        "--guard",
        choices=("quiet", "busy"),
        help=(
            "abort with exit 75 when other workers appear: 'quiet' also"
            " when the load stays above 3 for over a minute, 'busy' only"
            " on the busy-process list"
        ),
    )
    p.add_argument(
        "--watch-process",
        help="also record the CPU seconds this named server process used",
    )
    p.add_argument(
        "--per-run-state",
        action="store_true",
        help="macOS: sample pmset therm and power after every run",
    )
    p.add_argument("--null-reason", action="append", default=[])
    args = p.parse_args(argv)

    command = shlex.split(args.cmd)
    use_time = (
        (not args.no_time)
        and (not args.linux)
        and platform.system() == "Darwin"
        and Path(TIME_BIN).exists()
    )
    use_pmset = (not args.no_pmset) and platform.system() == "Darwin"
    dirs = {"A": args.dir_a, "B": args.dir_b}
    orders = round_orders(args.seed, args.rounds)
    jsonl = args.out.with_suffix(".jsonl")
    header_path = args.out.with_suffix(".header.json")
    done_pairs = completed_pairs(jsonl)
    if done_pairs == 0 and jsonl.exists():
        jsonl.unlink()

    idle = (
        {"waited_seconds": 0.0, "load1_at_start": os.getloadavg()[0]}
        if args.no_wait
        else wait_idle(args.wait_idle_hours)
    )
    started = datetime.now(UTC).isoformat(timespec="seconds")
    nulls: dict[str, str] = {}
    if not use_time and not args.linux:
        reason = "/usr/bin/time -l is not available or was disabled"
        nulls["instructions"] = reason
        nulls["cycles"] = reason
        nulls["max_rss_bytes"] = reason
    if not use_pmset:
        reason = "pmset exists only on macOS hosts"
        nulls["cpu_speed_limit"] = reason
        nulls["power_source"] = reason
    if args.linux:
        nulls["max_rss_bytes"] = "perf stat does not report it"
        nulls["cpu_speed_limit"] = "no cpufreq in a KVM guest"
        nulls["power_source"] = "a server; not applicable"
    for item in args.null_reason:
        key, _, why = item.partition("=")
        nulls[key] = why
    header: dict[str, Any] = {
        "label": args.label,
        "target": args.target,
        "note": args.note,
        "command": command,
        "dirs": {k: str(v) for k, v in dirs.items()},
        "rounds_requested": args.rounds,
        "order_seed": args.seed,
        "order_rule": "perfproof.bench.runner.round_orders; A=baseline",
        "warmup": "one pair (pair -1, warmup=true), excluded from stats",
        "tools": tool_versions(args.tool),
        "machine": machine_info(),
        "idle_wait": idle,
        "power_source_at_start": power_source() if use_pmset else None,
        "started_utc": started,
        "resumed_from_pair": done_pairs,
        "fields": {
            "wall_s": "perf_counter around spawn..exit, includes the"
            " /usr/bin/time wrapper where used (same in both arms)",
            "user_s_sys_s": "RUSAGE_CHILDREN delta across the run",
            "instructions_cycles": "/usr/bin/time -l; includes the"
            " wrapper's own startup, a constant in both arms",
            "load1": "os.getloadavg()[0] at the start of the pair",
            "cpu_speed_limit": "pmset -g therm CPU_Speed_Limit after the"
            " pair; 100 when no limit is recorded",
            "power_source": "pmset -g batt after the pair",
            "power_source_at_start": "pmset -g batt when recording"
            " began; macOS passes record only on AC Power and abort"
            " when the Mac goes to battery",
            "linux_perf": "perf stat -e instructions:u,cycles:u,"
            "cpu-migrations wraps the command (constant in both arms);"
            " instructions and cycles are user-space only",
            "cpu_sampled_at_100ms": "field 39 of /proc/<child>/stat read"
            " once 100 ms after spawn; one sample, not the whole run",
            "steal_ticks": "/proc/stat steal, all vCPUs, USER_HZ ticks"
            " elapsed across the execution; all_cpu_ticks likewise",
            "taskset": args.taskset,
            "per_run_state": "with --per-run-state, cpu_speed_limit and"
            " power_source are sampled after each run, not each pair",
            "loadavg_start_end": "Linux: /proc/loadavg 1-minute value at"
            " the start and end of each run",
            "metrics": "the full PERFPROOF JSON the command printed, when"
            " it has more than one key",
        },
        "null_fields": nulls,
    }
    header_path.write_text(json.dumps(header, indent=2) + "\n")

    def record(
        f: Any, pair: int, warmup: bool, order: str, load1: float
    ) -> None:
        rows: list[dict[str, Any]] = []
        for pos, arm in enumerate([order, "B" if order == "A" else "A"]):
            m = execute(
                command,
                dirs[arm],
                use_time,
                args.linux,
                args.taskset,
                args.watch_process,
            )
            row = {"pair": pair, "warmup": warmup, "pos": pos, "arm": arm}
            row |= m | {"load1": load1}
            if args.per_run_state and use_pmset:
                row["cpu_speed_limit"], row["power_source"] = host_state()
            rows.append(row)
        speed, source = host_state() if use_pmset else (None, None)
        for row in rows:
            if not (args.per_run_state and use_pmset):
                row["cpu_speed_limit"] = speed
                row["power_source"] = source
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
        f.flush()

    with jsonl.open("a") as f:
        if done_pairs == 0:
            record(f, -1, True, "A", os.getloadavg()[0])
        over_since: float | None = None
        for pair in range(done_pairs, args.rounds):
            if args.guard:
                now = time.monotonic()
                if args.guard == "quiet" and os.getloadavg()[0] > IDLE_LOAD:
                    over_since = over_since or now
                else:
                    over_since = None
                busy = busy_processes() if pair % GUARD_BUSY_EVERY == 0 else []
                off_mains = use_pmset and not on_ac_power()
                if (
                    busy
                    or off_mains
                    or (over_since and now - over_since > GUARD_LOAD_SECONDS)
                ):
                    if off_mains:
                        reason = "not on AC power"
                    elif busy:
                        reason = f"busy processes {busy}"
                    else:
                        reason = f"load above {IDLE_LOAD} for over a minute"
                    header["aborted"] = f"pair {pair}: {reason}"
                    header_path.write_text(json.dumps(header, indent=2) + "\n")
                    sys.stdout.write(
                        f"{args.label}: ABORT at pair {pair}: {reason}\n"
                    )
                    return ABORT_EXIT
            record(f, pair, False, orders[pair], os.getloadavg()[0])
            if (pair + 1) % 100 == 0:
                sys.stdout.write(f"{args.label}: {pair + 1} pairs\n")
                sys.stdout.flush()
    header["finished_utc"] = datetime.now(UTC).isoformat(timespec="seconds")
    header_path.write_text(json.dumps(header, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
