# Work log

Concise record of commits, merges, tests, calibration, benchmark
evidence, spending, decisions and blockers. Newest first. Kept by the
lead; task branches don't edit it.

## 2026-10-16, overnight session

### Commits and merges on main

| Commit | What |
|---|---|
| `0eb8160` | T1.3 decision rule merged (PR #3), squash, at reviewed head `bd14548`: reviewer and stats-auditor both PASS on that commit, CI 4/4 green on it |
| `aa6ef93` | Plan rebuilt as a small exact CLI plus skills; failure modes, CLI spec, evaluation design, target contracts |

### Tests

| When | Where | Result |
|---|---|---|
| after `0eb8160` | main, this Mac | 727 passed, 1 skipped (`longdouble` is float64 on macOS arm64) |

### GPU spend

No paid runs. Modal billing summary read on 2026-10-16: $21.86
metered this period, $21.67 covered by credit, $0.00 billed; no Modal
apps running. Ledger: `.claude/local/gpu-spend.md`.

### Decisions

- **Runtime gate scope (PR #5 reviews):** the gate compares what comes
  from outside the tree and fails closed on anything it can't identify.
  A native binary built in the tree is code under test, so its
  outside-tree linked libraries are compared instead; a committed binary
  that differs is a swap and fails; in-tree dependencies stay recorded,
  not gated, as `docs/method.md` already says.

- **Cross-host drift and order effect (T1.4):** in the alternating
  session design these effects either cancel within a session or bias
  the known speedup, so the simulator refuses them in cross-host mode.
  Calibration excludes those two cross-host cells with that reason, and
  campaign-wide drift is recorded as a limit of the cross-machine
  design.
- **Python profiling on macOS (T2.9 finding):** py-spy, austin and
  CPython 3.15's sampler all refuse to run on macOS without root. T2.10
  uses an in-process sampler (pyinstrument) on macOS and py-spy on
  Linux. T2.9's py-spy fixtures were captured on Linux in Docker.
- **Codex models:** the ChatGPT Go login rejects `gpt-5.3-codex` (the
  model in Faras's global config) and older slugs; `gpt-5.6-luna` works
  and is passed explicitly.

- **Workers run as background subagents in isolated worktrees.** The
  app refused to start new sessions from this turn, because a session
  event triggered it. Subagents with `isolation: worktree` give the
  same isolation. The lead runs the reviewer and stats-auditor on each
  PR independently before merging, instead of workers reviewing their
  own work.
- **T1.3 merged before T1.5 and T1.6**, as the plan allows; its
  checkbox stays open until T1.5 decides through it and T1.6 passes.

### In progress

T1.1, T1.4, T2.12, T2.2, T2.3, T2.7.

### Blockers

None yet.
