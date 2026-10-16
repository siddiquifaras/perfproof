# Method

This page defines how perfproof decides whether a change is faster. It
is the reference the code is built and tested against, and its
acceptance rules are fixed before any calibration runs. Where a part is
not implemented yet, `.claude/plan.md` tracks it.

## The loop

1. **Define the benchmark.** A command, its working directory and
   environment, the metric, a correctness check, the input data, and
   the files that make up the benchmark, its harness and its tests.
   perfproof hashes the files and the spec itself.
2. **Snapshot the baseline.** The baseline is a git commit. perfproof
   checks it out into its own worktree so the baseline and the
   candidate exist side by side and can be run in turns.
3. **Profile the baseline** and hand the agent the top hotspots with
   `file:line`, not a raw profiler dump.
4. **The agent changes the code.**
5. **Compare.** Gates first, then interleaved rounds until the
   stopping rule ends it.
6. **Report.** One JSON record and one readable report per comparison.

## Outcomes and reasons

Every verdict has an outcome and a reason. They are separate fields in
the record, so a tool or an agent never has to parse prose to learn why.

| Situation | Outcome | Reason |
|---|---|---|
| Interval shows the speedup clears the bar | KEEP | `performance` |
| Interval rules out a speedup above the bar | REJECT | `performance` |
| Faster, but a guardrail is shown worse than its allowance or an absolute limit | REJECT | `guardrail` |
| Faster, but a guardrail's compliance can't be established | INCONCLUSIVE | `insufficient_evidence`, naming the guardrail |
| Candidate gives wrong results | REJECT | `correctness`, gate `correctness` |
| Changed code that no check ever ran | REJECT | `correctness`, gate `coverage` |
| The experiment itself is not valid (locked files, procedure, host) | REJECT | `integrity`, with the gate named |
| The benchmark would reach a service that isn't local | Refused before anything runs | `integrity`, gate `side_effects` |
| Interval still straddles the bar when the budget runs out | INCONCLUSIVE | `insufficient_evidence` |

A correctness or integrity REJECT says nothing about speed. Gates run
before any timing, so those verdicts carry no speedup estimate at all.
A performance REJECT says nothing about correctness beyond the gate
having passed. A guardrail REJECT keeps the speedup estimate, because
the point is to show what the speed cost.

## What perfproof promises

perfproof cannot prove a change correct. It promises something narrower
that it can keep: **it never keeps a change that failed a check, and
every report says what was checked and what wasn't.** How much that is
worth depends on the checks, so they are built to be strong:

- the project's own tests, run on the baseline first to find flaky ones;
- differential checks: old and new code on the same inputs, including
  real inputs captured while the benchmark ran;
- a coverage gate: every changed line must have run under those checks;
- guardrails: the speed must not be bought with memory or tail latency;
- a side-effect guard: nothing runs against a service that isn't local.

## Correctness

The `correctness` and `coverage` gates decide whether the candidate
still does what the baseline did.

**Failed executions.** An execution of the benchmark, warmup or
measured, that exits non-zero, is killed by a signal, times out or
can't be started from its tree (a bad interpreter line, a missing or
non-executable program) produces no sample; its time never enters the
statistics. If the
candidate fails this way, the candidate fails the `correctness` gate:
REJECT with reason `correctness`, the exit status and the tail of its
output in the record. If the baseline fails, the experiment itself is
broken: the comparison stops with no verdict and reports the error.
So does a fault in perfproof's own process, such as running out of
file descriptors or processes: it is blamed on neither version.

**Test results.** perfproof judges tests one by one where the runner
reports them: it asks pytest for a JUnit XML report and points other
runners at the same path through `PERFPROOF_JUNIT_XML`. Where no
report is written, the command's exit status is the only result.

**Pre-existing failures.** A test that fails in every baseline run is
a known failure: it is listed in the report and not used to judge the
candidate, and lines only it reaches count as unverified for the
coverage gate. Only a test command that can't run at all on the
baseline (it times out, is killed, or can't start) stops the
comparison as a broken baseline.

**Flaky tests.** Before judging the candidate, perfproof runs the
correctness command on the baseline three times. A test that doesn't
give the same result every time is flaky: it is quarantined, listed in
the report, and not used to judge the candidate. Three runs catch a
test that fails half the time only 75% of the time, so a test that
fails on the candidate after passing every baseline run is run five
more times on each tree before the candidate is blamed. If it fails on
the baseline in any of those runs, it is flaky and quarantined; if it
fails only on the candidate, the candidate fails the gate. Quarantine never
weakens the coverage gate, because lines only the quarantined tests
ran count as unverified.

**Differential checks.** Tests check what their authors thought of.
Differential checks compare the two versions directly:

- Declared outputs of the benchmark run are compared between the trees,
  as bytes, text, or numbers within `rtol` and `atol`.
- While the baseline benchmark runs, perfproof captures up to a fixed
  number of real calls to each function the candidate changed: the
  arguments, and the return value. Each captured call is replayed
  against the candidate in a separate process, and the results must
  match. Calls whose arguments can't be captured faithfully (open
  files, sockets, live objects) are skipped and counted in the report.
- Where type hints allow, generated inputs (Hypothesis) are run through
  both versions as well.
- For model outputs: fixed-seed greedy decoding must give the same
  tokens, or logprobs must agree within a declared tolerance.

Any mismatch fails the `correctness` gate, with the first differences
in the record.

**Coverage gate.** perfproof takes the lines the candidate changed from
`git diff` and runs the correctness command and the differential checks
under coverage. Every changed executable line must have run. A change
in code no check executed is unverified, and an unverified change is
not kept. For a target where perfproof cannot measure coverage yet, a
KEEP needs the spec's explicit opt-in, and the verdict is labelled
"coverage not checked".

## Side effects

A benchmark that writes to a real database or calls a paid API is not
safe to run blindly.

**The benchmark's environment.** Every command perfproof runs for a
comparison (benchmark, correctness check, reset) gets a small base
environment, not perfproof's own: `PATH`, `HOME`, `USER`, `LOGNAME`,
`SHELL`, `TMPDIR`, `TERM`, `TZ`, `LANG` and the `LC_*` variables, plus
the spec's own `env`. Anything else the benchmark needs, such as
`CUDA_VISIBLE_DEVICES` or a database URL, is declared in the spec. So
the agent's own credentials and service addresses (an API base URL, a
cloud token) never reach the benchmark, and both versions run with
exactly the declared environment.

**The check.** Before anything executes, perfproof checks that
environment, the commands' arguments and the declared config files for
service addresses, such as `DATABASE_URL`, `REDIS_URL`, and variables
ending in `_HOST` or `_URL`. If any points at a host that isn't local
(`localhost`, `127.0.0.1` and the rest of `127/8`, `::1`, `0.0.0.0` and
`::`, a Unix socket, or a container started for the run), the run is
refused with gate `side_effects`, unless the spec opts in. Nothing is
resolved through DNS, and anything that can't be parsed is refused.
A spec can declare a reset command that perfproof runs before every
execution, so a database starts each round in the same state. A reset
that fails fails the `side_effects` gate, with its output recorded.

## Integrity

The comparison is only meaningful if both versions went through the
same experiment. Every property of the run falls in one of three
classes.

**Must match, or REJECT with reason `integrity`:**

| Gate | What is compared |
|---|---|
| `lock` | Hashes of the benchmark harness, the correctness oracle (tests and expected outputs), declared input data, and declared config files |
| `procedure` | Hash of the spec (command, working directory relative to the tree, declared environment variables, metric, warmup, budget, look schedule, inputs) and the perfproof version; and for every round, the argv, declared environment, working directory and resolved executable that perfproof actually launched |
| `runtime` | The runtime from outside the tree that each arm actually runs: the interpreter and its version, followed through launchers such as `uv run`, `env` and shebangs; for a native executable built in the tree, the libraries it links from outside the tree, with their hashes; a binary committed in the tree that differs between arms. Anything that can't be identified fails. Waivable only when the spec allows runtime changes. An untracked executable inside the tree is treated as a build of the code under test |
| `same_host` | Host identity in the machine fingerprint of every sample. With the cross-machine opt-in, the session design under "Comparisons across machines" applies instead. |

**Recorded, not gated:** the environment each command ran with (names, and
values for variables known to affect performance such as
`OMP_NUM_THREADS` and `PYTHONHASHSEED`), each tree's dependency
lockfile hashes, OS and kernel, CPU frequency policy, power and thermal
state. Differences between the arms appear in the report as warnings.

**Allowed only by explicit opt-in in the spec, and labelled in the
verdict:** dependency or runtime changes (when upgrading a library is
the optimization), and comparisons across hosts.

Rules that follow:

- perfproof launches every measured process itself with an argv list,
  so the recorded command is the process that ran.
- The spec is frozen once defined. Changing anything in it creates a new
  spec, which needs a new baseline.
- The baseline is measured again in every comparison. perfproof never
  compares a candidate against stored baseline numbers.
- With the `reported` metric the number comes from the benchmark's own
  process, which the candidate could influence. So a `reported` metric
  is allowed only when the code that times and prints the value is in
  locked files, and perfproof records wall time alongside it and
  computes the wall-clock interval too. The two measure different
  things (wall time includes startup), so only direction is compared:
  if one interval shows the candidate faster and the other excludes
  that, the record carries a `metric_disagreement` diagnostic and the
  report leads with it. The verdict always comes from the spec's
  metric. perfproof never switches to whichever metric looks better.
- Out of scope for v1: candidates that attack perfproof itself or the
  operating system. perfproof assumes the candidate changes only its
  own source tree.

## Observations

**One round is one observation.** A round runs the baseline once and
the candidate once, back to back, in an order drawn at random for that
round. Its paired ratio is the observation, and `n` is the number of
rounds.

- Each execution produces one value. If the benchmark loops
  internally, the value is the single number it reports for that
  execution. Inner iterations share process state, so they are not
  independent observations.
- Warmup executions run first for each version and are discarded.
- The ratio is oriented so that above 1 means the candidate is faster:
  `r = baseline / candidate` when lower is better (time, counts),
  `r = candidate / baseline` when higher is better (throughput).

**Metrics.** One per benchmark:

- `wall`: wall-clock time of the whole command.
- `cpu`: user plus system CPU time of the command, from `wait4`.
- `reported`: the benchmark prints `PERFPROOF {"value": <number>}` on
  stdout. Any language can do this, and it excludes process startup.
- `counter`: hardware instruction or cycle counts from `perf stat`
  (Linux). Far less noisy than time, so small changes need fewer rounds.

### Assumptions

The procedure below assumes the log ratios `d_i = log(r_i)` of
different rounds are independent draws from one distribution.

- **Within a round**, the two runs share conditions such as heat and
  background load. That dependence is wanted: it is what makes the
  ratio cancel slow drift.
- **Between rounds**, independence can fail: thermal state, caches and
  background jobs persist across rounds, and noise can change level
  halfway through. Pairing does not fix this.
- **Order inside a round** can matter, for example when the second run
  inherits a warm cache. Random order turns that bias into noise.

Each of these violations is a calibration scenario. The record reports
the lag-1 autocorrelation of `d` and warns when it is above 0.3. If the
primary procedure fails the acceptance rule under the autocorrelated
scenarios, the prespecified fallback is batch means: consecutive rounds
are grouped in blocks of 5 and each block mean becomes one observation.

### Comparisons across machines

The default, and the comparison perfproof's claims rest on, runs both
versions on one machine. Pairing rounds cancels what the two versions
share. It does nothing about a difference between machines: if the
baseline always runs on a machine that is 10% faster, no amount of
pairing removes that.

So in v1:

- **One fixed machine per version is refused.** That comparison fails
  the `same_host` gate.
- **Cross-machine comparisons need an explicit opt-in** and use one
  design only. Sessions alternate (A, B, A, B...), and every session
  gets a host allocated fresh from a pool, for example a new Modal
  container, so perfproof never picks which host a version gets. Each
  session is reduced to the mean log value of its rounds, and one
  observation is a consecutive A and B session pair. Host offsets then
  vary from pair to pair and become noise in the interval instead of a
  bias. Only completed pairs count: a baseline session and the
  candidate session launched right after it, both finished. A failed
  or incomplete session voids its pair, and sessions are never
  re-paired across the gap. A host that was never allocated is not a
  session.
- **Below `n0` completed pairs, speed never decides.** While budget
  remains and another pair can be collected, the comparison continues.
  When no more pairs can be collected, the verdict is INCONCLUSIVE. A
  failed gate still decides by rule 1 at any pair count.

  | Situation | Action |
  |---|---|
  | A gate failed | REJECT, naming the gate |
  | Fewer than `n0` pairs, and another pair can be collected | Continue |
  | Fewer than `n0` pairs, and no more can be collected | INCONCLUSIVE |
  | `n0` pairs or more | Rules 2 to 5 of the decision rule |
- **The design rests on an assumption perfproof can't check:** that the
  pool allocates hosts independently of which version runs. The verdict
  is labelled cross-host and states that assumption, the record keeps
  every session's fingerprint, and the report warns when host types
  differ between the arms (GPU model, driver, CPU).
- Calibration of this design simulates random per-session offsets. It
  cannot cover a pool that hands one version systematically different
  hosts, and the report says so.
- Drift and order effects inside a session cancel in the session-pair
  design, and drift across a whole campaign biases a pair by about
  `log(1 + D) / (2n - 1)` for total drift `D` over `n` pairs, because the
  candidate session always follows the baseline one. Calibration does
  not cover those two effects across hosts, and that is a stated limit
  of cross-machine verdicts.

## Speedup and interval

**Estimand.** The speedup is the geometric-mean ratio
`S = exp(E[d])`. It is the standard summary for multiplicative
performance changes, and it is what the primary procedure targets.

**Primary procedure, fixed in advance.** At a look with `n` rounds, the
interval for `E[d]` is the Student t interval on the mean of `d`, at
the per-look confidence below, exponentiated to give `[L, U]` for `S`.

**Challengers.** The Hodges-Lehmann estimate with the Wilcoxon
signed-rank interval (targets the pseudo-median of `d`), and the
bootstrap of the ratio of medians (unpaired; it is what the hand-run
vLLM study used). They run in calibration for comparison. A challenger
replaces the primary only if it passes every acceptance rule on the
development scenarios and the switch, with its change of estimand, is
written into this page before the audit runs. The audit then judges
whichever procedure is primary at that point.

## Stopping

The look schedule is fixed before the first round:

- The first look comes after `n0` rounds, then one every `k` rounds,
  for at most `K` looks, and never past `max_rounds` or `max_seconds`.
  `n0` is at least 10: t intervals on fewer rounds are too fragile
  under heavy tails.
- If the budget runs out between looks, perfproof takes a final look at
  the rounds it has. That look replaces the remaining scheduled looks,
  so the total never exceeds `K`.
- Each look computes a two-sided interval at confidence `1 - alpha / K`
  with `alpha = 0.05` (a Bonferroni split).
- The comparison stops at the first look where rule 2 or 3 below
  fires.

**Why this controls false KEEPs.** Suppose the true speedup is at or
below the bar. A false KEEP needs some look's lower bound to exceed the
bar, which means that look's interval missed `S` from above. Each look
does that with probability at most `alpha / (2K)` if its interval is
valid at its `n`. Over at most `K` looks the union bound gives a
false-KEEP probability of at most `alpha / 2 = 2.5%`, however the looks
depend on each other. The same argument bounds false REJECTs at 2.5%
when the true speedup is above the bar.

That argument leans on one assumption: each look's interval is valid
at its own `n`, including when `n` is set by a time budget. Calibration
tests it under the complete stopping procedure, not at fixed `n` alone.

## Decision rule

| Name | Default | Meaning |
|---|---|---|
| `min_effect` | `0.03` | Smallest speedup worth keeping |
| `alpha` | `0.05` | Total error budget, split across looks |
| `n0`, `k`, `K` | per target | Look schedule |
| `max_rounds`, `max_seconds` | per target | Run budget |

The first rule that matches decides:

1. **REJECT** (`correctness` or `integrity`) if a gate failed. When
   several gates fail, the first in this order is named:
   `side_effects`, `lock`, `procedure`, `runtime`, `same_host`,
   `correctness`, `coverage`. Integrity comes first because a
   correctness check run under a broken lock or procedure tests
   nothing, and coverage comes last because it asks whether passing
   checks reached the change.
2. If `L > 1 + min_effect`, the guardrails decide, as set out under
   "Guardrails": **KEEP** (`performance`) when every guardrail passed or
   is not applicable, **REJECT** (`guardrail`) when any is breached, and
   otherwise continue while budget remains, then **INCONCLUSIVE**
   (`insufficient_evidence`).
3. **REJECT** (`performance`) if `U <= 1 + min_effect`. If `U < 1`, the
   record also flags a regression.
4. **Continue** if budget remains.
5. **INCONCLUSIVE** (`insufficient_evidence`): the interval still
   straddles the bar when the budget runs out.

**Gate statuses.** Every gate ends `passed`, `failed`, `waived` or
`not_available`. Only a gate with its own opt-in can be `waived`, and
the opt-in is recorded. `not_available` means the gate couldn't be
checked here, for example coverage for a language perfproof can't
instrument. It blocks KEEP. When rule 2 matches with any gate
`not_available`, the result is **INCONCLUSIVE**
(`insufficient_evidence`) at that look, naming the gate, because more
rounds can't make it available. Rules 1 and 3 still give REJECT, and
so does a guardrail breach under rule 2, because a REJECT keeps
nothing.

**Before calibration.** Until the procedure has passed calibration
(see "Calibration"), rules 2 and 3 never match. A comparison runs to
the end of its budget and ends **INCONCLUSIVE**
(`insufficient_evidence`), with the diagnostic
`procedure_uncalibrated` recorded. Rule 1 still gives REJECT when a
gate fails. Every record states whether it was made by a calibrated
procedure, and that field has no default.

With the default bar of +3%:

| Speedup | Interval | Rule | Result |
|---|---|---|---|
| +8% | +5% to +11% | 2 | KEEP |
| +4% | +3.5% to +4.5% | 2 | KEEP |
| +5% | +2% to +8% | 4, then 5 at the end of the budget | INCONCLUSIVE |
| +10% | -2% to +21% | 4, then 5 at the end of the budget | INCONCLUSIVE |
| +1% | -1% to +2% | 3 | REJECT, performance |
| -6% | -9% to -3% | 3, regression flagged | REJECT, performance |
| not measured | not computed | 1 | REJECT, correctness |

A promising point estimate never turns into a KEEP. If the budget ends
with the interval straddling the bar, the answer is INCONCLUSIVE
however good the estimate looks.

### Guardrails

The **primary objective** is the one metric the goal is about, set per
workload in the project model, such as p99 latency for a web workload
or throughput for a serving one. It decides whether a change is faster.
**Guardrails** are the other metrics that must not get worse while it
does. For a given workload a metric is the primary or a guardrail,
never both.

**Applicability.** Which guardrails apply depends on the target class.
The project can override that per workload, and the reason is recorded.

| Guardrail | Applies by default to | Provisional allowance |
|---|---|---|
| Peak memory: max RSS of the whole process tree | every target class | 10% |
| Peak GPU memory | LLM serving, GPU kernels and PyTorch | 10% |
| p99 latency, when it isn't the primary | web services, LLM serving, agentic apps | 5% |

Any other guardrail, such as throughput when latency is the primary,
has no default: the project declares it with its allowance. For each
workload, every guardrail in the table is either applied or marked
`not_applicable` with a reason. Nothing is left implicit.

**The defaults are not permission to regress.** 10% and 5% are the
refusal thresholds used until the project sets its own. They are not a
budget to spend. Every guardrail's measured change is reported, and a
change that costs memory or latency within its allowance is flagged
with how much, so the developer sees the trade. The record keeps which
allowances applied and where each came from: the default, the target
class, or the project.

**Absolute limits.** A project can also set absolute limits, such as a
memory ceiling (a container limit, the GPU's capacity) or a latency
SLO. A limit is checked on the candidate's own measurements, not on a
ratio: compliance is shown when the upper bound of the candidate's
metric is within the limit, a breach when its lower bound is beyond
it. A change can sit within its relative allowance and still breach an
absolute limit.

**Status.** Every guardrail ends a comparison in exactly one state:

| Status | Meaning |
|---|---|
| `passed` | Shown within its allowance and every limit: the upper bound of its worsening ratio is at most `1 + allowance`, and the candidate's upper bound is within each limit |
| `breached` | Shown worse: the lower bound of its worsening ratio exceeds `1 + allowance`, or the candidate's lower bound exceeds a limit. One check showing a breach is enough, even if another check's interval is missing |
| `not_established` | Measured, but the data can't tell yet; at least one of its intervals was computed |
| `unmeasurable` | Applicable, but perfproof could not measure it here; the reason is recorded |
| `not_applicable` | Marked not applicable for this workload; the reason is recorded |

**Decision.** Guardrails matter only when the primary interval would
give KEEP:

- every guardrail `passed` or `not_applicable`: **KEEP**;
- any guardrail `breached`: **REJECT** with reason `guardrail`, keeping
  the primary interval and every guardrail interval that was measured,
  the breaching one always among them;
- otherwise, some guardrail `not_established` or `unmeasurable`:
  continue while budget remains, then **INCONCLUSIVE**
  (`insufficient_evidence`), naming the guardrails that couldn't be
  established. An applicable metric that can't be measured never
  passes silently.

A performance REJECT or INCONCLUSIVE stands as it is, with the
guardrails reported alongside. Requiring compliance to be shown, not
merely not disproved, means a noisy guardrail such as p99 can hold a
change at INCONCLUSIVE; the evidence planning report names it as the
blocker.

**Error control.** Let `G` count every allowance and limit checked. Each
guardrail check at a look uses confidence `1 - alpha / (K G)`. By the
union bound under "Stopping", the chance of a KEEP while some
guardrail is truly beyond its allowance or limit is at most 2.5%, and
the chance that a guardrail shows a breach when all are truly within
is at most 2.5%. The primary interval and its false-KEEP bound are
unchanged, because guardrails can only turn a KEEP into a REJECT or an
INCONCLUSIVE. Calibration measures guardrail false passes and false
breaches per cell under the acceptance rule.

### Numerics

The decision and the interval have separate jobs, and floating-point
error is handled in the interval, never in the decision.

- **The decision** compares the exact float64 bounds that are recorded
  with `bar = 1 + min_effect`, computed in float64 from the stored
  `min_effect`. A typed `0.118` is the float64 nearest it; the report
  prints the stored value so nobody has to guess. KEEP is strict
  (`L > bar`), REJECT is inclusive (`U <= bar`), and no tolerance,
  epsilon, display rounding or log-scale comparison is applied.
- **The interval** must never come out more favourable to the
  candidate than its exact value because of rounding. Measured on
  macOS arm64: 48 rounds all exactly at the bar, with
  `min_effect = 0.4730308035801494`, give a mean log ratio one ulp
  high, a standard deviation of 5.6e-17 instead of 0, and
  `L = U = bar + 1 ulp`, so a KEEP for a change that sits on the bar.
  The mirror case gives false REJECTs. Two measures fix this:
  - When every ratio is the same float, the interval is that ratio
    exactly, with no arithmetic that can move it.
  - Otherwise, each bound is widened outward by an error bound derived
    from the operations that produced it. That is conservative
    interval construction, not rounding: the width comes from an
    analysis of the computation, never from observing which nudge
    makes the tests pass, and a fixed one-ulp step is not a derived
    bound.
- **Rounding to a display precision** (1.0300000000000002 shown as
  1.03) happens only in the rendered report, never before a decision.

## Calibration

Calibration checks that the procedure keeps its promises. Its pass
rule is fixed here, before any run, and is not changed after seeing
results.

### What counts as an error

The bar splits true speedups in two. Whether an outcome is an error
depends only on which side of the bar the true speedup `S` is on:

| True speedup | KEEP | REJECT | INCONCLUSIVE |
|---|---|---|---|
| At or below the bar, `S <= 1 + min_effect` | false KEEP | correct | not an error, unresolved |
| Above the bar, `S > 1 + min_effect` | correct | false REJECT | not an error, lost power |

- Exactly at the bar, KEEP's claim (`S > 1 + min_effect`) is false, so
  a KEEP there is a false KEEP and a REJECT is correct. This matches the
  strict `>` in rule 2.
- Below the bar, REJECT is correct however real the speedup is. The
  1.5% decoy sits below the bar, so rejecting it is a right answer.
- perfproof makes no claim to tell apart speedups just either side of
  the bar. Close to it, INCONCLUSIVE is the expected answer, and its
  rate is reported.
- Correctness and integrity REJECTs are not part of these rates.
  Calibration exercises the statistics; the gates have their own tests.

### Acceptance rule

| Error | True speedups tested | Limit |
|---|---|---|
| False KEEP | 0.95, 1.00, 1.03 | 2.5% |
| False REJECT | 1.04, 1.05, 1.10, 1.20 | 2.5% |

The argument under "Stopping" bounds both errors at 2.5% for every
true speedup on its side of the bar. The cells nearest the bar, 1.03
for false KEEP and 1.04 for false REJECT, are where a broken procedure
shows first.

- Each cell (one noise scenario, one host mode, one true speedup) runs
  the complete procedure, looks and budget included, `R = 4000` times.
- **The controlled rates are unconditional.** A cell's false-KEEP rate
  is its number of KEEPs divided by all `R` runs, INCONCLUSIVEs
  included, and the same for false REJECT. That is the probability the
  argument under "Stopping" bounds, and the only one the acceptance
  rule checks. The error rate among runs that reached a verdict,
  KEEPs divided by KEEPs plus REJECTs, is not bounded by that argument;
  it is reported per cell as a diagnostic, never used to pass a cell.
- Any stopping rule added later, such as stopping early with
  INCONCLUSIVE, is calibrated as part of the procedure it changes. An
  early stop that only replaces "continue" at a scheduled look, with
  the looks and their confidence unchanged, cannot raise the
  unconditional rates, because every KEEP or REJECT it gives is one
  the original procedure gives on the same measurements. It can lower
  the share of runs that reach a verdict and change the conditional
  rate, so calibration measures both rather than assuming either.
- A cell passes only if the one-sided 95% Clopper-Pearson upper bound
  on its observed rate is at most the limit. An observed 2.4% fails;
  the evidence has to show the rate is below the limit, not near it.
  At `R = 4000` the largest passing count is 83 (2.075%) and 84
  (2.1%) fails, checked both as the beta quantile and from the
  definition `P(X <= k | p = 0.025) <= 0.05`. A procedure
  whose true rate is 1.5% passes a cell with probability 0.998; one at
  2.0% passes with probability 0.66. The Bonferroni split should keep
  true rates well under 2.5%, so a sound procedure has room to spare.
- Every cell must pass. A failing cell can't be dropped, rerun at a
  larger `R`, or rerun at another seed. The fix is a change to the
  procedure, after which the whole calibration runs again at a new
  seed.
- Every cell reports its false-KEEP or false-REJECT count, rate and
  bound on its own. There is no pooled pass rate; one failing cell
  fails the calibration.
- Reported for every cell, but not pass or fail: KEEP rate (power) at
  `S` = 1.05 and 1.10, the INCONCLUSIVE rate, coverage at fixed `n`,
  and rounds used (mean and 95th percentile).
- Passing supports the procedure under the conditions tested, not in
  general. Every report names the conditions its target class was
  calibrated under.

### Development scenarios

Used while building the procedure. Each runs on one host (offsets
shared within a round) and cross host (random per-session offsets,
alternating session pairs, as under "Comparisons across machines").

| Scenario | What it imitates |
|---|---|
| Log-normal, CV 1%, 5%, 15% | Ordinary run-to-run jitter |
| Student-t, 3 degrees of freedom | Heavy tails |
| Bimodal, 10% of runs 30% slower | GC pauses, JIT tiers, cache misses |
| Rare 3x outliers, 1 in 50 | Background jobs |
| Linear drift of 10% across the session | Thermal throttling |
| Variance step, CV doubles halfway | A noisy neighbour arriving mid-session |
| AR(1), rho 0.5 | Rounds that resemble their neighbours |
| Order effect, second run of a round 3% faster | Warm caches handed from one run to the next |
| Per-session offset of 15% | Different hosts or sessions, as in the vLLM study |

### Audit scenarios

Held out from development, so the procedure can't be tuned to them.
The audit checks two different things, and both are required:

- **Fresh seeds** on the development scenarios, to show the result
  wasn't luck of one seed.
- **New scenarios**, configurations nobody built the procedure
  against, to show it wasn't tuned to the development set. The
  `stats-auditor` draws them at audit time from these ranges, combining
  several violations in one scenario:

| Parameter | Range |
|---|---|
| CV | 0.5% to 20% |
| Student-t degrees of freedom | 2.5 to 10 |
| Outlier rate and size | 0 to 5%, 2x to 5x |
| Drift across the session | 0 to 20% |
| Variance step | 1x to 4x, at a random point |
| AR(1) rho | 0 to 0.7 |
| Order effect | 0 to 5% |

- The audit draws at least 20 new scenarios. That number measures
  breadth of conditions; the `R` runs inside each scenario measure its
  error rates. Results are reported per scenario, never pooled.
- The auditor applies the acceptance rule itself, from the raw outcome
  counts, rather than trusting the test suite's assertions.
- Audit parameters and seeds are written into this page only together
  with the audit's result, and the implementation never reads them.
  Once an audit has run, its scenarios are exposed: after a failure
  they join the development scenarios, and the next audit draws new
  ones. Fixing the procedure against a failed audit's scenarios and
  then re-auditing on those same scenarios does not count.

### Real noise

Synthetic scenarios can be wrong in ways that flatter the procedure.
For each target class, perfproof records long A/A traces (the same
commit run against itself) on two machines. Traces from the first
machine are used in development: to check the synthetic scenarios'
CV, tails and autocorrelation against reality, and to calibrate
directly by block-bootstrapping the traces with a known speedup
injected. Traces from the second machine are held out for the audit.
The same acceptance rule applies. Every A/A comparison must end REJECT
or INCONCLUSIVE, never KEEP.

| Target class | Development traces | Audit traces | Status |
|---|---|---|---|
| Python, CPU-bound | this MacBook | Linux CI runner | planned in M2 |
| Native code | this MacBook | Linux CI runner | planned in M4 |
| GPU kernels | one paid GPU session | a second paid session | planned in M5 |
| LLM serving throughput | one paid GPU session | a second paid session | planned in M5 |

Until a target class passes on its held-out traces, its verdicts carry
"not calibrated for this target class" in the report.

Calibration results are written to this page with the seed, `R`, the
observed rates, and their Clopper-Pearson bounds.

## What the record contains

- Outcome, reason, the gate or rule that decided it, speedup estimate,
  interval, rounds, looks, and whether the comparison crossed hosts
- Procedure, look schedule, and calibration status for the target class
- Spec hash, perfproof version, and the command executed in each round
- Noise of each version and of the paired ratio, and lag-1
  autocorrelation of the ratio
- Gates and their results, including recorded differences between arms
- Machine fingerprint: CPU, cores, frequency policy, load, power source,
  thermal state, GPU, driver and clocks when present
- Warnings, for example running on battery or a busy machine
- Paths to the raw samples and any profiles
