# Work log

Concise record of commits, merges, tests, calibration, benchmark
evidence, spending, decisions and blockers. Newest first. Kept by the
lead; task branches don't edit it.

## 2026-10-16, overnight session

### Commits and merges on main

| Commit | What |
|---|---|
| `6e225cd` | T2.16 side-effect guard merged (PR #16), squash, after six review rounds (libpq/WHATWG URL parsing, service files, inline config keys, argv NAME=value, http(s), linear scans, everyday-file false refusals, multi-line lists); reviewer PASS at `fe188b9`, lead merged main and ran /verify (3161 passed) and the Linux check (3158 passed) on the merge. Benchmarks now get a base environment plus the spec's env (`benchmark_environment`), not wired into the runner yet (T2.5) |
| `b86989e` | T2.17 CLI skeleton merged (PR #18), squash, at `af0f454` after four review rounds (internal_error contract, --json help, OSError mapping, non-UTF-8 paths, closed stdout/stderr); reviewer PASS on that commit, head contains main, worker's /verify (2890 passed) and Linux check (2887 passed) on it |
| `6e101a3` | T2.4 correctness gate merged (PR #17), squash, after four review rounds (known baseline failures, inf/NaN/huge-number comparisons, NUL bytes, shared rerun budget, bounded memory); reviewer PASS on code at `200d4b7`, lead merged main (`595b3e7`) and ran /verify (2791 passed) and the Linux check (2791 passed) on that head |
| `7701084` | T2.6 report merged (PR #15), squash, after four review rounds (headline overclaims, rounding, atomic and resumable `write`, two-writer race); reviewer PASS at `4f3cb71`, lead merged main (docs-only delta) and ran the Linux check on the merge (2698 passed) |
| `e0c70cd` | Method spec changes merged (PR #13), squash, at `8f4077b` after six stats audits: balanced order pairs, per-value resolution widening and the resolution stop, per-look confidence `1 - alpha/(2K)` (and `1 - alpha/(2KG)` for guardrails), float64 ratios as the observations, one-version skew stated as an uncalibrated limit, a table of measured power costs; final audit PASS on that commit |
| `f1b99d3` | T4.1 evaluation task pool and miner merged (PR #14), squash, after four review rounds; reviewer PASS at `5d7c54a`, lead merged main into the branch (`e83e81b`, eval-only diff) and ran the Linux check on the merge (2632 passed). T4.1 stays open: building the selected tasks runs with T4.2 |
| `c3fcdcf` | T1.2 bootstrap merged (PR #10), squash, at `4ac774b`; reviewer PASS and stats-auditor PASS on that commit (0 violations in ~160k exact-arithmetic rows; BCa kept as a free comparison only, weaker than percentile everywhere measured). The head didn't contain T2.12, so 30 bootstrap tests failed on main until the next commit fixed the `calibrated` argument; merge rule now requires the head to contain main |
| `ba00c0a` | T1.1 interval procedures merged (PR #11), squash, at `2ab900a`; reviewer PASS and stats-auditor PASS on that commit (bit-identical to the audited numerics on macOS and Linux, 0 false KEEP/REJECT in the exact-arithmetic sweeps, chunking mutants red, memory within bound on 78 shapes), Linux check exit 0; stats tests on merged main 1507 passed |
| `d4dcc9c` | T2.3 runner merged (PR #4), squash, at `a5a60c1` after six review rounds (failed executions as samples, duplicate keys, SIGCHLD fallback, harness faults blamed on an arm, missing program/cwd, exec errno split); reviewer PASS on that commit, Linux Docker check exit 0 |
| `ee2a149` | T2.12 safety schema merged (PR #9), squash, at `2a33595`; reviewer PASS and stats-auditor PASS on that commit (gate sweep 5.6M decisions, 7.7M permutations, defaults sweep 40,320 records, 123/123 mutants red), Linux Docker check exit 0. Example specs bumped to schema 2 right after (examples suite 43 passed) |
| `fa8bc71` | T2.7 examples and decoys merged (PR #12), squash, at `9402fff`; reviewer PASS on that commit (decoys re-probed at 150 rounds: rolling slight 1.0078 (1.0016-1.0140), membership 1.0140 (1.0098-1.0181); group-kill timeout proven by mutation), CI 4/4 on that head |
| `bfc5f2c` | T2.2 spec, lock, procedure and snapshot merged (PR #5), squash, at `27e6811` after six review rounds on the runtime gate; reviewer PASS on that commit, CI 4/4 on that head. Stated limits: interpreters started by an identified program (including `python -m runpy tox`), dlopen, renamed launchers, untracked in-tree native builds, host-arch slice only |
| `876cf40` | CI made manual-only: Actions minutes nearly spent. Merge gate is now `/verify` plus a Linux x86_64 Docker run on the exact head (`b5a4530`); CI runs once before release |
| `879bbb7` | T1.4 noise simulator merged (PR #6), squash, at `49fe296`; reviewer PASS and stats-auditor PASS on that commit (19/19 mutants killed, chunk bound measured), CI 4/4. Earlier audit blocked on a normal-noise RNG test gap, fixed |
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

- **Per-look confidence `1 - alpha / (2K)` (PR #13 revision; for Faras
  to confirm).** The PR #13 audit showed an exact interval split as
  `alpha / K` sits at 2.0-2.5% false KEEP on the bar for K <= 3, which
  the acceptance rule (CP upper bound <= 2.5% at R = 4000) passes only
  4-70% of the time. Designing to half the promise (1.25%) gives a sound
  procedure room to pass; the cost is more INCONCLUSIVEs. Same margin
  for guardrails.
- **Resolution: a derived widening, not a floor.** Each value's
  resolution widens the interval by the mean per-round log-ratio error
  bound; an undeclared `reported` metric that looks quantized ends
  INCONCLUSIVE (`coarse_resolution`). Replaces the `min_effect / 10`
  floor, which the audit showed both missed quantized false KEEPs and
  depended on an unspecified aggregation.

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

As of ~05:30 PKT, usage limit reached; resume 06:52. Resume point if the usage limit hits (reset 06:50).

| PR | Task | State |
|---|---|---|
| #19 | T2.13 | Round 3 fixes in progress (pre-import benchmark modules outside the guard, compare in both processes, os._exit replay, ctypes wording) |
| #20 | T1.5 | Reviewer and stats-auditor running at `5ade9e4` |
| #21 | T2.18 | Draft at `f3bd1c2`, suite green on macOS; merges LAST after #16-#20; then worker merges main, runs checks, un-drafts; then review |
| new | T4.2 | Phase 1 (select and build CPU tasks) in progress |

Fixed in `eeeb13e`: `tests/bench/test_runner.py::test_cpu_counts_busy_time_not_sleep` (`cpu < 0.2` for a `sleep(0.3)` child) failed once on the Linux check at load 40-50; passed alone. Load-sensitive margin; needs a follow-up in the runner tests.

Next once #9 merges: T2.4, T2.6, T2.13, T2.16. After #5 and #4: T2.17 CLI.

### Known gaps

- `.claude/hooks/lint_python.py` is listed in pyright's `include` but is
  never type-checked: pyright auto-excludes dot directories even with
  an explicit `exclude` (44 of the 45 tracked files are analysed; naming
  the file directly finds no source files). Ruff still lints it. Fix by
  moving the hook out of the dot directory or checking it separately.

### Blockers

None yet.
