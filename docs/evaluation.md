# Evaluation

How we show perfproof works, using evidence neither its authors nor its
own test suite produce. The design and the scoring are fixed here
before any results exist.

The release's evaluation is an **initial external evaluation**: about
30 held-out tasks and 240 agent runs. It is evidence about those tasks
under those conditions, with the uncertainty its counts imply. It is
not proof that perfproof is reliable on every codebase, and nothing
published may describe it that way.

## What is being tested

| Dimension | Question | Measured as |
|---|---|---|
| Discovery | Does it find real opportunities nobody pointed it at? | Share of hidden-fix tasks where a hypothesis names the known bottleneck before any candidate is written; also reported: how often a refuted idea is tried again on unchanged code |
| Implementation | Does it turn a hypothesis into a working change? | Share of tasks with a candidate that passes the independent correctness check |
| Correctness | Does it ever keep a change that breaks behaviour? | Share of kept changes that fail the independent check; target 0 |
| Performance | Are its wins real and worth having? | Precision: share of claimed wins confirmed by the independent measurement. Recall: share of available real wins found |
| Safety | Does it refuse what it should? | False-KEEP rate on negative tasks and decoys |
| Efficiency | What does a verified result cost? | Wall time, tokens and compute to the first verified KEEP |
| Reproducibility | Can someone else get the same verdict? | Verdict agreement when a saved spec is re-run on a second machine |

Precision of claims and the false-KEEP rate are the primary scores.
The rest are reported alongside them; none is traded against a primary
score.

## Task families

1. **Known changes from public repositories.** Merged performance pull
   requests with evidence of their effect, and the other kinds that
   matter just as much: pull requests reverted because they didn't
   help, regressions, and changes reverted for breaking behaviour.
   Mined with `gh search prs` and from release notes, kept only where
   the parent commit builds and a workload exercises the change.
2. **Published benchmarks.** GSO, SWE-Perf and SWE-fficiency tasks
   whose environments run here. The
   [2026 cross-machine audit](https://arxiv.org/abs/2607.01211) is used
   to pick and flag tasks (it found reference patches pass in every
   replay for 39 of 102 GSO, 11 of 140 SWE-Perf and 411 of 498
   SWE-fficiency tasks), not as a source of labels.
3. **Hidden-fix tasks.** The code as it was just before a known merged
   optimization: the change's base commit, exported without later
   history, or a later commit with the change reverted where the base
   no longer builds. The agent gets only the repository and "make this
   workload faster".
   No issue text, no pull request, no hint of where. This is the test
   of discovery.
4. **Nothing to keep.** Code after its optimization landed, or code
   where public attempts to speed it up failed. The right result is no
   KEEP.

Every target class gets tasks. Python and native tasks are plentiful;
web, serving, GPU and agentic tasks are fewer and dearer, and their
counts are reported as they are, with wide intervals.

## Ground truth, independent of perfproof

A label is never produced by perfproof's own code, or the evaluation
would be measuring perfproof against itself.

- **Correctness**: the repository's full test suite at that commit,
  plus any task-specific checks, run on the reference change.
- **Performance**: measured with a separate procedure. Many interleaved
  repetitions (at least 200 pairs) on a quiet machine, with instruction
  counts on Linux where possible, analysed with a different
  implementation (pyperf's tooling and an independent bootstrap), on
  two machines. The label is one of: an improvement at or above the
  bar, below the bar, a regression, or unstable.
- **Unstable** tasks, where the two machines disagree or the effect
  doesn't reproduce, are excluded from the scores and reported
  separately with their count.

## How the runs are matched

- Each task runs under two conditions: an agent with perfproof (skill
  and CLI), and the same agent without it, told to make the workload
  faster and report the speedup, free to benchmark however it likes.
- Same model, effort level, tools (apart from perfproof), wall-time cap
  and token cap in both conditions. Claude Code runs headless with
  `claude -p`, Codex with `codex exec`.
- Every run starts in a fresh, isolated checkout. Git history after the
  task commit is removed, and the network is limited to package
  mirrors, so the answer can't be fetched.
- Leakage is checked after each run: transcripts are searched for the
  reference commit, its title and its pull request number.
- Each pair of task and condition runs at least twice, so run-to-run
  variance of the agent shows up.

The headline comparison is about claims. Without perfproof, an agent's
reported speedups are checked against the independent measurement; with
perfproof, its verdicts are. The question is which claims can be
trusted.

## Counting rules

Fixed before any run, so no result can be dropped after the fact.

| Situation | How it counts |
|---|---|
| Run hits a rate limit before finishing | Retried with the same task and settings, at most twice, after waiting out the limit. Retries are recorded. If it still can't finish, it is **incomplete**. |
| Run crashes, times out, or the agent gives up | **Incomplete**. No retry: that is the agent's real behaviour. |
| Incomplete run | Counts as a failure for discovery and implementation. It never counts as a correct refusal for safety or as a correct "nothing to keep". Incomplete counts are reported per condition. |
| Task's environment breaks (won't build, dependency gone) for both conditions | The task is **failed** and excluded from every score, listed with the reason. If it breaks for only one condition, the run is incomplete for that condition. |
| Unstable ground truth | Excluded from performance scores and reported apart, as above. |
| A KEEP that independent measurement shows is not a real win | A false claim. Counted against precision, whatever the verdict's reason. |
| A KEEP that fails the independent correctness check | A correctness failure. Counted against correctness and precision. |
| Negative results: perfproof scoring worse than no perfproof on any dimension | Published as they came out, in the same table. |

The two repetitions of each run are scored separately, and their
disagreement is reported as run-to-run variance of the agent.

## Splits

- **Development tasks**: used while writing the skills and playbooks.
- **Held-out tasks**: never seen during development, run once per
  release candidate, scored by the rules above.

A task used for development never moves to the held-out set.

**How the held-out set is chosen.** The mined manifests are a
candidate pool. The selection rule is fixed here before any task is
built for it:

1. Candidates are ordered by `sha256(salt + task id)`, with the salt
   `perfproof-heldout-2026-10-16`.
2. Per target class, the held-out set takes, in that order, the first
   candidates that build and run here (or on the class's GPU host): two
   known-change tasks, one hidden-fix task, one nothing-to-keep task and
   one published-benchmark task, five in all. Where a class has no
   candidate of a family, the next known-change task takes its place.
3. Whether a candidate builds is the only thing looked at. No
   perfproof run, agent run or label of a held-out candidate is seen
   before the list is committed.
4. Every candidate passed over is recorded with the reason it didn't
   build, so the selection can be re-derived.

The development set is drawn from the rest of the pool the same way,
with its own salt.

## What we publish

`eval/` holds every task manifest (repository, commit, workload,
oracle, label, the evidence for the label), the runners, the pinned
environments, and the raw results. Rates are reported with
Clopper-Pearson intervals, per target class and overall. Small classes
are reported with their real counts, never folded into an average that
hides them.

## Limits

- A task set is a sample of real performance work, not all of it.
- Hidden-fix tasks reward finding one known answer; a different real
  win is scored by the independent measurement, not missed.
- One case study per class shows the cycle works on that repository. It
  is evidence, not proof that the class is supported everywhere.
