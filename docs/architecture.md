# Architecture

A small exact core and the Markdown skills that drive it. This page is
the map. Why each part exists is in [failure-modes.md](failure-modes.md),
the interface is [cli.md](cli.md), prove is [method.md](method.md), and
understand and discover are [discovery.md](discovery.md).

## Shape

```mermaid
flowchart TD
    dev[developer] --> agent["agent: Claude Code, Codex, any shell"]
    agent -->|reads| skill["skills/: the /perfproof loop and one playbook per language"]
    skill -->|runs| cli[perfproof CLI]
    cli --> und["understand: project model"]
    cli --> disc["discover: find, profile summaries, hypothesis log"]
    cli --> prove["prove: gates, interleaved rounds, verdict, record"]
```

*The agent never talks to the statistics directly. It goes through the CLI.*

- **The CLI** owns everything that must be exact: state, measurement,
  statistics, gates, undo and records. Agents call it with `--json`,
  and long comparisons with `--detach` and `status`.
- **The skills** own judgment: choosing what to look at, forming
  hypotheses, writing candidates. If the agent skips a step, the
  CLI's gates still stand between its output and a kept change.

Everything runs on the local machine. Remote runners (SSH, Modal) are
not built.

## Packages

These are the packages in `src/perfproof`, checked against the tree.

| Package | Job |
|---|---|
| `perfproof.schema` | Pydantic models for every record: spec, sample, gate, verdict, guardrail, project model, hypothesis |
| `perfproof.stats` | Intervals, sequential looks, decision rule, guardrails, noise simulation. Pure functions on NumPy arrays |
| `perfproof.bench` | Spec and lock, snapshots, runner, correctness, differential checks, coverage, side-effect guard, compare |
| `perfproof.machine` | Fingerprint and warnings |
| `perfproof.project` | The project model: proposal, confirmation, staleness, history |
| `perfproof.hypotheses` | The append-only hypothesis log |
| `perfproof.profilers` | Run a profiler, summarise folded stacks to the top frames |
| `perfproof.find` | `perfproof find`: map the app, probe, rank, plan (see [find.md](find.md)) |
| `perfproof.report` | Renders a record. Computes nothing new |
| `perfproof.commands` | One module per CLI command, plus shared helpers |
| `perfproof.cli` | Argument parsing and dispatch |

## Module boundaries

The intended direction is one way, from the CLI down to the maths.

```mermaid
flowchart TD
    cli --> commands
    commands --> find
    commands --> bench
    commands --> project
    commands --> profilers
    commands --> report
    find --> profilers
    find --> project
    bench --> stats
    bench --> machine
    bench --> schema
    project --> schema
    stats --> schema
```

*Arrows are allowed imports. `stats` takes arrays and never imports `bench`.*

Three edges go the wrong way today and are being fixed separately:

- **`profilers` imports `commands`** for `JsonValue`
  (`profilers/growth.py:73`, `profilers/summary.py:30`,
  `profilers/rank.py:45`).
- **`find` imports private `commands` helpers**: `_profile_rank` and
  `_profile_show` (`find/mapping.py:21-22`, `find/probes.py:25`) and
  `_project` (`find/engine.py:17`).
- **`bench` and `project` import each other**: `bench/compare.py:243-246`
  uses `project`, and `project/store.py:29` uses `bench.spec`.

## On disk

perfproof keeps its state in `.perfproof/` inside the user's
repository, which it adds to `.git/info/exclude`:

```
.perfproof/
  project.json                 targets, workloads, goals, constraints
  project.history.jsonl        every earlier version of every entry
  benchmarks/<name>/
    <spec-hash>.json           one spec version and its lock manifest
  hypotheses/<workload>.jsonl  every hypothesis event, append-only
  worktrees/<name>/            baseline and candidate checkouts
  harness/                     benchmark files perfproof owns; copied into
                               both trees before every run, all locked
  runs/<run-id>/
    logs/setup/                output of each tree's setup commands
    progress.json              where a detached compare has got to
    pid                        the detached process and its start time
    samples.jsonl              one line per completed round, for resume
    record.json                the full machine-readable result
    report.md                  the readable report
    samples.npz                raw per-round samples
    profiles/                  profiler outputs, untouched
```

A benchmark's name is how people and agents refer to it. Its spec hash
identifies one exact version: changing any spec field gives a new hash
and a new file beside the old one, never an overwrite.

## What perfproof reuses and what it adds

Checked against each tool's own documentation.

| Tool | What it already does well | What it doesn't do |
|---|---|---|
| [hyperfine](https://github.com/sharkdp/hyperfine) | Warmup, a per-run prepare hook, shell-startup correction, outlier warnings, JSON export | Runs each command's batch separately, not interleaved; no significance test or verdict |
| [pyperf](https://pyperf.readthedocs.io/en/latest/analyze.html) | Worker processes, loop calibration, `system tune` to cut jitter, a t-test in `compare_to` | Unpaired comparison; no minimum effect, no INCONCLUSIVE, no correctness gate |
| [Criterion](https://bheisler.github.io/criterion.rs/book/analysis.html) | Bootstrap intervals, Tukey outlier classification, comparison with a saved baseline, a noise threshold | Compares against a stored earlier run rather than interleaving; Rust only |
| Google Benchmark | Automatic iteration counts, repetitions, a comparison script | Same as above, C++ only |
| [Bencher](https://bencher.dev/docs/explanation/thresholds) | CI thresholds with a t-test or z-score against history, alerts | Compares against history, not a paired run; no correctness gate |
| CodSpeed | Low-noise CI measurement through CPU simulation | A hosted CI service; no correctness gate or agent loop |

perfproof reuses their lessons: warmup, calibrated inner loops for
in-process timing (the `reported` metric), outlier reporting, system
tuning advice in `doctor`, and instruction counts on Linux. What it adds,
and what none of them does:

- old and new interleaved in randomly ordered pairs on one machine;
- a verdict with a minimum effect and an honest INCONCLUSIVE, with
  calibrated error rates under sequential looks;
- gates that make "faster but wrong" and "faster by cheating" a REJECT;
- choose-then-confirm when many candidates are tried;
- the agent loop that finds what to try.
