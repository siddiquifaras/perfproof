# Who perfproof is for

People differ on two things: how much they know about performance
engineering, and how much agent usage they can spend. One `/perfproof`
has to give each of them the most for what they put in, without a new
setup.

Status: the Python prove path is supported. Depth tiers, `find` and the
other languages are experimental ([targets.md](targets.md)). This page
describes the design, and the skill eval is what checks it.

## The bar

Someone types "make this faster", "find inefficiencies", "why is this
slow" or, helping an open-source project, "what's wrong here?". The
skill should do what a strong performance engineer would: find the real
problem from evidence, find the fix, apply it, prove it, keep only what
is real, and explain it in plain words.

I judge it by measurement, not impression:

- **Starts on plain phrasing**, and only when it should. A trigger test
  covers the phrasings.
- **Finds the right problem.** On tasks with a known bottleneck, it
  names that one.
- **Finds the right fix** and applies it without breaking anything.
- **Right verdict or refusal** on every known-answer task. Never a
  wrong KEEP.
- **Tells everything:** what was slow and why, what changed, the
  evidence, what wasn't checked. A PR-ready write-up only when asked.
- **Beats the agent alone:** more real wins and fewer false claims than
  the same agent without perfproof, at a cost that fits a 5-hour window.

Every change to the skill is scored on the skill eval (T3.19) before it
merges.

## The two axes

| | Knows little about performance | Knows performance engineering |
|---|---|---|
| **Small budget** (free tier, $20) | Wants one clear answer cheaply: is this faster, did it break anything, what one thing to try | Wants the exact tool without paying an agent to think: the CLI alone, their own benchmark, their own idea |
| **Large budget** ($100, $200, team seats) | Wants perfproof to find wins they'd never think of, in plain words | Wants depth: every angle, every profiler, several candidates, long measurements, raw evidence |

## Principles

1. **One command, nothing to set up.** `/perfproof` works on a repo it
   has never seen. The only file it writes is `.perfproof/`, which git
   ignores.
2. **Progressive disclosure.** A beginner sees plain sentences and one
   next step. Everything an expert wants is one word or flag away:
   `--json`, `record.json`, the spec file, the profiler choice, the
   bar, the schedule.
3. **The verdict rule never bends.** Depth, plan and expertise change
   how much perfproof explores and how long it measures, never what
   counts as a KEEP. A cheap run says "can't tell" more often. It can't
   keep something a thorough run would reject.
4. **Value per token.** Cheap steps first: one profile, then the
   Amdahl or roofline bound drops every idea that can't clear the bar
   before any code is written.
5. **Always one suggestion.** Every stop ends in one next step, the
   recommended one first, at most two options ([skill.md](skill.md)).

## How each kind of user is served

**Beginner, small budget**
- `/perfproof light`, or proving a change they already have (light by
  default): one workload, the top idea or two, one attempt, a short
  measurement, the brief report.
- The report says kept, not kept or can't tell, with one sentence of
  reason and what wasn't checked.

**Beginner, large budget**
- `/perfproof` (medium) or `full`: it learns the project, tries the
  best ideas across the angles in [discovery.md](discovery.md), and
  explains each in plain words.
- Kept changes stack on their own branch. Nothing else is touched.

**Expert, any budget**
- Brings their own idea (`/perfproof try <idea>` or a branch to prove).
  perfproof skips understanding and discovery and goes straight to
  gates and measurement.
- Brings their own benchmark, metric, bar, guardrails and profiler
  through the spec. The defaults are named, never hidden.
- Uses the CLI with no agent at all: `perfproof bench define`,
  `perfproof compare --json`. That costs no model usage, so it's also
  the free-plan path for proving a change.
- Reads the evidence: `record.json`, the samples, the profiler output.
  None of it is summarised away.

## Choosing the depth

Depth tiers are experimental. They exist as skill wording, and the
boundaries between them haven't been measured.

- **A depth word picks it:** `/perfproof light | medium | full`. With none,
  the default is `medium`, and proving an existing change is `light`.
- **Cost:** the first time a depth would cost more than a light run,
  perfproof says roughly what it'll use, as a share of a 5-hour window
  (T3.14), and offers the lighter choice. The choice is remembered in
  `.perfproof/`. It never guesses a user's plan.

## What perfproof suggests

| When | It suggests |
|---|---|
| After profiling | Ideas ranked by their bound, with the cheapest test for each |
| After a verdict | The next idea worth trying, or "nothing left can clear the bar" |
| When it can't decide | What would settle it (more rounds, a quieter machine, a test that reaches the changed lines), only if possible |

## How we know it works

- **Every depth:** no wrong KEEP, and a measured cost per kept speedup
  (T3.13, T3.14).
- **Light and medium:** each fits in one 5-hour window on the seat it's
  measured on (T3.14).
- **Expert path:** proving a change through the CLI alone gives the same
  verdict as through the skill.
- **First run:** install to first verdict, timed (T3.16).

None of these has a published number yet. They're checks, not results.
