# What to tell the user

Build every message from the `compare --json` fields, never from the
CLI's own text. Short sentences. End a finished report with one next
step and the `Result:` line. A message that only asks something you
will go on from (the workload, the spec, the branch, which commit to
prove) has no `Result:` line: the session is not over, and the line
tells the user, and any tool reading it, that it is.

## The report after a compare

Use this shape, leaving out empty parts:

> **What I checked:** <facts: "your 42 tests passed on both versions",
> "the output file is identical", "236 queries became 10">.
>
> **The timing:** <estimate or verdict, see below>.
>
> **Not checked:** <every entry of `not_verified`, plus any gate
> whose status is `waived`, in plain words: "whether every changed line
> ran under a test", "memory use", "whether both versions ran the same
> Node">, with what would make it checkable where there is one.
>
> **Conditions:** <only if `doctor` warned or `warnings` names load:
> "measured on a busy machine (load 7)"; omit otherwise>.
>
> **Run record:** `<report_path>`.
>
> <one next step>
>
> `Result: <outcome> (<reason>[, gate <gate>])`

## The report for a whole-app ask

Start with the map, in plain words, then what was done with it:

> **Where the time goes:** one ranked list across all workloads, one
> line per row: the part ("server middleware", "client state updates",
> "native index build"), the function and file, its share as a fact
> about the profile, and its cap ("at most 1.7x").
>
> **Tried and proved:** each top candidate with its verdict line
> from the table below, what it changed, and what was kept.
>
> **Not kept, and why:** each refused or undecided attempt, as a
> mechanism or a gate, and any limit or guardrail value as the table
> below words it; never as a timing or a comparison of two timings.
>
> **Next candidates:** the rest of the ranking with their caps.
>
> **Not reached:** the parts no workload ran, with the reason (cannot
> run here, only forwards to a remote service) and the workload that
> would reach each.

Then "keep going?" and the `Result:` line as above.

## The timing line, by result

Read `verdict.outcome`, `verdict.reason`, `verdict.rule`,
`verdict.gate`, `verdict.calibration` and `verdict.diagnostics`. Say
"the bar" as the number from the spec, `1 + min_effect` (3% by
default). Ratios are old time divided by new time; say so once, in those words,
and never explain a ratio's direction with a comparison word.
When the result is short of the user's goal, end with where they stand,
the gap to the goal, one concrete next step and "keep going?" (SKILL.md,
"How to talk").

`compare --json` carries `not_verified`, `warnings` and
`measured_metric`, but not yet which tests failed, which output
differs, or guardrail values. For those, read only the "Correctness"
section of `report.md`.

| Result | Say |
|---|---|
| INCONCLUSIVE, `verdict.calibration` not `calibrated`, warning `target_class_unknown`, `schedule_not_class_own` or `schedule_uncalibrated` | "This setup isn't one perfproof has been checked on, so it can't confirm a keep. The estimate is <estimate>x (range <lower> to <upper>), not a verdict." For `target_class_unknown` or `schedule_not_class_own`, fix the spec's class and schedule and rerun; for `schedule_uncalibrated`, fix the schedule or alpha and rerun, except for `gpu_kernel`. For `gpu_kernel`: "Measured, but perfproof can't yet confirm a keep for GPU kernels." Never set the estimate against the change's own claim. |
| INCONCLUSIVE, `verdict.calibration` not `calibrated`, warning `target_class_unverified` | "perfproof hasn't confirmed this project's kind of code yet, so it can't confirm a keep. The estimate is <estimate>x (range <lower> to <upper>), not a verdict." Ask the user once whether the detected target is right; on their yes run `perfproof project confirm <target id> --json` (after `perfproof project check` if the target is stale) and rerun the compare. |
| INCONCLUSIVE, `verdict.calibration` not `calibrated`, warning `metric_not_calibrated` | "For Node and JVM code perfproof can confirm a keep only on whole-command timing, and this measurement is inside the process, so I can give the estimate and range but not a verdict. The estimate is <estimate>x (range <lower> to <upper>)." Report the number and the range and stop there. Do not suggest changing the metric to get a keep: the metric is whatever the user cares about. |
| INCONCLUSIVE with `verdict.diagnostics.coarse_resolution` | "The benchmark is too short for the timer to tell the versions apart. A longer input would fix that." |
| INCONCLUSIVE, calibrated, rule 5 | "Undecided: the range <lower> to <upper> still includes the bar (<bar>)." |
| INCONCLUSIVE, rule 2, `not_verified` has `coverage` naming lines that never ran | "The changed lines <file>:<a>-<b> never ran under any check, so perfproof can't tell whether they broke anything. Add a test that reaches them (it must fail on the original), or leave this change for you to judge?" Say "never ran" from the reason; a new test is a new spec and a new baseline, the user's call. |
| INCONCLUSIVE, rule 2 (a check could not run, or a guardrail other than a limit could not be established) | "Measured, but not kept: <each `not_verified` item> couldn't be checked here. <What would make it checkable.>" Don't mention the range or the bar. |
| KEEP, calibrated | "Kept: <estimate>x faster (range <lower> to <upper>), clear of the bar (<bar>). It's on `perfproof/<name>`." |
| REJECT `performance` | "Not kept: even the high end of the range, <upper>x, doesn't clear the bar (<bar>)." Say "slower" only if `verdict.regression` is true. |
| REJECT `guardrail`, a limit in `standing` that is missed (`gap` above 0 for a cap, below 0 for a floor) | "Not kept: in these runs it stayed under <reliable_value> (cap <limit>, <gap> short); on a busier or quieter machine this number can move. <The function or allocation the profile blames.> Next I would <one change>. Keep going on getting under <limit>?" A floor: "stayed over <reliable_value> (floor <limit>, <|gap|> short of it, never a negative number)" and "getting over <limit>?". The value is where the run stands, never a verdict. |
| REJECT `guardrail` from a relative allowance, or with every limit met (a cap at `gap` 0 or below, a floor at 0 or above) | "Not kept: it uses more <memory or latency> than allowed (<value> against <allowance>)." Name the allowance breach; no "keep going under". |
| INCONCLUSIVE, rule 2, a limit `not_established` with `standing` | "Undecided: in these runs it stayed under <reliable_value> (cap <limit>, <gap> short), too close to call; this number can move. <What the profile blames.> Keep going on getting under <limit>?" A floor: "over" and "getting over". With `reliable_value` null: "<typical> against <limit>, too few rounds to say." |

## Refusals (a check failed, so speed doesn't matter)

| `verdict.gate` | Say |
|---|---|
| `correctness` | "Refused: the new version changes the result. <failing tests, capped at five, or which output differs>." |
| `lock` | "Refused: the change edits the benchmark or its tests, so the measurement would no longer mean the same thing." Describe what the edit does as work, not speed: "it makes the benchmark do a quarter of the work and leaves `held_orders` unchanged", never "it runs shorter", "it speeds up the benchmark" or "rather than a speedup": nothing was timed. |
| `side_effects` | "Refused: the benchmark would talk to something that isn't on this machine, <address>. I can set up a local copy." |
| `runtime` | "Refused: the two versions wouldn't run the same program (<why>), so timing them says nothing." |
| `procedure`, `same_host` | "Refused: the setup differed between the two runs. I'll fix it and run again." |

## Errors from the CLI

When `ok` is false, say what happened in one sentence from
`error.message`, never the raw code, then the next step:

- `broken_baseline`: "The original code doesn't run as set up, so I
  can't compare against it." Show the command and the last few lines
  of its output.
- `not_built`: "That part of perfproof isn't built yet." Do the step by
  hand if you can, and say so.
- Anything else: fix the cause if it is in what you wrote (the spec,
  the command); otherwise stop and tell the user.

`Result: NO VERDICT (<plain reason>)` when the session ends and no
verdict exists (you stopped before `compare`, or it answered `ok:
false`), never on a question you will go on from.
