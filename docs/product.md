# Who perfproof is for, and how one command serves them all

perfproof is used by people who differ on two axes: how much they know
about performance engineering, and how much agent usage they can
spend. One `/perfproof` has to give each of them the most value for
what they put in, without asking anyone to learn a new setup.

## The bar

Someone types "make this faster", "find inefficiencies", "why is this
slow" or, helping an open-source project, "what's wrong here?", and
the skill does what a strong performance engineer would: finds the
real problem from evidence, finds the fix that removes it, applies it,
proves it, keeps only what is real, and explains all of it in plain
words. It is judged by measurement, not by impression:

- **Starts on plain phrasing**, and only when it should (a trigger
  test over phrasings).
- **Finds the right problem**: on tasks with a known bottleneck, it
  names that one.
- **Finds the right fix** and applies it without breaking anything.
- **Right verdict or refusal** on every known-answer task; never a
  wrong KEEP.
- **Tells everything**: what was slow and why, what changed, the
  evidence, what was not checked; for open source, a PR-ready write-up
  with the evidence maintainers ask for.
- **Beats the agent alone**: more real wins and fewer false claims than
  the same agent without perfproof, at a cost that fits a 5-hour window.

Every change to the skill is scored on the skill eval (T3.19) before it
merges.

## The two axes

| | Knows little about performance | Knows performance engineering |
|---|---|---|
| **Small budget** (free tier, $20) | Wants one clear answer cheaply: is this faster, did it break anything, what one thing should I try | Wants the exact tool without paying an agent to think: the CLI alone, their own benchmark, their own idea |
| **Large budget** ($100, $200, team seats) | Wants perfproof to find wins they would never think of, and to explain them in plain words | Wants depth: every angle, every profiler, several candidates, long measurements, and the raw evidence |

## Principles

1. **One command, nothing to set up.** `/perfproof` works on a repo it
   has never seen. The only file it writes is `.perfproof/`, which git
   ignores. Nobody changes their tools to use it.
2. **Progressive disclosure.** A beginner sees plain sentences and one
   next step. Everything an expert wants is one word or one flag away,
   never in the beginner's path: `--json`, `record.json`, the spec
   file, the profiler choice, the bar, the schedule.
3. **The verdict rule never bends.** Depth, plan and expertise change
   how much perfproof explores and how long it measures. They never
   change what counts as a KEEP. A cheap run says "can't tell" more
   often; it can't keep something a thorough run would reject.
4. **Value per token.** Cheap steps first: one profile, then the
   Amdahl or roofline bound drops every idea that can't clear the bar
   before any code is written. The agent's turns are spent where the
   expected gain is highest (T3.14, T3.15).
5. **Always one suggestion.** Every stop ends in one next step, the
   recommended one first, at most two options (`docs/skill.md`).

## How each kind of user is served

**Beginner, small budget.**
- `/perfproof light`, or proving a change they already have (light by
  default): one workload, the top idea or two, one attempt, a short
  measurement, the brief report.
- The report reads: kept, not kept, or can't tell, with the reason in
  one sentence and what was not checked.

**Beginner, large budget.**
- `/perfproof` (medium) or `full`: perfproof learns the project, tries
  the best ideas across the angles in `docs/discovery.md`, and explains
  each one in plain words: what was slow, why, and what the change
  does about it.
- Kept changes stack on their own branch, and nothing else is touched.

**Expert, any budget.**
- Brings their own idea (`/perfproof try <idea>` or a branch to prove).
  perfproof skips understanding and discovery for it and goes straight
  to gates and measurement.
- Brings their own benchmark, metric, bar, guardrails and profiler
  through the spec. The defaults are named, never hidden.
- Uses the CLI directly with no agent at all: `perfproof bench define`,
  `perfproof compare --json`. This costs no model usage, which also
  makes it the free-plan path for proving a change.
- Reads the evidence: `record.json`, the samples, the profiler output.
  None of it is summarized away.

**Free plan.**
- The CLI proves a change with no agent.
- With an agent, `light` keeps the session small.

## Choosing the depth

- A depth word picks it: `/perfproof light | medium | full`.
- If none is given, the default is `medium`, and proving an existing
  change is `light`.
- The first time a depth would cost more than a light run, perfproof
  says roughly what it will use, measured as the share of a 5-hour
  window (T3.14), and offers the lighter choice. The user's choice is
  remembered in `.perfproof/` so it isn't asked again.
- perfproof never guesses a user's plan. It states the cost and lets
  the user choose.

## What perfproof suggests

- After profiling: the ideas ranked by the most each could gain (its
  bound), with the cheapest test for each.
- After a verdict: the next idea worth trying, or "nothing left can
  clear the bar", which is a useful answer in its own right.
- When it can't decide: what would settle it (more rounds, a quieter
  machine, a test that reaches the changed lines), and only what is
  actually possible.

## How we know it works for each kind of user

- **Every depth:** no wrong KEEP, and a measured cost per kept speedup
  (T3.13, T3.14).
- **Light and medium:** each fits in one 5-hour window on the seat
  they are measured on (T3.14).
- **Expert path:** proving a change through the CLI alone, with no
  agent, gives the same verdict as through the skill.
- **First run:** from install to first verdict, timed (T3.16).
