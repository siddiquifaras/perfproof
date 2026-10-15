# Work log

Concise record of commits, merges, tests, calibration, benchmark
evidence, spending, decisions and blockers. Newest first. Kept by the
lead; task branches don't edit it.

## 2026-10-16, overnight session

### Commits and merges on main

| Commit | What |
|---|---|
| `e80e399` | T2.1 machine fingerprint merged (PR #7), squash, at reviewed head `a376647`; reviewer PASS on that commit after two blocked rounds (Linux `host_id` collision fixed with boot_id; real cpuinfo captured), CI 4/4 |
| `a141718` | Spec: a test that fails only on the candidate is re-run five more times on each tree before the candidate is blamed (three baseline runs catch a 50%-flaky test only 75% of the time; found by the T2.7 worker) |
| `0efbd4b` | T2.9 folded-stack summariser merged (PR #8), squash, at `f317b70` after three reviews (two blocked on frame normalisation, fixed); CI 4/4 on that head |
| `0eb8160` | T1.3 decision rule merged (PR #3), squash, at reviewed head `bd14548`: reviewer and stats-auditor both PASS on that commit, CI 4/4 green on it |
| `aa6ef93` | Plan rebuilt as a small exact CLI plus skills; failure modes, CLI spec, evaluation design, target contracts |

### Tests

| When | Where | Result |
|---|---|---|
| after `0efbd4b` | main, this Mac | 781 passed, 1 skipped |
| after `0eb8160` | main, this Mac | 727 passed, 1 skipped (`longdouble` is float64 on macOS arm64) |

### GPU spend

No paid runs. Modal billing summary read on 2026-10-16: $21.86
metered this period, $21.67 covered by credit, $0.00 billed; no Modal
apps running. Ledger: `.claude/local/gpu-spend.md`.

### Decisions

- **Method changes from the T1.1 audit (PR #13, awaiting stats-auditor):**
  balanced order in pairs of rounds; a resolution floor
  (`coarse_resolution`, from 4000/4000 false KEEPs on millisecond-printed
  timings in the auditor's simulation); per-look confidence
  `1 - alpha / max(K, 2)`; float64 ratios are the observations; rare
  one-arm slowdowns and runtime-gate blind spots stated as limits.
  AR(1) 0.5 beating both the primary and batch means goes to T1.5/T1.6.
  The acceptance rule's multiplicity at the bar (an exact procedure
  passes all ~20 S = 1.03 cells with probability ~0.82) is left for
  Faras.
- **Runtime gate contract (PR #5, fourth review):** static launch
  analysis can't see what a program spawns. The gate covers what
  perfproof launches, followed through the launch forms it knows, and
  fails closed on known launchers (tox, nox, poetry, uvx...). The rest
  is a stated limit, not further review rounds.
- **A `not_available` gate blocks KEEP but not REJECT** (PR #9 audit):
  a REJECT keeps nothing. Written into `docs/method.md`.
- **A guardrail is `breached` when one check shows it**, even if another
  check's interval is missing (PR #9 audit F1).
- **Target classes in the schema are the six release classes**; an
  unknown class must declare every default guardrail (PR #9 review).

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

As of 04:50 PKT. Resume point if the usage limit hits (reset 06:50).

| PR | Task | State |
|---|---|---|
| #5 | T2.2 | Worker fixing review round 4 (B1 slashless shebang, B2 macOS relative rpath cwd, B3 tox/nox launchers); then reviewer, bounded to the stated contract |
| #6 | T1.4 | Reviewer PASS at `49fe296`; stats-auditor running on `49fe296`; merge if PASS |
| #9 | T2.12 | Reviewer PASS at `cf5bed7`; stats-auditor finishing `67c6f40` then `cf5bed7`; merge if PASS |
| #10 | T1.2 | Worker fixing BCa near-tie false KEEPs, clip to NaN, refuse longdouble; then reviewer + stats-auditor |
| #11 | T1.1 | Stats-auditor PASS at `be1b21e`; worker adding HL memory, eps, layout tests on top of `eee6e74`; then final reviewer + stats-auditor |
| #12 | T2.7 | Worker retuning rolling slight-speedup to ~1.5%, `peak_rss` timeout; then reviewer |
| #13 | spec | Method changes from the T1.1 audit; stats-auditor running |
| #4 | T2.3 | Waits for #5; then rebase on `command_hash`, add `procedure_gate` test, balanced pairs and resolution once #13 merges |
| — | T4.1 | Worker still building the evaluation task set |

Next once #9 merges: T2.4, T2.6, T2.13, T2.16. After #5 and #4: T2.17 CLI.

### Blockers

None yet.
