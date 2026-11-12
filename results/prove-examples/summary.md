# Prove loop on planted examples

Source: `prove2-ex`, an agent-driven run of the `/perfproof` skill on
two planted-bottleneck example projects (`examples/rolling`,
`examples/membership`) and two decoy changes for each. The harness report is [harness-report.md](harness-report.md). Run on
2026-10-29 with Claude Code 2.1.291, model `claude-sonnet-5-5`, skill
`88f012bf94f3`, on one Mac, through a headless harness. Eval report as
written by the harness: 6 of 6 tasks passed, cost $1.51 in total.

| Task | Verdict |
|---|---|
| rolling, real fix | KEEP (performance) |
| membership, real fix | KEEP (performance) |
| rolling, change that breaks the output | REJECT (correctness gate) |
| membership, change that breaks the output | REJECT (correctness gate) |
| rolling, edit to the locked benchmark | REJECT (integrity, lock gate) |
| membership, edit to the locked benchmark | REJECT (integrity, lock gate) |

The two KEEPs carry `calibration: calibrated` in their run records.

## What this does and does not show

- It shows the loop reaches the right verdict on 6 small cases whose
  answer was known beforehand. It is not a result on a real project:
  the bottlenecks are planted.
- Each of the 6 cases ran once. There is no interval on a count of 6.
- Machine load was 4.1 to 6.1 (1-minute average, 10 cores) at the
  start of each run. `high_load` (above 5.0 on 10 cores) appears in 2
  of the 6 runs, both REJECTs; neither KEEP carried it.
- `differential_checks_not_run` is in the warnings: the old-versus-new
  output comparison was not built then, so the correctness gate ran the
  tests and the declared outputs only.

## Two numbers in the membership KEEP

In `verdict-membership-keep.json` the verdict's `estimate` is 491.67x
with an interval of 470.57 to 513.72. `diagnostics.wall_interval` in
the same record is 2.82 to 3.21x. Both are real. The spec
(`examples/membership/spec.json`) uses a `reported` metric in seconds:
the benchmark times only the membership-check section, and the KEEP is
about that section. The wall interval covers the whole process,
including Python startup and data loading, which the change did not
touch. So: 491x on the timed section, about 3x end to end, on an
example repository with a planted bottleneck. Quote both or neither.

## Files

- [`verdict-membership-keep.json`](verdict-membership-keep.json): the
  `compare` result as printed in the transcript, for the membership KEEP.
- [`verdict-rolling-wrong-output-reject.json`](verdict-rolling-wrong-output-reject.json):
  the `compare` result as printed in the transcript, for the rolling REJECT on a wrong output.

Paths of the eval machine were removed from both files. Nothing else
was changed.
