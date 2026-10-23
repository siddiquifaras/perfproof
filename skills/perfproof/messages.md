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

## The timing line, by result

Read `verdict.outcome`, `verdict.reason`, `verdict.rule`,
`verdict.gate`, `verdict.calibration` and `verdict.diagnostics`. Say
"the bar" as the number from the spec, `1 + min_effect` (3% by
default). Ratios are old time divided by new time; say so once.

`compare --json` carries `not_verified`, `warnings` and
`measured_metric`, but not yet which tests failed, which output
differs, or guardrail values. For those, read only the "Correctness"
section of `report.md`.

| Result | Say |
|---|---|
| INCONCLUSIVE with `verdict.diagnostics.procedure_uncalibrated` (every run today) | "perfproof can't keep changes yet. The estimate is <estimate>x (range <lower> to <upper>), which is not a verdict. The change is <where it is>." Never set the estimate against the change's own claim. |
| INCONCLUSIVE with `verdict.diagnostics.coarse_resolution` | "The benchmark is too short for the timer to tell the versions apart. A longer input would fix that." |
| INCONCLUSIVE, calibrated, rule 5 | "Undecided: the range <lower> to <upper> still includes the bar (<bar>)." |
| INCONCLUSIVE, rule 2, `not_verified` has `coverage` naming lines that never ran | "The changed lines <file>:<a>-<b> never ran under any check, so perfproof can't tell whether they broke anything. Add a test that reaches them (it must fail on the original), or leave this change for you to judge?" Say "never ran" from the reason; a new test is a new spec and a new baseline, the user's call. |
| INCONCLUSIVE, rule 2 (a check could not run, or a guardrail could not be established) | "Measured, but not kept: <each `not_verified` item> couldn't be checked here. <What would make it checkable.>" Don't mention the range or the bar. |
| KEEP, calibrated | "Kept: <estimate>x faster (range <lower> to <upper>), clear of the bar (<bar>). It's on `perfproof/<name>`." |
| REJECT `performance` | "Not kept: even the high end of the range, <upper>x, doesn't clear the bar (<bar>)." Say "slower" only if `verdict.regression` is true. |
| REJECT `guardrail` | "Not kept: it uses more <memory or latency> than allowed (<value> against <limit>)." |

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
