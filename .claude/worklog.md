# Work log

Concise record of commits, merges, tests, calibration, benchmark
evidence, spending, decisions and blockers. Newest first. Kept by the
lead; task branches don't edit it.

## 2026-10-16, overnight session

### Commits and merges on main

| Commit | What |
|---|---|
| `ecc0b5d` | T3.8 first version of the /perfproof skill merged (PR #41): SKILL.md plus spec, discovery and messages guides. Run headless (claude -p, Sonnet 5.5) on example repos: prove real speedup gives INCONCLUSIVE uncalibrated with estimate and no claim comparison ($0.23, 60 s); prove wrong-output gives REJECT correctness with the failure reproduced ($0.15, 26 s); bare find profiles, finds the hotspot, fixes, proves, leaves the change on a branch with consent ($0.23 to 0.45). Reviewer PASS at `ad1d8bb`; the re-runs used that exact text. Open: Codex, session undo/resume, install-skills, message snapshots, real repos |
| `9e117d8` | T3.1 project-model and hypothesis schema merged (PR #36), squash, after two review rounds: OutcomeEvent mirrors Verdict (no speedup on gate REJECTs, bar sides exact, calibration and gate recorded); history events tied to status; HowToRunEntry `missing`; hash kind binding. The re-review's last block (rule 5 with no interval refused) was the lead's own wrong instruction, fixed by the lead (`21757ab`). /verify 3960 passed and Linux 3957 passed on the merged head. SCHEMA_VERSION stays 3; new root documents carry their own version |
| `554ccd2` | T4.2 independent labeller merged (PR #29), squash, after three rounds: oracle failure gives `fail` without a suite recipe; runtime no-perfproof-import test; pinned rustup-init 1.28.2 and uv 0.12.20 tarballs with sha256 (lead verified uv's against GitHub's published file), pinned cmake/ninja/node; held-out guard covers every file that decides a label and runs before anything is touched; reruns of invalid results wired and tested. Reviewer simulated the two-machine rule: false improvement 0.05 to 0.7% in plausible cells, 2.6% only at lag-1 0.9. Lead merged main (T2.19 delta, independent), eval/label 102 passed, pyright clean. Pilot: about 40 VM hours, about $2.80 on a CCX13 (estimate) |
| `b9db3b2` | T2.19 runner follow-ups merged (PR #34), squash: balanced order pairs (`round_orders` matches simulate.py), the spec's reset before every execution outside the timed region, `run_single` so the baseline recheck runs once; compare drops the reset refusal and order warning. Reviewer PASS at `7d792d7`; lead merged main twice, /verify 3721 passed and Linux 3718 passed on the first merge, then examples (46) and runner/compare tests (112) after the examples-only delta |
| `d9adde6` | T2.20 examples merged (PR #35), squash: shipped specs declare objective `wall_time` and the three default guardrails; compare now runs them (fixes INCONCLUSIVE uncalibrated; wrong-output REJECT correctness; bench-edit REJECT lock; remote-db REJECT side_effects). Reviewer PASS at `661184a` (4 of 4 spec mutations caught); lead merged main (no code delta), ruff clean, `pytest examples` 46 passed. Peak memory is `unmeasurable` until T2.15 |
| `a10063d` | T3.12 skill contract merged (PR #32), squash, after two rounds: split into `docs/skill.md` (208-line contract), `skill-messages.md`, `skill-rationale.md`; one row per trigger; revisions only when explicit; NO VERDICT where none exists; dirty-tree check before writes on the find path; default depth medium, light for diffs. Lead read the contract and confirmed the rule-2 not_available case in method.md. Docs only |
| `ec2275e` | T3.17 where the tokens go merged (PR #31), squash: 173 sessions; cache reads 54.2% of cost pooled, 70.6% in the T3.10 hand-runs; CLI output 3.2% there. Review blocked numbers the script did not produce and an understated hand-run share; one fix round; lead reran the script with the same `--until` (headline identical; one in-progress transcript differs because files written after the cutoff are skipped, a known reproducibility gap) |
| `daaf539` | Lead: discovery angle checklist gains a "Seen in classified diffs (counts, not rates)" column, a Skippable work angle (16 changes) and a thin Bandwidth against peak angle (4), from T3.18; existing Evidence text kept unchanged |
| `bba5521` | T3.18 agent-inefficiency catalogue merged (PR #30), squash: 126 classified changes (55 random draw, 38 reverted and regression, 18 targeted, 15 agent and T3.11), held-out excluded. Review: 11 of 11 random classifications agreed, every count recomputed; one fix round (counts not rates, four rows reclassified, thin Bandwidth, X3 figures); lead recomputed the draw counts and merged main. Docs only |
| `2840ebe` | Ruff: `studies/**/*.md` excluded, so study transcripts keep quoted code verbatim (main's format check had failed on two quotes from #25 and #27) |
| `252bea1` | T3.11 perf PR and unaided-agent study merged (PR #28), squash: 10 PRs (7 merged, 3 closed; 2 reverts) and 7 agent runs scored. Review re-fetched every PR and checked about 70 run values against the logs; one fix round corrected a misread quote, P4 and P7 figures, X3 summary, numbers with no surviving record (now labelled), X2 setup difference, C4 ranking consistency; lead spot-checked the fixes. Docs only |
| `e6cd1a8` | T2.14 coverage gate merged (PR #26), squash, after three review rounds (five false passes fixed: clause-exact deletion siting, lambda entry arcs, long-lived child lifetimes, untracked files listed before any run, `git diff --text`; parser failures fail closed; only judged test ids count). Reviewer PASS on code at `822793d` (8 of 8 mutations caught), worker /verify (3702 passed) and Linux (3699 passed) there; lead merged main (docs-only delta) into the head. Done-when: covered change, unreached branch, quarantined-only, deleted line, non-Python change without opt-in, each tested |
| `39d10d7` | T3.10 hand-runs on frames2py and picolm merged (PR #27), squash. Reviews found every number traced to logs and records; three wording rounds removed verdict language from uncalibrated INCONCLUSIVE results; the lead made the last wording fixes and merged main. Docs only, under `studies/hand-runs/` |
| `8edc142` | T3.10 hand-runs on NetBox merged (PR #25), squash. Same pattern: every number traced; overclaiming removed over three rounds; lead fixed the last items (estimates labelled, query counts instead of timings, `~` paths) and merged main. Docs only |
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

**Resumed 2026-10-19** after the pause. Release target about 2026-11-02 (Faras); review rounds capped at two per PR, then the lead decides. Every subagent, reviewer and side
session now runs on Sonnet 5.5 (Faras); the lead stays on Opus 5.5.
Stopped agents were restarted as fresh Sonnet agents in their own
worktrees. New plan tasks from Faras: T3.14 depth tiers, T3.15
token-thrift output, T3.16 install and first run, and research-backed
playbooks (M5 intro).

| Piece | Branch / worktree | Where it stopped | Next |
|---|---|---|---|
| T2.19 runner follow-ups | `task/t2.19-runner`, `.claude/worktrees/agent-a0a873c1036fbd37a` (85d2733, committed, not pushed, no PR) | Mutation checks of the new tests, overhead re-measure, checks, push, PR | Review, merge |
| T4.2 labeller | `task/t4.2-labeller`, `.claude/worktrees/agent-ae559ea2ecf238552` (a0d94a1; main merged uncommitted?) | README total-hours estimate for the CCX13, Linux check, push, PR | Review; then tell Faras to create the Hetzner CCX13 (approved, $5 cap) |
| T1.6 calibration | `task/t1.6-calibration`, `.claude/worktrees/agent-ad272fa48b82d2ed9` (staged, uncommitted: montecarlo.py, run_calibration.py, two result CSVs, bootstrap.py) | The CI-subset test file; N1 decision; results table in method.md; merge main (schema 3) | reviewer + stats-auditor, merge |
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
  deletes it. Labeller merged 2026-10-19 (`554ccd2`); pilot estimate about
  40 hours, about $2.80, inside the $5 cap. On hold at Faras's request (2026-10-19); held-out labelling waits. Spent so far: $0.

### Blockers

None yet.
