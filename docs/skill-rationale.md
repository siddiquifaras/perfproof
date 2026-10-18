# /perfproof contract: rationale and evidence

Why each row of [skill.md](skill.md) exists, which study row motivated
it, and what the CLI still lacks. Messages are in
[skill-messages.md](skill-messages.md).

## Evidence keys

`§` is a numbered section of a transcript; "Step" is the transcript's
own step number.

| Key | Where |
|---|---|
| `nb:F-nn` | `studies/hand-runs/netbox/findings.md`, finding F-nn |
| `nb:e1` to `nb:e4` | `studies/hand-runs/netbox/e1-bare.md` to `e4-prove-change.md`, `§n` |
| `nb:Setup` | `studies/hand-runs/netbox/README.md`, "Setup" |
| `fp:finding n` | `studies/hand-runs/frames2py/findings.md`, finding n |
| `fp:T1` to `fp:T4` | `studies/hand-runs/frames2py/1-bare.md` to `4-existing-change.md`, "Step n" |
| `pl:finding n` | `studies/hand-runs/picolm/findings.md`, finding n |
| `pl:T1` to `pl:T4` | `studies/hand-runs/picolm/1-bare.md` to `4-existing-change.md`, "Step n" |
| `pr:§n` | `studies/pr-study/findings.md`, section n |
| `pr:P1` to `pr:P10` | `studies/pr-study/prs.md`, row Pn |
| `pr:C1` to `pr:C4`, `pr:X1` to `pr:X3` | `studies/pr-study/agents.md`, run Cn or Xn |
| `tok` | `studies/tokens/tables.md` |
| `failure n` | `docs/failure-modes.md`, row n |

## What this contract does not rest on

- No study row exercised a dirty working tree (BS-2) or a run outside a
  repository (BS-1): those come from failure 15, the plan and the CLI's
  error codes.
- No study ran on a calibrated procedure, so R-1 to R-3 and R-12 follow
  `docs/method.md` and the schema, not a transcript. R-11 follows the
  method's guardrail row and the gate statuses.
- The studies' user replies were simulated by the hand-runner.
- No run used the skill itself; every transcript is a person playing it
  with the CLI as it stood. T3.8 and T3.13 test whether an agent given
  this contract behaves as written.

## Why: words and conventions (skill.md, sections 1 and 2)

- Rule 1 and R-13. Every compare in the three hand-runs ended
  INCONCLUSIVE because the thin path cannot KEEP (nb:README "Every
  compare that ran"; fp:findings "What happened"; pl:findings "What
  happened"). Reports that put a direction on those results ("points
  the other way", "wouldn't merge either on this evidence": fp:T4
  report) are what the rule forbids; the studies' own reports still
  contain them. nb:F-27 asks for one fixed sentence; fp:finding 1 asks
  whether the report may say more than INCONCLUSIVE.
- Rule 2. `compare`'s text prints "speedup {x}x" for an uncalibrated
  estimate (`src/perfproof/commands/compare.py`, `_text`).
- Rule 3. nb:F-30, nb:e4 §6: an interval of 0.65x to 0.92x printed
  "Regression: no".
- Rule 4. Cap near the bar: nb:F-22, nb:e2 §2 (1.035x against 1.03x).
  An estimate above its cap: nb:e3 §5 (5.07x against 4.27x), pl:T2
  Step 5. Profile fractions: `Hotspot.self_fraction` is a frame's own
  time, `total_fraction` includes callees, and `basis` says what the
  fraction is of (`src/perfproof/schema.py`, `Hotspot`;
  `commands/profile.py`).
- Rule 5. pl:finding 2 (a prediction matched a confounded run).
- Rule 6. nb:F-24, pr:§2 (X2, X3 and P5 claimed a speedup with no
  measurement).
- Rule 7. nb:F-15 (577.8 ms then 1,324.1 ms for the same measurement);
  fp:finding 17; pr:§3.
- Rule 8. Failure 5 (rounding at the bar).
- Rule 9. cli.md: "a gate that isn't available on this target blocks
  KEEP; it never passes". pl:finding 3 and fp:finding 2: a waived
  runtime gate and an unmeasurable memory guardrail were easy to miss.
- Result line. pr:§2 (a speed question that cannot be measured must not
  end in prose that sounds like an answer). A session with no run has no
  verdict, so it does not claim INCONCLUSIVE (C-25).
- Permission and writes. `.perfproof/` is excluded through
  `.git/info/exclude` (`bench/snapshot.py`, `exclude_state_dir`);
  nb:F-04 (the harness must be committed to a branch of the user's
  repo, a commit the user did not ask for); pr:C3 (a second environment
  and two containers outlived the session); failure 15.
- Token reading. T3.17 (`tok`): in the T3.10 hand-runs about 70% of
  cost was cache re-reads of a growing context, and perfproof CLI
  output was about 3%; file reads and search about 22%, shell commands
  the agent wrote about 20%. So the contract's levers are fewer turns,
  targeted reads and CLI commands in place of scripts; brief output
  stays but is not the main lever (plan T3.15). fp:T1 Step 7 and the
  `samples.npz` reads are the hand-written steps C-09 and C-13 would
  replace.

## Why: input router (section 3)

- Depth. Plan T3.14: `medium` by default, proving an existing diff is
  `light`, one declared table; a lighter tier gives more INCONCLUSIVE,
  never a looser KEEP.
- IN-1. nb:e1 §2 (the proposal and three questions; the confirmation was
  recorded nowhere: nb:F-09); fp:T1 Step 2 and fp:finding 9; pl:T1
  Step 3 and pl:finding 22; cli.md ("`init` proposes and asks").
- IN-2. nb:e2 §2 (the device list was not the outlier); pl:T2 Step 1
  ("first token" means prefill); fp:T2 Step 3. The premise check
  follows workload confirmation because nothing runs on a proposed
  workload (discovery.md, "Revalidation"; failure 17). nb:e4's line
  names two branches inside a sentence; as free text the skill asks
  once (IN-2c).
- IN-3. nb:e3 §2 and nb:F-26 (the cost was in a file the user did not
  name); fp:T3 Step 5 and fp:finding 20 (the named function was 1.0% of
  an execution); fp:T3 Step 2 and fp:finding 19 (a copy that looked
  redundant made the scatter about 17x as long); pl:T3 and pl:finding
  19.
- Whole-argument rule. A phrase that merely contains a word that
  resolves as a branch or identifier (an English word that is also a
  function name) would otherwise start the wrong path.

## Why: prove path (section 4)

- PF-1 to PF-3. pl:T1 correction and pl:finding 1 (a candidate cut from
  the wrong branch measured two changes, credited the gain to the wrong
  one, and every gate passed); pl:T4 merge-base check; pl:finding 20;
  nb:F-29 and nb:e4 §2 to §3 (a branch without the harness got a
  recorded REJECT naming neither commit); fp:finding 24. Uncommitted
  work: `compare --candidate` takes a directory and the record carries
  `candidate_dirty` (`schema.py`, `RunRecord`).
- PF-4. pl:T4 PR A (a table "with #16's flags"); fp:T4 branch 1 (the
  developer moved off bincount on purpose); pr:§1.
- PF-5. fp:finding 28 (no command says which benchmark covers a diff);
  failure 17.
- PF-6. pr:§7 (P5's first tests passed on an empty registry; P8's passed
  with the mask deleted); cli.md session (coverage REJECT).
- PF-7. nb:F-28 (4.73x to 5.44x and 5.17x to 6.37x ten minutes apart).
- PF-8. pr:§12 (what maintainers ask for in every thread), plan M3
  intro (time to a verdict is measured), pr:X1 and pr:X3 (a claim
  written without a measurement).
- The whole path: nb:e4, fp:T4, pl:T4, pr:findings 1, 2, 4, 5, 7, 12.

## Why: find path (section 5)

- Order and stops: cli.md session. Cap at or below the bar: nb:e2 §2.
  Cheap test refuting an idea: fp:T1 H1, fp:T3 Steps 1 to 3, pl:T2 H2;
  discovery.md "The chain". Cheap tests that do not match the workload:
  fp:finding 18 (a 2^11 to 2^15 fit predicted 1.17x, compare gave
  0.73x). Wrong process: nb:F-01 (96.8% `socket.recv_into`).
  Line attribution for vectorised code: fp:finding 6. History: fp:T1
  Step 8 (`6b09b2d`), fp:finding 19. Hidden state: fp:finding 15 and
  fp:T4 Step 3. What else the change touches: pr:§8. One environment:
  pr:C3, pr:§9.

## Why: bad states (section 6)

- BS-1, BS-2. Failure 15; plan T3.8 ("dirty-tree refusal"); the prove
  path needs no clean tree because candidates and baselines are checked
  out into `.perfproof/worktrees/`; `profile` runs in the working tree
  as it is and leaves `perfproof-out/` there (fp:finding 8, fp:T1
  Step 7), so the check ignores declared outputs and C-09 asks for a
  worktree. The find path refuses rather than offering a choice,
  because perfproof must not touch uncommitted work and a profile of a
  tree that differs from the baseline misleads.
- BS-3. pr:§9 and pr:X3 (the sandbox refused Postgres on localhost; the
  agent reported "PostgreSQL is unavailable", ran no tests and claimed a
  speedup that measured 0.58x and 0.71x); nb:F-16.
- BS-4. nb:F-18 and nb:e1 §2 (NetBox's test config points at
  `localhost:5432`, another project's Postgres on that machine); pr:C3;
  nb:F-07; failure 14; method.md "Side effects".
- BS-5. nb:e1 §2 and nb:F-09; pl:T1 Step 1 and pl:finding 22; pr:X2,
  pr:X3, pr:P5; fp:finding 10 (the script that decides the number was
  never shown); fp:finding 11 and pl:finding 10 (inputs outside the
  repository cannot be locked); nb:F-04.
- BS-6. pl:T1 Steps 1 and 3, pl:finding 16 (no tests, CI only builds),
  pl:finding 17 (two empty outputs compared "identical"), pl:finding 15
  (-ffast-math passed one prompt); fp:finding 14 (full suite 524 s at
  load 23); fp:finding 13 (a change that broke mypy passed the gate);
  nb:F-14 (14 runs of a Django suite); pr:C2 and pr:§4 (7.4x faster, the
  output diverged on all three prompts).
- BS-7. `compare` raises `broken_baseline` for a baseline that cannot
  start; `profile` raises `workload_failed` (`commands/compare.py` and
  `commands/profile.py` docstrings); `compare` has no `workload_failed`.
  nb:Setup (a Postgres pull stalled, the demo dump's triggers failed,
  no `pg_config`); pl:finding 4 (no build step in the spec). A failed
  `runtime` gate in a returned record is a REJECT (R-8), not this row:
  fp:finding 3 and fp:T2 Step 10 (a leftover `.venv`), pl:finding 3
  (`/bin/sh` for a native build).
- BS-8. nb:F-02 and nb:e1 §7 to §8 (1,982 differences, all a port in a
  URL); pl:finding 18 and pl:T1 Step 3 (end-of-text after one token);
  fp:T3 Step 5 and fp:finding 20.
- BS-9. As PF-2 and PF-3.
- BS-10. fp:T1 Step 10 and fp:finding 16 (load 40.5: cv 0.54 to 0.66,
  425 s; load 2.7: cv 0.08 to 0.11, 99.5 s); nb:e1 §1; pl:finding 23
  and pl:finding 13 (load rose to 87 mid-run, the warning showed
  10.23); pr:§3. Warning codes and advice: `machine/warn.py`,
  `commands/doctor.py`.
- BS-11. nb:F-14 (1,505.9 s, no round run, no progress file); fp:finding
  25; pl:finding 23 (up to 979 s foreground); pr:§10, pr:C3, pr:C1 (a
  final message that was a promise); failure 16; cli.md "Long runs".
  Today `compare --detach` and `--resume` answer `not_built`
  (`commands/compare.py`); an option the CLI does not define answers
  `usage` (`cli.py`).
- BS-12. nb:F-13 (`spec_conflict` without guidance), fp:finding 22
  (ambiguous hash), nb:F-10 and fp:finding 12 (undeclared guardrails),
  pl:finding 8 (a schedule `bench define` accepted and `compare`
  refused). Fail closed: CLAUDE.md "Design".

## Why: results (section 7)

- R-1 to R-3: method.md "Outcomes and reasons".
- R-4. nb:e4 §5 to §6 and nb:F-03 (a pin 23 to 15 for the good change,
  23 to 27 for the one that added queries; locking the pin makes any
  improvement impossible, not locking it lets a candidate loosen
  pins); pr:§4; fp:finding 15; pr:X3 (19 to 13 queries tripped the
  project's own baseline test while the list got slower).
- R-5. cli.md session (`io.py:152-158`); failure 11; pr:§7.
- R-6. pl:T4 PR B and pl:finding 7 (a locked Makefile; a flags PR can
  never pass).
- R-7. nb:F-18.
- R-8, R-9. method.md: an integrity REJECT is "the experiment itself is
  not valid". `verdict.reason == "integrity"` with `verdict.gate`
  naming `lock`, `side_effects`, `runtime`, `procedure` or `same_host`.
  Waiver label: `allow_runtime_change` (pl:finding 3).
- R-10. method.md, "Outcomes and reasons", last row; pl:T1 Step 4.
- R-11. method.md: a guardrail whose compliance cannot be established is
  INCONCLUSIVE naming the guardrail; `GateStatus.not_available` blocks
  KEEP. Unexercised in the studies.
- R-12. cli.md ("INCONCLUSIVE comes with what would settle it"); nb:F-23
  (the by-hand figure of about 12,000 rounds was an inference).
- R-13. The nine hand-run verdict tables; BS-10's sentences replace the
  studies' prose for the same warnings.

## Appendix: what the CLI is missing

Proposals for follow-up tasks, not changes made here. The contract
names C-02, C-04 and C-22; the rest are listed for the lead.

| ID | What is missing | Study finding | Planned in |
|---|---|---|---|
| C-01 | `profile` follows the server child and warns when the profiled process only waited | nb:F-01 (high) | none |
| C-02 | `bench define --check`: run the harness twice on the baseline, diff declared outputs, refuse an empty output, report a `reported` value against its resolution and the timed part against the whole execution | nb:F-02 (high); pl:findings 17, 18, 8; fp:finding 20 | none |
| C-03 | `compare` refuses before a record exists when the candidate lacks locked files; reports commits between baseline and candidate; the record names repository and candidate commit | nb:F-29 (high); pl:finding 1 (high); fp:finding 24 | none |
| C-04 | `compare --json` carries gate detail, failing test ids and output differences (capped), `warnings[]`, `guardrails[]` and the machine load at start and end. Today only `verdict` and `gates[].{gate,status}` | nb:F-14; pl:finding 13 | T3.15 |
| C-05 | A distinct reason for a setup fault instead of REJECT integrity | fp:finding 3 (high); nb:F-29; pl:finding 3 (block) | none |
| C-06 | `measure` counts queries per request | nb:F-08 (high) | T3.4 (angle absent from its text) |
| C-07 | A baseline noise check and a cheap-test command | fp:finding 17 (high); nb:F-15 | none |
| C-08 | `measure --set` | pl:finding 21 | T3.4 |
| C-09 | `profile` restricted to the timed region or a function, accepting `uv run`, a macOS native profiler, per-thread names normalised, run in a worktree | fp:findings 4 (block), 5, 8; pl:findings 11 (block), 12 | none |
| C-10 | INCONCLUSIVE states the rounds and minutes that would settle it | nb:F-23; fp:finding 23 | T3.7 |
| C-11 | Do not print `Regression:` when its rule cannot fire | nb:F-30 (high) | none |
| C-12 | Load re-read at the end of a run | pl:finding 13 | none |
| C-13 | `peak_memory` judged, not "unmeasurable"; T2.15 first decides what the metric is, since `wait4` gives the largest single process | fp:finding 2 (high); pl:finding 14; nb:F-20 | T2.15 |
| C-14 | `--detach`, `status`, `cancel`, `--resume`, `progress.json` | nb:F-14 (high); fp:finding 25; pl:finding 23 | T3.6 |
| C-15 | `doctor` checks services, tools and what listens on a local port | nb:F-16; nb:F-18; fp:finding 26 | none |
| C-16 | `init` and `project` | nb:F-09; fp:finding 9 (block); pl:finding 22 (block) | T3.2 |
| C-17 | `bench list` with no name; `show` by prefix; versions with what changed | nb:F-21; fp:findings 22, 27; pl:finding 9 | none |
| C-18 | Spec: build step per tree; outputs and `reported` from stdout; locked paths outside the repo; wrapper scripts locked; correctness env; target class; latency-sum objective; `define` refuses what `compare` refuses | pl:findings 3 to 6, 8; fp:findings 10 to 12; nb:F-10 to F-12 | none |
| C-19 | A pin-file rule: a pinned count may change only toward fewer queries | nb:F-03 (high) | none |
| C-20 | `compare` runs a spec with a reset | nb:F-07 | T2.19 |
| C-21 | Correctness: test granularity without JUnit XML; project CI checks; several inputs; subset then full suite | nb:F-19; fp:findings 13, 14; pl:finding 15 | none |
| C-22 | Which workloads execute a function; which workload covers a diff | nb:F-25; fp:finding 28 | none |
| C-23 | `confirm` for ranking | nb:F-28 | T3.5 |
| C-24 | `--brief` outputs with a byte cap | plan T3.15 | T3.15 |
| C-25 | A record or `report` for a session that stopped before `compare` | pr:§2 | none |
| C-26 | A cap and its workload stored with a hypothesis | nb:F-22 | partly T3.1 |
| C-27 | A dirty-tree refusal and an undo of only perfproof's files | failure 15 | T3.8 |
