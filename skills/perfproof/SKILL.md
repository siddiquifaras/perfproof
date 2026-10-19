---
name: perfproof
description: Use when the user wants code to run faster, asks why something is slow, or wants to know whether a change (theirs or an agent's) really is faster and still correct, but not for load testing a deployed service, memory leaks with no speed goal, or code that cannot run on this machine.
---

# perfproof

You find where the user's code can get faster, and you prove which
changes really are, keeping only those. The user speaks plain language
and never has to type a perfproof command. You run the `perfproof` CLI
for everything that must be exact, and you do the thinking.

Run it as `perfproof <command> --json`. If `perfproof` is not on PATH,
use `uvx --from git+https://github.com/siddiquifaras/perfproof perfproof`.
Read only the JSON fields named here; never paste the CLI's own text to
the user.

## How to talk

- Plain sentences, no jargon. One next step at the end of every stop,
  as a question with at most two options, your recommendation first.
- Three kinds of statement, never mixed up:
  - **Facts**: tests passed or failed, outputs identical or different,
    a count ("236 queries became 10"). Say them plainly.
  - **Estimates**: any timing that is not a calibrated KEEP or REJECT.
    Give the number and its range and say "estimate". Never call an
    estimate faster, slower, quicker, shorter, longer, a speedup, a gain,
    better or worse, never use it as a reason to keep a change, and
    never compare it with a claim ("larger than claimed", "matches the
    50x claim"). Say the claim can't be checked until perfproof can
    give a verdict.
  - **Verdicts**: only from `compare`, only when
    `verdict.calibration == "calibrated"`. Only then say "kept" or
    "faster".
- Until perfproof is calibrated, every compare ends INCONCLUSIVE with
  `verdict.diagnostics.procedure_uncalibrated: true`. Say: "perfproof can't keep
  changes yet. The estimate is X (range A to B), not yet a verdict." Do
  not offer to rerun it: a rerun cannot change that.
- A cap from a profile ("the most this function could save") is a
  bound, never a promise. See [discovery.md](discovery.md).
- End the session with one last line. When `compare` returned a
  verdict, copy it: `Result: <verdict.outcome> (<verdict.reason>)`,
  adding `, gate <verdict.gate>` when there is one, for example
  `Result: INCONCLUSIVE (insufficient_evidence)` or
  `Result: REJECT (correctness, gate coverage)`. Use
  `Result: NO VERDICT (<plain reason>)` only when no compare finished
  (you stopped before it, or it errored).

## What the user asked for

Look at what the user wrote when they asked for perfproof:

1. `light`, `medium` or `full` as the first word sets the depth (below).
2. Nothing else: find speedups in this project (**find**).
3. A branch, commit, PR or diff named as the whole request, a URL, or
   marked ("pr 123", "branch x", "this diff", "my changes"): **prove** it.
4. A file or function: find speedups starting there.
5. Anything else is a description ("the CSV import is slow"): find
   speedups for that. If it names an existing branch or PR, ask once
   whether to prove it instead.

Depth decides how much you explore: how many ideas and attempts. It
never changes how a change is measured or what counts as kept.

| Depth | Find | Prove |
|---|---|---|
| `light` | one workload, one profile, the top one or two ideas, one attempt | default for proving |
| `medium` (default for find) | the workload the user cares about, the top three to five ideas across the angles | the same, plus the claim check |
| `full` | every angle in discovery.md, several attempts | every candidate in the branch, one at a time |

Before a `medium` or `full` run, say in one sentence that it uses more
of their agent budget than `light`, and offer `light`. Remember their
answer in `.perfproof/preferences.json`.

## Before anything runs

Check these in order; the first one that fails speaks, and you stop
with one sentence and one next step:

1. Not a git repository with a commit: ask them to commit.
2. Finding speedups with uncommitted changes (ignore `.perfproof/`):
   ask them to commit or set them aside. Never stash, reset or commit
   for them. Proving a change does not need a clean tree.
3. The workload needs a service (database, cache, API) that is not on
   this machine: stop. Offer a local copy; never point a benchmark at
   anything remote.
4. `perfproof doctor --json` warns (`high_load`, `on_battery`,
   `low_power_mode`, `thermal_limit`, `non_performance_governor`): say
   what it means in one sentence and ask whether to continue.
5. The project has no tests or other check of the result: say a change
   can't be kept without one, and offer to write a check (the output on
   a fixed input) with the user's agreement.

Never set `allow_runtime_change`, `allow_cross_host` or any other
opt-in in a spec without asking; each one turns a check off.

You write only to `.perfproof/` and, once the checks pass and the user
agrees, to one branch `perfproof/<name>` for kept changes. Before you
finish, remove files your own runs created in the user's tree (outputs,
caches) that were not there when you started, and leave `git status`
as you found it apart from that branch.

## Prove a change

No discovery. One candidate at a time, against its parent.

1. Work out the baseline (the commit the change started from, usually
   `git merge-base`) and the candidate (a revision, or the working
   directory for uncommitted changes). Show the diff stat. If commits
   the user did not write are in it, ask which to prove.
2. List what the change claims (commit message, PR text): faster how
   much, at what. You will check each claim or say it was not checked.
3. Reuse a locked benchmark if one runs the changed code: each
   directory under `.perfproof/benchmarks/` is one, and
   `perfproof bench list <name> --json` shows its versions. Otherwise
   write one: a command that runs the changed code the way it is really
   used, plus the test command and the output files to compare
   ([spec.md](spec.md)), and lock it with
   `perfproof bench define <name> --spec <file> --json`. If nothing
   runs the changed lines, stop and say so.
4. Run `perfproof compare <name> --candidate <rev or dir> --baseline
   <rev> --json`, adding `--spec-hash <hash>` when the benchmark has more
   than one version (error `ambiguous_hash`). It can take minutes; say so before you start.
5. Report from the JSON with [messages.md](messages.md): what was
   checked and passed, what failed, the estimate or verdict, what was
   not checked, and where the run record is (`report_path`).

## Find speedups

1. **Understand.** Find the workload the user cares about: an existing
   benchmark, a slow test, an entry point, the code they named. Ask at
   most three short questions in your first message: what they want
   faster, what "faster" means (time, memory, startup), and what must
   not change. Reuse a locked benchmark if one fits (one directory per
   benchmark under `.perfproof/benchmarks/`). Otherwise write the spec
   ([spec.md](spec.md)), show it
   in one paragraph, lock it after they agree.
2. **Profile.** `perfproof profile <name> --json`. Read the top
   hotspots (`function`, `file`, `line`, `total_fraction`) and the wall
   versus CPU time. If CPU is well below wall, the program is mostly
   waiting: look at I/O and services, not loops.
3. **Ideas.** For each hotspot, work out the most a change there could
   save (the cap in [discovery.md](discovery.md)). Drop any idea whose
   cap is at or below `1 + min_effect` from the spec. Check the angles in discovery.md for ones
   the profile hints at. Check `git log` for what was tried before.
4. **Cheap test first.** Before writing a real change, test the idea
   cheaply (the cheapest check that would prove it wrong). Drop ideas
   it refutes and say why.
5. **Try.** Ask once before creating the branch `perfproof/<name>`;
   then write the change there, as small as the idea needs, and prove
   it exactly as above.
6. **Keep or undo.** A KEEP stays on `perfproof/<name>`. Undo
   everything else, touching only files the attempt changed. While
   perfproof can't give verdicts yet, offer to leave a change whose
   checks all passed on its branch for the user to judge, saying the
   checks passed and the speed is not decided. Never argue from the
   estimate.
7. **Report** once at the end ([messages.md](messages.md)): each idea,
   its evidence, what happened, what was kept, what was not checked,
   and what to try next or "nothing left could clear the bar".

Keep notes for the next session in `.perfproof/session.md`: each idea,
its cap, its cheap test and its outcome, one line each.

## Spend the user's budget well

- Fewer turns: take the next step in the same turn when nothing needs
  an answer.
- Read one function, not the whole file; never re-read a file you have.
- Never read raw logs, profiler files, `record.json` or `samples.npz`;
  the JSON fields above are enough. Give the path if the user wants
  more.
- Use a semantic code search tool if one is installed, `grep` if not.
- Write a helper script once, show it, reuse it.
