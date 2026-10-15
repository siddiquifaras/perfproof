# Failure modes

Every component of perfproof exists to do a job. This page lists the
ways perfproof could fool itself, break code or cause harm, and for each
one: the mechanism that stops it, where that mechanism is enforced, how
we show it works, and what it still can't catch. A failure that matters
is stopped by code; a written rule alone is not a mechanism.

"Code" means the `perfproof` CLI enforces it and a test proves it.
"Contract" means a written, per-target definition the code applies,
such as what "same output" means for a GPU kernel. "Skill" means the
agent's playbook asks for it, and nothing breaks if the agent forgets,
because a later code mechanism catches the result.

## False performance claims

| # | Failure | Mechanism | Enforced by | Shown to work by | Still can't catch |
|---|---|---|---|---|---|
| 1 | Noise read as a speedup | Old and new run in turns, paired ratios, t interval, a minimum effect, three outcomes | Code | Monte Carlo calibration, held-out audit, A/A runs on real machines | Noise unlike anything in the scenarios or the recorded traces |
| 2 | Looking at the numbers until they look good | A look schedule fixed before the first round, error budget split across looks | Code | Calibration with looks on | Nothing within the schedule; costs more INCONCLUSIVEs |
| 3 | Trying many candidates or settings and keeping the luckiest | The best is chosen, then confirmed by a fresh, independent comparison; only that is claimed | Code (`perfproof confirm`) | Simulation with many candidates and no real effect | Nothing, at the cost of one extra comparison |
| 4 | Comparing runs from different machines | `same_host` gate; cross-machine only by opt-in, with the alternating fresh-host design and a label | Code | Gate tests; calibration of the session design | A host pool that hands one version systematically different machines |
| 5 | Rounding at the bar turning "equal" into KEEP | Exact float64 decisions on recorded bounds; intervals widened only by a derived error bound | Code | Full-path regression test with the measured reproducer | Nothing known |
| 6 | Measuring perfproof's own overhead | Nothing perfproof does runs between rounds; overhead per execution measured | Code | Runner overhead test | Overhead below the measurement floor |

## Cheating, intended or not

| # | Failure | Mechanism | Enforced by | Shown to work by | Still can't catch |
|---|---|---|---|---|---|
| 7 | The candidate edits the benchmark, tests or inputs | `lock` gate: hashes of harness, oracle, inputs, configs | Code | Gate tests; the benchmark-edit decoy | A workload that was unrepresentative from the start (see 16) |
| 8 | Old and new run differently (flags, env, command) | `procedure` gate: spec hash, perfproof version, the argv, env and cwd of every round | Code | Gate tests | Differences outside what perfproof launches |
| 9 | The candidate fakes the number it reports | A `reported` metric must come from locked code; wall time is measured alongside and disagreement is flagged | Code | Lock-gate tests; the metric-disagreement test in `compare` | A locked harness that was already wrong |

## Breaking the code

| # | Failure | Mechanism | Enforced by | Shown to work by | Still can't catch |
|---|---|---|---|---|---|
| 10 | Faster but wrong, or crashing under the benchmark | `correctness` gate: the project's tests, output comparison between versions, replay of captured real calls; any benchmark execution that fails gives no sample and fails the candidate | Code, plus a contract per target class for what "same output" means | Mutation testing of the fixtures; the wrong-output decoy | Wrong behaviour no test, output or captured call exercises |
| 11 | The change is never exercised by any check | `coverage` gate on changed lines; where coverage can't be measured, no KEEP without an explicit opt-in | Code | Untested-branch decoy | Lines that run but whose results nothing inspects |
| 12 | A flaky test blamed on the change, or hiding a break | Tests run three times on the baseline first; flaky ones quarantined; lines only they reach count as unverified | Code | Flaky-test fixture | Flakiness that never shows in three runs |
| 13 | Speed bought with memory or tail latency | Guardrails with statuses and absolute limits; KEEP needs compliance shown | Code, plus a contract per target class for which guardrails apply | Memory decoy; guardrail calibration | Costs nobody declared as a guardrail |

## Harm outside the comparison

| # | Failure | Mechanism | Enforced by | Shown to work by | Still can't catch |
|---|---|---|---|---|---|
| 14 | A benchmark touches a real database or paid API | `side_effects` preflight refuses non-local service addresses; a reset command between rounds | Code | Preflight tests; the remote-database fixture | Services configured only inside files the preflight doesn't read |
| 15 | The developer's own uncommitted work is lost | A session refuses a dirty tree unless told how to set it aside; kept changes go on perfproof's own branch; undo touches only files perfproof changed | Code | Session tests | Nothing within the repository |
| 16 | A long run killed by a client timeout, then misread | `--detach`, `status`, `cancel`, `--resume` after re-checking gates and the machine; a partial run never yields a verdict | Code | `kill -9` and cancel tests | Nothing |

## Discovery going wrong

These cost time, not correctness, because nothing is kept without the
mechanisms above.

| # | Failure | Mechanism | Enforced by | Shown to work by | Still can't catch |
|---|---|---|---|---|---|
| 17 | Measuring an unrepresentative workload | The developer confirms every workload, goal and constraint; no comparison runs on an unconfirmed or stale one; the report states where the workload came from | Code (refusal), skill (proposal) | Refusal tests; evaluation | A developer confirming a poor workload |
| 18 | Guessing the hotspot instead of measuring | Every hypothesis carries its evidence by path and hash; profiles summarised by code | Code (log), skill (reasoning) | Log tests; evaluation of discovery | A plausible but wrong reading of good evidence |
| 19 | Retrying ideas that already failed | The hypothesis log, anchored to code, shown with every opportunity | Code (log), skill (judgment) | Log tests; repeat rate in the evaluation | Two ideas that look different but aren't |
| 20 | Claiming more than was shown | Reports render only recorded fields; caps labelled as bounds; claims name the repository | Code (report), skill (wording) | Report snapshot tests; evaluation review | Over-reading by the person reading the report |

## Components and why each exists

| Component | Job | Failure modes it covers | Verified by |
|---|---|---|---|
| `stats` | Intervals, looks, decision, guardrails | 1, 2, 3, 5, 13 | Unit and property tests, calibration, audit |
| `bench` | Spec and lock, runner, gates, compare | 4, 6 to 12, 14 | Gate tests, decoys, mutation testing |
| `perfproof confirm` | Choose then confirm | 3 | Simulation |
| Session state | Branch, undo, dirty-tree refusal, resume | 15, 16 | Session tests |
| Project model and hypothesis log | Persistence, invalidation, refusal on stale entries | 17, 18, 19 | Unit tests |
| `profile` | Run a profiler, summarise folded stacks to the top frames | 18 | Fixtures captured from real tools |
| `report` | Render a record; nothing computed | 20 | Byte-identical snapshot tests |
| `load` | Drive an HTTP service at a constant rate, record latency percentiles | 1 and 13 for services | Fixture service |
| `replay` | Record and replay an app's model and tool calls | 1 and 14 for agentic apps | Fixture app; live-sample check |
| Remote runner | Run the same engine on an SSH host or Modal | 4 for GPU work | Remote fixture run |
| Skills and playbooks | Understand and discover: what to look at, which cheap test to run, how to read each tool | none on their own; their output is checked by the code above | The external evaluation |
| Evaluation harness | Tests whether perfproof helps, on tasks we didn't write | all, from outside | Its own published task set and scoring |

## Removed, and why

| Was planned | Removed because |
|---|---|
| MCP server | Claude Code and Codex both run shell commands and can run them in the background. A CLI serves both, and an MCP layer would block no failure mode. It stays a candidate for a thin wrapper after the release if a client needs it. |
| Background job framework | One detached process and its run directory cover failure 16 (`docs/cli.md`, "Long runs"). |
| One adapter per profiler | Most profilers can produce folded stacks; one summariser covers them. Playbooks say how to run each tool. |
| Discover engine with angle plugins | The angles are measurements the CLI already makes (wall versus CPU time, scaling runs, captured repeated calls, profiles) plus a checklist in the playbooks. |
| Target interface and six target modules | One generic workload (any command, any metric) proves every class. Class differences are contracts and playbooks, plus `load` and `replay` where the failure table needs them. |
| Settings search engine | A setting is a candidate; `perfproof confirm` already handles many candidates safely. |
