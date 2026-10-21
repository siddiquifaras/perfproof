# Method

This page defines how perfproof decides whether a change is faster. It
is the reference the code is built and tested against, and its
acceptance rules are fixed before any calibration runs. Where a part is
not implemented yet, `.claude/plan.md` tracks it.

## The loop

1. **Define the benchmark.** A command, its working directory and
   environment, the metric, a correctness check, the input data, and
   the files that make up the benchmark, its harness and its tests.
   perfproof hashes the files and the spec itself.
2. **Snapshot the baseline.** The baseline is a git commit. perfproof
   checks it out into its own worktree so the baseline and the
   candidate exist side by side and can be run in turns.
3. **Profile the baseline** and hand the agent the top hotspots with
   `file:line`, not a raw profiler dump.
4. **The agent changes the code.**
5. **Compare.** Gates first, then interleaved rounds until the
   stopping rule ends it.
6. **Report.** One JSON record and one readable report per comparison.

## Outcomes and reasons

Every verdict has an outcome and a reason. They are separate fields in
the record, so a tool or an agent never has to parse prose to learn why.

| Situation | Outcome | Reason |
|---|---|---|
| Interval shows the speedup clears the bar | KEEP | `performance` |
| Interval rules out a speedup above the bar | REJECT | `performance` |
| Faster, but a guardrail is shown worse than its allowance or an absolute limit | REJECT | `guardrail` |
| Faster, but a guardrail's compliance can't be established | INCONCLUSIVE | `insufficient_evidence`, naming the guardrail |
| Candidate gives wrong results | REJECT | `correctness`, gate `correctness` |
| Changed code that no check ran | INCONCLUSIVE | `insufficient_evidence`, gate `coverage` `not_available`, naming the lines |
| The experiment itself is not valid (locked files, procedure, both arms' runtimes identified and different, a candidate that can't be started where the baseline runs, a host identity that changed) | REJECT | `integrity`, with the gate named |
| Something could not be verified (an unidentified runtime, an unreadable host identity, no correctness check declared, a guardrail not declared) | INCONCLUSIVE | `insufficient_evidence`, naming what was not verified; the comparison is still measured and recorded |
| The benchmark would reach a service that isn't local | Refused before anything runs | `integrity`, gate `side_effects` |
| Interval still straddles the bar when the budget runs out | INCONCLUSIVE | `insufficient_evidence` |
| A value no larger than its resolution, or an undeclared `reported` resolution that can't be trusted | INCONCLUSIVE | `insufficient_evidence`, diagnostic `coarse_resolution` |
| The interval would decide, but a diagnostic says its coverage is not shown at this look's length (autocorrelated rounds, outliers) and the budget is out | INCONCLUSIVE | `insufficient_evidence`, diagnostic `autocorrelated_rounds` or `outlier_rounds` |

**Unknown is not wrong.** perfproof exists to support the agent on any
codebase, so it never stops or rejects for something it merely cannot
verify. It measures, records what it could not verify, and withholds
only a KEEP: the verdict is INCONCLUSIVE and names the reason, so the
agent can tell the developer what would make the change keepable. A
REJECT, or a stop with no verdict, stays for two things only:

- *evidence of a real problem*: the arms ran different identified
  runtimes, the output changed, a benchmark or test file was edited, a
  slowdown was measured, a side effect was seen;
- *an unsafe run*: one that would reach a remote or real service,
  destroy data, or need credentials, or whose service address cannot be
  parsed.

A correctness or integrity REJECT says nothing about speed. Gates run
before any timing, so those verdicts carry no speedup estimate at all.
A performance REJECT says nothing about correctness beyond the gate
having passed. A guardrail REJECT keeps the speedup estimate, because
the point is to show what the speed cost.

## What perfproof promises

perfproof cannot prove a change correct. It promises something narrower
that it can keep: **it never keeps a change that failed a check, and
every report says what was checked and what wasn't.** How much that is
worth depends on the checks, so they are built to be strong:

- the project's own tests, run on the baseline first to find flaky ones;
- differential checks: old and new code on the same inputs, including
  real inputs captured while the benchmark ran;
- a coverage gate: every changed line must have run under those checks;
- guardrails: the speed must not be bought with memory or tail latency;
- a side-effect guard: nothing runs against a service that isn't local.

## Correctness

The `correctness` and `coverage` gates decide whether the candidate
still does what the baseline did.

**Failed executions.** An execution of the benchmark, warmup or
measured, that exits non-zero, is killed by a signal, times out or
can't be started from its tree (a bad interpreter line, a missing or
non-executable program) produces no sample; its time never enters the
statistics. If the
candidate fails this way, the candidate fails the `correctness` gate:
REJECT with reason `correctness`, the exit status and the tail of its
output in the record. A candidate that can't be started from its tree
at all (its program or interpreter is missing there, or a bad
interpreter line) while the baseline runs the same command means the
two arms do not run the same program: the `runtime` gate fails, REJECT
with reason `integrity`, unless the spec allows runtime changes, when
it is the `correctness` gate as above. A candidate that started and then
exited non-zero is always the `correctness` gate's. If the baseline fails,
or its program or interpreter can't be found, the experiment itself is
broken: the comparison stops with no verdict and reports the error.
So does a fault in perfproof's own process, such as running out of
file descriptors or processes: it is blamed on neither version.

A `setup` command (see "Integrity") that fails follows the same rules.
In the baseline the experiment is broken. When the candidate's setup
fails, perfproof first runs it once more in the same tree, because an
idempotent setup on a half-prepared tree can pass where a transient
fault failed it. If the rerun passes, the run goes on and records a
`setup_retried` warning that points at the first failure's logs. If the
rerun fails too, the baseline's setup is run once more to rule out a
shared fault: if that fails, the comparison stops as a broken baseline;
if it passes, the candidate fails the `correctness` gate, with both
failures' reasons and the second attempt's status and output tail in
the record.

**No check declared.** A spec may declare no test command and no
output to compare. The `correctness` gate is then `not_available`,
with the detail "the spec declares no correctness check", and a KEEP
is withheld. The candidate's executions are still run and a failed one
fails the gate as above, so a crash is still a REJECT. A gate that
checks nothing never passes.

**Test results.** perfproof judges tests one by one where the runner
reports them: it asks pytest for a JUnit XML report and points other
runners at the same path through `PERFPROOF_JUNIT_XML`. Where no
report is written, the command's exit status is the only result.

**Pre-existing failures.** A test that fails in every baseline run is
a known failure: it is listed in the report and not used to judge the
candidate, and lines only it reaches count as unverified for the
coverage gate. Only a test command that can't run at all on the
baseline (it times out, is killed, or can't start) stops the
comparison as a broken baseline.

**Flaky tests.** Before judging the candidate, perfproof runs the
correctness command on the baseline three times. A test that doesn't
give the same result every time is flaky: it is quarantined, listed in
the report, and not used to judge the candidate. Three runs catch a
test that fails half the time only 75% of the time, so a test that
fails on the candidate after passing every baseline run is run five
more times on each tree before the candidate is blamed. If it fails on
the baseline in any of those runs, it is flaky and quarantined; if it
fails only on the candidate, the candidate fails the gate. The five
reruns are shared by every suspect, so the gate always ends: a test
that first fails on the candidate during them is judged on every
baseline run so far. Quarantine never
weakens the coverage gate, because lines only the quarantined tests
ran count as unverified.

**Differential checks.** Tests check what their authors thought of.
Differential checks compare the two versions directly:

- Declared outputs of the benchmark run are compared between the trees,
  as bytes, text, or numbers within `rtol` and `atol`.
- While the baseline benchmark runs, perfproof captures up to a fixed
  number of real calls to each function the candidate changed: the
  arguments, and the return value. Each captured call is replayed
  against the candidate in a separate process, and the results must
  match. Calls whose arguments can't be captured faithfully (open
  files, sockets, live objects) are skipped and counted in the report.
- Where type hints allow, generated inputs (Hypothesis) are run through
  both versions as well.
- For model outputs: fixed-seed greedy decoding must give the same
  tokens, or logprobs must agree within a declared tolerance.

Any mismatch fails the `correctness` gate, with the first differences
in the record.

**Coverage gate.** perfproof takes the lines the candidate changed from
`git diff` and runs the correctness command and the differential checks
under coverage. Every changed executable line must have run. A change
in code no check executed is unverified, and an unverified change is
not kept. It is not rejected either: the gate is `not_available`, with
the unverified lines in its record, and the verdict is INCONCLUSIVE
(`insufficient_evidence`), because the change may well be right and
perfproof only cannot show it. For a target where perfproof cannot measure coverage yet, a
KEEP needs the spec's explicit opt-in, and the verdict is labelled
"coverage not checked".

## Side effects

A benchmark that writes to a real database or calls a paid API is not
safe to run blindly.

**The benchmark's environment.** Every command perfproof runs for a
comparison (benchmark, correctness check, reset, setup) gets a small base
environment, not perfproof's own: `PATH`, `HOME`, `USER`, `LOGNAME`,
`SHELL`, `TMPDIR`, `TERM`, `TZ`, `LANG` and the `LC_*` variables, plus
the spec's own `env`. Anything else the benchmark needs, such as
`CUDA_VISIBLE_DEVICES` or a database URL, is declared in the spec. So
the agent's own credentials and service addresses (an API base URL, a
cloud token) never reach the benchmark's environment, and both
versions run with
exactly the declared environment.

**The check.** Before anything executes, perfproof checks that
environment, the commands' arguments and the declared config files for
service addresses, such as `DATABASE_URL`, `REDIS_URL`, and variables
ending in `_HOST` or `_URL`. If any points at a host that isn't local
(`localhost`, `127.0.0.1` and the rest of `127/8`, `::1`, `0.0.0.0` and
`::`, a Unix socket, or a container started for the run), the run is
refused with gate `side_effects`, unless the spec opts in. Nothing is
resolved through DNS, and anything that can't be parsed is refused.

**What the guard is for.** It catches a benchmark that would reach a
real service by mistake: a production database URL left in the
environment, a config file pointing at a shared cache, a command that
calls a paid API. It reads addresses the way the common clients read
them (libpq, SQLAlchemy, the WHATWG URL rules), and refuses anything it
can't parse cleanly. It is not a sandbox: a benchmark written to hide
an address from it (an address built at run time, or spelled to fool a
parser) is outside what it can promise, as is any attack on perfproof
itself.

A spec can declare a reset command that perfproof runs before every
execution, so a database starts each round in the same state. A reset
that fails fails the `side_effects` gate, with its output recorded. A
reset that cannot be started because of a fault of the host (not of its
program or working directory: descriptors, processes or memory
exhausted, an oversized environment) is a `harness_fault` stop with no
verdict, as for setup, and blames neither tree.
The arguments of the `setup` commands (see "Integrity") are checked like
those of the benchmark, with one exception: an `https` URL whose host is
exactly a public package registry (`pypi.org`, `files.pythonhosted.org`,
`registry.npmjs.org`, `registry.yarnpkg.com`, `crates.io`,
`index.crates.io`, `static.crates.io`, `proxy.golang.org`,
`sum.golang.org`, `repo.maven.apache.org`, `repo1.maven.org`,
`rubygems.org`, `index.rubygems.org`), with no user info, is allowed in
a `setup` argument, since installing dependencies is what setup is for.
The same URL in the benchmark, correctness or reset command is refused,
as is any other remote host in setup. The exemption is only for an
argument that is exactly the URL, or `--name=URL` whose value is exactly
the URL: any space, tab, newline, NUL or other control character in the
argument, before or after the URL, keeps it under the ordinary check,
because clients disagree about where such a URL ends. A registry host written any other
way (a mirror, an IP address, `http`) is not on the list. A value that
cannot be parsed stays refused everywhere: an address perfproof cannot
read is an unsafe run, not an unknown one.

## Integrity

The comparison is only meaningful if both versions went through the
same experiment. Every property of the run falls in one of three
classes.

**Must match, or REJECT with reason `integrity`** (a difference that
is seen; what can't be read is `not_available`, as set out after the
table):

| Gate | What is compared |
|---|---|
| `lock` | Hashes of the benchmark harness, the correctness oracle (tests and expected outputs), declared input data, and declared config files |
| `procedure` | Hash of the spec (command, working directory relative to the tree, declared environment variables, metric, warmup, budget, look schedule, inputs, setup commands) and the perfproof version; and for every round, the argv, declared environment, working directory and resolved executable that perfproof actually launched |
| `runtime` | The runtime from outside the tree that each arm actually runs: the interpreter and its version, followed through launchers such as `uv run`, `env` and shebangs; for a native executable built in the tree, the libraries it links from outside the tree, with their hashes; a binary committed in the tree that differs between arms. Node and Java are recognised (below). Anything that can't be identified is `not_available`, including tools known to start other interpreters (tox, nox, poetry and the like), a shell (the program it runs is not the one perfproof launched), and builders and toolchains such as `cargo`, `go`, `make`, `mvn`, `gradle`, `dotnet` and `ruby`. It is `failed` only when both arms are identified and differ. Waivable only when the spec allows runtime changes. An untracked executable inside the tree is treated as a build of the code under test. Not seen: libraries loaded at run time with `dlopen`, and an interpreter started by a program the gate identified as Python or native |
| `same_host` | Host identity in the machine fingerprint of every sample. With the cross-machine opt-in, the session design under "Comparisons across machines" applies instead. A host identity that can't be read is `not_available`; one that is read and differs is `failed`. |

**What can't be read is `not_available`, not `failed`.** A gate fails
only on evidence. When perfproof cannot identify what it would compare,
the gate is `not_available`, its detail says what and why, the
comparison goes on and measures, and the verdict can't be a KEEP.

- `runtime`: each arm is identified or unknown. Both identified and
  different (the interpreter, its version, its executable's hash, a
  tracked binary, the outside libraries a native build links) is
  `failed`. If either arm is unknown the gate is `not_available`
  naming the arm, the launcher and the reason, even when the other arm
  was identified, because nothing is then compared. A baseline that
  can't run is still a broken experiment and stops the comparison as
  under "Failed executions"; it is found when the baseline first runs.
  For a native build, a closure that can't be listed, an unreadable
  `git` answer and loader variables (`DYLD_*`, `LD_*`) in the
  environment are likewise `not_available`: perfproof can't follow
  them, which is not a difference between the arms.
- `same_host`: a host identity that could not be read, at the start or
  at the end, is `not_available`; two readable identities that differ
  stay `failed`. The end check comes after the last look, so a host
  identity lost only then still withholds the KEEP: the verdict becomes
  INCONCLUSIVE naming `same_host`.
- A launcher that may fetch and run code from the network when
  perfproof starts the benchmark makes the `runtime` gate
  `not_available` with the reason "this launcher may download code at
  run time", and the same launcher in the test command makes it
  `not_available` too. It is measured and not kept, never a stop:
  perfproof can't see what such a launcher fetches, which is not
  evidence of harm. The forms are `npx` and `npm exec` (including
  `npm x`) with no local command, no `--no-install` and no `--offline`
  (or with `-p`); `pnpm dlx`, `pnpx`, `pnx`, `yarn dlx`, `bunx`,
  `bun x`, `uvx`, `pipx run`, `uv tool run`; `deno run` on a URL or an
  `npm:` or `jsr:` specifier; `corepack` running a package manager
  that is not in corepack's cache (`COREPACK_HOME`, else
  `~/.cache/node/corepack`) unless `COREPACK_ENABLE_NETWORK=0`, and
  `corepack prepare`, `install`, `up`, `use` and `pack`; and the
  install forms of `npm`, `pnpm`, `yarn` and `bun` (`install`, `i`,
  `ci`, `add`, `update`, `up`, `upgrade`, `create`, and a bare `yarn`).
  Options in front of the subcommand are skipped, so `npm --silent
  install` is the install form, and `yarn --version`, `-v`, `--help`
  and `corepack enable` are not on the list. The same commands are
  fine in `setup`. Build tools that fetch dependencies on their own
  (`cargo`, `go`, `mvn`, `gradle`) are not on the list because
  perfproof cannot tell when they will: declare the fetch in `setup`
  so the measured run has nothing left to fetch. The side-effect guard
  still reads their arguments and declared config.
- Simple wrappers are peeled off before the command is identified, so
  the runtime gate sees the program that runs: `timeout` (with its
  options and duration), `nice`, `nohup`, `command`, `exec`, `time`,
  `stdbuf`, `setsid`, and `env` (including `env -S`). Only a wrapper
  that resolves, on the benchmark's `PATH` from its working directory,
  to a file outside the tree is peeled: one in the tree is code under
  test, so it is identified as the program it is (a script, so unknown)
  and never skipped. `command -v`, `-V` and `-p` are not peeled, and a
  wrapper whose options are not understood is identified as itself, an
  unknown.

**Node and Java runtimes.** A command whose program name resolves
(symlinks followed) to `node` or `nodejs` is a Node runtime, and to
`java` a Java runtime, wherever the file lives, in the tree or outside
it. A shell is unknown, because the program it runs is not the one
perfproof launched.

*Node* is asked, not derived. The gate runs the launch chain's own
`node -e` in the benchmark's working directory and environment, with
stdin closed, a timeout and output capped at 4096 characters; Node
prints the real path of its own executable and its version. The
runtime is the executable that answered, so a version-manager shim
(`asdf`, `nodenv` and the like) that selects a binary by directory or
environment is seen through. The record carries the real path and the
version followed by `; sha256 <digest>` of that file, so a rebuilt or
patched binary at the same path and version still differs. A probe that
cannot start, times out, exits non-zero, prints more than a report or
names a file that cannot be read makes the runtime unknown, so the
gate is `not_available`.

*Java* is identified by its resolved path, the banner of `java
-version` (run the same way; stderr, with the `Picked up ...` lines
that `JAVA_TOOL_OPTIONS` adds dropped) and the SHA-256 of the resolved
launcher. A `java` that exits non-zero, such as the macOS
`/usr/bin/java` stub with no JDK behind it, is unknown.

*Scripts and launchers.* A `node_modules/.bin` script is followed
through its `#!` line (`#!/usr/bin/env node` resolves `node` on the
benchmark's `PATH`) and then asked as above; one written for `sh` is a
shell. `npx` and `npm exec` are followed: the command is resolved with
every `node_modules/.bin` from the working directory upward in front of
the `PATH` to learn what kind of program it is, and when it is Node the
Node is asked through the launcher itself (`npx --no-install node -e`,
`npm exec --no -- node -e`), so `.npmrc` settings, `script-shell` and a
local `node_modules/.bin/node` apply exactly as they do to the
benchmark, with no list of settings to keep complete. Because these two
fetch and run a package that is not installed, they are followed only
when the command is present in a `node_modules/.bin` or `--no-install`
(`--no`) forbids the fetch, with no other option before the command
except `--`; the probe always carries `--no-install`, which stops
an install but not a registry metadata request npm may still make
when the command is neither local nor global. A command's own
interpreter is checked against the launcher's: a `.bin` script
whose shebang names a different Node (`#!/abs/node18`) than the
launcher's `node` is refused, with both named. Not
followed, so unknown (`runtime` `not_available`, with the reason),
because the Node they run is chosen by configuration the gate does not
read: `pnpm`, `pn`, `yarn`, `yarnpkg`, `bun`, `deno`, `npm run` and
every other `npm` subcommand, and options not understood. Where the
form would also fetch code, `npx`, `pnpx`, `corepack` and the rest of
the list above, the run stops instead. The launcher's own runtime is
not recorded: only the command's is.

Not seen for Node and Java: `NODE_OPTIONS` is recorded with its value
and `JAVA_TOOL_OPTIONS` by name only, and neither is gated (the
Node probe runs with `NODE_OPTIONS` removed, so a preload can neither
forge nor spoil the report); Node options set in an `.npmrc`
(`node-options`) are not recorded anywhere; the JDK's `libjvm` and
class libraries are covered by the version banner only, not hashed,
and a `java` shim that chooses between JDK homes is recorded as the
shim, which passes; a Node program that spawns
another runtime is recorded under its own.

**Recorded, not gated:** the environment each command ran with (names, and
values for variables known to affect performance such as
`OMP_NUM_THREADS` and `PYTHONHASHSEED`), each tree's dependency
lockfile hashes, OS and kernel, CPU frequency policy, power and thermal
state. Differences between the arms appear in the report as warnings.

**Allowed only by explicit opt-in in the spec, and labelled in the
verdict:** dependency or runtime changes (when upgrading a library is
the optimization), and comparisons across hosts.

**Harness files perfproof owns.** A benchmark the agent wrote for a
repository is not part of either version of it. It is usually
uncommitted, so a baseline checkout lacks it. Files the spec lists in
`harness`, `inputs` or `config` below `.perfproof/harness/` belong to
perfproof. Before anything runs, perfproof replaces that directory in
both trees, at the same relative path, with a byte-identical copy of the
one in the main checkout, where the lock manifest was taken. It replaces
the whole directory, so a file a candidate adds beside the benchmark is
removed and a symlink standing in its place is replaced, not followed.
This holds for a candidate directory the user supplied too: whatever it
has in `.perfproof/harness/` is deleted. As soon as the spec lists one
file in that directory, the `lock` gate locks every file in it, listed
or not, so a helper module beside the benchmark that is edited after the
spec was frozen is refused. Only `.git` and `__pycache__` are ignored;
any other file that appears or changes there after the freeze, such as a
cache a benchmark writes beside itself or a `.DS_Store`, fails the
`lock` gate. This is intended: it fails closed. The gate hashes both copies against the
manifest: an edit to the main checkout after the freeze is refused, and
a candidate's own changes can never reach these files.

**Per-tree setup.** A spec may declare `setup`: argv commands, with a
timeout for each, run once in each tree, baseline first, before warmup
and outside the timed region, with the benchmark's environment. They
prepare a tree the same way for both versions, for example
`["uv", "sync", "--frozen"]`, which gives each tree its own virtual
environment, with the project installed editable from that tree's own
source. Run the benchmark with `uv run --frozen` (or `--no-sync`), so
that it never re-resolves or re-installs mid-run, or with the
tree-relative path of that environment's interpreter (`.venv/bin/python`).
A path into the main checkout's environment would, with an editable
install, import the main checkout's source in both trees and make the
comparison meaningless. The `lock` gate is checked again after setup,
which runs code from the candidate's tree.

Limits to know. Setup runs in the candidate directory the user supplied,
as given, so it can change that directory's environment, including the
main checkout's `.venv` when the candidate is the main checkout. A
dependency difference between the trees, such as a changed lockfile, is
currently only a `dependency_lock_differs` warning, not a gate; so
setup that installs different dependencies in the two trees is measured,
and flagged, not refused. A launch error of setup that does not point at
its program or working directory (descriptors, processes or memory
exhausted, an oversized environment) is a fault of the host: the
comparison stops with no verdict as a harness fault, for either tree.
When a program or interpreter the benchmark or the test command names
lives in an ignored directory such as `.venv/` that the baseline
checkout does not have, the error says so and advises `setup`; the
setup, reset and runtime-probe commands get no such explanation.

Rules that follow:

- perfproof launches every measured process itself with an argv list,
  so the recorded command is the process that ran.
- The spec is frozen once defined. Changing anything in it creates a new
  spec, which needs a new baseline.
- The baseline is measured again in every comparison. perfproof never
  compares a candidate against stored baseline numbers.
- With the `reported` metric the number comes from the benchmark's own
  process, which the candidate could influence. So a `reported` metric
  is allowed only when the code that times and prints the value is in
  locked files, and perfproof records wall time alongside it and
  computes the wall-clock interval too. The two measure different
  things (wall time includes startup), so only direction is compared:
  if one interval shows the candidate faster and the other excludes
  that, the record carries a `metric_disagreement` diagnostic and the
  report leads with it. The verdict always comes from the spec's
  metric. perfproof never switches to whichever metric looks better.
- Out of scope for v1: candidates that attack perfproof itself or the
  operating system. perfproof assumes the candidate changes only its
  own source tree.

## Observations

**One round is one observation.** A round runs the baseline once and
the candidate once, back to back. Its paired ratio is the observation,
and `n` is the number of rounds.

**Order is balanced.** On one host, rounds come in order pairs: one
round of the pair runs the baseline first, the other runs the
candidate first, and which of the two comes first is drawn at random.
Every look uses complete order pairs only (see "Stopping"). A fair
coin per round would leave the two orders unequal by chance, and with
an order effect that imbalance shifts the mean: in an audit simulation
with a 3% order effect, the t interval at `n = 10` missed from above at
0.67% against a nominal 0.42%, and with balanced order it missed at
0. With balanced order the effect cancels in every look, and the swing
it adds to `d` widens the interval instead of moving it. The
cross-machine design under "Comparisons across machines" has its own
ordering.

- Each execution produces one value. If the benchmark loops
  internally, the value is the single number it reports for that
  execution. Inner iterations share process state, so they are not
  independent observations.
- Warmup executions run first for each version and are discarded.
- The ratio is oriented so that above 1 means the candidate is faster:
  `r = baseline / candidate` when lower is better (time, counts),
  `r = candidate / baseline` when higher is better (throughput).

**Metrics.** One per benchmark:

- `wall`: wall-clock time of the whole command.
- `cpu`: user plus system CPU time of the command, from `wait4`.
- `reported`: the benchmark prints `PERFPROOF {"value": <number>}` on
  stdout. Any language can do this, and it excludes process startup.
- `counter`: hardware instruction or cycle counts from `perf stat`
  (Linux). Far less noisy than time, so small changes need fewer rounds.
  Where `perf` can't count the event (another operating system, no
  `perf`, no permission), the comparison does not stop. The declared
  fallback is `wall`: it measures the whole command's wall time, records
  a `counter_unavailable` warning with the reason, and withholds a KEEP,
  because the verdict then rests on the fallback and not on the metric
  the spec named. Its samples are seconds, whatever the spec's unit.

**Resolution.** A measurement is only known to within its
resolution, and a coarse one reads a small change as no change or as a
whole step. In an audit simulation of timings near 10 ms printed to
the millisecond, a true speedup of 1.06% read as 10/9 in every round
and gave 4000 false KEEPs in 4000 comparisons. Every value therefore
carries a resolution `q`:

- `wall` and `cpu`: the clock's step, measured once per run.
- `counter`: one count.
- `reported`: declared in the spec when the benchmark knows it (the
  timer it reads, the unit it rounds or truncates to), and otherwise
  the place of the last digit printed for that value (`9` has `q = 1`,
  `9.40` has 0.01). A value printed with its trailing zeros dropped,
  such as `1` for `1.000`, gets the coarser step: that costs power,
  never correctness, and printing a fixed number of decimals avoids
  it. Printed digits overstate the resolution when
  arithmetic follows the rounding: a millisecond total divided by 3
  prints as `9.666666666666666`. So a `reported` metric without a
  declared resolution must also look continuous: if either arm has
  fewer distinct values than half its rounds, the resolution can't be
  trusted.

A value is known only to within one step `q`: half a step when it was
rounded, but a full step when it was truncated or is the difference of
two readings of a stepped clock, and perfproof can't tell which. So
each round's log ratio is known only to within
`e_i = -log(1 - q_b / b_i) - log(1 - q_c / c_i)`, an exact bound since
`|log(1 + x)| <= -log(1 - |x|)`. The mean log ratio is off by at most
the mean of the `e_i`, so both bounds of every interval are widened
outward by that mean, including the exact interval of identical ratios
under "Numerics". The bound is exact when each value's `q` is at least
its real step; a `reported` value whose real step is coarser than
declared or printed, and that still looks continuous, is a stated
limit (in audit simulations, about 130 false KEEPs in 4000 on the bar
with a 5% shared factor and no other jitter). A run printed to the
millisecond around 10 ms widens by about 20% and can't reach a verdict
near a 3% bar. The widening, and the resolution stop below, cost
power, never correctness. Worst cases measured in audit simulations at
a true 4% speedup, as lost KEEP rate, with the noise per execution
where each was worst:

| What is measured or printed | Lost KEEP rate |
|---|---|
| 1 ms runs, nanosecond clock | about 1 point (1.6% noise) |
| 2 ms of `wait4` CPU time, microsecond step | about 12 points (1.6% noise) |
| 5 significant digits, trailing zeros kept (`%#.5g`) | about 2 points (1.7% noise) |
| 4 significant digits, trailing zeros kept (`%#.4g`) | about 23 points (1.5% noise); at 0.1% noise the continuity check stops about 94% of comparisons |
| 5 significant digits, trailing zeros dropped (`%.5g`) | about 9 points (1.5% noise); about 20 (0.1% noise), from comparisons stopped by a bare `1` |
| 4 significant digits, trailing zeros dropped (`%.4g`) | about 70 points (1% noise); nearly all at 0.1% noise |

Dropping trailing zeros costs more because a value printed as `1.1`
or `1` gets a coarse step, and a value printed as a bare `1` (which is
how `%g` and JavaScript's `JSON.stringify` print the value 1.0) ends
the comparison
with `coarse_resolution`. So a benchmark should print a fixed number
of decimals, or declare its resolution.

The widening is derived from the measurement, like the rounding bound
under "Numerics", and the record keeps it. Guardrail intervals are widened the same way, and in the
cross-machine design each session pair's observation is widened by
the mean `e_i` of its rounds.

**Resolution stop.** When a value is no larger than its own step, or a
`reported` metric with no declared resolution fails the continuity
check above, the comparison stops at that look with **INCONCLUSIVE**
(`insufficient_evidence`) and the diagnostic `coarse_resolution`; the
report asks for more digits or more work per execution. It is checked
after rule 1 and before rules 2 and 3, at every look, in both host
modes.

**Resolution unknown.** For a `reported` metric with no declared
resolution, the step comes from the printed digits. If a value's line
can't be read back from the benchmark's output to find them (it is
gone, or its text is not the number the runner recorded), the
resolution of that value is unknown. The comparison goes on, measuring:
that value's step is taken as 0 for the interval, so the interval is
not widened for it, the continuity check still applies, the record
carries a `resolution_unknown` warning, and a KEEP is withheld. A
REJECT on such values rests on the continuity check alone, a stated
limit.

### Assumptions

The procedure assumes the log ratios `d_i = log(r_i)` of different
rounds are independent draws from one distribution, up to the
dependence that batch means absorbs and the two conditions in which
the diagnostic gate below refuses to decide.

**Skew in one version.** When one version is occasionally much
slower, the log ratios are skewed, and a t interval is expected to
under-cover on the skewed side. Calibration has no one-version skew
scenario, so whether the primary covers under skew is unknown: no
committed run shows it passing or failing, and until one does this is
a stated limit of the procedure. The record reports the skewness of
`d`, and the report warns when its magnitude exceeds 2. Slow runs
rarer than about one in `n` can't be seen at all by any procedure on
`n` rounds; the record states how many rounds the verdict rests on.

- **Within a round**, the two runs share conditions such as heat and
  background load. That dependence is wanted: it is what makes the
  ratio cancel slow drift.
- **Between rounds**, independence can fail: thermal state, caches and
  background jobs persist across rounds, and noise can change level
  halfway through. Pairing does not fix this.
- **Order inside a round** can matter, for example when the second run
  inherits a warm cache. Balanced order (above) cancels that bias.

Each of the two violations above, and the order effect, is a
calibration scenario. The record reports the lag-1 autocorrelation of
`d`, computed after subtracting each order's mean (balanced order
otherwise drives it negative), and warns when it is above 0.3. The
plain t interval on the rounds failed the acceptance rule under the
autocorrelated scenario, which is why the primary is batch means:
consecutive observations are grouped in blocks and each block mean
becomes one observation ("Speedup and interval").

**Block size (T1.5 audit N1, decided in T1.6).** A block is 6
observations, three whole order pairs on one host and six session
pairs cross host. Blocks of 5 split order pairs, so a look at 16 or 26
rounds would end on an unpaired round. Blocks hold complete pairs, and
a trailing incomplete block is dropped, which drops whole pairs only,
because one-host looks are on even round counts. Fewer than two
complete blocks give no interval: a look at fewer than 12 observations
neither keeps nor rejects and the comparison continues, and at its
last look it is INCONCLUSIVE. The evidence is under "Calibration
results", from the run that chose it and a second run at a fresh seed
that confirmed it.

**Diagnostic gate (T1.8).** Blocks of 6 still under-cover in two
conditions inside the audit ranges, found by the T1.6 audit and kept
as development scenarios ("Exposed scenarios"): AR(1) with `rho`
0.6 to 0.7 and little other noise, where the dependence left between
block means makes each look's interval too narrow and the miss rate
adds up over the looks, and 4 to 5% outliers of 2.8 to 4.5 times with
a CV under 1%, where two or three block means decide the look and one
outlier in them is enough to mislead it. (INFERENCE: these mechanisms
were not isolated by a committed run.) Both can be seen in the data they
harm. At a look, a KEEP or a performance REJECT (rules 2 and 3
of the decision rule) is **withheld** when a diagnostic of the `n`
observations says the interval's coverage is not shown at the look's
length:

| Diagnostic | Withheld when | Until the look has | Named |
|---|---|---|---|
| Lag-1 autocorrelation of `d`, order means removed as above | above 0.1 | 20 blocks (120 observations) | `autocorrelated_rounds` |
| Outlier share: the share of rounds whose `d` is more than 6 robust standard deviations from the median of its order | above 0.03 | 10 blocks (60 observations) | `outlier_rounds` |

The robust standard deviation is 1.4826 times the median absolute
deviation of the order-centred `d` over all rounds; when more than
half the deviations are 0 it is 0 and every nonzero deviation is an
outlier. Blocks are observations divided by 6, as above. Both
diagnostics use the observations of the look itself (rounds on one
host, session pairs cross host), so the gate needs no state between
looks.

A withheld look is a continue while budget remains (rule 4) and
**INCONCLUSIVE** (`insufficient_evidence`) at its last look (rule 5),
with the diagnostic named; when both apply, the autocorrelation is
named. The interval is still computed and recorded, the regression
flag is not set, and a look the gate does not touch is decided as the
decision rule says. The thresholds are fixed here and the acceptance
rule is not adjusted to them.

What this costs: a comparison whose lag-1 autocorrelation is above 0.1
at a look cannot be decided there unless the look has 20 blocks, which
under the default schedules only GPU kernels can reach (120 rounds, at
its last look); every other target class ends INCONCLUSIVE when the
autocorrelation is above 0.1 at its last look. A lag-1 above 0.1 is
common in noise that is independent, so the gate also costs power
where nothing is wrong, and the diagnostic is blunt cross host, where
the 15% offsets between sessions dilute the autocorrelation `d` shows.
Likewise an outlier share above 3% needs 10 blocks, which the Python
and native schedule reaches only at its last look (60 rounds) and GPU
kernels from its third (60 rounds), so web services, agentic apps and
LLM serving end INCONCLUSIVE; one outlier is above 3% of any look
shorter than 34 observations. The measured cost is
under "Calibration results".

A withheld look takes no KEEP or REJECT that the interval does not
give at that look, but unlike an early INCONCLUSIVE it lets the
comparison reach later looks, so the argument under "Stopping" does
not carry over by itself: the gate is calibrated as part of the
procedure, and the acceptance rule below judges the procedure with it.
The limits (0.1, 20 blocks; 0.03, 10 blocks, 6 deviations) were chosen
on exploratory runs on three seeds before the runs below; those
exploratory counts are not committed, and the committed evidence is the
procedure with and without the gate in the same cells.

### Comparisons across machines

The default, and the comparison perfproof's claims rest on, runs both
versions on one machine. Pairing rounds cancels what the two versions
share. It does nothing about a difference between machines: if the
baseline always runs on a machine that is 10% faster, no amount of
pairing removes that.

So in v1:

- **One fixed machine per version is refused.** That comparison fails
  the `same_host` gate.
- **Cross-machine comparisons need an explicit opt-in** and use one
  design only. Sessions alternate (A, B, A, B...), and every session
  gets a host allocated fresh from a pool, for example a new Modal
  container, so perfproof never picks which host a version gets. Each
  session is reduced to the mean log value of its rounds, and one
  observation is a consecutive A and B session pair. Host offsets then
  vary from pair to pair and become noise in the interval instead of a
  bias. Only completed pairs count: a baseline session and the
  candidate session launched right after it, both finished. A failed
  or incomplete session voids its pair, and sessions are never
  re-paired across the gap. A host that was never allocated is not a
  session.
- **Below `n0` completed pairs, speed never decides.** While budget
  remains and another pair can be collected, the comparison continues.
  When no more pairs can be collected, the verdict is INCONCLUSIVE. A
  failed gate still decides by rule 1 at any pair count.

  | Situation | Action |
  |---|---|
  | A gate failed | REJECT, naming the gate |
  | Fewer than `n0` pairs, and another pair can be collected | Continue |
  | Fewer than `n0` pairs, and no more can be collected | INCONCLUSIVE |
  | `n0` pairs or more | The resolution stop, then rules 2 to 5 of the decision rule |
- **The design rests on an assumption perfproof can't check:** that the
  pool allocates hosts independently of which version runs. The verdict
  is labelled cross-host and states that assumption, the record keeps
  every session's fingerprint, and the report warns when host types
  differ between the arms (GPU model, driver, CPU).
- Calibration of this design simulates random per-session offsets. It
  cannot cover a pool that hands one version systematically different
  hosts, and the report says so.
- Drift and order effects inside a session cancel in the session-pair
  design, and drift across a whole campaign biases a pair by about
  `log(1 + D) / (2n - 1)` for total drift `D` over `n` pairs, because the
  candidate session always follows the baseline one. Calibration does
  not cover those two effects across hosts, and that is a stated limit
  of cross-machine verdicts.

## Speedup and interval

**Estimand.** The speedup is the geometric-mean ratio
`S = exp(E[d])`. It is the standard summary for multiplicative
performance changes, and it is what the primary procedure targets.

**Primary procedure, fixed in advance.** Batch means. At a look with
`n` observations (rounds on one host, session pairs cross host), the
log ratios `d` are grouped in consecutive blocks of 6, the mean of `d`
over each block is one observation, and the interval for `E[d]` is the
Student t interval on the `m = n // 6` block means (`m - 1` degrees of
freedom) at the per-look confidence below, exponentiated to give
`[L, U]` for `S`. A trailing incomplete block is dropped, so block
boundaries stay where they were as the looks accumulate. It is the
same procedure on one host and cross host.

- **A bound that cannot be represented** is no interval: a lower bound
  that underflows to 0 or an upper bound that overflows to infinity (the
  conservative values `intervals` returns past the float range), before
  or after the resolution widening, or a NaN bound. The look records no
  interval, neither keeps nor rejects, and is rule 4 while budget
  remains and rule 5 (INCONCLUSIVE, `insufficient_evidence`) at its last
  look, never an error; the record carries a
  `interval_unrepresentable` warning, so the empty interval has a cause.
- **Fewer than two complete blocks** (`n` below 12) give no interval,
  never an error: the look neither keeps nor rejects, it is rule 4
  while budget remains and rule 5 (INCONCLUSIVE,
  `insufficient_evidence`) at its last look, and the resolution stop
  still applies. With `n0 = 10` the first look therefore never
  decides.
- The diagnostic gate under "Assumptions" can withhold a KEEP or a
  performance REJECT at a look that has an interval.

**Why batch means.** The plain Student t interval on the rounds was
the primary until T1.8. In calibration it failed the acceptance rule
under AR(1), and cross host it passed the development scenarios only
because the 15% session offset dilutes the autocorrelation, so it is
not kept for either host mode. The counts are under "Calibration
results". The Hodges-Lehmann estimate with the Wilcoxon interval and
the bootstrap of the ratio of medians were run as challengers, lost
everywhere, and are deleted.

## Stopping

The look schedule is fixed before the first round:

- The first look comes after `n0` rounds, then one every `k` rounds,
  for at most `K` looks, and never past `max_rounds` or `max_seconds`.
  `n0` is at least 10: intervals on fewer rounds are too fragile
  under heavy tails. On one host `n0` and `k` are even, so every
  scheduled look falls on complete order pairs.
- If the budget runs out between looks, perfproof takes a final look at
  the complete order pairs it has, dropping an unpaired last round.
  That look replaces the remaining scheduled looks, so the total never
  exceeds `K`. A budget that ends before `n0` rounds gives no look at
  all, and the result is **INCONCLUSIVE** (`insufficient_evidence`).
- A detached run's time limit (`docs/cli.md`, "Timeout") is a budget
  end like `max_seconds`: before starting another round that cannot
  finish, with the kill grace, before the limit, the run takes its
  final look on the rounds it has, exactly as at a `max_seconds` end,
  and records a `time_limit_reached` warning that says how many
  rounds the final look used (complete order pairs only). Under the
  uncalibrated procedure that is always INCONCLUSIVE
  (`insufficient_evidence`); a run with fewer than `n0` rounds has no
  look and is INCONCLUSIVE with no interval. The time limit is a budget
  end, not a verification gap: it does not block a KEEP that the final
  look's interval supports, and the warning is not one of the things
  that withhold a KEEP. Only a run stuck inside one round or the gates
  when the limit arrives is killed with no verdict.
- Each look computes a two-sided interval at confidence
  `1 - alpha / (2K)` with `alpha = 0.05`: a Bonferroni split over the
  looks, designed to halve the error the promise allows. The margin is
  deliberate. Calibration has to show each error rate is below 2.5%,
  and a procedure designed to exactly 2.5% can't show that: in audit
  simulations an exact interval split as `alpha / K` gave false-KEEP
  rates on the bar of 2.5% at one look, 2.2 to 2.5% at two and 2.0 to
  2.2% at three, which pass a calibration cell with probability 0.04
  to 0.7. Split as `alpha / (2K)`, two looks gave 1.1 to 1.2%. The cost
  is power: averaged over the audit's scenarios without
  autocorrelation, the KEEP rate fell by 5 to 8 points at a true 5%
  speedup and 3 to 5 points at 10%; single scenarios ranged from 0 to
  63 points, the most in the least noisy one.
- The comparison stops at the first look where the resolution stop or
  rule 2 or 3 below fires.
- **A schedule outside the calibrated ones.** The gate under
  "Assumptions" lifts at 20 blocks (120 observations), so a schedule
  that can take more than one look at that length has looks the
  calibration never covered (the T1.8 audit's F2). Such a spec is not
  refused: perfproof cannot verify the procedure there, so it measures
  anyway, says what it could not verify, and withholds only the KEEP.
  The comparison runs; a KEEP is withheld the way an unestablished
  guardrail withholds it (rule 2 continues, and is INCONCLUSIVE at the
  last look); REJECTs (a failed gate, a measured performance REJECT)
  are decided as usual; and the record carries the warning
  `schedule_uncalibrated`, which the report shows. The test is whether
  more than one such look is possible: the scheduled looks at
  `n0 + j k` for `j < K` that are 120 or more and at most
  `max_rounds`, plus one when exactly one scheduled look is that long,
  it is not the `K`-th, and `max_rounds` is at least 2 rounds past it
  (a budget that ends there takes a final look on the complete order
  pairs, which replaces the remaining scheduled looks and can also be
  120 or more). The default schedules never reach it: GPU kernels
  has its single look at 120 as its last.

**Defaults per target class.** Provisional: chosen by reasoning about
each class's round cost and noise, not measured or calibrated. T1.6
calibrates the procedure under them, and a class's defaults change only
with that evidence.

| Target class | `n0` | `k` | `K` | `max_rounds` | `max_seconds` | Why |
|---|---|---|---|---|---|---|
| Python, CPU-bound | 10 | 10 | 6 | 60 | 1800 | Rounds are cheap; six looks let a clear win stop at 10 rounds |
| Native | 10 | 10 | 6 | 60 | 1800 | As Python |
| Web services | 10 | 10 | 3 | 30 | 3600 | A round is a load run of about a minute; fewer looks cost less confidence |
| LLM serving | 10 | 6 | 3 | 22 | 3600 | Paid GPU minutes; cross host, the same numbers count session pairs |
| GPU kernels | 20 | 20 | 6 | 120 | 1800 | Kernel timings are often bimodal, so a later first look |
| Agentic apps | 10 | 10 | 3 | 30 | 3600 | Rounds are expensive and noisy |

Everywhere `max_rounds = n0 + k (K - 1)`, so a round budget never cuts
the last look short, and on one host `n0` and `k` are even. A spec may
set its own schedule, and the record holds the schedule used.

**Why this controls false KEEPs.** Suppose the true speedup is at or
below the bar. A false KEEP needs some look's lower bound to exceed the
bar, which means that look's interval missed `S` from above. Each look
does that with probability at most `alpha / (4K)` if its interval is
valid at its `n`. Over at most `K` looks the union bound gives a
false-KEEP probability of at most `alpha / 4 = 1.25%`, however the
looks depend on each other, which leaves the promised 2.5% with room
for an interval that is only approximately valid. The same argument
bounds false REJECTs when the true speedup is above the bar.

That argument leans on one assumption: each look's interval is valid
at its own `n`, including when `n` is set by a time budget. Calibration
tests it under the complete stopping procedure, not at fixed `n` alone.

## Decision rule

| Name | Default | Meaning |
|---|---|---|
| `min_effect` | `0.03` | Smallest speedup worth keeping |
| `alpha` | `0.05` | Error the promise allows; each look is designed to `alpha / (2K)`, half of it in total |
| `n0`, `k`, `K` | per target | Look schedule |
| `max_rounds`, `max_seconds` | per target | Run budget |

The first rule that matches decides:

1. **REJECT** (`correctness` or `integrity`) if a gate failed, which
   needs evidence: a gate that could not be checked is `not_available`,
   never failed. When
   several gates fail, the first in this order is named:
   `side_effects`, `lock`, `procedure`, `runtime`, `same_host`,
   `correctness`, `coverage`. Integrity comes first because a
   correctness check run under a broken lock or procedure tests
   nothing, and coverage comes last because it asks whether passing
   checks reached the change.
   Then, if the resolution stop under "Resolution" applies:
   **INCONCLUSIVE** (`insufficient_evidence`, `coarse_resolution`).
2. If `L > 1 + min_effect`, the guardrails decide, as set out under
   "Guardrails": **KEEP** (`performance`) when every guardrail passed or
   is not applicable, **REJECT** (`guardrail`) when any is breached,
   **INCONCLUSIVE** (`insufficient_evidence`) at once when something
   that no further round can lift withholds the KEEP (see "Gate
   statuses"), and otherwise continue while budget remains, then
   **INCONCLUSIVE**.
3. **REJECT** (`performance`) if `U <= 1 + min_effect`. If `U < 1`, the
   record also flags a regression.
4. **Continue** if budget remains.
5. **INCONCLUSIVE** (`insufficient_evidence`): the interval still
   straddles the bar when the budget runs out.

**Gate statuses.** Every gate ends `passed`, `failed`, `waived` or
`not_available`. Only a gate with its own opt-in can be `waived`, and
the opt-in is recorded. `not_available` means the gate couldn't be
checked here, for example coverage for a language perfproof can't
instrument, a runtime perfproof can't identify, a host identity it
can't read, or a spec with no correctness check. It blocks KEEP. When
rule 2 matches with any gate `not_available`, the result is
**INCONCLUSIVE** (`insufficient_evidence`) at that look, naming the
gate, because more rounds can't make it available. Rules 1 and 3 still
give REJECT, and so does a guardrail breach under rule 2, because a
REJECT keeps nothing. Four more things withhold a KEEP the same way
without being gates: a counter metric that fell back to wall time, a
value whose resolution is unknown, a look schedule outside the
calibrated ones, and a guardrail not shown or not declared. Each is
named in the record (a `RunWarning`, or the guardrail's status), so the
agent can say what would make the change keepable; the schema refuses a
KEEP that carries one. A comparison does not go on collecting rounds for
a KEEP that can't come: when a look would keep but one of these, fixed
for the run, withholds it (any `not_available` gate, an undeclared or
unmeasurable guardrail, the counter fallback, an unknown resolution, a
late-look schedule), the comparison ends at that look as INCONCLUSIVE
under rule 2, naming it, and REJECT stays reachable at earlier looks as
before. A guardrail that is `not_established` is not fixed for the run:
more rounds can narrow it, so those looks continue.

**Before calibration.** Until the procedure has passed calibration
(see "Calibration"), rules 2 and 3 never match. A comparison runs to
the end of its budget and ends **INCONCLUSIVE**
(`insufficient_evidence`), with the diagnostic
`procedure_uncalibrated` recorded. Rule 1 still gives REJECT when a
gate fails, and the resolution stop still ends it early. The
diagnostic gate under "Assumptions" is still evaluated on the rule the
interval gives as if calibrated, so the record names the diagnostic
(`autocorrelated_rounds` or `outlier_rounds`) that would have withheld
a KEEP or REJECT, and the report says so. Every record states whether
it was made by a calibrated procedure, and that field has no default.

With the default bar of +3%:

| Speedup | Interval | Rule | Result |
|---|---|---|---|
| +8% | +5% to +11% | 2 | KEEP |
| +4% | +3.5% to +4.5% | 2 | KEEP |
| +5% | +2% to +8% | 4, then 5 at the end of the budget | INCONCLUSIVE |
| +10% | -2% to +21% | 4, then 5 at the end of the budget | INCONCLUSIVE |
| +1% | -1% to +2% | 3 | REJECT, performance |
| -6% | -9% to -3% | 3, regression flagged | REJECT, performance |
| not measured | not computed | 1 | REJECT, correctness |

A promising point estimate never turns into a KEEP. If the budget ends
with the interval straddling the bar, the answer is INCONCLUSIVE
however good the estimate looks.

### Guardrails

The **primary objective** is the one metric the goal is about, set per
workload in the project model, such as p99 latency for a web workload
or throughput for a serving one. It decides whether a change is faster.
**Guardrails** are the other metrics that must not get worse while it
does. For a given workload a metric is the primary or a guardrail,
never both.

**Applicability.** Which guardrails apply depends on the target class.
The project can override that per workload, and the reason is recorded.

| Guardrail | Applies by default to | Provisional allowance |
|---|---|---|
| Peak memory: max RSS of the whole process tree | every target class | 10% |
| Peak GPU memory | LLM serving, GPU kernels and PyTorch | 10% |
| p99 latency, when it isn't the primary | web services, LLM serving, agentic apps | 5% |

Any other guardrail, such as throughput when latency is the primary,
has no default: the project declares it with its allowance. For each
workload, every guardrail in the table is either applied or marked
`not_applicable` with a reason. Nothing is left implicit in the
record: a default guardrail the spec does not declare is not a reason
to stop. The comparison measures anyway, and the record lists that
guardrail as `not_declared`, with the reason. Where the target class
is unknown, the defaults are peak memory, peak GPU memory and p99
latency, so a spec that declares none of them records all three.

**The defaults are not permission to regress.** 10% and 5% are the
refusal thresholds used until the project sets its own. They are not a
budget to spend. Every guardrail's measured change is reported, and a
change that costs memory or latency within its allowance is flagged
with how much, so the developer sees the trade. The record keeps which
allowances applied and where each came from: the default, the target
class, or the project.

**Absolute limits.** A project can also set absolute limits, such as a
memory ceiling (a container limit, the GPU's capacity) or a latency
SLO. A limit is checked on the candidate's own measurements, not on a
ratio: compliance is shown when the upper bound of the candidate's
metric is within the limit, a breach when its lower bound is beyond
it. A change can sit within its relative allowance and still breach an
absolute limit.

**Status.** Every guardrail ends a comparison in exactly one state:

| Status | Meaning |
|---|---|
| `passed` | Shown within its allowance and every limit: the upper bound of its worsening ratio is at most `1 + allowance`, and the candidate's upper bound is within each limit |
| `breached` | Shown worse: the lower bound of its worsening ratio exceeds `1 + allowance`, or the candidate's lower bound exceeds a limit. One check showing a breach is enough, even if another check's interval is missing |
| `not_established` | Measured, but the data can't tell yet; at least one of its intervals was computed |
| `unmeasurable` | Applicable, but perfproof could not measure it here; the reason is recorded |
| `not_declared` | A default guardrail the spec does not declare; nothing was checked, and the reason is recorded. It has no spec entry to carry an allowance |
| `not_applicable` | Marked not applicable for this workload; the reason is recorded |

**Decision.** Guardrails matter only when the primary interval would
give KEEP:

- every guardrail `passed` or `not_applicable`: **KEEP**;
- any guardrail `breached`: **REJECT** with reason `guardrail`, keeping
  the primary interval and every guardrail interval that was measured,
  the breaching one always among them;
- otherwise, some guardrail `not_established`: continue while budget
  remains, then **INCONCLUSIVE** (`insufficient_evidence`), naming the
  guardrails that couldn't be established, since more rounds can
  narrow its interval;
- or some guardrail `unmeasurable` or `not_declared`: **INCONCLUSIVE**
  at that look, naming it, because more rounds can't measure or declare
  it. An applicable metric that can't be measured never
  passes silently, and a default one nobody declared is not passed
  either: declaring it, with an allowance, in a new spec is what makes
  the change keepable.

A performance REJECT or INCONCLUSIVE stands as it is, with the
guardrails reported alongside. Requiring compliance to be shown, not
merely not disproved, means a noisy guardrail such as p99 can hold a
change at INCONCLUSIVE; the evidence planning report names it as the
blocker.

**Error control.** Let `G` count every allowance and limit checked. Each
guardrail check at a look uses confidence `1 - alpha / (2 K G)`, with
the same margin as the primary under "Stopping". By the union bound,
the chance of a KEEP while some guardrail is truly beyond its
allowance or limit is at most 1.25%, and the chance that a guardrail
shows a breach when all are truly within is at most 1.25%; both are
held to 2.5% in calibration. The primary interval and its false-KEEP bound are
unchanged, because guardrails can only turn a KEEP into a REJECT or an
INCONCLUSIVE. Calibration measures guardrail false passes and false
breaches per cell under the acceptance rule.

### Numerics

The decision and the interval have separate jobs, and floating-point
error is handled in the interval, never in the decision.

- **The decision** compares the exact float64 bounds that are recorded
  with `bar = 1 + min_effect`, computed in float64 from the stored
  `min_effect`. A typed `0.118` is the float64 nearest it; the report
  prints the stored value so nobody has to guess. KEEP is strict
  (`L > bar`), REJECT is inclusive (`U <= bar`), and no tolerance,
  epsilon, display rounding or log-scale comparison is applied.
- **The interval** must never come out more favourable to the
  candidate than its exact value because of rounding. Measured on
  macOS arm64: 48 rounds all exactly at the bar, with
  `min_effect = 0.4730308035801494`, give a mean log ratio one ulp
  high, a standard deviation of 5.6e-17 instead of 0, and
  `L = U = bar + 1 ulp`, so a KEEP for a change that sits on the bar.
  The mirror case gives false REJECTs. Two measures fix this:
  - When every ratio is the same float, the interval is that ratio
    exactly, with no arithmetic that can move it; the resolution
    widening under "Observations" then applies to it like any other
    interval.
  - Otherwise, each bound is widened outward by an error bound derived
    from the operations that produced it. That is conservative
    interval construction, not rounding: the width comes from an
    analysis of the computation, never from observing which nudge
    makes the tests pass, and a fixed one-ulp step is not a derived
    bound.
- **The observations** are the float64 measurements as recorded and
  their float64 ratios. A ratio that rounds onto the bar is on the bar,
  even when the exact quotient of the two measurements lies a fraction
  of an ulp to one side.
- **Rounding to a display precision** (1.0300000000000002 shown as
  1.03) happens only in the rendered report, never before a decision.

## Calibration

Calibration checks that the procedure keeps its promises. Its pass
rule is fixed here, before any run, and is not changed after seeing
results.

### What counts as an error

The bar splits true speedups in two. Whether an outcome is an error
depends only on which side of the bar the true speedup `S` is on:

| True speedup | KEEP | REJECT | INCONCLUSIVE |
|---|---|---|---|
| At or below the bar, `S <= 1 + min_effect` | false KEEP | correct | not an error, unresolved |
| Above the bar, `S > 1 + min_effect` | correct | false REJECT | not an error, lost power |

- Exactly at the bar, KEEP's claim (`S > 1 + min_effect`) is false, so
  a KEEP there is a false KEEP and a REJECT is correct. This matches the
  strict `>` in rule 2.
- Below the bar, REJECT is correct however real the speedup is. The
  1.5% decoy sits below the bar, so rejecting it is a right answer.
- perfproof makes no claim to tell apart speedups just either side of
  the bar. Close to it, INCONCLUSIVE is the expected answer, and its
  rate is reported.
- Correctness and integrity REJECTs are not part of these rates.
  Calibration exercises the statistics; the gates have their own tests.

### Acceptance rule

| Error | True speedups tested | Limit |
|---|---|---|
| False KEEP | 0.95, 1.00, 1.03 | 2.5% |
| False REJECT | 1.04, 1.05, 1.10, 1.20 | 2.5% |

The argument under "Stopping" bounds both errors at 1.25% for every
true speedup on its side of the bar, against the 2.5% limit here. The cells nearest the bar, 1.03
for false KEEP and 1.04 for false REJECT, are where a broken procedure
shows first.

- Each cell (one noise scenario, one host mode, one true speedup) runs
  the complete procedure, looks and budget included, `R = 4000` times.
- **The controlled rates are unconditional.** A cell's false-KEEP rate
  is its number of KEEPs divided by all `R` runs, INCONCLUSIVEs
  included, and the same for false REJECT. That is the probability the
  argument under "Stopping" bounds, and the only one the acceptance
  rule checks. The error rate among runs that reached a verdict,
  KEEPs divided by KEEPs plus REJECTs, is not bounded by that argument;
  it is reported per cell as a diagnostic, never used to pass a cell.
- Any stopping rule added later, such as stopping early with
  INCONCLUSIVE, is calibrated as part of the procedure it changes. An
  early stop that only replaces "continue" at a scheduled look, with
  the looks and their confidence unchanged, cannot raise the
  unconditional rates, because every KEEP or REJECT it gives is one
  the original procedure gives on the same measurements. It can lower
  the share of runs that reach a verdict and change the conditional
  rate, so calibration measures both rather than assuming either.
- A cell passes only if the one-sided 95% Clopper-Pearson upper bound
  on its observed rate is at most the limit. An observed 2.4% fails;
  the evidence has to show the rate is below the limit, not near it.
  At `R = 4000` the largest passing count is 83 (2.075%) and 84
  (2.1%) fails, checked both as the beta quantile and from the
  definition `P(X <= k | p = 0.025) <= 0.05`. A procedure
  whose true rate is 1.5% passes a cell with probability 0.998; one at
  2.0% passes with probability 0.66. The split under "Stopping" is
  designed to 1.25% so that a sound procedure has room to spare.
- Every cell must pass. A failing cell can't be dropped, rerun at a
  larger `R`, or rerun at another seed. The fix is a change to the
  procedure, after which the whole calibration runs again at a new
  seed.
- Every cell reports its false-KEEP or false-REJECT count, rate and
  bound on its own. There is no pooled pass rate; one failing cell
  fails the calibration.
- Reported for every cell, but not pass or fail: KEEP rate (power) at
  `S` = 1.05 and 1.10, the INCONCLUSIVE rate, coverage at fixed `n`,
  and rounds used (mean and 95th percentile).
- Passing supports the procedure under the conditions tested, not in
  general. Every report names the conditions its target class was
  calibrated under.

### Development scenarios

Used while building the procedure. Each runs on one host (offsets
shared within a round) and cross host (random per-session offsets,
alternating session pairs, as under "Comparisons across machines").

| Scenario | What it imitates |
|---|---|
| Log-normal, CV 1%, 5%, 15% | Ordinary run-to-run jitter |
| Student-t, 3 degrees of freedom | Heavy tails |
| Bimodal, 10% of runs 30% slower | GC pauses, JIT tiers, cache misses |
| Rare 3x outliers, 1 in 50 | Background jobs |
| Linear drift of 10% across the session | Thermal throttling |
| Variance step, CV doubles halfway | A noisy neighbour arriving mid-session |
| AR(1), rho 0.5 | Rounds that resemble their neighbours |
| Order effect, second run of a round 3% faster | Warm caches handed from one run to the next |
| Per-session offset of 15% | Different hosts or sessions, as in the vLLM study |

### Exposed scenarios

The T1.6 audit of PR #33 drew its held-out scenarios from the audit
ranges below. Blocks of 6 rounds failed the acceptance rule in two
conditions inside those ranges (see "Diagnostic gate"), so the
scenarios that failed, with the auditor's ablations of the first, are
development scenarios from here on, in `tests/calibration/exposed.py`
with their exact parameters. A scenario name `A<seed>-<index>` is the
auditor's draw from the audit ranges; parameters are rounded here.
They run in every host mode and schedule like the scenarios above;
cross host, drift and the order effect are dropped, as the simulator
refuses them.

| Scenario | Parameters |
|---|---|
| `A20261052-00` | CV 0.66%, t df 6.8, 4.9% outliers of 4x, drift 18%, CV x1.3 from 52%, rho 0.0039, order 4% |
| `A20261052-04` | CV 15%, t df 6.4, CV x2.7 from 88%, rho 0.68 |
| `A20261052-14` | CV 11%, drift 13%, CV x1.2 from 49%, rho 0.61 |
| `A20261053-04` | CV 0.7%, t df 6.2, 4.4% outliers of 4.3x, rho 0.6 |
| `A20261054-08` | CV 0.54%, 4.3% outliers of 4.3x, rho 0.2, order 2.4% |
| `A20261054-13` | CV 0.52%, drift 14%, rho 0.61, order 2.5% |
| `A20261055-17` | CV 0.57%, t df 3.4, 4.2% outliers of 2.8x, CV x3.1 from 57% |
| `A20261056-02` | CV 0.54%, CV x1.3 from 56%, rho 0.62, order 2.8% |
| `A20261056-15` | CV 4.7%, CV x2.1 from 42%, rho 0.67, order 2.8% |
| `RHO0.6` | CV 5%, rho 0.6 |
| `RHO0.7` | CV 5%, rho 0.7 |
| `A00-outliers-only` | CV 0.66%, 4.9% outliers of 4x |
| `A00-outliers-5pct-x5-cv1` | CV 1%, 5% outliers of 5x |
| `A00-outliers-1pct-x5-cv1` | CV 1%, 1% outliers of 5x |

### Audit scenarios

Held out from development, so the procedure can't be tuned to them.
The audit checks two different things, and both are required:

- **Fresh seeds** on the development scenarios, to show the result
  wasn't luck of one seed.
- **New scenarios**, configurations nobody built the procedure
  against, to show it wasn't tuned to the development set. The
  `stats-auditor` draws them at audit time from these ranges, combining
  several violations in one scenario:

| Parameter | Range |
|---|---|
| CV | 0.5% to 20% |
| Student-t degrees of freedom | 2.5 to 10 |
| Outlier rate and size | 0 to 5%, 2x to 5x |
| Drift across the session | 0 to 20% |
| Variance step | 1x to 4x, at a random point |
| AR(1) rho | 0 to 0.7 |
| Order effect | 0 to 5% |

- The audit draws at least 20 new scenarios. That number measures
  breadth of conditions; the `R` runs inside each scenario measure its
  error rates. Results are reported per scenario, never pooled.
- The auditor applies the acceptance rule itself, from the raw outcome
  counts, rather than trusting the test suite's assertions.
- Audit parameters and seeds are written into this page only together
  with the audit's result, and the implementation never reads them.
  Once an audit has run, its scenarios are exposed: after a failure
  they join the development scenarios, and the next audit draws new
  ones. Fixing the procedure against a failed audit's scenarios and
  then re-auditing on those same scenarios does not count.

### Real noise

Synthetic scenarios can be wrong in ways that flatter the procedure.
For each target class, perfproof records long A/A traces (the same
commit run against itself) on two machines. Traces from the first
machine are used in development: to check the synthetic scenarios'
CV, tails and autocorrelation against reality, and to calibrate
directly by block-bootstrapping the traces with a known speedup
injected. Traces from the second machine are held out for the audit.
The same acceptance rule applies. Every A/A comparison must end REJECT
or INCONCLUSIVE, never KEEP.

| Target class | Development traces | Audit traces | Status |
|---|---|---|---|
| Python, CPU-bound | this MacBook | Linux CI runner | planned in M2 |
| Native code | this MacBook | Linux CI runner | planned in M4 |
| GPU kernels | one paid GPU session | a second paid session | planned in M5 |
| LLM serving throughput | one paid GPU session | a second paid session | planned in M5 |

Until a target class passes on its held-out traces, its verdicts carry
"not calibrated for this target class" in the report.

Calibration results are written to this page with the seed, `R`, the
observed rates, and their Clopper-Pearson bounds.

### Calibration results

Written by T1.6 and extended by T1.8. Every number below came from the
runs named here; the per-cell counts, bounds and diagnostics for all of
them are in `tests/calibration/results/` (`seed-<seed>.csv`, one row
per cell and procedure), and `tests/calibration/test_calibration.py`
reruns the cells nearest the bar and checks them against the T1.8
files. `uv run python tests/calibration/run_calibration.py report <csv>`
prints the per-cell table and `summary <csv>` the totals and the cost
table below from those files.

**Conditions.** Every development scenario of the table above and the
exposed scenarios, one host and cross host (drift and the order effect
have no cross-host cell, as the simulator refuses them; the exposed
scenarios run cross host without them), true speedups 0.95, 1.00, 1.03,
1.04, 1.05, 1.10 and 1.20, the four look schedules of the per-target
defaults under "Stopping" (python and native share one, web services
and agentic apps share one), sequential looks on, `R = 4000` per cell,
`min_effect = 0.03`, `alpha = 0.05`. That is 4 schedules x (11 one-host
+ 9 cross-host development scenarios + 14 exposed scenarios in each
host mode) x 7 speedups = 1344 cells, 560 of them the development
grid. One host uses balanced order. Development scenarios other than
the log-normal rows take a run-to-run CV of 5%, the exposed ones their
own; cross host every scenario also has a per-session offset of 15%.
Each cell draws from its own seed sequence keyed by the seed and the
cell's name, so a cell gives the same counts whichever other cells run
with it. The time budget holds `max_rounds` typical rounds plus the
look-ahead round, accumulated in log space, so a candidate that is
slower than typical reaches `max_seconds` before `max_rounds`. At the
seed of record the share of replications cut by time is 77% of the runs
at 0.95, 42% at 1.00, 25% at 1.03, 21% at 1.04, 17% at 1.05, 6% at 1.10
and 1% at 1.20 (mean over the 80 development cells of each speedup), so
a sizeable share of runs end at a final look on fewer rounds than the
schedule's last look. Not covered: one-version skew (above), a pool that
hands one version systematically different hosts, drift across a whole
cross-host campaign, CV other than 5% for the non-log-normal
development rows, and any target class other than through its schedule.
Real noise is not calibrated yet.

**Runs.** Four complete runs, all on this MacBook (macOS arm64), 4
worker processes, 25 s of wall time each for the two T1.8 runs.

| Run | Seed | Cells | Procedures |
|---|---|---|---|
| T1.6 development | 1652377851162723444 | 560 | t, batch means in blocks of 4, 5, 6 and 10, Hodges-Lehmann, bootstrap of the ratio of medians (percentile and BCa) |
| T1.6 of record | 844029934949031648 | 560 | t, batch means in blocks of 6 |
| T1.8 of record | 3598798392042499920 | 1344 | batch means in blocks of 6 with the gate (the production `run_looks`), and without it (the harness) |
| T1.8 fresh | 3078527063959977686 | 1344 | the same |

`PERFPROOF_CALIBRATION_SEED=<seed> uv run python
tests/calibration/run_calibration.py run --out <file> --workers 4`
reproduces a T1.8 run, and `csv` turns it into the results file. Both
T1.8 seeds were drawn before either run started, and neither was used
while the gate was being chosen, which used exploratory runs on other
seeds whose raw counts are not committed. The T1.6 development run's
other procedures were produced by the harness at commit `8406d80`.

**The t primary fails under AR(1).** In every run the t primary, the
procedure of "Speedup and interval" until T1.8, fails the acceptance
rule in the one-host AR(1)
cells and nowhere else: 10 failing cells at the development seed and 11
at the seed of record, all `ar1/one_host`. These are failing cells, not
rerun, written up as they came (seed of record):

| Schedule | Scenario | Host | 0.95 | 1.00 | 1.03 | 1.04 | 1.05 | 1.10 | 1.20 |
|---|---|---|---|---|---|---|---|---|---|
| python_native | ar1 | one_host | 0 (0.07%) | 27 (0.93%) | 498 (13.34%) **FAIL** | 204 (5.71%) **FAIL** | 85 (2.54%) **FAIL** | 0 (0.07%) | 0 (0.07%) |
| web_agentic | ar1 | one_host | 0 (0.07%) | 32 (1.07%) | 469 (12.59%) **FAIL** | 231 (6.42%) **FAIL** | 126 (3.64%) **FAIL** | 5 (0.26%) | 0 (0.07%) |
| llm_serving | ar1 | one_host | 0 (0.07%) | 53 (1.66%) | 421 (11.36%) **FAIL** | 233 (6.47%) **FAIL** | 112 (3.27%) **FAIL** | 1 (0.12%) | 0 (0.07%) |
| gpu_kernels | ar1 | one_host | 0 (0.07%) | 10 (0.42%) | 527 (14.09%) **FAIL** | 175 (4.94%) **FAIL** | 44 (1.41%) | 0 (0.07%) | 0 (0.07%) |

False-KEEP counts at 1.03 are 408 to 528 of 4000 over the two seeds
(the limit is 83), five to six times the limit, in every schedule. The
two seeds fail the same cells except python_native at 1.05, which fails
at the seed of record (85 false REJECTs, a bound of 2.54%) and passes at
the development seed. The t interval at fixed `n` shows the cause, at
the seed of record: under AR(1) with rho 0.5 its one-sided miss rate at
`n` = `max_rounds` is up to 5.4% (python_native), 6.7% (web_agentic),
6.8% (llm_serving) and 5.3% (gpu_kernels) one host against 0.2% to 0.4%
designed per side, while in the scenarios other than AR(1) it is at
most 0.8%. Cross host the same cells reach up to 1.00% (web_agentic),
against 0.42% designed (the `n_max_miss_above` and `n_max_miss_below`
columns of the seed-of-record CSV, `R` = 4000, so a Monte Carlo error of
at most 0.16 points): the 15% session offset dilutes the autocorrelation
but does not remove it. This is the condition under "Assumptions" for
the documented fallback.

**Decision: the fallback replaces the primary.** Under the rule
written under "Assumptions", the t primary fails the acceptance rule
under the autocorrelated scenario, so the prespecified fallback, batch
means, became the primary. This is the documented switch and no other
challenger is substituted.

**N1 (block size).** Candidates, all in whole order pairs, were
blocks of 4, 6 and 10 rounds, with the T1.5 block of 5 for reference.
At the development seed, over all 560 cells:

| Block (rounds) | Failing cells | Largest error count (limit 83) | KEEP at 1.05, one host | KEEP at 1.10, one host |
|---|---|---|---|---|
| 4 (2 pairs) | 1: gpu_kernels/ar1/one_host/1.03 | 95 | 22.9% | 70.6% |
| 5 (not whole pairs) | 1: python_native/ar1/one_host/1.03 | 85 | 21.6% | 66.9% |
| 6 (3 pairs) | 0 | 67 | 19.3% | 60.9% |
| 10 (5 pairs) | 0 | 56 | 14.4% | 47.5% |
| t (reference) | 10 | 528 | 26.7% | 84.1% |

The KEEP rates are unweighted means over the one-host scenarios other
than AR(1), all schedules together, a comparison between rows and not a
property of any one cell. Block 6 is the smallest of the whole-pair
blocks run (4, 6 and 10, with 5 for reference) that passes every cell, and it keeps more of the power than block 10, which
leaves no interval below 20 rounds. The block was fixed on the
development seed, so the seed of record is the test of it: block 6
passes all 560 cells there as well, with the largest error count 64
(1.97%, gpu_kernels/ar1/one_host/1.03). **Decision: blocks of 6
rounds.** The price against the t interval, from the same table, is
power: at the development seed, KEEP at 1.10 falls from 84.1% to 60.9%
in the mean over the one-host scenarios, and the cross-host means fall
from 2.9% to 2.1% (1.05) and from 27.6% to 18.2% (1.10). INFERENCE: part
of the cross-host loss is that blocks of 6 session pairs leave one
block, and so no decision, at the first look of the schedules with
`n0 = 10`; this was not isolated.

**Challengers.** All lose, and are deleted. At the development seed,
none passes a cell the batch-means fallback fails (it fails none), and
each except blocks of 10 fails cells that it passes; blocks of 10
fail none and lose on power:

| Procedure | Failing cells | Where |
|---|---|---|
| Hodges-Lehmann with the Wilcoxon interval | 10 | ar1/one_host, all four schedules, 1.03 and 1.04, and 1.05 for web_agentic and llm_serving |
| Bootstrap of the ratio of medians, percentile | 8 | ar1/one_host, all four schedules, 1.03 and 1.04 |
| Bootstrap of the ratio of medians, BCa | 7 | ar1/one_host, 1.03 in all four schedules, 1.04 in three |
| Batch means, blocks of 4 | 1 | gpu_kernels/ar1/one_host/1.03 |
| Batch means, blocks of 5 | 1 | python_native/ar1/one_host/1.03 |
| Batch means, blocks of 10 | 0 | power below block 6 (above) |

Every failing challenger cell is under AR(1) on one host. Block 10 has
less power than block 6 and leaves no interval below 20 rounds. The
bootstrap and BCa intervals have more power than batch means in
blocks of 6 (about 76% and 72% KEEP at 1.10, one host, against 61%) and fail
the rule in cells where it passes; an interval that does not pass
cannot be primary. The harness no longer runs any of them, and T1.8 deleted
`src/perfproof/stats/bootstrap.py` and the Hodges-Lehmann code.

**The T1.6 audit found two gaps.** The audit of PR #33 ran
held-out scenarios from the audit ranges on the batch-means interval of
blocks of 6 and found it failing the acceptance rule in two conditions
inside the ranges: AR(1) with `rho` 0.6 to 0.7 and little other noise,
and 4 to 5% outliers of 2.8 to 4.5 times with a CV under 1% on
schedules with three blocks or fewer. The auditor's counts are in that
audit, not in this repository; the scenarios that failed are the
exposed scenarios of `tests/calibration/exposed.py`, and the
procedure "without gate" below is the audited one, so the table of
failing cells after the acceptance table is the committed evidence of
the gaps.

**Acceptance, T1.8.** The primary of "Speedup and interval", with the
diagnostic gate of "Assumptions", run as the production
`perfproof.stats.sequential.run_looks` does. A cell passes if the
one-sided 95% Clopper-Pearson upper bound on its error rate is at most
2.5%, which at `R` = 4000 is a count of at most 83. Largest error count
over the cells of each group, and the number of cells failing:

| Seed | Scenarios | Procedure | Cells | Failing | Largest count (bound) |
|---|---|---|---|---|---|
| 3598798392042499920 (of record) | development | batch means 6 with gate (primary) | 560 | 0 | 47 (1.50%), `gpu_kernels/lognormal_cv5/cross_host/1.03` |
| 3598798392042499920 (of record) | development | batch means 6 without gate | 560 | 0 | 59 (1.83%), `gpu_kernels/ar1/one_host/1.03` |
| 3598798392042499920 (of record) | exposed | batch means 6 with gate (primary) | 784 | 0 | 62 (1.91%), `gpu_kernels/A20261056-15/cross_host/1.03` |
| 3598798392042499920 (of record) | exposed | batch means 6 without gate | 784 | 19 | 130 (3.75%), `gpu_kernels/RHO0.7/one_host/1.03` |
| 3078527063959977686 (fresh) | development | batch means 6 with gate (primary) | 560 | 0 | 43 (1.38%), `gpu_kernels/variance_step/one_host/1.03` |
| 3078527063959977686 (fresh) | development | batch means 6 without gate | 560 | 0 | 75 (2.27%), `gpu_kernels/ar1/one_host/1.03` |
| 3078527063959977686 (fresh) | exposed | batch means 6 with gate (primary) | 784 | 0 | 67 (2.05%), `gpu_kernels/A20261056-15/cross_host/1.03` |
| 3078527063959977686 (fresh) | exposed | batch means 6 without gate | 784 | 18 | 122 (3.54%), `gpu_kernels/RHO0.7/one_host/1.03` |

Every one of the 1344 cells of the primary passes at both seeds. The
closest cell to the limit is the same at both seeds, cross host
in an exposed scenario: `gpu_kernels/A20261056-15/cross_host/1.03`, 62 of 4000 at the seed of
record and 67 at the fresh seed, against 83. The session offsets
dilute the autocorrelation the gate can see there. No failing cell was rerun
and no cell was dropped. Per cell, at the seed of record, error count
and, in brackets, the upper bound (false KEEP at 0.95, 1.00 and 1.03,
false REJECT at 1.04, 1.05, 1.10 and 1.20):

| Schedule | Scenario | Host | 0.95 | 1.00 | 1.03 | 1.04 | 1.05 | 1.10 | 1.20 |
|---|---|---|---|---|---|---|---|---|---|
| python_native | lognormal_cv1 | one_host | 0 (0.07%) | 0 (0.07%) | 30 (1.02%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| python_native | lognormal_cv5 | one_host | 0 (0.07%) | 1 (0.12%) | 17 (0.64%) | 8 (0.36%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| python_native | lognormal_cv15 | one_host | 0 (0.07%) | 10 (0.42%) | 17 (0.64%) | 15 (0.58%) | 9 (0.39%) | 0 (0.07%) | 0 (0.07%) |
| python_native | student_t_df3 | one_host | 0 (0.07%) | 0 (0.07%) | 29 (0.99%) | 3 (0.19%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) |
| python_native | bimodal | one_host | 0 (0.07%) | 0 (0.07%) | 26 (0.90%) | 4 (0.23%) | 5 (0.26%) | 0 (0.07%) | 0 (0.07%) |
| python_native | outliers | one_host | 0 (0.07%) | 0 (0.07%) | 8 (0.36%) | 4 (0.23%) | 2 (0.16%) | 0 (0.07%) | 0 (0.07%) |
| python_native | drift | one_host | 0 (0.07%) | 0 (0.07%) | 32 (1.07%) | 6 (0.30%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) |
| python_native | variance_step | one_host | 0 (0.07%) | 1 (0.12%) | 26 (0.90%) | 9 (0.39%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| python_native | ar1 | one_host | 0 (0.07%) | 0 (0.07%) | 7 (0.33%) | 3 (0.19%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) |
| python_native | order_effect | one_host | 0 (0.07%) | 0 (0.07%) | 30 (1.02%) | 2 (0.16%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| python_native | session_offset | one_host | 0 (0.07%) | 2 (0.16%) | 31 (1.04%) | 4 (0.23%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) |
| python_native | A20261052-00 | one_host | 0 (0.07%) | 0 (0.07%) | 2 (0.16%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| python_native | A20261052-04 | one_host | 1 (0.12%) | 1 (0.12%) | 7 (0.33%) | 3 (0.19%) | 4 (0.23%) | 2 (0.16%) | 0 (0.07%) |
| python_native | A20261052-14 | one_host | 0 (0.07%) | 5 (0.26%) | 10 (0.42%) | 9 (0.39%) | 4 (0.23%) | 1 (0.12%) | 0 (0.07%) |
| python_native | A20261053-04 | one_host | 0 (0.07%) | 0 (0.07%) | 2 (0.16%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| python_native | A20261054-08 | one_host | 0 (0.07%) | 0 (0.07%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| python_native | A20261054-13 | one_host | 0 (0.07%) | 0 (0.07%) | 3 (0.19%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| python_native | A20261055-17 | one_host | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| python_native | A20261056-02 | one_host | 0 (0.07%) | 0 (0.07%) | 4 (0.23%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| python_native | A20261056-15 | one_host | 0 (0.07%) | 0 (0.07%) | 7 (0.33%) | 1 (0.12%) | 2 (0.16%) | 0 (0.07%) | 0 (0.07%) |
| python_native | RHO0.6 | one_host | 0 (0.07%) | 0 (0.07%) | 11 (0.45%) | 1 (0.12%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) |
| python_native | RHO0.7 | one_host | 0 (0.07%) | 0 (0.07%) | 2 (0.16%) | 1 (0.12%) | 3 (0.19%) | 0 (0.07%) | 0 (0.07%) |
| python_native | A00-outliers-only | one_host | 0 (0.07%) | 0 (0.07%) | 2 (0.16%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| python_native | A00-outliers-5pct-x5-cv1 | one_host | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| python_native | A00-outliers-1pct-x5-cv1 | one_host | 0 (0.07%) | 0 (0.07%) | 7 (0.33%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| python_native | lognormal_cv1 | cross_host | 0 (0.07%) | 3 (0.19%) | 32 (1.07%) | 19 (0.70%) | 12 (0.49%) | 2 (0.16%) | 0 (0.07%) |
| python_native | lognormal_cv5 | cross_host | 0 (0.07%) | 9 (0.39%) | 20 (0.73%) | 18 (0.67%) | 16 (0.61%) | 1 (0.12%) | 0 (0.07%) |
| python_native | lognormal_cv15 | cross_host | 1 (0.12%) | 10 (0.42%) | 25 (0.87%) | 15 (0.58%) | 12 (0.49%) | 4 (0.23%) | 0 (0.07%) |
| python_native | student_t_df3 | cross_host | 1 (0.12%) | 2 (0.16%) | 36 (1.19%) | 12 (0.49%) | 9 (0.39%) | 1 (0.12%) | 0 (0.07%) |
| python_native | bimodal | cross_host | 1 (0.12%) | 3 (0.19%) | 32 (1.07%) | 16 (0.61%) | 7 (0.33%) | 0 (0.07%) | 0 (0.07%) |
| python_native | outliers | cross_host | 0 (0.07%) | 4 (0.23%) | 19 (0.70%) | 18 (0.67%) | 6 (0.30%) | 0 (0.07%) | 0 (0.07%) |
| python_native | variance_step | cross_host | 0 (0.07%) | 5 (0.26%) | 37 (1.22%) | 17 (0.64%) | 17 (0.64%) | 0 (0.07%) | 0 (0.07%) |
| python_native | ar1 | cross_host | 0 (0.07%) | 8 (0.36%) | 32 (1.07%) | 13 (0.52%) | 12 (0.49%) | 1 (0.12%) | 0 (0.07%) |
| python_native | session_offset | cross_host | 0 (0.07%) | 9 (0.39%) | 18 (0.67%) | 15 (0.58%) | 5 (0.26%) | 1 (0.12%) | 0 (0.07%) |
| python_native | A20261052-00 | cross_host | 0 (0.07%) | 3 (0.19%) | 13 (0.52%) | 9 (0.39%) | 7 (0.33%) | 1 (0.12%) | 0 (0.07%) |
| python_native | A20261052-04 | cross_host | 6 (0.30%) | 11 (0.45%) | 37 (1.22%) | 32 (1.07%) | 22 (0.78%) | 10 (0.42%) | 3 (0.19%) |
| python_native | A20261052-14 | cross_host | 2 (0.16%) | 8 (0.36%) | 34 (1.13%) | 25 (0.87%) | 18 (0.67%) | 2 (0.16%) | 0 (0.07%) |
| python_native | A20261053-04 | cross_host | 0 (0.07%) | 0 (0.07%) | 6 (0.30%) | 11 (0.45%) | 6 (0.30%) | 2 (0.16%) | 0 (0.07%) |
| python_native | A20261054-08 | cross_host | 0 (0.07%) | 0 (0.07%) | 9 (0.39%) | 4 (0.23%) | 5 (0.26%) | 1 (0.12%) | 0 (0.07%) |
| python_native | A20261054-13 | cross_host | 0 (0.07%) | 4 (0.23%) | 35 (1.16%) | 15 (0.58%) | 16 (0.61%) | 0 (0.07%) | 0 (0.07%) |
| python_native | A20261055-17 | cross_host | 0 (0.07%) | 7 (0.33%) | 20 (0.73%) | 14 (0.55%) | 8 (0.36%) | 3 (0.19%) | 0 (0.07%) |
| python_native | A20261056-02 | cross_host | 0 (0.07%) | 2 (0.16%) | 33 (1.10%) | 12 (0.49%) | 12 (0.49%) | 0 (0.07%) | 0 (0.07%) |
| python_native | A20261056-15 | cross_host | 1 (0.12%) | 12 (0.49%) | 29 (0.99%) | 22 (0.78%) | 12 (0.49%) | 0 (0.07%) | 0 (0.07%) |
| python_native | RHO0.6 | cross_host | 2 (0.16%) | 7 (0.33%) | 37 (1.22%) | 26 (0.90%) | 8 (0.36%) | 0 (0.07%) | 0 (0.07%) |
| python_native | RHO0.7 | cross_host | 1 (0.12%) | 14 (0.55%) | 36 (1.19%) | 25 (0.87%) | 18 (0.67%) | 0 (0.07%) | 0 (0.07%) |
| python_native | A00-outliers-only | cross_host | 1 (0.12%) | 4 (0.23%) | 5 (0.26%) | 6 (0.30%) | 4 (0.23%) | 2 (0.16%) | 0 (0.07%) |
| python_native | A00-outliers-5pct-x5-cv1 | cross_host | 0 (0.07%) | 1 (0.12%) | 4 (0.23%) | 4 (0.23%) | 7 (0.33%) | 0 (0.07%) | 0 (0.07%) |
| python_native | A00-outliers-1pct-x5-cv1 | cross_host | 0 (0.07%) | 1 (0.12%) | 18 (0.67%) | 9 (0.39%) | 9 (0.39%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | lognormal_cv1 | one_host | 0 (0.07%) | 0 (0.07%) | 30 (1.02%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | lognormal_cv5 | one_host | 0 (0.07%) | 0 (0.07%) | 31 (1.04%) | 5 (0.26%) | 2 (0.16%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | lognormal_cv15 | one_host | 1 (0.12%) | 8 (0.36%) | 21 (0.76%) | 22 (0.78%) | 8 (0.36%) | 1 (0.12%) | 0 (0.07%) |
| web_agentic | student_t_df3 | one_host | 0 (0.07%) | 0 (0.07%) | 21 (0.76%) | 2 (0.16%) | 2 (0.16%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | bimodal | one_host | 0 (0.07%) | 2 (0.16%) | 28 (0.96%) | 12 (0.49%) | 8 (0.36%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | outliers | one_host | 0 (0.07%) | 0 (0.07%) | 9 (0.39%) | 2 (0.16%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | drift | one_host | 0 (0.07%) | 0 (0.07%) | 31 (1.04%) | 5 (0.26%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | variance_step | one_host | 0 (0.07%) | 2 (0.16%) | 26 (0.90%) | 5 (0.26%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | ar1 | one_host | 0 (0.07%) | 3 (0.19%) | 15 (0.58%) | 7 (0.33%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | order_effect | one_host | 0 (0.07%) | 0 (0.07%) | 21 (0.76%) | 8 (0.36%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | session_offset | one_host | 0 (0.07%) | 1 (0.12%) | 27 (0.93%) | 15 (0.58%) | 4 (0.23%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | A20261052-00 | one_host | 0 (0.07%) | 0 (0.07%) | 2 (0.16%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | A20261052-04 | one_host | 3 (0.19%) | 3 (0.19%) | 10 (0.42%) | 10 (0.42%) | 3 (0.19%) | 3 (0.19%) | 0 (0.07%) |
| web_agentic | A20261052-14 | one_host | 1 (0.12%) | 4 (0.23%) | 10 (0.42%) | 5 (0.26%) | 4 (0.23%) | 1 (0.12%) | 0 (0.07%) |
| web_agentic | A20261053-04 | one_host | 0 (0.07%) | 0 (0.07%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | A20261054-08 | one_host | 0 (0.07%) | 0 (0.07%) | 3 (0.19%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | A20261054-13 | one_host | 0 (0.07%) | 0 (0.07%) | 13 (0.52%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | A20261055-17 | one_host | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | A20261056-02 | one_host | 0 (0.07%) | 0 (0.07%) | 9 (0.39%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | A20261056-15 | one_host | 0 (0.07%) | 0 (0.07%) | 5 (0.26%) | 1 (0.12%) | 3 (0.19%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | RHO0.6 | one_host | 0 (0.07%) | 1 (0.12%) | 7 (0.33%) | 3 (0.19%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | RHO0.7 | one_host | 0 (0.07%) | 2 (0.16%) | 5 (0.26%) | 1 (0.12%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | A00-outliers-only | one_host | 0 (0.07%) | 0 (0.07%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | A00-outliers-5pct-x5-cv1 | one_host | 0 (0.07%) | 0 (0.07%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | A00-outliers-1pct-x5-cv1 | one_host | 0 (0.07%) | 0 (0.07%) | 25 (0.87%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | lognormal_cv1 | cross_host | 1 (0.12%) | 7 (0.33%) | 31 (1.04%) | 13 (0.52%) | 15 (0.58%) | 1 (0.12%) | 0 (0.07%) |
| web_agentic | lognormal_cv5 | cross_host | 1 (0.12%) | 9 (0.39%) | 28 (0.96%) | 22 (0.78%) | 13 (0.52%) | 1 (0.12%) | 0 (0.07%) |
| web_agentic | lognormal_cv15 | cross_host | 1 (0.12%) | 11 (0.45%) | 27 (0.93%) | 15 (0.58%) | 24 (0.84%) | 3 (0.19%) | 0 (0.07%) |
| web_agentic | student_t_df3 | cross_host | 0 (0.07%) | 9 (0.39%) | 26 (0.90%) | 14 (0.55%) | 10 (0.42%) | 1 (0.12%) | 0 (0.07%) |
| web_agentic | bimodal | cross_host | 2 (0.16%) | 17 (0.64%) | 25 (0.87%) | 19 (0.70%) | 13 (0.52%) | 3 (0.19%) | 0 (0.07%) |
| web_agentic | outliers | cross_host | 0 (0.07%) | 9 (0.39%) | 22 (0.78%) | 10 (0.42%) | 9 (0.39%) | 3 (0.19%) | 0 (0.07%) |
| web_agentic | variance_step | cross_host | 1 (0.12%) | 13 (0.52%) | 22 (0.78%) | 23 (0.81%) | 13 (0.52%) | 1 (0.12%) | 0 (0.07%) |
| web_agentic | ar1 | cross_host | 2 (0.16%) | 7 (0.33%) | 30 (1.02%) | 17 (0.64%) | 14 (0.55%) | 1 (0.12%) | 0 (0.07%) |
| web_agentic | session_offset | cross_host | 2 (0.16%) | 3 (0.19%) | 26 (0.90%) | 13 (0.52%) | 13 (0.52%) | 2 (0.16%) | 0 (0.07%) |
| web_agentic | A20261052-00 | cross_host | 1 (0.12%) | 4 (0.23%) | 14 (0.55%) | 4 (0.23%) | 7 (0.33%) | 1 (0.12%) | 0 (0.07%) |
| web_agentic | A20261052-04 | cross_host | 5 (0.26%) | 22 (0.78%) | 26 (0.90%) | 25 (0.87%) | 25 (0.87%) | 7 (0.33%) | 2 (0.16%) |
| web_agentic | A20261052-14 | cross_host | 7 (0.33%) | 18 (0.67%) | 24 (0.84%) | 18 (0.67%) | 29 (0.99%) | 6 (0.30%) | 1 (0.12%) |
| web_agentic | A20261053-04 | cross_host | 0 (0.07%) | 6 (0.30%) | 8 (0.36%) | 8 (0.36%) | 3 (0.19%) | 2 (0.16%) | 1 (0.12%) |
| web_agentic | A20261054-08 | cross_host | 1 (0.12%) | 8 (0.36%) | 4 (0.23%) | 5 (0.26%) | 2 (0.16%) | 1 (0.12%) | 0 (0.07%) |
| web_agentic | A20261054-13 | cross_host | 1 (0.12%) | 10 (0.42%) | 31 (1.04%) | 25 (0.87%) | 13 (0.52%) | 1 (0.12%) | 0 (0.07%) |
| web_agentic | A20261055-17 | cross_host | 4 (0.23%) | 6 (0.30%) | 26 (0.90%) | 15 (0.58%) | 9 (0.39%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | A20261056-02 | cross_host | 1 (0.12%) | 3 (0.19%) | 22 (0.78%) | 24 (0.84%) | 6 (0.30%) | 1 (0.12%) | 0 (0.07%) |
| web_agentic | A20261056-15 | cross_host | 3 (0.19%) | 16 (0.61%) | 25 (0.87%) | 19 (0.70%) | 21 (0.76%) | 0 (0.07%) | 0 (0.07%) |
| web_agentic | RHO0.6 | cross_host | 2 (0.16%) | 7 (0.33%) | 31 (1.04%) | 19 (0.70%) | 8 (0.36%) | 3 (0.19%) | 0 (0.07%) |
| web_agentic | RHO0.7 | cross_host | 3 (0.19%) | 10 (0.42%) | 32 (1.07%) | 29 (0.99%) | 18 (0.67%) | 3 (0.19%) | 0 (0.07%) |
| web_agentic | A00-outliers-only | cross_host | 1 (0.12%) | 5 (0.26%) | 8 (0.36%) | 7 (0.33%) | 11 (0.45%) | 3 (0.19%) | 0 (0.07%) |
| web_agentic | A00-outliers-5pct-x5-cv1 | cross_host | 1 (0.12%) | 2 (0.16%) | 9 (0.39%) | 6 (0.30%) | 3 (0.19%) | 1 (0.12%) | 0 (0.07%) |
| web_agentic | A00-outliers-1pct-x5-cv1 | cross_host | 0 (0.07%) | 6 (0.30%) | 19 (0.70%) | 13 (0.52%) | 10 (0.42%) | 0 (0.07%) | 0 (0.07%) |
| llm_serving | lognormal_cv1 | one_host | 0 (0.07%) | 0 (0.07%) | 29 (0.99%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| llm_serving | lognormal_cv5 | one_host | 0 (0.07%) | 0 (0.07%) | 24 (0.84%) | 18 (0.67%) | 5 (0.26%) | 0 (0.07%) | 0 (0.07%) |
| llm_serving | lognormal_cv15 | one_host | 3 (0.19%) | 10 (0.42%) | 26 (0.90%) | 17 (0.64%) | 14 (0.55%) | 6 (0.30%) | 1 (0.12%) |
| llm_serving | student_t_df3 | one_host | 0 (0.07%) | 0 (0.07%) | 32 (1.07%) | 7 (0.33%) | 2 (0.16%) | 0 (0.07%) | 0 (0.07%) |
| llm_serving | bimodal | one_host | 0 (0.07%) | 4 (0.23%) | 26 (0.90%) | 9 (0.39%) | 7 (0.33%) | 0 (0.07%) | 0 (0.07%) |
| llm_serving | outliers | one_host | 0 (0.07%) | 0 (0.07%) | 12 (0.49%) | 7 (0.33%) | 2 (0.16%) | 0 (0.07%) | 0 (0.07%) |
| llm_serving | drift | one_host | 0 (0.07%) | 1 (0.12%) | 20 (0.73%) | 9 (0.39%) | 5 (0.26%) | 0 (0.07%) | 0 (0.07%) |
| llm_serving | variance_step | one_host | 0 (0.07%) | 4 (0.23%) | 26 (0.90%) | 8 (0.36%) | 7 (0.33%) | 0 (0.07%) | 0 (0.07%) |
| llm_serving | ar1 | one_host | 0 (0.07%) | 3 (0.19%) | 10 (0.42%) | 1 (0.12%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) |
| llm_serving | order_effect | one_host | 0 (0.07%) | 1 (0.12%) | 21 (0.76%) | 13 (0.52%) | 6 (0.30%) | 0 (0.07%) | 0 (0.07%) |
| llm_serving | session_offset | one_host | 0 (0.07%) | 1 (0.12%) | 20 (0.73%) | 12 (0.49%) | 2 (0.16%) | 0 (0.07%) | 0 (0.07%) |
| llm_serving | A20261052-00 | one_host | 0 (0.07%) | 0 (0.07%) | 2 (0.16%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| llm_serving | A20261052-04 | one_host | 4 (0.23%) | 6 (0.30%) | 9 (0.39%) | 4 (0.23%) | 5 (0.26%) | 0 (0.07%) | 1 (0.12%) |
| llm_serving | A20261052-14 | one_host | 0 (0.07%) | 1 (0.12%) | 8 (0.36%) | 12 (0.49%) | 6 (0.30%) | 4 (0.23%) | 0 (0.07%) |
| llm_serving | A20261053-04 | one_host | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 1 (0.12%) |
| llm_serving | A20261054-08 | one_host | 0 (0.07%) | 1 (0.12%) | 4 (0.23%) | 0 (0.07%) | 0 (0.07%) | 1 (0.12%) | 0 (0.07%) |
| llm_serving | A20261054-13 | one_host | 0 (0.07%) | 0 (0.07%) | 11 (0.45%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| llm_serving | A20261055-17 | one_host | 0 (0.07%) | 0 (0.07%) | 4 (0.23%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| llm_serving | A20261056-02 | one_host | 0 (0.07%) | 0 (0.07%) | 7 (0.33%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| llm_serving | A20261056-15 | one_host | 0 (0.07%) | 7 (0.33%) | 6 (0.30%) | 3 (0.19%) | 5 (0.26%) | 1 (0.12%) | 0 (0.07%) |
| llm_serving | RHO0.6 | one_host | 0 (0.07%) | 2 (0.16%) | 10 (0.42%) | 8 (0.36%) | 4 (0.23%) | 0 (0.07%) | 0 (0.07%) |
| llm_serving | RHO0.7 | one_host | 0 (0.07%) | 3 (0.19%) | 4 (0.23%) | 4 (0.23%) | 4 (0.23%) | 0 (0.07%) | 0 (0.07%) |
| llm_serving | A00-outliers-only | one_host | 0 (0.07%) | 0 (0.07%) | 4 (0.23%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| llm_serving | A00-outliers-5pct-x5-cv1 | one_host | 0 (0.07%) | 2 (0.16%) | 9 (0.39%) | 1 (0.12%) | 1 (0.12%) | 0 (0.07%) | 1 (0.12%) |
| llm_serving | A00-outliers-1pct-x5-cv1 | one_host | 0 (0.07%) | 0 (0.07%) | 15 (0.58%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| llm_serving | lognormal_cv1 | cross_host | 5 (0.26%) | 8 (0.36%) | 27 (0.93%) | 20 (0.73%) | 16 (0.61%) | 3 (0.19%) | 0 (0.07%) |
| llm_serving | lognormal_cv5 | cross_host | 3 (0.19%) | 9 (0.39%) | 21 (0.76%) | 14 (0.55%) | 16 (0.61%) | 2 (0.16%) | 0 (0.07%) |
| llm_serving | lognormal_cv15 | cross_host | 5 (0.26%) | 17 (0.64%) | 18 (0.67%) | 18 (0.67%) | 9 (0.39%) | 5 (0.26%) | 0 (0.07%) |
| llm_serving | student_t_df3 | cross_host | 4 (0.23%) | 9 (0.39%) | 29 (0.99%) | 18 (0.67%) | 20 (0.73%) | 3 (0.19%) | 0 (0.07%) |
| llm_serving | bimodal | cross_host | 5 (0.26%) | 17 (0.64%) | 34 (1.13%) | 27 (0.93%) | 15 (0.58%) | 5 (0.26%) | 0 (0.07%) |
| llm_serving | outliers | cross_host | 5 (0.26%) | 8 (0.36%) | 21 (0.76%) | 16 (0.61%) | 25 (0.87%) | 6 (0.30%) | 1 (0.12%) |
| llm_serving | variance_step | cross_host | 3 (0.19%) | 11 (0.45%) | 32 (1.07%) | 18 (0.67%) | 16 (0.61%) | 7 (0.33%) | 1 (0.12%) |
| llm_serving | ar1 | cross_host | 4 (0.23%) | 11 (0.45%) | 30 (1.02%) | 25 (0.87%) | 18 (0.67%) | 5 (0.26%) | 0 (0.07%) |
| llm_serving | session_offset | cross_host | 3 (0.19%) | 5 (0.26%) | 21 (0.76%) | 17 (0.64%) | 13 (0.52%) | 2 (0.16%) | 0 (0.07%) |
| llm_serving | A20261052-00 | cross_host | 3 (0.19%) | 9 (0.39%) | 11 (0.45%) | 10 (0.42%) | 9 (0.39%) | 7 (0.33%) | 2 (0.16%) |
| llm_serving | A20261052-04 | cross_host | 4 (0.23%) | 10 (0.42%) | 20 (0.73%) | 22 (0.78%) | 20 (0.73%) | 20 (0.73%) | 4 (0.23%) |
| llm_serving | A20261052-14 | cross_host | 7 (0.33%) | 14 (0.55%) | 25 (0.87%) | 18 (0.67%) | 16 (0.61%) | 10 (0.42%) | 0 (0.07%) |
| llm_serving | A20261053-04 | cross_host | 1 (0.12%) | 1 (0.12%) | 14 (0.55%) | 6 (0.30%) | 9 (0.39%) | 7 (0.33%) | 1 (0.12%) |
| llm_serving | A20261054-08 | cross_host | 4 (0.23%) | 5 (0.26%) | 8 (0.36%) | 8 (0.36%) | 5 (0.26%) | 6 (0.30%) | 1 (0.12%) |
| llm_serving | A20261054-13 | cross_host | 2 (0.16%) | 16 (0.61%) | 25 (0.87%) | 20 (0.73%) | 15 (0.58%) | 5 (0.26%) | 0 (0.07%) |
| llm_serving | A20261055-17 | cross_host | 2 (0.16%) | 13 (0.52%) | 12 (0.49%) | 18 (0.67%) | 17 (0.64%) | 5 (0.26%) | 1 (0.12%) |
| llm_serving | A20261056-02 | cross_host | 1 (0.12%) | 14 (0.55%) | 18 (0.67%) | 27 (0.93%) | 17 (0.64%) | 4 (0.23%) | 0 (0.07%) |
| llm_serving | A20261056-15 | cross_host | 0 (0.07%) | 15 (0.58%) | 31 (1.04%) | 23 (0.81%) | 18 (0.67%) | 5 (0.26%) | 0 (0.07%) |
| llm_serving | RHO0.6 | cross_host | 2 (0.16%) | 18 (0.67%) | 28 (0.96%) | 24 (0.84%) | 11 (0.45%) | 4 (0.23%) | 0 (0.07%) |
| llm_serving | RHO0.7 | cross_host | 6 (0.30%) | 12 (0.49%) | 20 (0.73%) | 14 (0.55%) | 13 (0.52%) | 4 (0.23%) | 0 (0.07%) |
| llm_serving | A00-outliers-only | cross_host | 2 (0.16%) | 7 (0.33%) | 7 (0.33%) | 7 (0.33%) | 6 (0.30%) | 4 (0.23%) | 0 (0.07%) |
| llm_serving | A00-outliers-5pct-x5-cv1 | cross_host | 3 (0.19%) | 6 (0.30%) | 4 (0.23%) | 8 (0.36%) | 3 (0.19%) | 3 (0.19%) | 2 (0.16%) |
| llm_serving | A00-outliers-1pct-x5-cv1 | cross_host | 2 (0.16%) | 3 (0.19%) | 17 (0.64%) | 13 (0.52%) | 11 (0.45%) | 1 (0.12%) | 0 (0.07%) |
| gpu_kernels | lognormal_cv1 | one_host | 0 (0.07%) | 0 (0.07%) | 37 (1.22%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | lognormal_cv5 | one_host | 0 (0.07%) | 0 (0.07%) | 23 (0.81%) | 4 (0.23%) | 2 (0.16%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | lognormal_cv15 | one_host | 0 (0.07%) | 2 (0.16%) | 41 (1.33%) | 20 (0.73%) | 9 (0.39%) | 1 (0.12%) | 0 (0.07%) |
| gpu_kernels | student_t_df3 | one_host | 0 (0.07%) | 0 (0.07%) | 25 (0.87%) | 0 (0.07%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | bimodal | one_host | 0 (0.07%) | 2 (0.16%) | 37 (1.22%) | 15 (0.58%) | 4 (0.23%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | outliers | one_host | 0 (0.07%) | 0 (0.07%) | 7 (0.33%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | drift | one_host | 0 (0.07%) | 0 (0.07%) | 23 (0.81%) | 9 (0.39%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | variance_step | one_host | 0 (0.07%) | 1 (0.12%) | 38 (1.24%) | 7 (0.33%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | ar1 | one_host | 0 (0.07%) | 0 (0.07%) | 23 (0.81%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | order_effect | one_host | 0 (0.07%) | 0 (0.07%) | 45 (1.44%) | 4 (0.23%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | session_offset | one_host | 0 (0.07%) | 0 (0.07%) | 45 (1.44%) | 6 (0.30%) | 2 (0.16%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | A20261052-00 | one_host | 0 (0.07%) | 1 (0.12%) | 8 (0.36%) | 4 (0.23%) | 6 (0.30%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | A20261052-04 | one_host | 1 (0.12%) | 0 (0.07%) | 14 (0.55%) | 12 (0.49%) | 11 (0.45%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | A20261052-14 | one_host | 0 (0.07%) | 2 (0.16%) | 4 (0.23%) | 5 (0.26%) | 3 (0.19%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | A20261053-04 | one_host | 0 (0.07%) | 0 (0.07%) | 6 (0.30%) | 2 (0.16%) | 2 (0.16%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | A20261054-08 | one_host | 0 (0.07%) | 0 (0.07%) | 7 (0.33%) | 1 (0.12%) | 1 (0.12%) | 1 (0.12%) | 0 (0.07%) |
| gpu_kernels | A20261054-13 | one_host | 0 (0.07%) | 0 (0.07%) | 6 (0.30%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | A20261055-17 | one_host | 0 (0.07%) | 1 (0.12%) | 6 (0.30%) | 2 (0.16%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | A20261056-02 | one_host | 0 (0.07%) | 0 (0.07%) | 29 (0.99%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | A20261056-15 | one_host | 0 (0.07%) | 1 (0.12%) | 34 (1.13%) | 7 (0.33%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | RHO0.6 | one_host | 0 (0.07%) | 0 (0.07%) | 31 (1.04%) | 6 (0.30%) | 2 (0.16%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | RHO0.7 | one_host | 0 (0.07%) | 1 (0.12%) | 45 (1.44%) | 17 (0.64%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | A00-outliers-only | one_host | 1 (0.12%) | 0 (0.07%) | 9 (0.39%) | 5 (0.26%) | 1 (0.12%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | A00-outliers-5pct-x5-cv1 | one_host | 0 (0.07%) | 0 (0.07%) | 9 (0.39%) | 6 (0.30%) | 2 (0.16%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | A00-outliers-1pct-x5-cv1 | one_host | 0 (0.07%) | 0 (0.07%) | 19 (0.70%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | lognormal_cv1 | cross_host | 0 (0.07%) | 3 (0.19%) | 37 (1.22%) | 16 (0.61%) | 9 (0.39%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | lognormal_cv5 | cross_host | 0 (0.07%) | 4 (0.23%) | 47 (1.50%) | 11 (0.45%) | 6 (0.30%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | lognormal_cv15 | cross_host | 1 (0.12%) | 8 (0.36%) | 28 (0.96%) | 20 (0.73%) | 11 (0.45%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | student_t_df3 | cross_host | 0 (0.07%) | 4 (0.23%) | 32 (1.07%) | 15 (0.58%) | 6 (0.30%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | bimodal | cross_host | 0 (0.07%) | 8 (0.36%) | 36 (1.19%) | 23 (0.81%) | 13 (0.52%) | 1 (0.12%) | 0 (0.07%) |
| gpu_kernels | outliers | cross_host | 0 (0.07%) | 8 (0.36%) | 27 (0.93%) | 12 (0.49%) | 11 (0.45%) | 2 (0.16%) | 0 (0.07%) |
| gpu_kernels | variance_step | cross_host | 0 (0.07%) | 4 (0.23%) | 29 (0.99%) | 14 (0.55%) | 8 (0.36%) | 1 (0.12%) | 0 (0.07%) |
| gpu_kernels | ar1 | cross_host | 0 (0.07%) | 4 (0.23%) | 44 (1.41%) | 20 (0.73%) | 4 (0.23%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | session_offset | cross_host | 1 (0.12%) | 5 (0.26%) | 36 (1.19%) | 20 (0.73%) | 7 (0.33%) | 2 (0.16%) | 0 (0.07%) |
| gpu_kernels | A20261052-00 | cross_host | 0 (0.07%) | 1 (0.12%) | 15 (0.58%) | 12 (0.49%) | 7 (0.33%) | 2 (0.16%) | 0 (0.07%) |
| gpu_kernels | A20261052-04 | cross_host | 4 (0.23%) | 11 (0.45%) | 39 (1.27%) | 22 (0.78%) | 20 (0.73%) | 4 (0.23%) | 3 (0.19%) |
| gpu_kernels | A20261052-14 | cross_host | 3 (0.19%) | 20 (0.73%) | 39 (1.27%) | 24 (0.84%) | 16 (0.61%) | 4 (0.23%) | 0 (0.07%) |
| gpu_kernels | A20261053-04 | cross_host | 0 (0.07%) | 3 (0.19%) | 26 (0.90%) | 9 (0.39%) | 8 (0.36%) | 1 (0.12%) | 1 (0.12%) |
| gpu_kernels | A20261054-08 | cross_host | 2 (0.16%) | 8 (0.36%) | 11 (0.45%) | 10 (0.42%) | 6 (0.30%) | 1 (0.12%) | 1 (0.12%) |
| gpu_kernels | A20261054-13 | cross_host | 1 (0.12%) | 4 (0.23%) | 31 (1.04%) | 7 (0.33%) | 9 (0.39%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | A20261055-17 | cross_host | 1 (0.12%) | 3 (0.19%) | 24 (0.84%) | 16 (0.61%) | 4 (0.23%) | 1 (0.12%) | 0 (0.07%) |
| gpu_kernels | A20261056-02 | cross_host | 0 (0.07%) | 3 (0.19%) | 20 (0.73%) | 8 (0.36%) | 10 (0.42%) | 1 (0.12%) | 0 (0.07%) |
| gpu_kernels | A20261056-15 | cross_host | 0 (0.07%) | 5 (0.26%) | 62 (1.91%) | 29 (0.99%) | 11 (0.45%) | 2 (0.16%) | 0 (0.07%) |
| gpu_kernels | RHO0.6 | cross_host | 0 (0.07%) | 11 (0.45%) | 45 (1.44%) | 27 (0.93%) | 12 (0.49%) | 1 (0.12%) | 0 (0.07%) |
| gpu_kernels | RHO0.7 | cross_host | 0 (0.07%) | 5 (0.26%) | 56 (1.75%) | 34 (1.13%) | 19 (0.70%) | 2 (0.16%) | 0 (0.07%) |
| gpu_kernels | A00-outliers-only | cross_host | 0 (0.07%) | 8 (0.36%) | 20 (0.73%) | 11 (0.45%) | 8 (0.36%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | A00-outliers-5pct-x5-cv1 | cross_host | 3 (0.19%) | 8 (0.36%) | 17 (0.64%) | 13 (0.52%) | 10 (0.42%) | 0 (0.07%) | 0 (0.07%) |
| gpu_kernels | A00-outliers-1pct-x5-cv1 | cross_host | 0 (0.07%) | 3 (0.19%) | 19 (0.70%) | 10 (0.42%) | 6 (0.30%) | 1 (0.12%) | 0 (0.07%) |

**What the gate fixes.** The same cells without the gate fail the
acceptance rule in 19 cells at the seed of record and 18 at the
fresh seed, all of them in the exposed scenarios, none in the
development scenarios. Cells that fail without the gate at either
seed, with the error count at each seed without and with it:

| Cell | Without gate, seed of record | With gate | Without gate, fresh | With gate |
|---|---|---|---|---|
| `gpu_kernels/A20261052-04/cross_host/1.03` | 84 **FAIL** | 39 | 89 **FAIL** | 29 |
| `gpu_kernels/A20261052-04/one_host/1.03` | 103 **FAIL** | 14 | 114 **FAIL** | 16 |
| `gpu_kernels/A20261052-04/one_host/1.04` | 80 | 12 | 84 **FAIL** | 18 |
| `gpu_kernels/A20261054-13/one_host/1.03` | 97 **FAIL** | 6 | 108 **FAIL** | 8 |
| `gpu_kernels/A20261056-02/one_host/1.03` | 70 | 29 | 92 **FAIL** | 30 |
| `gpu_kernels/A20261056-15/one_host/1.03` | 112 **FAIL** | 34 | 97 **FAIL** | 23 |
| `gpu_kernels/RHO0.6/one_host/1.03` | 102 **FAIL** | 31 | 88 **FAIL** | 32 |
| `gpu_kernels/RHO0.7/one_host/1.03` | 130 **FAIL** | 45 | 122 **FAIL** | 52 |
| `llm_serving/A00-outliers-only/one_host/1.00` | 90 **FAIL** | 0 | 81 | 0 |
| `llm_serving/A00-outliers-only/one_host/1.03` | 106 **FAIL** | 4 | 117 **FAIL** | 1 |
| `llm_serving/A00-outliers-only/one_host/1.04` | 91 **FAIL** | 0 | 102 **FAIL** | 0 |
| `llm_serving/A00-outliers-only/one_host/1.05` | 72 | 0 | 103 **FAIL** | 2 |
| `llm_serving/A00-outliers-only/one_host/1.10` | 95 **FAIL** | 0 | 64 | 0 |
| `llm_serving/A20261052-00/one_host/1.00` | 88 **FAIL** | 0 | 94 **FAIL** | 0 |
| `llm_serving/A20261052-00/one_host/1.03` | 109 **FAIL** | 2 | 108 **FAIL** | 9 |
| `llm_serving/A20261052-00/one_host/1.04` | 95 **FAIL** | 0 | 89 **FAIL** | 0 |
| `llm_serving/A20261052-00/one_host/1.05` | 88 **FAIL** | 0 | 80 | 0 |
| `llm_serving/A20261054-08/one_host/1.03` | 116 **FAIL** | 4 | 110 **FAIL** | 0 |
| `llm_serving/A20261054-08/one_host/1.04` | 91 **FAIL** | 0 | 70 | 0 |
| `llm_serving/A20261054-08/one_host/1.05` | 76 | 0 | 84 **FAIL** | 0 |
| `llm_serving/A20261055-17/one_host/1.03` | 98 **FAIL** | 4 | 85 **FAIL** | 6 |
| `python_native/A20261052-04/one_host/1.03` | 93 **FAIL** | 7 | 68 | 3 |
| `python_native/RHO0.7/one_host/1.03` | 93 **FAIL** | 2 | 97 **FAIL** | 2 |

Without the gate the failures are the two conditions of the T1.6
audit: the AR(1) cells on the GPU kernels schedule (one host, and cross
host in the draw with `rho` 0.68), the outlier cells on the LLM serving
schedule, and two cells on the Python and native schedule. The gate
brings every one of them below the limit at both seeds, and in most by
a wide margin.

**What the gate costs.** Power, and the share of runs that end
INCONCLUSIVE, in the development scenarios at the seed of record, for
the same simulated rounds with and without the gate (mean over the
scenarios of each row):

| Schedule | Host | Scenarios | KEEP at 1.05, without to with gate | KEEP at 1.10, without to with gate | INCONCLUSIVE at 1.04, without to with gate |
|---|---|---|---|---|---|
| python_native | one_host | AR(1) only | 8.2% to 1.0% | 79.8% to 4.0% | 96.2% to 99.5% |
| python_native | one_host | the other 10 | 19.8% to 17.9% | 80.3% to 71.1% | 88.2% to 89.6% |
| python_native | cross_host | AR(1) only | 1.8% to 1.6% | 16.4% to 14.4% | 97.9% to 98.1% |
| python_native | cross_host | the other 8 | 2.0% to 1.9% | 15.6% to 14.6% | 98.2% to 98.4% |
| web_agentic | one_host | AR(1) only | 3.9% to 1.2% | 32.1% to 4.2% | 97.3% to 99.3% |
| web_agentic | one_host | the other 10 | 13.4% to 10.7% | 51.1% to 42.1% | 94.2% to 95.0% |
| web_agentic | cross_host | AR(1) only | 1.4% to 1.2% | 6.8% to 5.9% | 98.5% to 98.7% |
| web_agentic | cross_host | the other 8 | 1.5% to 1.4% | 6.0% to 5.5% | 98.4% to 98.6% |
| llm_serving | one_host | AR(1) only | 2.0% to 0.5% | 8.1% to 2.1% | 98.4% to 99.6% |
| llm_serving | one_host | the other 10 | 4.7% to 3.9% | 20.1% to 15.6% | 97.5% to 98.0% |
| llm_serving | cross_host | AR(1) only | 1.1% to 0.9% | 2.6% to 2.1% | 98.4% to 98.7% |
| llm_serving | cross_host | the other 8 | 1.2% to 1.0% | 2.8% to 2.4% | 98.4% to 98.7% |
| gpu_kernels | one_host | AR(1) only | 22.2% to 16.8% | 99.8% to 99.8% | 92.8% to 96.3% |
| gpu_kernels | one_host | the other 10 | 39.1% to 38.4% | 92.4% to 92.1% | 83.4% to 83.6% |
| gpu_kernels | cross_host | AR(1) only | 4.5% to 4.2% | 50.0% to 49.3% | 97.4% to 97.5% |
| gpu_kernels | cross_host | the other 8 | 4.0% to 3.8% | 48.0% to 47.4% | 97.6% to 97.8% |

The loss is concentrated where the gate acts: AR(1) with `rho` 0.5 on
one host loses nearly all its power outside the GPU kernels schedule,
because a lag-1 autocorrelation above 0.1 can't be decided there before
120 observations; the other scenarios lose a part of their power
because a lag-1 above 0.1 is common in independent noise at these
lengths. Cross host the gate costs little, because the session offsets
leave little autocorrelation to see. These are the costs the acceptance
rule does not judge.

**One-version skew stays a stated limit.** No remedy has been shown
to pass: this run has no skew scenario, and no committed probe or
result covers one, so the limit under "Assumptions" stays as stated,
uncalibrated.

**What this leaves.** Calibrated under the conditions above only; the
gate is blunt where the autocorrelation is hidden, as cross host, and
its cost is power. The procedure is still `CALIBRATED = False` in
`bench/compare.py`, and T1.7 audits it on held-out scenarios drawn at
audit time.

## What the record contains

- Outcome, reason, the gate or rule that decided it, speedup estimate,
  interval, rounds, looks, and whether the comparison crossed hosts
- Procedure, look schedule, and calibration status for the target class
- Spec hash, perfproof version, and the command executed in each round
- Noise of each version and of the paired ratio; lag-1
  autocorrelation of the log ratio after subtracting each order's
  mean; skewness of the log ratio
- Each value's resolution `q`, where it came from (clock, count,
  declared or printed), and the widening it added; `coarse_resolution`
  when it applied
- Gates and their results, including recorded differences between arms
- Machine fingerprint: CPU, cores, frequency policy, load, power source,
  thermal state, GPU, driver and clocks when present
- Warnings, for example running on battery or a busy machine
- Paths to the raw samples and any profiles
