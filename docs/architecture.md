# Architecture

The design target for the release. `.claude/plan.md` tracks what
exists. Each component's reason to exist is in
[failure-modes.md](failure-modes.md); the interface is in
[cli.md](cli.md); prove is specified in [method.md](method.md), and
understand and discover in [discovery.md](discovery.md).

## Shape

A small exact core and the skills that drive it:

```
 developer ──▶ agent (Claude Code, Codex, anything with a shell)
                 │ reads
                 ▼
          skills/: /perfproof loop + one playbook per target class
                 │ runs
                 ▼
          perfproof CLI ── understand: project model (persisted, invalidated)
                        ── discover:   profile summaries, scaling runs,
                                       stub-out bounds, hypothesis log
                        ── prove:      gates, interleaved rounds, verdict,
                                       guardrails, confirm, record, report
                 │
          local runner ── remote runner (SSH host, Modal)
```

- **The CLI** owns everything that must be exact: state, measurement,
  statistics, gates, undo and records. Agents use it through the shell
  with `--json`, and long comparisons with `--detach` and `status`.
- **The skills** own judgment: understanding the project with the
  developer, choosing what to look at, forming hypotheses, choosing a
  cheap test, writing candidates. They are Markdown. If the agent
  skips a step, the CLI's gates still stand between its output and a
  kept change.

## Packages

| Package | Job |
|---|---|
| `perfproof.schema` | Pydantic models for every record: spec, sample, gate, verdict, guardrail, project model, hypothesis |
| `perfproof.stats` | Intervals, sequential looks, decision rule, guardrails, noise simulation. Pure functions on NumPy arrays |
| `perfproof.bench` | Spec and lock, snapshots, runner, correctness, differential checks, coverage, side-effect guard, compare, confirm |
| `perfproof.machine` | Fingerprint and warnings |
| `perfproof.project` | The project model: proposal, confirmation, staleness, history |
| `perfproof.hypotheses` | The append-only hypothesis log |
| `perfproof.profilers` | Run a profiler, summarise folded stacks to the top frames |
| `perfproof.helpers` | `load` (constant-rate HTTP driver) and `replay` (model and tool calls) |
| `perfproof.remote` | SSH and Modal runners; the remote side runs the same engine |
| `perfproof.session` | The `/perfproof` session: branch, undo, dirty-tree refusal, resume |
| `perfproof.report` | Renders a record. Computes nothing new |
| `perfproof.cli` | One module per command under `perfproof/commands/` |

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
  runs/<run-id>/
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
