# /perfproof messages

Every message the user sees, by ID, for [skill.md](skill.md). The skill
fills the `{placeholders}` (named after the `--json` field they come
from) and adds nothing; T3.8 snapshot-tests each. Plain words, no codes:
a placeholder never holds a raw error code. Every session ends with its
`Result:` line (skill.md, section 2), always last.

Words rule: messages under R-10 to R-13 and any estimate never contain
a direction word (skill.md, rule 1).

## Inputs

**IN-1, bare run**

```text
I looked at the repository. Nothing below is confirmed yet.
  Kind of project: {target} ({evidence})
  Ways to run it: {workload_1} ({source_1}); {workload_2} ({source_2})
  How I would check a change: {oracle} ({evidence})
Three questions:
1. Which of these is how it is really used?
2. What should improve: {metrics_found}, or something else?
3. What must not change (public API, output, dependencies, files)?
```

With no workload found, line 2 reads "Ways to run it: none found" and
BS-5 speaks. The goal stays "unknown" if the repository doesn't say.

**IN-2a, free text, the named thing is the largest share**

```text
I read this as: {interpretation}. In one run of {workload} (machine
load {load}), {named} took {share} of the time, the most of anything
measured. That is a measured share, not yet a change. Next I will look
at what it spends that time on.
```

**IN-2b, it is not**

```text
In one run of {workload} (machine load {load}), {named} took
{named_share}. {other} took {other_share}, the most of anything
measured. Look at {named} anyway, or at {other}?
```

**IN-2c, the text names branches or PRs**

```text
You mention {refs}. Do you want me to check those changes, or look for
something new to speed up?
```

**IN-3a, the cost is elsewhere**

```text
{target} accounts for {share} of {workload}, but the time is spent in
{callee_or_file}, which it calls. I will change {other_file} instead of
{target}. Tell me now if you want only {target} touched.
```

**IN-3b, the named code is almost free**

```text
{target} is {share} of one {workload} run. Even if it cost nothing,
the whole run's time could drop by at most a factor of {cap} (a bound,
not a result; {basis}). {reason}
Pick a different workload, or stop here?
```

When `{cap}` is at or below the bar, the skill does not offer to try it.

**IN-5, depth** (shown for `light` and `medium` with the last sentence)

```text
Depth {word}: up to {hypotheses} ideas, {attempts} tries, at most
{rounds} timed rounds per comparison. A lighter depth stops sooner, so
more results end undecided.
```

## Find path

**FP-1, estimate above its bound**

```text
The estimate ({estimate}x) is higher than the bound I worked out
({cap}x, from {where}). One of the two is off for this workload; I
haven't found which.
```

**FP-2, hidden state**

```text
This change makes {state} differ in {n} places after {calls} calls,
while {output} is identical. Is {state} part of what must stay the
same?
```

**CF-5, a cheap test refutes the idea**

```text
I tried {idea} on a small test first. {fact_or_bound} So I won't build
it. Next: {next_idea}.
```

## Prove path

**PF-2**

```text
{candidate} is {n} commit(s) on top of {base_sha}: {files} files,
{adds} lines added, {dels} removed. {extra}
I will compare it with {base_sha}.
```

`{extra}` is empty, or "It also contains {k} commit(s) by someone else:
{shas}. Those would be measured together with the change. Prove them
together, or only the part you wrote?"

**PF-3**

```text
{candidate} does not have the benchmark files, so perfproof can't
compare it as it is. I put your {n} commit(s) unchanged on top of the
benchmark branch as {derived} ({derived_sha}); your own commit stays
{sha}. Both are named in the report.
```

**PF-8, report, one per candidate**

```text
{candidate} ({sha}{derived}): {claim_line}
  Facts:       {facts}
  Measured:    {result_sentence}      (from R-1 to R-13)
  Not checked: {not_checked}
  Conditions:  {platform}, machine load {load}, {rounds} rounds, {spread}
  Run: {run_id}   Time to this result: {minutes} min
Result: {OUTCOME} ({reason})
```

`{claim_line}` is the claim as the author made it, with "not checked"
beside any claim perfproof did not test. Memory and tail latency appear
under Facts when judged, otherwise under Not checked.

## Bad states before a run

**BS-1**

```text
This folder is not a git repository with at least one commit, and
perfproof compares versions of code through git. Your files were not
touched. Run `git init` and make a first commit, then ask again?
Result: NO VERDICT (not a git repository)
```

**BS-2**

```text
You have changes that are not committed ({n} files, for example
{path_1}, {path_2}). I won't touch them, and I can't start from a
working tree that differs from the last commit. Commit them or set them
aside and ask again, or name a branch to check (that works as it is).
Result: NO VERDICT (uncommitted changes)
```

**BS-3**

```text
I couldn't reach {service} at {address} from here, so I couldn't run
{workload}. That doesn't tell me whether {service} is running; my
connection may be what is blocked. Nothing was measured or changed.
Start a local {service} in Docker on a free port, or tell me the
address to use?
Result: NO VERDICT (service not reachable)
```

**BS-4a, not local**

```text
This benchmark would reach {host}, which is not this machine. I won't
run it against a service that could be real. Your data was not touched.
Start a local {service} on port {free_port} with sample data?
Result: NO VERDICT (benchmark would reach {host})
```

**BS-4b, local but not this project's**

```text
Something already listens on {address}: {what}. It may not be this
project's, and the tests would create and drop databases in it. I won't
use it. Start a separate one on port {free_port}?
Result: NO VERDICT (port {port} belongs to something else)
```

**BS-4c, a writing workload**

```text
This workload writes data, and perfproof can't yet reset it between
rounds, so the two versions would not start from the same state. Measure
a read-only version instead?
Result: NO VERDICT (workload writes data)
```

**BS-5a, nothing can be run**

```text
I couldn't find a way to run {thing}: {what_is_missing}. Without a run I
can't say anything about its speed. Nothing was changed.
Result: NO VERDICT (no workload)
```

**BS-5b, a workload to confirm**

```text
There is no confirmed benchmark yet. I can use: {one_line} (it runs
{command} and times {what}). {script_note} It would live in
{harness_path} on a new branch {branch}, which I'd create for you.
perfproof can't lock {outside_inputs}; if they change between runs,
nothing will notice. Is this how it is really used?
```

Nothing is measured until the user says yes.

**BS-6a, no tests**

```text
{project} has no tests, so perfproof has nothing to check a change
against. I can check that its output on {fixed_input} stays
{identical_or_within}, and that the output is not empty. A change that
makes the output differ will be refused, whatever its speed. Is that
the right bar?
Result: NO VERDICT (no way to check a change)
```

**BS-6b, too slow**

```text
The full tests take about {t} here, and perfproof runs them {k} times
per comparison. I'd use {subset} ({n} tests, about {t_subset}) for each
comparison and run {rest} only at the end. Not run until then: {rest}.
OK?
```

**BS-7**

```text
The original code ({baseline_sha}) would not {build_or_run}: {command}
failed. Last lines: {tail}. Full log: {log_path}. I haven't looked at
your change, because I have nothing to compare it with. The project's
README says to run `{setup}` first. Run it?
Result: NO VERDICT (original code will not run)
```

**BS-8a, output differs between two runs of the original**

```text
Run twice on the same code, this benchmark gives different output
({n} lines differ, for example {example}). Until that is fixed every
change would be refused for it. I'll {fix} and check again.
```

**BS-8b, the run did not do the work**

```text
This run did {observed} (for example {count}), not the work you asked
about. I'll change {what} and check again before I lock it.
```

**BS-8c, a small timed part**

```text
Only {part} of each {total} run is the part being timed ({share}); the
rest is {other}. Each round is slow and the measurement coarse. Shorten
{what}?
```

**BS-10**, one sentence per warning code, then the advice

| Code | Sentence |
|---|---|
| `high_load` | The machine is busy: load {load} on {cores} cores ({top_processes}). |
| `on_battery` | The machine is running on battery. |
| `low_power_mode` | Low Power Mode is on. |
| `thermal_limit` | The machine is slowing itself down to cool. |
| `non_performance_governor` | The CPU is not set to run at full speed. |

```text
{sentences} Timings taken now vary, so a comparison would likely end
undecided and take longer. {advice} I'll keep doing the parts that need
no timing. Fix it and I'll re-check, or go on anyway?
```

`{advice}` is the `advice` field of the warning. Unknown code: the
warning's `message`, unchanged.

**BS-11a, start**

```text
Started the comparison ({run_id}). {length_sentence} I'll tell you when
each look finishes. You can leave; ask again to pick it up.
```

**BS-11b, cut off**

```text
The comparison {run_id} stopped after {rounds} rounds ({state_plain}).
There is no result, and none will be reported from a partial run.
Resume it?
Result: NO VERDICT (run not finished)
```

`{state_plain}` is "it was interrupted", "it was cancelled" or "it is
still running". `{length_sentence}` is the CLI's rounds and minutes
estimate when it gave one, otherwise "I can't say how long it will
take."

**BS-12**, by what failed

```text
perfproof stopped before measuring: {plain}. Nothing was kept and your
code is unchanged. Details: {run_dir}. {next}
Result: NO VERDICT (perfproof stopped)
```

`{plain}` comes from this table; an unknown code gets "something I don't
recognise went wrong".

| Failure | `{plain}` and `{next}` |
|---|---|
| a command not built, or an option unknown to this version (`not_built`, `usage`) | "this version of perfproof can't {step} yet"; next: the step done another way, or stop |
| `ambiguous_hash` | "two saved versions of this benchmark match"; next: "I'll use the newest ({hash})" |
| `spec_conflict` | "this benchmark's definition changed after it was frozen"; next: "I'll save it under a new name" |
| `undeclared_guardrails` | "a limit this kind of project needs was not declared"; next: show the one to declare |
| `unsupported_spec`, `invalid_spec`, `harness_fault`, `internal_*` | "the benchmark definition or perfproof itself failed"; next: stop |

## Results after a run

All results end with the `Result:` line. Ratios are old time divided by
new time.

**R-1, kept (calibrated)**

```text
Kept: {candidate} is faster on {workload}: {estimate}x, interval {lo}x
to {hi}x, above the {bar}x bar, {rounds} rounds. Facts: {facts}.
Memory and other limits: {guardrails_line}. Committed on {branch} as
{sha}. Not checked: {not_checked}.
Result: KEEP (performance)
```

**R-2, not kept, performance (calibrated)**

```text
Not kept: the interval {lo}x to {hi}x is at or below the {bar}x bar
({rounds} rounds). {slower_clause} Your code is back as it was.
Result: REJECT (performance)
```

`{slower_clause}` is "It is slower than the original." only when
`verdict.regression`, otherwise empty.

**R-3, not kept, a limit**

```text
Not kept: it uses more {guardrail} ({value} against the limit
{limit}), whatever its speed. Your code is back as it was.
Result: REJECT (guardrail)
```

**R-4, a check failed**

```text
Your change broke {n} check(s): {ids}. {fact} Nothing about speed was
measured, and your code is back as it was.
{pin_next}
Result: REJECT (correctness, gate correctness)
```

`{fact}` for a pin: "The project's pinned query count for {test} is
{old}; the change makes it {new}." For output: "The output {file}
differs from the original's in {n} places." For a crash: "The benchmark
crashed on your change: {tail}." `{pin_next}`: "Update {pin_file} the
project's way ({command}) and measure the speed, or drop this change?"
or "Drop this change, or tell me what difference is allowed?"

**R-5, lines never ran**

```text
The changed lines {file}:{a}-{b} never ran under any check, so
perfproof can't tell whether they broke anything. Your code is back as
it was. Add a test that reaches them (it must fail on the original), or
skip this change?
Result: REJECT (correctness, gate coverage)
```

When coverage is `not_available` the result is not R-5 but R-11; the
reason line reads "perfproof can't see which {language} lines ran, so it
can't keep a change here"; if the user's spec opted out of coverage it
adds "coverage was switched off by this benchmark's settings".

**R-6, edits a locked file**

```text
Refused: your change edits {file}, which this benchmark locks so it
can't be tuned to pass. Compare against a benchmark that doesn't lock
{file}? That is a new baseline.
Result: REJECT (integrity, gate lock)
```

**R-7, would reach a real service**

```text
Refused: the benchmark would reach {host}, which is not this machine.
Start a local copy on port {free_port}?
Result: REJECT (integrity, gate side_effects)
```

**R-8, not the same program**

```text
Refused: the two versions would not run the same program ({detail}). I'll
{fix}. {waiver_line}
Result: REJECT (integrity, gate runtime)
```

`{waiver_line}` is empty, or "To run it anyway you would have to allow a
runtime change, and every result would say so. Allow it?"

**R-9, setup differed**

```text
Refused: the setup differed between the two versions ({gate_plain}:
{detail}). I'll fix it and run again.
Result: REJECT (integrity, gate {gate})
```

**R-10, timer too coarse**

```text
The clock on this machine is too coarse for this benchmark: each
measured value is about as small as the clock's smallest step. Nothing
was kept. Use a longer benchmark, or a counted measure?
Result: INCONCLUSIVE (insufficient_evidence)
```

**R-11, something could not be checked**

```text
Undecided on {candidate}: {what} could not be checked ({label}), so
perfproof can't keep it. Nothing was kept. {fix}
Result: INCONCLUSIVE (insufficient_evidence)
```

**R-12, undecided after calibration**

```text
Undecided on {candidate}: after {rounds} rounds the interval {lo}x to
{hi}x still includes the {bar}x bar. Nothing was kept. {settle}
Result: INCONCLUSIVE (insufficient_evidence)
```

`{settle}` is "At this noise a decision needs about {r} more rounds
(about {t} minutes). Run them, or use a quieter machine?" when the CLI
gave it, else empty.

**R-13, uncalibrated (what every run shows today)**

```text
perfproof can't keep changes yet: its own accuracy check isn't finished,
so no result is final. For {candidate} on {workload} it measured an
estimate of {estimate}x (uncalibrated; interval {lo}x to {hi}x over
{rounds} alternating rounds). Nothing was merged. The change is on
{where} ({sha}) for you to read.
Facts:       {facts}
Not checked: {not_checked}
Conditions:  {conditions}
Ratios are old time divided by new time.
Result: INCONCLUSIVE (insufficient_evidence)
```

`{where}` is the branch, derived branch or directory holding the change.
Next step: "Want me to try the next idea?" (find path) or none (prove).
`{conditions}` has one clause per warning, from this table, or the
warning's `message` unchanged:

| Code | Clause |
|---|---|
| `high_load` | the machine was busy (load {x}) |
| `order_not_balanced` | the new version ran first in {k} of {n} rounds |
| `autocorrelated_rounds` | neighbouring rounds were not independent |
| `differential_checks_not_run` | old and new code were not run on the same recorded calls |
| `on_battery`, `low_power_mode`, `thermal_limit`, `non_performance_governor` | the BS-10 sentence for that code |

Where the estimate is above the skill's own bound, FP-1 follows the
facts line.
