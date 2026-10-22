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
  as a question with at most two options, your recommendation first,
  for example "Shall I <recommended>, or <alternative>?"
- Three kinds of statement, never mixed up:
  - **Facts**: tests passed or failed, outputs identical or different,
    a count ("236 queries became 10"). Say them plainly.
  - **Estimates**: any timing that is not a calibrated KEEP or REJECT.
    Give the number and its range and say "estimate". Never call an
    estimate faster, slower, quicker, shorter, longer, a speedup, a gain,
    better, worse, positive or negative, never use it as a reason to keep a change, and
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
- Before anything is decided, name a goal by its metric ("the time
  the `rolling_sum` calls take") and the bar as a line ("the low end of
  the range must be above 3%"), never as "faster" or "beats".
- A cap from a profile ("the most this function could save") is a
  bound, never a promise: say "can save at most 10x", never "10x
  faster". See [discovery.md](discovery.md).
- A question you will go on from once it is answered (which workload,
  the spec, a branch, a busy machine) gets no `Result:` line. A stop
  that ends the session (not a git repository, uncommitted changes,
  nothing to run) and every finished run end with one last line, the
  only place the word "Result" appears as a label: never use it as a
  heading or bold label in the body ("**Result:**"); head the timing
  paragraph "**The timing:**" as in messages.md. When `compare` returned a
  verdict, copy it: `Result: <verdict.outcome> (<verdict.reason>)`,
  adding `, gate <verdict.gate>` when there is one, for example
  `Result: INCONCLUSIVE (insufficient_evidence)` or
  `Result: REJECT (correctness, gate correctness)`. A REJECT from a
  gate (correctness, lock, side_effects, runtime, procedure, same_host)
  is a verdict: copy it. Use `Result: NO VERDICT (<plain
  reason>)` only when `compare` returned no verdict at all (you stopped
  before it, or it answered `ok: false`). An uncalibrated INCONCLUSIVE
  is "not yet a verdict" to the user, but `compare` did return one:
  its line is `Result: INCONCLUSIVE (<verdict.reason>)`, never NO
  VERDICT.

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
   `low_power_mode`, `thermal_limit`, `non_performance_governor`): this
   is how real machines are, not a reason to stop. Say what it means in
   one sentence, carry on, and put the conditions in the report. The
   old and new versions run in alternating rounds, so load that hits
   both mostly cancels; it only widens the range. Offer to wait only if
   the user would rather. When finding speedups, mention it in the one
   question of step 1.
5. The project has no tests or other check of the result: say a change
   can't be kept without one, and offer to write a check (the output on
   a fixed input) with the user's agreement. If they decline, still
   measure; perfproof reports the estimate and says it can't keep.

Never set `allow_runtime_change`, `allow_cross_host` or any other
opt-in in a spec without asking; each one turns a check off.

You write only to `.perfproof/` and, once the checks pass and the user
agrees, to one branch `perfproof/<name>` for kept changes. Never commit
to, rewrite or edit a branch the user named or owns; a fix you write for
their change goes on a new `perfproof/<name>` branch. Before you
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
   much, at what. Quote each claim word for word in quotation marks
   rather than restating it. You will check each claim or say it was
   not checked.
3. Reuse a locked benchmark if one runs the changed code: each
   directory under `.perfproof/benchmarks/` is one, and
   `perfproof bench list <name> --json` shows its versions. Otherwise
   write one: a command that runs the changed code the way it is really
   used, plus the test command and the output files to compare
   ([spec.md](spec.md)), and lock it with
   `perfproof bench define <name> --spec <file> --json`. A script you
   write for it goes in `.perfproof/harness/`, never in the user's
   tree. If you can't find any command that runs the changed code,
   stop and say so (nothing can be measured without one).
   Never refuse a change on your own reading of the diff, even when it
   plainly edits the benchmark or its tests: run `compare`, which
   refuses such a change in seconds at its lock check and records why.
   A refusal must come from perfproof, never from you.
4. Run `perfproof compare <name> --candidate <rev or dir> --baseline
   <rev> --json`, adding `--spec-hash <hash>` when the benchmark has more
   than one version (error `ambiguous_hash`). It can take minutes;
   say so before you start.
5. Report from the JSON with [messages.md](messages.md): what was
   checked and passed (only gates in `gates[]` with their `status`),
   what failed, the estimate (`verdict.estimate`, range
   `verdict.interval.lower` to `verdict.interval.upper`; never
   `diagnostics.wall_interval`) or verdict, what was not checked, and
   where the run record is (`report_path`). Never say a check ran
   unless `gates[]` shows it. "Not checked" comes from the JSON's
   `not_verified` list: say each item in plain words and, where one
   exists, what would make the change keepable ("run the program
   directly instead of through a shell script"). Offer only a step that
   would actually change the item: coverage isn't checked by perfproof
   yet, so say "perfproof can't check which changed lines ran yet" and
   don't offer a test for it. If `measured_metric` is set, say what was
   actually measured ("instructions weren't available here, so wall
   time was measured"). An unverified item is not a failure: the
   change was measured, it just can't be kept until that is checked.

## Find speedups

1. **Understand, then ask once.** Run `perfproof init --json` (once
   per repository; it writes only `.perfproof/`). It proposes
   `targets[]`, `workloads[]` and goals, each with an `id` and its
   evidence, and `questions[]` for what it could not find out. Prepare
   everything before you ask: your suggested answer to each question
   that matters (what "faster" means, what must not change), the spec
   built on them ([spec.md](spec.md)) or a locked benchmark that fits
   (one directory per benchmark under `.perfproof/benchmarks/`), and
   the branch `perfproof/<name>` where attempts will go. Then ask in
   one message, your suggestion first for each: the workload and
   answers, the spec in one paragraph, permission for the branch, and
   any machine warning from the checks above. On their yes, confirm in
   this order, since each needs the one before:
   `perfproof project confirm <target id> <workload id> --json`, then
   `perfproof project goal <workload id> --metric <m> --confirm --json`
   (`<m>` is `wall`, `cpu`, `p99`, `memory`, or the name of a metric
   your benchmark reports, with `--direction` and `--unit`), then lock
   the spec. Nothing is measured on a workload the user has not
   confirmed. After that, don't stop again before the report unless
   something comes up that only the user can decide.
2. **Profile.** `perfproof profile <name> --json`. Print and read the
   top entries of `result.hotspots[]` (`function`, `file`, `line`,
   `total_fraction`), not a cut of the whole output, and the wall
   versus CPU time. If CPU is well below wall, the program is mostly
   waiting: look at I/O and services, not loops. For a native
   program, give `profile` a profiler that writes folded stacks (samply
   or perf) with `--profiler-command` and `--own-module <binary>`. If
   none is installed, sample the program with the system sampler
   instead (`sample` on macOS), call each function's share of the
   samples an estimate, name the sampler, and say perfproof's profile
   did not run.
   For Java and other JVM code, read [java.md](java.md) first: how to
   launch the benchmark so the runtime is identified, how to warm it
   up, how to profile it, and what usually goes wrong.
3. **Ideas.** For each hotspot, work out the most a change there could
   save (the cap in [discovery.md](discovery.md)). For a Python
   function, measure the cap rather than estimate it:
   `perfproof measure <name> --stub <module>:<function> --json` gives
   `result.stub.cap_bound`, or the reason no cap can be given in
   `result.stub.cap_withheld`; then use the profile's share instead
   and say the cap comes from the profile. For
   growth with input size, `--sizes 1,2,4,8 --size-arg=--n=<base>`; for
   one setting, `--set NAME=VALUE`. These are bounds and estimates, not
   verdicts. Drop any idea whose cap is at or below `1 + min_effect`
   from the spec. Check the angles in discovery.md for ones the profile
   hints at. Check `git log` for what was tried before.
4. **Cheap test first.** Before writing a real change, test the idea
   cheaply (the cheapest check that would prove it wrong). Drop ideas
   it refutes and say why as a mechanism you read in the code or a
   count you made ("the lookup rebuilds a dict on every call"), never
   as a timing or a word
   like "slower" or "no quicker". A cheap timing only decides what to
   try next: never report it to the user. Report counts or a bound
   instead ("the stage is 18% of the run, so it can save at most
   1.2x").
5. **Try.** Create the branch the user allowed in step 1 (ask now
   only if they did not) from the commit the user was on, write the
   change there, as small as the idea needs, commit it on that branch,
   and prove it exactly as above with `--baseline <that commit>
   --candidate perfproof/<name>`. Every attempt uses that same
   baseline.
6. **Keep or undo.** First switch back to the branch the user was on,
   so their working tree is exactly as it was. A KEEP stays on
   `perfproof/<name>`. An attempt whose checks failed is undone by
   deleting its branch, which only you created; never touch anything
   else. While perfproof can't give verdicts yet, an attempt whose
   checks all passed stays on its branch for the user to judge: say the
   checks passed and the speed is not decided, and offer to delete it.
   Never argue from the estimate.
7. **Report** once at the end ([messages.md](messages.md)): each idea,
   its evidence, what happened, what was kept, what was not checked,
   and what to try next. Say "nothing left could clear the bar" only
   when no idea with a cap above the bar is left untried; otherwise
   say what this depth tried, which ideas remain with their caps, and
   offer the next depth (at `full`, say what you would try next).

Keep notes for the next session in `.perfproof/session.md`: each idea,
its cap, its cheap test and its outcome, one line each. Read it before
trying an idea: an idea refuted before on unchanged code is not retried
without saying what differs.

## Spend the user's budget well

- Fewer turns: take the next step in the same turn when nothing needs
  an answer.
- Read one function, not the whole file; never re-read a file you have.
- Never read raw logs, profiler files, `record.json` or `samples.npz`;
  the JSON fields above are enough. Give the path if the user wants
  more.
- Use a semantic code search tool if one is installed, `grep` if not.
- Write a helper script once, show it, reuse it.
