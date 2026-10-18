# Findings: what the skill must do, and refuse to do

Task T3.11. Each finding states a rule for the `/perfproof` skill, the
rows that motivate it (P1–P10 in [prs.md](prs.md), C1–C4 and X1–X3 in
[agents.md](agents.md)), the failure in
[docs/failure-modes.md](../../docs/failure-modes.md) it answers, and
the CLI command or skill decision it affects. These feed T3.12, the
skill contract.

The agent timings behind these findings are descriptive A/B
comparisons on one loaded laptop, not calibrated verdicts. They can
show that a claim did or did not reproduce there; they can't say KEEP.

## 1. An agent's own number is a hypothesis, never the verdict

Every speed claim, including one the skill's own find path produced,
goes through `compare` before the word "faster" appears anywhere.

- X1 claimed +9% from one unalternated in-process comparison; across
  six alternated runs (four with surviving raw data, two without) its
  change was slower, 0.76–0.95x, winning 0–3 rounds of 10.
- X3 claimed "a faster device list API"; the full device list was
  0.58x on the demo data and 0.71x on a larger copy, slower in every
  round.
- P4 claimed +10% on one GPU and was reverted after slowdowns on
  others: about −10.6% and −12% on two AMD cards and about −52% and
  −28% on an Intel card (two flash-attention settings). P9's low-concurrency gain became a loss in a later run
  against a different baseline, so the claim was not stable.
- C1 is the counter-example: it reported its own result as mixed and
  declined to merge; my timing agreed on the large-batch gain (1.06–1.08x, smaller
  than it reported) and did not reproduce its small-batch slowdown.

Failures 1, 4, 20. Affects: the skill's ending in both find and prove;
`compare` is the only source of a KEEP.

## 2. No measurement means no claim

If the workload can't be run, the skill says so and stops. It never
writes "faster" on the strength of reasoning about the code.

- X2 built the binary, ran nothing, and reported "made token generation
  faster"; measured, 0.98–1.01x, no change. (Whether its sandbox could
  read the model through the symlink was not tested; see agents.md X2.)
- X3 ran nothing and reported "faster"; it was slower.
- P5 asserts that a 3,500x regression is fixed with no post-fix timing
  anywhere in the PR or issue, and nobody asked for one.

Failure 20. Affects: `compare` output, `report` wording; the skill's
"no workload" bad state, which must end in INCONCLUSIVE with the reason,
not in prose.

## 3. The machine's state is part of the record

Record the load with every number, interleave old and new, and refuse
a verdict when the noise swamps the effect. Never quote a quick timing
the skill ran itself.

- The unchanged picolm base ran at 77 ms per token at load 8–11 and
  138 at load 15–23 (C2, X2; both timing files survive). The cause was
  not isolated, so this shows variation across the session, not load
  alone. frames2py's base also varied between sessions, but I have no
  surviving record of its figures.
- X1's and P9's single comparisons went the wrong way. P2's author saw
  the same variant lose 4–8% one day and 5–13% the next.
- C3 itself noted about 15% between median and minimum on the
  baseline alone, and re-ran base and patched before trusting it.

Failures 1, 4. Affects: `doctor` before a session, the `same_host`
gate, the interleaving and stopping rule in `compare`, and the record's
machine fingerprint. The skill decides when to wait rather than
measure.

## 4. Faster but different is REJECT until the developer says otherwise

The correctness gate compares outputs between versions, not only test
results. Where a project has no tests, there is no KEEP without an
explicit oracle.

- C2 was 7.4x faster and its greedy output diverged from the base on
  all three prompts, after 14 to 293 characters. C2 said so itself; an
  agent that hadn't would have shipped it. picolm has no tests, only
  builds.
- P1's parallel path lost or added rows in some cases, found before
  and after merge. P8 read wrong slots and overflowed an int32 index,
  found in review. P9 had a race that hurt accuracy and broke CI, and
  was reverted. In these threads the defects surfaced in review or
  after merge, not from the authors' evidence.

Failures 10, 11. Affects: `check` and the `correctness` gate; the
per-target contract in `docs/targets.md` for what "same output" means
(for LLM serving, a declared tolerance such as token agreement or
perplexity, confirmed by the developer); the skill's "no tests" bad
state.

## 5. A proxy metric is not the metric

Query counts, allocations, kernel microseconds and trace bubbles are
evidence for a hypothesis. The verdict uses the workload's end-to-end
metric, and a proxy that moves the right way while the metric moves
the wrong way is a REJECT.

- X3 cut the device list from 19 queries to 13 and made it slower:
  the joins plausibly cost more than the queries they replaced (not
  profiled). The project's
  own query-count baseline test failed, and its message invites the
  agent to update the baseline.
- P8's 6x is a 9 µs kernel saving; the one end-to-end measurement
  found 1.5–1.9%. P10's bubbles vanished in Nsight while TPOT moved
  about 2%.
- X1 dropped a combined scatter that was fast in isolation and slower
  end to end, then shipped a change it measured at +9% in one
  unalternated comparison; alternated, the same step was slower in
  isolation and in the kernel. C1's first idea trimmed arithmetic for
  10% that it judged to be noise.

Failures 7, 9, 20. Affects: `bench`, which fixes the metric before
any change; `lock`, which refuses an edit to a baseline file or
benchmark; `log`, where the proxy result belongs.

## 6. Measure the path people use, not only the path the change helps

The confirmed workload decides. When a change targets one path, the
report shows the default path too, and a slower default is a REJECT
whatever the targeted path did.

- C3's `?fields=` fix was 10.4x on data with primary IPs and did
  almost nothing on the demo data, where no device has one. X3 gained
  1.57x on that same path and lost 0.58x and 0.71x on the default list.
- P4: one iGPU and one model, applied to every Vulkan device. P7:
  SQLite in memory only. P10: 8 prompts under a profiler. P3: a network
  download written to disk, on unstated machines.

Failure 17. Affects: `init` and `project` (the developer confirms the
workload; the record states where it came from); the skill's choice of
what to compare when the agent's own benchmark is narrower than the
workload.

## 7. A new test must fail on the baseline

The coverage gate alone doesn't catch a test that can't fail. The
skill checks every new test against the baseline, or by reverting the
source, before it counts.

- P5's first tests passed on an empty registry. P8's tests passed with
  the mask deleted, 64 of 64, and the author later wrote that it was the third test of his that
  a reviewer had found not to test what it claimed.
- C1 broke its own code two ways and watched both mutants fail; C3
  reverted its source to confirm its tests failed. Both were unprompted,
  so some agents already do this; the skill makes it a step.

Failure 11. Affects: `check`, with a mutant or baseline run for added
tests; the `coverage` gate.

## 8. Say what else the change touches

Before a verdict, the skill names every file and code path outside the
workload, and the guardrails that apply, and either measures them or
refuses KEEP.

- C3's URL template cache sits in a field every NetBox API serializer
  uses; the workload was one endpoint. X2 flushes output every 16
  tokens, a visible change for anyone reading the stream.
- P3 raised the default buffer size and needed a memory cap before
  merge. P1 ran out of memory at 12 threads. P8 added cold
  recompiles of 25–28 ms. P10's supporting run raised mean TTFT by 32%,
  unremarked.

Failure 13. Affects: `compare`'s guardrails and the per-target
guardrail contract; the skill's diff summary, which lists changes
beyond the ask as the PR description does.

## 9. Use the project's own setup, and say when a gate can't run

The skill detects the configured environment before it builds another,
and when a check is unreachable it says that the check didn't run,
never that the service is down.

- X3's sandbox refused the connection to the configured Postgres on
  localhost and X3 reported "PostgreSQL is unavailable in the
  environment", then claimed a speedup with its tests unrun.
- C3 had a working venv, configuration, database and cache in front of
  it and built a second set: two containers, its own venv, its own
  seeded data. The containers outlived the session.
- X1 and X3 each lost a step to `python` not being on the path.

Failure 14 on the harm side, and the fail-closed rule for a gate whose
state is unknown. Affects: `init` (detects how the project runs), `doctor` (checks that
the configured services answer from where the benchmark will run), and
the skill's "service unreachable" bad state.

## 10. No verdict until every gate has finished

A session that ends with work pending reports no verdict. Long runs
detach and are resumed; a cut-off run says it is partial.

- C3 started the full suite in the background and the headless session
  ended saying it would report the results once the suite completed.
  The suite died with the session; the final message was a promise.
- C1 was cut off by a usage limit with its slow tests still running
  and said so. P7 was merged before the project's benchmark had run on
  it.

Failure 16. Affects: `--detach`, `status`, `--resume`; the skill's
rule that `report` renders only a finished record.

## 11. Profile and measure are different steps

A number taken with a profiler attached is a hotspot, not a speedup.

- P10's author said the gain looked larger in Nsight than in plain
  runs, then supported the PR with a run that had the profiler on.
- C4's stage timers put the frame-publishing share at 64–74%; my
  recheck of four of its numbers (frame size, `add.at` cost, fresh versus
  reused read, publish interval) agreed in direction with smaller
  effect sizes; its percentage shares and option ranking were not
  rechecked.

Failures 8, 18. Affects: `profile` and `measure` as find-path steps
whose output goes to `log`; `compare` runs with nothing attached.

## 12. Hand the reviewer what maintainers ask for

The prove report carries, by default, the things reviewers asked for
in every thread: the platform, the input sizes, how many runs, the
spread, the default path alongside the targeted one, memory and tail
latency, and what was not checked.

- Asked for across P1–P10: scaling with threads or concurrency (P1,
  P9), other platforms (P1, P4) or databases (P7), cold-cache numbers (P1), a memory
  bound (P3), an end-to-end number (P8, P10), positive controls (P5),
  small inputs and edge cases (P8).
- The median time to a decision was 26.4 days (1.2 to 319.4), and
  two of the seven merged PRs were reverted within six days. P4's
  approval carried no text. A record a reviewer can read in
  one place is the fast path this study argues for.

Failure 20. Affects: `report`, and the PR description the skill
writes.

## 13. The find path ends in prove, and its own speed is measured

Unaided, the six agents that changed code spent 51 s to 39 min; the
Claude Code runs cost $2.40 to $3.31 at list price and Codex reported
no cost. The three quickest made claims that did not reproduce here (X1, X2, X3),
and the most careful (C1) was still unconfirmed by its own account.
The skill's prove step has to be cheap enough to run on every
candidate and to replace the agent's own timing, or agents will keep
quoting theirs.

- C4 shows the find path can be right in ranking without a change:
  the skill records that as hypotheses with their evidence, then
  measures.

Failure 18, 19. Affects: `log` for hypotheses; the time-to-verdict
figure the plan asks for in every report.

## Not covered by this study

- No run used `perfproof`, so none of these rules has been shown to
  stop the failure in practice; that is the evaluation's job.
- Three Codex runs and four Claude Code runs, one machine, three
  repositories. Different prompts or a quiet machine could change the
  size of every effect here; the direction of each finding rests on
  more than one row.
- No finding covers a dirty working tree or remote I/O; no row of this
  study touched either.
- Six of the ten PRs involve an agent (four written by one, two
  assisted), found through their disclosure markers; undisclosed agent PRs can't be told apart from human ones.
