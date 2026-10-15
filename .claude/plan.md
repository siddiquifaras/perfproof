# perfproof plan

The working plan for v1. `docs/method.md` and `docs/architecture.md`
are the spec; this file is the order of work.

## How to use this file

- **Lead** (Faras, or the session he runs in the main checkout) owns
  this file: ticks tasks after merging, edits tasks, answers open
  questions. Task branches never edit it.
- **Workers** take one task each in their own worktree session:
  `/run-task <ID>` in Claude Code. The skill reads the task, checks its
  dependencies are merged, and ends with a PR.
- A task can start when everything under **Needs** is merged into
  `main` and its milestone's gate is open. Tasks without overlapping
  **Owns** can run at the same time.
- **Done when** is the test of completion. A PR that doesn't show it
  passing isn't done.
- **Effort** is the suggested Claude Code effort level for the session.

### Integration

Separate worktrees stop edits from colliding. They don't stop two
tasks from assuming different things about a shared interface.

- `perfproof.schema` is the only contract between packages. A task that
  needs it changed stops and reports; the lead makes the change as its
  own PR first.
- The lead merges in dependency order and runs the full suite, including
  `pytest -m calibration`, on `main` after every merge. A red `main`
  stops new merges until it is fixed.
- Questions that cross lanes go to the lead, not to the other worker.

### Milestone gates

The verdict is the product, so nothing is built on it before it is
trustworthy.

- **M1 exit:** T1.6 merged with every development cell passing the
  acceptance rule in `docs/method.md`, and the T1.7 audit passing on
  held-out scenarios.
- **M2 exit:** T2.8 green on this Mac and on Linux CI: the full local
  loop works on the Python examples and every decoy is refused.
- M3, M4, M5 and M6 tasks start only after the M2 exit. M2 tasks that
  don't depend on M1 may start before the M1 exit.
- The goal of M1 and M2 is one end-to-end result nobody can dispute.
  Task count is not progress.

## Decisions

| Topic | Decision |
|---|---|
| Repo | `siddiquifaras/perfproof`, private until launch |
| License | Apache-2.0 |
| Language | Python 3.12+, managed with uv only |
| Style | PEP 8 via ruff at 79 columns, NumPy docstrings, pyright strict |
| Agent instructions | `AGENTS.md` shared by all agents; `CLAUDE.md` imports it and adds Claude Code specifics; nested `AGENTS.md` per package |
| Dev skills | `.claude/skills/`, exposed to Codex through the `.agents/skills` symlink |
| Git rights | Agents branch, commit, push and open PRs. Merges to `main`, releases and PyPI publishing are Faras's. |
| Parallel work | Claude Code worktree sessions, plus subagents inside a session |
| Verdicts | KEEP if `L > 1 + min_effect`; REJECT if `U <= 1 + min_effect` or a gate fails; INCONCLUSIVE if the interval still straddles the bar when the budget runs out. `min_effect = 0.03`. Every verdict carries a reason: `performance`, `correctness`, `integrity` or `insufficient_evidence`. Full rule in `docs/method.md`. |
| Interval procedure | Fixed in advance: t interval on the mean log ratio, Bonferroni split across at most `K` looks, geometric-mean speedup as the estimand. Batch means is the prespecified fallback for autocorrelation. A challenger replaces it only under the rule in `docs/method.md`. |
| Calibration acceptance | Fixed in advance: errors defined relative to the bar (KEEP at or below it is a false KEEP; REJECT above it is a false REJECT; rejecting a below-bar speedup is correct). Every cell's one-sided 95% Clopper-Pearson upper bound at most 2.5% (at most 83 errors in `R = 4000`), reported per cell, no reruns or reseeding after a failure |
| Cross-machine | Same machine is the default and the basis of perfproof's claims. One fixed machine per version is refused. Cross-machine needs an opt-in and the alternating fresh-host session design, and its verdicts are labelled with the assumption they rest on |
| Calibration scope | Development scenarios, held-out audit scenarios at fresh seeds, and real A/A traces per target class with a held-out machine; uncalibrated target classes are labelled in every report |
| Metrics | `wall`, `cpu`, `reported`, and `counter` (Linux `perf stat`) in v1 |
| Shared data types | Pydantic models in `perfproof.schema`; NumPy arrays inside `stats` |
| Runtime dependencies | `coverage` and `hypothesis` join the runtime dependencies in T2.12, for the coverage gate and differential checks. `numpy` for the stats core. `pydantic` for the schema; the MCP SDK requires `pydantic>=2.12` anyway, so it adds nothing to the install. `scipy` for the t quantiles of the primary procedure and the Wilcoxon challenger. `mcp` arrives with T3.3. Test and lint tools stay in the `dev` group. |
| Entry point | `perfproof mcp` starts the MCP server; MCP clients launch `uvx perfproof mcp` |
| Remote | `host` argument on `measure`, `profile` and `compare`; the remote side runs the same engine |
| Local-only files | `.claude/local/` (handoff, budget) and `CLAUDE.local.md`, gitignored, copied into worktrees by `.worktreeinclude`. Nothing needed to build, test or run perfproof lives there. |
| Client support claims | A client is named as supported only after T3.4 and T5.8 pass with it |
| Product shape | Understand, discover, prove, repeated: `docs/discovery.md` specifies the first two, `docs/method.md` the third. Deterministic code owns state, measurement, statistics and records; the agent owns judgment |
| Release scope | Six target classes: Python, native, web and CRUD services, LLM serving, GPU kernels and PyTorch, agentic apps. A class is claimed only when its real-repo case study and its real-trace calibration both pass |

## Open questions

Ask Faras; don't decide silently.

1. Case-study repositories still to choose: a real CRUD app (T6.13), a
   GPU kernel or PyTorch workload (T6.26), and an agentic app (T6.31).
   Python is frames2py, native is picolm, serving is vLLM.
2. GPU budget against the release. The serving and GPU-kernel tracks
   need paid runs (T6.15, T6.18, T6.20, T6.22, T6.24, T6.26). Before M6
   starts, estimate their total cost per the `gpu-run` skill and
   compare it with the budget in `.claude/local/HANDOFF.md`. If it
   doesn't fit, decide what changes; don't quietly drop a class.
3. A host for testing the SSH runner (T6.14): Remote Login to this Mac,
   a cheap Linux VM, or a Modal sandbox with sshd.
4. Installing Codex locally (standalone binary, no Homebrew) for T0.4
   and T3.4.
5. Default guardrail allowances: how much worse may peak memory and p99
   latency get before a speedup is refused? Needed before T2.15.

Repository admin (Faras): make the CI checks required on `main` so a red
PR can't merge. T0.2 delivered the checks; enforcing them is a setting.

## Not scheduled yet

Useful, but not needed for the release experience. Each gets a task
with its own acceptance test when its prerequisite lands. None of them
may loosen the decision rule or the calibration criteria. Everything
the release needs is in a milestone, however hard it is to build.

| Capability | Waits for | Note |
|---|---|---|
| No-regression mode | T1.7 | "Is it no more than m slower?" as a signed `min_effect`. Needs a schema task if `min_effect` must go negative. |
| CPU pinning and isolation | T2.3 | Linux only, where permissions allow; never a requirement. |
| History bisect | M2 exit | Benchmark across git history to find when a path got slower, using `compare` at each bisect step. Many steps mean many chances to be wrong, so the commit it blames is confirmed by a fresh, independent `compare` of that commit against its parent, and only that run is claimed. |
| CI regression check | T3.1, no-regression mode | `perfproof compare --base origin/main` as a PR check that fails when a measured path is shown slower than its allowance. CI runners are noisy, so it uses the `counter` metric on Linux where available. Optionally a ratchet that tightens the allowance when a path gets faster. |

## Lanes

```
M0  T0.3 schema ─┬─ T0.2 CI
                 │
M1  T1.1 ─┬──────┼─ T1.5 ─┐
    T1.3 ─┘      │        ├─ T1.6 calibration ─ T1.7 audit ══ M1 exit
    T1.2 ────────┼────────┤
    T1.4 ────────┼────────┘
                 │
M2  T2.1 T2.2 T2.3 T2.4 T2.6 T2.7 T2.9 ─┬─ T2.5 (after M1 exit)
    T2.12 (after T1.3) ─ T2.13…T2.16 ───┤
                 T2.10 (after T2.7, T2.9)├─ T2.11 real traces
                                         └─ T2.8 local loop ══ M2 exit

after the M2 exit:
M3  T3.1 CLI ─ T3.3 MCP ─ T3.4 clients; T3.2 jobs; T3.5 skills; T3.6 planning
M4  T4.0 schema ─ T4.1 project model, T4.2 detection
M5  T5.1…T5.6 discover ─ T5.7 /perfproof loop ─ T5.8 agents
M6  six target tracks in parallel; each case study also needs T5.7
M7  release bar, README, release
```

Before the M1 exit, T1.1 to T1.4, T2.1 to T2.4, T2.6, T2.7 and T2.9 can
all run at once: their files don't overlap.

## M0 Foundation

- [x] **T0.1 Repo and agent setup.** Private repo, uv package skeleton,
  ruff, pyright and pytest config, `AGENTS.md`, `CLAUDE.md`, nested
  package guides, `.claude/` settings, lint hook, skills and subagents,
  README and docs, reference files from the vLLM study.

- [x] **T0.2 CI** (siddiquifaras/perfproof#2, `c7aefda`)
  - Owns: `.github/workflows/`
  - Needs: T0.3
  - Do: GitHub Actions on `ubuntu-latest` and `macos-latest`, Python
    3.12 and 3.13, using `astral-sh/setup-uv`. Steps: `uv sync
    --frozen`, ruff format check, ruff check, pyright, pytest. A
    separate nightly job runs `pytest -m calibration`.
  - Done when: CI is green on the PR and the PR shows a deliberately
    failing commit going red.
  - Effort: medium

- [x] **T0.3 Schema contracts** (siddiquifaras/perfproof#1, `40f12a7`)
  - Owns: `src/perfproof/schema.py`, `tests/test_schema.py`
  - Needs: none
  - Do: frozen Pydantic models with a `schema_version`:
    `MetricSpec` (kind, direction, unit), `CorrectnessSpec`, `Budget`,
    `LookSchedule`, `BenchmarkSpec` (with its content hash and the
    opt-ins for runtime changes and cross-host comparison),
    `LockManifest` (files and input data), `Sample` (version, round,
    position in round, value, cpu time, max RSS, exit code, hash of the
    executed argv, env and cwd), `GateResult`, `Interval`, `Verdict`
    (outcome, reason, deciding gate or rule, estimate, interval,
    rounds, looks, procedure, regression flag, cross-host flag,
    calibration status, diagnostics such as `metric_disagreement`),
    `Hotspot`,
    `MachineFingerprint` (every probe optional, with a reason when
    missing), `RunRecord`. Field names and meanings match
    `docs/method.md` and `docs/architecture.md`.
  - Done when: JSON round-trip tests for every model; invalid values
    (negative counts, unknown enum, NaN estimate, a correctness or
    integrity verdict carrying a speedup estimate) are rejected; the
    spec hash is stable across key order and changes when any field
    changes; the exported JSON Schema is checked in and a test fails if
    it drifts.
  - Effort: high

- [ ] **T0.4 Check Codex discovery** (lead)
  - Needs: open question 4
  - Do: in a fresh Codex session in this repo, confirm it loads
    `AGENTS.md`, lists the skills through the `.agents/skills`
    symlink, and reads a nested `AGENTS.md` when working in
    `src/perfproof/stats/`. If the symlink or nested files are not
    picked up, change the layout and record what Codex actually does.
  - Done when: the observed behaviour is written into this file.

## M1 Statistics core

Every task here follows `src/perfproof/stats/AGENTS.md` and is reviewed
by `stats-auditor` before its PR opens.

- [ ] **T1.1 Paired intervals**
  - Owns: `src/perfproof/stats/intervals.py`,
    `tests/stats/test_intervals.py`
  - Needs: none
  - Do: from baseline and candidate arrays shaped `(..., n)` and a
    metric direction, compute log ratios and two intervals with a
    leading batch axis: the t interval on the mean log ratio, and the
    Hodges-Lehmann estimate with the Wilcoxon signed-rank interval
    (exact for small n, normal approximation with tie and continuity
    correction above a documented n). The t interval is the primary
    procedure in `docs/method.md`; Hodges-Lehmann is a challenger.
    Confidence level is an argument, so T1.5 can apply the Bonferroni
    split. Include the batch-means transform (block size argument) that
    `docs/method.md` names as the autocorrelation fallback. Return
    estimate and bounds on the ratio scale.
  - Numerics, per "Numerics" in `docs/method.md`: no bound may come out
    more favourable to the candidate than its exact value because of
    rounding. When every ratio in a batch row is the same float, the
    interval is exactly that ratio. Otherwise each bound is widened
    outward by an error bound derived from the operations that
    produced it (mean, variance, quantile, `exp`), stated in the
    docstring with its derivation. No tolerance chosen by observation,
    and no fixed one-ulp step.
  - Done when: results match `scipy.stats` reference computations to
    1e-12 on fixed inputs (which alone cannot catch the numerics
    defect); property tests from the stats guide pass; 10,000 batched
    replications of n = 40 run in one call, with the timing in the PR;
    and a committed full-path regression test in
    `tests/stats/test_intervals.py` runs observations through this
    interval, `decide` and `RunRecord` validation for every case below,
    with no false KEEP and no false REJECT:
    - the reproducer: `min_effect = 0.4730308035801494`, 48 rounds all
      at `b = 1 + min_effect`;
    - all rounds at the bar and at one ulp either side of it, for the
      `min_effect` values T1.3 tested (0, 0.03, 0.118, 0.124,
      0.4730308035801494) and for a sweep of at least 300 bars;
    - tiny-noise rounds around each of those bars.
    The PR states the defect, its reproducer, the fix, the error-bound
    derivation, and the before and after counts.
  - Effort: high

- [ ] **T1.2 Bootstrap ratio of medians**
  - Owns: `src/perfproof/stats/bootstrap.py`,
    `tests/stats/test_bootstrap.py`
  - Needs: none
  - Do: unpaired percentile and BCa intervals for
    `median(candidate) / median(baseline)`, oriented by direction.
    Vectorized as in the stats guide, chunked under a byte budget
    argument. Use `reference/vllm-case-study/repro.py`
    `bootstrap_ratio_ci` as the behavioural reference, not as code to
    copy.
  - Done when: agrees with the reference implementation on the same
    data within Monte Carlo error; memory stays under the budget for
    B = 100,000, n = 200 (measured); timing for B = 10,000, n = 100 in
    the PR.
  - Effort: high

- [ ] **T1.3 Decision rule**
  - Owns: `src/perfproof/stats/decision.py`,
    `tests/stats/test_decision.py`
  - Needs: T0.3
  - Do: a pure function from gate results, interval, `min_effect`,
    whether budget remains, and (cross host) completed session pairs
    and `n0`, to `KEEP`, `REJECT`, `INCONCLUSIVE` or continue, plus the
    reason, the deciding gate or rule, and the regression flag, exactly
    as in `docs/method.md`.
  - Numerical contract. The verdict is a function of the float64
    ratio-scale bounds `L` and `U` that go into `Verdict.interval`,
    compared with `bar = 1 + min_effect` computed in float64 (not
    `exp(log1p(min_effect))`):
    - KEEP iff `L > bar`;
    - performance REJECT iff `U <= bar`;
    - INCONCLUSIVE iff `L <= bar < U` and no budget remains; continue
      iff `L <= bar < U` and budget remains;
    - regression iff the verdict is a performance REJECT and `U < 1`;
    - any failed gate decides first (rule 1), whatever the interval
      and whether the host is local or remote; when several fail, the
      first in `lock`, `procedure`, `runtime`, `same_host`,
      `correctness` is named, whatever order they ran in.
    No epsilon, tolerance, rounding or log-scale comparison may decide.
    Intervals can be computed on the log scale, but the decision is
    made after `exp`, on the exact floats that are recorded. Nothing is
    rounded to a display precision before a decision. Conservative
    outward widening is allowed only inside T1.1's interval
    construction, justified by its derived error bound; the bounds that
    result are what gets recorded and decided on. The bar uses the
    stored float64 `min_effect`, not the decimal the user typed. The
    docstring states how `L` and `U` are computed and converted.
    `perfproof.schema.RunRecord` refuses any record that breaks these
    rules: a log-scale comparison can call KEEP when the recorded `L`
    is exactly 1.03 after `exp`, and that record could not be written.
  - Cross-host evidence. The decision takes a count; this task also
    owns the pure helper that produces it, and T2.5 and T5.2 must build
    records with it:
    - One `MachineFingerprint` per session started, probed on the host
      that session ran on, including sessions that never pair (crashed,
      timed out, gate failed). A probe that fails leaves its fields
      `None` with a reason in `missing`; nothing is filled in.
    - `completed_session_pairs` counts a pair only when a baseline
      session and the candidate session launched right after it both
      completed. A failed or incomplete session voids its pair, and
      sessions are never re-paired across the gap. A host that was
      never allocated is not a session; the session launched in its
      place takes its turn.
    - Fewer than `n0` completed pairs: never KEEP or a performance
      REJECT. Continue while budget remains and another pair can be
      collected; INCONCLUSIVE when no more can be. A failed gate still
      decides by rule 1 at any pair count. From `n0` on, the bar rules
      above apply.
    - A gate that fails before any remote host exists: rule 1 REJECT
      with zero rounds and `session_pairs = 0`, and `machines` holds
      exactly one fingerprint, of the machine that ran the gates. No
      fingerprint is recorded for a host that was never allocated.
  - Done when:
    - every row of the method table passes as written;
    - `L` and `U` at `bar` and one float64 ulp either side
      (`math.nextafter`), for `min_effect` 0.03 and 0; `U == 1` and
      `nextafter(1, 0)` for the regression flag;
    - bounds come out of the function bit-identical to what went in;
    - every gate failing, in both host modes, overrides a KEEP-worthy
      interval;
    - a property test: for intervals sampled near the bar, including
      ones computed as `exp(log-scale bounds)`, every verdict the
      function returns validates as a `RunRecord`, and changing its
      rule to either of the other two makes the record invalid;
    - cross-host tests at `n0 - 1`, `n0` and `n0 + 1` completed pairs,
      and pair-counting tests for a failed middle session, a trailing
      unpaired baseline, and an incomplete candidate;
    - invalid parameters raise `ValueError` naming the value.
  - Ticked off only when, beyond the merge: T1.5's sequential driver
    calls this function as its only decision path, and T1.6 meets the
    false-KEEP and false-REJECT acceptance rule in every cell through
    it, with no scenario excluded. That is evidence under the scenarios
    and assumptions tested, not proof of validity in general.
  - Effort: high

- [ ] **T1.4 Noise simulator**
  - Owns: `src/perfproof/stats/simulate.py`,
    `tests/stats/test_simulate.py`
  - Needs: none
  - Do: generate paired samples shaped `(R, n, 2)` with a chosen true
    speedup, from parameters rather than named presets, so any mix of
    the effects in `docs/method.md` can be combined in one scenario.
    The development scenarios are presets on top. Two modes: one host
    (offsets shared within a round) and cross host (random per-session
    offsets, alternating session-pair design). In-round order is
    randomized per round, so the order effect is meaningful. Seeded
    `Generator` in, arrays out. The audit ranges in `docs/method.md`
    must be expressible.
  - Done when: each model's sample statistics (CV, tail mass, drift
    slope, variance step, lag-1 autocorrelation, order effect, bimodal
    fraction, outlier rate) match its parameters within stated
    tolerance at large R.
  - Effort: medium

- [ ] **T1.5 Sequential looks**
  - Owns: `src/perfproof/stats/sequential.py`,
    `tests/stats/test_sequential.py`
  - Needs: T1.1, T1.3
  - Do: the look schedule from `docs/method.md` (`n0 >= 10`, `k`, `K`,
    budget cut-off with a final look that replaces the remaining
    looks), the Bonferroni split, and a driver that takes the paired
    samples so far and returns the decision at each look. The driver
    decides only through T1.3's function, never a second copy of the
    rule.
  - Done when: driver output on fixed inputs is checked by hand in
    tests, including a budget that runs out between looks; a test feeds
    a clearly faster candidate whose interval still straddles the bar at
    the budget and gets INCONCLUSIVE, never KEEP; the per-target
    schedule defaults are written into `docs/method.md`.
  - Effort: high

- [ ] **T1.6 Calibration**
  - Owns: `tests/calibration/`, the calibration results in
    `docs/method.md`
  - Needs: T1.1, T1.2, T1.4, T1.5
  - Do: Monte Carlo over every noise model, both host modes, true
    speedups 0.95, 1.00, 1.03, 1.04, 1.05, 1.10, 1.20, with sequential
    looks
    on, `R = 4000` per cell, on the development scenarios. Apply the
    acceptance rule in `docs/method.md` exactly as written, and report
    the diagnostics it lists. Run the challengers alongside the
    primary. Vectorize replications; chunk to bound memory. If a
    procedure is too expensive for `R = 4000` (the bootstrap under
    sequential looks may be), run it at a smaller `R` and report the
    wider standard error rather than skipping cells. The seed
    comes from `PERFPROOF_CALIBRATION_SEED` when set, so the auditor can
    rerun with a different one.
  - Done when: results table in `docs/method.md` with seed, `R`,
    counts and Clopper-Pearson bounds; every cell passes for the primary
    procedure (or the batch-means fallback, or a challenger switched in
    under the documented rule), or the failing cells are written up and
    the task stays open; challengers that lose everywhere and serve no
    other purpose are deleted; a CI-sized subset runs in under two
    minutes.
  - Effort: max

- [ ] **T1.7 Held-out audit** (lead runs `stats-auditor`)
  - Owns: the audit results in `docs/method.md`
  - Needs: T1.6
  - Do: the audit in `docs/method.md`: the development scenarios at
    fresh seeds, and at least 20 new scenarios drawn at audit time from
    the audit ranges, each combining several violations. The auditor
    applies the acceptance rule from raw outcome counts.
  - Done when: every cell of every scenario passes, reported per
    scenario with seeds and parameters. A failure sends the procedure
    back to T1.6; the failed audit's scenarios join the development
    set, and the next audit draws new ones.
  - Effort: max

## M2 Local loop

The first working product: the whole loop on Python examples, on this
Mac and on Linux CI, with decoys that must be refused.

- [ ] **T2.1 Machine fingerprint**
  - Owns: `src/perfproof/machine/`, `tests/machine/`
  - Needs: T0.3
  - Do: probes for macOS and Linux: host identity, CPU model, physical
    and logical cores, frequency policy, load average, power source and
    battery, thermal state, memory, OS and Python versions, NVIDIA GPU,
    driver, clocks when `nvidia-smi` exists. Warnings for battery power,
    high load, non-performance governor, thermal limits. Probes run
    once per comparison, never between rounds.
  - Done when: parsers are tested on captured outputs of each command
    (`pmset -g batt`, `pmset -g therm`, `sysctl`, `/proc/cpuinfo`,
    `nvidia-smi`), and an integration test fingerprints the machine it
    runs on.
  - Effort: medium

- [ ] **T2.2 Spec, lock, procedure and snapshot**
  - Owns: `src/perfproof/bench/spec.py`, `src/perfproof/bench/lock.py`,
    `src/perfproof/bench/snapshot.py`, `tests/bench/test_spec.py`,
    `tests/bench/test_lock.py`, `tests/bench/test_snapshot.py`
  - Needs: T0.3
  - Do: save specs under `.perfproof/benchmarks/` as frozen records
    keyed by their hash; a changed spec is a new spec. Streaming SHA-256
    lock manifest over the benchmark harness, the correctness oracle,
    declared input data and declared config files. The `lock`,
    `procedure` and `runtime` gates and the recorded-not-gated
    differences from `docs/method.md`, with the spec's opt-ins.
    Baseline checkout with `git worktree add --detach` under
    `.perfproof/worktrees/`; add `.perfproof/` to `.git/info/exclude`.
  - Done when: tests on a temporary git repo show each of these fails
    its gate: an edited benchmark file, an edited test, changed input
    data, a changed spec field, a round whose executed command differs
    from the spec, a different interpreter in one tree without the
    opt-in. A differing undeclared environment variable is recorded as
    a warning, not a failure. Untouched trees pass, and the baseline worktree
    matches the snapshot commit.
  - Effort: high

- [ ] **T2.3 Runner**
  - Owns: `src/perfproof/bench/runner.py`, `tests/bench/test_runner.py`
  - Needs: T0.3
  - Do: run a spec's argv in a given tree with env, cwd and timeout.
    `wall` from `perf_counter_ns` around the child, `cpu` and max RSS
    from `os.wait4`, `reported` from a streamed `PERFPROOF {...}` stdout
    line, `counter` from `perf stat -x,` on Linux. Record the argv,
    declared env, cwd and resolved executable actually launched in
    every `Sample`, and wall time alongside every `reported` value.
    Stdout and stderr go to files, not memory. Warmup, then rounds with
    in-round order drawn from a recorded seed.
  - Done when: each metric is tested against a real child process;
    runner overhead per invocation is measured against a bare
    `subprocess.run` of the same command and reported in the PR.
  - Effort: high

- [ ] **T2.4 Correctness gate**
  - Owns: `src/perfproof/bench/correctness.py`,
    `tests/bench/test_correctness.py`
  - Needs: T0.3
  - Do: run the spec's test command on the baseline three times first
    and quarantine any test whose result changes (flaky), per
    "Correctness" in `docs/method.md`; then run it in the candidate
    tree. Compare declared outputs between trees as bytes, text, or
    numbers with `rtol` and `atol` (text numbers and `.npy` files).
    Return the first differences, capped.
  - Done when: tests cover equal, unequal, within-tolerance, NaN
    handling, a missing output file, a flaky test quarantined rather
    than blamed on the candidate, and a test that fails on all three
    baseline runs (the baseline itself is broken: the run stops).
  - Effort: medium

- [ ] **T2.5 Compare**
  - Owns: `src/perfproof/bench/compare.py`,
    `tests/bench/test_compare.py`
  - Needs: M1 exit, T2.1, T2.2, T2.3, T2.4, T2.13, T2.14, T2.15,
    T2.16
  - Do: gates, then interleaved rounds per the look schedule, then the
    sequential decision, then a `RunRecord` with samples saved to
    `samples.npz`. Re-check the procedure and same-host gates on the
    finished samples. For a `reported` metric, compute the wall-clock
    interval too and set the `metric_disagreement` diagnostic per
    `docs/method.md`. Decides through T1.3's function and builds
    cross-host evidence by T1.3's rules. Uses the procedure fixed by
    T1.6 and T1.7.
  - Done when: an end-to-end test on a temporary repo with a sleep-based
    benchmark returns, with the right reason field each time: KEEP
    (`performance`) for a large speedup, REJECT (`performance`) for no
    change, REJECT (`integrity`, `lock`) for a locked-file edit, REJECT
    (`correctness`) for wrong output, and INCONCLUSIVE
    (`insufficient_evidence`) when the candidate is faster but the
    budget is too small to show it clears the bar.
  - Effort: high

- [ ] **T2.6 Report**
  - Owns: `src/perfproof/report/`, `tests/report/`
  - Needs: T0.3
  - Do: write `record.json` and render `report.md` from a `RunRecord`:
    verdict and what it means first, then interval, rounds, gates,
    calibration status, warnings, machine, links to samples and
    profiles. Separate "claim" from "measured".
  - Done when: snapshot tests on records built in the test, one per
    outcome and one per gate failure; rendering the same `record.json`
    twice, in different processes and time zones, gives byte-identical
    output. Rendering reads no clock and no environment; every
    timestamp comes from the record; ordering and number formatting are
    fixed.
  - Effort: medium

- [ ] **T2.7 Examples and decoys**
  - Owns: `examples/`
  - Needs: none
  - Do: small Python programs, each with a benchmark command, a
    correctness check, a profiler-visible bottleneck, and a real fix: a
    pure-Python loop that NumPy vectorizes, and a quadratic membership
    check that a set fixes. Each example also ships decoy candidates
    that must not be kept: one edits the benchmark, one returns wrong
    output, one is a no-op refactor, one is genuinely but only
    slightly faster (about 1.5%) so its evidence can't clear the bar,
    one is faster but changes a branch no test reaches, and one is
    faster by caching everything at several times the memory. One
    example's suite includes a deliberately flaky test, and one spec
    points `DATABASE_URL` at a non-local host.
    Add a C or Rust kernel with a cache-unfriendly access pattern for
    M4.
  - Done when: each example's test passes before and after its fix, and
    fails for the wrong-output decoy; `examples/README.md` says what
    each one and each decoy demonstrates.
  - Effort: medium

- [ ] **T2.9 Profiler adapter base**
  - Owns: `src/perfproof/profilers/base.py`,
    `tests/profilers/test_base.py`
  - Needs: T0.3
  - Do: the adapter protocol, a bounded top-N heap, frame
    normalization helpers, per `src/perfproof/profilers/AGENTS.md`.
  - Effort: medium

- [ ] **T2.10 py-spy adapter**
  - Owns: `src/perfproof/profilers/pyspy.py`,
    `tests/profilers/test_pyspy.py`, `tests/fixtures/py-spy/`
  - Needs: T2.7, T2.9
  - Do: adapter per the contract, fixtures captured from `examples/`.
  - Done when: parser tests on captured fixtures; an integration test
    runs py-spy on each Python example and finds its known bottleneck
    in the top 3; parse time and peak memory on the largest fixture in
    the PR.
  - Effort: medium

- [ ] **T2.11 Real noise traces, CPU**
  - Owns: `tests/calibration/traces/`,
    `tests/calibration/test_real_traces.py`, the CPU rows of the real
    noise table in `docs/method.md`
  - Needs: M1 exit, T2.3, T2.7
  - Do: record long A/A traces (one example's baseline against itself,
    several hundred rounds) on this Mac for development and on a Linux
    CI runner for the held-out audit. Compare the Mac traces' CV, tails
    and autocorrelation with the synthetic scenarios; calibrate by
    block bootstrap of the traces with an injected speedup, at the true
    speedups T1.6 uses, under the same acceptance rule. The
    `stats-auditor` runs the Linux traces.
  - Done when: the real-trace results are in `docs/method.md` next to
    the synthetic ones, and the CPU Python target class is marked
    calibrated or the gap is written up as a task.
  - Effort: high

- [ ] **T2.12 Schema for the safety checks**
  - Owns: `src/perfproof/schema.py`, `src/perfproof/schema.json`,
    `tests/test_schema.py`, the gate list and precedence in
    `src/perfproof/stats/decision.py` and its tests, `pyproject.toml`
    and `uv.lock` (adds `coverage` and moves `hypothesis` to runtime
    dependencies, so T2.13 and T2.14 never touch the lockfile)
  - Needs: T1.3 merged
  - Do: the schema and decision changes `docs/method.md` now needs:
    gates `side_effects` and `coverage`, with the precedence
    `side_effects`, `lock`, `procedure`, `runtime`, `same_host`,
    `correctness`, `coverage`; reason `guardrail` for a REJECT that
    keeps the speedup estimate; guardrail specs (metric, allowance) and
    results (interval, breached); the opt-ins for remote services and
    for targets without coverage; a reset command; quarantined flaky
    tests; differential-check results (calls captured, replayed,
    skipped with reasons, first differences). One task, because the
    schema is the shared contract.
  - Done when: round-trip and refusal tests for every new field; a
    guardrail REJECT validates only with its intervals present; the
    JSON Schema drift test passes; all existing tests still pass.
  - Effort: high

- [ ] **T2.13 Differential checks**
  - Owns: `src/perfproof/bench/differential.py`,
    `tests/bench/test_differential.py`
  - Needs: T2.12, T2.3
  - Do: per "Differential checks" in `docs/method.md`, for Python:
    capture up to a fixed number of real calls to each changed
    function while the baseline benchmark runs, replay each against
    the candidate in a separate process and compare results; run
    Hypothesis-generated inputs through both versions where type hints
    allow; count and explain every call that couldn't be captured.
    Capture adds overhead, so it runs in its own pass, never during a
    measured round.
  - Done when: tests show a changed function with a subtle bug (wrong
    on one input in fifty) caught by replayed real calls, an
    unpicklable argument skipped with its reason, and no capture code
    running during measured rounds.
  - Effort: high

- [ ] **T2.14 Coverage gate**
  - Owns: `src/perfproof/bench/coverage_gate.py`,
    `tests/bench/test_coverage_gate.py`
  - Needs: T2.12, T2.4, T2.13
  - Do: changed executable lines from `git diff` between the trees;
    run the correctness command and the differential checks under
    coverage.py; fail the `coverage` gate, listing the unverified
    lines, when any changed line didn't run. Lines only quarantined
    flaky tests reached count as unverified. Non-Python changes report
    coverage as not checked, which needs the spec's opt-in for a KEEP.
  - Done when: tests cover a fully covered change, a change in a branch
    no test reaches, a change reached only by a quarantined test, a
    deleted line, and a non-Python change without the opt-in.
  - Effort: high

- [ ] **T2.15 Guardrails**
  - Owns: `src/perfproof/stats/guardrails.py`,
    `tests/stats/test_guardrails.py`, the guardrail step in
    `src/perfproof/stats/decision.py`, guardrail cells in
    `tests/calibration/`
  - Needs: T2.12, T1.6
  - Do: per "Guardrails" in `docs/method.md`: a paired interval on each
    guardrail's worsening ratio at confidence `1 - alpha / (K G)`, the
    breach rule, and the REJECT with reason `guardrail` that blocks a
    KEEP. Peak memory is always a guardrail.
  - Done when: tests cover a breach blocking a KEEP, an unresolved
    guardrail not blocking, and a breach alongside a performance REJECT
    or INCONCLUSIVE leaving that verdict alone; calibration shows the
    false-block rate for guardrails within their allowances passes the
    acceptance rule in `docs/method.md`, reported per cell.
  - Effort: high

- [ ] **T2.16 Side-effect guard**
  - Owns: `src/perfproof/bench/side_effects.py`,
    `tests/bench/test_side_effects.py`
  - Needs: T2.12
  - Do: the preflight in "Side effects" in `docs/method.md`: refuse a
    run whose spec or process environment points a service address at
    a host that isn't local, unless the spec opts in; run the spec's
    reset command before every execution.
  - Done when: tests cover each variable pattern, local and non-local
    hosts, IPv6, Unix sockets, the opt-in, and a reset command that
    fails (the run stops, with its output in the record).
  - Effort: medium

- [ ] **T2.8 Local loop end to end**
  - Owns: `tests/e2e/`, the A/A section of `docs/method.md`
  - Needs: T2.5, T2.6, T2.10, T2.11, T2.13, T2.14, T2.15, T2.16
  - Do: for each Python example, through the engine's Python API:
    define and lock the benchmark, snapshot the baseline, profile it,
    apply the fix, check correctness, compare, write the report. Then
    run every decoy, and the same commit against itself.
  - Done when, on this Mac and on Linux CI: the profile ranks the known
    bottleneck in the top 3; every fix returns KEEP; the benchmark-edit
    decoy is REJECTed by the lock gate, the wrong-output decoy by the
    correctness gate or the differential checks, the untested-branch
    decoy by the coverage gate, the memory decoy by the peak-memory
    guardrail, the remote-database spec is refused before anything
    runs, the flaky test is quarantined without blaming any candidate,
    the no-op decoy is never KEEP, and the
    slightly-faster decoy ends INCONCLUSIVE or REJECT, never KEEP, with
    its outcome counts reported; the report regenerated from
    `record.json` is byte-identical to the original; A/A never
    returns KEEP across at least 50 comparisons per machine, with the
    outcome counts reported. Results and machines are written into
    `docs/method.md`.
  - Effort: high

## M3 Agent interfaces

Starts after the M2 exit. Exposes prove to agents and makes long runs
safe to start from any client.

- [ ] **T3.1 CLI**
  - Owns: `src/perfproof/cli.py`, `src/perfproof/__main__.py`,
    `tests/test_cli.py`, `pyproject.toml` (script entry)
  - Needs: M2 exit
  - Do: argparse subcommands mirroring the MCP tools. JSON output with
    `--json`. Later tasks add their own subcommands.
  - Done when: each subcommand is tested through `main(argv)`.
  - Effort: medium

- [ ] **T3.2 Background jobs**
  - Owns: `src/perfproof/jobs/`, `tests/jobs/`
  - Needs: M2 exit
  - Do: long `measure`, `profile`, `discover` and `compare` calls run as
    jobs: the call returns a job ID at once, progress is written to
    `.perfproof/jobs/<id>.json`, and `job` reports status, progress and
    result. Completed rounds are on disk, so a killed job resumes
    without redoing them, and the record marks where it resumed. Every
    job has a hard total timeout; cancel and timeout kill the whole
    process tree.
  - Done when: a compare that outlasts a 60-second client timeout
    finishes through polling; `kill -9` mid-run resumes with no round
    lost or repeated and a valid record; cancel leaves no child
    process behind.
  - Effort: high

- [ ] **T3.3 MCP server**
  - Owns: `src/perfproof/mcp/`, `tests/mcp/`, the `mcp` subcommand in
    `cli.py`, `pyproject.toml`, `uv.lock` (adds `mcp`)
  - Needs: T3.1, T3.2
  - Do: stdio server with the tools in `docs/architecture.md`, built on
    the official Python SDK (check its current API before starting).
    Short tool descriptions, expressive typed parameters, compact JSON
    results with paths to raw data. Every call that can outlast a
    client's tool timeout returns a job ID.
  - Done when: an in-process MCP client test calls every tool; a real
    `uvx --from . perfproof mcp` subprocess answers `tools/list`.
  - Effort: high

- [ ] **T3.4 Client setup, verified**
  - Owns: `docs/install.md`
  - Needs: T3.3, open question 4
  - Do: setup for Claude Code (`claude mcp add`), Codex
    (`codex mcp add` and `.codex/config.toml`), and a generic MCP JSON
    config. Verify each by starting the client and listing perfproof's
    tools.
  - Done when: every instruction in the page was run, and the page says
    which client versions it was checked with.
  - Effort: medium

- [ ] **T3.5 Skill packaging**
  - Owns: `src/perfproof/skills.py`, `tests/test_skills.py`, the
    `install-skills` subcommand in `cli.py`, the wheel's package data
  - Needs: T3.1
  - Do: `perfproof install-skills --client claude|codex|<dir>` copies
    the packaged skills (the `/perfproof` loop and every target
    playbook) into place. `skills/` ships inside the wheel.
  - Done when: install tested into temporary directories for each
    client layout, including an update over an older install.
  - Effort: medium

- [ ] **T3.6 Evidence planning and early INCONCLUSIVE**
  - Owns: `src/perfproof/stats/planning.py`,
    `tests/stats/test_planning.py`, its cells in `tests/calibration/`
  - Needs: M1 exit
  - Do: after the first look, estimate the rounds a decision needs at
    the observed noise. When that is beyond the budget, stop at a
    scheduled look with INCONCLUSIVE and say what would settle it: more
    rounds and their time, a quieter metric (`counter` on Linux), a
    quieter machine, or a larger input. The stop only replaces
    "continue" at a scheduled look; looks and their confidence never
    change.
  - Done when: predicted rounds agree with realised rounds in
    simulation within a stated tolerance; calibration with the early
    stop on passes the acceptance rule in every cell, reporting both
    the unconditional rates and the conditional rate among verdicts;
    every early INCONCLUSIVE report names its estimate and options.
  - Effort: high

## M4 Understand

Starts after T3.3. Builds the project model in `docs/discovery.md`.

- [ ] **T4.0 Schema for understand and discover**
  - Owns: `src/perfproof/schema.py`, `src/perfproof/schema.json`,
    `tests/test_schema.py`
  - Needs: M2 exit
  - Do: models for the project model (targets, workloads, goals,
    constraints, how to run, the files each entry was derived from),
    opportunities (angle, evidence, cap and its derivation), hypotheses
    (every field in `docs/discovery.md`), cheap-test results, and jobs.
    One task, because the schema is the shared contract.
  - Done when: round-trip and refusal tests for every new model; a cap
    can't be stored in a speedup field; the drift test passes.
  - Effort: high

- [ ] **T4.1 Project model**
  - Owns: `src/perfproof/understand/`, `tests/understand/`, the
    `project` MCP tool and CLI subcommand
  - Needs: T4.0, T3.3
  - Do: `.perfproof/project.json` per "Understand" in
    `docs/discovery.md`: perfproof proposes, the developer confirms;
    unknown fields stay empty and are asked about; entries record the
    files they came from and are flagged when those change; switching
    goals reuses the model.
  - Done when: tests cover propose and confirm, a refused proposal, an
    unknown field asked rather than filled, staleness after an edit,
    and a goal switch that keeps workloads and constraints.
  - Effort: high

- [ ] **T4.2 Target detection and workload finding**
  - Owns: `src/perfproof/targets/detect.py`,
    `src/perfproof/targets/workloads.py`, `tests/targets/`, the
    `detect_target` MCP tool
  - Needs: T4.0
  - Do: detect all six target classes from manifests, imports and
    configs, each with its evidence and a confidence. Find candidate
    workloads: existing benchmark suites (pytest-benchmark, asv,
    criterion, Google Benchmark), the slowest tests, entry points and
    console scripts, web routes, serving configs, agent entry points.
  - Done when: correct on `examples/`, on a small synthetic tree per
    class including ambiguous mixes, and, read-only, on frames2py and
    picolm.
  - Effort: high

## M5 Discover

Starts after T4.0. Builds the discovery side of `docs/discovery.md`.

- [ ] **T5.1 Discover engine and measurement angles**
  - Owns: `src/perfproof/discover/` (except the files below),
    `tests/discover/`, the `discover` MCP tool and CLI subcommand
  - Needs: T4.0, T2.10, T2.13
  - Do: ranked opportunities from every angle in `docs/discovery.md`.
    Each angle is a source with an availability check, so an angle
    whose adapter isn't built yet reports "not available" with the
    reason instead of disappearing. The angles buildable now: CPU
    hotspots, waiting versus computing, repeated identical calls,
    growth with input size, startup and imports. The others plug in
    from their M6 tracks. Every opportunity carries its evidence and
    its cap with the derivation, labelled as a bound.
  - Done when: each buildable angle finds the case planted for it in
    `examples/` (added in this task); the list is ranked by cap; unbuilt
    angles report themselves unavailable.
  - Effort: high

- [ ] **T5.2 Hypotheses log**
  - Owns: `src/perfproof/hypotheses.py`, `tests/test_hypotheses.py`, the
    `hypothesis` MCP tool
  - Needs: T4.0
  - Do: the append-only `.perfproof/hypotheses/<workload>.jsonl` from
    `docs/discovery.md`. `discover` attaches matching past hypotheses
    to each opportunity, marking those made on an older version of the
    code. The code finds and shows history; the agent judges repeats.
  - Done when: tests cover appending, matching after the function moves
    within its file, a hypothesis on changed source marked as older, a
    corrupt line skipped with a warning rather than losing the file,
    and two sessions appending at once.
  - Effort: medium

- [ ] **T5.3 Cheap tests**
  - Owns: `src/perfproof/discover/experiments.py`,
    `tests/discover/test_experiments.py`
  - Needs: T5.2, T2.13
  - Do: the cheap tests in `docs/discovery.md`: time one stage; count
    calls, queries or allocations; microbenchmark an alternative in
    isolation; bound a function's cost by replaying it from captured
    calls instead of running it ("what if this were free?"); toggle one
    setting. Results are stored on the hypothesis as evidence, never as
    a verdict.
  - Done when: each kind runs on a planted example; on every example,
    the "what if free" bound is at least the speedup its real fix gets
    (measured), so it holds as an upper bound there.
  - Effort: high

- [ ] **T5.4 Choose, then confirm**
  - Owns: `src/perfproof/discover/search.py`,
    `tests/discover/test_search.py`
  - Needs: T5.2
  - Do: up to `k` candidates for one hypothesis (default 3), each in its
    own worktree from the same baseline, measured one at a time, never
    concurrently. Every candidate goes through the gates and `compare`.
    The best KEEP is chosen, then confirmed by a fresh, independent
    `compare`; only that confirmation is claimed.
  - Done when: on an example with fixes of known different strength,
    the strongest is chosen and confirmed; with `k` candidates and no
    real speedup, the confirmed false-KEEP rate passes the acceptance
    rule in simulation; selection-stage verdicts never appear as claims.
  - Effort: high

- [ ] **T5.5 Settings search**
  - Owns: `src/perfproof/discover/settings.py`,
    `tests/discover/test_settings.py`
  - Needs: T5.4
  - Do: search a settings space (declared by the target module or the
    spec: threads, batch sizes, flags, backends, compile options) by
    screening one setting at a time, then trying small combinations of
    the promising ones, within a budget. Every setting tried is a
    candidate under T5.4's rule.
  - Done when: on an example with a planted setting win, it is found
    and confirmed; with many settings and no real effect, the confirmed
    false-KEEP rate passes the acceptance rule in simulation.
  - Effort: high

- [ ] **T5.6 explain_hotspot**
  - Owns: `src/perfproof/profilers/explain.py`,
    `tests/profilers/test_explain.py`, the `explain_hotspot` MCP tool
  - Needs: T2.10
  - Do: the source excerpt and per-line profile detail for one hotspot.
  - Effort: medium

- [ ] **T5.7 The /perfproof loop**
  - Owns: `skills/perfproof/`, `src/perfproof/optimize.py`,
    `tests/test_optimize.py`, the `optimize` subcommand in `cli.py`
  - Needs: T3.5, T4.1, T4.2, T5.1, T5.2, T5.3, T5.4
  - Do: the skill a user starts with `/perfproof` (Claude Code) or
    `$perfproof` (Codex), and the engine code that holds its state. The
    agent decides what to try; deterministic code owns the cycle,
    budgets, kept changes and undo.
    1. **Understand**: load or build the project model; the developer
       confirms workloads, goal and constraints.
    2. **Discover**: rank opportunities, each with its evidence, cap and
       past hypotheses. The agent turns the top ones into hypotheses
       and runs their cheap tests.
    3. **Try**: for the best surviving hypothesis, the agent writes one
       candidate, or several through T5.4, or a settings search through
       T5.5, using the target's playbook.
    4. **Prove**: `compare` against the last kept state. A KEEP becomes
       a commit on a `perfproof/<session>` branch; anything else is
       undone, file by file, for the files that attempt changed. Every
       hypothesis and verdict goes into the log.
    5. **Repeat**, re-running discover after each KEEP, until the budget
       (time, attempts, opportunities) runs out or the largest
       remaining cap is below the bar.
    6. Compare the original baseline with the final stacked state once
       more, and write one report of every hypothesis, test, candidate
       and verdict.
    It refuses a dirty working tree unless the developer says how to
    set those changes aside, never discards a change it didn't make,
    runs every long step as a job, and resumes after a kill.
  - Done when: on every example, a fresh Claude Code session with only
    perfproof's MCP server and skills runs `/perfproof` to the end with
    no input after the confirmation: it finds the planted
    opportunities, keeps the real fixes, refuses the decoys, ends with
    a combined verdict consistent with the per-step ones, and leaves
    pre-existing uncommitted work untouched. Tests cover undo, resume
    after a kill, every stop condition, and a dirty tree.
  - Effort: max

- [ ] **T5.8 Agent in the loop**
  - Owns: `tests/agents/`, `docs/case-studies/examples.md`
  - Needs: T3.4, T5.7
  - Do: fresh Claude Code and Codex sessions with only perfproof's MCP
    server and skills run `/perfproof` (or `$perfproof`) on every
    example, each also handed a decoy. Record the verdicts, the tool
    calls, and where the agent went wrong.
  - Done when: both clients reach the correct verdict on every example
    and decoy, or the failures are written up and turned into tasks.
  - Effort: high

## M6 Target tracks

Starts after the M2 exit. The six tracks run in parallel. Each ends
with a case study on a real repository through `/perfproof`, which is
that class's release test: the project model confirmed by Faras, at
least one hypothesis tested with a cheap test, every candidate's
verdict recorded, and either a verified improvement or a written
account of what was tried and why nothing was kept. A class is claimed
in the release only when its case study and its real-trace calibration
both pass. Every paid step goes through the `gpu-run` skill. Adapters
follow `src/perfproof/profilers/AGENTS.md`; playbooks follow
`skills/AGENTS.md`.

Each track's tasks own `src/perfproof/targets/<class>.py` (or the
adapter files named), their tests and fixtures, and their playbook
under `skills/perfproof-<class>/`.

### Python

- [ ] **T6.1 Scalene and memray adapters.** Needs M2 exit. Done when
  parser tests pass on captured fixtures and each finds the planted
  hotspot or allocation churn in the top 3; memray feeds the
  allocation angle.
- [ ] **T6.2 Python playbook.** Needs T6.1, T5.1. Done when an agent
  with no other context gets a correct verdict on every Python example
  with it.
- [ ] **T6.3 Case study: frames2py.** Needs T5.7, T6.2. Effort: max.

### Native

- [ ] **T6.4 samply and perf adapters** (perf on Linux). Needs M2 exit.
- [ ] **T6.5 Native coverage** with llvm-cov or gcov, so native changes
  stop needing the "coverage not checked" opt-in. Needs T2.14.
- [ ] **T6.6 Native noise traces**: A/A on this Mac (development) and
  Linux CI (held out), calibrated as in T2.11. Needs T6.4.
- [ ] **T6.7 Native playbook.** Needs T6.4, T5.1.
- [ ] **T6.8 Case study: picolm.** Greedy fixed-seed decoding must give
  identical tokens, or the tolerance is declared in the spec. Its
  remote is upstream `RightNow-AI/picolm`: any upstream PR is Faras's
  call. Needs T5.7, T6.5, T6.7. Effort: max.

### Web and CRUD services

- [ ] **T6.9 Service workload runner**: start the server, wait for its
  health check, warm up, drive load at a constant request rate (open
  loop, so a slow response can't hide by delaying the next request),
  record p50, p99 and throughput per round, run the reset command
  between rounds, with the database in a local container. The request
  mix comes from recorded traffic or a mix the developer confirms;
  perfproof never claims a generated load is representative. Needs
  T2.16. Effort: high.
- [ ] **T6.10 Database and serialization angles**: queries counted per
  request (driver hooks or query logs), N+1 patterns, serialization
  stage timing, connection-pool waits. Needs T6.9, T5.1.
- [ ] **T6.11 Service noise traces**, calibrated as in T2.11. Needs T6.9.
- [ ] **T6.12 Web playbook.** Needs T6.10.
- [ ] **T6.13 Case study: a real CRUD app** (open question 1). Needs
  T5.7, T6.12. Effort: max.

### LLM serving

- [ ] **T6.14 SSH runner**: sync both trees, run the engine remotely,
  pull the record back. Needs open question 3.
- [ ] **T6.15 Modal runner**: one sandbox runs baseline and candidate
  interleaved; image chosen per benchmark; volumes for caches; fail
  fast with log tail. Cross-host sessions record fingerprints and count
  pairs by T1.3's rules. Paid.
- [ ] **T6.16 GPU timing and fingerprint**: CUDA event timing helper for
  `reported` benchmarks, GPU clocks and throttle reasons, optional
  clock locking where permitted.
- [ ] **T6.17 Serving workload and settings space**: `vllm bench serve`
  and SGLang bench wrappers with `/metrics` counters, building on the
  reference scripts; a declared settings space (batch limits,
  scheduling, KV-cache and prefix-caching options, attention backend,
  quantization) for T5.5. Comparing versions that need separate images
  uses the cross-machine session design, opted in and labelled. Needs
  T6.15, T5.5.
- [ ] **T6.18 Serving noise traces**: paid A/A on one GPU type, a second
  session held out. Needs T6.17. Paid.
- [ ] **T6.19 Serving playbook.** Needs T6.17.
- [ ] **T6.20 Case study: vLLM**: re-run the prefix-caching comparison
  against the hand-run numbers, then a full `/perfproof` pass for new
  opportunities. Needs T5.7, T6.19. Paid. Effort: max.

### GPU kernels and PyTorch

- [ ] **T6.21 torch.profiler adapter.** Needs M2 exit.
- [ ] **T6.22 Nsight Systems and Nsight Compute adapters**: kernel
  summaries from `nsys stats`, per-kernel metrics from `ncu --csv`.
  Fixtures captured in one planned paid session. Needs T6.15. Paid.
- [ ] **T6.23 GPU angles**: idle time, launch overhead, occupancy,
  memory throughput, host-to-device copies. Needs T6.22, T5.1.
- [ ] **T6.24 Kernel noise traces**: paid A/A, a second session held
  out. Needs T6.16. Paid.
- [ ] **T6.25 GPU and PyTorch playbook.** Needs T6.23.
- [ ] **T6.26 Case study: a real GPU kernel or PyTorch workload** (open
  question 1). Needs T5.7, T6.25. Paid. Effort: max.

### Agentic and LLM apps

- [ ] **T6.27 Record and replay**: record an app's model and tool calls
  once, replay them so the workload measures the app's own code with no
  API cost, rate limits or network noise. Replays are checked against a
  live sample so they stay faithful, and a replay that diverges stops
  the run. Needs T2.16. Effort: high.
- [ ] **T6.28 Agentic angles**: wasted or repeated model calls,
  sequential calls that could run in parallel, prompt-caching
  opportunities, token and payload size, retries. Needs T6.27, T5.1.
- [ ] **T6.29 Agentic noise traces.** Needs T6.27.
- [ ] **T6.30 Agentic playbook.** Needs T6.28.
- [ ] **T6.31 Case study: a real agentic app** (open question 1). Needs
  T5.7, T6.30. Effort: max.

## M7 Release

- [ ] **T7.1 Release bar** (lead): for every target class, its case
  study and real-trace calibration passed, or the class is not
  claimed; Claude Code and Codex verified (T3.4, T5.8); every README
  claim links to the run that backs it.
- [ ] **T7.2** README with the case-study numbers, demo recording, docs
  pass, fresh prior-art check.
- [ ] **T7.3** Release: version, changelog, PyPI publish (Faras), repo
  public (Faras).
