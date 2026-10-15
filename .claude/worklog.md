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
