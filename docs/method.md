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
| `runtime` | The runtime from outside the tree that each arm actually runs: the interpreter and its version, followed through launchers such as `uv run`, `env` and shebangs; for a native executable built in the tree, the libraries it links from outside the tree, with their hashes; a binary committed in the tree that differs between arms. Node, Java, Go and the C and C++ compilers are recognised (below). Anything that can't be identified is `not_available`, including tools known to start other interpreters (tox, nox, poetry and the like), a shell (the program it runs is not the one perfproof launched), and builders and toolchains such as `cargo`, `make`, `mvn`, `gradle`, `dotnet` and `ruby`. It is `failed` only when both arms are identified and differ. Waivable only when the spec allows runtime changes. An untracked executable inside the tree is treated as a build of the code under test. Not seen: libraries loaded at run time with `dlopen`, and an interpreter started by a program the gate identified as Python or native |
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
  `corepack prepare`, `install`, `up`, `use` and `pack`; `go run` and
  `go install` on a `module/path@version`, `go get` and `go mod
  download`; and the install forms of `npm`, `pnpm`, `yarn` and `bun` (`install`, `i`,
  `ci`, `add`, `update`, `up`, `upgrade`, `create`, and a bare `yarn`).
  Options in front of the subcommand are skipped, so `npm --silent
  install` is the install form, and `yarn --version`, `-v`, `--help`
  and `corepack enable` are not on the list. The same commands are
  fine in `setup`. Build tools that fetch dependencies on their own
  (`cargo`, `go test`, `go run` and `go build` of the project's own
  packages, `mvn`, `gradle`) are not on the list because
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

**Node, Java and Go runtimes.** A command whose program name resolves
(symlinks followed) to `node` or `nodejs` is a Node runtime, to `java` a
Java runtime, wherever the file lives, in the tree or outside it, and to
a native `go` (an ELF or Mach-O file, not a script) with the subcommand
`run`, `test`, `build` or `vet` a Go toolchain. A shell is unknown,
because the program it runs is not the one perfproof launched.

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

*Go* is asked, not derived, and only for `go run`, `go test`, `go build`
and `go vet`, the subcommands that compile the code under test (`go
install`, `go tool`, `go generate`, `go get`, `go mod` and the rest
start other programs or fetch code, so they are unknown). The gate runs
the command's own `go list -f '{{.Root}}' runtime` in the benchmark's
working directory and environment, with stdin closed, a timeout and
output capped at 4096 characters. `go env` and `go version` are not
asked, because they do not read `go.mod`: with `go 1.99` in it and
`GOTOOLCHAIN=local`, `go env GOVERSION` and `go version` printed the
installed version and `go list` and `go test` failed with "go.mod
requires go >= 1.99" (the integration test
`test_go_env_and_go_version_do_not_read_the_go_mod_but_go_list_does`,
run in the `golang:1.25` image). `go list`
switches toolchain as `go test` does, when `go.mod`'s `go` or
`toolchain` line asks for another one and `GOTOOLCHAIN` allows it, so
the root it prints is that of the toolchain that will run. That
toolchain's own `bin/go` is then asked for `go env -json`. The record
carries the real path of that `go` and the version, platform and the
settings that change generated code (`CGO_ENABLED`, `GOAMD64`,
`GOARM64`, `GOEXPERIMENT`, `GOFLAGS`, the empty ones left out) followed
by `; sha256 <digest>` of that file. A probe that cannot start, times
out, exits non-zero (including a `go.mod` that asks for a toolchain
that cannot be had), prints something that is not the report, or names
a `bin/go` that is not a native file on this machine makes the runtime
unknown. A switch to a toolchain that is not in the module cache is
downloaded by the probe, as the first run of the benchmark would,
within the probe's time limit (30 s in `compare`; a download that
needs longer makes the runtime unknown)
(`studies/go/toolchain-switch.txt`, run with a limit of 600 s: with `go 1.26.0` in `go.mod`, the
installed 1.25.14 and `GOTOOLCHAIN=auto`, the record names the 1.26.0
toolchain in the module cache; with `go 1.25.0` it names the installed
one). A `go` that is a script, such as a version manager's shim or a
launcher that runs Go in a container, is a shell, so unknown, and a
KEEP is withheld.

*A built Go program* is a native executable built in the tree, which
the gate already compared by the libraries it links from outside the
tree. Go also stamps the toolchain's version into every executable it
builds (the header `\xff Go buildinf:`, read without running the file,
Go 1.18 and later), so two executables built by different toolchains
are a difference, `<path> go toolchain`, and one built by the same is
named in the passed detail. An executable that is not a Go build, or
whose header cannot be read, is compared as before. `go run` and `go
test` start the toolchain, which compiles (from the build cache after
the first time) and starts the program as a child, so their wall time
includes the `go` command's own start-up: 25.2 ms at the median for an
empty program, against 1.3 ms for its built binary, 30 starts each in
one container (`studies/go/results.txt`, from `studies/go/run.sh`).
Build in `setup` and time the executable (`go build -o`, or `go test -c`
and the test binary) unless the command line is what the user waits for.

*C and C++* programs are identified two ways. A command whose program
resolves to a native `cc`, `c++`, `gcc`, `g++`, `clang` or `clang++`
(versioned and target-prefixed names such as `gcc-14` and
`aarch64-linux-gnu-g++` too) is asked, as the benchmark would run it,
for `--version` and `-dumpmachine`, with stdin closed, a timeout and
output capped at 4096 characters. The record is the first line of the
banner, the target and the SHA-256 of the compiler's file; when the
banner has an `InstalledDir` line (clang prints one, and Apple's
`/usr/bin/clang` is a shim that runs the compiler in the Command Line
Tools) the file hashed is the one of that name in that directory. A
driver that is a script (ccache's wrapper, a distribution's alternatives
script) is a shell, so unknown, as are `make`, `cmake` and `ninja`,
which start other programs. A native executable built in the tree,
which the gate already compared by the libraries it links from outside
the tree, is also compared by what it records of its toolchain, read
from the file without running it (`perfproof.bench.nativebuildinfo`):
an ELF file's `.comment` section lists the compilers that contributed
object files (`GCC: (GNU) 14.4.0`; a clang build on Debian lists
`Debian clang version 19.1.7 (3+b1)` and the gcc line of the C runtime
objects), and a Mach-O file's `LC_BUILD_VERSION` names the SDK and the
linker (`ld 1230.1`) but no compiler (`tests/fixtures/native/SOURCE.md`,
`tests/bench/test_lock_native.py`). Two executables that record
different toolchains are a difference, `<path> native toolchain`; one
that records none (stripped, not a C or C++ build, an ELF file that is
32-bit or big-endian, a fat Mach-O) adds nothing when neither does and
is unverified when only one does. The same records are in the passed
detail. Not seen: compiler flags (`-O` level, `-march`), which the
executable does not record, so two versions compiled with different
flags in `setup` (not checked) pass this gate; ccache and the compiler
a `make` runs; the Mach-O compiler version.

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

Not seen for Node, Java and Go: `NODE_OPTIONS` is recorded with its value
and `JAVA_TOOL_OPTIONS` by name only, and neither is gated (the
Node probe runs with `NODE_OPTIONS` removed, so a preload can neither
forge nor spoil the report); Node options set in an `.npmrc`
(`node-options`) are not recorded anywhere; the JDK's `libjvm` and
class libraries are covered by the version banner only, not hashed,
and a `java` shim that chooses between JDK homes is recorded as the
shim, which passes; a Node program that spawns
another runtime is recorded under its own. For Go, the toolchain's
standard library and tools are covered by the version and the hash of
`go` only, not hashed file by file; `go.work` and `vendor/` are not
recorded (`go.sum` is, as a dependency lockfile); the C compiler that
cgo calls is not recorded; and a `go` the gate cannot read makes the
runtime unknown.

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

### Warm-up (Node)

Warmup executions (above) do not warm V8. Each execution is a new
process, and the optimised code one process produced is not reused by
the next, so a discarded execution leaves the next one just as cold. A Node
benchmark has to warm up inside its own process and report only the
steady window, with the `reported` metric: `wall` and `cpu` include the
start-up and the warm-up they should leave out (a loader or `npx`
adds to every start; how much was not measured here).

The in-process timing is not flat. One workload (a web-framework
app serving 60,000 requests in process, Node v24.13.0; the source and
commit are in `tests/fixtures/node/SOURCE.md`), 12 processes of 60
batches of 500 iterations. The Mac was busy with other jobs; the load
was not recorded. The driver and the raw output are in
`tests/fixtures/node/warmup/` (`tests/fixtures/node/SOURCE.md` has the
commands), and `tests/profilers/test_node_warmup.py` recomputes every
number in these tables from that output:

| Iterations done | Median time per iteration, over the 12 processes | Against the steady value |
|---|---|---|
| 0 to 500 | 131.6 µs | 7.5x |
| 500 to 1,000 | 39.2 µs | 2.2x |
| 5,500 to 6,000 | 36.2 µs | 2.1x |
| 6,000 to 6,500 | 18.3 µs | 1.05x |
| 9,000 to 9,500 | 18.0 µs | 1.03x |
| steady (batches 40 to 59) | 17.5 µs | 1x |

The curve is not monotone, and it is not smooth after the drop. On the
12 per-batch medians, 21 of the 48 batches from the 13th on were more
than 10% above the steady value and the worst was 1.46x; on the 576 raw
batches (12 processes) it is 301 of 576 and 2.79x (the cause was not
isolated: collections and the load on the machine are the candidates).
One batch is not a measurement; a window is. A window of 10,000 iterations started after N iterations had, over the
same 12 processes (CV is the population standard deviation over the
mean):

| Warm-up | Mean per iteration | Spread over processes (CV) |
|---|---|---|
| 0 | 35.8 µs | 6.2% |
| 2,000 | 27.8 µs | 4.9% |
| 4,000 | 23.9 µs | 4.2% |
| 6,000 | 21.6 µs | 7.4% |
| 10,000 | 21.7 µs | 9.0% |
| 15,000 | 21.1 µs | 9.1% |

And as the `reported` value of 15 separate processes each, 10,000
measured iterations: no warm-up 36.3 µs (CV 7.1%), 12,000 warm-up
iterations 21.4 µs (CV 8.8%). The warm-up did not reduce the spread on
that loaded machine; it removed a level (and a transient) that would
otherwise be in every value. That is the case for it, and the
arithmetic is an inference, not a measurement: if the transient adds a
fixed 0.15 s to a window whose steady work is 0.21 s, a real 10% saving
in the steady work reads as a 5.8% one, and a change that only shortens
the transient (less code to compile) reads as a gain that production,
which runs steady, never sees.

**The rules.** The constants below (the tolerance floor 2%, the factor
3 on the standard error, the tolerance cap 4%, the margin 8, 0.2 s, 12 processes, 20-batch
windows, the recording lengths) are *Provisional: chosen by reasoning
and by synthetic models, not calibrated on real data*. Real
quiet-machine data does not exist yet: the one measured curve was taken
on a busy Mac. The estimator's tolerance and the margin were each
chosen by a rule written before the runs
(`studies/node-warmup/PREREGISTRATION.md`, committed before them). The
margin rule picked nothing, and margin 8 is the closest, not a pick
("The margin" below). The robust estimator (window medians, a
MAD-based `se`, the 4% cap) and its pass rule were written down before
its runs; the 4% is one value, not chosen from a set.

- The benchmark runs a **fixed** number of warm-up iterations, then
  times a fixed number, the same in both versions. Never "until the
  timing is stable": that ends at a different point in each run, and
  puts the measured window at a different JIT state in the two
  versions, which is a bias the interleaving cannot cancel.
- Choose the counts on **both** trees, the baseline and the candidate,
  each from its own recording: 12 fresh processes, each printing the
  time per batch of 500 iterations for R batches (start with R = 320,
  160,000 iterations; the recording has to be about 10 times the
  settle point or longer).
  - `W(s)` is the mean, over the processes, of the median of the 20
    batches that start at batch `s`.
  - The **tail windows** are `W(R - 20)`, `W(R - 40)` and `W(R - 60)`.
    Their mean is the reference `ref`.
  - The tolerance is `tol = min(max(0.02, 3 se), 0.04)`. `se` is a
    robust relative standard error of a tail window value, taken from
    the same 12 processes: divide each process's three tail window
    values by their own mean, take the absolute deviations of those 36
    ratios from their median over the processes in the same window,
    take the median of those deviations, multiply it by 1.4826 and
    divide by the square root of 12. (Dividing by the process's own
    mean keeps a process that is simply slower from inflating it.) It
    is 2% on a quiet machine, grows with the noise of the batches, and
    stops at 4%, so a few huge batches cannot widen it.
  - The tail windows **agree** when the largest is at most
    `(1 + tol)` times the smallest.
  - The tree's `N` is the smallest `s` for which `W(s)`, `W(s + 20)`
    and `W(s + 40)` each satisfy `|W / ref - 1| <= tol`; in iterations,
    `500 s`. Nothing else is checked: not the windows between `s + 40`
    and the measured window, and not whether a window is below `ref`.
  - When the tail windows do not agree, record again with fresh
    processes at twice the length (640, then 1,280 batches, 640,000
    iterations at most). If the longest recording still does not
    agree, that tree's warm-up is **unknown**.
  - `WARM` is 8 times the larger of the two trees' `N`, in iterations,
    the same in both versions; `MEASURE` is the same for both, at
    least 10,000 (20 batches).
  - **Unknown is a note, not a gate.** If either tree's warm-up is
    unknown, `WARM` is 160,000 iterations (or 8 times the other tree's
    `N` if that is larger), and the comparison proceeds normally:
    perfproof's verdict stands, a KEEP included. Nothing fails over
    this, and an execution is never failed for it. The skill reports,
    as a note, that the warm-up was not pinned down and that a long
    warm-up was used, and says what would pin it (a longer recording or
    a quieter machine). A noisy tail widens `tol` up to 4%; past that
    the tail windows can disagree at every length and the tree is
    unknown, which is common above noise 0.5 ("Why the tolerance is
    robust and capped" in `docs/method.md`). In the model 51% of pairs
    are unknown at noise 1.0, 5% at 0.5 and none at 0.1. Cost: on a busy
    machine more pairs take the long warm-up, so their executions take
    about 3.6 s each (435 s for 60 rounds) instead of about 1.3 s, and
    comparisons take longer. Measured with unknown pairs allowed to KEEP
    (`out/unknown-keep-w160000.txt`, seed 20261051, 2,000 comparisons
    per row, sharp and exponential grid, clean and with outliers q 2%
    x10 and x30, margin 8): all pairs combined, the worst row's CP95
    upper bound is at most 1.74% at noise 0.1 to 1.0, and 2.09% at 1.5
    and 1.74% at 2.0, as measured and not tuned. Among the pairs that
    ended unknown, pooled over the grid, it is at most 1.34% at every
    noise run. 640,000 was run on the same rows and seed
    (`out/unknown-keep-w640000.txt`, at most 1.68% up to noise 1.0);
    160,000 is used because it meets the same bar at a quarter of the
    cost (13.4 s per execution).
- **Why the tail.** A reference found by comparing windows at n, 2n
  and 4n is fooled by the flat stretch before the drop. For a tree
  that settles twice or more later than the committed one, the windows
  at 500, 1,000 and 2,000 iterations all fall before the drop and
  agree, so `N` came out as 500.
- **Why the tolerance is robust and capped.** At a fixed 2%, the tail
  windows of one tree agree on a 320-batch recording 98% of the time at
  noise 0.25 and 23% of the time at 1.0, and a pair ends
  unknown 8.25% of the time at noise 0.5 and 71% at 1.0
  (`out/estimator-unknown.txt`, seed 20261022, 4,000 pairs per row,
  sharp pair (1, 1)). A tolerance of 3 se from window means, `se` the
  sample standard error, fixed that: unknown in 0.52% of pairs at noise
  1.0 in that run. But it has no upper limit, and one batch 30 times
  the usual in each process inside the tail windows made it 51% and `N`
  0 batches, so `WARM` 0; random outliers in 1% to 2% of batches,
  30 times the usual, put `N` at 1 batch or less in 27.5% to 32.0% of
  recordings (`out/outlier-probe.txt`, part 1, noise 0.25, 400
  recordings per row; `outlier_probe.py`). The rule above uses window
  medians, a MAD-based `se` and a cap at 4%; on those same recordings
  its `N` stayed at 9 to 10 batches and `tol` at 2.0%. The cost is in
  the unknown rate: with the cap, `tol` cannot follow the noise past
  4%. Per pair of trees (`out/estimator-unknown-robust.txt`, same
  seed, pairs and sharp pair; "first recording" is per tree, 320
  batches; times are the model's, start-up excluded, margin 8; `WARM`
  and the execution columns are at 640,000 for an unknown pair, the
  rule run when the table was made. The rule now uses 160,000 (3.6 s
  per execution), so an unknown pair's costs are lower than shown):

  | Noise | Median `tol` | First recording agrees (a tree) | Pair unknown | `N` median batches (5th, 95th), known trees | Choosing both trees (s) | Median `WARM` | Mean execution (s) | Choosing + 122 executions (s) |
  |---|---|---|---|---|---|---|---|---|
  | 0.10 | 2.0% | 100.0% | 0.00% | 7 (6, 9) | 82 | 28,000 | 0.98 | 202 |
  | 0.25 | 2.0% | 85.1% | 0.68% | 10 (8, 13) | 114 | 40,000 | 1.36 | 280 |
  | 0.50 | 3.5% | 70.7% | 5.13% | 10 (8, 17) | 156 | 44,000 | 2.09 | 411 |
  | 1.00 | 4.0% | 32.3% | 51.25% | 13 (9, 85) | 334 | 640,000 | 8.63 | 1,387 |
  | 1.50 | 4.0% | 14.2% | 84.97% | 24 (9, 175) | 449 | 640,000 | 12.67 | 1,995 |
  | 2.00 | 4.0% | 8.8% | 94.97% | 52 (10, 260) | 496 | 640,000 | 13.88 | 2,189 |

  **At the committed noise, 1.0, about half the pairs end unknown, and
  they are measured like any other.** That is the price of the cap,
  and it is not rare. An unknown pair costs about 435 s for 122
  executions (3.6 s each, `WARM` 160,000; 13.4 s each and about 1,600 s
  at 640,000) where a known one costs about 150 s to 280 s. The cap of 4% is provisional. Removing
  it (round 7, "Earlier rules") makes about 5.8% of pairs unknown at
  noise 1.0 but gives a false KEEP CP95 of 16.22% on the exponential
  tails; a 15% cap was as often unknown and its error rows were not
  run.
- **The margin.** The margin runs below used the scaled-tolerance
  estimator (`tol = max(0.02, 3 se)` from window means, the rule
  before the robust one; `out/rule-sweep-scaled.txt` and the others
  named `scaled`). Only margin 8 was run with the robust estimator,
  at the end of this bullet. Margins 2, 3, 4, 5, 6 and 8 were run
  through production `run_looks` (calibrated, bar 3%, 60 rounds) on the sharp
  pairs and the exponential tails, which are the claimed coverage.
  The rule for the choice, fixed before the runs and then widened once
  (the amendment, with its reason, is in `PREREGISTRATION.md`): the
  smallest margin whose CP95 upper bound is at most 2.5% on every row
  of the sharp pairs and exponential tails, at every noise 0.1, 0.25,
  0.5, 1.0, 1.5 and 2.0, counting every comparison. It was first run
  over 2, 3 and 4, where none qualified (worst 29.9%, 18.9% and 14.4%
  at noise 2.0, `out/rule-sweep-scaled-m234.txt`); the candidates were
  then widened to 5, 6 and 8, a decision made after seeing that result.
  **No margin qualifies.** Worst CP95 upper bound over those rows, in
  percent (`out/margin-pick.txt`, seed 20261022, 4,000 comparisons per
  row; a false KEEP of a true ratio of 1.00 or 1.03, or a false REJECT
  of a true 1.06):

  | Noise | Margin 2 | Margin 3 | Margin 4 | Margin 5 | Margin 6 | Margin 8 |
  |---|---|---|---|---|---|---|
  | 0.10 | 2.05 | 1.13 | 1.27 | 1.10 | 1.24 | 1.27 |
  | 0.25 | 1.75 | 1.36 | 1.33 | 1.33 | 1.38 | 1.16 |
  | 0.50 | 5.18 | 2.02 | 1.16 | 1.41 | 1.22 | 1.07 |
  | 1.00 | 20.61 | 11.25 | 6.86 | 4.12 | 2.81 | 1.69 |
  | 1.50 | 23.18 | 13.50 | 7.28 | 5.08 | 2.95 | 1.88 |
  | 2.00 | 29.44 | 19.34 | 14.40 | 10.58 | 9.44 | 6.00 |

  The sharp pairs alone are at most 1.5% for every margin at every
  noise (false KEEP) and at most 0.4% (false REJECT). Every failure is
  on the exponential tails, never on the sharp pairs, but not always on
  the same ones: at margins 2 to 5 slow tails fail too (tau 10,000 to
  40,000 at margin 2), and only at margins 6 and 8 are the failures
  confined to fast tails (tau 2,000 to 5,000). At noise 1.0 and above
  a fast tail is found settled too early, because a 20-batch window
  dilutes it and the tolerance is wide. **Margin 8 is used because it
  is the closest, not because the rule chose it.** On the sweep's grid
  of tails it meets the 2.5% limit at every noise through 1.5 (1.88% at
  most), and at noise 2.0 one row of that grid exceeds it (an
  exponential tail with a = 0.5 and tau = 5,000: a false KEEP of a true
  ratio of 1.03 5.4% of the time, CP95 upper 6.0%). That one row is the
  edge of a band, not an isolated failure; see "What the rule covers".
  With margin 8 and that estimator, `WARM` is about 44,000 iterations
  at the committed noise (the median over pairs), against a drop at
  5,667. A larger margin was not run.

  *Margin 8 with the robust estimator.* Fixed before the runs
  (`PREREGISTRATION.md`, round 6): the robust estimator replaces the
  scaled one if, at margin 8 and noise 0.1, 0.25, 0.5 and 1.0, every
  row of the six sharp pairs and ten exponential tails of the sweep
  has a CP95 upper bound of at most 2.5%, with no outliers and with
  outliers of q = 0.5%, 1% and 2% at m = 10 and 30 (in the recordings
  and the executions of both trees). It passed: the worst bound over
  every row, false KEEP and false REJECT, sharp and exponential, was
  1.80% (`out/robust-sweep-a.txt`, `out/robust-sweep-b.txt`, seed
  20261025, 2,000 comparisons per row; `out/robust-verdict.txt` has
  each noise and outlier setting). The fine grid of `exp_grid.py`
  (a of 0.5, 0.6 and 0.75, tau 2,500 to 30,000; 33 pairs, 99 cells
  per noise, no outliers, `out/exp-grid-robust.txt`, seed 20261026,
  2,000 per row) has no cell over 2.5% at noise 0.1 (worst 1.63%),
  0.25 (1.80%), 0.5 (1.63%) or 1.0 (1.63%), and its 30 claimed cells
  are at most 1.91%. Those rows counted a pair that ended
  unknown as no KEEP, which the rule no longer does: the model's rows
  run every pair, and with unknown pairs allowed to KEEP the end-to-end
  bounds are in the unknown bullet above (worst row at most 1.74% up
  to noise 1.0, `out/unknown-keep-w160000.txt`). Counted end to end
  with unknown pairs withheld, the worst bound was 1.15% with outliers
  at noise 1.0 and 1.68% at any noise. Within the known pairs alone it
  is higher: up to 2.42% (noise
  1.0, q 2% x10, exponential a 0.5, tau 2,000, 15 errors in 950 known
  pairs, `out/robust-sweep-b.txt`), and up to 1.79% clean at noise
  1.0.
- Measure at least 0.2 s per execution, in one timed block. That
  keeps the clock's step negligible; it is not a variance argument (the
  10,000-iteration windows above, about 0.21 s, had a CV of 7% to 9%),
  and a longer window was not measured.
- Print the per-iteration mean in seconds at full precision:
  `seconds.toPrecision(12)`. `toFixed(9)` prints `0.000000000` for a
  fast enough workload (a failed execution, hence REJECT) and keeps four
  significant digits at 2 µs; `JSON.stringify` prints 1.0 as `1`, which
  `coarse_resolution` stops. `toPrecision` keeps trailing zeros, so the
  step is known, and an exponent (`2.00000000000e-7`) is valid JSON.
- Re-choose the counts when the workload or the Node version changes.
  The candidate's own recording is made too, because a candidate that
  settles later is the case at risk.

On the committed curve the rule cannot be evaluated: the recording has
60 batches, so the three tail windows would include the drop (the
tail windows read 20.3, 21.7 and 35.8 µs, a spread of 76%,
`out/rule-on-committed.txt`). For that curve extended by the model,
`N` is a median of 13 batches (6,500 iterations; 5th to 95th
percentile 9 to 85) at noise 1.0 in the trees whose tail agrees, and
`WARM` a median of 640,000 when the table was made, because 51% of
pairs end unknown (`out/estimator-unknown-robust.txt`; model output,
not a measurement); an unknown pair now uses 160,000.

**Cost.** An execution takes about `WARM x t_warm + MEASURE x t_steady`
plus start-up (not measured here), where `t_warm` is the mean time per
iteration over the warm-up, higher than `t_steady`. All the per-process
times below are for this one workload on this one Mac
(`studies/node-warmup/out/cost.txt`, 12 processes, start-up excluded,
steady value used past the 30,000 recorded iterations). `WARM` 44,000
and `MEASURE` 10,000 take 1.27 s (1.06 s of warm-up). On a tree that
really settles 2 and 3 times later (the committed mean curve stretched
along the iteration axis, `WARM` 88,000 and 132,000) an execution takes
2.28 s and 3.30 s. The unknown path, `WARM` 160,000, takes 3.6 s per
execution and 435 s for 60 rounds (at 640,000, which was tried and not
used, 13.4 s and 1,606 s, `out/cost.txt`). On a busy machine more pairs
take this path, so comparisons take longer. With the robust
estimator it fires in 51.25% of pairs at the committed noise (4,000
pairs, sharp pair (1, 1), one seed, no bound), 5.13% at noise 0.5 and
85% to 95% at 1.5 to 2.0 (table above). It is not always the longest
execution: a slow tail needs a larger `WARM`. At margin 8 the
claimed exponential tails (a = 0.75, tau 40,000 is the slowest) take,
over noise 0.1, 0.25, 0.5 and 1.0 in `out/exp-grid-robust.txt` (same
cost model; 95th percentile of `WARM`), at most 18.75 s at the median
`WARM` (noise 0.25) and 24.77 s at the 95th percentile (noise 1.0).
60 rounds of 18.75 s executions take 2,250 s, above the default
`max_seconds` of 1,800. These are the model's figures. The unknown
path's 3.6 s is the longest execution only where no slow tail is
present.

Choosing the counts costs, per tree, 12 processes times the recording
time: one process takes 3.4 s for 320 batches, 6.7 s for 640 and 13.2 s
for 1,280. Expected over trials, both trees, with the robust
estimator (the model's mean of the retries): 82 s at noise 0.1, 114 s
at 0.25, 156 s at 0.5 and 334 s at 1.0 (41 s per tree when the first
recording is enough, 121 s when it takes two, 280 s when it takes all
three). A
round is two executions, and `warmup` executions run for each version
first. So, as *Provisional* limits: `timeout_seconds` at least 3 times
the longest execution measured on either tree, whichever path chose
its `WARM` (74.3 s for the 24.77 s above), and `budget.max_seconds` at
least `1.2 x 2 x (warmup + max_rounds) x` that execution time (3,626 s
for 24.77 s with one `warmup` and 60 rounds). A
timeout is a failed execution ("Failed executions" above; `runner.py`,
the `timed_out` branch of the execution). In the baseline it stops
the comparison with no verdict (`broken_baseline`, in `compare.py`'s
`_guard`). In the candidate, perfproof runs the baseline once more
(`_recheck_baseline`): if that passes, the candidate is rejected on
`correctness` with `timed_out` recorded; if the baseline fails too,
there is no verdict. A timeout shorter than the slower arm needs is
therefore a stop or a wrong REJECT, never a KEEP.

**Stated limits.** `studies/node-warmup/` holds the models and their
output (`run_all.sh` regenerates them). They are synthetic: built from
the one committed curve (a sharp drop at 5,667 iterations, sample sd
749, 12 processes; `out/drop-stats.txt` derives both from the
definition of the drop and a process spread of 0.042 in excess of batch
noise), or from an exponential tail `1 + a exp(-k / tau)` with the
committed noise, production `run_looks` (calibrated, bar 3%), 60
rounds, an arm's drop scaled by f, batches independent, settling in
iterations not seconds, 12 independent processes per recording. Noise
scales the committed batch deviations about their means: 1.0 is the
committed curve, measured on a busy Mac; 0.1 to 0.5 are quieter, 1.5
and 2.0 noisier. Error rates are false KEEP of a true ratio at or below
the bar (1.00 and 1.03) and false REJECT of a true 6% gain; "CP95
upper" is the upper end of a 95% binomial bound, and "at most 2.5%"
includes 2.5%.

*What the rule covers.* Up to noise 1.0 (the committed curve and
quieter): the sharp-drop pairs (the drop at 1, 2 or 3 times the
committed position for either tree) and exponential tails on a grid:
a of 0.5 and 0.75, tau of 2,000, 5,000, 10,000, 20,000 and 40,000,
each against a tail with tau 300, with and without heavy-tailed
batches (outliers of 0.5% to 2% of batches at 10 or 30 times the
usual), at margin 8 and the robust estimator. The CP95 upper bounds
are at most 1.80% (false KEEP) and 1.21% (false REJECT) over every
row (`out/robust-verdict.txt`, `robust_sweep.py`, seed 20261025, 2,000
comparisons per row). The fine grid of `exp_grid.py` (seed 20261026,
2,000 per row: a of 0.5, 0.6 and 0.75, tau at 2,500 to 8,000 in steps
and at 15,000 and 30,000, `out/exp-grid-robust.txt`) has no cell over
2.5% CP95 at noise 0.1, 0.25, 0.5 or 1.0 (worst 1.80%, 99 cells per
noise). Nothing else is claimed: not the grids' gaps beyond what was
run, not tails with a below 0.5 (a = 0.3 or 0.4 at noise 0.5 has one
cell over, 4.63% CP95), not tau above 40,000, not noise above 1.0 with
this estimator, and not a loaded host's other signatures (outliers
in runs, not a sustained slowdown). The sharp-pair and exponential
claim at noise 1.0 holds with about half the pairs unknown, which now
take the long warm-up and may KEEP ("Why the tolerance is robust and
capped").

*Heavy-tailed batches.* A loaded host produces batches many times the
usual. Against the mean-based scaled estimator (the previous rule),
end to end at margin 8 and noise 1.0, outliers in 0.5% to 2% of
batches gave a false KEEP of a true ratio of 1.03 of 8.00% to 8.25%
on the exponential tail a = 0.5, tau 5,000 (CP95 up to 9.33%) and 3.40%
(CP95 4.14%) on the sharp pair (3, 1) with q = 1% at 30 times
(`out/outlier-probe.txt`, part 2, 2,000 comparisons per row, seed
20261024). With the robust estimator the same rows gave at most 0.85%
(CP95 1.27%). Outliers were multiplied into each batch independently,
in the recordings (not the first batch) and the executions of both
trees; nothing was run on outliers that cluster in time.

*Above noise 1.0 the exponential cells over the limit are a stated
limit of the scaled estimator, not re-run with the robust one.* They
sit at and between the sweep's grid points, and which ones depends on
the noise (cells where the false KEEP of a true ratio
of 1.03 has a CP95 upper bound over 2.5%, `exp_grid.py`):
- noise 1.25: one cell over (a = 0.5, tau 2,500: 2.25%, CP95 2.68%);
- noise 1.5: five cells over, 2.20% to 3.05% (CP95 up to 3.54%): a =
  0.5 at tau 3,000 to 4,500 and a = 0.6 at tau 2,500;
- noise 1.75: eleven cells over, worst 4.33% (CP95 4.89%): a = 0.5 at
  tau 2,500 to 5,500 (5,000 is a sweep grid point) and a = 0.6 at tau
  2,500 to 4,000;
- noise 2.0: 18 cells over (17 of 99 fine cells and the sweep grid's
  a = 0.5, tau 5,000), worst 6.03% (CP95 6.68%): a = 0.5 at tau 2,500
  to 7,000, a = 0.6 at 3,000 to 5,500 and a = 0.75 at 2,500 to 3,500;
  the sweep's own row a = 0.5, tau 5,000 gave 5.4% in the sweep and
  5.55% here.
Fast tails (tau 2,500 to 7,000) are the ones at risk, for the reason
given under "The margin". Amplitudes below the claimed range fail
earlier: a = 0.3 at tau 5,000 gives 2.17% (CP95 2.59%) at noise 1.0,
and noise above 2.0 was not run, so it is not claimed.

*What the rule does not cover.* The shape tables, the mixed pairs and
the held-out scenarios below were run with the scaled-tolerance
estimator (`out/limits-table.txt`, `out/mixed-pair-scaled.txt`,
`out/heldout-scaled-a.txt`, `out/heldout-scaled-b.txt`) and have not
been re-run with the robust one, whose `N` and `WARM` differ (above):
whether they carry over is unknown. **A hump or a drift that changes the
steady value by more than the bar is not detected.** The rule looks at
a window of 20 batches at three places after `N` and at the tail; a
shape between them, or a slow drift that the tail windows treat as
steady, is not seen, and which of these the measured window catches
depends on where it falls, not on the rule. At margin 8, the worst
CP95 upper bound over the shapes of each family, false KEEP, in percent
(`out/limits-table.txt` has every margin and the false REJECT):

| Family (columns: noise) | 0.10 | 0.25 | 0.50 | 1.00 | 1.50 | 2.00 |
|---|---|---|---|---|---|---|
| second plateau (+2%, +4%, +6%) | 7.26 | 6.60 | 10.06 | 19.23 | 20.05 | 13.91 |
| drift (+2%, +4%) | 56.38 | 39.23 | 29.62 | 14.37 | 8.27 | 4.87 |
| hump, +10% (two positions), and late tree with a hump | 89.83 | 88.13 | 76.99 | 32.30 | 33.36 | 31.59 |
| narrow hump, +5% to +20% over 2,000 or 4,000 iterations, starting at 14,000 to 52,000 | 75.76 | 69.34 | 50.34 | 18.42 | 8.46 | 5.42 |
| hump on the earlier tree of a mixed pair | 1.27 | 1.13 | 1.07 | 1.16 | 1.30 | 1.19 |

The audit shapes (a second plateau of +2%, +4% and +6% until 80,000
iterations, a drift of +2% and +4% falling to zero over 300,000, a +10%
hump, a late tree with a +10% hump) at margin 8, as the false KEEP rate
of a true ratio of 1.00 and of 1.03 (4,000 comparisons per cell):

| Shape | Noise 0.25: 1.00 / 1.03 | Noise 1.0: 1.00 / 1.03 |
|---|---|---|
| second plateau +2% | 0.05% / 5.95% | 0.20% / 4.97% |
| second plateau +4% | 0.03% / 0.82% | 1.60% / 16.75% |
| second plateau +6% | 0.03% / 0.55% | 3.52% / 18.20% |
| drift +2% over 300,000 | 0.10% / 12.28% | 0.10% / 3.98% |
| drift +4% over 300,000 | 0.65% / 37.95% | 1.10% / 13.45% |
| hump +10% 20,000 to 60,000 | 0.00% / 0.62% | 6.00% / 8.07% |
| hump +10% 50,000 to 80,000 | 79.25% / 87.28% | 11.72% / 31.07% |
| late f = 3 and hump 50,000 to 70,000 | 0.00% / 0.62% | 0.00% / 0.68% |
| late f = 3 and hump 100,000 to 130,000 | 0.00% / 1.00% | 0.90% / 3.67% |

The narrow-hump and mixed-pair rows need a reading. The mixed pairs put a hump of +10%
or +20% between 40,000 and 75,000 on the earlier-settling tree, and
the later tree (drop at 2 or 3 times) sets `WARM`, which at margin 8 is
beyond the hump (5th percentile 68,000 for the tree at 2 times, 112,000
at 3 times, at noise 2.0, `out/sharp-warm.txt`): they pass because of where the window falls,
not because the hump was seen. The same five pairs had a worst CP95
upper bound of 87% false KEEP at margin 3 (noise 0.10) and 76% at
margin 4, where the window did fall on the hump
(`out/limits-table.txt`). The narrow humps (an area of 40, 60 or 80
percent-batches, so 2% to 4% of excess in a 20-batch window that holds
the whole hump) that start before 30,000 end before the measured window
at margin 8 (1.4% CP95 upper bound at most, over every noise), and the
ones that start at 40,000 to 52,000 sit in it: their false KEEP of a
true ratio of 1.03 at noise 1.0 is 1.57% to 17.40%, and the family's
worst CP95 upper bound is 75.76% at noise 0.10 and 18.42% at 1.0.

Held-out random scenarios (`heldout.py`; ranges written down before the
runs: arm A with f of 1 to 3 and one or two of a plateau, a drift or a
hump, arm A's drop-position spread 1 or 2 times the committed one, arm B
with f of 1 to 2 and sometimes a small plateau or drift, process spread
0.02 to 0.08; 30 scenarios per noise, 1,000
comparisons per cell, 3 cells per scenario, so 90 cells): at margin
8, the cells whose CP95 upper bound exceeds 2.5%, and how many of
them are a false KEEP, were 8 (6) of 90 at noise 0.1, 24 (17) at 0.25,
23 (17) at 0.5, 27 (21) at 1.0, 44 (33) at 1.5 and 42 (30) at 2.0
(`out/heldout-scaled-a.txt`, `out/heldout-scaled-b.txt`). These
scenarios are built to violate the rule's assumptions, so those counts
are the size of what it does not cover, not an error rate.

The golden set, T1.12, checks the Node workload end to end: it runs
real `perfproof compare` with injected speedups of known size, Node
included, on real hardware. It does not scan warm-up shapes, so it does
not bound the shapes listed here. It has not run yet, and data from real
hardware on warm-up shapes does not exist. Until both do, this rule's
behaviour on real workloads is unknown, and so is whether margin 8 is
enough or too much on a quiet machine.

Earlier rules, kept as evidence for what this one replaced (the models
behind the bullets after the first use the committed noise, 1.0):

- *The rule before this revision* (a cover check through the measured
  window, a 2% tolerance, an "unknown" verdict when the cover failed;
  the figures are at noise 1.0 unless a noise is given):
  its stats audit (figures reported by the audit, not reproduced in this
  repository) found that the check ran per tree while both trees
  are measured at 3 x the larger `N`, so a hump on the earlier-settling
  tree was never checked (5.3% false KEEP on the bar at the committed
  noise); that the 2% was chosen at noise 1.0 and 1.5, where noise
  hides the shapes, and at noise 0.75 and below drift and plateau
  shapes gave 6% to 55% false KEEP; and that about 83% of pairs at the
  committed noise ended in "warm-up unknown", which withholds a KEEP on
  a busy machine and contradicts the design that a busy machine widens
  the interval and never blocks one. It was removed rather than tuned
  again; its model and output are in the history of the pull request
  (`e05f8ba`).
- *The scaled tolerance from window means* (`tol = max(0.02, 3 se)`,
  `se` the sample standard error of 20-batch window means, no cap):
  unknown in 0.52% of pairs at noise 1.0, but heavy-tailed batches
  moved it: one batch 30 times the usual in each process gave `tol` of
  51% and `N` of 0, and outliers in 0.5% to 2% of batches gave a false
  KEEP of 8.0% to 8.3% on an exponential tail at margin 8 (`out/outlier-probe.txt`). Replaced
  by the robust estimator above; its margin runs are the `scaled`
  files.
- *The robust estimator with a different cap* (round 7, rejected,
  `PREREGISTRATION.md`): with no cap the sharp pair (1, 1) is unknown
  in 4.65%, 5.80% and 5.75% of pairs at noise 0.5, 1.0 and 1.5
  (`out/estimator-unknown-r7.txt`; a 15% cap: 4.65%, 5.80%, 5.63%);
  at noise 1.0 and 1.5 that is over the 5% the rule allowed, and with no cap the exponential tails
  at noise 1.0 gave a CP95 upper bound of 16.22% (false KEEP, clean),
  up to 7.43% with outliers (`out/r7-nocap-verdict.txt`). The 15%
  variant's error rows were not run. The 4% cap stays.
- *More processes per tree* (round 8, rejected, `PREREGISTRATION.md`):
  with the 4% cap and margin 8, 24 processes gave a pair unknown in
  18.53% and 61.22% of pairs at noise 1.0 and 1.5 and 36 processes
  6.57% and 39.70% (12: 51.25% and 84.97%; limit 5%;
  `out/estimator-unknown-r8-p24.txt`, `out/estimator-unknown-r8-p36.txt`).
  Choosing both trees at noise 1.0 costs 446 s and 496 s (12 processes:
  334 s). The error rows (part (a)) were not run. The 12-process rule
  stays.
- *`N` by windows at n, 2n and 4n* (noise 1.0, `WARM` 3 N, one recording
  of 320 batches, `out/estimate-model.txt`, "BEFORE"): with trees that
  settle like the committed one it was fine (0.72% on the bar), but a
  baseline that settles 3 times later gave a false KEEP of a no-change
  70.5% (CP95 71.7%) and 79.8% on the bar, and a candidate that
  settles 3 times later a false REJECT of a true 6% gain 70.4% (CP95
  71.5%); at 2 times it was 1.2% (CP95 1.5%) at worst. The cause is
  the one above. On exponential tails with tau of 5,000 or more it
  gave a false KEEP of 14.5% to 91.0% of no change.
- *The tail rule at 5% without a cover* (noise 1.0, `estimate-model.txt`,
  "AFTER"): the largest CP95 upper bound over the sharp pairs was
  1.10% false KEEP and 0.19% false REJECT, and over the exponential
  tails at most 1.3% on the bar except tau 2,000. It does not see a
  stage between `N + 30,000` and `3 N + MEASURE`; the stats audit's
  +10% hump on a late tree gave 59% false KEEP (79% on the bar) in its
  own run of that rule, which this repository does not reproduce.
- *Margin with the drop known exactly* (noise 1.0, `out/margin-model.txt`): with
  margin 2, 3 and 4 and f up to 3 for either arm, the largest CP95
  upper bounds were 1.1%, 1.1% and 1.4% for a false KEEP and 0.3%,
  0.2% and 0.3% for a false REJECT. That is the margin alone, without
  an estimate of `N`; the estimated-`N` runs above are what the rule
  does.
- *The earliest rule* (noise 1.0, `WARM` 12,000 from the baseline alone, 2.1 times
  the drop; `out/ab-model.txt`): a baseline that settles 2.2 times
  later gave a false KEEP of a no-change 10.8% of the time and, at 2.5
  times, 83.8%; a candidate that settles 2.2 or 2.5 times later gave a
  false REJECT of a true 6% gain 12.0% and 84.3% of the time.
- *The hand check* "compare the first and second half of a window" has
  little power and is not a safeguard. Pooled over 12 executions (a z
  test on the mean log ratio of first against second half), it fired
  3.5% of the time with no bias, 15.9% at a window-mean bias of +2.4%,
  56.1% at +6.9% and 93.2% at +13.3% (`out/half-check-power.txt`); one
  execution fires 25% of the time with no bias at all.
- *Not covered:* noise above 1.0 with the robust estimator (the
  scaled one was run to 2.0 and its exponential ridge is above);
  exponential amplitudes below 0.5 (a = 0.3 at noise 1.0 is over the
  limit with the scaled estimator, and one cell of a = 0.3 to 0.4 at
  noise 0.5 with the robust one); outliers that cluster in time or a
  sustained slowdown ("Heavy-tailed batches" above covers independent
  outliers only); a cap other than 4%; shapes other than those above
  (a periodic or growing
  curve, a second stage not in the list), recordings whose processes
  differ more than the committed 12 (spread 0.042), and any workload
  other than the one measured. Also not covered: heap size and
  collection settings (`NODE_OPTIONS` is recorded, not gated), and a
  workload that never reaches a steady state (growing caches, data that
  changes per iteration). The curve was measured under load; whether
  the same counts are conservative on a quiet machine was not measured.

### Warm-up (Java)

A JVM starts every execution cold. It loads classes, interprets
bytecode, and compiles hot methods on background threads (C1, then
C2) while the program runs, so the time of an execution mixes warm-up
with steady state. The spec's `warmup` executions do not warm the next
one: each is a new process, and the JIT's work is not carried from one
to the next. They still earn their place as a smoke test: a benchmark
that fails to start (a missing class, a bad flag) fails in a discarded
execution, before any measured round. (Class-data and ahead-of-time
caches that a project may enable are not modelled here.) Three rules
follow.

- **Say what is being measured.** If the user's real use is a
  short-lived command (a CLI, a test run), the warm-up is part of what
  they feel: measure `wall` or `cpu` of the whole command, and do not
  warm in process. For a library, a service or anything long-lived,
  measure the steady state with a `reported` value taken inside the
  process after warm-up, which also leaves JVM start-up out.
- **The project's JMH first.** Where a project has JMH benchmarks, run
  the one that covers the changed code, through a small runner that runs
  JMH and prints its primary score as `PERFPROOF {"value": ...}`
  (`skills/perfproof/java.md` has it). JMH forks, warms up and measures
  by its own rules. The runner fails unless exactly one benchmark and
  parameter set matched, and the spec's direction must match the
  benchmark's mode (average time is lower-is-better, throughput
  higher-is-better). A round runs both arms once, so its cost is
  `2 x forks x (warm-up + measured) x seconds per iteration`, with the
  iteration counts summed. The first look is at 10 rounds, so against
  the default `max_seconds` of 1800 a round must cost well under 180 s. For the
  settings the runs of this section used, the formula gives 80 s a round
  (one fork, 15 warm-up and 5 measured iterations of 2 s: fits) and
  300 s a round (one fork, 10 warm-up and 5 measured iterations of
  10 s: ten rounds are 3000 s, which does not fit). Choose settings that
  fit, or raise `max_seconds` and say why.
- **Otherwise, iterate in process until stable.** The program repeats
  one iteration of the workload (long enough to be far above the
  clock's step, tens of milliseconds). It does not look at stability
  before 200 iterations; from then on it stops warming up when the
  median of the last 10 iteration times is within 1% of the median of
  the 10 before. Then it times 20 more and reports their median per
  operation, printed with all its digits so the resolution check
  passes. If it has not stabilised after 600 iterations it reports the
  median of the last 20 warm-up iterations, says so on stderr and
  exits 0. It is not a failed execution: a failed execution is a
  REJECT on correctness for a candidate and no verdict for a broken
  baseline (see "Failed executions"), and a workload that sometimes
  does not settle would turn into that. The value then carries the
  settling noise, and the interval widens by it. Every execution ends
  with one stderr line, `stabilised after N iterations`, or
  `did not stabilise after N iterations` (the
  committed task workloads also print every iteration's time before
  it; the template in `skills/perfproof/java.md` prints only that
  line).

  The rule costs at least 220 iterations an execution: about 7 s at 30
  ms an iteration and 18 s at 80 ms, 4.4 to 5.5 times the 40 to 50 of
  the first form of the rule. For 220 iterations (the floor and the 20
  timed ones) the span is 0.26 s to 17.5 s over the committed
  workloads' iterations of 1.2 to 79 ms; whether settling follows
  iterations or seconds is not measured. An execution that
  reaches the cap costs 600 iterations, so the spec's `timeout_seconds`
  must be above that, about `2 x (600 x seconds per iteration + 10)`: a
  timeout is a failed execution, which the cap exists to avoid.

Provisional: the constants of that rule (10 and 10 iterations, 1%, 20
timed iterations, the floor of 200, the cap of 600, "tens of
milliseconds", and the factor 2 and the 10 s in the timeout formula)
were chosen by reasoning and by the model below, not
measured or calibrated on JVM data. The tolerance of 1% is below the
default bar (`min_effect`, 3%), so a bar-sized drift between two
blocks does not pass as stable; but a drift check bounds the slope of
the warm-up curve, not the distance to the steady state, so it does
not by itself tell that warm-up is over.

**Stated limit: settling.** The alternating rounds cover random
JVM-to-JVM variation (each execution is a fresh JVM at its own level).
They do not cover a systematic difference in how fast the two arms
settle: with the floor, both arms discard about the same 200
iterations whatever their curves look like, so an arm that is still
settling at iteration 200 is read as slower. In a synthetic model
(`studies/java-warmup/ab_model.py`; iteration times
`T (1 + a exp(-k / tau))` with `a` 0.15, 2% noise an iteration, the
model's cap at the floor plus 120 iterations rather than 600, a JVM
level with 5% SD, production `run_looks`, 60 rounds, seed 913377451,
4000 comparisons a row; command and outputs under
`studies/java-warmup/`) a baseline settling
with `tau` 60 iterations against a candidate with `tau` 3, and a true
ratio of 1.00, gave a false KEEP:

| Stop rule | False KEEP, baseline `tau` 60, candidate 3 | False REJECT, true 6% gain, baseline `tau` 3, candidate 60 | Bias of the ratio |
|---|---|---|---|
| 5% tolerance, from iteration 20 (the first form of the rule) | 84.9% | 85.6% | +9.2% and -8.4% |
| 1% tolerance, from iteration 20 | 78.7% | 80.2% | +8.2% and -7.6% |
| 1%, floor of 60 iterations (2000 comparisons) | 8.0% | 9.5% | +4.5% and -4.3% |
| 1%, floor of 120 (2000 comparisons) | 0.2% | 0.1% | +1.7% and -1.7% |
| 1%, floor of 200 (2000 comparisons) | 0.0% | 0.0% | +0.4% and -0.4% |

Those rows are far from the bar (true ratios of 1.00 and 1.06 against
a bar of 1.03), where a bias of 0.4% decides nothing. On the bar, at a
true ratio of 1.03 with the floor of 200 (seed 771390254, 2000
comparisons a row, outputs in `studies/java-warmup/`), a false KEEP is:

| Baseline `tau`, candidate `tau` 3 | False KEEP | Upper 95% bound (Clopper-Pearson) | Bias of the ratio |
|---|---|---|---|
| Control: both arms `tau` 3 | 0.5% | 0.8% | +0.0% |
| 60 | 1.9% | 2.4% | +0.5% |
| 75 | 2.8% | 3.4% | +0.9% |
| 90 | 7.7% | 8.7% | +1.4% |
| 120 | 27.0% | 28.7% | +2.6% |

The bias is about `a exp(-(floor + 10) / tau)` (0.15 times
`exp(-210 / tau)` here), so the limit moves with the amplitude `a` and
the settling time `tau`; the false KEEP rate crosses 2.5% near a bias
of 0.7% (about a quarter of the bar), which is `tau` 68 for `a` of
0.15. The stats auditor's amplitude rows (seed 462910583, 4000
comparisons a row, `studies/java-warmup/audit-out-G7.txt`, command in
`audit_model.py`) agree: at a predicted bias of 0.69% a false KEEP is
2.2% for `a` 0.5 and 2.9% (bias 0.67%) for `a` 1.0, and at 1.5% it is
7.2% and 8.5%. More noise an iteration lowers these rates, because it
widens the interval: in the auditor's noise rows (same seed and size,
`studies/java-warmup/audit-out-G8.txt`) a false KEEP at `tau` 60, 75, 90
and 120 was 1.1, 2.3, 5.2 and 17.2% at 10% noise an iteration and 0.7,
1.6, 1.4 and 3.9% at 31% noise, against a control of 0.9% at both.

Tightening the tolerance barely helps, and the iterations the arms
report until stable do not show the difference (medians 26 and 25 at 1%
from iteration 20, and 200 and 200 at the floor, which is where most
executions stop); what helps is the floor, which sets how much warm-up
is discarded whatever the data look like. The floor of 200 is therefore
the protection, and what it leaves is a limit: a JVM that settles more
slowly than the model's `tau` of 60 iterations, or a transient the model
does not have, is not covered. The model has no JVM data behind it, and
its noise of 2% an iteration is below what the committed manifests'
workload tails show: the iteration time's coefficient of variation over
the last 14 iterations of one JVM is 2.9 to 9.2% in the 5 tails with
iterations of 8.7 to 79 ms and 25 to 129% in the 7 tails with iterations
of 1.2 to 16 ms (12 tails from the six Java manifests' `runs`, which are
runs of the earlier form of the loop and will be re-run on a quiet
machine). The stderr line the skill reads is `did not stabilise`. It
detects an execution whose iterations are runaway or very noisy, not
slow settling (a higher floor makes a cap more likely: for flat noisy
iterations with a coefficient of variation of 95%, the cap rate went
from 0.8% at a floor of 200 to 3.0, 9.5 and 30.0% at floors of 300, 400
and 500, 20000 executions a cell, seed 462910583,
`studies/java-warmup/audit-out-cap-vs-floor.txt`), so it is not a check
on the limit above. The skill reports every capped execution and points
it to the deoptimisation and noise rows of its playbook; the verdict
perfproof gave stands, and the report says it includes warm-up medians.

Measured on one workload, a CPU-bound Java loop of 266,304 calls an
iteration, JDK 21.0.12.1 Temurin, in process, 10 fresh JVMs each, with
the first form of the rule (5% tolerance from iteration 20):

| Where | Iterations until stable | First iteration | Reported ns per call, 10 JVMs | First iteration against the stable one |
|---|---|---|---|---|
| macOS 15.7.7 arm64, native JDK | 20 to 31 | 52 to 74 ms | 108.5 to 129.7 | median 1.81, range 1.68 to 2.54 |
| Linux arm64, Docker VM on the same Mac | 20 to 29 | 65 to 95 ms | 154.8 to 178.5 | median 1.70, range 1.43 to 2.30 |

The last column divides each JVM's first iteration by its reported
value times 266,304 calls (the stable iteration is taken from the
reported value, not from the iteration list). The values of 10
identical JVMs spread by 19.5% and 15.3% (max over min). These are
single runs, not calibration; the two rows are different machines and
are not to be compared with each other.
`eval/tasks/workloads/java/warmup-runs.txt` is an excerpt of the output:
per JVM the value to two decimals, the first two iteration times and the
iterations until stable, not the whole iteration list. The exact source
of the loop is `eval/tasks/workloads/java/evidence/workload-source.txt`
(sha256
`92868074da3506f1d65f8e67b875aeb4076213a868888ccd0b851409db32f5ec`).

Stable is not final. A library's own JMH benchmark (one method, one
parameter set), run through
`eval/tasks/workloads/java/PerfproofJmh.java` with one fork, 15 warm-up
iterations of 2 s and 5 measured (settings
`-Djmh.forks=1 -Djmh.warmup=15 -Djmh.measure=5 -Djmh.time=2`), run
three times in the same Linux VM,
ended at 1414, 895 and 1354 ns/op. In the first run the score sat
between 860 and 920 ns/op for ten iterations, then went to 1060 at
the eleventh and 1450 at the twelfth and stayed near 1400 through the
measured iterations. On the Mac itself (settings
`-Djmh.forks=1 -Djmh.warmup=10 -Djmh.measure=5`, the default 10 s
iterations), the ten warm-up
iterations went 1197, 1121, 1103, 1086, 1012, 1078, 1099, 1058, 1075,
1070 and the five measured iterations 1074, 1031, 986, 992, 948. The
cause of the step is not established (a recompilation or noise on the
host; no event log was captured). What it shows is that a warm-up that
looks converged can be followed by a different level, so no
in-process stop rule is trusted alone. The warm-up default of the
runner (5 iterations of 2 s, 10 s a fork) is shorter than the level
change in the Linux run above, which came about 20 to 24 s in, so
JMH's own warm-up can end before such a change; raise `-Djmh.warmup`
where the budget allows. What perfproof does when the
rounds of a benchmark disagree widely is what it does for any noisy
benchmark: the interval stays wide, and at the budget the verdict is
INCONCLUSIVE (`insufficient_evidence`), which the skill explains.

### Warm-up (Go)

Go compiles ahead of time to machine code. There is no interpreter and
no JIT, so no execution has a phase that runs different code from the
next; the warm-up of "Warm-up (Java)" does not exist, and its rules (an
in-process loop that waits for the iteration time to settle, with a
floor and a cap) are not used. What can still make the first
iterations differ, a cold cache, page faults while the heap grows, the
first collections, is the whole of what "warm-up" means here, and what
is measured of it follows.

The runs (`studies/go/run.sh`, summarised by `studies/go/analyze.py`
into `studies/go/results.txt`; raw output in `studies/go/runs/`): one
workload, an echo router serving one request through `ServeHTTP`, in
12 fresh processes, each timing 60 blocks of 100,000 requests and
reading the number of collections in each block. Go 1.25.14, linux/arm64
in a Docker container on the Mac the work was done on, which was shared
with other work while it ran, so its noise is higher than a quiet
machine's.

- *The first block is not slower.* Its time over the median of that
  process's blocks 10 to 59 was 0.99 at the median of the 12 processes,
  0.93 at least and 1.22 at most; the pooled medians of blocks 0 to 5
  were 0.969 to 1.021 of steady. In this workload there is no warm-up
  curve to discard. (For comparison, the first iteration of the Java
  loop in "Warm-up (Java)" was 1.43 to 2.54 times its stable one.) That
  is one workload with a small heap (48 bytes allocated a request); a
  workload that builds a large structure first, or grows a heap to
  hundreds of megabytes, pays for that in the first iterations, and
  none was run.
- *Processes differ.* The steady medians of the 12 identical processes
  spread by 8.6% (maximum over minimum, 253.8 to 275.6 ns a request),
  and a block's time varied with a coefficient of variation of 5.8 to
  36.5% inside a process. Neither is warm-up, and both are what the
  alternating rounds and the interval are for: a comparison of two
  versions of such a benchmark needs enough rounds to cover them.
- *Collections are part of the work, and part of the noise.* The
  collector runs concurrently and its frequency follows the allocation
  volume and the live heap, so a version that allocates less is faster
  for it, which is the point. In the runs there were 78 to 82
  collections over the 6,000,000 requests of a process, one or two in a
  block; the steady blocks that held two collections took 1.018 of the
  steady median and those that held one took 0.994 (199 and 401 blocks).
  A cycle that falls inside or outside the timed region is therefore
  worth a few percent of a run that holds one or two, and little of a
  run that holds dozens.

What follows for a Go benchmark:

- Make one execution long enough to hold tens of collections (the
  workload above had about 1.3 per 100,000 requests, so 2,000,000
  requests are about 26); a `-benchtime Nx` that gives a hundred
  iterations of a millisecond each may hold none, and then the
  allocation cost is either all in or all out of the run.
- Give both versions the same `GOGC`, `GOMAXPROCS` and `GOMEMLIMIT`.
  The first two are recorded with their values in the environment of
  every run, and a difference is a warning, not a gate; never set one
  to change a single version's collector behaviour.
- A `reported` metric from inside the process takes the median of
  blocks, not one number over one long run, so that a collection or a
  scheduling pause in one block does not decide it: the template is in
  `skills/perfproof/go.md`. Its block count and size are provisional,
  chosen by reasoning and the runs above, not calibrated. It has no
  stop rule, so no execution can end in "did not settle", and a
  non-zero exit, which is a failed execution and a REJECT on
  correctness for a candidate, comes only from wrong output.
- For a short-lived command, time the built executable as a whole
  (`wall`), not `go run`, which adds the toolchain's own start-up (see
  "Node, Java and Go runtimes").

Not measured: workloads with a large live heap (where the collector's
pacing, and so its share, changes), programs that are mostly goroutine
scheduling or blocking, cgo, and any machine but the one above.

### Warm-up (C and C++)

C and C++ compile ahead of time. There is no interpreter and no JIT, so
the warm-up of "Warm-up (Java)" does not exist and its rules are not
used. What can still make early iterations differ is the cold start of
the process: page faults, instruction and data caches, branch
predictors, frequency scaling. One run measures how large that is for
one workload.

The run (`studies/native/run.sh`, summarised by `studies/native/analyze.py`
into `studies/native/results.txt`; raw output in
`studies/native/runs-warmup/`): one workload, LZ4 decompression of one
block of about 370 KB (the five source files that
`studies/native/run.sh` names, 371,627 bytes together), built with the system cc
at `-O2`, in 12 fresh processes, each timing 60 blocks of 300
decompressions (17 to 19 ms a block). Apple clang 17.0.0 on an
Apple M4 with 10 CPUs, native, not in a container. The machine was
shared with other work while it ran: the load average was 7.07 when the
run started (`studies/native/runs-warmup/environment.txt`).

- *The first block is slightly slower, by at most 10%.* Its time over
  that process's steady median (blocks 10 to 59) was 1.03 at the median
  of the 12 processes, 0.99 at least and 1.10 at most. Pooled over the
  12, the median of block 0 over steady was 1.030, of blocks 1 to 5
  0.999 to 1.016, and of block 59 0.995 (`studies/native/results.txt`).
  There is a small cold-start cost in the first block, and no curve
  after the first few blocks.
- *Processes differ by 3%.* The steady medians of the 12 identical
  processes spread by a factor of 1.032 (57,398 to 59,210 ns a
  decompression), and a block's variation inside a process was 1.7 to
  5.5%. This is the noise the alternating rounds and the interval are
  for.
One workload, one machine, 12 processes. What follows for a C or C++
benchmark is expected from this and from how ahead-of-time code runs,
not measured beyond it:

- Make one execution long enough that start-up is a small part of it
  (half a second is a suggestion, not a measured threshold, and the
  same for both versions), and use a fixed amount of work, not a fixed
  time. A benchmark mode that runs for N seconds gives both versions
  the same wall time.
- `warmup` executions (a whole execution each) cover the cold start
  seen above (up to 10% of a 17 ms block); there is no in-process phase
  to wait for, and an in-process loop with a floor and a cap as for Java
  is not used.
- A `reported` rate from inside the process takes the median of blocks,
  not one number over one long run, so that a pause in one block does
  not decide it. This follows the Go template in `skills/perfproof/go.md`
  and has not been calibrated for C.
- Say when the machine was loaded: `perfproof profile` reports
  `machine_loaded` with the load average and the share of wall time with
  no thread on a CPU (`studies/native/simdjson-profile.json`).

Not measured: a workload that builds a large structure first or maps a
large file (those pay page faults in the first iterations), workloads
that mostly wait on I/O, threaded programs, x86_64, a machine with no other load, and
the effect of frequency scaling on a laptop on battery.

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
