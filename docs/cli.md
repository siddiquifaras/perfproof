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

## Saved profiles

Every `profile` run keeps the result it returns. The run's directory,
`.perfproof/profiles/<name>/<id>/`, holds `result.json`: the `--json`
`result` object, written whether or not `--json` was passed, to a
temporary file and then renamed, so it is whole or absent. A reader
(an agent after its context was cut, the skill eval's scorer) reads the
file; it never profiles again to see the same lists.

The object is the `result` of `profile --json` as it stands, with
`hotspots` and `frames_by_total` in the order printed, plus two keys
that name the run: `run_id` (the directory's name) and `finished_at`
(UTC, `YYYY-MM-DDTHH:MM:SS.ffffffZ`, the moment the file was written).
Keys are only ever added. A write that fails is `state_write_failed`,
as for the run's directory.

## Long runs

`compare` runs in the foreground by default. When a comparison may
outlast the caller's command timeout, the agent adds `--detach`. There
is no daemon, queue or job service: a detached compare is one process
in its own session, and its run directory is the only state.

| Piece | What it is |
|---|---|
| `runs/<id>/job.json` | What to run: spec, trees, order seed, timeout and deadline, and when each resume happened |
| `runs/<id>/progress.json` | Replaced after each completed round, outside the timed region: phase (`gates` or `rounds`), rounds done, looks, round budget |
| `runs/<id>/samples.jsonl` | One line per completed round, appended after it is written; not fsynced, so no flush burst before the next round |
| `runs/<id>/machine.json` | The machine fingerprint taken before anything ran, and the start time. A resume needs it |
| `runs/<id>/trees.json` | A manifest of both trees' files at the start. A resume needs it |
| `runs/<id>/run.lock` | Held while a resume decides and spawns |
| `runs/<id>/pid` | Process ID and its start time, so a reused PID isn't mistaken for the run |
| `runs/<id>/cancelled`, `timed_out`, `failed.json` | Written by `cancel`, by the timeout, and by a stop with no verdict (`code`, `message`) |
| `runs/<id>/detached.log` | The process's stdout and stderr |

A resume without `machine.json` or `trees.json` is `not_resumable`.

`status <id>` and `cancel <id>` take a run id or a unique prefix of it
or of its random part. `compare --resume <id>` takes the same.

### Job states

`status` reports exactly one state. It is decided in this order, first
match wins:

| State | Meaning |
|---|---|
| `finished` | `record.json` exists. Nothing else decides this. The `status` result then carries the verdict |
| `cancelled` | The `cancelled` marker exists and there is no record. No verdict |
| `timed_out` | The `timed_out` marker exists and there is no record: the hard timeout fired. No verdict |
| `failed` | The compare stopped with no verdict (`broken_baseline`, `harness_fault`, `unsupported_spec`, `internal_fault` for a fault in perfproof, or any resume-time refusal the new process finds, such as `machine_changed` or `invalid_job`); `failed.json` has the code and message. No resume can help |
| `running` | The process in `pid` exists, is not a zombie and has the start time written in `pid` |
| `interrupted` | None of the above: the process is gone and left no record, as after `kill -9` or a reboot. The only state `--resume` accepts |
| `unknown` | The process table could not be read, so whether the process lives is not known. Never resumable; a failed read is not "gone" |

A reused PID is never taken for the run: a process counts only when its
start time equals the one in `pid`.

### Commands

| Command | Behaviour |
|---|---|
| `compare --detach` | Does everything that can be refused (spec, revisions, trees), then starts the comparison in a process of its own session and returns at once with the run id. `--timeout SECONDS` is its hard total timeout |
| `status <id>` | Reads the run directory and checks the process; changes nothing |
| `cancel <id> [--grace SECONDS]` | Marks the run `cancelled`, then ends its process and every descendant: SIGTERM to the run's process group (the run leads its own), then SIGKILL for everything still alive, found by parent and by process group. `--grace` (default 10 s) is the longest it waits for the run's process to exit; the SIGKILL sweep starts as soon as it has, at once for an `interrupted` run. No verdict. An already `cancelled`, `timed_out` or `failed` run keeps its state but its processes are still ended. A `finished` run is refused |
| `compare --resume <id>` | Continues an `interrupted` run from its completed rounds. Always detached: it returns at once with the run id and the same `--json` shape as `--detach`. `--baseline` and `--detach` are accepted and ignored |

`--resume` takes no benchmark name, `--candidate`, `--spec-hash` or
`--timeout`: they are the run's. Before anything starts it checks that
the run is `interrupted`, that its journal is whole (a torn last line is cut and that round runs again; only a complete line that is not the next round makes the journal invalid), that both trees
hold the files they held when it began, that the hard timeout has not
passed, and that the machine is the one the run began on (the probes
that identify a machine, not load or temperature). It ends any process
of the run left by a `kill -9`, then the new process checks every gate
again and continues from the journal. Two resumes at once are
serialized by a lock; the second finds the first running and is
refused. The record marks where it resumed.

### Timeout

`--timeout SECONDS` applies to `--detach` only (with a foreground
`compare` it is a `usage` error). It must be above 0 and at most 2147483647, and is rounded up to whole
seconds with a minimum of 1 s. The default is twice the spec's
`budget.max_seconds` plus 3600 seconds. The default's factor and floor
and the 10 s kill grace are chosen allowances, not measured values.
The deadline is fixed when the run starts, so it covers the whole run
including resumes. The run ends itself first when it can: before it
starts a round that could not finish, with the 10 s grace, before the
deadline, it takes its final look on the rounds it has, as at the end
of `budget.max_seconds`, and writes its record with a
`time_limit_reached` warning. That is the state `finished`, with an
INCONCLUSIVE verdict (always, while the procedure is uncalibrated)
from the rounds it has. When the deadline is hit with no such end
(the run is inside a round or the gates) the run's process and
everything it started are killed (SIGALRM in the run's process, then
SIGKILL of the tree if unwinding takes longer than 10 seconds), the
`timed_out` marker is written, and the state is `timed_out`: no
verdict. A `timed_out` run can't be resumed.

### Results

`compare --detach` and `compare --resume`, with `--json`:

```json
{"run_id": "str", "run_dir": "str", "state": "running", "pid": 0}
```

`compare` in the foreground, with `--json`:

| Field | Type | |
|---|---|---|
| `run_id`, `run_dir`, `record_path`, `report_path` | string | Absolute paths |
| `verdict` | object | The record's verdict: `outcome`, `reason`, `rule`, `gate`, `estimate`, `interval`, `rounds`, `looks`, `procedure`, `diagnostics` and the rest |
| `gates` | array | `{"gate", "status"}` in the record's order |
| `warnings` | array | `{"code", "message"}` for every warning in the record |
| `not_verified` | array | What withholds a KEEP, in plain terms for the skill: gates `not_available` with their reasons, guardrails `not_declared`, `unmeasurable` or `not_established`, and the warnings `counter_unavailable`, `resolution_unknown` and `schedule_uncalibrated`. Each is `{"kind", "name", "status", "reason"}`; empty when nothing was left unverified |
| `measured_metric` | object or null | The metric actually measured when it is not the spec's: a `counter` that fell back to wall time in seconds. Null otherwise |

`verdict.procedure` names the interval procedure that decided:
`hac_lf` or `t_log_ratio_batch_means`. `compare` picks it from the
spec's look schedule before any round, never from data: `hac_lf` for
the three default schedules of the Python and native, web service and
agentic, and LLM serving classes (`n0`, `k`, `max_looks` and
`max_rounds` of 10, 10, 6, 60; 10, 10, 3, 30; and 10, 6, 3, 22), and
`t_log_ratio_batch_means` (batch means with the diagnostic gate) for
the GPU kernels schedule (20, 20, 6, 120) and for any other schedule.
A spec that edits its schedule therefore gets the gated procedure, the
fail-closed choice, and so does any
`alpha` other than 0.05. Under `hac_lf` the diagnostics
`autocorrelated_rounds` and `outlier_rounds` are null: it has no gate.
`docs/method.md`, "The T1.11 decision", is the rule.

When changed lines ran under no check, the `coverage` gate is
`not_available` and its `not_verified` reason names them by file
(`work.py lines 12-14 never ran`); the JSON fields are unchanged. The
run record's `coverage` gate carries the same lines in `unverified`.

### Coverage for other languages

`compare` measures Python itself. For any other language, the spec
declares an optional `correctness.coverage_command` (an argv list, with
`correctness.coverage_timeout_seconds`, required with it and only with
it) that writes a standard coverage report. It is backward compatible:
absent by default, a spec without it keeps its hash, and `schema_version`
stays 4.

When non-Python files changed and the command is declared, `compare`
runs it once in the candidate tree (in `correctness.cwd`) with the
environment variable `PERFPROOF_COVERAGE_REPORT` set to a file path that
does not exist, and reads lcov (`SF:`, `DA:`, `end_of_record`) or
Cobertura XML from there. With no command declared nothing extra runs
and those changes are `not_available` ("no coverage command declared").
Paths in the report are relative to `correctness.cwd` (Cobertura: to
each `<source>` first) or absolute inside the tree. A changed line
listed with zero hits is unverified (gate `not_available`, lines
named); a line not listed is not executable. The full contract is
"Coverage reports" in `docs/method.md`.

`status` carries the same `not_verified` and `warnings` in its `verdict`
object for a finished detached run.

`status`, with `--json`:

| Field | Type | |
|---|---|---|
| `run_id` | string | The full run id |
| `state` | string | One of the states above |
| `rounds_done` | integer | Completed rounds, counted in `samples.jsonl` |
| `max_rounds`, `looks` | integer or null | From `progress.json`; null before it was written |
| `phase` | string or null | `gates` or `rounds`; null before `progress.json` was written |
| `verdict` | object | Only when `finished`: `outcome`, `reason`, `rule`, `gate`, `rounds`, `looks`, `estimate`, `interval` (`[lower, upper]` or null), `record_path`. The rest is in the record |
| `error` | object | Only when `failed`: `code` and `message` strings |

`cancel`, with `--json`: `{"run_id": "str", "state": "cancelled"}` when
the run was cancelled. For a run that had already `timed_out` or
`failed`, `state` is that state.

### Error codes

| Code | Command | When |
|---|---|---|
| `not_a_repository` | all three | `--repo` is in no git working tree |
| `not_found` | `status`, `cancel`, `--resume` | No run, or more than one run, matches the id |
| `not_found` | `compare` | No such benchmark, no spec matches (including a `--spec-hash` that is not hex), or `--candidate` is an existing path that is not a directory |
| `invalid_record` | `status` | The record of a `finished` run does not read back |
| `invalid_record` | `compare` | The spec directory can't be read or a spec does not load, foreground or `--detach` |
| `ambiguous_hash` | `compare` | More than one spec of the benchmark matches |
| `invalid_revision` | `compare` | A revision names no commit, or a worktree can't be made |
| `already_finished` | `cancel` | The run has a record |
| `cancel_incomplete` | `cancel` | A process of the run survived SIGKILL (`details.pids`), or the marker or process table could not be written or read |
| `usage` | `cancel` | `--grace` is negative or not finite |
| `usage` | `compare` | `--timeout` without `--detach`, or not above 0 or above 2147483647; a name or `--candidate` missing (both are required without `--resume`); `--resume` with a name, `--candidate`, `--spec-hash` or `--timeout` |
| `not_resumable` | `--resume` | The state is not `interrupted` (`details.state`); the files a resume needs are missing; a tree is gone or its files changed since the run began; the hard timeout has passed |
| `machine_changed` | `--resume` | The machine's identifying probes differ from the ones the run began on (`details.differs`). Rounds from two machines are never mixed; start a new compare. Also written to `failed.json` if the new process finds it |
| `invalid_journal` | `--resume` | A complete line of `samples.jsonl` is not the next round, or is damaged. A torn last line is not this: it is cut and re-run |
| `invalid_job` | `--resume` | `job.json` is missing or does not read back |
| `orphan_running` | `--resume` | A process of the run left by a `kill -9` could not be ended; perfproof does not resume beside it (`details.pids`) |
| `state_unknown` | `--resume` | The process table could not be read, so leftover processes can't be ruled out |
| `spawn_failed` | `--detach`, `--resume` | The detached process did not start or could not be identified (`details.run_dir`; the message has the log tail) |

`invalid_revision`, `ambiguous_hash`, `not_found` and `invalid_record`
are raised before the run starts, so `--detach` returns them directly.
`broken_baseline`, `harness_fault`, `unsupported_spec` and
`internal_fault` come from the foreground compare (with
`details.run_dir`) or sit in `failed.json` of a detached run.
A thing perfproof merely cannot verify is never a stop, a launcher
that may download code included: it is a gate `not_available` or a
guardrail `not_declared` in the record, and the verdict is INCONCLUSIVE
naming it. A resume that would measure another metric kind than the
first attempt (perf usable in one and not the other) is stopped with
`not_resumable` in `failed.json`, with a plain reason, because the
rounds already taken would mix units.

A run that is killed before it finishes never yields a verdict.

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

● perfproof project confirm target-python_cpu bench-tile --json
● perfproof project goal bench-tile --metric wall --confirm --json
● perfproof project constraint add --kind public_api
    --subject "public API" --confirm --json
● perfproof project constraint add --kind forbidden_dependency
    --subject "any new dependency" --confirm --json
  Confirmed. Guardrails: peak memory, 10% (default for python). No bar
  was given, so the default bar applies and is named as such.

● perfproof bench define bench-tile --json
  Spec 4f1c… locked: benchmarks/bench_tile.py, tests/ (214 files),
  data/sample.tif

● perfproof profile bench-tile --profiler py-spy --json
  Top frames (share of wall time):
    38%  tilekit/resample.py:88   _bilinear_row
    21%  tilekit/io.py:140        TileWriter.write   (waiting: CPU 4%)
    12%  tilekit/pyramid.py:61    _level_shape
  Then a second table, "Top frames by total time": frames ranked with
  their callees, including ones with no self time (`--json`:
  `result.frames_by_total`, beside `result.hotspots`). A library
  frame with no self time that has a project frame deeper in every
  stack it appears in (it only calls back into project code, such as
  Rust's `__rust_begin_short_backtrace`) is left out; project frames
  and library frames with self time never are.
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
  INCONCLUSIVE  insufficient_evidence  gate coverage not available
  Changed lines tilekit/io.py:152-158 never ran under any check, so
  this can't be kept. Undone.

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
