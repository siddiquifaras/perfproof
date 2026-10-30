---
name: perfproof
description: Use when the user wants code or a function to run faster or to be sped up, says something takes forever or is slow, asks what is inefficient or wrong performance-wise in a project, or wants to know whether a change (theirs or an agent's) really is faster and still correct, but not for load testing a deployed service, memory leaks with no speed goal, or code that cannot run on this machine.
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
    a win, better, worse, positive or negative, never use it as a
    reason to keep a change, and never compare it with a claim
    ("larger than claimed", "matches the 50x claim"). Do not use
    these words even to say you are not using them or to describe an
    idea's room to improve: say "the cap is 1.04x" or "it is 3% of
    the run". Say the claim can't be checked until perfproof can
    give a verdict. The rule covers every sentence of a paragraph that
    holds a number, ratio, estimate, range or interval, whatever the
    sentence is about. The words to keep out of such a paragraph:
    faster, slower, quicker, shorter, longer, speedup, gain, win,
    better, worse, regression, improve, improvement, slows, beats,
    outperforms, efficient, efficiency, less time, cuts the time,
    reduces the time, sped up, "times as fast", "dropped from",
    "fraction of the time", "order of magnitude", "points to",
    "suggests". These all break it: "I'm not claiming a gain", "I can't
    confirm it is faster than main", "a faster subset of the tests",
    "it would look better than it is", "less time per call", "kept or
    faster". Write the sentence without the word: "perfproof can't give
    a verdict on <change> yet", "a smaller subset of the tests", "the
    ratio is old time divided by new time". Put the estimate in a
    paragraph of its own ("The estimate is X, range A to B.") and the
    rest of your prose in other paragraphs. To refer to a claim's own
    words, put them in quotation marks.
  - **Verdicts**: only from `compare`, only when
    `verdict.calibration == "calibrated"`. Only then say "kept" or
    "faster".
- When `verdict.calibration` is not `calibrated`, KEEP is withheld, and
  `not_verified` or `warnings` names why (`target_class_unknown`,
  `schedule_not_class_own`, `schedule_uncalibrated`,
  `metric_not_calibrated`). Say it plainly:
  "this setup isn't one perfproof has been checked on, so it can't
  confirm a keep", then the estimate (X, range A to B) as "not a
  verdict". For `gpu_kernel`: "measured, but perfproof can't yet confirm
  a keep for GPU kernels". If the spec lacks the class or its schedule,
  fix the spec and rerun; otherwise a rerun cannot change it.
- Every stop short of the user's goal (INCONCLUSIVE, an estimate whose
  range does not clear the bar, a speedup short of the target, a cap
  missed) ends with four things, never a dead end: where the user
  stands (the estimate and its range, or the closest value perfproof
  reliably reached: a fact about the run, not a verdict), the gap
  between that and the goal, one concrete next step (the next idea and
  its cap, a larger workload, a different angle), and the question
  "keep going?" as the one question, recommendation first (for example
  "Keep going with <idea>, or stop here?"). Numbers here are
  information, never a KEEP; the wording rules above still apply: any
  estimate goes in its own paragraph, the rest in plain sentences.
  An absolute limit missed or too close to call (`gap` positive for a cap, negative for a floor) is
  such a stop: from `guardrails[].standing` in the record say where the
  run stands (`reliable_value`, limit, gap, "in these runs"), name what
  the profile blames, propose one change, ask "Keep going on getting
  under <cap>?" ("over <floor>" for a floor). On yes the gap is the
  goal: profile it, change, `compare`, repeat. A relative allowance
  breach is "not kept", no loop. `reliable_value` is no verdict.
- Final answer order: what was found and changed, the estimate and range
  in its own paragraph, why it is right (tests, identical output), the
  verdict in one plain line plus the one step that clears a withheld
  keep, the ranked map, `Result:`. A REJECT comes first ([messages.md](messages.md)).
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
  before it, or it answered `ok: false`). An INCONCLUSIVE with KEEP withheld
  is "not yet a verdict" to the user, but `compare` did return one:
  its line is `Result: INCONCLUSIVE (<verdict.reason>)`, never NO
  VERDICT. The line reports the last `compare` verdict even when you
  did something after it (deleted a branch, answered a question): never
  put prose in it.

## What the user asked for

Look at what the user wrote when they asked for perfproof:

1. `light`, `medium` or `deep` as the first word sets the depth (below).
2. Nothing else, or a whole-app ask ("speed up the app", "fix
   everything"): find speedups in the project (**find**), at `medium`,
   without asking what to speed up. "Fix everything" means rank, then
   work from the top, and report one ranked map ([messages.md](messages.md)).
3. A branch, commit, PR or diff named as the whole request, a URL, or
   marked ("pr 123", "branch x", "this diff", "my changes"): **prove** it.
4. A file or function: find speedups starting there.
5. Anything else is a description ("the CSV import is slow"): find
   speedups for that. If it names an existing branch or PR, ask once
   whether to prove it instead.

A long or rambling prompt: pull out the slow area, the goal and any
limits, and drop the rest. Claims in it ("it's the database layer",
"everything is slow") are leads to check against the code and the
profile, never facts; if the profile contradicts one, say so plainly in
one line. Ask at most one short question, only when the repo cannot answer it, and never
ask the user to write a benchmark.

Depth decides how much you explore and try. It never changes how a
change is measured or what counts as kept. Every tier ends with a
ranked list of where the time goes, with ceilings, and a result.

| Depth | Find (`perfproof find` asks for) | Prove |
|---|---|---|
| `light` | 1 workload; try the top real candidate | default for proving |
| `medium` (bare `/perfproof`) | tests and existing benchmarks profiled; 3 workloads from different entry points or kinds, one with a size knob; try the top 2-3 candidates and prove the best | the same, plus the claim check |
| `deep` | one workload per entry point, size knobs everywhere, the native recorder on native parts; every candidate with a meaningful ceiling; a second, fresh `perfproof compare` of the best | every candidate in the branch, one at a time |

Say in one sentence what the depth will cover and that `light` is
shorter; don't wait for an answer unless they asked. Remember the depth
they choose in `.perfproof/preferences.json`.

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
   question of step 2.
5. The project has no tests or other check of the result: say a change
   can't be kept without one, and offer to write a check (the output on
   a fixed input) with the user's agreement. If they decline, still
   measure; perfproof reports the estimate and says it can't keep.

Never set `allow_runtime_change`, `allow_cross_host` or any other
opt-in in a spec without asking; each one turns a check off.

Before the first `compare`, in both modes, the project model needs a
confirmed target (`perfproof init --json` if it is missing). If one
target fits and the user hasn't confirmed it, ask one plain question
("perfproof sees this as <class> code, from <evidence>; is that
right?"): fold it into the question you are already asking, or ask it
on its own if there is none. If several fit (one app of
several parts), the same question names each with its evidence ("Python
over a C++ extension, from ...; right?"). On yes run `perfproof project
confirm <target id> --json` on each; a spec takes its part's class. If none fits, leave
`target_class` out and say once that KEEP can't be calibrated. The spec sets that `target_class`
and its schedule ([spec.md](spec.md)); adding them to an existing spec
changes its hash, so define a new version and say so in one line.
Without a confirmed class, `compare` measures but cannot keep.

When proving a change to non-Python code of a calibrated class, take its
playbook's coverage command and probe the tool (`--version`). If missing,
add its install to the question you are already asking, before the first
`compare`: "to check which changed lines your tests run it needs
`cargo-llvm-cov`, a dev tool; ok?". Yes: install, declare
`correctness.coverage_command`. No: say KEEP can't be checked. (Find mode
gets this from `find`'s question.)

You write only to `.perfproof/`, a coverage tool the user agreed to, and,
once the checks pass and the user agrees, to one branch `perfproof/<name>` for kept changes. Never commit
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
   `perfproof bench define <name> --spec <file> --json` (read `ok` in
   the full output; never filter it). A script you
   write for it goes in `.perfproof/harness/`, never in the user's
   tree. If you can't find any command that runs the changed code,
   stop and say so (nothing can be measured without one).
   If the change edits code AND adds or edits a benchmark, never lock a
   file the change touches: copy the benchmark into
   `.perfproof/harness/` (the baseline's version if it has one, else the
   change's new file) so both versions run the identical benchmark, and
   lock that copy. If the change touches only benchmark or test files,
   build the spec on them as usual and let `compare` judge it.
   Once `bench define` has locked a file, never edit it: if the
   benchmark needs fixing, write a corrected copy under a new name,
   define that, and compare with it.
   Never refuse a change on your own reading of the diff, even when it
   plainly edits the benchmark or its tests: run `compare`, which
   refuses such a change in seconds at its lock check and records why.
   A refusal must come from perfproof, never from you.
4. Run `perfproof compare <name> --candidate <rev or dir> --baseline
   <rev> --json`, adding `--spec-hash <hash>` when the benchmark has more
   than one version (error `ambiguous_hash`). It can take minutes;
   say so before you start. Never end your turn while it runs: run it
   in the foreground; if your tool's time limit is shorter, add
   `--detach` and follow it with `perfproof status <id> --json` (never
   by looking its process up by name), and wait for the verdict. If
   `--detach` fails with `spawn_failed`, run in the foreground. A finished `status` gives only a summary: read the record at
   `verdict.record_path` for the full `verdict` (with `diagnostics` and
   `calibration`), `gates` and `measured_metric`, and give the
   `report.md` beside it as the run record.
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
   would actually change the item. When the `coverage` reason names
   lines ("... lines 12-14 never ran"), say those changed lines never
   ran under any check and offer a test that reaches them and fails on
   the original (a new spec, the user's call; messages.md R-5). When it
   says no coverage command is declared or no report was written, say "perfproof can't see which
   {language} lines ran" and don't offer a test for it. If `measured_metric` is set, say what was
   actually measured ("instructions weren't available here, so wall
   time was measured"). An unverified item is not a failure: the
   change was measured, it just can't be kept until that is checked.

## Find speedups

1. **Run `find`.** `perfproof find --depth <depth> --json` (bare
   `/perfproof` is `medium`). It does the mechanical discovery: the
   project's parts, each part's tests and existing benchmarks profiled,
   the native recorder where it applies, and your workloads profiled and
   ranked. Read `done`, `question`, `next_steps`, `map`, `leads`,
   `not_reached` and `skipped`. Writing and profiling workloads needs no
   permission, so do step 2 first and ask in step 3.
2. **Do the `next_steps` in order** (not `install_or_confirm`), calling
   `find` again after each.
   - `define_workloads`: write the workloads it asks for through the
     entry points it names, on realistic input, run the way users run
     the program (output to a terminal can take other code paths than a
     pipe). One kind of operation per workload, never a combined script;
     one modifies and re-emits data where the project does; where users
     make many small calls, keep inputs small. Tests and benchmarks that
     call internals are sources of inputs, never the workload. A user's
     claim decides only what the first workload looks at. A profile
     covers start-up too, so loop long enough that it is a minor share,
     and tell the user what an enlarged workload now does, never how
     long a run took. Write scratch and profile output under
     `.perfproof/`, not `/tmp`. Then `perfproof bench define <name>
     --spec <file> --json` ([spec.md](spec.md)) and `perfproof project
     add-workload <name> --target <target_id> --json`, so the user can
     confirm it; read Node and TypeScript launch advice in
     [node.md](node.md) first.
   - `add_size_knob`: `perfproof find --size-arg WORKLOAD:FLAG=BASE` (or
     `--size-env WORKLOAD:NAME=BASE`) for the one workload whose single
     item can grow: a long document, a large image.
   - `reach_lead`: write a workload through the entry point a user would
     use that reaches the lead, shaped by the inputs of the test its
     `via_test` names. If nothing a user does reaches it, say so in plain
     words and go on.
   - `continue_find`: call `find` again.
   - A step that truly can't be met (one kind of operation exists, the
     program needs a remote service): `perfproof find --waive
     CODE=REASON` with the honest reason.
3. **The one question.** Once `find` has no step left but
   `install_or_confirm`, ask one question that joins `question` (class,
   test command, installs) with the workloads you will measure, by name,
   one line each, and what "faster" means with what must not change,
   permission for the branch `perfproof/<name>` and any machine warning
   from the checks. When the test command is unknown, offer the one you
   would run (`find --test-command <part>='["cmd","arg"]'`). On yes, record
   it all: `perfproof project confirm <target_id> <workload ids> --json` (those
   `init` listed and those you added),
   `perfproof find --confirm-tests <part>`, each install command, then
   `perfproof project goal <workload id> --metric <m> --confirm --json`
   for each (`<m>` is `wall`, `cpu`, `p99`, `memory` or your benchmark's
   metric, with `--direction` and `--unit`). If a command fails, say so
   plainly. A workload the user declines stays unconfirmed, so `compare`
   can't keep a change on it: say that plainly. On no, record nothing and say what
   that limits: an unconfirmed class or missing coverage tool means KEEP
   can't be given; an unconfirmed test command means the tests are not
   profiled, so their leads are missing. After the answer, don't stop
   again before the report unless only the user can decide something.
4. Repeat steps 1 to 3 until `done`. Language playbooks say how to launch each
   runtime: [java.md](java.md), [rust.md](rust.md), [go.md](go.md),
   [ruby.md](ruby.md), [csharp.md](csharp.md), [native.md](native.md);
   several languages in one stack: [discovery.md](discovery.md).
5. **Read the map.** Print the top 15 rows of `map` in the order given,
   one per line as `function file line cap total_fraction`. Print the top
   15 of `leads` the same way. Skip `wrapper` rows, and name any row at 5% or more that you skip, with the reason. Leads are
   places to look, never candidates or claims. A `layer` of `native`
   under a Python row, or `skipped` entries, say what one profile could
   not see: say so. For a function the map lacks that you suspect from
   the code: `perfproof profile show <name> --function <function>
   --json`. If the map is flat or only start-up and the driving loop,
   add a workload through another entry point and call `find` again.
   `not_reached` is "not covered by these workloads", never "fine". If
   CPU is well below wall, look at I/O and services, not loops. Before
   reading code, tell the user the top candidates by name and file, with
   the share as a fact ("<share> of the samples are under `<function>`
   in `<file>`"), which you will try first and why: its cap at the
   largest size. If the claim's workload shows no project function
   above the bar, say the profile doesn't support it. A project function
   high in total share but low in self share is already a map row, which
   carries `total_fraction`.

6. **Ideas.** For each ranked candidate, work out the most a change
   there could save (the cap in [discovery.md](discovery.md)). For a Python
   function, measure the cap rather than estimate it:
   `perfproof measure <name> --stub <module>:<function> --json` gives
   `result.stub.cap_bound`, or the reason no cap can be given in
   `result.stub.cap_withheld`; then use the profile's share instead
   and say the cap comes from the profile. For
   one setting, `--set NAME=VALUE`. These are bounds and estimates, not
   verdicts. Drop any idea whose cap is at or below `1 + min_effect`
   from the spec. Check the angles in discovery.md for ones the profile
   hints at. Check `git log` for what was tried before.
7. **Cheap test first.** Before writing a real change, test the idea
   cheaply (the cheapest check that would prove it wrong). Drop ideas
   it refutes and say why as a mechanism you read in the code or a
   count you made ("the lookup rebuilds a dict on every call"), never
   as a timing or a word like "slower" or "no quicker". A timing you
   took yourself (`time`, a loop, a stopwatch, even "about 23 ms") only
   decides what to try next: never report it to the user. Report counts or a bound
   instead ("the stage is 18% of the run, so it can save at most
   1.2x").
8. **Try.** Create the branch the user allowed in step 2 (ask now
   only if they did not) from the commit the user was on, write the
   change there, as small as the idea needs, commit it on that branch,
   and prove it exactly as above with `--baseline <that commit>
   --candidate perfproof/<name>`. Every attempt uses that same
   baseline. Before calling a build unbuildable, read 30 log lines and
   try the obvious fix once ([native.md](native.md)).
9. **Keep or undo.** First switch back to the branch the user was on,
   so their working tree is exactly as it was. A KEEP stays on
   `perfproof/<name>`. An attempt whose checks failed is undone by
   deleting its branch, which only you created; never touch anything
   else. While perfproof can't give verdicts yet, an attempt whose
   checks all passed stays on its branch for the user to judge: say the
   checks passed and the speed is not decided, and offer to delete it.
   Never argue from the estimate.
10. **Report** once at the end ([messages.md](messages.md)): each idea,
   its evidence, what happened, what was kept, what was not checked,
   and what to try next. Say "nothing left could clear the bar" only
   when no idea with a cap above the bar is left untried; otherwise
   say what this depth tried, which ideas remain with their caps, and
   offer the next depth (at `deep`, say what you would try next).

Keep notes for the next session in `.perfproof/session.md`: each idea,
its cap, its cheap test and its outcome, one line each. Read it before
trying an idea: an idea refuted before on unchanged code is not retried
without saying what differs.

## Spend the user's budget well

- Fewer turns: take the next step in the same turn when nothing needs
  an answer.
- Never end a turn while a command you started is still running (a
  build, a profile, `compare`, a test run). If your tool moves it to
  the background, check its output file in a loop every few seconds
  until it finishes: "I'll report when it finishes" ends the session
  with no report. A build that prints nothing for
  minutes is hung: stop it and rerun it in one process with the
  language file's flags. If you do have to stop, say what is unfinished
  and end with the `Result:` line.
- Read one function, not the whole file; never re-read a file you have.
- Never read raw logs, profiler files, `record.json` or `samples.npz`;
  the JSON fields above are enough. Give the path if the user wants
  more.
- Use a semantic code search tool if one is installed, `grep` if not.
- Write a helper script once, show it, reuse it.
