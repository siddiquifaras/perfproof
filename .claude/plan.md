# perfproof plan

The working plan for the release. The specs are
`docs/failure-modes.md` (why each part exists), `docs/cli.md` (what the
agent and developer see), `docs/method.md` (prove),
`docs/discovery.md` (understand and discover), `docs/evaluation.md`
(how we show it works) and `docs/targets.md` (per-class contracts).
This file is the order of work.

## Priority (Faras, 2026-10-19)

The `/perfproof` skill is the product: it has to work end to end, in
plain language, for beginners and experts on any plan
(`docs/product.md`). Everything else exists to make it as good as
possible. Order of work:

1. The skill (T3.8), scored by the skill eval (T3.19) after every
   change, on real repos as well as the examples.
2. Whatever the skill needs from the CLI, as the runs expose it:
   gate details in `compare --json`, detach and status (T3.6), the
   project model wired into compare (T3.2), the hypothesis log (T3.3),
   `measure` (T3.4).
3. Trustworthy KEEP: T1.6, T1.8, T1.7.
4. Depth tiers measured (T3.14), short output (T3.15), install (T3.16),
   the eval harness (T3.13).
5. Everything else, including CLI commands no skill flow uses yet
   (`confirm`, `load`, `replay`), waits.

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
- Every task traces to a row of `docs/failure-modes.md` or to a
  dimension of `docs/evaluation.md`. A task that traces to neither
  shouldn't exist.

### Integration

- `perfproof.schema` is the only contract between packages. A task that
  needs it changed stops and reports; the lead makes the change as its
  own PR first.
- The lead merges in dependency order and runs the full suite, including
  `pytest -m calibration`, on `main` after every merge. A red `main`
  stops new merges until it is fixed.
- Questions that cross lanes go to the lead, not to the other worker.

### Milestone gates

- **M1 exit:** T1.6 merged with every development cell passing the
  acceptance rule in `docs/method.md`, and the T1.7 audit passing on
  held-out scenarios.
- **M2 exit:** T2.8 green on this Mac and on Linux CI: the full local
  loop works through the CLI on the Python examples and every decoy is
  refused.
- **The thin path comes early.** `perfproof compare` runs end to end
  (T2.17, T2.5) before the M1 exit, but until then it cannot return
  KEEP or a performance REJECT: it reports its interval with outcome
  INCONCLUSIVE and the diagnostic `procedure_uncalibrated`. A gate not
  built yet reports `not_available` and blocks KEEP. Nothing is ever
  claimed that wasn't verified.
- M3 and M5 start after the M2 exit. M4 (evaluation) starts now: task
  mining and ground truth need nothing from the product.

## Shared contracts

Fixed before the work that depends on them, in this order:

| # | Contract | Defined by | Frozen before |
|---|---|---|---|
| 1 | Records, verdicts, gates, guardrail statuses | T0.3 (done), T2.12 | T2.13 to T2.16, T2.5 |
| 2 | One decision path: only `stats.decision` decides a verdict | T1.3, T2.12, T2.15 | T1.5, T2.5, T3.5 |
| 3 | Runner and `Sample`: how any process is launched and measured, locally or remotely | T2.3 | T2.5, T5.11 |
| 4 | The CLI's `--json` output for every command: the interface the skills rely on | T2.17, each command's task | T3.8 |
| 5 | Project model and hypothesis records | T3.1 | T3.2, T3.3 |
| 6 | Folded stacks as the one profile format the summariser reads | T2.9 | T3.4, every playbook |
| 7 | Per-class contract: oracle, guardrails, supported boundary | `docs/targets.md`, each class's contract task | that class's case study |

Strictly serial, whatever else runs: schema changes (T2.12, T3.1) and
edits to `stats/decision.py` (T1.3, T2.12, T2.15), one at a time.

### Integration tests that keep it together

1. **A fixture project per target class** (T5.0), each a small real
   program with a real fix and the three negative candidates (wrong,
   slower, unvalidated), run through the same CLI on every PR that
   touches `schema`, `stats`, `bench` or a helper. Serving and GPU
   fixtures run on CPU stand-ins in CI, which prove the plumbing only;
   their GPU paths are proven by the paid runs in their tracks.
2. **Architecture rule** in CI (import-linter): verdicts come only from
   `stats.decision`, and nothing imports `reference/`.
3. **Golden records**: one `RunRecord` per class from its fixture,
   committed; regenerated reports must match byte for byte.
4. **Skill smoke test**: `/perfproof` run headless on one fixture per
   class, nightly, in Claude Code and Codex.
5. **The external evaluation** (M4) on held-out tasks for each release
   candidate.

### What runs in parallel

| Wave | Starts when | Tasks |
|---|---|---|
| 1 | now | T1.1, T1.2, T1.4, T2.1, T2.2, T2.3, T2.4, T2.6, T2.7, T2.9, T4.1 |
| 2 | T1.3 merged | T2.12, then T2.13, T2.16; T1.5 after T1.1; T2.14 after T2.4 and T2.13; T2.17 after T2.2 and T2.3; T4.2 after T4.1 |
| 3 | M1 exit | T2.15, T2.5's verdicts unlocked, T2.11; T2.10 after T2.7 and T2.9; then T2.8 |
| 4 | M2 exit | T3.1 to T3.7, T5.0, T5.3, T5.7, T5.11, T5.12, T5.19 |
| 5 | T3.8 | the class contracts and playbooks; T4.3 |
| 6 | the class's playbook and fixture | each class's traces and case study |
| 7 | every class through its case study | T4.5, then M6 |

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
| Product shape | A small exact core (the `perfproof` CLI) and Markdown skills. The CLI owns everything that must be exact: state, measurement, statistics, gates, undo, records. The skills own judgment. Every component justified in `docs/failure-modes.md` |
| Agent interface | The CLI with `--json`, used through the shell; `--detach` and `status` for long runs. No MCP server in the release: both target clients run shell commands, and it would block no failure mode |
| Verdicts | KEEP if `L > 1 + min_effect`; REJECT if `U <= 1 + min_effect` or a gate fails; INCONCLUSIVE if the interval still straddles the bar when the budget runs out. `min_effect = 0.03`. Every verdict carries a reason: `performance`, `correctness`, `integrity`, `guardrail` or `insufficient_evidence`. Full rule in `docs/method.md`. |
| Interval procedure | Fixed in advance: t interval on the mean log ratio, Bonferroni split across at most `K` looks, geometric-mean speedup as the estimand. Batch means is the prespecified fallback for autocorrelation. A challenger replaces it only under the rule in `docs/method.md`. |
| Calibration acceptance | Fixed in advance: errors defined relative to the bar. Every cell's one-sided 95% Clopper-Pearson upper bound at most 2.5% (at most 83 errors in `R = 4000`), reported per cell, no reruns or reseeding after a failure |
| Guardrails | Primary objective and guardrails are separate. Provisional default allowances: peak memory 10%, p99 latency 5%, refusal thresholds rather than permission to regress, overridable per target class and project, plus absolute limits. KEEP needs every applicable guardrail shown compliant; unmeasurable or unestablished compliance gives INCONCLUSIVE. Contract in `docs/method.md` |
| Cross-machine | Same machine is the default and the basis of perfproof's claims. One fixed machine per version is refused. Cross-machine needs an opt-in and the alternating fresh-host session design, and its verdicts are labelled with the assumption they rest on |
| Calibration scope | Development scenarios, held-out audit scenarios at fresh seeds, and real A/A traces per target class with a held-out machine; uncalibrated target classes are labelled in every report |
| Target classes | One generic workload proves every class: any command, any metric. Class differences are contracts in `docs/targets.md` and playbooks, plus two helpers the failure table needs: `load` (services) and `replay` (agentic apps) |
| Profiling | `perfproof profile` runs a profiler and summarises folded stacks. Playbooks say how to run each tool and turn its output into folded stacks; there is no adapter per profiler |
| Metrics | `wall`, `cpu`, `reported`, and `counter` (Linux `perf stat`) |
| Shared data types | Pydantic models in `perfproof.schema`; NumPy arrays inside `stats` |
| Runtime dependencies | `numpy`, `scipy`, `pydantic`; `coverage` and `hypothesis` join in T2.12. Test and lint tools stay in the `dev` group |
| Remote | `--host ssh://…` or `--host modal:<image>`; the remote side runs the same engine |
| Evaluation | External, fixed in advance in `docs/evaluation.md`. Ground truth comes from an independent measurement procedure, never from perfproof. Primary scores: precision of claims and false-KEEP rate |
| Release scope | Six target classes: Python, native, web and CRUD services, LLM serving, GPU kernels and PyTorch, agentic apps. A class is claimed only when its contract, fixture, real-trace calibration and case study all pass, and the claim names what it was shown on |
| GPU budget | Free credit only: nothing out of pocket. Modal's monthly credit (about $8 left this period on 16 October, $30 next), plus Lightning AI and Kaggle free hours; tracked in `.claude/local/gpu-spend.md` by the `gpu-run` skill. Paid GPU runs happen only when Faras is present. T4 and L4 GPUs and small models |
| Case studies | Python: frames2py. Native: picolm. Web: NetBox (Django REST API, Postgres and Redis in local containers). Serving: vLLM with a small model on an L4. GPU and PyTorch: nanoGPT on a T4, with llm.c's CUDA kernels as evaluation tasks. Agentic: gpt-researcher, its model and search calls replayed |
| Evaluation scale | 30 held-out tasks, five per class by the selection rule in `docs/evaluation.md`, and a development set drawn the same way; Claude Code and Codex, with and without perfproof, two repetitions: about 240 held-out runs, on Faras's existing plans |
| Codex | Installed with `npm install -g @openai/codex` (Node is already on this Mac through nvm), then `codex login` by Faras |
| Local-only files | `.claude/local/` (handoff, budget) and `CLAUDE.local.md`, gitignored, copied into worktrees by `.worktreeinclude`. Nothing needed to build, test or run perfproof lives there. |

## Estimates

Rough, so conflicts show now rather than at M5. Every paid run still
gets a worst-case estimate and approval through the `gpu-run` skill.

Rates and credit as read from the Modal CLI on 16 October 2026
(`modal billing rates`, `modal billing summary`): T4 $0.59 an hour, L4
$0.80, CPU $0.0473 per core-hour and memory $0.008 per GiB-hour for
Functions, three times that for Sandboxes. This billing period: $21.86
metered, $21.67 covered by credit, $0.00 billed. Against the Starter
plan's $30 monthly credit (from modal.com/pricing), about $8 of credit
is left this period. No Modal apps were running.

**Budget:** free credit only, nothing out of pocket, and GPU runs only
while Faras is present. Free tiers: Modal's monthly credit, Lightning AI's free GPU hours
through the SSH runner, and Kaggle's weekly GPU quota where a job fits
a notebook. Work is sized for T4 and L4 GPUs and small models; nothing
in the plan needs an A100.

| Paid work | Task | GPU | GPU-hours, rough |
|---|---|---|---|
| SSH and Modal runner plumbing | T5.11 | T4 | 1 |
| Serving A/A traces, two sessions | T5.14 | L4 | 4 |
| vLLM case: a `/perfproof` pass on a small model, same image, interleaved, about three candidates and a confirmation | T5.15 | L4 | 10 |
| GPU traces and profiler captures | T5.16, T5.17 | T4 | 3 |
| nanoGPT case | T5.18 | T4 | 6 |
| Evaluation tasks in the serving and GPU classes | T4.5 | T4, L4 | 6 |
| **Total** | | | **about 30** |

With 2 cores and 8 GiB per job, that is about $26 run as Modal
Functions (T4 about $0.75 an hour, L4 about $0.96) or about $36 as
Sandboxes, so it must be spread across this period's remaining credit
and next month's $30, with Lightning and Kaggle hours taking the rest;
the ceiling is the free credit available, never out-of-pocket spend. The original A100 prefix-caching finding is
not re-run: it stays in `docs/case-studies/` as hand-run prior work,
and the vLLM case is a new study on hardware the budget covers.

The evaluation's agent runs use Faras's existing Claude and ChatGPT
plans, so their limit is the plans' rate limits and wall time, not
money. They run in batches across days.

Work: 65 open tasks. M1 and M2 hold 24 of them and are the critical
path; M4 runs alongside them.

## Open questions

Ask Faras; don't decide silently.

1. A host for testing the SSH runner (T5.11). Recommended: a free
   Lightning AI Studio, which also supplies free GPU hours; it needs
   Faras to create the account.

Repository admin (Faras): make the CI checks required on `main` so a red
PR can't merge.

## Not scheduled yet

Useful, but not needed for the release. None of them may loosen the
decision rule or the calibration criteria.

| Capability | Waits for | Note |
|---|---|---|
| Thin MCP wrapper over the CLI | M6 | Only if a client that can't run shell commands needs it |
| No-regression mode | T1.7 | "Is it no more than m slower?" as a signed `min_effect`; a schema task if `min_effect` must go negative |
| CPU pinning and isolation | T2.3 | Linux only, where permissions allow; never a requirement |
| History bisect | M2 exit | The commit it blames is confirmed by a fresh, independent `compare` against its parent, and only that run is claimed |
| Kaggle runner | T5.11 | Only if Modal credit and Lightning hours run out: Kaggle's free weekly GPU quota runs notebook jobs, which would need a third runner |
| CI regression check | T2.17, no-regression mode | `perfproof compare --base origin/main` as a PR check, using `counter` on Linux where available |

## Lanes

```
M0  T0.3 schema ─┬─ T0.2 CI
                 │
M1  T1.1 ─┬──────┼─ T1.5 ─┐
    T1.3 ─┘      │        ├─ T1.6 calibration ─ T1.7 audit ══ M1 exit
    T1.2 ────────┼────────┤
    T1.4 ────────┼────────┘
                 │
M2  T2.1 T2.2 T2.3 T2.4 T2.6 T2.7 T2.9 ─ T2.17 CLI ─ T2.5 compare
    T2.12 ─ T2.13…T2.16;  T2.10;  T2.11 ─ T2.8 local loop ══ M2 exit

M3  T3.1 ─ T3.2 project, T3.3 log;  T3.4 discovery;  T3.5 confirm;
    T3.6 detach;  T3.7 planning ─ T3.8 /perfproof ─ T3.9 clients
M4  T4.1 tasks ─ T4.2 ground truth;  T4.3 runner ─ T4.4 scoring ─ T4.5
M5  T5.0 fixtures; six classes: contract, playbook, helpers, traces,
    case study
M6  release bar, README, release
```

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

- [x] **T0.4 Check Codex discovery** (lead). Observed on 16 October
  2026 with codex-cli 0.163.0 and `gpt-5.6-luna`, read-only, in this
  repo: Codex loads the root `AGENTS.md` at start; it lists `gpu-run`,
  `run-task` and `verify` through the `.agents/skills` symlink; it does
  not load nested `AGENTS.md` files on its own, but reads them when the
  root file's "Where to read before editing" table sends it there, so
  the layout stays. The ChatGPT login rejects `gpt-5.3-codex`,
  `gpt-5.5`, `gpt-5.4` and older slugs; `gpt-5.6-luna` and `gpt-6-luna`
  work, so evaluation runs pass `-m` explicitly.

## M1 Statistics core

Every task here follows `src/perfproof/stats/AGENTS.md` and is reviewed
by `stats-auditor` before its PR opens.

- [x] **T1.1 Paired intervals**
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

- [x] **T1.2 Bootstrap ratio of medians**
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

- [ ] **T1.3 Decision rule** (merged: siddiquifaras/perfproof#3, `0eb8160`; ticked after T1.5 and T1.6)
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
    owns the pure helper that produces it, and T2.5 and T5.11 must build
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

- [x] **T1.4 Noise simulator**
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

- [x] **T1.5 Sequential looks** (siddiquifaras/perfproof#20, `6fe693c`; schedule defaults in `docs/method.md`)
  - Owns: `src/perfproof/stats/sequential.py`,
    `tests/stats/test_sequential.py`
  - Needs: T1.1, T1.3
  - Do: the look schedule from `docs/method.md` (`n0 >= 10`, `k`, `K`,
    budget cut-off with a final look that replaces the remaining
    looks), the Bonferroni split, and a driver that takes the paired
    samples so far and returns the decision at each look. The driver
    decides only through T1.3's function, never a second copy of the
    rule. Once PR #13 merges: looks on complete order pairs only
    (even `n0` and `k` on one host), per-look confidence
    `1 - alpha / (2K)`, the resolution widening of the interval and
    the `coarse_resolution` check, and lag-1 after subtracting each
    order's mean. Also add balanced order to
    T1.4's simulator (`simulate.py`, a small change this task may
    make): today it draws a fair coin per round. With it, three test
    gaps from the T1.4 final audit (probes in
    `scratch/audit-pr6c-probes/`): a stream that ignores the seed
    passes the suite, since the different-seed test runs every effect
    at once (test each stream alone with two seeds); noise passed
    through `exp` and back isn't caught (a heavy-tailed `cv=1e154`
    case is); and `NoiseScenario` accepts a CV large enough to make
    every log time non-finite, against its "finite" docstring
    (refuse it).
  - Inputs from the T1.1 audit (auditor's own simulator, probes in
    `scratch/audit-pr11-probes/`, to re-measure on T1.4's):
    - The PR #13 audit found AR(1) failing at rho as low as 0.13 when
      combined with other effects, not only at 0.5.
    - AR(1) 0.5 in `d` defeated both the t primary (651/4000 false
      KEEP at S = 1.03, six looks) and the batch-means fallback
      (113/4000; limit 83). Batch means at `n = 10` (two blocks, one
      df) also under-covers under bimodal and outlier noise. The
      fallback as written won't rescue autocorrelation at short `n`.
      Candidate remedies, to choose under calibration: wait for enough
      rounds for at least 10 blocks before a decision when lag-1
      autocorrelation is high, a HAC variance with a small-sample df
      correction, or INCONCLUSIVE with a diagnostic. Whatever is
      chosen is written into `docs/method.md` before T1.7.
    - Hodges-Lehmann passed every order-effect cell the t primary
      failed.
    - One-version skew (2x slow runs in 5% of one version's
      executions, or 1.3x in 10%) beats the t primary at every length
      the PR #13 audit tried (probes in `scratch/audit-pr13b-probes/`).
      It is a stated, uncalibrated limit in `docs/method.md` for now.
      Investigate a remedy (a skew-corrected interval, a skewness check
      that forces more rounds, or a robust estimand); if one passes,
      add the skew scenarios to the development set before T1.7.
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
    rerun with a different one. In cross-host mode every development
    scenario gets random per-session offsets, as `docs/method.md`
    says; T1.4's presets set `session_offset_cv` only in the
    `session_offset` preset, so add it to each scenario here. Time
    budgets at heavy tails: T1.4's audit found 59% of replications at
    the heavy-tail corner contain a run over 100 times the typical
    time, so accumulate time in log space (`np.logaddexp.accumulate`).
  - From the T1.2 final audit (probes in `scratch/audit-pr10d-probes/`):
    BCa stays as a free comparison only; it was weaker than the
    percentile interval in nearly every cell measured, and both break
    under AR(1) 0.5 (375-614 false KEEPs per 4000 at one look). Add the
    auditor's constructed tests (`test_constructed.py`) for the three
    margins no suite test reaches, and fix the peak going over
    `max_bytes` at the smallest accepted budget, as a small follow-up
    to `bootstrap.py`.
  - From T1.5 (PR #20; R = 4000, seed 20261016, one host, balanced
    order, cv 0.05; counts per 4000, limit 83; to be re-measured by the
    auditor): under AR(1) 0.5 at looks 10..60 the t primary gave 471
    false KEEPs and Hodges-Lehmann 424, while batch means gave 58 (82 at
    looks 20..120, one count from failing). A candidate 2x slow in 5% of
    runs beat all three (t 650, batch means 326, Hodges-Lehmann 1613);
    1.3x in 10% passed with batch means only. So the prespecified
    batch-means fallback looks like the answer for autocorrelation and
    nothing yet answers one-version skew. Calibrate with
    `simulate(..., order="balanced")`; the simulator still defaults to
    `"coin"`.
  - Open before batch means can become primary (T1.5 audit N1):
    blocks of 5 rounds split order pairs, so a look at 16, 26, ... rounds
    uses an odd count. Decide block size in whole order pairs (or drop to
    complete blocks of pairs) and write it into `docs/method.md`.
  - Decision for Faras, not taken: under the acceptance rule as
    written, an exact procedure on the bar (one-sided error 1.61% at
    six looks) passes each S = 1.03 cell with probability 0.990 and
    all of about 20 such cells with probability 0.82 (T1.1 audit). A
    failing cell is written up, never rerun.
  - Done when: results table in `docs/method.md` with seed, `R`,
    counts and Clopper-Pearson bounds; every cell passes for the primary
    procedure (or the batch-means fallback, or a challenger switched in
    under the documented rule), or the failing cells are written up and
    the task stays open; challengers that lose everywhere and serve no
    other purpose are deleted; a CI-sized subset runs in under two
    minutes.
  - Effort: max

- [ ] **T1.8 Switch the primary to batch means, and close the audit-range gaps**
  - Owns: `src/perfproof/stats/intervals.py`, `sequential.py`, their
    tests, `tests/calibration/`, "Speedup and interval" and the
    calibration sections of `docs/method.md`, the t references in
    `docs/failure-modes.md`
  - Needs: T1.6 merged
  - Decision (lead, from the T1.6 stats audit on PR #33): batch means
    with blocks of 6 rounds (3 order pairs) is the primary in both host
    modes; cross host does not keep t (it passed the development grid
    only because the 15% session offset dilutes AR(1), and failed 24 of
    160 held-out cross-host scenarios against 1 for blocks of 6).
    Fewer than two complete blocks gives no interval and the look
    continues, never an error. `BATCH_BLOCK` becomes 6; reconcile
    `method.md:378` ("cross host a block is 6 session pairs").
  - Close the gaps the audit found inside the documented audit ranges,
    where blocks of 6 still fail: AR(1) rho 0.6 to 0.7 with little
    other noise (gpu_kernels), and 4 to 5% outliers at 2.8 to 4.5x with
    CV under 1% on schedules with three blocks or fewer (llm_serving,
    22 rounds). Candidate remedies, chosen under calibration and
    written into `docs/method.md` first: a diagnostic gate that, when
    lag-1 autocorrelation or the outlier share is high, withholds a
    decision until at least ten blocks exist and otherwise ends
    INCONCLUSIVE naming it; a larger minimum schedule where three
    blocks is all the budget allows. The auditor's exposed scenarios
    (`scratch/audit-pr33-work/heldout/`) become development scenarios;
    T1.7 draws fresh ones.
  - Also: a committed differential test of production `decide_look` at
    block 6 against the harness; the T1.2 auditor's third constructed
    test if the bootstrap stays, else delete Hodges-Lehmann and the
    bootstrap (they lost everywhere).
  - Done when: the full grid and the auditor's scenarios pass the
    acceptance rule at the seed of record and at a fresh seed, both
    host modes; `docs/method.md` names batch means as the primary
    throughout; stats-auditor passes.
  - Effort: high

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

- [x] **T2.1 Machine fingerprint**
  - Owns: `src/perfproof/machine/`, `tests/machine/`
  - Needs: T0.3
  - Do: probes for macOS and Linux: host identity, CPU model, physical
    and logical cores, frequency policy, load average, power source and
    battery, thermal state, memory, OS and Python versions, NVIDIA GPU,
    driver, clocks when `nvidia-smi` exists. Warnings for battery power,
    high load, non-performance governor, thermal limits. Probes run
    once per comparison, never between rounds.
  - Done when: parsers are tested on captured outputs of each command
    (`pmset -g batt`, `pmset -g therm`, `sysctl`, `/proc/cpuinfo` from an
    arm64 Linux container), and an integration test fingerprints the
    machine it runs on. The x86 `/proc/cpuinfo` and `nvidia-smi` captures
    need a Linux x86 or GPU host and move to T5.12, in the GPU session;
    until then those parsers are tested on constructed samples, labelled
    as such.
  - Effort: medium

- [x] **T2.2 Spec, lock, procedure and snapshot**
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

- [x] **T2.3 Runner**
  - Owns: `src/perfproof/bench/runner.py`, `tests/bench/test_runner.py`
  - Needs: T0.3
  - Do: run a spec's argv in a given tree with env, cwd and timeout.
    `wall` from `perf_counter_ns` around the child, `cpu` and max RSS
    from `os.wait4`, `reported` from a streamed `PERFPROOF {...}` stdout
    line, `counter` from `perf stat -x,` on Linux. Record the argv,
    declared env, cwd and resolved executable actually launched in
    every `Sample`, and wall time alongside every `reported` value.
    Stdout and stderr go to files, not memory. Warmup, then rounds with
    in-round order drawn from a recorded seed; once PR #13 merges,
    rounds come in balanced pairs (one each order, which first drawn
    from the seed). Record each value's resolution: the clock step
    for `wall` and `cpu`, measured once per run; one count for
    `counter`; for `reported`, the spec's declared resolution, else
    the place of the last digit printed.
  - Done when: each metric is tested against a real child process;
    runner overhead per invocation is measured against a bare
    `subprocess.run` of the same command and reported in the PR.
  - Effort: high

- [x] **T2.18 Schema follow-ups for the method changes** (siddiquifaras/perfproof#21, `6c5e4aa`)
  - Owns: `src/perfproof/schema.py`, `src/perfproof/schema.json`,
    `tests/test_schema.py`
  - Needs: T2.12, PR #13 merged
  - Do: bring the shared contract in line with `docs/method.md`:
    `LookSchedule` refuses odd `n0` or `k` on one host and documents
    `1 - alpha / (2K)`; a `resolution` field on `reported` metrics in
    the spec, and a per-sample resolution; `coarse_resolution` and the
    resolution widening in `Diagnostics`; a runtime kind field on
    `TreeEnvironment` (interpreter, native build, unknown with a
    reason) and the linked outside-tree libraries, replacing T2.2's
    fixed marker text in `missing["runtime_version"]`. From T2.6: the
    repository and candidate commit on `RunRecord` (failure mode 20 wants
    claims to name the repository), the calibration conditions a verdict
    rests on, and fields for any capped value so the report can label it
    a bound. From T2.13: a declared numeric tolerance for differential
    checks (today they compare exactly, so any reordered float
    arithmetic mismatches).
  - Done when: each refusal and field has a test that fails without
    it; `schema.json` regenerated; T2.2's gate reads the new kind field
    and its tests pass unchanged in behaviour.
  - Effort: medium

- [x] **T2.19 Runner follow-ups from compare** (siddiquifaras/perfproof#34, `b9db3b2`; follow-ups: a reset that fails only on the baseline arm should stop as a broken experiment, which needs a method.md change first; `run_reset` reads an ECHILD status as success when SIGCHLD is ignored)
  - Owns: `src/perfproof/bench/runner.py`, `tests/bench/test_runner.py`,
    and the matching refusals in `src/perfproof/bench/compare.py`
  - Needs: T2.5 merged
  - Do: rounds come in balanced order pairs per "Order is balanced" in
    `docs/method.md` (one pair each way, which first drawn from the
    recorded seed; a resumed run repeats the same pairs); a hook that
    runs the spec's `reset` before every execution; a single-execution
    call, so compare's baseline recheck runs the baseline once rather
    than a two-slot warmup pair. Then drop compare's `unsupported_spec`
    refusal for `reset` and its `order_not_balanced` warning.
  - Done when: a test shows every look's rounds split evenly between
    the two orders; a reset counter file shows one reset per
    execution; the recheck runs exactly one baseline execution;
    compare runs a spec with `reset` end to end.
  - Effort: medium

- [x] **T2.20 Examples declare objective and guardrails** (siddiquifaras/perfproof#35, `d9adde6`)
  - Owns: `examples/*/spec*.json`, `examples/*/test_*.py` where a
    spec's contents are checked
  - Needs: T2.18 (schema 3 for the specs), T2.5
  - Do: give each shipped spec the objective and the guardrails its
    target class requires, so `perfproof compare` runs them instead of
    refusing with `undeclared_guardrails`.
  - Done when: `perfproof compare` on each example's fix runs to a
    verdict through the CLI.
  - Effort: low

- [ ] **T2.21 Real-repo harness and per-tree setup** (top CLI priority)
  - Found by the skill's first real-repo run (frames2py): a benchmark
    the skill writes under `.perfproof/` is missing from the baseline
    worktree, and a gitignored `.venv` interpreter doesn't exist there
    (and sharing the main checkout's editable venv would import the
    same source for both versions). Do: perfproof-owned harness files
    under `.perfproof/harness/` copied identically into both trees and
    locked; an optional `setup` on the spec run once per tree before
    warmup (for example `uv sync --frozen`); a clear message when an
    interpreter lives in an ignored directory.
  - Done when: an end-to-end test on a temporary uv project proves each
    tree imports its own source, a candidate can't alter harness files,
    and the frames2py case reaches a verdict.

- [x] **T2.4 Correctness gate**
  - Owns: `src/perfproof/bench/correctness.py`,
    `tests/bench/test_correctness.py`
  - Needs: T0.3
  - Do: run the spec's test command on the baseline three times first
    and quarantine any test whose result changes (flaky), per
    "Correctness" in `docs/method.md`; then run it in the candidate
    tree; a test that fails only on the candidate is re-run five more
    times on each tree before the candidate is blamed. Compare declared outputs between trees as bytes, text, or
    numbers with `rtol` and `atol` (text numbers and `.npy` files).
    Return the first differences, capped.
  - Done when: tests cover equal, unequal, within-tolerance, NaN
    handling, a missing output file, a flaky test quarantined rather
    than blamed on the candidate, and a test that fails on all three
    baseline runs is a known failure, listed and not judged (per
    "Pre-existing failures" in `docs/method.md`).
  - Effort: medium

- [ ] **T2.5 Compare**
  - Owns: `src/perfproof/bench/compare.py`,
    `tests/bench/test_compare.py`
  - Needs: T2.17, T2.1, T2.2, T2.3, T2.4, T1.3; each gate as its task
    lands (T2.13, T2.14, T2.15, T2.16); M1 exit for real verdicts
  - Every command runs with `side_effects.benchmark_environment(spec,
    os.environ)` (the base allowlist plus the spec's `env`, per "Side
    effects" in `docs/method.md`), passed as the runner's `base_env`
    and to the side-effect gate, and the reset runs before every
    execution. Take the looks from `sequential.plan_looks` and each
    look's confidence from `sequential.look_confidence`, then decide
    each look with `sequential.decide_look` (resolution
    stop, widening, then T1.3's rules), never by calling
    `decision.decide` directly, so the resolution stop isn't skipped.
    A harness `RunnerError` stops the comparison with no
    verdict; an `ExecutionError` follows "Failed executions". When the
    candidate fails, run the baseline once more on the same round
    before blaming it: if the baseline fails too, the failure is shared
    (a noexec mount, a program missing on the host, a race on a shared
    binary) and the comparison stops as a broken baseline. From the
    T2.3 review: blaming `launch` only when the program sits inside the
    arm's tree would also remove the shared cases at the runner.
  - Do: the thin path first, then deepened as gates land. Until the M1
    exit, the outcome is INCONCLUSIVE with the diagnostic
    `procedure_uncalibrated`, whatever the interval; a gate not built
    yet is reported `not_available` and blocks KEEP. Then: gates, then
    interleaved rounds per the look schedule, then the
    sequential decision, then a `RunRecord` with samples saved to
    `samples.npz`. Re-check the procedure and same-host gates on the
    finished samples. A candidate execution that exits non-zero, is
    signalled or times out is a correctness REJECT with its exit status
    and output tail; a baseline one stops the comparison with no
    verdict. For a `reported` metric, compute the wall-clock
    interval too and set the `metric_disagreement` diagnostic per
    `docs/method.md`. Decides through T1.3's function and builds
    cross-host evidence by T1.3's rules. Uses the procedure fixed by
    T1.6 and T1.7.
  - Thin path merged (siddiquifaras/perfproof#24, `7bb5731`). Still to
    wire: the differential checks (T2.13) and coverage gate (T2.14);
    the spec's declared `metric.resolution` (compare uses printed
    digits), the differential `rtol`/`atol` and `capture_limit`; the
    resolution stop recorded as rule 1 with `coarse_resolution` (schema
    3) instead of rule 5 plus a warning; guardrail widening in the
    record; the second fingerprint once the schema has a place for it;
    reset and balanced order (T2.19); detach, status, cancel, resume;
    the coverage gate needs the judged test ids, which
    `SuiteComparison` (T2.4) carries only as a count; differential
    replays under coverage.
  - Done when: before the M1 exit, `perfproof compare` runs end to end
    on an example and never returns KEEP or a performance REJECT; after
    it, an end-to-end test on a temporary repo with a sleep-based
    benchmark returns, with the right reason field each time: KEEP
    (`performance`) for a large speedup, REJECT (`performance`) for no
    change, REJECT (`integrity`, `lock`) for a locked-file edit, REJECT
    (`correctness`) for wrong output, and INCONCLUSIVE
    (`insufficient_evidence`) when the candidate is faster but the
    budget is too small to show it clears the bar; and a `reported`
    metric that disagrees with wall time on direction sets
    `metric_disagreement`.
  - Effort: high

- [x] **T2.6 Report**
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

- [x] **T2.7 Examples and decoys**
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

- [x] **T2.9 Folded-stack summariser** (siddiquifaras/perfproof#8, `0efbd4b`)
  - Owns: `src/perfproof/profilers/folded.py`,
    `tests/profilers/test_folded.py`
  - Needs: T0.3
  - Do: read folded stacks (the common format py-spy, perf, samply and
    others can produce) as a stream, aggregate self and total time per
    frame, keep a bounded top N, normalise frames, per
    `src/perfproof/profilers/AGENTS.md`. One summariser for every
    profiler, instead of one adapter each.
  - Done when: tests on folded stacks captured from at least two real
    profilers; parse time and peak memory on a large capture in the PR.
  - Effort: medium

- [x] **T2.10 `perfproof profile` with py-spy** (siddiquifaras/perfproof#23, `d8f36c8`)
  - Owns: `src/perfproof/profilers/run.py`,
    `tests/profilers/test_run.py`, `tests/fixtures/py-spy/`, the
    `profile` command
  - Needs: T2.7, T2.9, T2.17
  - Do: run a profiler on a workload and summarise its folded stacks
    with wall versus CPU time. Built in: py-spy on Linux, and on macOS an
    in-process sampler that needs no root (pyinstrument, run through
    `uv run --with pyinstrument`, its output converted to folded
    stacks), because py-spy refuses to run on macOS without root (found
    in T2.9). Other profilers plug in as a command that emits folded
    stacks, which the playbooks supply.
  - Done when: parser tests on captured fixtures; an integration test
    runs py-spy on each Python example and finds its known bottleneck
    in the top 3; parse time and peak memory on the largest fixture in
    the PR.
  - Follow-ups from the review (T5.1 or the next profile change): the
    text says CPU is summed over "all threads and processes", but
    `wait4` counts only waited-for processes; "at least N%" rounds to
    nearest instead of down; a CPU-bound run reads "waiting for at least
    0%". py-spy on native Linux hardware is unverified.
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

- [x] **T2.12 Schema for the safety checks**
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
    keeps the speedup estimate; the primary objective; guardrail specs
    (metric, allowance and its source, absolute limits, applicability
    with reason) and results (interval, status: `passed`, `breached`,
    `not_established`, `unmeasurable`, `not_applicable`); the opt-ins
    for remote services and for targets without coverage; a reset
    command; quarantined flaky
    tests; differential-check results (calls captured, replayed,
    skipped with reasons, first differences). One task, because the
    schema is the shared contract.
  - Done when: round-trip and refusal tests for every new field; a
    guardrail REJECT validates only with its intervals present; a KEEP
    is refused unless every guardrail is `passed` or `not_applicable`;
    `not_applicable` and `unmeasurable` are refused without a reason;
    the JSON Schema drift test passes; all existing tests still pass.
  - Effort: high

- [x] **T2.13 Differential checks** (siddiquifaras/perfproof#19, `76d2f3c`)
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

- [x] **T2.14 Coverage gate** (siddiquifaras/perfproof#26, `e6cd1a8`; follow-ups: differential replays under coverage, judged ids from `SuiteComparison`, a gitignored candidate `.py` is not judged)
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
  - Input from T2.3 (unresolved; decide here and write it into
    `docs/method.md`): the spec defines peak memory as the max RSS of
    the whole process tree, but the runner's `max_rss_bytes` comes from
    `wait4` and is the peak of the largest single process in the tree.
    On Linux it is also `None` when the benchmark's peak is below
    perfproof's own RSS, because a child's high-water mark starts from
    the parent's address space at fork. With perfproof's NumPy-sized
    process that makes the default guardrail `unmeasurable` for many
    small benchmarks. Options: define the metric as the largest single
    process and spawn through a tiny helper so the child's mark starts
    small; or use cgroup v2 `memory.peak` on Linux for a true tree peak
    where a cgroup can be created, with the single-process figure on
    macOS. Whichever is chosen, the record names which one it is.
  - Do: per "Guardrails" in `docs/method.md`: a paired interval on each
    guardrail's worsening ratio and, for absolute limits, on the
    candidate's own metric, at confidence `1 - alpha / (2 K G)`; the five
    statuses; the decision step (KEEP only when every guardrail is
    `passed` or `not_applicable`, REJECT on a breach, otherwise continue
    then INCONCLUSIVE naming the guardrail); the provisional defaults
    (peak memory 10%, p99 5%) with target-class and project overrides,
    recording each allowance's source; a report line for every
    within-allowance regression. Peak memory is the whole process
    tree's max RSS.
  - Done when: tests cover a breach blocking a KEEP; a
    `not_established` and an `unmeasurable` guardrail each giving
    INCONCLUSIVE, never KEEP; a within-allowance regression reported;
    an absolute-limit breach inside the relative allowance; a
    `not_applicable` guardrail without a reason refused; a breach
    alongside a performance REJECT or INCONCLUSIVE leaving that verdict
    alone. Calibration shows guardrail false passes and false breaches
    pass the acceptance rule, reported per cell.
  - Effort: high

- [x] **T2.16 Side-effect guard**
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

- [x] **T2.17 CLI skeleton**
  - Owns: `src/perfproof/cli.py`, `src/perfproof/__main__.py`,
    `src/perfproof/commands/`, `tests/test_cli.py`, `pyproject.toml`
    (script entry)
  - Needs: T2.2, T2.3
  - Do: the `perfproof` command and its registry: each command is its
    own module under `src/perfproof/commands/`, found by name, so each
    later task adds a file rather than editing a shared one. `--json`
    output with a stable shape per command, `doctor`, `bench`. The
    `--help` text in `docs/cli.md` is the target.
  - Done when: every registered command is tested through `main(argv)`
    with and without `--json`; adding a command module needs no edit
    elsewhere.
  - Effort: medium

- [ ] **T2.8 Local loop end to end**
  - Owns: `tests/e2e/`, the A/A section of `docs/method.md`
  - Needs: T2.5, T2.6, T2.10, T2.11, T2.13, T2.14, T2.15, T2.16,
    T2.19, T2.20
  - Do: for each Python example, through the CLI:
    define and lock the benchmark, snapshot the baseline, profile it,
    apply the fix, check correctness, compare, write the report. Then
    run every decoy, and the same commit against itself.
  - Also: CI runs `uv run pytest examples` (T2.7's own checks of the
    examples and decoys); today CI runs only `tests/`, so they never
    run on Linux.
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

## M3 Agent workflow

Runs alongside M2 to meet the release target (Faras, 2026-10-19);
each task still waits for the tasks it needs. Turns the proving core into the
`/perfproof` workflow in `docs/cli.md`.

Users mostly type `/perfproof`, sometimes with a line of context
(`/perfproof the CSV import is slow`). Two entry points are equally
first-class: **find** a speedup for someone who doesn't know where to
look ("make this faster", "why is this slow"), and **prove** a change
someone or an agent already wrote ("is this faster, and did it break
anything?"). Prove on an existing diff, branch or PR skips discovery
entirely and is the fast path; find always ends in prove, so every
claim is checked for speed, correctness and regressions elsewhere
(guardrails), and perfproof's own time to a verdict is measured.

- [x] **T3.10 Hand-run study** (siddiquifaras/perfproof#25, #27)
  - Owns: `studies/hand-runs/`
  - Needs: nothing beyond the current CLI
  - Do: hand-run the whole `/perfproof` conversation, playing the skill
    with the CLI as it exists, on frames2py, picolm and NetBox (Postgres
    and Redis in local containers). Work in clones under `scratch/`,
    never in Faras's own checkouts, and push nothing upstream. Each
    entry point at least once per repo: bare `/perfproof`, free text, a
    named file or function, and an existing diff or branch. Record each
    transcript, every place the CLI was missing or wrong, every
    decision the skill had to make, and the time each step took, with
    the load average.
  - Done when: a transcript per repo and entry point, and a findings
    list where each item names the CLI command or skill decision it
    affects.
  - Effort: high

- [x] **T3.11 Perf PR and unaided-agent study** (siddiquifaras/perfproof#28)
  - Owns: `studies/pr-study/`
  - Needs: nothing
  - Do: collect 10 real merged or rejected performance pull requests
    from public repositories, a few agent-written, with their review
    threads; for each, what was claimed, what was true, and what
    evidence maintainers asked for. Then run fresh Claude Code
    (`claude -p`) and Codex (`codex exec`, sparingly) sessions without
    perfproof on "make X faster" in the T3.10 repos, and score what
    they claimed against what a measurement shows.
  - Done when: a table per PR and per session with claim, truth and
    evidence asked, each row linked to its source.
  - Effort: medium

- [x] **T3.12 Skill contract** (siddiquifaras/perfproof#32, `a10063d`; for T3.8: rule 8's "old time divided by new time" assumes lower is better)
  - Owns: `docs/skill.md`
  - Needs: T3.10, T3.11
  - Do: from that material, every input (none, free text, a file or
    function, a diff, branch or PR) and every bad state (no tests, no
    workload, dirty tree, too noisy, remote I/O, long run) gets one
    defined behaviour, ending in a plain sentence and the next step.
    Prove-a-change is a fast path with no discovery. Failure is fast
    and explained.
  - Done when: each row cites the transcript or PR that motivated it.
  - Effort: medium

- [x] **T3.1 Schema for the project model and hypotheses** (siddiquifaras/perfproof#36, `9e117d8`; for T3.2: which statuses `created`/`edited` history lines may carry, when an entry version bumps, cross-entry status consistency, naming the default bar)
  - Owns: `src/perfproof/schema.py`, `src/perfproof/schema.json`,
    `tests/test_schema.py`
  - Needs: T2.12
  - Do: models for the project model (targets, workloads, goals,
    constraints, how to run, provenance with file hashes, status,
    history) and hypothesis events (every step of the chain in
    `docs/discovery.md`, with anchors). One task, because the schema is
    the shared contract.
  - Done when: round-trip and refusal tests for every new model; a cap
    can't be stored in a speedup field; a hypothesis event without its
    evidence hashes is refused; the drift test passes.
  - Effort: high

- [x] **T3.2 `init` and `project`** (siddiquifaras/perfproof#39, `9998d31`; wiring `require_confirmed` into compare and measure follows T3.6)
  - Owns: `src/perfproof/project/`, `tests/project/`, the `init` and
    `project` commands
  - Needs: T3.1, T2.17
  - Do: the project model contract in `docs/discovery.md`. `init`
    detects target classes and candidate workloads with their evidence
    (manifests, existing benchmark suites, the slowest tests, entry
    points, routes, serving configs) and proposes; `project` shows,
    confirms, retires, and checks staleness. Atomic writes, history
    kept. `compare` and `measure` refuse a workload, goal or constraint
    that is unconfirmed or stale.
  - Done when: tests cover propose and confirm, an unknown goal asked
    rather than filled, each invalidation trigger, revalidation that
    finds no change, revalidation that needs reconfirmation, a refused
    `compare` on a stale workload, and a crash mid-write leaving the
    model intact. Detection is correct on the T5.0 fixtures and,
    read-only, on frames2py and picolm.
  - Effort: high

- [x] **T3.3 `log`** (siddiquifaras/perfproof#38, `dbf5846`)
  - Owns: `src/perfproof/hypotheses.py`, `tests/test_hypotheses.py`,
    the `log` command
  - Needs: T3.1
  - Do: the append-only hypothesis log in `docs/discovery.md`: events
    per step, anchors, staleness, past hypotheses shown with a new one
    on the same code. `log` refuses an implementation event on a
    hypothesis its cheap test refuted, unless a reason is given.
  - Done when: tests cover each step of the chain, matching after a
    function moves within its file, a stale hypothesis after its code
    changes, a corrupt line skipped and kept, two sessions appending at
    once, and the refusal above.
  - Effort: medium

- [ ] **T3.4 Discovery primitives**
  - Owns: `src/perfproof/profilers/summary.py` and its tests, the
    `--sizes`, `--stub` and `--set` options of `measure`
  - Needs: T2.10, T2.13
  - Do: `profile` for any tool whose output is folded stacks, with
    wall versus CPU time; `measure --sizes` fits how time grows with
    input size; `measure --stub` replays one function from captured
    calls to bound its cost ("what if this were free"); `measure --set`
    changes one setting. Every result is stored as evidence on a
    hypothesis, never as a verdict.
  - Done when: each runs on a planted case in `examples/` (added here);
    on every example, the stub bound is at least the speedup the real
    fix gets, measured, so it holds as an upper bound there.
  - Effort: high

- [ ] **T3.5 `confirm`**
  - Owns: `src/perfproof/bench/confirm.py`, `tests/bench/test_confirm.py`,
    the `confirm` command
  - Needs: T2.5, M1 exit
  - Do: up to `k` candidates (code changes or settings) from the same
    baseline, measured one at a time, each through the gates and
    `compare`; the best KEEP is confirmed by a fresh, independent
    `compare`, and only that is claimed.
  - Done when: on an example with fixes of known different strength,
    the strongest is chosen and confirmed; with `k` candidates and no
    real effect, the confirmed false-KEEP rate passes the acceptance
    rule in simulation; selection-stage results never appear as claims.
  - Effort: high

- [ ] **T3.6 Detach, status, cancel, resume**
  - Owns: the `--detach` and `--resume` paths of `compare`, the `status`
    and `cancel` commands, `tests/bench/test_detach.py`
  - Needs: T2.5
  - Do: exactly "Long runs" in `docs/cli.md`: one process in its own
    session, its run directory as the only state, no daemon or queue.
    Resume re-checks every gate and the machine fingerprint first. A
    partial run never yields a verdict. Hard total timeout; cancel and
    timeout kill the whole process tree.
  - Done when: a compare that outlasts a 60-second tool timeout
    finishes through `status`; `kill -9` mid-run is reported
    `interrupted` and resumes with no round lost or repeated; a resume
    on a changed machine is refused; cancel leaves no child process; a
    reused PID is not mistaken for the run.
  - Effort: high

- [ ] **T3.7 Evidence planning**
  - Owns: `src/perfproof/stats/planning.py`,
    `tests/stats/test_planning.py`, its cells in `tests/calibration/`
  - Needs: M1 exit
  - Do: after the first look, estimate the rounds a decision needs at
    the observed noise. When that is beyond the budget, stop at a
    scheduled look with INCONCLUSIVE and say what would settle it. The
    stop only replaces "continue" at a scheduled look.
  - Done when: predicted rounds agree with realised rounds in
    simulation within a stated tolerance; calibration with the early
    stop on passes the acceptance rule in every cell, reporting the
    unconditional rates and the conditional rate among verdicts.
  - Effort: high

- [ ] **T3.8 The /perfproof skill and session**
  - Owns: `skills/perfproof/`, `src/perfproof/session.py`,
    `tests/test_session.py`, the `install-skills` command
  - Needs: T3.2, T3.3, T3.4, T3.5, T3.6, T3.12
  - Do: the skill that runs the session in `docs/cli.md`, and the
    session state behind it: its own branch, undo of only the files an
    attempt changed, dirty-tree refusal, resume. The skill's core holds
    the discovery discipline (evidence first, hypotheses with
    falsifiable predictions, cheap tests before code) and the angle
    checklist; class detail lives in the class playbooks. `install-skills`
    puts the skill and playbooks in place for Claude Code or Codex.
  - Serves the users in `docs/product.md` (beginner to expert, free plan
    to $200): progressive disclosure, `/perfproof try <idea>` for an
    expert's own idea, one suggestion at every stop.
  - Built to `docs/skill.md` (T3.12) on the CLI's `--json`, for Claude
    Code and Codex, with snapshot tests of every message the user sees.
  - Done when: session tests cover undo, resume after a kill, every stop
    condition and a dirty tree; a fresh headless Claude Code session
    with only the CLI and skills runs `/perfproof` on every Python
    example to the end with no input after confirmation, keeps the
    real fix, refuses the decoys, and leaves pre-existing uncommitted
    work untouched.
  - Effort: max

- [ ] **T3.9 Clients, verified**
  - Owns: `docs/install.md`, `tests/agents/`
  - Needs: T3.8, Codex installed (see Decisions)
  - Do: install and use in Claude Code and in Codex, each verified by
    running `/perfproof` (or `$perfproof`) on a fixture; the nightly
    skill smoke test.
  - Done when: every instruction in `docs/install.md` was run, with the
    client versions recorded, and the smoke test runs nightly in both
    clients.
  - Effort: medium

- [ ] **T3.13 Agent eval harness**
  - Owns: `eval/agents/`, `tests/eval/test_agents.py`
  - Needs: T3.8, T4.2
  - Do: a deterministic harness that runs `claude -p` and `codex exec`
    with the skill on the examples, the decoys and the T4.1 tasks, and
    scores each run by code on verdict, reason, branch state and time to
    verdict.
  - Done when: both agents reach the same correct verdict on tasks with
    known answers, with no wrong KEEP; scores are reproducible from the
    saved run records; proving a change through the CLI alone, with no
    agent, gives the same verdict as through the skill.
  - Effort: high

- [ ] **T3.19 Skill eval: score every skill change**
  - Owns: `eval/skill/`
  - Needs: T3.8 (merged), T2.21 for the real-repo tasks
  - Do: a harness that runs the installed skill headless (`claude -p`,
    later `codex exec`) over a fixed task set and scores each run by
    code from its transcript and the repo afterwards: trigger (did the
    skill start on the phrasing), hotspot found (the known one),
    fix correct (tests and outputs), verdict or refusal correct,
    tree left clean, wording rules (no direction words on an estimate,
    correct `Result:` line), turns, cost, wall time. Task set: the
    examples and their decoys, a trigger set of plain phrasings
    ("make this faster", "what's wrong here", "is this PR actually
    faster", and off-topic ones), then development tasks from T4.1 and
    frames2py and picolm clones. A run of the whole set fits in about
    an hour and a few dollars; results are saved and compared with the
    previous run.
  - Done when: the harness scores the current skill on the examples and
    the trigger set, reproducibly from saved transcripts, and a
    deliberately broken skill (a removed rule) scores worse.
  - Effort: high

- [ ] **T3.14 Depth tiers: `/perfproof light`, `medium`, `full`**
  - Owns: the tier presets in `skills/perfproof/`, the `--depth` option
    of `compare` and `measure`, `docs/cli.md` "Depth"
  - Needs: T3.8, T3.13, T3.15, T3.17
  - Why: people are on different plans; a $20 plan runs out fast. The
    tier sets how much perfproof explores and how long it measures. It
    never touches the verdict rule: a lighter tier gets more
    INCONCLUSIVE, never a looser KEEP.
  - Do: the boundaries are measured, not chosen. With T3.13's harness
    on the development tasks, vary the hypothesis cap, attempt cap and
    round budget, and plot real speedups kept (independent labels,
    T4.2) against agent tokens and wall time. Put `light`, `medium` and
    `full` where the curve flattens. Ideas are tried in order of their
    Amdahl or roofline bound (`docs/discovery.md`), so a cap cuts the
    least promising first. The default is `medium`; proving an
    existing diff is `light` by default. Each preset is one declared table in one place; the skill
    and the CLI read it, never restate it.
  - Cost unit: the share of a 5-hour usage window each tier uses on
    Faras's $100 Team premium seat (`get_usage` before and after),
    measured in a window where nothing else runs, plus tokens and wall
    time. Faras's call: one 5-hour window on that seat is the gauge for
    how a tier will work on a Pro seat; every measurement names the seat
    it was taken on.
  - Done when: the cost curves and the chosen boundaries are in
    `docs/cli.md` with their data; no tier gives a wrong KEEP; a
    `light` and a `medium` session each fit in one 5-hour window on the
    Team seat (measured); each tier's message snapshots are tested;
    the first costlier-than-light run states its rough cost and offers
    light, and the choice is remembered in `.perfproof/`
    (`docs/product.md`).
  - Effort: high

- [ ] **T3.15 Token-thrift output**
  - Owns: the `--brief` form of every command's output, the summaries
    of test and profiler output, `docs/cli.md` "Output"
  - Needs: T3.8
  - Why: most of a session's tokens go to the agent reading output (test
    logs, profiles, diffs). The CLI must do the reading and hand the
    agent only what decides the next step.
  - Do: every command has a brief form, the default for the skill:
    verdict, reason, the numbers that back it, and the next step, in a
    fixed small size. Test output is summarized by the CLI (counts,
    the failing ids that differ between trees, a capped tail);
    profiles to the top N with wall versus CPU; raw logs are files the
    agent opens only on request. The skill tells the agent to use a
    semantic code search (mgrep, zg, grepai or similar) when one is
    installed, and plain grep otherwise; perfproof depends on none.
  - Do first, from T3.17 (PR #31; 172 sessions, mostly
    perfproof's own development, so its pooled mix is not a user's).
    The closest subset to real use, the two T3.10 hand-runs: cache
    re-reads of a growing context 70.6% of cost, the fixed prefix
    15.3%, file reads and search about 23%, perfproof CLI output about
    3%. So the levers, largest first: fewer agent turns per session
    (every turn re-reads the whole context); a small fixed skill
    prompt; targeted file reads instead of whole files and re-reads (a
    semantic search tool helps here); CLI commands for steps agents
    otherwise script by hand; brief CLI output stays, but it is not the
    main lever. These shares are estimates with about 10% error; T3.14
    and T3.15 measure real `/perfproof` sessions against them.
  - Done when: each brief output has a byte cap and a test; T3.13
    reports tokens per session before and after on the examples, and
    the saving on T3.17's top cost items is measured, not assumed.
  - Effort: medium

- [ ] **T3.16 Install and first run**
  - Owns: `docs/install.md`, the Claude Code plugin marketplace entry,
    the Codex skill install
  - Needs: T3.8
  - Why: people are comfortable with their setup and there are many
    tools. perfproof has to fit in without asking them to change
    anything.
  - Do: one install line per agent (a Claude Code plugin marketplace
    entry; the Codex equivalent); the CLI runs through `uvx` so nothing
    is added to the project's dependencies; the only file perfproof
    writes is `.perfproof/`, ignored by git; `/perfproof` with no
    arguments works on a repository it has never seen, asking only what
    it can't find out.
  - Done when: on a fresh machine, each documented step was run and
    timed from install to first verdict, client versions recorded; a
    first run leaves `git status` clean apart from `.perfproof/`.
  - Effort: low

- [x] **T3.17 Where the tokens go** (siddiquifaras/perfproof#31; `count_tokens` not run, no API key: ratios fitted to usage growth)
  - Owns: `studies/tokens/`
  - Needs: nothing (uses data already on disk)
  - Do: from the raw session logs (Claude Code's JSONL under
    `~/.claude/projects/` for the T3.10 and T3.11 sessions and their
    subagents, and any `codex exec` logs), attribute every token to its
    cause: reading test output, profiles, perfproof output, code
    search and file reads, diffs, the agent's own reasoning and
    messages. Count with the real tokenizer (Anthropic's
    `count_tokens`) on a sample to calibrate any cheaper estimate, and
    say which numbers are exact and which estimated.
  - Done when: a table and chart, largest cost first, per session and
    pooled, with the method; the top five items each name the change
    that would cut them (feeds T3.15 and T3.14).
  - Effort: medium

- [x] **T3.18 What agents leave slow: a catalogue from real diffs** (siddiquifaras/perfproof#30, `bba5521`)
  - Owns: `studies/agent-inefficiencies/`, the angle checklist's
    evidence column in `docs/discovery.md` (as a proposal for the lead)
  - Needs: nothing (uses the T4.1 pool and the T3.11 study)
  - Do: classify the actual code changes in the merged and reverted
    performance PRs of the T4.1 pool (the diffs, not the titles), with
    tree-sitter or GumTree where a syntax-level diff helps, by the kind
    of inefficiency removed (quadratic loop, row-by-row dataframe work,
    unbatched queries, repeated I/O, missing cache, blocking call in
    async code, needless copies, and whatever else the data shows).
    Do the same for the agent-written code in T3.11's runs and the
    agent-authored PRs. For each kind, name the cheap measurement that
    would confirm or rule it out (queries per request, allocations per
    call, time growth with input size, achieved bandwidth against
    peak). Held-out tasks are excluded: only development and pool
    candidates are read.
  - Done when: a table of kinds with counts, landed versus reverted,
    agent-written versus human, each with its measurement and the
    diffs it came from; the angle checklist gains an evidence column
    proposed from it.
  - Effort: medium

M3 is right when T3.13 passes: both agents reach the same correct
verdict on tasks with known answers, with no wrong KEEP.

## M4 Evaluation

Starts now, in parallel with M1 and M2. Implements
`docs/evaluation.md`; lives in `eval/`, outside the package.

- [ ] **T4.1 Tasks**
  - Owns: `eval/tasks/`, `eval/mine/`
  - Needs: none
  - Do: the four task families in `docs/evaluation.md`, for every
    target class: merged, reverted, regressing and correctness-breaking
    performance changes mined from public repositories; GSO, SWE-Perf
    and SWE-fficiency tasks whose environments run here, flagged with
    the 2026 audit's replay results; hidden-fix tasks; nothing-to-keep
    tasks. One manifest per task: repository, commit, workload, oracle,
    family, class. Split into development and held-out sets at the
    scale in Decisions. Only popular, maintained repositories. llm.c's
    `dev/cuda` kernels, which ship several versions of each kernel with
    a CPU reference check, supply GPU hidden-fix tasks.
  - Done when: the task counts per family and class are in
    `eval/README.md`, the candidate pool and miner are merged, and the
    selected development and held-out tasks (selection rule in
    `docs/evaluation.md`) build and run here, with the held-out list
    committed before any held-out run. Building the selected tasks
    runs with T4.2; the pool alone doesn't close this task.
  - Status (siddiquifaras/perfproof#22, `e8c91e2`): 30 held-out tasks
    committed in f595745 before any held-out run; the 20 CPU ones and 20
    development tasks build and run here. Open: the 10 serving and GPU
    held-out tasks need a GPU host (paid, waits for Faras), then the
    serving and GPU development draw. Follow-ups: `classify([])`
    returns `runs` for a manifest with no runs (an unknown becoming a
    value); `draw.py`'s docstring still describes the reference-side
    outcome as only a failed oracle or workload.
  - Effort: high

- [ ] **T4.2 Independent ground truth** (labeller merged in siddiquifaras/perfproof#29; held-out labelling on the second machine deferred until after release, Faras 2026-10-19: not a release gate, needed only to publish an accuracy number)
  - Owns: `eval/label/`
  - Needs: T4.1
  - Do: label every task with the independent procedure in
    `docs/evaluation.md`: the full test suite for correctness, at least
    200 interleaved pairs on two machines analysed with separate tooling
    for performance, and "unstable" where they disagree. Nothing in
    `src/perfproof/` is imported.
  - Done when: every task has a label and its evidence; an A/A check of
    the labeller on unchanged code labels nothing an improvement; the
    unstable tasks are counted and listed.
  - Effort: high

- [ ] **T4.3 Agent runner**
  - Owns: `eval/run/`
  - Needs: T4.1 for runs without perfproof, T3.8 for runs with it
  - Do: matched runs of `claude -p` and `codex exec`, with and without
    perfproof, in fresh isolated checkouts with later history removed,
    network limited to package mirrors, identical caps; transcripts and
    final claims saved; the leakage check.
  - Done when: development tasks run in both conditions; a planted
    leak (the fix's commit left in history) is caught by the leakage
    check. Runs without perfproof start early, as raw material on how
    agents fail today.
  - Effort: high

- [ ] **T4.4 Scoring**
  - Owns: `eval/score/`
  - Needs: T4.2, T4.3
  - Do: the scoring fixed in `docs/evaluation.md`, written before any
    held-out run: every dimension, per class and overall, rates with
    Clopper-Pearson intervals, unstable tasks reported apart.
  - Done when: the scorer runs on the development results and its
    output format is frozen.
  - Effort: medium

- [ ] **T4.5 Held-out evaluation**
  - Needs: T4.4, every class's case study
  - Do: the held-out run for the release candidate, once.
  - Done when: results and raw transcripts are in `eval/results/`, and
    the release notes quote them as they are.
  - Effort: high

## M5 Target classes

Starts after the M2 exit. Each class needs its contract (correctness
oracle, guardrails and how they're measured, the supported boundary)
in `docs/targets.md`, a playbook in `skills/perfproof-<class>/`, its
fixture passing T5.0, real-trace calibration, and a case study.

The case study is that class's release test. On a real repository
through `/perfproof`, it must run the whole cycle: the project model
built and confirmed by Faras; hypotheses with at least one cheap
falsification test; every candidate proven, with its verdict and
record. It must include negative cases on that repository: a candidate
that gives wrong results, one that is slower, and one that can't be
validated, each refused for the right reason. It ends in a verified
improvement or a written account of what was tried and why nothing was
kept. It is evidence for that repository, not proof for the class, and
the write-up says so. Paid steps go through the `gpu-run` skill.

Each playbook distils methods from primary sources (papers, official
guides, the creators' repositories) and links them; it never copies
their text. Starting points, read before writing a playbook: the GPU
and serving playbooks draw on
[wafer-ai/gpu-perf-engineering-resources](https://github.com/wafer-ai/gpu-perf-engineering-resources)
(roofline, coalescing and occupancy, tensor cores, FlashAttention,
Nsight Compute sections, the CUTLASS GEMM measurement methodology,
continuous batching, KV cache, speculative decoding, serving
benchmarks); the web playbook may point to load tools (k6, Locust) and
borrow ideas, with credit, from
[QAInsights/perf-skills](https://github.com/QAInsights/perf-skills)
(MIT), a load-testing knowledge base with no measurement or proof of
its own. A playbook turns a source into a checklist of angles with the
measurement that would show each one, so the agent finds speedups it
would not write on its own.

- [ ] **T5.0 Fixture projects and the integration job**
  - Owns: `tests/fixtures/projects/`, `tests/integration/`, the
    import-linter contract, the CI job, golden records,
    `pyproject.toml` (adds `import-linter` and `mutmut` to the dev
    group)
  - Needs: T2.8, T3.2
  - Do: the integration tests under "Shared contracts": one small real
    project per class with a real fix and the three negative
    candidates, run through the CLI on every relevant PR.
  - Done when: every fixture's fix is a KEEP and every negative is
    refused for its reason; a deliberately broken gate turns the job
    red; mutation testing (mutmut) of the gate code against the
    fixtures leaves no surviving mutant in a gate's decision without a
    recorded reason.
  - Effort: high

### Python

- [ ] **T5.1 Python contract and playbook.** py-spy, Scalene and
  memray (via folded stacks or their own summaries), vectorization,
  algorithms, caching, allocations, imports. Needs T3.8, T5.0. Done
  when an agent with no other context gets a correct verdict on every
  Python example and fixture with it.
- [ ] **T5.2 Case study: frames2py.** Needs T5.1, T2.11. Effort: max.

### Native

- [ ] **T5.3 Native coverage** with llvm-cov or gcov, so native changes
  stop needing the "coverage not checked" opt-in. Needs T2.14.
- [ ] **T5.4 Native contract and playbook.** samply and perf turned
  into folded stacks, instruction counts on Linux, output comparison
  as the oracle. Needs T3.8, T5.0, T5.3.
- [ ] **T5.5 Native traces**: A/A on this Mac (development) and Linux CI
  (held out), calibrated as in T2.11. Needs T5.4.
- [ ] **T5.6 Case study: picolm.** Greedy fixed-seed decoding must give
  identical tokens, or the tolerance is declared in the spec. Its
  remote is upstream `RightNow-AI/picolm`: any upstream PR is Faras's
  call. Needs T5.4, T5.5. Effort: max.

### Web and CRUD services

- [ ] **T5.7 `perfproof load`**: start the server, wait for its health
  check, warm up, drive load at a constant request rate (open loop, so
  a slow response can't hide by delaying the next request), record
  p50, p99 and throughput per round, reset between rounds, database in
  a local container. The request mix comes from recorded traffic or a
  mix the developer confirms. Needs T2.16. Done when it passes against
  the web fixture and a deliberately slowed endpoint shows in p99.
- [ ] **T5.8 Web contract and playbook.** Response comparison on the
  recorded requests as the oracle; query counting per request, N+1
  patterns, serialization timing, connection-pool waits. Needs T5.7,
  T3.8.
- [ ] **T5.9 Service traces**, calibrated as in T2.11. Needs T5.7.
- [ ] **T5.10 Case study: NetBox** (`netbox-community/netbox`, about
  22k stars): its REST API under `perfproof load`, Postgres and Redis in
  local containers, seeded data, DRF serialization and query patterns
  as the likely angles. Needs T5.8, T5.9. Effort: max.

### LLM serving

- [ ] **T5.11 Remote runners**: SSH (tested on a free Lightning AI
  Studio) and Modal, using Modal Functions rather than Sandboxes where
  they fit, since Sandboxes bill CPU and memory at three times the
  rate. Before any Modal run, the runner reads `modal billing rates`
  and `modal billing summary`, computes the worst case (hard timeout
  times everything reserved), and refuses if that is more than the free
  credit remaining. Both trees go to
  the host,
  the same engine runs there, the record comes back. One sandbox runs
  baseline and candidate interleaved; volumes for caches; fail fast
  with log tail. Cross-host sessions follow T1.3's rules. A host
  restored from a memory snapshot keeps its boot_id and machine-id, so
  `host_id` can't tell such hosts apart: the runner must not use
  snapshot restore for cross-host sessions, or must add its own host
  identity. Needs T2.5, open question 1. Paid, small.
- [ ] **T5.12 GPU fingerprint and timing**: GPU, driver, clocks and
  throttle reasons; CUDA-event timing for `reported` benchmarks; peak
  GPU memory as a guardrail; the real `nvidia-smi` and x86
  `/proc/cpuinfo` captures moved here from T2.1. Check what
  `nvidia-smi` prints with no GPU: T2.1's parser reads empty output as
  "no GPUs", which is wrong if it can exit 0 with nothing printed.
  Needs T2.1, T2.3.
- [ ] **T5.13 Serving contract and playbook.** `vllm bench serve` and
  SGLang bench as workloads with `/metrics` counters, building on the
  reference scripts; fixed-seed greedy token equality or a declared
  logprob tolerance as the oracle; settings tried through `confirm`.
  Needs T5.11, T5.12, T3.8.
- [ ] **T5.14 Serving traces**: paid A/A on one GPU type, a second
  session held out. Needs T5.13. Paid.
- [ ] **T5.15 Case study: vLLM** (`vllm-project/vllm`, about 94k
  stars): a full `/perfproof` pass on a popular small model that fits
  an L4, settings and code candidates in one image, interleaved on one
  host. Needs T5.13, T5.14. Paid, within the budget. Effort: max.

### GPU kernels and PyTorch

- [ ] **T5.16 GPU contract and playbook.** torch.profiler, Nsight
  Systems and Compute, occupancy, memory throughput, launch overhead,
  fusion, `torch.compile`, data types; numeric tolerance as the
  oracle. Nsight fixtures captured in one planned paid session. Nsight
  Compute needs GPU performance counters, which hosted containers often
  block; the playbook falls back to Nsight Systems and torch.profiler
  where they are. Needs T5.11, T5.12, T3.8. Paid, small.
- [ ] **T5.17 Kernel traces**: paid A/A, a second session held out.
  Needs T5.16. Paid.
- [ ] **T5.18 Case study: nanoGPT** (`karpathy/nanoGPT`, about 64k
  stars): the training step on a T4, with the loss after a fixed number
  of steps within a declared tolerance as the oracle. Needs T5.16,
  T5.17. Paid, within the budget. Effort: max.

### Agentic and LLM apps

- [ ] **T5.19 `perfproof replay`**: record an app's model and tool calls
  once, replay them so the workload measures the app's own code with no
  API cost, rate limits or network noise. Replays are checked against
  a live sample, and a replay that diverges stops the run. Needs T2.16.
- [ ] **T5.20 Agentic contract and playbook.** The replayed call
  sequence and outputs as the oracle; wasted or repeated model calls,
  sequential calls that could run in parallel, prompt caching, payload
  size, retries. Needs T5.19, T3.8.
- [ ] **T5.21 Agentic traces.** Needs T5.19.
- [ ] **T5.22 Case study: gpt-researcher**
  (`assafelovic/gpt-researcher`, about 30k stars): one research run
  recorded, then replayed; sequential searches and model calls,
  repeated calls and payload size as the likely angles. Needs T5.20,
  T5.21. Effort: max.

## M6 Release

- [ ] **T6.1 Release bar** (lead). Target: full release by about
  2026-11-02 (Faras, 2026-10-19: one to two weeks). The bar:
  - KEEP is trustworthy: T1.6 calibration passes for the procedure in
    force (or the method is fixed until it does). Non-negotiable; it is
    the product's core promise.
  - `/perfproof` finds and proves end to end in Claude Code and Codex
    (T3.8, T3.9, T3.13), with depth tiers (T3.14), brief output
    (T3.15) and a one-line install (T3.16).
  - Each class ships with what it was shown on: its fixture and the
    case studies run by then. A class without a run behind it is
    labelled experimental in the README, not dropped and not claimed.
  - Every README claim links to the run or result behind it.
  Dropped (Faras): outside developer trials and an upstream
  maintainer's acceptance as release gates.
- [ ] **T6.2** README with the case-study and evaluation results, demo
  recording, docs pass, fresh prior-art check.
- [ ] **T6.3** Release: version, changelog, PyPI publish (Faras), repo
  public (Faras).
