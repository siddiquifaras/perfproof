# The CLI and a session

The target interface for the release, written before the tasks so the
plan serves what the agent and the developer actually see. Every number
on this page is illustrative, not a measurement.

## `perfproof --help`

```
perfproof: find where code can get faster, and keep only the changes
that really are.

usage: perfproof <command> [options]

Understand
  init             Create .perfproof/ and propose a project model from
                   the repository: target classes, candidate workloads,
                   build and run commands, each with its evidence
  project          Show the project model; propose, confirm or retire
                   entries; check which entries are stale

Discover
  profile          Run a profiler on a workload and summarise the top
                   frames with file:line and share of time
  measure          Measure one version: scaling runs (--sizes), a stage
                   with one function replayed from captured calls
                   (--stub), or one setting changed (--set)
  log              Record a hypothesis, its cheap test, its candidate
                   and its outcome; show the log for a workload

Prove
  bench            Define and lock a workload's benchmark spec
  check            Run the correctness gates only: tests, output
                   comparison, captured-call replay, coverage
  compare          Prove a candidate against a baseline: gates,
                   interleaved rounds, guardrails, verdict, record
  confirm          Choose the best of several candidates, then confirm
                   it with a fresh, independent compare
  status           State and progress of a detached compare
  cancel           Stop a detached compare and its whole process tree
  report           Render a run record as readable text

Helpers
  load             Drive an HTTP service at a constant request rate and
                   record latency percentiles and throughput
  replay           Record an app's model and tool calls, or replay them
  doctor           Check this machine, its noise sources and the tools
                   perfproof can use here
  install-skills   Install the /perfproof skill and the playbooks for
                   Claude Code or Codex

Common options
  --json           Machine-readable output, for agents
  --detach         Return at once; follow with `perfproof status`
  --resume RUN     Continue an interrupted compare from its last
                   completed round
  --host HOST      Run on ssh://user@host or modal:<image> instead of here

Every verdict is KEEP, REJECT or INCONCLUSIVE, with a reason. A gate
that isn't available on this target blocks KEEP; it never passes.
```

## Long runs

`compare` runs in the foreground by default. When a comparison may
outlast the caller's command timeout, the agent adds `--detach`. There
is no daemon, queue or job service: a detached compare is one process
in its own session, and its run directory is the only state.

| Piece | What it is |
|---|---|
| `runs/<id>/progress.json` | Written after each completed round, outside the timed region: rounds done, current look, state |
| `runs/<id>/samples.jsonl` | One line per completed round, flushed after it is written |
| `runs/<id>/pid` | Process ID and its start time, so a reused PID isn't mistaken for the run |
| `status <id>` | Reads `progress.json` and checks the PID: `running`, `finished` (record written), `cancelled`, or `interrupted` (process gone, no record) |
| `cancel <id>` | SIGTERM to the run's process group, SIGKILL after a grace period; state becomes `cancelled`; no verdict |
| `compare --resume <id>` | Continues an `interrupted` run from its completed rounds, after re-checking every gate and the machine fingerprint. If the fingerprint changed, it refuses. The record marks where it resumed |

A run that never finishes never yields a verdict.

## A session

A developer has maintained `tilekit`, a Python image-tiling library,
for three years. The obvious hotspots are long fixed. They type
`/perfproof` in Claude Code.

```
> /perfproof

● perfproof init --json
  Proposed project model (nothing is confirmed yet):
    target   python            evidence: pyproject.toml, src/tilekit/
    workload bench-tile        benchmarks/bench_tile.py  (existing benchmark)
    workload test-slowest      tests/test_pyramid.py::test_large  (slowest test, 4.1 s)
    goal     ?                 unknown: what should get faster?
    oracle   pytest tests/     evidence: 214 tests found

  I found two candidate workloads. Which represents real use, and what
  matters most: wall time, peak memory, or something else? Anything
  that must not change, such as the public API?

> bench-tile is what users run. Wall time. Don't change the public API
  or add dependencies.

● perfproof project confirm workload=bench-tile goal=wall
    constraints="public API fixed; no new dependencies" --json
  Confirmed. Guardrails: peak memory, 10% (default for python).

● perfproof bench define bench-tile --json
  Spec 4f1c… locked: benchmarks/bench_tile.py, tests/ (214 files),
  data/sample.tif

● perfproof profile bench-tile --profiler py-spy --json
  Top frames (share of wall time):
    38%  tilekit/resample.py:88   _bilinear_row
    21%  tilekit/io.py:140        TileWriter.write   (waiting: CPU 4%)
    12%  tilekit/pyramid.py:61    _level_shape
  Wall 1.92 s, CPU 1.46 s: 24% of wall time is waiting, not computing.

● perfproof measure bench-tile --sizes 1x,2x,4x --json
  Time grows as n^1.0. Nothing super-linear.

  Three hypotheses:
  H1  _bilinear_row loops in Python per pixel; vectorizing removes the
      interpreter overhead. Cap 1.61x (38% of time).
  H2  TileWriter.write calls fsync per tile; most of its 21% is waiting.
      Cap 1.27x.
  H3  _level_shape is called 9,400 times with 12 distinct arguments
      (from captured calls); caching it removes repeated work. Cap 1.14x.

● perfproof log add bench-tile H1 … H2 … H3 … --json
● perfproof measure bench-tile --stub tilekit.io:TileWriter.write --json
  With TileWriter.write replayed from captured calls: 1.51 s (was 1.92 s).
  H2 cheap test: supported. At most 21% of time is in that call.

● [writes a candidate for H1 in its worktree]
● perfproof compare bench-tile --candidate .perfproof/worktrees/h1 --detach --json
  Started run 7a2e. Follow with `perfproof status 7a2e`.
● perfproof status 7a2e --json
  Round 30 of at most 120, look 3 of 6. Gates: all passed.
● perfproof status 7a2e --json
  KEEP  performance  speedup 1.34x  interval 1.29x to 1.40x  40 rounds
  Guardrails: peak memory +2% (within 10%), passed.
  Committed to perfproof/session-0f3 as 1c9d2e4.

● [writes a candidate for H2: batch fsync per level]
● perfproof compare bench-tile --candidate .perfproof/worktrees/h2 --json
  REJECT  correctness  gate coverage
  Changed lines tilekit/io.py:152-158 never ran under any check.
  Undone.

  H2 needs a test that reaches io.py:152-158. tests/ is locked for this
  spec, so a new test means a new spec and a new baseline, which is
  your call. Add one, or skip H2?

> skip it

● perfproof log update bench-tile H2 outcome=skipped reason="untestable without new test" --json
● [writes a candidate for H3: lru_cache on _level_shape]
● perfproof compare bench-tile --candidate .perfproof/worktrees/h3 --json
  INCONCLUSIVE  insufficient_evidence  speedup 1.02x  interval 0.99x to 1.06x
  At this noise a decision needs about 410 rounds (~25 min). Options:
  more rounds, a quieter machine, or --metric counter on Linux.
  Undone.

● perfproof compare bench-tile --baseline session-start --candidate perfproof/session-0f3 --json
  KEEP  performance  speedup 1.33x  interval 1.28x to 1.39x

  Done. One change kept on perfproof/session-0f3 (1.33x, interval
  1.28x to 1.39x, memory +2%). H2 is likely worth up to 1.27x but needs
  a test for io.py:152-158. H3 couldn't be settled at this noise.
  Full report: .perfproof/runs/9b41/report.md
```

What the transcript pins down for the plan:

- `init` proposes and asks; it never fills the goal by default.
- `profile`, `measure --sizes` and `measure --stub` are the discovery
  primitives; the hypotheses are the agent's, recorded by `log`.
- A gate that can't be satisfied stops the attempt and comes back to
  the developer with the exact lines; perfproof doesn't edit locked
  tests to get a KEEP.
- INCONCLUSIVE comes with what would settle it.
- The session ends with one comparison of the whole kept stack against
  where it started.
