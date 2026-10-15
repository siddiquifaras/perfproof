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
| Runtime dependencies | `numpy` for the stats core. `pydantic` for the schema; the MCP SDK requires `pydantic>=2.12` anyway, so it adds nothing to the install. `scipy` for the t quantiles of the primary procedure and the Wilcoxon challenger. `mcp` arrives with T3.2. Test and lint tools stay in the `dev` group. |
| Entry point | `perfproof mcp` starts the MCP server; MCP clients launch `uvx perfproof mcp` |
| Remote | `host` argument on `measure`, `profile` and `compare`; the remote side runs the same engine |
| Local-only files | `.claude/local/` (handoff, budget) and `CLAUDE.local.md`, gitignored, copied into worktrees by `.worktreeinclude`. Nothing needed to build, test or run perfproof lives there. |
| Client support claims | A client is named as supported only after T3.3 and T3.5 pass with it |

## Open questions

Ask Faras; don't decide silently.

1. Libraries for case studies 2 and 3 (a NumPy/Python library with open
   "slow" issues and its own benchmarks; a well-known PyTorch training
   script).
2. How to split the remaining GPU budget across T5.2, T5.5, T5.6 and the
   case studies.
3. A host for testing the SSH runner (T5.1): Remote Login to this Mac,
   a cheap Linux VM, or a Modal sandbox with sshd.
4. Installing Codex locally (standalone binary, no Homebrew) for T0.4
   and T3.3.

Repository admin (Faras): make the CI checks required on `main` so a red
PR can't merge. T0.2 delivered the checks; enforcing them is a setting.

## Not scheduled yet

Real needs, deliberately kept out of the path to the first working
loop. Each gets a task with its own acceptance test when its
prerequisite lands. None of them may loosen the decision rule or the
calibration criteria.

| Capability | Waits for | Note |
|---|---|---|
| Sample-size estimate and early INCONCLUSIVE | T1.6 | After pilot rounds, estimate the rounds a decision needs and say what would help. An early stop may only turn "continue" into INCONCLUSIVE at a scheduled look, never change the looks or their confidence. Calibrated as part of the procedure, reporting both the unconditional error rates the acceptance rule checks and the conditional rate among verdicts, as `docs/method.md` defines. |
| No-regression mode | T1.7 | "Is it no more than m slower?" as a signed `min_effect`. Needs a schema task if `min_effect` must go negative. |
| Background execution | T3.2 design | Long `measure`, `profile` and `compare` calls as jobs with progress and resume, so MCP clients never time out mid-run. Decided in T3.2's design, built when needed. |
| Web-service target | M2 exit | Start, health check, warm-up, constant-rate load, latency percentiles and throughput per round, database reset between rounds. vLLM serving becomes a preset. Acceptance: a real CRUD app end to end, with the bottleneck layer identified, and no claim that a generic load is representative. |
| CPU pinning and isolation | T2.3 | Linux only, where permissions allow; never a requirement. |

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
                 T2.10 (after T2.7, T2.9)├─ T2.11 real traces
                                         └─ T2.8 local loop ══ M2 exit
                                                    │
M3  T3.1 ─ T3.2 ─ T3.3 ─ T3.4 ─ T3.6 ─ T3.5, T3.7 ◄─┤
M4  T4.2 … T4.6 (parallel), T4.8, T4.9 ◄────────────┤
M5  T5.1 T5.2 T5.3 ─ T5.4 ─ T5.5, T5.6 ◄────────────┘
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
  - Do: run the spec's test command in the candidate tree; compare
    declared outputs between trees as bytes, text, or numbers with
    `rtol` and `atol` (text numbers and `.npy` files). Return the first
    differences, capped.
  - Done when: tests cover equal, unequal, within-tolerance, NaN
    handling, and a missing output file.
  - Effort: medium

- [ ] **T2.5 Compare**
  - Owns: `src/perfproof/bench/compare.py`,
    `tests/bench/test_compare.py`
  - Needs: M1 exit, T2.1, T2.2, T2.3, T2.4
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
    output, one is a no-op refactor, and one is genuinely but only
    slightly faster (about 1.5%), so its evidence can't clear the bar.
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

- [ ] **T2.8 Local loop end to end**
  - Owns: `tests/e2e/`, the A/A section of `docs/method.md`
  - Needs: T2.5, T2.6, T2.10, T2.11
  - Do: for each Python example, through the engine's Python API:
    define and lock the benchmark, snapshot the baseline, profile it,
    apply the fix, check correctness, compare, write the report. Then
    run every decoy, and the same commit against itself.
  - Done when, on this Mac and on Linux CI: the profile ranks the known
    bottleneck in the top 3; every fix returns KEEP; the benchmark-edit
    decoy is REJECTed by the lock gate, the wrong-output decoy by the
    correctness gate, the no-op decoy is never KEEP, and the
    slightly-faster decoy ends INCONCLUSIVE or REJECT, never KEEP, with
    its outcome counts reported; the report regenerated from
    `record.json` is byte-identical to the original; A/A never
    returns KEEP across at least 50 comparisons per machine, with the
    outcome counts reported. Results and machines are written into
    `docs/method.md`.
  - Effort: high

## M3 Agent interfaces

Starts after the M2 exit.

- [ ] **T3.1 CLI**
  - Owns: `src/perfproof/cli.py`, `src/perfproof/__main__.py`,
    `tests/test_cli.py`, `pyproject.toml` (script entry)
  - Needs: M2 exit
  - Do: argparse subcommands mirroring the MCP tools. JSON output with
    `--json`. The `mcp` and `install-skills` subcommands come with
    T3.2 and T3.4.
  - Done when: each subcommand is tested through `main(argv)`.
  - Effort: medium

- [ ] **T3.2 MCP server**
  - Owns: `src/perfproof/mcp/`, `tests/mcp/`, the `mcp` subcommand in
    `cli.py`, `pyproject.toml`, `uv.lock` (adds `mcp`)
  - Needs: T3.1
  - Do: stdio server with the tools in `docs/architecture.md`, built on
    the official Python SDK (check its current API before starting).
    Short tool descriptions, expressive typed parameters, compact JSON
    results with paths to raw data. Decide, before writing tools, how
    calls longer than a client's tool timeout behave (see background
    execution under "Not scheduled yet"), and write the decision into
    `docs/architecture.md`.
  - Done when: an in-process MCP client test calls every tool; a real
    `uvx --from . perfproof mcp` subprocess answers `tools/list`.
  - Effort: high

- [ ] **T3.3 Client setup, verified**
  - Owns: `docs/install.md`
  - Needs: T3.2, open question 4
  - Do: setup for Claude Code (`claude mcp add`), Codex
    (`codex mcp add` and `.codex/config.toml`), and a generic MCP JSON
    config. Verify each by starting the client and listing perfproof's
    tools.
  - Done when: every instruction in the page was run, and the page says
    which client versions it was checked with.
  - Effort: medium

- [ ] **T3.4 Python playbook and installer**
  - Owns: `skills/perfproof-python/`, `src/perfproof/skills.py`,
    `tests/test_skills.py`, the `install-skills` subcommand in `cli.py`
  - Needs: T3.1
  - Do: the Python skill per `skills/AGENTS.md`, and
    `perfproof install-skills --client claude|codex|<dir>` that copies
    packaged skills into place. Ship `skills/` inside the wheel.
  - Done when: install tested into temporary directories for each
    client layout; skill check from `skills/AGENTS.md` done.
  - Effort: medium

- [ ] **T3.6 One-command optimize loop**
  - Owns: `skills/perfproof/`, `src/perfproof/optimize.py`,
    `tests/test_optimize.py`, the `optimize` subcommand in `cli.py`
  - Needs: T3.2, T3.4
  - Do: the skill a user starts with `/perfproof` (Claude Code) or
    `$perfproof` (Codex), and the engine code that holds its state.
    The model decides what to try; deterministic code owns everything
    else: which hotspots were tried, which changes were kept, budgets,
    and undoing.
    1. Detect the project and find or propose a benchmark; the user
       confirms it before anything is measured.
    2. Profile the baseline and rank hotspots by the most a fix could
       gain (Amdahl's law: a share `p` of runtime caps the speedup at
       `1 / (1 - p)`).
    3. For the top hotspot, the agent makes one change using the
       target's playbook.
    4. `compare` against the last kept state. KEEP becomes a commit on
       a `perfproof/<session>` branch; anything else is undone, file
       by file, for the files that attempt changed.
    5. Move to the next hotspot, re-profiling after each KEEP. Stop when
       the budget (time, attempts, hotspots) runs out, or when the
       largest remaining hotspot's cap is below the bar.
    6. Compare the original baseline against the final stacked state
       once more, and write one report listing every attempt, its
       verdict and its evidence.
    It refuses to start on a dirty working tree unless the user says
    how to set those changes aside, and it never discards a change it
    didn't make. Every step has a timeout, and progress is written to
    disk so an interrupted session can resume.
  - Done when: on every example, a fresh Claude Code session given only
    perfproof's MCP server and skills runs `/perfproof` to the end with
    no further input after confirming the benchmark: it finds the
    planted bottleneck, keeps the real fix, ends with a combined
    verdict that matches the per-step ones, and leaves pre-existing
    uncommitted work untouched. Tests cover undo, resume after a kill,
    every stop condition, and a dirty tree.
  - Effort: high

- [ ] **T3.7 Several candidate fixes per hotspot**
  - Owns: `src/perfproof/search.py`, `tests/test_search.py`, the
    candidate step of `skills/perfproof/`
  - Needs: T3.6
  - Do: for one hotspot, the agent writes up to `k` candidates (default
    3) that take different approaches, each in its own worktree from
    the same baseline. Candidates are measured one at a time, never
    concurrently, so they don't perturb each other. Every candidate
    goes through the gates and `compare`. Picking the best of `k` noisy
    estimates overstates the winner, so the choice and the claim are
    separate: the candidate with the highest estimate among the KEEPs
    is chosen, then confirmed by a fresh, independent `compare` against
    the baseline. Only that confirmation run's verdict is reported and
    kept, so its guarantees are the standard ones.
  - Done when: on an example with several fixes of known different
    strength, the strongest is chosen and confirmed; a simulation of
    `k` candidates with no real speedup shows the confirmed false-KEEP
    rate within the procedure's limit under the acceptance rule in
    `docs/method.md`; and the selection-stage verdicts never appear in
    the final report as claims.
  - Effort: high

- [ ] **T3.5 Agent in the loop**
  - Owns: `tests/agents/`, `docs/case-studies/examples.md`
  - Needs: T3.3, T3.6
  - Do: fresh Claude Code and Codex sessions with only perfproof's MCP
    server and skills, each running `/perfproof` (or `$perfproof`) on
    an example, and each also handed a decoy. Record the verdicts, the
    tool calls, and where the agent went wrong.
  - Done when: both clients reach the correct verdict on every example
    and decoy, or the failures are written up and turned into tasks.
  - Effort: high

## M4 Profilers and targets

Starts after the M2 exit. Each adapter follows
`src/perfproof/profilers/AGENTS.md`; T4.2 to T4.6 run in parallel.

- [ ] **T4.2 Scalene** · **T4.3 memray** · **T4.4 samply** ·
  **T4.5 perf** (Linux) · **T4.6 torch.profiler**
  - Owns: `src/perfproof/profilers/<tool>.py`,
    `tests/profilers/test_<tool>.py`, `tests/fixtures/<tool>/`
  - Needs: M2 exit
  - Do: adapter per the contract; fixtures captured from `examples/`.
  - Done when: parser tests on captured fixtures; integration test runs
    the real tool on an example and finds the known hotspot in the top
    3; parse time and peak memory on the largest fixture in the PR.
  - Effort: medium

- [ ] **T4.7 Nsight Systems and Nsight Compute**
  - Owns: `src/perfproof/profilers/nsys.py`,
    `src/perfproof/profilers/ncu.py`, matching tests and fixtures
  - Needs: T5.2
  - Do: kernel summaries from `nsys stats` and per-kernel metrics from
    `ncu --csv`. Fixtures need a GPU, so capture them in one planned
    paid session through the `gpu-run` skill.
  - Effort: medium

- [ ] **T4.8 explain_hotspot**
  - Owns: `src/perfproof/profilers/explain.py`,
    `tests/profilers/test_explain.py`
  - Needs: M2 exit
  - Effort: medium

- [ ] **T4.9 detect_target**
  - Owns: `src/perfproof/targets/`, `tests/targets/`
  - Needs: M2 exit
  - Do: detect Python, C/C++, Rust, PyTorch, CUDA and serving projects
    from manifest files and imports; suggest a benchmark command with a
    confidence and the evidence for it.
  - Done when: tested on `examples/` and on small synthetic trees for
    each target, including ambiguous ones.
  - Effort: medium

- [ ] **T4.10 Real noise traces, native**: A/A traces for the C or Rust
  example on this Mac and Linux CI, calibrated as in T2.11. Needs T4.4.

## M5 Remote and GPU

Starts after the M2 exit. Anything that spends money goes through the
`gpu-run` skill.

- [ ] **T5.1 SSH runner**: sync both trees, run the engine remotely,
  pull the record back. Needs open question 3.
- [ ] **T5.2 Modal runner**: one sandbox runs baseline and candidate
  interleaved; image chosen per benchmark; volumes for caches; fail
  fast with log tail. Cross-host sessions record fingerprints and count
  pairs by T1.3's rules. Paid.
- [ ] **T5.3 GPU timing and fingerprint**: CUDA event timing helper for
  `reported` benchmarks, GPU clocks and throttle reasons, optional clock
  locking where permitted.
- [ ] **T5.4 Serving target**: `vllm bench serve` and SGLang bench
  wrappers with `/metrics` counters, building on the reference scripts.
  Comparing versions that need separate images uses the cross-machine
  session design, opted in and labelled. Needs T5.2.
- [ ] **T5.5 vLLM case study through perfproof**: re-run the prefix
  caching comparison and compare with the hand-run numbers. Needs T5.4.
  Paid.
- [ ] **T5.6 Real noise traces, GPU and serving**: paid A/A traces on
  one GPU type for a CUDA kernel benchmark and for serving throughput,
  calibrated as in T2.11. Until this lands, those target classes are
  reported as uncalibrated. Needs T5.3, T5.4. Paid.
- [ ] **T5.7 Native, PyTorch, CUDA and serving playbooks**. Needs the
  matching adapters.

## M6 Case studies

- [ ] **T6.1 Case study 2** and **T6.2 Case study 3**: libraries from
  open question 1. Real issues, upstream PRs or comments, evidence
  reports in `docs/case-studies/`.

## M7 Launch

- [ ] **T7.1** README with real case-study numbers, demo recording,
  docs pass, fresh prior-art check. Every claim in the README links to
  the run that backs it; clients and target classes are named only if
  their tasks passed.
- [ ] **T7.2** Release: version, changelog, PyPI publish (Faras), repo
  public (Faras).
