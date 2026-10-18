# The /perfproof skill contract

The contract the `/perfproof` skill (T3.8) is built and tested against:
what it does for every input and every bad state. The exact words the
user sees are in [skill-messages.md](skill-messages.md), by ID. The
study rows behind each rule, and what the CLI still lacks, are in
[skill-rationale.md](skill-rationale.md). The CLI is
[cli.md](cli.md); the decision rule is [method.md](method.md).

The skill holds judgment (what to ask, what to try, how to put a
result). The CLI holds everything that must be exact. A rule here that
must always hold also belongs in the CLI; "C-nn" marks where it is
still only a request (rationale, appendix).

## 1. Words

Every sentence the skill writes is a Fact, an Estimate or a Verdict.

| Class | Covers | Words allowed |
|---|---|---|
| Fact | Deterministic results: a check passed or failed, output identical or different, a counted number, lines that never ran | "passed", "failed", "broke", "identical", "differs", "N fewer queries". A count is never called a speed. |
| Estimate | Anything timed that is not a calibrated KEEP or REJECT: an uncalibrated or INCONCLUSIVE compare, a cheap-test timing, a profile share, a cap | "estimate", "interval", "measured", "bound", "share of time", the number with its unit |
| Verdict | A calibrated `compare` outcome (`verdict.calibration == "calibrated"`) | "kept" and "faster" only for KEEP; "not kept" and "slower" only for a REJECT with `verdict.regression`; "undecided" for INCONCLUSIVE |

1. **No direction on an estimate.** Next to an uncalibrated or
   INCONCLUSIVE result the skill never writes faster, slower, speedup,
   gain, win, better, worse, regression, improves, slows, "didn't
   work", "no benefit", "points to", "suggests". It prints the estimate
   and interval as numbers and does not say which side of 1 they fall
   on. Before calibration every compare ends this way
   (`diagnostics.procedure_uncalibrated`; method.md, "Before
   calibration").
2. **Never relay the CLI's own text.** `compare` prints "speedup 1.3x"
   even when uncalibrated. The skill builds sentences from `--json`
   fields only, never from `text` or `report.md`.
3. **Never relay `verdict.regression`** except in R-2 on a calibrated
   REJECT: before calibration it reads "no" for any interval.
4. **A cap is a bound.** Cap `1 / (1 - p)` uses a hotspot's
   `total_fraction` (not `self_fraction`) and names the profile's
   `basis`. It is labelled "bound" and never shares a sentence with
   "faster". A cap at or below `1 + spec.min_effect` is not tried.
5. **Prediction matching measurement is not evidence** the mechanism is
   right. Report both, labelled.
6. **No claim about the user's system without a measurement.** No
   measurement, no claim: if the workload cannot run, the skill says so
   and stops.
7. **No quick timings of its own.** Cheap-test timings may order work;
   they are never reported as evidence. Only `compare` produces a
   number about speed. Counts transfer; microseconds do not.
8. **Round outward**: three significant digits, lower bound down, upper
   bound up. The unit sentence is "Ratios are old time divided by new
   time."
9. **Waived and unavailable are listed.** Every gate with status
   `waived` or `not_available` and every guardrail not `passed` or
   `not_applicable` goes under "Not checked" with its label ("coverage
   not checked", "runtime change allowed", "memory not judged").

## 2. Conventions

- **Last line.** Every session ends with exactly one line, always the
  last:
  `Result: {OUTCOME} ({reason}[, gate {gate}])` copied from
  `verdict.outcome`, `verdict.reason`, `verdict.gate` when a verdict
  exists; otherwise `Result: NO VERDICT ({plain reason})`. Never
  INCONCLUSIVE when no verdict exists.
- **Every stop says**, one sentence each: what happened; what it means
  for the user's code; the next step. The next step is one question or
  one command, at most two options, the recommended one first. One
  question at a time, except the first message of a bare run (three).
- **Permission.** Ask in chat, and wait, before: creating a branch or
  committing; starting a container; setting a spec opt-in
  (`allow_remote_services`, `allow_runtime_change`, unchecked
  coverage); editing or raising a pinned or golden test file; a paid
  host (`gpu-run`; AGENTS.md "Paid compute"); posting to a remote.
  Never merge, push, stash, reset or delete the user's work.
- **Writes.** Only `.perfproof/`, `.git/info/exclude` (so
  `.perfproof/` is ignored), perfproof's own worktrees, and, after the
  checks pass and the user agrees, one branch `perfproof/{name}` for
  harness files and kept changes. No branch exists before the checks
  below pass.
- **Message IDs.** Each user-visible message has an ID in
  skill-messages.md; T3.8 snapshot-tests each.

## 3. Input router

First match wins; each line has one behaviour.

1. A depth word, only as the exact first word (`light`, `medium`,
   `full`), is removed and passed to `--depth` (planned, T3.14). Find
   default `medium`; proving a diff defaults to `light`. The skill
   never restates preset numbers; it prints the CLI's. Depth never
   changes the decision rule.
2. Nothing left: **IN-1**, the bare run (find path).
3. A diff, branch, commit or PR only when it is the whole argument, a
   URL, or marked ("pr 123", "branch x", "commit x", "diff"): **IN-4**,
   prove path.
4. A path that exists, `path::name` or `Class.method`, as the whole
   argument: **IN-3**, find path from that code (a pointer, not a
   scope, unless the user says "only").
5. Anything else is free text: **IN-2**, find path. If the words name
   existing branches or PRs, ask once whether to prove them.

## 4. Prove path (IN-4): no discovery

Gates before timing; one candidate at a time, each against its own
parent. Does not need a clean tree.

1. **PF-1** Resolve refs read-only (`git rev-parse`; `gh pr view`).
2. **PF-2** Baseline is `git merge-base`; show the diff stat; name
   commits the author did not write and ask which to prove.
3. **PF-3** Locked benchmark files missing from the candidate
   (`git ls-tree`): cherry-pick onto the harness branch unchanged, name
   both commits; conflict: stop and ask. Uncommitted work is proved as
   a directory (`--candidate DIR`); the record marks it dirty.
4. **PF-4** List the claims in the commit or PR; flag mixed claims and
   claims the history contradicts; check each or mark it not checked.
5. **PF-5** Find the confirmed workload that runs the changed lines
   (C-22); none: BS-5.
6. **PF-6** Gates (`check`, planned, or the compare gates). New tests
   must fail on the baseline before they count.
7. **PF-7** `compare --detach --depth light` then `status` (BS-11).
   Several candidates are never ranked from separate runs; ranking is
   `confirm` (planned) or one against the other.
8. **PF-8** Report (message PF-8): facts, estimate or verdict, what was
   not checked, run id, minutes to the result.

## 5. Find path (IN-1, IN-2, IN-3)

1. `doctor`. 2. Understand: confirm workload, goal and constraints
(`init`, `project`; nothing is measured on a `proposed` workload).
3. Premise check: measure what the user named next to its siblings in
one run. 4. `profile`. 5. `measure` angles. 6. Hypotheses with
falsifiable predictions and caps (`log add`). 7. Cheap test before any
code; refuted ideas are logged and dropped. 8. Candidate in its own
worktree, `compare` (several: `confirm`). 9. One last `compare` of the
kept stack against the start, then the report.

Rules: history first (`git log -S`, the hypothesis log); do not remove
code before testing that it is redundant; a profile whose top frame is a
wait while children did the work is of the wrong process and ranks
nothing; set every estimate beside its cap (FP-1); ask whether hidden
state is part of the output (FP-2); list what the change touches;
reuse the project's own environment and remove containers the skill
started.

## 6. Bad states before any run, in check order

The first row whose trigger holds speaks; the rest are re-checked after
the user acts. Nothing is written until BS-1 and BS-2 pass on the find
path.

| ID | Trigger (what the skill reads) | Behaviour | Result line |
|---|---|---|---|
| BS-1 | Not a git repository or no commit (`git rev-parse`; `compare` error `not_a_repository`) | Stop. Ask the user to initialise and commit. | NO VERDICT (not a git repository) |
| BS-2 | Find path only: `git status --porcelain` lists changes, ignoring `.perfproof/` and the declared outputs of confirmed specs | Stop; the skill does not stash, reset or commit. Ask the user to commit or set them aside, or to name a branch to prove (prove works as is). | NO VERDICT (uncommitted changes) |
| BS-3 | A service in the project model fails a connect test from where the benchmark runs | Say a check did not run, never that the service is down. Offer a local copy or an address. | NO VERDICT (service not reachable) |
| BS-4 | The config or environment points off this machine, or a local port belongs to something else (`lsof`, `docker ps`) | Never set `allow_remote_services`. Offer a local copy on a free port, with consent. A writing workload needs a reset (T2.19): offer a read-only mix. | NO VERDICT (benchmark would reach {host}) |
| BS-5 | No confirmed workload (`project`); only `proposed` ones | Nothing runs on a proposed workload: confirm it first, showing any script the skill wrote and what cannot be locked. Nothing runnable: stop. | NO VERDICT (no workload) |
| BS-6 | No oracle, or `correctness` gate `not_available`, or the oracle is too slow | Propose an explicit oracle (output on a fixed input, not empty) or a subset, with the user's yes. No KEEP without one. List the rest as not run. | NO VERDICT (no way to check a change) |
| BS-7 | `compare` error `broken_baseline`, or `profile` error `workload_failed`, on the baseline | No record exists. Show the command and a capped log tail; use the project's documented setup; no opt-in. | NO VERDICT (original code will not run) |
| BS-8 | Two baseline runs differ in declared output; or the run did not do the work; or the timed part is tiny (C-02) | Normalise or fix before locking; ask only about shortening. | none (fixed, then continue) |
| BS-9 | Merge-base differs, locked files missing, foreign commits (PF-2, PF-3) | Set the baseline or derive a branch, and say so. | none (fixed, then continue) |
| BS-10 | `doctor` warnings `high_load`, `on_battery`, `low_power_mode`, `thermal_limit`, `non_performance_governor` | Hold timing steps only; name what uses the CPU; never busy-wait; go on if the user says so, with the load at start and end in the report. | none (held, or goes on) |
| BS-11 | Every `compare` | `--detach`, poll `status`, speak when a look completes. A partial run yields no verdict. Paid or remote host: stop and ask with the cost. | NO VERDICT (run not finished) when cut off |
| BS-12 | Any other `ok == false`: `not_built`, `usage`, `internal_*`, `invalid_spec`, `spec_conflict`, `ambiguous_hash`, `unsupported_spec`, `undeclared_guardrails`, `harness_fault` | Fail closed; no retry that changes the experiment. Allowed fixes: newest spec for `ambiguous_hash`; a new spec name for `spec_conflict`; declare the missing guardrail for `undeclared_guardrails`. `not_built` and `usage`: this version cannot do that step. | NO VERDICT (perfproof stopped) |

## 7. Results after a run, first match wins

Read `verdict.outcome`, `verdict.reason`, `verdict.rule`,
`verdict.gate`, `verdict.calibration` and `gates[]`; `compare --json`
carries no gate detail, warnings or guardrails today (C-04).

| ID | Trigger | Behaviour |
|---|---|---|
| R-1 | KEEP, calibrated | Kept; estimate, interval, bar, facts, branch and sha, not checked. |
| R-2 | REJECT `performance` | Not kept: interval at or below the bar; "slower" only if `verdict.regression`. Undo. |
| R-3 | REJECT `guardrail` | Not kept: names the guardrail, value and limit. Undo. |
| R-4 | REJECT `correctness`, gate `correctness` | A check failed or the benchmark crashed: failing ids (capped) or the differing output. A pin or golden file may be updated only by the project's own workflow, toward fewer queries, as a separate commit, after the user says yes; never raised to get a result. Faster but different is REJECT until the user states the allowed difference. Undo. |
| R-5 | REJECT `correctness`, gate `coverage` | Lines never ran. Offer a test that fails on the baseline (a new spec, the user's call) or drop. Undo. |
| R-6 | REJECT `integrity`, gate `lock` | Refused: the change edits a locked file. Offer a spec that does not lock it (new baseline, asked first). |
| R-7 | REJECT `integrity`, gate `side_effects` | Refused: a service not local. As BS-4. |
| R-8 | REJECT `integrity`, gate `runtime` in a returned record | Refused: the two versions would not run the same program. Fix the setup (leftover worktree, native build not built); a waiver is the user's decision, with its label stated. |
| R-9 | REJECT `integrity`, gate `procedure` or `same_host` | Refused: the setup differed between the two versions. Fix and rerun. |
| R-10 | INCONCLUSIVE, `diagnostics.coarse_resolution` | The timer is too coarse for this benchmark. Longer workload or a count metric. |
| R-11 | INCONCLUSIVE, rule 2 (a gate `not_available` or a guardrail not established) | Undecided: names what could not be checked. Does not mention the interval or the bar. |
| R-12 | INCONCLUSIVE, rule 5, `calibration == "calibrated"` | Undecided: the interval includes the bar. Rounds and minutes that would settle it only if the CLI gives them (T3.7); never computed by hand. |
| R-13 | INCONCLUSIVE, rule 5, `calibration == "uncalibrated"` (every run today) | "perfproof can't keep changes yet." Estimate with interval, labelled uncalibrated, facts, not checked, conditions, and where the change is. No rerun is offered to change the outcome. |

## 8. What the skill reads

Reads: the `--brief` form of each command (planned, T3.15), or until
then only the named `--json` fields: `doctor` warnings and
`machine.{load_average, logical_cores}`; `compare`
`verdict.*`, `diagnostics.*`, `gates[].{gate,status}`, `run_id`,
`report_path`; `profile` top hotspots (`function`, `file`, `line`,
`self_fraction`, `total_fraction`), `basis`, `unprofiled`; the project
model and the hypothesis log for the code at hand; `git status`,
`merge-base`, `diff --stat`, commit messages and PR body; one
function's source, not the file; failing test ids and a capped tail.

Never reads raw: whole logs, profiler or folded-stack files,
`samples.npz`, `record.json`, `report.md` (gives the path), lockfiles,
vendored or generated code, data, weights, `.venv`, `.perfproof/runs`
internals, a full diff. Searches with a semantic code search when one
is installed, `grep` otherwise. Takes the next step in the same turn
when nothing needs an answer; a turn spent on a question is spent on
purpose. Writes a script once, shows it and reuses it, where the CLI has
no command yet.
