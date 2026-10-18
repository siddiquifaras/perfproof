# Work log

Concise record of commits, merges, tests, calibration, benchmark
evidence, spending, decisions and blockers. Newest first. Kept by the
lead; task branches don't edit it.

## 2026-10-16, overnight session

### Commits and merges on main

| Commit | What |
|---|---|
| `6c5e4aa` | T2.18 schema 3 merged (PR #21), squash, after three review rounds (resolution stop carries no estimate or interval; a capped differential count labelled a bound; repository URLs refuse any user part on web schemes, plain login names only for ssh and scp); reviewer PASS at `b4f9cfc` (rounds 2-3 narrow, Sonnet), worker /verify (3669 passed) and Linux check (3666 passed) on that head, which contains main `d115c42`. Done-when: 22 refusals and fields each caught by a test (mutation probe), schema.json regenerated, the runtime gate reads `runtime_kind` |
| `e8c91e2` | T4.2 phase 1 (T4.1 build and run) merged (PR #22), squash, after three review rounds (buildability judged on the task tree alone: a reference-only failure, hang, launch failure or broken build is `reference-fails` and drawn; README plan counts; salts pinned; Docker runs bounded and torn down; held-out reference workload times no longer recorded; home path scrubbed; Rails tzdata image); reviewer PASS at `8534ed8` (narrow, Sonnet), worker /verify (3530 passed, eval/mine 101) and Linux check (3527 passed) on that head, which contains main `cb975d1`. Done-when (T4.1 part): 30 held-out committed in f595745 before any held-out run, 20 CPU held-out and 20 development at `runs`; serving and GPU held-out unbuilt pending a GPU host |
| `7bb5731` | T2.5 compare, thin path, merged (PR #24), squash, after three review rounds (K>1 look wiring and one confidence source, broken_baseline for an unstartable baseline or a missing interpreter, env-leak test, internal_fault instead of tracebacks); reviewer PASS at `09cfb5a` (round 3 narrow, Sonnet); head `5374ea0` is that commit plus main `e01e6b1` with the PR files unchanged; worker /verify 3503 passed, lead Linux check 3500 passed on `5374ea0`. Done-when (before M1 exit): rolling fix INCONCLUSIVE `procedure_uncalibrated`, wrong-output REJECT correctness, bench-edit REJECT lock, remote-db spec REJECT side_effects; never KEEP or a performance REJECT. T2.5 stays open for real verdicts, differential and coverage wiring, reset (T2.19), detach |
| `76d2f3c` | T2.13 differential checks merged (PR #19), squash, after nine review rounds: guard on library and recorded-module first runs (LazyLoader, duplicate registration), per-thread suspension so overlapping imports cannot leave the guard off, atomic first-run claim; reviewer PASS at `962a302` (narrow B1 re-review on Sonnet), worker /verify (3462 passed) and Linux check (3459 passed) on that head, which contains main `381d3b0`. Done-when: a one-in-fifty bug caught by replayed real calls (100 captured, 100 replayed, 2 mismatches), an unpicklable argument skipped with its reason, no capture code in measured rounds (agent module absent in six runner executions) |
| `d8f36c8` | T2.10 `perfproof profile` merged (PR #23), squash, after two review rounds (`{output}` required so benchmark stdout is never read as a profile; CPU time read as summed over threads, BLAS spin); reviewer PASS at `055eca4`, lead merged main (plan/worklog-only delta) into `1b3e285` and ran /verify (3425 passed) and the Linux check (3422 passed) on it. Done-when: parser tests on captured py-spy and pyinstrument fixtures; py-spy and pyinstrument find the known bottleneck at rank 1 on rolling and membership (Linux Docker, emulated x86_64); parse time 5.0 ms and 0.48 MiB peak on the largest fixture, in the PR |
| `6fe693c` | T1.5 sequential looks merged (PR #20), squash, after four review rounds and three stats audits; reviewer PASS and stats-auditor PASS at `67f6838`, lead merged main and ran /verify (3347 passed) and the Linux check (3344 passed) on the merge. T1.5 stays open in the plan until its per-target schedule defaults are written into docs/method.md |
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

**Resumed 2026-10-19** after the pause. Every subagent, reviewer and side
session now runs on Sonnet 5.5 (Faras); the lead stays on Opus 5.5.
Stopped agents were restarted as fresh Sonnet agents in their own
worktrees. New plan tasks from Faras: T3.14 depth tiers, T3.15
token-thrift output, T3.16 install and first run, and research-backed
playbooks (M5 intro).

| Piece | Branch / worktree | Where it stopped | Next |
|---|---|---|---|
| PR #26 T2.14 coverage gate | `task/t2.14-coverage-gate`, `.claude/worktrees/agent-a519b3f94416597c1` (head 822793d, main 27b65e2 merged) | Snapshot untracked files before any run, so the gate's own leftovers (`__pycache__`) never count; its test; final checks | Narrow re-review of the untracked rule, then merge |
| T2.19 runner follow-ups | `task/t2.19-runner`, `.claude/worktrees/agent-a0a873c1036fbd37a` (85d2733, committed, not pushed, no PR) | Mutation checks of the new tests, overhead re-measure, checks, push, PR | Review, merge |
| T4.2 labeller | `task/t4.2-labeller`, `.claude/worktrees/agent-ae559ea2ecf238552` (a0d94a1; main merged uncommitted?) | README total-hours estimate for the CCX13, Linux check, push, PR | Review; then tell Faras to create the Hetzner CCX13 (approved, $5 cap) |
| T1.6 calibration | `task/t1.6-calibration`, `.claude/worktrees/agent-ad272fa48b82d2ed9` (staged, uncommitted: montecarlo.py, run_calibration.py, two result CSVs, bootstrap.py) | The CI-subset test file; N1 decision; results table in method.md; merge main (schema 3) | reviewer + stats-auditor, merge |
| T3.11 PR study | session local_4c51bae0 (`t3.11-pr-study` worktree; 63ef61b, 3c505fc local, not pushed) | findings.md, summary table in agents.md, push, PR | Review, merge |
| PR #25 T3.10 NetBox hand-runs | head 7ef0b00 | Lead review was running (fabrication, overclaiming, secrets, scope); not finished | Rerun review, merge |
| PR #27 T3.10 frames2py/picolm | head 70e8259 | Lead review was running; had confirmed 17 run dirs | Rerun review, merge |
| T1.3 session | local_b4ab9cc0 (PR #3 merged 2026-10-15) | Waiting on a prompt only Faras can answer; its work is merged | Faras answers or archives it |

Main is `27b65e2`. Merged this stretch: #23 profile, #19 differential, #24 compare, #22 eval tasks, #21 schema 3.

Next after the above: T2.20 examples (needs schema 3, done), compare wiring (differential, coverage, declared resolution), T3.12 skill contract from the three studies.

### Known gaps

- `.claude/hooks/lint_python.py` is listed in pyright's `include` but is
  never type-checked: pyright auto-excludes dot directories even with
  an explicit `exclude` (44 of the 45 tracked files are analysed; naming
  the file directly finds no source files). Ruff still lints it. Fix by
  moving the hook out of the dot directory or checking it separately.

- `tests/bench/test_correctness.py::test_candidate_timeout_fails_the_gate`
  failed once in linux.sh on PR #23's head `055eca4` at load ~16 (another
  container running): the baseline's empty script overran its 0.5 s
  timeout and raised `BaselineBrokenError`. Passed on a rerun alone.
  Load-sensitive timeout margin in T2.4's tests; not yet fixed.

- `tests/bench/test_side_effects.py::test_minified_json_scales_linearly`
  failed once in /verify on PR #24 at load ~26 and passed alone: a
  timing-ratio test, load-sensitive like the two above.

### Paid compute

- 2026-10-17: Faras approved a Hetzner CCX13 (2 dedicated vCPU, about
  $0.07/hour, billed hourly until deleted) as the second labelling
  machine for T4.2, about 20 hours, capped at $5. Faras creates and
  deletes it; not created yet (waits for the labeller). Spent so far: $0.

### Blockers

None yet.
