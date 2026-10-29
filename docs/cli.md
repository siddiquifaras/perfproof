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

The result also carries, added after the keys above:

- `project_frames_above_bar`: the project's own frames
  (`is_library` false; never a harness frame) whose total share is at
  least `min_effect`,
  ranked by total, as `Hotspot` objects. It is not cut to `--top`, only
  to 50, so every project function worth a cap is listed even when
  the printed lists don't reach it. Frames the total ranking leaves
  out (synthetic and entry frames, and the frames on every stack above
  the point where stacks first differ) are not in it either.
- `next_step`: `null`, or `{"code", "message"}` exactly when
  `project_frames_above_bar` is empty, by one of two codes. Which one
  is decided over every sampled stack, not the printed lists.
  `no_project_frame_above_bar`: some frame was classified as the
  project's and none is at the bar, so profile a second workload that
  enters the project another way (another entry point or direction)
  before choosing an idea. `project_frames_unknown`: no frame was
  classified as the project's at all. The message depends on how the
  frames name their code. Frames written `module`symbol` with no source
  file (native, Rust, .NET without sources) are the project's only when
  `--own-module` names their binary or library, so it says to rerun
  with `--own-module <name>`. Frames with source paths (Python, Node,
  Java, Go, Ruby) are classified by the project root, which
  `--own-module` cannot change, so it says no frame is under the root
  (it names the root) and to check `--repo` and that the workload calls
  the project's code, not only an installed copy. No other condition sets it. The text output
  prints the message as one line. Results saved before it existed lack the key; readers treat a
  missing key as `null`.
- `min_effect`: the spec's bar as a fraction, which the list above was
  cut at.
- `own_modules`: the `--own-module` values the run was given, which
  `profile show` needs to classify frames the same way.
- `size`: only in a run made by `profile --sizes`: `kind`, `name`,
  `base`, `factor`, `value` and the `group` the sizes of one command
  share. A plain profile has no `size` key.

### Looking a function up: `profile show`

```
perfproof profile show <name> [<run-id>] --function <pattern> [--json]
```

Reads the saved run `.perfproof/profiles/<name>/<run-id>/` (a run id is
a directory name or a unique prefix of one; the latest run when
omitted) and answers for the functions that match `<pattern>` from the
run's folded stacks, which are streamed once: nothing is profiled again.
It exists for the function the agent suspects from reading the code and
that the printed lists don't show. `--function` goes after the names.
A benchmark named `show` is profiled as usual unless `--function` or a
second name follows.

A pattern is matched as the skill evaluation matches its hotspot names
(`perfproof.profilers.names`): the last component of the printed name
must equal the pattern's, and an owner in the pattern (`Echo.ServeHTTP`,
`Foo::bar`) must end the printed owner. Arguments, generics, import
paths and Rust hashes in the printed name are ignored, and a name that
only shares a suffix (`sum` for `slow_sum`) never matches. Every
function is searched, including the entry and synthetic frames the
printed lists leave out.

`--json` result: `name`, `run_id`, `directory`, `folded`, `profiler`,
`basis`, `weight_unit` (as saved), `pattern`, `samples` (all samples of
the profile, the denominator), `found`, `functions`, `functions_omitted`,
`reason`, `match`, `note`, `min_effect` (the run's bar, `null` if it
did not save one) and `labels`. `functions` lists the matches by total share then self
share, at most 50, each with `function`, `file`, `line`, `frame_kind`,
`is_library`, `is_harness`, `self_fraction`, `total_fraction`, `self_samples`,
`total_samples`, `cap` (`1 / (1 - total_fraction)`, a bound and never a
measured effect; `null` when the function is the whole profile), `seen`
and, when the bar is known, `cap_above_bar` (`true` for a `null` cap).
Two functions of the same name in different files are two entries.

`match` says how the matches were found: `exact`, or, for a pattern with
an owner that no printed frame carries (`kernels.fib`, where the frame
prints as `fib (app/kernels.py:12)`), `file` (the owner is the file's
stem) and then `bare_name` (the name alone). `note` says which when it
is not `exact`. A function present under its bare name is never
reported as absent. Eval scoring keeps the strict rule.

A function with no sample is not an error: `found` is `false`,
`functions` holds one entry for the pattern with zero shares and
counts, `cap` 1.0 and `seen` false, and `reason` and that entry's
`reason` say `not seen in this profile`, with the two causes that can
hide a function the workload runs (inlined, or unsymbolised).

Error codes: `not_a_repository`, `invalid_name`, `invalid_argument`
(no `--function`, a wrong number of names, a run id that is not a
directory name), `invalid_pattern`, `not_found` (no saved run, or none
with that id), `ambiguous_run`, `invalid_record` (`result.json` is not
a profile result), `state_read_failed` and `invalid_profile`.

### Profiles at several sizes: `profile --sizes`

```
perfproof profile <name> --sizes 1,2,4 (--size-arg=--n=1000 | --size-env N=1000)
                  [--profiler ...] [--json]
```

Profiles the benchmark once per size, using the size mechanism of
`measure --sizes`: `--size-arg=FLAG=BASE` appends `FLAG <size>` to the
spec's command, `--size-env NAME=BASE` sets the variable, and the sizes
are `BASE` times each multiplier of the list (at least three distinct;
an `x` suffix is allowed). Each size is an ordinary profile run: its own
directory and `result.json`, with one added key, `size` (`kind`, `name`,
`base`, `factor`, `value`, `group`; absent from a plain profile). The
runs go back to back and nothing else happens between them: the
summaries, evidence, saved results and fits are made after the last
profiled run, so perfproof's own work never sits between two runs. A
failure at one size ends the command with the error of that run,
naming the size (`details.size`); the
directories and the profiler's raw output of the sizes already
profiled stay, but no result is saved for any size (so `profile show`
and `--rank` do not see them).

Then it fits how each project function's **cost** grows with size.
Cost at a size is the function's total share of that size's profile
times the unprofiled run's wall time there, in seconds (an estimate:
two separate runs). The exponent `e` of `cost ~ size ** e` is the
slope of a least-squares line on the logs
(`perfproof.bench.estimates.fit_exponent`), with its 95% interval,
`r_squared` and `n`, the number of sizes that saw the function. The
workload's own exponent is fitted the same way to the wall times. Each
fitted function gets an `evidence`: `shown` when the lower end of its
interval is above the upper end of the workload's (its share, and so
its cap, rises at bigger inputs, at these sizes); `lead` when not, but
its exponent is at least 0.25 above the workload's (the intervals
overlap, so the fits cannot tell the two apart: a place to look, with
the note `interval_overlaps_workload`; 0.25 is an arbitrary display
margin that only keeps near-ties out of the leads); `none` otherwise;
`null` without a fit or a workload fit. Only `shown` is ever worded as
"faster than the workload" or "its ceiling rises". The method and its
limits are in `perfproof.profilers.growth`.

`--json` result: `name`, `spec_hash` (the benchmark's, not the
size-modified command's), `size_group`, `knob`, `sizes`, `runs`
(`factor`, `size`, `run_id`, `directory`, `wall_seconds`,
`profiled_wall_seconds`), `workload` (`exponent`, `low`, `high`,
`r_squared`, `n`; `null` below three sizes), `functions` (every project
function seen at any size, by exponent descending), `faster_than_workload`
(those with `evidence` `shown` or `lead`, or that appear only at larger
sizes),
`growth_file` and `labels`. Each function has `function`, `file`,
`line`, `frame_kind`, `costs`, `total_fractions` and `samples` (one per
size, `null` where unseen), `fit` (`null` when fewer than three sizes
saw it), `evidence`, `appears_at_larger_sizes`
and `notes`. The same document is saved as
`.perfproof/profiles/<name>/growth-<group>.json`.

`notes` say when to distrust a fit: `few_points` (fewer than four
sizes; three give an interval with one degree of freedom, which is
very wide), `noisy` (`r_squared` below 0.9 or an interval wider than
1), `few_samples` (under 30 samples at some size, counting a
pyinstrument millisecond as a sample), `missing_sizes` (seen at only
some sizes; fitted from those, and not at all below three: `no_fit`).
Limits: one profile per size, so fits rest on 3 or 4 points; a wall
time with start-up in it pulls the workload's exponent toward 0 and
flags more functions; a function that dominates the workload has the
workload's own exponent and is not flagged (its cap at the largest
size, in `profile show --rank`, shows it); a knob that changes no work gives exponents near
0; functions match across sizes by kind, file and name, not line.

Errors, beyond those of `profile`: `invalid_argument` (`--sizes` without
`--size-arg` or `--size-env`, or the reverse, or a malformed knob);
fewer than three distinct sizes is a usage error (exit 2).

### Ranking across saved profiles: `profile show --rank`

```
perfproof profile show --rank [<name>...] [--top N] [--json]
```

Reads every saved profile run of the named benchmarks (every benchmark
with saved profiles when none is named) and ranks the project's
functions across all of them. It profiles nothing: each run's folded
stacks are streamed once, so with many large profiles it takes as long
as reading them. `--rank` replaces `--function`; a benchmark named
`show` is still profiled as usual unless `--rank`, `--function` or a
second name follows. Run directories are named by their UTC start to the microsecond, rising
with each one a process creates, so the latest run (what `profile show`
reads by default) is the last created, also within one `--sizes` run.
Delete old runs under `.perfproof/profiles/` when
the code has changed: the ranking has no notion of a stale profile.

A frame whose file is under the project's `.perfproof/` directory (the
workload driver an agent keeps in `.perfproof/harness/`) is not project
code: it is `is_library` true and `is_harness` true on its `Hotspot`,
so it is never ranked, never in `project_frames_above_bar` or a growth
fit, never counts as a project frame for `next_step`, and its file is
never counted as sampled. It stays in `hotspots` and `frames_by_total`
with `is_harness` set, so an agent can see its driver's cost.

The rule is fixed: **cap at the largest size measured, descending.**
Within each benchmark, take the profile at its largest measured size (a
plain profile is its own largest) and the function's cap there,
`1 / (1 - p)` for its total share `p` in that profile; across
benchmarks take the largest of these. A function that is a whole
profile has no finite cap and comes first. A function not sampled at a
benchmark's largest size has an unknown cap there (not a small one); if
that is so for every benchmark that saw it, its cap is unknown and it
comes last, saying so. Ties go to the function seen in more profiles,
then file, line and name. Real inputs are at least the test size, so a
function that is costly only at small sizes does not outrank one that
dominates at the largest. Nothing is projected beyond the measured
sizes. The highest cap in any profile stays in each row as
`best_any_size`, labelled informational and never used to rank. A cap
is a bound, never a measured effect. A **wrapper** (`wrapper` true: a
known cap that is not finite, and no time of its own, such as `main`)
keeps its place in the JSON, flagged; the text output lists wrappers
after the real candidates and says so.

`--json` result: `benchmarks`, `runs` (`benchmark`, `run_id`, `size`,
`factor`, `samples`, `wall_seconds`), `skipped_runs` (a run that cannot
be read, with the `reason`; it never fails the ranking), `order`,
`functions`, `coverage` and `labels`. Each function row has
`function`, `file`, `line`, `frame_kind`, `wrapper`, `cap` (the ranking
cap above; `null` when not finite or unknown) and `cap_known`,
`total_fraction` and `self_fraction` (in the profile of that cap;
`null` when unknown), `cap_above_bar` (that run's bar), `ranked_in`
(benchmark, `run_id`, `size` of that profile), `at_largest` (per
benchmark that saw it: the largest `size`, `seen`, `run_id`,
`total_fraction`, `cap`; the last three `null` when not seen there),
`best_any_size` (`cap`, `total_fraction`, benchmark, `run_id`, `size`,
`label`), `seen_in` (benchmark, `run_id`, `size`, `factor`, total share
and cap in each profile that saw it), `growth` and
`ceiling_rises_with_size`. `growth` is the fit of the most-pointed
`--sizes` run of the benchmark that saw the function: `exponent`,
`low`, `high`, `n`, `workload_exponent`, `evidence`,
`appears_at_larger_sizes` and `notes`, as above; `null` when no scaling
run saw it. `ceiling_rises_with_size` is `true` only when the
growth is `shown`, `false` when the evidence is `none`, and `null` for
a lead, for a function seen only at larger sizes, and for a function no
scaling run saw (`growth` tells these apart). A `true` with `few_points`
or `noisy` in `notes` still rests on few, scattered points. The text
output is the top `--top` rows in words, then the never-sampled files.

**Never sampled.** `coverage` lists the project's source files that no
function of any saved profile sampled: `source_files`, `sampled`,
`never_sampled` (the count), `never_sampled_by_suffix`, `files` (all of
them, at most 5000), `suffixes` and `suffix_source`, `walk_truncated`
and `excluded`. It is not line or branch coverage: a file listed was
never run by the saved workloads, or only for too little time to be
sampled. Source files are those of the project walk that `init` uses
(VCS, virtualenv, build and cache directories skipped; depth and file
count bounded), with a suffix of a language the project has, minus test
files (a `test` or `tests` directory, `test_*`, `*_test.*`,
`conftest.py`). The project model records target classes, not
languages, so the suffixes are a fixed table per target class (Python
for every class but `native`, whose C, C++, Rust and Go suffixes stand
in for it; `.cu` and `.cuh` for `gpu_kernel`) plus the suffix of every
project file a profile did sample; with no project model, the sampled
files' suffixes alone.

Errors: `not_a_repository`, `invalid_name`, `not_found` (no readable
saved profile) and `state_read_failed`.

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
`time_limit_reached` warning. That is the state `finished`, with the
verdict that look gives (INCONCLUSIVE unless its interval clears or
falls below the bar) from the rounds it has. When the deadline is hit
with no such end (the run is inside a round or the gates) the run's process and
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
| `not_verified` | array | What withholds a KEEP, in plain terms for the skill: gates `not_available` with their reasons, guardrails `not_declared`, `unmeasurable` or `not_established`, and the warnings `counter_unavailable`, `resolution_unknown`, `schedule_uncalibrated`, `schedule_not_class_own`, `target_class_unknown` and `target_class_unverified`. Each is `{"kind", "name", "status", "reason"}`; empty when nothing was left unverified |
| `measured_metric` | object or null | The metric actually measured when it is not the spec's: a `counter` that fell back to wall time in seconds. Null otherwise |

`verdict.calibration` is `calibrated` exactly when all five hold: the
spec declares `target_class` (one of `python_cpu`, `native`,
`web_service`, `llm_serving`, `gpu_kernel`, `agentic`), the project
model (`.perfproof/project.json` of the main checkout) has a target
entry of that class in state `confirmed` and not stale by the check of
`perfproof project check` (`proposed`, `stale` and `retired` do not
count; a changed evidence file, or no model, does not count), its look schedule is
that class's own default schedule, `alpha` is 0.05, and that schedule
is calibrated (`gpu_kernel` is not yet: no real traces).
Then `verdict.target_class` is the class and
`verdict.calibration_conditions` names the procedure, schedule, `alpha`
and the seed of the run of record. Otherwise it is `uncalibrated` and a
KEEP is withheld (INCONCLUSIVE, `insufficient_evidence`) with one
warning naming why: `target_class_unknown` (no class declared),
`schedule_not_class_own` (the schedule is another class's),
`target_class_unverified` (the project model does not confirm the
class) or `schedule_uncalibrated` (`alpha` or schedule outside the domain,
including `gpu_kernel`). A REJECT on evidence is decided the same
either way. "The calibrated domain" in `method.md` has the rule.

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

A guardrail with absolute limits adds `standing` to its result in the
record (`record.json`, `guardrails[]`), one entry per limit in the spec's
order, for every outcome of a guardrail that was evaluated. The fields
are additive (a reader that does not know `standing` or the spec's
optional `limit_quantile` loses nothing, and a spec that sets no
`limit_quantile` hashes as before):

| Field | Type | |
|---|---|---|
| `label`, `limit` | string, number | The limit's label and cap, in the guardrail's unit |
| `quantile` | number | The statistic the cap is on: 0.5 (the median) unless the spec declares `limit_quantile` (such as 0.99; only the median is calibrated) |
| `typical` | number | The sample's own value of that statistic |
| `reliable_value` | number or null | The one-sided bound in the cap's direction, in these runs: the upper bound for a cap, the lower for a floor. Its design confidence holds only for rounds that behave like a stable machine; it is not claimed for drift or another machine. Null while too few rounds give a bound, and for a guardrail recorded `unmeasurable` |
| `gap` | number or null | `reliable_value - limit`, signed; null with `reliable_value`. For a cap, at most 0 is met; for a floor, at least 0 |
| `rounds_over`, `rounds` | integers | Rounds beyond the cap, of the rounds the figures use |

`reliable_value` is information, never a verdict: the status is read off
the recorded interval, and the figure never by itself causes a KEEP.
The report prints one line per limit, for example `Limit memory: median
498 MB; in these runs it stayed under 512 MB (cap 500 MB, 12 MB short);
4 of 40 rounds over the cap. On a busier or quieter machine this number
can move`. The figures describe the session, on the machine as it was. `docs/method.md`, "Absolute limits", is the rule.

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
does not exist, and reads lcov (`SF:`, `DA:`, `end_of_record`),
Cobertura XML or JaCoCo XML from there. With no command declared nothing extra runs
and those changes are `not_available` ("no coverage command declared").
Paths in the report are relative to `correctness.cwd` (Cobertura: to
each `<source>` first; JaCoCo: package path plus source file name under
`src/main/java` and the other source roots) or absolute inside the tree. A changed line
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

## What a benchmark prints

A `reported` metric, and every guardrail the runner cannot measure
itself, comes from one line the benchmark prints on stdout, once per
execution:

```
PERFPROOF {"value": 12.500000, "p99_latency": 41.250000}
```

The line is `PERFPROOF`, a space and one JSON object. perfproof reads
this line and no other output, with one parser, for the objective and
for the guardrails.

| Key | When | Meaning |
|---|---|---|
| `value` | The spec's metric is `reported` (required then; with no `reported` metric it is not allowed) | The primary metric, in the metric's unit |
| a guardrail's `metric` name, such as `p99_latency`, `throughput` or a project's own | Every applicable guardrail except `peak_memory`, which perfproof measures from `wait4` | That guardrail's value for this execution, in the guardrail's unit |

- When the spec reports at least one value (a `reported` metric or an
  applicable reported guardrail), perfproof reads the line; otherwise it
  reads no output. Values are positive, finite JSON numbers;
  0 is refused for a guardrail as for `value`. A string, `null`, `NaN`,
  `0`, a negative number, a repeated key, a key the spec does not ask
  for (including a guardrail marked not applicable), or two
  `PERFPROOF` lines in one execution fails that execution with
  `cause: output`, as a bad `value` does today.
- A guardrail's value is whatever the benchmark computes for that
  execution, such as the 99th percentile of that execution's own
  request latencies. perfproof takes it as given and does not compute
  or check it. It pairs the baseline's and the candidate's values by
  round, the way it pairs peak memory, and records them per round in
  `samples.npz` (column `reported_<metric>`, NaN where unknown) and, for
  a detached run, in `samples.jsonl`.
- Print a fixed number of decimals (`%.6f`). The resolution of each
  value, which widens its interval, is the place of its last printed
  digit, as for `value` (`docs/method.md`, "Resolution").
- **A guardrail the line leaves out is unknown for that execution**,
  never a default. A missing line is the same when the metric is not
  `reported`. A round with the value unknown in either arm makes the
  guardrail `unmeasurable`, with the count of executions in its reason
  (`docs/method.md`, "Guardrails", "Missing values"), so the comparison
  can't KEEP. perfproof does not measure it on the rounds that have it.
- A cap on a quantile near 1 has no rank bound at the default budgets
  (`docs/method.md`, "The owner's decision on limits"); that rule is
  unchanged by where the values come from.

A worked example. A web benchmark times the work and reports its 99th
percentile latency in seconds, with throughput as the primary:

```python
import time

latencies = []
start = time.perf_counter()
for request in requests:
    t = time.perf_counter()
    handle(request)
    latencies.append(time.perf_counter() - t)
elapsed = time.perf_counter() - start

latencies.sort()
p99 = latencies[int(0.99 * (len(latencies) - 1))]
print(
    'PERFPROOF {"value": %.3f, "p99_latency": %.6f}'
    % (len(requests) / elapsed, p99)
)
```

with these in its spec (`direction` and `unit` as declared there):

```json
{"metric": {"kind": "reported", "direction": "higher_is_better",
            "unit": "requests/s"},
 "objective": "throughput",
 "guardrails": [{"metric": "p99_latency", "direction": "lower_is_better",
                 "unit": "s", "allowance": 0.05,
                 "allowance_source": "default"}]}
```

Each execution prints a line like `PERFPROOF {"value": 812.400, "p99_latency":
0.041250}`. The comparison then checks, round by round, that the
candidate's p99 is not worse than the baseline's by more than 5%: a
breach of that allowance is a `guardrail` REJECT when the primary shows
the change faster (`docs/method.md`, "Decision"; otherwise the primary's
own outcome stands, with the breach recorded), a p99 shown within it
no longer blocks a KEEP, and a p99 the benchmark did not print, or
printed too coarsely (fewer distinct values than half the rounds in
either arm), makes the guardrail `unmeasurable`, which does.

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
