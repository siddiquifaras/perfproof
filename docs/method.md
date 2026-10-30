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
perfproof only cannot show it. For a target where perfproof cannot measure coverage, a
KEEP needs the spec's explicit opt-in, and the verdict is labelled
"coverage not checked", with the reason: no coverage command is
declared, it wrote no report, or the report does not list the changed
file.

**Coverage reports (any language).** Python is measured by perfproof
with coverage.py. For every other language, the spec declares a
coverage command that writes a coverage report in a standard format,
and perfproof reads it. The whole contract:

- *How it is declared.* The optional spec field
  `correctness.coverage_command` (an argv list) with
  `correctness.coverage_timeout_seconds`, required with it and only
  with it. It is absent by default: a spec without it is unchanged and
  keeps its hash. When non-Python files changed and the field is
  declared, perfproof runs the command once in the candidate tree, in
  `correctness.cwd`, after the correctness gate passes, with the
  environment variable `PERFPROOF_COVERAGE_REPORT` set to the absolute
  path of a file that does not exist yet; the command runs the project's
  checks under its coverage tool and writes the report there. When it
  is not declared, nothing extra runs: the non-Python changes are
  `not_available` ("no coverage command declared"), or `waived` with the
  opt-in. A command that exits non-zero, times out, or writes no report
  leaves them `not_available` with that reason, or `waived` with the
  opt-in, as for any non-Python change the gate could not check.
- *Format.* Three: lcov (`SF:`, `DA:<line>,<hits>`, `end_of_record`),
  Cobertura XML (`class` elements with `filename`, `line` elements
  with `number` and `hits`) and JaCoCo XML (`package` elements with a
  `name`, `sourcefile` elements with a `name`, `line` elements with
  `nr` and `ci`; the hits are `ci`, the covered instructions). The
  first non-blank character tells XML (`<`) from lcov, and the root
  element tells JaCoCo (`report`) from Cobertura (`coverage`); any
  other root is unreadable. Only line hits are read. A report larger
  than 64 MiB (a provisional input bound, not measured and not a
  memory bound), or one that does not parse, is unreadable and counts
  as no report, with the error in the gate's detail.
- *Which file.* A path inside the candidate tree names that file; an
  absolute path outside it is ignored. A relative path is relative to
  the correctness command's working directory (`correctness.cwd`), and
  for Cobertura to each `<source>` directory first. A JaCoCo file is
  its package path plus the source file's name, looked for under
  `src/main/java`, `src/java`, `src` and then the directory itself,
  each below `correctness.cwd`; the first that exists names the file. A changed file the
  report does not list is not checked.
- *Which lines.* The changed lines are the lines `git diff` adds or
  changes in the candidate (every line of a new file), as for Python.
  A changed line the report lists with zero hits is unverified; with
  hits above zero it ran; one the report does not list is not
  executable and asks nothing. Lines only deleted ask nothing, and a
  deleted file is not checked.
- *What a hit proves.* A report has no per-test labels, so a hit
  counts only when the command exited 0 and no test was quarantined or
  a known failure (the lines may have run only for those tests);
  otherwise the lines that ran are unverified too, and the gate says
  why.
- *Result.* Unverified lines make the gate `not_available`, naming
  them by file. When every changed line of every non-Python file the
  report lists ran, and no other non-Python file changed, the gate
  passes.

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
| `runtime` | The runtime from outside the tree that each arm actually runs: the interpreter and its version, followed through launchers such as `uv run`, `env` and shebangs; for a native executable built in the tree, the libraries it links from outside the tree, with their hashes; a binary committed in the tree that differs between arms. Node, Java, Go, Ruby, .NET and the C and C++ compilers are recognised, and a Rust build is read for its compiler (below). Anything that can't be identified is `not_available`, including tools known to start other interpreters (tox, nox, poetry and the like), a shell (the program it runs is not the one perfproof launched), and builders and toolchains such as `cargo`, `make`, `mvn` and `gradle`, and `dotnet` with a subcommand other than `build`, `run`, `test` or `exec`. It is `failed` only when both arms are identified and differ. Waivable only when the spec allows runtime changes. An untracked executable inside the tree is treated as a build of the code under test. Not seen: libraries loaded at run time with `dlopen`, and an interpreter started by a program the gate identified as Python or native |
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

**Node, Java, Go, Ruby and .NET runtimes.** A command whose program name resolves
(symlinks followed) to `node` or `nodejs` is a Node runtime, to `java` a
Java runtime, wherever the file lives, in the tree or outside it, to
a native `go` (an ELF or Mach-O file, not a script) with the subcommand
`run`, `test`, `build` or `vet` a Go toolchain, to `ruby` (or `jruby`,
`truffleruby`, `ruby3.4`) a Ruby, and to a native `dotnet` with the
subcommand `build`, `run`, `test` or `exec`, or a `.dll` argument, a .NET
host. A shell is unknown,
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

*Ruby* is asked, not derived, as Node is. The gate runs the launch
chain's own `ruby -e` in the benchmark's working directory and
environment, with `RUBYOPT` removed (a `-r` in it could print a forged
report), stdin closed, a timeout and output capped at 4096 characters;
Ruby prints the real path of its own executable (`RbConfig.ruby`) and
`RUBY_DESCRIPTION`, the line `ruby -v` prints. The runtime is the
executable that answered, so a version manager's shim (`rbenv`, `asdf`,
`mise`) named `ruby` is seen through; the record carries that path and
the line followed by `; sha256 <digest>` of the file. `bundle exec
COMMAND` is followed to its command, identified as if launched directly,
which is how Bundler starts it (through `PATH`); a binstub or a gem
executable is followed through its `#!` line. Unknown, so `runtime` is
`not_available` with the reason: `bundle` with any other subcommand or
an option before the command, `rbenv`, `rvm`, `asdf`, `mise` and
`chruby-exec`, and a `ruby` that is a container launcher (the program
launched is the launcher). `bundle install`, `update`, `add`, `lock`,
`cache` and `outdated`, and `gem install`, `update` and `fetch`, download
gems, so the run stops as a fetching launch. `RUBYOPT`, `RUBYLIB`,
`RUBY_YJIT_ENABLE`, `RUBY_GC_HEAP_GROWTH_FACTOR` and
`RUBY_GC_HEAP_INIT_SLOTS` are recorded with their values, and a
difference is a warning, not a gate.

*.NET* is asked, and only for `dotnet build`, `run`, `test` and `exec`
and for a `.dll` argument; every other subcommand (`tool`, `new`, `add`,
`restore`, `nuget`, `workload`) starts other programs or fetches code,
so it is unknown (and `tool install`, `update` and `exec`, `dnx`, `add`,
`restore` and `workload install` stop the run as fetching launches). The
gate runs the command's own `dotnet --version` in the benchmark's
working directory and environment (the SDK that `global.json` selects,
and a failure when it asks for one that is not installed, which makes
the runtime unknown) and `dotnet --list-runtimes` (what a target
framework rolls forward to), with stdin closed, a timeout and output
capped at 4096 characters; `exec` and a `.dll` ask only for the runtimes,
which need no SDK. The record carries the real path of the `dotnet` host
and a version of the form `sdk 10.0.402; runtimes
Microsoft.AspNetCore.App 10.0.13, Microsoft.NETCore.App 10.0.13; sha256
<digest>`, the runtimes sorted and without their directories, the digest
that of the host. The SDK's compilers and the framework's assemblies are
covered by those versions only, not hashed. A `dotnet` that is a script
or a container launcher is a shell, so unknown. `DOTNET_TieredCompilation`,
`DOTNET_TieredPGO`, `DOTNET_TC_QuickJitForLoops`, `DOTNET_ReadyToRun`,
`DOTNET_gcServer`, `DOTNET_gcConcurrent` and `DOTNET_GCgen0size` are
recorded with their values, and `packages.lock.json` as a dependency
lockfile. The integration tests that run the real `dotnet` ran in the
SDK 10.0.402 image (`tests/fixtures/dotnet/SOURCE.md`); this Mac has no
.NET.

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

**Rust.** A Rust program is a native executable, so a binary that
`setup` builds at the same path in each tree, such as
`target/release/<name>`, is a native build like any other (above). The
gate also reads which compiler built it, from the binary: the commit of
`rustc` in the `/rustc/<40 hex digits>/` prefix of the standard
library's source paths, which a Rust binary carries on macOS and on
Linux, and, in an ELF binary, the `rustc version` line of its
`.comment` section. The file is read in chunks of `LOCK_CHUNK_BYTES`.
The same compiler in both trees passes, and the detail names it; the
decision rests on the commits, and the version lines (an unstripped
ELF binary has a second copy of one in its debug strings) are detail
only. Two different commits fail, with the difference `rustc`. A binary that
names a compiler in one tree only is `not_available`. A binary that
names none in either tree (not Rust) is
compared as a native build was before. `cargo`, `rustc` and `rustup`
are never followed, so a command that starts one is `not_available`
with the reason: rustup picks the toolchain from `rust-toolchain.toml`,
and `cargo run` builds before it starts the program, which would put
the build inside the timed run. Build in `setup` and run the binary
itself.

Not seen for Rust: cargo's own version (it is not run, because `cargo
-V` goes through the rustup proxy, which may install a toolchain that
`rust-toolchain.toml` pins and that is missing; that was not tested);
the `[profile]` settings in `Cargo.toml` (`opt-level`, `lto`,
`debug-assertions`) and `RUSTFLAGS`, so a change of profile between the
arms is not seen, and whether a binary is a debug or a release build
shows only in the path both arms share (`target/debug` or
`target/release`), which the detail names; the bytes of an untracked
build, which are not compared.

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
  The same line also carries every guardrail the runner can't measure
  itself, one key each (`docs/cli.md`, "What a benchmark prints").
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

### Warm-up (Rust)

Compiled Rust has no JIT, no interpreter tier and no class loading:
every execution of a built binary runs the same machine code from its
first instruction, so there is no JIT warm-up to wait for and the Java
rules (a floor of iterations, a stop rule) do not apply. Two
things outside the compiled code still made early executions slower in
the runs below, all on one machine that was busy with other work (10
cores, load average 15.7 at the first run), so none of the times is a
clean measurement.

- **The first execution of a new binary.**
  `eval/tasks/workloads/rust/warmup_runs.py` copied one release binary
  (an fd build searching, on one thread, the 20,000-file tree that
  `eval/tasks/workloads/sharkdp-fd-pr853/make_tree.py` makes)
  to five new files and ran each 30 times in a row; every wall time is
  in `eval/tasks/workloads/rust/warmup-runs.txt` (macOS 15.7.7, arm64).
  The first run of the five copies took 1045, 793, 371, 795 and 411 ms
  and the second 191, 131, 129, 208 and 190 ms; runs 16 to 30 of all
  five had a median of 153 ms (smallest 125, largest 243). So the first
  execution cost 2.4 to 6.8 times that median. What causes it was not
  isolated: the system's first look at a new executable on macOS and a
  cold file cache are both candidates (inference). Each tree's binary is
  new when `setup` builds it, so the spec's `warmup` should be at least
  1, as a precaution: whether a measured round sees this cost depends
  on the executions perfproof runs before it (for example one that
  produces the outputs for the correctness check), which was not
  examined here. Linux was not measured.
- **The first iterations in one process.**
  `eval/tasks/workloads/rust/warmup_iterations.rs` sorts and hashes
  20,000 small heap strings 200 times in one process
  (`eval/tasks/workloads/rust/warmup-iterations.txt`). Iteration 1 took
  4957 microseconds, iterations 2 to 5 took 3580, 2403, 2317 and 2297,
  but iterations 6 to 8 took 5421, 6798 and 5165, and of iterations 11
  to 200 (median 2822, smallest 2244.5, largest 11948) 83 of 190 took
  4000 or more. On this busy machine the series does not separate a
  warm-up from noise: iteration 1 is only slower than most of the later
  ones. As a precaution, a benchmark that loops in process and reports
  one number should discard its first few iterations (the skill says 5,
  a count this series does not establish) and should loop a fixed
  number of times: a loop that waits for the time to settle can fail
  to, and a benchmark that does not settle must not fail an execution.

Say what is measured as for any language: a command line tool is
measured whole (`wall`), with `warmup` at least 1; a library function
is looped in process and reported (`reported`).
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

### Warm-up (Ruby)

CRuby runs bytecode in an interpreter. With YJIT on (`--yjit`) it also
compiles methods to machine code once they have been called enough
times (`--yjit-call-threshold`), so the first iterations of a loop run
a mixture of interpreted and compiled code. Each execution is a new
process: the spec's `warmup` executions do not warm the next one, and
serve as a smoke test, as for Java.

The curve (`studies/ruby-warmup/run.sh`, summarised by
`studies/warmup_analyze.py` into `studies/ruby-warmup/results.txt`; raw
iteration times in `studies/ruby-warmup/runs/`): one workload, an
iteration of 600 string concatenations, 36,000 hash updates and 90,000
integer operations (`tests/fixtures/ruby/prog/lib/work.rb`), timed
1,500 times in each of 8 fresh processes, one container each, with
YJIT on and 8 with it off. Ruby 4.0.7, linux/arm64, the official
`ruby:4.0` image under Docker Desktop on a Mac that other work was
using, one CPU a container, so noise is higher than a quiet machine's.

- *YJIT on.* The steady iteration took 5.5 ms at the median of the 8
  processes (5.2 to 6.0). The first iteration took 3.1 times that at
  the median (2.8 to 4.3); the median over processes of the first 30
  iterations was 1.39 times it, and of the next 30 and every block of
  30 up to the 300th was 0.98 to 1.00. Compilation was over within the
  first two blocks in these runs.
- *YJIT off.* The steady iteration took 8.1 ms (7.9 to 8.7). The first
  iteration took 2.2 times that, the first block 1.14, the next 1.01.
  YJIT made the steady state 1.47 times faster here (a comparison of
  two medians of 8 processes, on one workload; not a measurement of
  Ruby).
- *The settle iteration is not a warm-up measure here.* The first
  iteration after which every window of 30 iterations stays within 10%
  of steady has a median of 330 and a maximum of 1,470 of 1,500 in the
  YJIT runs; within 20% it is 15 at the median and 630 at the
  maximum. Those late windows are single processes drifting by more
  than the tolerance on a shared machine (noise, a collection), not
  compilation, which the block medians above show as over. A rule that
  waited for "stable" would stop on noise.

What follows for a Ruby benchmark:

- **A fixed warm-up, no stop rule.** The in-process loop runs 300
  warm-up iterations (about ten times the 30 the curve needed), then 30
  timed ones, and reports their median, printed with all its digits. It
  never looks at stability, so no execution can end in "did not
  settle": a workload that never settles (a cache that keeps growing)
  still ends after the same number of iterations, and its spread is
  what the interval widens by. A non-zero exit, a failed execution and
  a REJECT on correctness for a candidate, comes only from wrong
  output. The template is in `skills/perfproof/ruby.md`.
- The count is by iterations, not seconds. An execution costs `(300 +
  30) x seconds per iteration`, so a 50 ms iteration is 16 s and a
  round of two executions about 33 s: the first look is at 10 rounds, so
  against the default `max_seconds` of 1800 an iteration must be well
  under 50 ms or `max_seconds` raised, with the reason said. One curve
  of one small workload does not show that 300 is enough for a large
  program, whose compiled code is larger; measure the curve of the real
  workload when the first iterations matter.
- Both versions get the same `--yjit` setting, which is in the
  launched argument list and so in the procedure gate; `RUBY_YJIT_ENABLE`
  and `RUBYOPT` are recorded as environment, and a difference is a
  warning.
- For a short-lived command (a CLI, a test run), the interpreter and the
  start-up are what the user waits for: time the command (`wall`) and do
  not warm in process.

Not measured: Rails-sized code paths, JRuby and TruffleRuby, `--zjit`,
`--yjit-call-threshold` values other than the default, a quiet machine,
and any machine but the one above.

### Warm-up (.NET)

The .NET runtime compiles each method the first time it is called in a
quick tier, counts calls, and recompiles hot methods in an optimising
tier on a background thread after a delay (tiered compilation, with
dynamic profile-guided optimisation and on-stack replacement for loops;
the delay is time, `DOTNET_TC_CallCountingDelayMs`, not calls). Each
execution is a new process: the spec's `warmup` executions do not warm
the next one, and serve as a smoke test, as for Java. The collector is
generational and concurrent by default.

The curve (`studies/dotnet-warmup/run.sh`, summarised by
`studies/warmup_analyze.py` into `studies/dotnet-warmup/results.txt`;
raw iteration times in `studies/dotnet-warmup/runs/`): one workload, an
iteration of 600 string concatenations, 36,000 dictionary updates and
90,000 integer operations (`tests/fixtures/dotnet/prog/App/Work.cs`),
timed 1,500 times in each of 8 fresh processes, one container each, with
tiered compilation on (the default) and 8 with `DOTNET_TieredCompilation=0`.
.NET 10.0.13, linux-arm64, the official SDK image under Docker Desktop
on a Mac that other work was using, one CPU a container, so noise is
higher than a quiet machine's. The iterations are timed inside the
process, so the build and the runtime's start-up are not in them.

- *Tiered on.* The steady iteration took 1.24 ms at the median of the 8
  processes (1.21 to 2.11: one process was slower throughout). The
  first iteration took 7.9 times that at the median (3.4 to 9.4); the
  median over processes of the first 30 iterations was 1.34 times it,
  of the next 30 1.07, and of every later block of 30 up to the 300th
  1.00 to 1.05. The first block that is within 10% of steady is the
  second at the median.
- *Tiered off* (`DOTNET_TieredCompilation=0`, every method compiled once
  with full optimisation). The steady iteration took 1.52 ms (1.47 to
  1.59), 1.23 times the tiered one on this workload, which is a result
  of this workload and not a property of the runtime. The first
  iteration took 3.0 times that (1.8 to 23.1), the first block 1.30,
  the next 1.05.
- *The settle iteration is not a warm-up measure here.* The first
  iteration after which every window of 30 stays within 10% of steady
  has a median of 31 with tiering on and a maximum of 1,470 of 1,500:
  that process drifted by more than the tolerance late in the run
  (noise, a collection), not compilation. A rule that waited for
  "stable" would stop on noise.

What follows for a .NET benchmark:

- **A fixed warm-up, no stop rule.** The in-process loop runs 300
  warm-up iterations, then 30 timed ones, and reports their median,
  printed with all its digits. It never looks at stability, so no
  execution can end in "did not settle"; a workload that never settles
  still ends after the same number of iterations, and its spread is what
  the interval widens by. A non-zero exit, a failed execution and a
  REJECT on correctness for a candidate, comes only from wrong output.
  The template is in `skills/perfproof/csharp.md`.
- **Keep an iteration at 1 ms or more.** Promotion waits on a timer as
  well as on calls, so a count of iterations is only a count of
  seconds if an iteration has some length. The curve above was measured
  at 1.2 ms an iteration, so 300 iterations are 0.4 s there, and it was
  within 10% of steady by the end of the second block of 30. Whether the
  delay setting moves that was not measured. An execution costs `(300 +
  30) x seconds per iteration`, so at 50 ms an iteration it is 16 s and
  a round of two executions about 33 s: against the default
  `max_seconds` of 1800 and a first look at 10 rounds, an iteration
  must be well under 50 ms or `max_seconds` raised, with the reason
  said. One curve of one small workload does not show that 300 is
  enough for a large service; measure the real workload's curve when the
  first iterations matter.
- Both versions get the same `DOTNET_TieredCompilation`,
  `DOTNET_TieredPGO`, `DOTNET_TC_QuickJitForLoops`, `DOTNET_ReadyToRun`,
  `DOTNET_gcServer`, `DOTNET_gcConcurrent` and `DOTNET_GCgen0size`;
  they are recorded with their values, and a difference is a warning.
- A short-lived command (a CLI, a test run) pays the JIT and the
  start-up every time: time `dotnet bin/App.dll` (`wall`) and do not
  warm in process. Build in `setup` and time the application, not
  `dotnet run`, which restores, builds and starts the program as a child
  on every execution.

Not measured: ASP.NET services, ReadyToRun or Native AOT builds, Server
GC, `DOTNET_TC_CallCountingDelayMs` and `DOTNET_TieredPGO` other than
the default, other runtimes than 10.0.13, a quiet machine, and any
machine but the one above.

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
scenario among its audit ranges; the skew probe outside them (T1.13,
`tests/calibration/skew.py`) fails 11 of 168 cells on the shipped
procedure at each recorded seed, 8 of them on the three class
schedules (stats-auditor recount of the committed CSVs, 2026-10-28),
and is recorded, not accepted. The real traces show the limit too: the
stats-auditor attributed the failing `linux-llm` off-class cells under
"Real noise" to that trace's burst segment, whose mean (-0.613%) sits
below the trace's overall mean (-0.073%): replicates are centred on the
overall mean, so the quiet pairs sit about 0.060% above `S` (the
auditor's ablation: dropping the first 36 pairs cuts false KEEPs at
1.03 from 143 to 9 on `python_native`), the skew limit this paragraph
describes. The trace's 16 extreme pairs are 10 negative and 6 positive,
and an arm-swapped replay gives at most 0.93%. No committed artifact
holds these measurements. So the primary
is not shown to cover under skew, and where a workload has it the
coverage is expected to fail on the skewed side; the false-KEEP rates
measured on those cells are named limits there, not acceptance. The
record reports the skewness of
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

**Diagnostic gate (T1.8).** *(The stats layer's interval is now `hac_lf`,
specified under "The T1.11 interval" below, which has no gate; this
gate and its diagnostics stay what `bench/compare.py` records until the
schema task.)* Blocks of 6 still under-cover in two
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

**Primary procedure, fixed in advance.** Batch means, the interval of
every schedule `compare` does not give to `hac_lf` ("The T1.11
decision" below: `hac_lf` decides the python_native, web_agentic and
llm_serving default schedules). At a look with
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

### The T1.11 interval (`hac_lf`)

Decided by the owner after T1.11 phase 1 (PR #57) and the real A/A
traces of phase 1b. On the three default schedules other than `gpu_kernels` ("The T1.11
decision") it replaces batch means and the diagnostic gate
as the interval of a look; the stats layer implements it as procedure
`hac_lf` (`perfproof.stats.intervals.hac_lf_interval`, driven by
`run_looks(procedure="hac_lf")`). **Status:** `bench/compare.py` decides
those schedules with it and records `hac_lf` as the verdict's
`procedure`; the diagnostics `autocorrelated_rounds` and `outlier_rounds`
are null there, since it has no gate. The per-look lag-1, its upper
bound and the limit used are computed (`Look.lag1`, `Look.ucb`,
`Look.rho_limit`) but not yet in the record; the record's
`lag1_autocorrelation` is the lag-1 of the log ratios as before.
`CALIBRATED` stayed False at that point (it is True since the flip
of 2026-10-28).

**Why.** Two measurements. (1) With critical values calibrated for
independent noise, every HAC, bootstrap or batch-means interval failed
the acceptance rule in AR(1) scenarios with `rho` 0.6 to 0.7 at 12 to
60 observations (T1.11 phase 1): the sample holds too little
information about dependence for any interval to be valid there
without a wider critical value. (2) On 19 real A/A traces (Hetzner
Linux, a Linux VM and a Mac, quiet and under steady and bursty load,
six workloads) the lag-1 autocorrelation of `d` is between -0.09 and
+0.13, and interleaving cancels shared load. The gate, and a critical
value widened for `rho` 0.7 everywhere, paid a large power cost for a
dependence the real traces do not show. The design widens in
proportion to the dependence the run's own data cannot rule out,
continuously, and never withholds.

**The interval.** At a look with `n >= 12` observations (rounds on one
host, even; session pairs cross host):

1. `d_i = log r_i`. On one host `u_i = d_i - m_o(i)` where `m_o` is
   the mean of `d` over the rounds that ran in the same order as round
   `i` (order centring, as the diagnostics used); cross host `u_i` is
   `d_i` minus its mean.
2. The estimate is the mean `d_bar` over all `n` observations.
3. Its variance is the Newey-West (Bartlett) estimate on `u`:
   `V = (g_0 + 2 sum_{j=1}^{M-1} (1 - j/M) g_j) / n` with
   `g_j = sum_{i<=n-j} u_i u_{i+j} / n` and `M = max(2, ceil(0.1 n))`
   (at most `n - 1`; `ceil` of the IEEE double product `0.1 n`, the
   form the table was built with), a fixed fraction `b = M / n` of
   about 0.1. The Bartlett kernel makes `V` non-negative.
4. `r` is the lag-1 autocorrelation of `u`, `sum u_i u_{i+1} / sum u_i^2`
   (0 when `u` is all zero), and its **upper confidence bound** is
   `ucb = r + z sqrt((1 - r^2) / n)` with `z` = `UCB_Z` below, `r`
   first raised by its rounding bound.
5. The look's **`rho` limit** is `max(0.3, ucb)` rounded up to the next
   tabulated limit (0.3, 0.4, ..., 0.9; the largest if `ucb` is above
   0.9). 0.3 is about twice the largest paired lag-1 seen on a real
   trace (+0.13).
6. The interval for `E[d]` is `d_bar -/+ c sqrt(V)`, exponentiated,
   where `c` is the table's critical value for the look's per-look
   confidence `1 - alpha / (2K)`, host mode, `n`, `rho` limit and `r`.

Every class of target uses the same rule and the same floor. A class
with real A/A traces does not get a different table, and a class
without traces does not get a blanket wider one: the caution comes
from the run's own `ucb`, which is wide at the small `n` of a short
run and narrows as rounds accumulate. A machine nobody measured whose
paired rounds depend on each other is caught by its own data, as early
as `n` allows, and widens the interval continuously. Nothing is
withheld: the look is decided on the interval it computed, and the
look records `r`, `ucb` and the limit it used.

There is no outlier diagnostic and no robust centre: the estimand
stays `exp(E[d])`, and heavy tails and outliers cost power through `V`
and `c` rather than a withheld look. (A Huber centre was far more
powerful under symmetric outliers in phase 1 but targets the centre of
symmetry of `d`, not `E[d]`: under one-arm skew, and on the real
`linux-membership` trace, it fails the acceptance rule. It stays
out.) The covariate adjustment of phase 1 is not in the procedure: the
recorded load was identical for the two runs of every pair in every
real trace, so it had nothing to adjust.

**The critical-value table.** For each host mode, look length `n` in
a set of tabulated lengths (12 to 30 in steps of 2, then 36, 42, 48,
54, 60, 72, 84, 96, 108, 120), per-look confidence for
`K` in 1, 2, 3, 4, 6, 8 and 12 looks (`1 - 0.05 / (2K)`), `rho` limit
and bin of the sample lag-1 (bins of width 0.1 from -0.5 to 0.9, open
ended, 16 in all):

- Draws: AR(1) Gaussian noise with unit variance, `rho` in
  `{0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9}`
  with the design's order structure (balanced order pairs, a coin per
  pair, one host; one group cross host), `N` draws per `rho`, a fixed
  seed. Per draw the pivot `|d_bar| / sqrt(V)` (true `E[d] = 0`) and
  `r`.
- A value vector `c_b` (one per bin) is **valid for a limit** when, for
  every `rho` in the grid up to the limit, the unconditional share of
  draws with pivot above `c_{bin(r)}` is at most `1 - confidence`: the
  interval is valid at its own `n` for every such `rho`, which is the
  condition the argument under "Stopping" uses. Among valid vectors the
  builder takes the one a greedy search reaches: start every bin at the
  independent-noise quantile and, while some `rho` is over its budget,
  raise by 0.02 the bin that contributes most to the miss rate of the
  worst `rho`. A bin that the data of a `rho` rarely visit costs nothing
  for that `rho`; the width goes where dependence shows up.
- Values are then made nondecreasing in `r` and in the limit, and
  nonincreasing in `n` over the tabulated lengths (running maxima from
  the largest `n` down), and rounded up to 3 decimals.
- Lookup is **conservative by construction**: `n` takes the largest
  tabulated length not above it (120 for any larger `n`); because the
  stored values never fall as `n` falls, a look at an untabulated `n`
  gets at least the value of the tabulated length above it. A
  confidence takes the smallest tabulated `K` whose confidence is not
  below it (so a custom schedule of 5 looks uses `K = 6`), and above
  `K = 12` there is no table, an error. The `rho` limit is rounded up
  to a tabulated one. `r` is raised by its rounding bound and
  interpolated linearly between the lower edges of its bin and the
  next, which never gives less than the bin's own value.
- The table is built twice from independent seeds; the committed one is
  the first, and the second's entries differ by at most the percentage
  recorded under "Calibration results".

**Rounding.** As for every interval here, no bound comes out more
favourable to the candidate than its exact value because of rounding;
`perfproof.stats.intervals` derives the outward widening for this
procedure from the operations that produce it, with the seminorm
argument in its docstring.

**What it costs and what it does not claim.** At a sample `r` near 0
and a small `n` the critical value is above the `t` quantile, because
the limit 0.3 must be covered at an `n` of 20 to 60. That is the price
of not withholding; "Calibration results" measures it against the T1.8
gate on synthetic scenarios and on the real traces. The floor of 0.3 is
a claim about paired rounds on the machines the traces were recorded
on; `ucb` is the safety net for others, and `UCB_Z` is tuned (below)
so the acceptance rule holds on AR(1) `rho` 0.6 to 0.7. One-version
skew stays a stated limit, as under "Assumptions".

**`UCB_Z`.** The smallest `z` in `{0.5, 1, 1.5, 2, 2.5, 3}` for which
the procedure passes every cell of the full synthetic grid at the
exploratory seed 2631286274093223815 with the committed table; fixed
before the runs at the seeds of record and fresh seed named under
"Calibration results".

**The power constraint.** The owner required the procedure to be no
worse than the T1.8 gated primary in KEEP power at true speedups 1.05,
1.10 and 1.20 and REJECT power at 0.90 and 0.80, on every schedule, in
every synthetic cell in both host modes and on every real trace, within
Monte Carlo error. "Calibration results" gives the smallest difference
in each and where it is negative, with the reason.

### The `bm_lf` interval (T1.11 phase 2b, rejected challenger)

**Status: rejected, removed from shipped code.** `bm_lf` failed acceptance (results below) and the owner chose the split of "The T1.11 decision". Its code (`bm_lf_interval`, `lf_tables_bm.py`, the harness variants and the table builder's `bm` kind) is not in the tree; it is in the history at commit 3338cd3, and its results are the committed `tests/calibration/results/t111p2b-*` files. The text below is the record of what was run.

A second candidate for the same job, fixed before any run of it, because
the owner's constraint (never worse than the T1.8 gated primary in KEEP
power at 1.05, 1.10 and 1.20 and REJECT power at 0.90 and 0.80, on every
schedule, every synthetic cell in both host modes and every real trace)
is hard and `hac_lf` does not meet it on `gpu_kernels`. The hypothesis:
keep the primary's estimator and standard error, and replace only its
gate.

**The interval.** At a look with `n >= 12` observations: the T1.8
batch-means interval of "Speedup and interval", unchanged (blocks of 6
consecutive observations, the mean of `d` over each block is one
observation, a trailing incomplete block dropped, mean and sample
standard deviation of the `m = n // 6` block means), but with the
critical value `c` in place of the `t` quantile on `m - 1` degrees of
freedom: `d_bar -/+ c s / sqrt(m)`, exponentiated. There is no gate and
nothing is withheld.

**The critical value** is the table of the `hac_lf` section built for
this pivot, `|mean of block means| / (s / sqrt(m))`, by the same
construction and the same rules: AR(1) Gaussian noise with `rho` on the
grid, the design's order structure, bins of the sample lag-1 `r` of the
order-centred `d` (all `n` rounds, not the block means), per-look
confidence for `K` in 1, 2, 3, 4, 6, 8, 12, a `rho` limit among 0.3 to
0.9, values valid for every `rho` up to the limit in the unconditional
sense (greedy search), nondecreasing in `r`, in the limit and in `K`,
nonincreasing in `n`, rounded up, with the same conservative lookup
(next tabulated `n` at or below, next `K` at or above, limit rounded up,
`r` raised by its rounding bound and interpolated from lower bin edges).
The look's limit is `max(0.3, ucb)` with the same
`ucb = r + z sqrt((1 - r^2) / n)`, `z` chosen by the same rule on the
exploratory seed, recorded per look (`lag1`, `ucb`, `rho_limit`). The
candidate values are searched on a geometric grid (steps of 0.4%)
because with `m = 2` blocks the `t` quantile is in the hundreds. The
table file is `perfproof.stats.lf_tables_bm`, built by
`tests/calibration/build_lf_tables.py build --kind bm` from seed
2711013974122286542, checked against a second build from seed
1695262030133760947.

**What is decided and how.** Fixed before the runs: the table seeds
above; `z` is the smallest in {0.5, 1, 1.5, 2, 2.5, 3} that passes every
cell of the full synthetic grid at the exploratory seed
2631286274093223815 with the committed table; acceptance at the seed of
record 3598798392042499920 and a fresh seed 2483202639896524218, the
development, exposed and held-out grids, both host modes, `R` = 4000,
and the 19 real traces under Politis-White, fixed 6 and fixed 20
blocks (seed 7257639517108691388); `dominance.py` against the gated
primary in every cell; the busy-machine and skew probes. Outcomes:
(a) `bm_lf` passes acceptance and has no cell worse than the primary by
more than 3 standard errors: it becomes the phase 2 procedure and
`hac_lf` is recorded as the losing challenger; (b) neither procedure
meets the constraint: the smallest-loss design and where it loses is
reported and the decision is the owner's; (c) a per-look choice between
the two would need a table built for that combined rule and acceptance
over it, and is not made here. Both procedures stay in the stats layer;
`bench/compare.py` and `perfproof.schema` are not wired.

### T1.11 results (`bm_lf`, rejected challenger)

Result files: `tests/calibration/results/t111p2b-*` (acceptance:
`t111p2b-acceptance.md`, `t111p2b-real-acceptance.md`; dominance:
`t111p2b-dominance-synthetic-record.md`,
`t111p2b-dominance-synthetic-fresh.md`, `t111p2b-dominance-real.md`; real
trace means: `t111p2b-real-power-mean-pw.md`; table builds:
`t111p2b-table-builds-compare.txt`). Seed of record 3598798392042499920,
fresh seed 2483202639896524218, real-trace seed 7257639517108691388,
exploratory seed for `z` 2631286274093223815, table seeds
2711013974122286542 (committed) and 1695262030133760947 (second build).

**The table.** The two builds differ by a median of 0.40%, a 95th
percentile of 1.98% (one host) and 2.37% (cross host), and a maximum of
10.22% and 10.58%. At a low sample lag-1 the floor equals the `t`
quantile of the primary (5.9 against 5.885, 3.82 against 3.808, 3.25
against 3.255 at the three looks compared).

**`z` (the pre-registered rule).** Six values, 0.5 to 3.0 in steps of
0.5, at the exploratory seed: 12 cells fail at every value, all
`llm_serving` one host in the outlier scenarios (`A00-outliers-only`,
`A00-outliers-5pct-x5-cv1`, `A20261052-00`, `A20261054-08`,
`A20261055-17`), worst count 105 to 107 of 4000 by `z`. The rule
selects no `z`; the evidence runs use 1.0 (the `hac_lf` value). INFERENCE:
22 rounds make three blocks, and the pivot of three block means of an
outlier-contaminated series is not the Gaussian one the table assumes.

**Acceptance** (limit 83 of 4000). Development 0 and 0 failing cells on
the seed of record and the fresh seed (worst 42 and 42), held-out 0 and 0
(worst 42, 42), exposed **11 and 12 failing** (worst 112 and 108, both
`llm_serving/A20261054-08/one_host/1.03`). `hac_lf` and the gated primary
fail no cell (exposed worst: `hac_lf` 67 and 63, primary 62 and 54).
Real traces (152 false-KEEP cells per rule), Politis-White 0 failing
(worst 43), fixed 6 0 (48), fixed 20 **1** (121, `linux-picolm`,
`llm_serving`, 1.03), against `hac_lf` 0, 0, 0 (34, 30, 62) and the
primary 0, 0, 0 (45, 50, 76). Probes outside the audit ranges: busy-machine
0 and 0 failing cells (`hac_lf` 7 and 6, primary 0 and 0), skew 7 and 7
(primary 7 and 7, `hac_lf` 11 and 11).

**The power constraint.** Smallest difference to the gated primary in
percentage points, with the number of cells worse than 3 standard errors
of two proportions:

| Cells | KEEP 1.05 | KEEP 1.10 | KEEP 1.20 | REJECT 0.90 | REJECT 0.80 |
|---|---|---|---|---|---|
| synthetic, record, `bm_lf` | -9.6, 4 | -35.8, 15 | -25.5, 11 | -1.5, 0 | -1.6, 0 |
| synthetic, record, `hac_lf` | -8.4, 69 | -35.4, 22 | -23.4, 9 | +2.4, 0 | +1.9, 0 |
| synthetic, fresh, `bm_lf` | -8.8, 4 | -35.7, 18 | -22.5, 9 | -1.8, 0 | -1.1, 0 |
| synthetic, fresh, `hac_lf` | -7.2, 40 | -35.8, 26 | -20.7, 10 | +2.5, 0 | +2.4, 0 |
| real traces, `bm_lf` | -7.4, 11 | -0.1, 0 | +0.0, 0 | +0.0, 0 | +0.0, 0 |
| real traces, `hac_lf` | -4.7, 10 | -0.2, 0 | +0.0, 0 | +0.0, 0 | +0.0, 0 |

Every `bm_lf` cell worse than 3 standard errors is `gpu_kernels`
(a per-schedule count of the same cells: 4, 15, 11 cells at 1.05, 1.10, 1.20 on the
record seed and 4, 18, 9 on the fresh seed, none on another schedule).
`hac_lf` also has worse cells on `python_native`, `web_agentic` and
`llm_serving` at KEEP 1.05 and 1.10 (by at most 1.8 points). The largest
`bm_lf` deficits are the same `gpu_kernels` cells as for `hac_lf`
(`RHO0.7`, `RHO0.6`, `A20261056-15`, `A20261052-04`, held-out `H3635800-28`
and `-10`), one host. The premise that near its floor the interval
matches the primary on clean noise held; the premise that the primary's
batch means keep their power at high `rho` did not hold.

Real-trace means with Politis-White blocks, KEEP at 1.10 for the primary,
`hac_lf` and `bm_lf`: `python_native` 84.9, 99.0, 98.1; `web_agentic`
51.1, 91.3, 79.6; `llm_serving` 26.0, 81.4, 40.2; `gpu_kernels` 100,
100, 100. Mean rounds at 1.10: 40.5, 25.0, 32.6; 27.5, 22.4, 26.2; 21.7,
18.0, 21.6; 52.8, 27.5, 38.9.

INFERENCE, not shown by a run: the primary's power on `gpu_kernels` at
`rho` of 0.6 and above comes from a smaller critical value than any
interval that keeps the per-look budget over `rho` up to its upper bound
can use; both procedures lose there for that reason, which is why a
per-look choice between them (c) would not close it. Not checked: that a
critical value at a looser level would pass acceptance on those cells.

**Outcome.** (b). `bm_lf` loses power only on `gpu_kernels` (synthetic by
up to 35.8 points at KEEP 1.10, real traces by up to 7.4 points at KEEP
1.05, `mac-node`), is not eligible because it fails acceptance on the
exposed `llm_serving` outlier cells and on one real-trace cell, and is
weaker than `hac_lf` on real `web_agentic` and `llm_serving`. `hac_lf`
passes acceptance everywhere and loses on the same `gpu_kernels` cells.
Neither meets "never worse than the gated primary"; the decision was
the owner's ("The T1.11 decision"): `hac_lf` on the non-GPU
schedules, the gated primary on `gpu_kernels`, and `bm_lf` removed.

### The T1.11 decision (split by schedule)

**Decision.** Faras (the owner), 2026-10-22, confirmed 2026-10-23 after
the T1.12 golden set: the interval is chosen by the spec's look
schedule, which is fixed before any data.

- `hac_lf` for the three default schedules of the calibration's
  `python_native`, `web_agentic` and `llm_serving` schedules
  (`TargetSchedule` in `tests/calibration/montecarlo.py`), identified by
  `(n0, k, K, max_rounds)`: `(10, 10, 6, 60)`, `(10, 10, 3, 30)` and
  `(10, 6, 3, 22)`. They are the defaults of Python, native, web
  service, agentic and LLM serving targets ("Defaults per target
  class").
- The T1.8 gated primary (batch means of 6 with the diagnostic gate)
  for the `gpu_kernels` schedule, `(20, 20, 6, 120)`, and for any other
  schedule.

`perfproof.stats.sequential.procedure_for_schedule` is the one place
that maps a schedule to a procedure, and `bench/compare.py` calls it
once, from the spec, before the first round; the verdict records the
result as `procedure`. **Unknown schedule.** A schedule that is none
of the four, because a spec set its own `n0`, `k`, `K` or `max_rounds`,
is decided by the gated primary. It never raises, so an unknown
schedule cannot stop a comparison. That is the fail-closed choice, not
a guarantee: neither procedure was calibrated on a schedule off the
four. The gated primary is the more cautious one there, because its
diagnostic gate can withhold a decision that `hac_lf` would make, and
it is the procedure the 120-round rule under "A schedule outside the
calibrated ones" was written for (INFERENCE: not measured on custom
schedules). `hac_lf` was accepted only on the three schedules above and
loses power on GPU kernels. The cost is that a spec that edits its
schedule, or sets `alpha` other than 0.05, gives up `hac_lf`'s power
(a smaller `alpha` would put the per-look confidence above the last
level of `hac_lf`'s table, which stops the run, so it must not reach
`hac_lf`). The time budget (`max_seconds`) is not part of the key: a
run it ends takes a final look on complete order pairs, which is not a
scheduled look, but the calibration harness cuts runs by the time
budget in the same way (`cut_by_time`), so those looks are inside the
cells above. A test holds the production table equal to the
calibration's schedules, and another holds each schedule to its
procedure. `CALIBRATED` stayed False then: the flip was a separate step
after the guardrails (T2.15) and a stats-auditor pass (done 2026-10-28).

**Why.** Neither `hac_lf` nor `bm_lf` meets the owner's constraint of
never being worse than the gated primary on any cell. Both lose on
`gpu_kernels` at `rho` 0.6 and above, where the primary's power rests on
an interval narrower than any that holds its level (INFERENCE, see the
results above). On the other three schedules `hac_lf` passes every
acceptance grid and has more power on the real traces (mean KEEP at
1.10 with Politis-White blocks, `hac_lf` against the primary:
`python_native` 99.0 against 84.9, `web_agentic` 91.3 against 51.1,
`llm_serving` 81.4 against 26.0; `t111p2b-real-power-mean-pw.md`). The owner
accepted the loss on non-GPU cells, and GPU keeps the primary, which is
identical there by construction.

**Accepted tolerance.** On synthetic non-GPU cells `hac_lf` may be below
the gated primary by at most 1.8 percentage points, and there is no
cell below it by more than 3 standard errors on any real trace.
Measured (tables below): the smallest synthetic difference is -1.8
points (fresh seed, `python_native`, KEEP 1.05) and the real-trace one
-1.5 points (`python_native`, fixed 6 blocks, KEEP 1.05), with 0
real-trace cells beyond 3 standard errors. On synthetic cells there are
cells beyond 3 standard errors (the conservative count: up to 29 of 108
per schedule at KEEP 1.05); the tolerance is on the size of the
difference, not on that count. REJECT power is higher than the
primary's on every schedule.

**Each procedure keeps its own guarantee.** The schedule, and with it the
procedure, is fixed before any round, so no data-dependent choice
between the two enters a verdict. Acceptance (the rule above) was shown
for each procedure on the schedules it covers, from the same runs; the
tables below take each cell from the procedure of its schedule. Those
tables are assembled from the runs of each procedure; "T1.13 results"
runs the shipped selection itself, through `run_looks`, at a fresh
seed.

**One thing the split does worse than the primary.** The skew probe
(one version's runs partly slowed, outside the audit ranges): the
primary fails 7 of 168 cells on each seed and the split fails 11, the
four extra being non-GPU cells (table below). The audit does not cover
this probe and the primary fails it too; it is recorded, not accepted by
the decision.

Tables made by `tests/calibration/split_report.py` from the committed
`t111p2b-*` CSVs (`tests/calibration/results/t111-split-tables.md`).
The row `split` is the split as decided; `batch_means_6` and `hac_lf`
are shown for the real traces to make the construction visible.

**Acceptance, synthetic grids**

| Seed | Grid | Cells | Failing | Worst false KEEP | Worst false REJECT |
|---|---|---|---|---|---|
| record (3598) | main/development | 560 | 0 | 47 gpu_kernels/lognormal_cv5/cross_host/1.03 | 23 gpu_kernels/bimodal/cross_host/1.04 |
| record (3598) | main/exposed | 784 | 0 | 67 llm_serving/A20261052-04/one_host/1.03 | 52 llm_serving/A20261052-04/one_host/1.04 |
| record (3598) | heldout/heldout | 1680 | 0 | 44 gpu_kernels/H3635800-28/cross_host/1.03 | 25 gpu_kernels/H3635800-03/one_host/1.04 |
| record (3598) | cond/probe | 252 | 0 | 32 python_native/COND-armbias-base-poorcov/one_host/1.03 | 31 python_native/COND-armbias-cand/one_host/1.05 |
| record (3598) | skew/probe | 168 | 11 | 293 python_native/SKEW-cand-5pct-x2/one_host/1.03 | 125 python_native/SKEW-base-5pct-x2/one_host/1.04 |
| fresh (2483) | main/development | 560 | 0 | 48 gpu_kernels/bimodal/cross_host/1.03 | 22 gpu_kernels/lognormal_cv15/cross_host/1.04 |
| fresh (2483) | main/exposed | 784 | 0 | 60 llm_serving/RHO0.7/one_host/1.03 | 50 web_agentic/A20261052-04/cross_host/1.04 |
| fresh (2483) | heldout/heldout | 1680 | 0 | 48 gpu_kernels/H3635800-15/one_host/1.03 | 28 gpu_kernels/H3635800-01/cross_host/1.04 |
| fresh (2483) | cond/probe | 252 | 0 | 46 python_native/COND-armbias-base-poorcov/one_host/1.03 | 27 python_native/COND-armbias-cand/one_host/1.04 |
| fresh (2483) | skew/probe | 168 | 11 | 281 python_native/SKEW-cand-5pct-x2/one_host/1.03 | 127 python_native/SKEW-base-5pct-x2/one_host/1.04 |

**Failing skew-probe cells by schedule**

| Seed | Schedule | Primary | Split |
|---|---|---|---|
| record (3598) | python_native | 2 | 3 |
| record (3598) | web_agentic | 2 | 3 |
| record (3598) | llm_serving | 0 | 2 |
| record (3598) | gpu_kernels | 3 | 3 |
| fresh (2483) | python_native | 2 | 3 |
| fresh (2483) | web_agentic | 2 | 3 |
| fresh (2483) | llm_serving | 0 | 2 |
| fresh (2483) | gpu_kernels | 3 | 3 |

**Acceptance, real traces**

| Procedure | Rule | Cells (1.00, 1.03) | Failing | Worst false KEEPs |
|---|---|---|---|---|
| split | pw | 152 | 0 | 45 linux-node/pw/gpu_kernels/1.03 |
| batch_means_6 | pw | 152 | 0 | 45 linux-node/pw/gpu_kernels/1.03 |
| hac_lf | pw | 152 | 0 | 34 mac-loaded-rolling/pw/gpu_kernels/1.03 |
| split | fixed6 | 152 | 0 | 50 mac-loaded-node/fixed6/gpu_kernels/1.03 |
| batch_means_6 | fixed6 | 152 | 0 | 50 mac-loaded-node/fixed6/gpu_kernels/1.03 |
| hac_lf | fixed6 | 152 | 0 | 30 mac-loaded-node/fixed6/gpu_kernels/1.03 |
| split | fixed20 | 152 | 0 | 76 linux-picolm/fixed20/gpu_kernels/1.03 |
| batch_means_6 | fixed20 | 152 | 0 | 76 linux-picolm/fixed20/gpu_kernels/1.03 |
| hac_lf | fixed20 | 152 | 0 | 62 linux-java/fixed20/gpu_kernels/1.03 |

**Dominance against the gated primary**
Each entry: the smallest difference of the split to the gated primary in points, then the cells worse than 3 standard errors of the cells at that speedup.

Synthetic, record seed 3598798392042499920 (development, exposed, held-out, power cells; both host modes)

| Schedule | KEEP 1.05 | KEEP 1.10 | KEEP 1.20 | REJECT 0.90 | REJECT 0.80 |
|---|---|---|---|---|---|
| python_native | -1.5, 11 of 108 | -1.6, 1 of 108 | +1.9, 0 of 108 | +5.5, 0 of 20 | +5.8, 0 of 20 |
| web_agentic | -1.2, 29 of 108 | -1.4, 4 of 108 | -0.5, 0 of 108 | +5.9, 0 of 20 | +20.3, 0 of 20 |
| llm_serving | -0.8, 21 of 108 | -1.0, 1 of 108 | -0.2, 0 of 108 | +3.7, 0 of 20 | +25.4, 0 of 20 |
| gpu_kernels | +0.0, 0 of 108 | +0.0, 0 of 108 | +0.0, 0 of 108 | +0.0, 0 of 20 | +0.0, 0 of 20 |

Synthetic, fresh seed 2483202639896524218 (development, exposed, held-out, power cells; both host modes)

| Schedule | KEEP 1.05 | KEEP 1.10 | KEEP 1.20 | REJECT 0.90 | REJECT 0.80 |
|---|---|---|---|---|---|
| python_native | -1.8, 6 of 108 | -1.5, 0 of 108 | +2.4, 0 of 108 | +5.6, 0 of 20 | +5.8, 0 of 20 |
| web_agentic | -1.0, 19 of 108 | -1.5, 3 of 108 | +0.6, 0 of 108 | +6.7, 0 of 20 | +20.5, 0 of 20 |
| llm_serving | -0.9, 11 of 108 | -0.8, 3 of 108 | +0.5, 0 of 108 | +3.8, 0 of 20 | +24.7, 0 of 20 |
| gpu_kernels | +0.0, 0 of 108 | +0.0, 0 of 108 | +0.0, 0 of 108 | +0.0, 0 of 20 | +0.0, 0 of 20 |

Real traces, block rule pw, seed 7257639517108691388

| Schedule | KEEP 1.05 | KEEP 1.10 | KEEP 1.20 | REJECT 0.90 | REJECT 0.80 |
|---|---|---|---|---|---|
| python_native | -0.8, 0 of 19 | +5.4, 0 of 19 | +4.8, 0 of 19 | +5.5, 0 of 19 | +5.2, 0 of 19 |
| web_agentic | -0.1, 0 of 19 | +24.3, 0 of 19 | +22.2, 0 of 19 | +21.7, 0 of 19 | +20.0, 0 of 19 |
| llm_serving | +0.8, 0 of 19 | +29.1, 0 of 19 | +30.6, 0 of 19 | +30.2, 0 of 19 | +29.4, 0 of 19 |
| gpu_kernels | +0.0, 0 of 19 | +0.0, 0 of 19 | +0.0, 0 of 19 | +0.0, 0 of 19 | +0.0, 0 of 19 |

Real traces, block rule fixed6, seed 7257639517108691388

| Schedule | KEEP 1.05 | KEEP 1.10 | KEEP 1.20 | REJECT 0.90 | REJECT 0.80 |
|---|---|---|---|---|---|
| python_native | -1.5, 0 of 19 | +4.6, 0 of 19 | +4.4, 0 of 19 | +4.1, 0 of 19 | +4.6, 0 of 19 |
| web_agentic | -1.1, 0 of 19 | +25.3, 0 of 19 | +19.8, 0 of 19 | +20.5, 0 of 19 | +17.8, 0 of 19 |
| llm_serving | -0.4, 0 of 19 | +26.4, 0 of 19 | +25.9, 0 of 19 | +25.1, 0 of 19 | +24.5, 0 of 19 |
| gpu_kernels | +0.0, 0 of 19 | +0.0, 0 of 19 | +0.0, 0 of 19 | +0.0, 0 of 19 | +0.0, 0 of 19 |

Real traces, block rule fixed20, seed 7257639517108691388

| Schedule | KEEP 1.05 | KEEP 1.10 | KEEP 1.20 | REJECT 0.90 | REJECT 0.80 |
|---|---|---|---|---|---|
| python_native | +0.1, 0 of 19 | +3.2, 0 of 19 | +2.6, 0 of 19 | +2.2, 0 of 19 | +2.8, 0 of 19 |
| web_agentic | -0.9, 0 of 19 | +19.1, 0 of 19 | +18.9, 0 of 19 | +18.2, 0 of 19 | +14.8, 0 of 19 |
| llm_serving | -0.1, 0 of 19 | +24.0, 0 of 19 | +20.4, 0 of 19 | +23.0, 0 of 19 | +21.3, 0 of 19 |
| gpu_kernels | +0.0, 0 of 19 | +0.0, 0 of 19 | +0.0, 0 of 19 | +0.0, 0 of 19 | +0.0, 0 of 19 |

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
  procedure that was uncalibrated, INCONCLUSIVE
  (`insufficient_evidence`; a calibrated one can give any verdict); a run with fewer than `n0` rounds has no
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
| Node | 10 | 10 | 6 | 60 | 1800 | As Python; whole-command `wall` timing only for a calibrated KEEP |
| JVM | 10 | 10 | 6 | 60 | 1800 | As Python; whole-command `wall` timing only for a calibrated KEEP |
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

**Which interval decides.** The interval of rules 2 and 3 is the one
`procedure_for_schedule` gives the spec's look schedule, before any
round ("The T1.11 decision" under "Speedup and interval"): `hac_lf` for
the python_native, web_agentic and llm_serving default schedules at
`alpha` 0.05, the gated batch means for gpu_kernels and for every other
schedule or `alpha`. The rules, the bar and the table below are the same for both; only the
interval and, for batch means, the gate differ. `hac_lf` never
withholds a look.

**Before calibration.** (Historical: `CALIBRATED` is True from the
flip of 2026-10-28, under "The calibrated domain", and rules 2 and 3
decide. What follows is what a record made with `CALIBRATED` False
shows.) Until the procedure has passed calibration
(see "Calibration"), rules 2 and 3 never match. A comparison runs to
the end of its budget and ends **INCONCLUSIVE**
(`insufficient_evidence`), with the diagnostic
`procedure_uncalibrated` recorded. Rule 1 still gives REJECT when a
gate fails, and the resolution stop still ends it early. Under
batch means the diagnostic gate under "Assumptions" is still evaluated
on the rule the interval gives as if calibrated (`hac_lf` has no gate),
so the record names the diagnostic
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
| Peak memory: the largest single process's peak resident set size | every target class | 10% |
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

**What peak memory is.** The largest single process's peak resident
set size: `ru_maxrss` of the benchmark process as `wait4` reports it,
which is the maximum over that process and the descendants it waited
for, never a sum. A descendant nobody waited for, or one that outlived
the benchmark, is not in it, and two processes of 100 MB each count as
100 MB, not 200 MB. It is not the peak of the whole process tree, which
this page said before T2.15; the owner directed on 2026-10-23 that
perfproof measure on a normal busy machine, which rules out the tree
peak and an unmeasurable default: a tree peak needs a cgroup (`memory.peak` on Linux, where
one can be created and read) or polling the benchmark, and perfproof
never polls while a round runs. The tree peak is not built; it is a
follow-up where cgroup v2 is available. The record names what was
measured (`measure` on the guardrail's result), so the name
`peak_memory` never stands for two quantities.

macOS reports a child's own peak. Linux gives a child the high-water
mark of the address space it was started from and keeps it across
`exec`. A benchmark started from perfproof itself, a NumPy-sized
process, would report at least perfproof's own peak, and every smaller
benchmark would be `unmeasurable`. So on Linux each execution is
started, timed and reaped by a small helper process
(`perfproof.bench.launch_helper`: a bare Python process of a few
megabytes that imports nothing else), and the benchmark's mark starts
from the helper's. The helper reports that floor, its own high-water
mark read just before the spawn; a peak at or below it (a bare
interpreter, about 7 to 10 MB on arm64) is unknown, and the guardrail
is then `unmeasurable` with that reason, never measured on the rounds
that remain. The helper does the timing around the same spawn and wait
as the direct path, started before the timed region, so wall and CPU
times change by no more than this, measured on Linux arm64 in Docker, for a
trivial command (60 rounds per arm, medians of wall time), the helper
ran from 0.13 ms faster to 0.27 ms slower than the direct path in two
repetitions (4.56 vs 4.42 ms, 3.75 vs 4.02 ms), a difference of the
size of the noise (standard deviations 0.2 to 3 ms) and about 7% of a
bare-interpreter run at worst, negligible for a benchmark of 100 ms. A
helper that used `fork` added 0.3 ms to every execution on top of that
and was dropped. Under user-mode emulation (amd64
containers on Apple silicon) `posix_spawn` does not report an `exec`
failure, so the helper probes (before its clock starts) and falls back to `fork` and
`execve` with an error pipe there.

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
ratio, and on a robust statistic of them, not their mean: compliance is
shown when the upper bound of that statistic is within the limit, a
breach when its lower bound is beyond it, and the guardrail is
`not_established` only while the bounds straddle the limit. A change can
sit within its relative allowance and still breach an absolute limit.

**The owner's decision on limits (Faras, 2026-10-23).** Absolute caps
are judged on a robust, rank-based bound of the candidate's own
statistic, and the numbers are always reported. This replaces the
earlier rule that held the guardrail whenever the sample showed
outliers (that rule is gone; "Guardrail calibration results" records
why). Three parts:

1. **The statistic.** The median of the candidate's values, or the
   quantile the project declares for the limit (`limit_quantile` on the
   guardrail spec). Only median caps are calibrated. A declared quantile
   is passed through but is not calibrated, and a quantile near 1 has no
   bound at the default budgets (a p99 cap needs 546 to 684 independent
   rounds; budgets reach 120): a limit-only p99 guardrail is then
   recorded `unmeasurable` (no `reliable_value`; `typical` and
   `rounds_over` are reported), and one with an allowance is
   `not_established`; a KEEP is withheld either way. A few wild rounds move
   an order statistic only by changing its rank, so a spike does not
   decide the check; a hard ceiling that no single execution may cross
   (an out-of-memory limit) is a different claim, about the maximum,
   that this check does not make, and a project that needs it should
   declare a quantile near 1 and run enough rounds for a bound to exist.
2. **The bound.** The exact distribution-free order-statistic bounds
   for a quantile `q` of `n` values: the upper bound is the `r`-th
   smallest value with `r` least such that `P(Bin(n, q) >= r) <=
   tail`, the lower bound the `s`-th with `s` largest such that
   `P(Bin(n, q) <= s - 1) <= tail`, at `tail = alpha / (4 K G)` on each
   side (confidence `1 - alpha / (2 K G)` two-sided, the guardrail's
   own), computed exactly in rational arithmetic (`rank_bounds`). They
   assume nothing of the shape of the values. They do assume the rounds
   are exchangeable, and rounds are dependent, so the indices are taken
   at an effective count: with `rho` the upper confidence bound (2
   standard errors, the `hac_lf` z doubled, clipped to 0.99) on the lag-1
   autocorrelation of the values' ranks (average ranks, each round
   centred on the mean rank of its order, as the primary's diagnostic
   does), `kappa = (1 + rho) / (1 - rho)` is the variance inflation of
   the mean of an AR(1) series and `n' = floor(n / kappa)` the
   independent rounds' worth; `rank_bounds(n')` gives indices `(s, r)`
   that map back to `s n / n'` rounded down and `r n / n'` rounded up of
   the `n` sorted values (`limit_bounds`). Blocking the rounds and
   taking the median of each block was tried and dropped: under an order
   effect or a slow mode a block's average sits between the modes, and
   its median is not the series' median (coverage failures of 16% at
   one exposed scenario against a 0.4% design). No bound exists until `n'`
   is large enough for both indices (for the median at the usual
   confidences 8 to 10 independent rounds; for a quantile near 1,
   hundreds), and until one does the guardrail is held at INCONCLUSIVE
   as `not_established` is. The schema cannot record `not_established`
   without an interval (an interval no check reads establishes nothing),
   so a limit-only guardrail with no bound yet is recorded
   `unmeasurable`, with the reason and the `standing` numbers that exist
   (typical, rounds over).
3. **The numbers.** For every limit, whatever its status, the record
   carries (`standing` on the guardrail's result, `LimitStanding`):
   - `typical`: the sample's own median, or declared quantile;
   - `reliable_value`: the one-sided bound in the cap's direction (the
     upper bound for a cap, the lower for a floor), at the guardrail's
     design confidence, which is shown for rounds that behave like a
     stable machine and is not claimed for drift (see "Scope of the
     claim"); `null` until a bound exists, and for a guardrail
     recorded `unmeasurable`;
   - `limit` and `gap = reliable_value - limit`, signed (negative: the
     cap is met with that margin, for a ceiling; positive: short by
     that much);
   - `rounds_over` of `rounds`: how many rounds were beyond the cap.

   The report says it in one line per limit, for example "median 498 MB;
   in these runs it stayed under 512 MB (cap 500 MB, 12 MB short); 4 of
   40 rounds over the cap. On a busier or quieter machine this number
   can move". `reliable_value` is information, not a verdict. The
   status is read off the recorded interval, which is the bounds, and
   `reliable_value` is its one side; it never by itself causes a KEEP,
   and it never turns a `not_established` or `breached` guardrail into a
   pass.

**Scope of the claim (lead decision, T2.15).** The bound covers rounds
that behave like a stable machine: values whose dependence the AR(1)
widening describes. An absolute level is a property of the machine's
state during the session as well as of the code, and 120 rounds cannot
learn a long-run level that drifts. So the claim, in the record's text
and in the report, is "in these runs, on the machine as it was", never
an unconditional "reliably under". The confidence printed with the
cap bound (the record's `candidate.confidence`) is the design level for
that case and is not shown for drift. Known limits, measured by the
audits: on persistent series the bound misses its tail (local-level
10.5% to 12.1%, regime 1.5% to 5.9% at fixed `n = 120`, over two audit
runs, against 0.21%; on the repo's real A/A traces at `n = 120`, loaded
Linux rows up to 10.9% and 18.4%, Mac rows up to 19.5% and 32.2%, 14 of
16 Mac rows at `n = 120` above 4 times the tail (28 of 32 counts `n` =
60 and 120 together); verdict-level false passes at 1.01 of 62 to 391
of 4000 across the audits, and 781 of 4000 in a period-150
oscillation), and the failed replacements are recorded under
"Guardrail calibration results". The `CALIBRATED` flip for guardrail
caps does not claim coverage under persistent drift. No diagnostic that
already exists detects drift in the candidate's own values (the gate on
the allowance's log ratios applies only to batch means and to the
ratios; the effective-rounds count is below `n` on ordinary data), so
nothing flags it and nothing here changes a status.

The earlier decision that the guardrail hold its verdict on outliers
(B1, same date) is superseded, and so is its schema consequence: a limit
is no longer recorded `unmeasurable` for outliers. The gated batch means
procedure still holds an *allowance* look, as the primary's gate does.

**Fail closed without guardrails.** `decide` takes the guardrail
actions as an argument; `guardrails=None` is not "no guardrails", it is
unchecked: a KEEP is then withheld as INCONCLUSIVE (`insufficient_evidence`,
rule 2) with the guardrail `unchecked` named, never given. The empty
mapping means the spec has none to check.

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
  it, whether or not another guardrail is `not_established`. An
  applicable metric that can't be measured never passes silently, and a
  default one nobody declared is not passed either: declaring it, with
  an allowance, in a new spec is what makes the change keepable.

The precedence is written once, in `perfproof.stats.decision`
(`guardrail_actions`): a breach, then a blocker no round lifts (a gate
`not_available`, a guardrail `unmeasurable` or `not_declared`), then a
guardrail not yet established, then KEEP. A breach stays a REJECT even
when a gate is `not_available`, because a REJECT keeps nothing.

A performance REJECT or INCONCLUSIVE stands as it is, with the
guardrails reported alongside. Requiring compliance to be shown, not
merely not disproved, means a noisy guardrail such as p99 can hold a
change at INCONCLUSIVE; the evidence planning report names it as the
blocker.

**Which interval.** A check is decided by the primary's own code
(`decide_look`), with the arms ordered so that the worsening ratio is
the speedup it computes and the allowance as its `min_effect` (0 for a
limit), so the interval, its outward rounding, the widening for
resolution and the one comparison with the bar are the primary's.

- **Allowance.** The worsening ratio of a round is `candidate /
  baseline` when lower is better and `baseline / candidate` when higher
  is better, paired by round, so above 1 is worse. Its interval is
  within the allowance `a` when `U <= 1 + a` and breached when `L > 1 +
  a`.
- **Limit.** The rank bounds above on the candidate's own values, not
  paired with the baseline and not a ratio: within a ceiling when the
  upper bound is at most the limit, breached when the lower bound is
  above it, in the metric's unit, with the comparison exact on the
  recorded interval. All of a guardrail's limits are judged on the one
  pair of bounds the record holds. The resolution step does not widen
  it: an order statistic of the measured values is not biased by a
  step.
- **Procedure.** The primary's: `hac_lf` on the three schedules
  `procedure_for_schedule` gives it, the gated batch means otherwise. Where
  `hac_lf`'s table does not reach the confidence `1 - alpha / (2 K G)`
  (`K G` above 12, its last tabulated look count), the gated batch means
  is used, as for an `alpha` the table cannot reach; the choice depends on
  the spec alone and is made before the first round. Under the gated
  procedure the diagnostic gate applies to the guardrail's own log
  ratios: a look it holds back leaves the allowance check open. A limit
  on the same guardrail is not held: its rank bound decides it (breach
  over the cap, otherwise not established). The record cannot show an
  allowance interval that says otherwise, since the schema ties a status
  to the intervals recorded with it, so that interval is not recorded;
  a guardrail with no limit that ends there is recorded `unmeasurable`,
  with the gate's reason and no interval, and one with a limit keeps the
  limit's interval and numbers.
- **Resolution.** Guardrail values are widened as under "Resolution",
  with the page size as the step of a resident size; a value no larger
  than its step makes the guardrail `unmeasurable`.
- **Missing values.** A guardrail is measured on every round or not at
  all. A round whose value is unknown in either arm makes it
  `unmeasurable`, because the rounds that lack a resident size are the
  small ones, and an interval on those that remain would lean toward
  large.
- **Reported guardrails.** A guardrail the runner does not measure
  (p99 latency, throughput, a project's own metric) is a `reported`
  value: its value for an execution is the number the benchmark prints
  for that execution, as a key of the same `PERFPROOF` line as the
  primary, and perfproof takes it as given. A value left out of the
  line is unknown, so a round missing it in either arm makes the
  guardrail `unmeasurable` (above). The resolution and the widening
  are those of a `reported` primary: the place of the last printed
  digit, with a value whose digits can't be read back taking step 0,
  the warning `resolution_unknown` and no KEEP. So is the continuity
  check, per arm and with the same threshold: when either arm has
  fewer distinct values than half its rounds, the guardrail is
  `unmeasurable` (its reason gives the distinct counts and asks for
  more digits), never `breached` or `passed`, and it only withholds a
  KEEP. It is checked in `compare` before the values reach
  `perfproof.stats.guardrails`.
- **One host.** Guardrails are decided on one host. A cross-host
  guardrail has no calibration, and `compare` refuses cross-host runs.

**Error control.** Let `G` count every allowance and limit checked. Each
guardrail check at a look uses confidence `1 - alpha / (2 K G)`, with
the same margin as the primary under "Stopping". By the union bound,
the chance of a KEEP while some guardrail is truly beyond its
allowance or limit is at most 1.25%, and the chance that a guardrail
shows a breach when all are truly within is at most 1.25%; both are
held to 2.5% in calibration. These bounds are for rounds that behave
like a stable machine (the simulator's dependence); for an absolute
limit under persistent drift they are measured false, see "Scope of the
claim" above. The primary interval and its false-KEEP bound are
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
- Every cell must pass. (Amended 2026-10-28 for the real traces only,
  by the owner after results: "every cell" there means the in-class
  cells; see "Real noise".) A failing cell can't be dropped, rerun at a
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

### The calibrated domain

Calibration supports the procedures under the conditions tested, and
`CALIBRATED` (True from 2026-10-28) claims no more than that. The
domain is:

- **`alpha` 0.05**, the default. The acceptance rule was run at no other
  level, and the gated primary breaches the 2.5% limit at `alpha` 0.1
  and 0.2 (stats-auditor on PR #85).
- **One of three class schedules**, as `(n0, k, K, max_rounds)`
  (`node` and `jvm` use the first, and are calibrated on a `wall`
  primary metric only, "Named limits" below):
  `python_native` `(10, 10, 6, 60)`, `web_agentic` `(10, 10, 3, 30)` and
  `llm_serving` `(10, 6, 3, 22)`, each decided by `hac_lf` ("The T1.11
  decision") and accepted on its own class's real traces ("Real noise").
  The time budget is not part of the key (calibration cuts runs by it).
- **Not yet: `gpu_kernels`** `(20, 20, 6, 120)`. It has no real traces,
  so it is pending them and outside the domain: the owner will record
  GPU traces on a rented GPU before launch, and until they pass the rule
  KEEP is withheld on this schedule.

Outside it, any other schedule, the gpu_kernels schedule, or any `alpha`
but 0.05, there is no acceptance evidence, so perfproof withholds the
KEEP and nothing else. The comparison runs, measures and reports as
usual, the interval is recorded, and a REJECT (a failed gate, a breached
guardrail, or a rule 3 interval below the bar) is decided as in the
domain. Where rule 2 would keep, the outcome is INCONCLUSIVE with reason
`insufficient_evidence` and the warning `schedule_uncalibrated`, which
says why: for `gpu_kernels` that it has no real traces yet, otherwise
the `alpha` and the schedule. Either way it says the verdict can never
be a KEEP. The code is
`perfproof.stats.sequential.in_calibrated_domain`, used by
`keep_blocked` in `bench/compare.py`. A schedule with more than one look
at 120 observations or more is also withheld ("Stopping"), inside or
outside the list. A REJECT is not withheld outside the domain, and its
false-REJECT rate there was not measured either; the report's claim for
it is as scoped as for a KEEP. The acceptance rule's cells are about the
domain only, and the off-class limits under "Real noise" hold inside it.

**The class and its schedule.** A spec declares `target_class`
(`BenchmarkSpec`, optional, left out of `spec_hash` while unset). One
map, `CLASS_SCHEDULES` in `perfproof.stats.sequential`, gives each
class its own default schedule, from "Defaults per target class":
`python_cpu` (Python, CPU-bound), `native` (Native), `node` and `jvm`
`(10, 10, 6, 60)` (the `python_native` schedule); `web_service` (Web services) and
`agentic` (Agentic apps) `(10, 10, 3, 30)` (`web_agentic`);
`llm_serving` (LLM serving) `(10, 6, 3, 22)`; `gpu_kernel` (GPU kernels)
`(20, 20, 6, 120)`. The same classes are named for the three `hac_lf`
schedules under "The T1.11 decision".

**What the field describes.** `calibration` is a property of the spec
(class, schedule, `alpha`), not of the rule that decided: every verdict
of a spec carries it, a gate REJECT (rule 1, decided on correctness or
integrity evidence, no interval) and the resolution stop included.

**When a verdict is `calibrated`.** Exactly when the spec declares a
`target_class`, the project model confirms it, its schedule is that
class's own (`CLASS_SCHEDULES`), `alpha` is 0.05, the schedule is in
the domain above, and the primary metric is `wall` when the class is
`node` or `jvm` or when the runtime gate identified a Node or Java
runtime in either tree (`TreeEnvironment.runtime_family`), whatever
class the spec declares. The agent writes `target_class` and perfproof cannot
tell whether it is true, so `compare` checks it against the project
model of the main checkout (`.perfproof/project.json`, read once per
run): the class must be that of a target entry in state `confirmed` for
which no invalidation trigger has fired, tested read-only with the
staleness check of `perfproof project check` (`find_stale`; the model
is never rewritten by `compare`). A `proposed`, `stale` or `retired`
entry does not count, a confirmed one whose evidence file changed does
not either, and neither does a missing, unreadable or target-less
model; the warning names which. This is a deterministic
check that fails closed; it is a check of consistency with what the
developer confirmed, not proof the code is of that class. The verdict's
`calibration_conditions` name the real traces the class rests on
(`TRACE_BASIS`): python_cpu 10, native 2, node 5, jvm 3, web_service 4,
agentic 4, llm_serving 2 (CPU Ollama only). `RunRecord` also refuses a `calibrated`
verdict whose spec is not on its class's own schedule at `alpha` 0.05,
or whose class is `gpu_kernel`, or whose class is `node` or `jvm` on a
primary metric that is not `wall`, so a record cannot claim what `compare`
would not give. The verdict
then carries `calibration` `calibrated`, `target_class` and
`calibration_conditions` naming the procedure, schedule, `alpha`, one
host and the seed of the run of record. Otherwise `calibration` is
`uncalibrated` and a KEEP is withheld with one named warning, the first
of: `schedule_uncalibrated` (`alpha` or schedule outside the domain,
which includes `gpu_kernel`), `target_class_unknown` (no class
declared), `schedule_not_class_own` (the schedule is another class's,
an off-class run), `metric_not_calibrated` (a `node` or `jvm` class, or
an identified Node or Java runtime, on a primary metric that is not
`wall`), `target_class_unverified` (the project model does
not confirm the class). A REJECT on evidence (rule 1, a guardrail breach, a
rule 3 interval at or below the bar) is decided the same whether the
verdict is calibrated or not. `RunRecord` refuses a `calibrated` verdict
whose class is not the spec's.

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
| Python, CPU-bound | this MacBook | Linux CI runner | 10 traces (rolling 7, membership 3); in-class run of record below |
| Native code | this MacBook | Linux CI runner | 2 traces (picolm); in-class run of record below |
| Node and JVM code | Linux VM, this MacBook | Linux CI runner | node 5, java 3 (`mac-java` new); in-class run of managed classes below |
| Web services | Hetzner Linux VM (both `web` traces); this MacBook (`web-node`, `web-go`) | Linux CI runner | 4 traces (web 2, web-node, web-go); in-class run of record below |
| Agentic apps | this MacBook | Linux CI runner | 4 traces (agent); in-class run of record below |
| GPU kernels | one paid GPU session | a second paid session | no traces: pending, KEEP withheld |
| LLM serving throughput | one paid GPU session | a second paid session | 2 traces, CPU Ollama only (no GPU); in-class run of record below |

Until a target class passes on its held-out traces, its verdicts carry
"not calibrated for this target class" in the report.

Calibration results are written to this page with the seed, `R`, the
observed rates, and their Clopper-Pearson bounds.

**The real-trace unit, and the owner's decision (Faras, 2026-10-28).**
The decision came after the results were seen: the stats-auditor's
whole real-trace run of PR #101 failed 6 cells, and the unit below was
fixed then, not before. The rule itself, the limit 2.5% and the
Clopper-Pearson test at `R = 4000` with a largest passing count of 83,
is unchanged.

- **Acceptance unit.** The real-trace unit is each target class's own
  traces under its own class schedule. A trace of another class run
  under a schedule that is not its own (an **off-class** cell) is
  reported, and a failure there is published as a named limit below,
  not counted as acceptance.
- **Which traces are in which class.** From each trace's header
  `target`, a workload label and not a class name, so the mapping is a
  reading. By class of `docs/targets.md`: `rolling` (7) and
  `membership` (3) are `python_cpu`, 10 traces; `picolm` (2) is
  `native`; `web` (2, the retried partial run left out) is
  `web_service`; `agent` (4) is `agentic`; `llm` (2, CPU Ollama
  serving, no GPU) is `llm_serving`. (This is the mapping of the earlier
  run of record, below; the managed-classes run further down puts
  `node`, `java` and the new web traces in classes of their own.) In
  the earlier run the `python_native` schedule's
  in-class cells also include `node` (5) and `java` (2), which belong to
  none of the six classes (`docs/targets.md` names Python and Node
  separately, and no Java class): they are run under that schedule as
  the nearest, and without them the in-class cells of the three
  schedules are 420 and none fails (stats-auditor). The `web_agentic`
  schedule's in-class cells are `web` and `agent` together (6 traces).
  `picolm` is a native LLM inference engine run on CPU as a command, and
  its cells pass under every schedule, so the choice between
  `python_native` and `llm_serving` for it moves no result.
  `gpu_kernels` has no real traces, so none is in its class.
- **`gpu_kernels` is not calibrated at launch.** With no real traces
  its real-trace unit is empty, so a KEEP is withheld on that schedule
  until GPU traces exist. The owner will record them on a rented GPU
  before launch.

**Run of record for the `CALIBRATED` flip.** The whole real-trace
calibration (`tests/calibration/run_real.py run`, `R = 4000`, the
three procedures of `PHASE_2`, 27 traces, 3 block rules, 4 schedules,
7 speedups, 2268 cells per procedure) at seed `661371947387106328`,
drawn from the operating system's entropy on 2026-10-28 and written
here before the run. Results are in
`tests/calibration/results/real-seed-661371947387106328.csv`. The
procedure judged is the shipped one: `hac_lf` on the three class
schedules, the gated batch means on `gpu_kernels`.

| Cells | Schedule | Cells | Failing | Worst false KEEPs of 4000 |
|---|---|---|---|---|
| in class | python_native | 399 | 0 | 30, `linux-rolling/fixed20/python_native/1.03` (bound 1.016%) |
| in class | web_agentic | 126 | 0 | 22, `linux-loaded-steady-web/fixed20/web_agentic/1.03` |
| in class | llm_serving | 42 | 0 | 29, `linux-llm/fixed6/llm_serving/1.03` |
| off class | python_native | 168 | 2 | 157, `linux-llm/fixed20/python_native/1.03` |
| off class | web_agentic | 441 | 0 | 43, `linux-llm/pw/web_agentic/1.03` |
| off class | llm_serving | 525 | 0 | 41, `linux-rolling/fixed20/llm_serving/1.03` |
| off class | gpu_kernels | 567 | 4 | 473, `linux-llm/fixed20/gpu_kernels/1.03` |

All 567 in-class cells pass; the worst in-class bound is 1.016% at the
2.5% limit. The acceptance table names speedups 0.95 and 1.04 too; they
are not in `run_real.py`'s `SPEEDUPS` (0.80, 0.90, 1.00, 1.03, 1.05,
1.10, 1.20), so the run of record does not contain them. The
stats-auditor ran the 162 in-class cells at those two speedups at its
own seed as a diagnostic, 0 failing; that is not acceptance. The in-class pass is the acceptance for the three class
schedules. `gpu_kernels` has no in-class cell, and its off-class
cells are not acceptance.

**Named limit: Node and JVM are calibrated on `wall` only
(stats-auditor of PR #120, 2026-10-29).** All eight Node and Java
traces are fresh-process `wall` timings. The auditor also recorded
three in-process Node A/A traces (`reported`, M4, loaded). One of them
failed its three block-rule cells at a true speedup of 1.03, with 165,
111 and 166 false KEEPs of 4000 (Clopper-Pearson bounds 4.7%, 3.2% and
4.7%) against the 2.5% limit; the other two passed, at most 23 of
4000. There is no in-process JVM trace. The 27% of "Warm-up (Java)" is
a model in which the two arms settle at different speeds, not a
measurement. Because one of three in-process Node traces fails, a
verdict is calibrated only on a `wall` primary metric when the class
is `node` or `jvm`, and also when the runtime gate identified a Node
or Java runtime (`TreeEnvironment.runtime_family`) whatever class the
spec declares: an identified `node bench.js` declared `native` or
`python_cpu` gave a calibrated KEEP on `reported` before this rule.
On any other metric kind the comparison runs, the estimate and
interval stand, and a KEEP cannot be given: `compare` records the
warning `metric_not_calibrated` and the verdict is INCONCLUSIVE when
it would have kept. A REJECT is decided as before.

Escapes this rule does not close (named, not fixed):

- A benchmark driver in one runtime that launches another, such as a
  Python script running `node bench.js`, is calibrated under its
  declared class: the runtime rule sees only the direct command the
  runtime gate identified.
- A Node or Java binary built inside the tree and run with
  `allow_runtime_change` is probed as a `native_build`, which carries
  no runtime family, so the rule does not see it. The flag alone does
  not open this: a `node` or `java` found on `PATH` is still
  identified and held to `wall`. The skill never sets the flag without
  asking.
- An unidentified runtime (a `./run.sh` wrapper) cannot get a KEEP,
  because the runtime gate is then `not_available`. Its REJECT still
  carries the `calibrated` label when the spec's class and schedule
  are in the domain, and a REJECT is decided the same whether the
  verdict is calibrated or not.

The numbers are the auditor's, not in a committed artifact.

**Named limits: the 6 failing off-class cells.** Every failing cell is
a trace under a schedule of another class, on the shipped procedure,
at true speedup 1.03. They were found by the auditor at seed
`3018888685074391575` (`R = 4000`, 648 cells) and reproduce at seed
`661371947387106328`. False KEEPs of 4000, the auditor's seed first:

| Trace | Schedule | Auditor's seed | Run of record |
|---|---|---|---|
| `linux-llm` | `gpu_kernels` | 484, 349 and 190 | 473 (fixed20), 342 (fixed6), 185 (pw) |
| `linux-llm` | `python_native` | 153 and 94 | 157 (fixed20), 96 (fixed6) |
| `linux-loaded-steady-web` | `gpu_kernels` | 104 | 107 (fixed20) |

The auditor attributed the five `linux-llm` cells to that trace's burst
segment, whose mean (-0.613%) sits below the trace's overall mean
(-0.073%), so that the quiet replicates sit about 0.060% above `S`: the
skew limit under "Assumptions". A second audit measured the same (all
16 pairs beyond 6 robust standard deviations lie in pairs 0 to 34, 10
negative and 6 positive). The `linux-loaded-steady-web` cell
has a different cause: three extreme pairs (159, 237 and 500), not the
early segment. Neither measurement is in a committed artifact. The
auditor reports the false-KEEP rate at true speedups up to 1.025 as
0.00% to 0.07% on these cells (not rerun here: the run of record has
no 1.025 cell). Read these as limits that bite at a true speedup
at the bar: the counts above are at 1.03, and a workload of one class
decided on another class's schedule can keep a change whose true
speedup is at the bar. The schedule is chosen from the spec, not the
workload, and the skill's spec guidance uses the `python_native`
schedule for every workload (`skills/perfproof/spec.md`), so in
practice a web, agentic or LLM workload runs on that schedule, which
makes the off-class `linux-llm` on `python_native` cells the ones an
LLM workload meets.

**Run of record for the managed classes (pre-registered, Faras,
2026-10-29).** The owner decision of 2026-10-29, taken after the earlier
results were seen (`.claude/worklog.md`, Decisions), makes KEEP reachable for
Node and JVM code (two new classes, `node` and `jvm`, on the
`python_native` schedule, `(10, 10, 6, 60)`) and for web services in
Node, Go and Ruby. Ruby web services are in `web_service` by the class
rule; no Ruby trace exists, so the run says nothing about them. The
`node` and `jvm` classes were decided by the owner after the earlier
results were seen, not before them. This paragraph is written before
the run. The trace
set is every trace committed in `tests/calibration/traces/data/` at the
commit that holds this paragraph: the 27 of the run above plus
`mac-java` (`Bench.java`, JDK 21, 1000 pairs), `mac-web-node` (a Node
`node:http` server, 600 pairs) and `mac-web-go` (a Go `net/http`
server, 600 pairs), recorded on this Mac in normal day-to-day use, 30
traces. The class mapping: `node` traces (header `target` `node`, 5) in `node`; `java` traces (`linux-java`,
`linuxvm-loadedbyothers-java` and `mac-java`, 3) in `jvm`; `web` (2, the
retried partial run left out), `web-node` and `web-go` traces (4) in
`web_service`; every other mapping as above. The procedures are the
three of `PHASE_2` (`batch_means_6`, `batch_means_6_ungated`, `hac_lf`),
3 block rules, 4 schedules, 7 speedups, `R = 4000`, at seed
`8268209547887630561`, drawn from the operating system's entropy on
2026-10-29 (`secrets.randbits(63)`) and committed before the run. The
acceptance rule is unchanged (largest passing count 83 of 4000). No
failing cell is rerun to obtain a pass. Results are reported below the
table of the run above, with the in-class cells per schedule, the cells
of the three new traces, the node and java cells under `python_native`
and the web traces under `web_agentic`.

**Results of the managed-classes run.** Run on 2026-10-29,
`tests/calibration/run_real.py run --out
tests/calibration/results/real-seed-8268209547887630561.csv --workers 4
--seed 8268209547887630561`, `R = 4000`, 7560 rows (2520 cells per procedure),
wall time 50 s as the script reported it. CSV:
`tests/calibration/results/real-seed-8268209547887630561.csv`. The
procedure judged is the shipped one (`hac_lf`; the gated batch means on
`gpu_kernels`). The three new traces were recorded on this Mac in
normal day-to-day use, one at a time: `mac-java` 1000 pairs in 26.7 min
(host load1 mean 6.76, max 16.17 on 10 cores), `mac-web-node` 600
pairs in 60.4 min (mean 5.55, max 11.14; the power source read Battery
Power on pairs 228 and 229, three runs, with no CPU speed limit) and
`mac-web-go` 600 pairs in 56.6 min (mean 4.80, max 7.57). None recorded
a retry or a throttled run.

| Cells | Schedule | Cells | Failing | Worst false KEEPs of 4000 |
|---|---|---|---|---|
| in class | python_native | 420 | 0 | 37, `linux-rolling/fixed20/python_native/1.03` (bound 1.215%) |
| in class | web_agentic | 168 | 0 | 18, `mac-loaded-agent/fixed6/web_agentic/1.03` |
| in class | llm_serving | 42 | 0 | 25, `linux-llm/pw/llm_serving/1.03` |
| off class | python_native | 210 | 2 | 170, `linux-llm/fixed20/python_native/1.03` |
| off class | web_agentic | 462 | 0 | 43, `linux-llm/pw/web_agentic/1.03` |
| off class | llm_serving | 588 | 0 | 40, `linux-rolling/fixed20/llm_serving/1.03` |
| off class | gpu_kernels | 630 | 4 | 474, `linux-llm/fixed20/gpu_kernels/1.03` |

All 630 in-class cells pass. The failing off-class cells are the six
of the run above again, at the same trace and schedule pairs and speedup
(1.03): `linux-llm` on `python_native` (fixed6 86, fixed20 170) and on
`gpu_kernels` (pw 204, fixed6 308, fixed20 474), and
`linux-loaded-steady-web` on `gpu_kernels` (fixed20 102). No other
off-class cell fails, including the three new traces on every schedule.
By class, the in-class cells and the worst false KEEPs of 4000:

| Class | Schedule | Traces | Cells | Failing | Worst false KEEPs of 4000 |
|---|---|---|---|---|---|
| python_cpu | python_native | 10 | 210 | 0 | 37, `linux-rolling/fixed20/python_native/1.03` |
| native | python_native | 2 | 42 | 0 | 13, `mac-loadedbyothers-picolm/fixed6/python_native/1.03` |
| node | python_native | 5 | 105 | 0 | 23, `linux-node/pw/python_native/1.03` |
| jvm | python_native | 3 | 63 | 0 | 19, `linux-java/fixed20/python_native/1.03` |
| web_service | web_agentic | 4 | 84 | 0 | 16, `linux-loaded-steady-web/fixed20/web_agentic/1.03` |
| agentic | web_agentic | 4 | 84 | 0 | 18, `mac-loaded-agent/fixed6/web_agentic/1.03` |
| llm_serving | llm_serving | 2 | 42 | 0 | 25, `linux-llm/pw/llm_serving/1.03` |

False KEEPs of 4000 at true speedup 1.00 / 1.03, by block rule (pw /
fixed6 / fixed20), of the cells the managed classes decide on. A
count of 84 or more fails (83 passes; none fails); every speedup of every cell here
passes. Politis-White blocks: `mac-java` 6, `mac-web-node` 2,
`mac-web-go` 2.

| Trace | Schedule | pw | fixed6 | fixed20 |
|---|---|---|---|---|
| `mac-java` | python_native (in class) | 0 / 3 | 0 / 9 | 0 / 6 |
| `mac-web-node` | python_native | 0 / 3 | 0 / 2 | 0 / 2 |
| `mac-web-node` | web_agentic (in class) | 0 / 7 | 0 / 4 | 0 / 1 |
| `mac-web-go` | python_native | 0 / 12 | 0 / 9 | 0 / 15 |
| `mac-web-go` | web_agentic (in class) | 0 / 13 | 0 / 14 | 0 / 8 |
| `linux-java` | python_native (in class) | 0 / 13 | 0 / 15 | 0 / 19 |
| `linuxvm-loadedbyothers-java` | python_native (in class) | 0 / 7 | 0 / 4 | 0 / 10 |
| `linux-node` | python_native (in class) | 0 / 23 | 0 / 19 | 0 / 4 |
| `linux-loaded-steady-node` | python_native (in class) | 0 / 16 | 0 / 17 | 0 / 1 |
| `linux-loaded-bursty-node` | python_native (in class) | 0 / 18 | 1 / 13 | 0 / 15 |
| `mac-loadedbyothers-node` | python_native (in class) | 0 / 14 | 0 / 7 | 0 / 10 |
| `mac-loadedbyothers-burners-node` | python_native (in class) | 0 / 17 | 0 / 20 | 0 / 15 |
| `linux-web` | web_agentic (in class) | 0 / 7 | 0 / 3 | 0 / 0 |
| `linux-loaded-steady-web` | web_agentic (in class) | 0 / 7 | 0 / 9 | 0 / 16 |

The `mac-java` cells on `web_agentic`, `llm_serving` and `gpu_kernels`
(worst 31 of 4000, `gpu_kernels` pw at 1.03), and the `mac-web-node` and
`mac-web-go` cells on `llm_serving` and `gpu_kernels` (worst 34,
`mac-web-go/fixed20/gpu_kernels/1.03`), also all pass. The two web
traces are one load shape of one service (2000 requests at concurrency
16, a server started per run) on one Mac, and `mac-java` is one JDK 21
on one Mac; the run says nothing about a JVM or Node service under
other runtimes, hosts or loads. The `web-go` target's `/db` route reads
an in-memory index instead of SQLite (the Go standard library has none).

**Named limits of the managed-classes run.**

- All three new traces come from one Mac session each, on one machine,
  in normal day-to-day use. The run says nothing about other hosts,
  other runtimes or other loads.
- Ruby has no trace (see above). Go, .NET and Ruby libraries that are
  not web services have no class and no calibrated KEEP.
- The run has no 0.95 or 1.04 speedup cells, as `SPEEDUPS` has none. The
  stats-auditor ran the in-class cells at those two speedups at its own
  seed `8786722520344204948`: 180 in-class cells, 0 failing, worst 8
  false KEEPs of 4000. That is an auditor check, not the run of record.
- A fresh seed resamples the same traces by block bootstrap, so it
  tests the procedure on those traces again, not on new workloads; the
  pass is no stronger than the traces are representative.

### Guardrail cells

Prespecified here for T2.15, before the run of record, and not
changed after seeing its results. The acceptance rule above applies
unchanged to every guardrail cell: the one-sided 95% Clopper-Pearson
upper bound on its error rate at `R = 4000` is at most 2.5%.

- **Seeds.** `7282691080722525374` for the run of record and
  `9113244239656782113` for a second complete run, each drawn from the
  operating system's entropy pool after the last change to the procedure
  and before its run began (the page under "Guardrail calibration results"
  names the earlier pairs discarded). Each cell draws from its own
  `SeedSequence` keyed by the CRC-32 of its name.
- **Cells.** One host. Each of the four look schedules of the primary's
  calibration; the scenarios of the primary's main grid (the development
  and exposed scenarios, 25 on one host) and three of page-quantised memory
  (values in whole 4 KiB pages: `pages_exact`, a level drawn per
  replication and no noise, so only the rounding varies; `pages_tight`,
  noise of 0.05% on 160 MB; `pages_noisy`, noise of 2% on 20 MB), with the
  resolution step passed as the page. Primary data of the verdict process
  below come from the cell's scenario (`lognormal_cv5` for the page ones).
- **Configurations.** `rel1`: an allowance of 10% alone, `G = 1`; true
  worsening ratios 1.00, 1.05, 1.10 (exactly on the bar, where compliance is
  still true) within and 1.11 (the nearest beyond), 1.20, 1.50 beyond.
  `rel3`: the same guardrail in a spec of three checks, `G = 3`, which
  sends the python_native schedule (`K = 6`) to the gated batch means;
  ratios 1.10 and 1.11. `limit`: the allowance with true ratio 1 (within)
  and one absolute limit, `G = 2`; true candidate value over the limit
  0.90, 0.97, 1.00 (on it, still within), 1.01, 1.03 and 1.10 (beyond).
  The truth of a limit cell is the median of the candidate's value over
  the session, the statistic the check bounds: the median of 2.4 million
  draws of the simulator's candidate time at ratio 1 (a fixed generator,
  so a number, about 1e-4 of a value at the noisiest scenario), divided
  by the cell's ratio (drift scenarios are left out of `limit`, since
  their median moves with the look). The check is on the median because
  a slow mode or outliers move it by their rank, not their size.
- **Messy cells.** Every cell is run and every one is judged by the rule.
  A `limit` cell in a scenario with a slow mode or outliers (`bimodal`,
  `outliers`, `A00-outliers-only`, `A00-outliers-5pct-x5-cv1`,
  `A00-outliers-1pct-x5-cv1`, `A20261053-04`, `A20261054-08`,
  `A20261055-17`; 192 per run) is called messy, only to report them
  apart; the harness's flag for it is `messy`.
- **Coverage of the bounds.** Per limit cell, the order-statistic bounds
  on the median are also judged as bounds, apart from the verdict: at the
  first look length (`n0`) and at `max_rounds`, over all replications,
  the upper bound must not fall below the true median, nor the lower rise
  above it, more often than its tail `alpha / (4 K G)` allows: the
  one-sided 95% Clopper-Pearson lower bound on the miss rate must be at
  most the tail. A replication with no bound yet claims nothing and
  misses nothing. This is the test that `reliable_value` has the
  design confidence on the simulator's AR(1) dependence, in every cell
  including the messy ones; it says nothing about persistent drift.
- **Two processes, one error each.** On the same replications. *Guardrail
  alone*: the comparison stops at the first look at which the guardrail
  is `passed` or `breached`; a false pass is `passed` while the guardrail
  is truly beyond, a false breach `breached` while it is truly within.
  *Verdict*: a second, independent draw of paired rounds with a true
  primary speedup of 1.20 supplies the primary's look, and each look's
  rule and the guardrail's status go through `guardrail_actions`; the
  comparison stops at the first look that does not continue. A false KEEP
  is a KEEP while the guardrail is truly beyond, a false guardrail REJECT
  a REJECT (`guardrail`) while it is truly within. A cell passes only if
  both bounds are at most 2.5%.
- **Reported, not part of the rule.** Per cell: how often the guardrail decides
  (`passed`, `breached`, undecided), the verdict's outcomes, and the 95th
  percentile of rounds used.
- **Sensitivity.** An exploratory run at seed `12345` (not evidence)
  showed the cells can fail: with the per-check confidence narrowed to 0.95
  the cell `python_native/lognormal_cv5/rel1/1.10` gave 94 false breaches
  of 4000 and `gpu_kernels/RHO0.7/rel1/1.10` 260, both failing; with the
  design's confidence they gave 17 and 54. The same run showed the
  `limit` cells in scenarios with 4% to 5% outliers failing at 1.03 under
  the first limit check (a mean-based interval); its other choices were
  the nearest-beyond ratios (1.11 and 1.01). A first version of this
  section left those cells out of the rule; the owner decided
  (2026-10-23) that every cell is judged, and then that the limit is
  judged on a rank-based bound ("The owner's decision on limits").

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
Real noise is covered under "Real noise" (its own class's traces
under its own schedule, 2026-10-28).

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
its cost is power. At that point the procedure was still
`CALIBRATED = False` in `bench/compare.py`, and T1.7 audited it on
held-out scenarios drawn at audit time.

### T1.11 results (`hac_lf`)

Every number below came from the runs named here; per-cell counts are in
`tests/calibration/results/t111p2-*` (gzipped CSVs, and the Markdown
tables `t111p2-acceptance.md`, `t111p2-dominance-*.md`,
`t111p2-real-*.md`). Commands: `build_lf_tables.py build`,
`run_calibration.py run --grid main|heldout|power|cond|skew --procedures
batch_means_6 batch_means_6_ungated hac_lf`, `run_real.py run`,
`compare.py acceptance|power`, `dominance.py`.

**Constants.** `UCB_Z = 1.0`, `RHO_FLOOR = 0.3`, `HAC_B = 0.1`. `UCB_Z`
was fixed by the rule above on the exploratory seed
2631286274093223815 with the committed table (full synthetic grid, 1344
cells; counts in `t111p2-tuning-main-exploratory-seed-2631286274093223815
.csv.gz`): `z` = 0.5 failed one exposed cell (`llm_serving/RHO0.7/one_host/
1.03`, 84 of 4000), 1.0 passed every cell (worst 59), so 1.0. No other
constant was tuned after a run; a failure at a later seed would raise `z`
and rerun everything at new seeds.

**The table.** Built from seed 7974144670174977917, 4,000,000 draws per
`rho` and look length (`lf_tables.py`); a second build from seed
7860359274439352604 differs in its entries by a median of 0.31%, a 95th
percentile of 1.16% and a maximum of 10.5% (one host; cross host 0.30%,
1.13%, 10.5%; the maximum is in the highest-lag-1 bins of the widest
limits, which hold few draws). Off its grid, fresh draws at `rho`
between the grid points and look lengths between the tabulated ones
(`test_hac_lf_table.py`, `R` = 400,000 each, both host modes) miss the
true mean no more often than the per-look budget plus 20%.

**Acceptance (false KEEP and false REJECT, limit 83 of 4000).** Seeds:
the T1.8 seed of record 3598798392042499920 and a fresh seed
7975269519414383480 drawn before the runs. `R = 4000`, both host modes,
all four schedules.

| Grid | Cells | `hac_lf` failing, record / fresh | Worst count, record / fresh | T1.8 gated primary failing | T1.8 ungated failing |
|---|---|---|---|---|---|
| Development scenarios | 560 | 0 / 0 | 31 / 39 | 0 / 0 | 0 / 0 |
| Exposed scenarios | 784 | 0 / 0 | 67 / 57 | 0 / 0 | 19 / 23 |
| Held-out (30 draws of the audit ranges, seed 4371735374463635800) | 1680 | 0 / 0 | 32 / 31 | 0 / 0 | 0 / 0 |

The worst cell is `llm_serving/A20261052-04/one_host/1.03` at the seed
of record (67, 1.7%). Outside the audit ranges (probes of
`tests/calibration/conditions.py` and `skew.py`, not acceptance cells):
the 9 busy-machine scenarios (252 cells): 7 / 6 failing, all in
`gpu_kernels` one host and all arm-correlated episodes with a 5% bias
(`COND-armbias-*`, up to 145), where the gated primary has 0 failing
because the gate withholds them; the shared-throttling scenarios pass.
One-version skew probes (168 cells): 11 / 11 failing, the gated
primary 7 / 7, the ungated 9 / 10: skew stays a stated limit.

**Real traces.** 19 A/A traces (10 Hetzner Linux, 3 Linux VM, 6 Mac;
`linux-picolm` has 141 pairs), moving-block bootstrap with the phase 1b
protocol (even blocks on even pair indexes; Politis-White length, fixed
6 and fixed 20 pairs; truth injected as `d - mean(d) + log S`), seed
1691201805404077360, `R = 4000`, one host.
False-KEEP cells (S 1.00 and 1.03, 19 traces x 4 schedules = 152 per
rule), limit 83:

| Block rule | `hac_lf` failing, worst | gated primary | ungated |
|---|---|---|---|
| Politis-White | 0, 35 (`linux-loaded-bursty-node` gpu) | 0, 42 | 0, 45 |
| fixed 6 | 0, 36 | 0, 52 | 0, 57 |
| fixed 20 | 0, 57 | **1**, 86 | **2**, 140 |

**Power against the gated primary.** Mean over the scenarios of each
group (synthetic, seed of record, one host; every group, both hosts and
the fresh seed in `t111p2-*power*`/`compare.py power`), then the 18 full
real traces (Politis-White blocks, `linux-picolm` left out of the
mean). KEEP at 1.10, percent:

| Schedule | Synthetic Gaussian | AR(1) 0.5 | 2% outliers of 3x | Shared throttling | Real traces |
|---|---|---|---|---|---|
| python_native | 65.9 to 73.3 | 4.0 to 57.2 | 30.6 to 41.1 | 60.9 to 89.8 | 84.9 to 99.0 |
| web_agentic | 47.6 to 63.4 | 4.2 to 29.1 | 17.6 to 31.9 | 21.2 to 52.4 | 51.0 to 91.1 |
| llm_serving | 28.8 to 53.4 | 2.1 to 21.5 | 6.4 to 28.1 | 6.3 to 29.6 | 26.4 to 81.4 |
| gpu_kernels | 87.1 to 86.4 | 99.8 to 94.8 | 62.8 to 63.7 | 89.6 to 99.6 | 100.0 to 100.0 |

(gated primary to `hac_lf`.) On the real traces KEEP at 1.05 goes
47.4, 18.1, 5.5 and 78.3 to 55.5, 38.0, 28.2 and 77.1 (python, web,
llm, gpu), REJECT at 0.90 87.4, 59.6, 42.7 and 100 to 100, 99.8, 98.5
and 100, and the mean rounds at 1.10 40.5, 27.5, 21.7 and 52.6 to 25.0,
22.4, 18.0 and 27.5. The tables for the other groups, REJECT power and
the cross-host cells are in the files named above.

**The power constraint (owner's, not met).** The smallest difference to
the gated primary, with the number of cells worse by more than 3
standard errors of two proportions of 4000 (conservative: both see the
same rounds):

| Cells | KEEP 1.05 | KEEP 1.10 | KEEP 1.20 | REJECT 0.90 | REJECT 0.80 |
|---|---|---|---|---|---|
| Synthetic, seed of record (432 KEEP, 80 REJECT cells) | -8.4 pts, 69 worse | -35.4, 22 | -23.4, 9 | +2.4, 0 | +1.9, 0 |
| Synthetic, fresh seed | -8.6, 41 | -37.0, 22 | -20.9, 13 | +2.7, 0 | +2.4, 0 |
| 19 real traces x 3 block rules (228 cells) | -4.6, 11 | -0.1, 0 | +0.0, 0 | +0.0, 0 | +0.0, 0 |

Where it is negative, and why. (1) On the three schedules other than
GPU kernels the deficit never exceeds 1.6 points (`python_native`,
`web_agentic`, `llm_serving`, at 1.05 and 1.10: 11 and 1 cells at
1.05/1.10 for python, 29 and 4 for web, 21 and 1 for llm at the seed
of record; the paired difference is smaller than the two-proportion
error used to count them, so most of these are at the edge of noise);
the critical value at a sample lag-1 near 0 is above the `t` quantile
by the price of the 0.3 floor. (2) `gpu_kernels`, where the gated
primary has 20 blocks and its gate never fires: on clean noise the
deficit is 0 to 3 points (the `t` quantile on 20 blocks, 3.2, is below
the least favourable 3.5 to 3.8 at `n` = 120); on the real traces at
1.05 it is up to 4.6 points in 11 of 228 cells and at most 0.1 point at
1.10 and above. (3) The large negative cells are all `gpu_kernels` with
`rho` 0.5 or more (`RHO0.6`, `RHO0.7`, the exposed draws with `rho`
about 0.6, a held-out draw). The gated primary passes acceptance there
(false-KEEP counts up to 56 of 83 in `RHO0.6` and `RHO0.7` at the seed
of record, 62 in the exposed draw `A20261056-15` cross host), so the
difference is not an over-limit error rate. INFERENCE, not shown by a
run: at `n` = 120 the primary's 20 block means of 6 rounds at `rho` 0.7
are correlated, its interval is narrower than a level-holding one, and
its power comes from that narrowness, which a procedure whose critical
value covers `rho` up to its `ucb` here (0.7 to 0.9) does not have.
Nothing in the real traces resembles them.

**Looks that used a limit above the floor.** At `S = 1.00` on the
real traces (Politis-White blocks; looks with an interval; the table is
`t111p2-real-fallback.md`, made by `fallback_share.py`), 27.2% of the
looks of `python_native`, 31.7% of `web_agentic`, 38.0% of
`llm_serving` and 22.8% of `gpu_kernels` had `ucb` above 0.3 and so a
limit of 0.4 or more; per trace and schedule from 11.3%
(`linux-loaded-bursty-node`, gpu) to 54.9% (`mac-node`, llm, the trace
with lag-1 +0.13). That is not rare: with `UCB_Z = 1` the bound
exceeds 0.3 at a sample lag-1 above about 0.08 at `n` = 20 and 0.17 at
`n` = 60, and the sample lag-1 of independent noise is that large often
at small `n`. A limit above the floor widens the interval by the
difference of the table's columns (`lf_tables.py`).

**Not shown.** Real noise on other machines and classes than the 19
traces (the floor of 0.3 and `UCB_Z` rest on them and on synthetic
AR(1)); the arm-correlated busy-machine probes, where this procedure
is weaker than the gated primary and has no covariate; one-version
skew; the time budget in the real-trace runs (every replicate takes
`max_rounds`). The table's maximum build-to-build difference is 10.5%
(only the summary of the second build is committed, in
`t111p2-table-builds-compare.txt`), and where it lies was not
examined.

### T1.12 golden set (design, written before the recorded campaign)

**Order of events.** The design below was committed (`387308e`) less
than a minute before the recorded campaign started (10:58 UTC). A
first campaign start at 10:50, with a wrapper that had a bias, ran
before that commit and was discarded after about 7 minutes. What was
looked at from it: 19 comparisons (10 at S = 1.00, 9 at S = 1.02) were
replayed through the report pipeline and inspected for the injected
size and the noise level. The replay of the 10 null comparisons also
printed per-method verdicts, which were seen (KEEP / REJECT /
INCONCLUSIVE of 10): `python_native` gated 0 / 7 / 3 and `hac_lf`
0 / 10 / 0; `web_agentic` gated 0 / 0 / 10 and `hac_lf` 0 / 10 / 0;
`llm_serving` gated 0 / 0 / 10 and `hac_lf` 0 / 6 / 4. No decision
rule, cell size or method ordering was changed because of what was
seen. The design is therefore
fixed before the recorded data, not before every number seen. One
rule was amended after the recorded data; see "Deviations" in the
results.

**Why.** T1.11 chose between the gated primary, `hac_lf` and the split
on synthetic grids and on 19 A/A traces whose speedup was injected into
the log ratios. Those test the interval, not the product. The owner
asked for the choice to rest on end-to-end results on known answers:
real benchmarks, a real slowdown of exactly known size, the real
`perfproof compare` run to its last round, and the three methods
replayed on the very same recorded executions.

**Truth by injection.** Each workload runs through a wrapper
(`studies/golden/wrap.py`, locked as the harness) that launches the
real benchmark as a child, times it from spawn to exit (`work`), then
spins on the wall clock until `f * work` more seconds have passed, and
reports `work + spin` as the metric (a `reported` metric, as the T2.11
traces used). The spin is scaled to that execution's own measured work,
so the log ratio of a round is exactly `log(1 + f)` plus the A/A noise
of the two works, with no calibration constant to drift. The spin goes
into the **baseline** arm, so the candidate is faster by `1 + f`:
`S = 1 + f`, the quantity the bar and the error table are about. (A
slowdown in the candidate would only give regressions, which have no
KEEP to get wrong.) The achieved spin of every execution is recorded;
the cell's achieved effect is `exp(mean log((work + spin) / work))` over
its baseline executions, which equals `1 + f` to the clock's resolution,
and is reported beside the nominal one. The two trees are one commit;
their A/A bias is estimated from the `f = 0` cell and reported. A cell's
label is its nominal `S`; if the bias is large enough to move a cell
across the bar the label is corrected before any decision is read.

**Workloads.** Python (pure-Python, derived from the membership
target), Node (the `bench.js` of T2.11 with `N` reduced), Java (the
`Bench.java` of T2.11 with `N` reduced) and native C (a small
deterministic program, `gcc -O2`), each sized to about 0.1 s of work per
execution so that thousands of comparisons fit a dedicated box. The
sizes differ from the T2.11 traces (0.4 to 1.5 s), which is stated where
the two are compared.

**Cells and size.** `S` in 1.00, 1.02, 1.03, 1.05 and 1.10 per
workload, 200 comparisons for 1.00 and 1.03 (a cell with no error then
has a one-sided 95% Clopper-Pearson upper bound of 1.49%), 100 for the
others. Cells are interleaved in time in blocks so that a slow period
of the host falls on every cell. Each comparison is one run of
`perfproof compare` to its maximum rounds (120 for Python and native,
60 for Node and Java); it is never repeated per method. Every execution
is recorded by the wrapper: arm, order, `work`, `spin`, reported value,
user and system CPU, instructions and cycles (`perf stat`), the 1-minute
load, steal ticks, and the host.

**Replay.** The recorded rounds of a comparison are the first `n` rounds
of every schedule that fits them, so one comparison replays on
`python_native` (60 rounds), `web_agentic` (30), `llm_serving` (22) and,
where 120 rounds were recorded, `gpu_kernels` (120). Each is decided by
`perfproof.stats.sequential.run_looks` as calibration does
(`calibrated=True`, no time budget) for three methods on the same data:
the gated primary (`batch_means`), `hac_lf` on every schedule, and the
split (`hac_lf` on all but `gpu_kernels`, as `procedure_for_schedule`
maps schedules). The wrong-KEEP rate of a method and cell is KEEPs over
comparisons with `S <= 1.03`; the power is KEEPs over comparisons with
`S` at 1.05 and 1.10; wrong-REJECTs are reported at 1.05 and 1.10. Each
rate has its one-sided 95% Clopper-Pearson bound. Rounds to verdict are
the rounds of the last look; wall time to verdict is the sum of the
recorded execution times of those rounds (both arms, and the wrapper's
startup excluded); the compute cost is the time the statistics code
takes per verdict, measured on the replay host. Methods are compared
pairwise on the same comparisons by the exact McNemar test on KEEP
against not-KEEP, and every discordant pair is classed by the look at
which the methods diverged and the reason (interval width, gate
withholding, different look).

**Choosing.** A method is admissible when its wrong-KEEP upper bound is
at most 2.5% in every cell of every schedule. Among admissible methods
the simplest is chosen, simplest meaning fewest decision paths: the
gated primary (one interval, one gate), then `hac_lf` everywhere (one
interval, no gate, a generated table), then the split (two intervals and
a dispatch). Where the rule picks a method that is clearly weaker in
power than another admissible one, the report says so with the numbers
and the owner decides. The recommendation names every cell where the
method loses to another by more than the McNemar error. If no method is
admissible the report says so and recommends none.

**Noise at the source.** A separate recorder (`studies/golden/noise.py`)
runs A/A pairs of the Python and native workloads on the same box under
four conditions: interleaved or blocked order, pinned (`taskset`) or
not, and records wall time, CPU time, instructions and cycles per
execution. The standard deviation and the order-centred lag-1 of the log
ratio are compared across conditions and metrics. This is CPU data from
one host and says nothing directly about a GPU; the report says what it
does and does not imply for the GPU gap.

**Not claimed.** One host (a KVM guest, one physical core with two SMT
threads) and four short workloads. A different machine, a longer
workload or a GPU may behave otherwise. Results from another host are
never pooled with these.

### T1.12 results (golden set)

Everything below is measured and comes from committed files:
`eval/golden/data/*.csv.gz` (every execution), `eval/golden/noise/*.csv`
(the A/A recordings), and the tables made from them by
`tests/golden/golden_report.py` and `golden_noise.py` into
`eval/golden/results/` (`cells.csv`, `replay.md`, `summary.md`,
`noise.md`). Sha256 of every copied file is in
`eval/golden/data-sha256.txt` and `eval/golden/noise-sha256.txt`; the
uncompressed CSVs' sha256 equal the box's (same files, one host).

**What was run.** One host: the Hetzner CCX13 (KVM guest, AMD EPYC-Milan,
one physical core with two SMT threads, kernel 7.0.0-30, Python 3.14.4).
2,800 real `perfproof compare` runs, each once to its maximum rounds
(Python and native 120, Node and Java 60), 700 per workload: 200 each at
S = 1.00 and 1.03, 100 each at 1.02, 1.05 and 1.10. 504,000 recorded
executions in all (every row of the CSVs is one). The batch ran
10:58 to 04:53 UTC (17.9 h of box time); the noise recorder 05:00 to
05:23; box time used by this task in all about 18.8 h.

**Deviations from the design above, and what they cost.**

- The cells of 100 that carry a wrong-KEEP bound (S = 1.02; 1.05 and
  1.10 are power cells) cannot show 2.5% even with no error (n = 100
  gives 2.95%). **Under the rule as
  registered ("Choosing": 2.5% in every cell) no method is admissible,
  because the 14 cells of 100 at S = 1.02 per method cannot pass.** The
  rule was amended to judge only the cells of 200 (S = 1.00, 1.03):
  proposed by the task author after seeing the data; accepted by the
  owner on 2026-10-23. Every method had 0 wrong-KEEPs in
  the S = 1.02 cells. Both readings are in `summary.md` and `replay.md`.
- The first batch was discarded after about 7 minutes: the wrapper read
  `/proc/stat` before stamping the end of the child, which put about
  100 us on the candidate arm only. The shipped wrapper stamps first; the
  time after the child exits is 10 us on each arm in an unspun cell
  (about 0.017% of the Python work); in a spun cell the baseline arm's
  is absorbed by the spin window and only the candidate's remains. And `test_wrapper_overhead_after_exit_is_negligible` holds it
  under 60 us.
- The slowdown is in the **baseline** arm so the candidate is faster by
  `1 + f` (see "Truth by injection"). `tests/calibration/traces/targets/`
  (since #84: the agent, llm and web targets) holds none of the four
  workloads used here; they are in `studies/golden/targets/` (Node and
  Java derived from the T2.11 sources, `N` reduced; Python and C written
  for this).
- Node and Java recorded 60 rounds, so they have no `gpu_kernels`
  replay (that schedule needs 120).
- The injected size is exact to the clock (achieved spin 2.9563% against
  2.9559% expected at the 1.03 cell, Python; within 0.003 points at every
  spun cell, `summary.md`). The measured mean log ratio of Python, native
  and Node cells is between -0.062 and +0.038 points of expected; four
  cells are beyond 2.4 standard errors (native 1.03 -0.023, z = -3.0;
  python 1.02 -0.062, -2.6; native 1.10 -0.054, -2.5; node 1.00 +0.038,
  +2.5), so a small shortfall for native is not excluded. The 10 us
  after-exit time on the candidate arm of a spun cell explains at most
  0.02 points of a shortfall and cannot explain node's positive one.
  Java's cells run from -0.03 to +0.18 points of expected, three of five
  above +0.1 (1.03: +0.116, z = +2.1; 1.05: +0.132; 1.10: +0.184), with a
  null cell at -0.006 (SE 0.054), so Java's A/A bias cannot be pinned
  below about +-0.1 points and a Java KEEP at nominal 1.03 may not be
  wrong. Two of the five failing gated cells below are Java cells
  (`java/web_agentic`, `java/llm_serving`); the verdict on the gated
  primary does not depend on them, since native/web_agentic,
  native/llm_serving and node/web_agentic fail at 2 of 200 each.

**Wrong-KEEP, cells of 200 (28 per method: 4 workloads, S = 1.00 and 1.03,
the schedules each can replay).**

| Method | Wrong-KEEPs in 5,600 | Worst cell | Worst CP95 | Cells above 2.5% |
|---|---|---|---|---|
| gated primary | 16 | java/llm_serving/1.03: 4 of 200 | 4.52% | 5 |
| `hac_lf` everywhere | 3 | three cells with 1 of 200 | 2.35% | 0 |
| split | 4 | native/gpu_kernels/1.03: 1 of 200 | 2.35% | 0 |

The five failing cells of the gated primary are native, Node and Java on
`web_agentic`, native and Java on `llm_serving`, all at 1.03 (2 or 4 of
200). `hac_lf` passes every cell, with a margin of one error: a cell with 1
of 200 has bound 2.35%, with 2 of 200 it would be 3.11%. The three cells
are node/python_native/1.03, node/web_agentic/1.03 and
java/python_native/1.03. The split differs from `hac_lf` only on
`gpu_kernels`, where it is the gated primary: one more wrong-KEEP
(native/1.03) than `hac_lf`'s none there.

**Power and rounds (`replay.md`, `summary.md`).** Wrong-REJECTs at
S = 1.05 and 1.10: 0 for every method and cell (`cells.csv`). KEEP rate at S = 1.05
and 1.10 over 100 comparisons: `hac_lf` is at or above the gated primary
in every one of the 28 cells (no row in "Where `hac_lf` keeps fewer").
Python on `web_agentic` at 1.05: 76 against 14; native on `llm_serving`
at 1.10: 100 against 0; Java at 1.05 is low for both (20 against 19 on
`python_native`, 7 against 7, 3 against 3) because its log ratio has a
standard deviation of 6.1%. On `gpu_kernels` (Python and native only, 120
rounds) both give 100% at 1.05 and 1.10. Mean rounds to a verdict at
S = 1.10, over workloads: `python_native` 47.1 gated against 21.7
`hac_lf`; `web_agentic` 28.2 against 21.0; `llm_serving` 21.9 against
16.9; `gpu_kernels` 60.7 against 20.5, which is 10.0 against 5.1 s,
6.4 against 4.9 s, 5.0 against 3.9 s and 9.2 against 3.1 s of recorded
execution time. At the null (1.00) the gated primary's mean is 44.8
rounds against 29.4 on `python_native`. The statistics cost per verdict
is 0.2 to 1.0 ms for every method, against seconds of execution.

**McNemar (exact, KEEP against not-KEEP, discordant pairs pooled over
cells).**

| Schedule | S | gated only KEEP | `hac_lf` only KEEP | p |
|---|---|---|---|---|
| python_native | at most 1.03 | 2 | 2 | 1 |
| web_agentic | at most 1.03 | 5 | 0 | 0.0625 |
| llm_serving | at most 1.03 | 7 | 0 | 0.0156 |
| gpu_kernels | at most 1.03 | 1 | 0 | 1 |
| python_native | at least 1.05 | 5 | 82 | 5e-19 |
| web_agentic | at least 1.05 | 5 | 432 | 7e-121 |
| llm_serving | at least 1.05 | 5 | 485 | 1e-136 |
| gpu_kernels | at least 1.05 | 0 | 0 | 1 |

`hac_lf` and the split are identical on every schedule but `gpu_kernels`
(one discordant pair there, native 1.03). The classes of disagreement
(`replay.md`): (a) `hac_lf` KEEP and the gated primary INCONCLUSIVE (84, 432
and 485 comparisons on the three non-GPU schedules; 82, 432 and 485 are at
S >= 1.05, and the other 2 on `python_native` are `hac_lf`'s own
wrong-KEEPs at 1.03, node and Java).
The gate withheld the last look in 76 of 84 on `python_native`
(`autocorrelated_rounds`), 390 of 432 on `web_agentic` (`outlier_rounds`
271, `autocorrelated_rounds` 119) and 182 of 485 on `llm_serving` (129
and 53), mean estimate at the verdict 1.08 to 1.09. The rest are plain
INCONCLUSIVE with an interval still on the bar (8, 42 and 303
comparisons, mean estimate 1.056, 1.060, 1.077). INFERENCE: the gate
fires on short heavy-tailed series whose sample lag-1 is noisy, since
the real lag-1 is near 0 (below). (b) The gated
primary keeps and `hac_lf` is plain INCONCLUSIVE in 7, 10 and 12
comparisons on the three schedules (5 each at S >= 1.05 and the wrong-
KEEPs of (c)), mean estimate 1.054, 1.049, 1.053: marginal estimates
(INFERENCE: `hac_lf`'s critical value is wider there). (c) At
S <= 1.03 the gated-only KEEPs (2, 5, 7 and 1 on the four schedules)
are its wrong-KEEPs (INFERENCE: a narrower interval at 22 to 30 rounds
than the table's worst case over rho).

**Real lag-1.** The order-centred lag-1 of the log ratio in the null
cell: Python +0.006, native -0.023, Node +0.037, Java -0.007. In the A/A
recordings with interleaved order it is between -0.05 and +0.19, apart
from one pinned native pass at +0.22 (wall), +0.30 (CPU) and +0.31
(cycles) (`noise.md`).
The gate and the 0.3 floor were set for a dependence the real data does
not show on this host.

**Noise at the source (`noise.md`; CPU data, one host, 600 pairs per
condition, two passes in opposite condition order).**

| Control | Effect, measured |
|---|---|
| Interleaved against blocked order | Blocked: lag-1 +0.37 to +0.66 on every wall, CPU and cycle series (drift between the A block and the B block), SD of the log ratio up to 2 times larger (Python wall 4.8, 6.5, 5.3 and 5.5% against 2.9, 4.1, 4.8 and 3.5% interleaved; native 2.8 to 3.9% against 2.0 to 2.2%), and a mean bias up to +0.57 points (Python) against within +-0.13 interleaved. Interleaving is the large control. |
| CPU pinning (`taskset -c 0`) | No consistent effect: Python interleaved wall SD 2.9 and 4.1% unpinned, 4.8 and 3.5% pinned (pinned higher in pass 1, lower in pass 2); native 2.2 and 2.2% unpinned, 2.0 and 2.2% pinned. The wall SD of the same condition moved by up to 40% between passes (CPU time and cycles by 52 to 53%, the stats-auditor's figures). |
| Metric: CPU time against wall time | CPU SD against wall SD in the golden null: Python 1.67 against 2.77%, native 0.69 against 1.01%, Node 1.48 against 1.55%, Java 7.04 against 6.11% (larger). A different quantity (it omits waiting), and no gain for Node or Java. |
| Metric: instructions | Native 0.0000% (deterministic), Python 0.07% (nearly deterministic), Node 0.44%, but Java 7.5%, worse than wall (6.1%): JIT-compiled code is not instruction-deterministic. Instructions are a different estimand from time. |
| Frequency and steal as covariates | Regressing the wall-time log ratio on the cycles-per-task-clock ratio and the steal difference explains R^2 of 0.000 (Python), 0.197 (native), 0.041 (Node), 0.053 (Java); SD falls from 1.01 to 0.90% for native and by under 0.3 points elsewhere. |

Would that alone close the GPU gap? **INFERENCE, from CPU data; no GPU
was run.** The gap is a loss of power against the gated primary under
synthetic correlation 0.6 to 0.7, which comes from the width of the
critical value (the table's worst case over rho up to the limit), not
from the noise level. Controls at the source scale the noise: they
narrow both intervals alike, and on these data they move nothing that
the gap depends on, because the real interleaved lag-1 is already about
0. Interleaving, which perfproof always does, is what keeps the lag-1
near 0 (blocked order gives +0.4 to +0.7). So noise control cannot close
a gap that exists by construction at rho 0.6 to 0.7; what it can do is
make the effect large against the noise, where both methods decide and
the gap vanishes, as it does here on `gpu_kernels` (both 100% at 1.05).
Whether a real GPU's paired rounds reach rho 0.6 is not known.

**Decision data.**

- **Under the admissibility rule as registered (wrong-KEEP CP95 at most
  2.5% in every cell), no method is admissible**: the cells of 100 at S
  = 1.02 cannot meet 2.5% with 0 errors (2.95%), and every method had 0
  errors there. The statement "`hac_lf` everywhere and the split are
  admissible" rests on judging only the cells of 200 (S = 1.00, 1.03),
  an amendment proposed by the task author after seeing the data and
  accepted by the owner on 2026-10-23.
  On that amended reading the gated primary is not admissible (5 cells
  above 2.5%).
- Robustness of that amended reading, from the stats-auditor's audit of
  PR #82 (its simulations; not committed, not re-derived by the task author): the
  gated primary is robustly inadmissible, probability of passing again
  about 0.005. `hac_lf` passing is sensitive to the draw: the
  probability it passes again is about 0.39 (cluster bootstrap) to 0.76
  (pooled over the 14 cells), consistent with its margin of one error
  per cell; for the split, the method the owner kept, about 0.29 to
  0.62; for the gated primary 0.006 and 0.005. On `gpu_kernels` the
  golden data cannot show the power gap; the simulation gap the audit
  quotes is -17 points at rho 0.6 and -35 at rho 0.7 at S = 1.10, CV 5%
  on the `gpu_kernels` schedule (at S = 1.05 it is about 7 to 8 points). The cells share comparisons (one recorded comparison is
  replayed on up to four schedules), so the wrong-KEEP counts are not
  independent: the 16 gated errors come from 10 distinct comparisons.
- **Owner's decision, 2026-10-23 (Faras, in chat).** (1) He accepts the
  amendment: admissibility is judged on the cells of 200 only. The
  decision is the owner's; the statement above stands that no method
  is admissible under the rule as registered. (2) Under it the split is
  admissible: 4 wrong-KEEPs in 5,600, no cell of 200 above 2.5%. (3)
  The simplicity order fixed above would have picked **`hac_lf`
  everywhere**; he **kept the split** (`hac_lf` for `python_native`,
  `web_agentic` and `llm_serving`, the gated primary for `gpu_kernels`)
  because of the simulated GPU power gap (-17 points at rho 0.6, -35 at
  rho 0.7, at S = 1.10, CV 5%, `gpu_kernels` schedule), which the golden data cannot test. "The T1.11 decision"
  therefore stands unchanged. (4) The paid GPU golden run is not run
  now; it waits for GPU work.
- It loses to another method in these places, with numbers: (1) 15
  comparisons across the three non-GPU schedules (5 each) at S >= 1.05
  where the gated primary keeps and `hac_lf` does not, against 999 in
  the other direction (counted per schedule); (2) on `gpu_kernels` it is 0 against 1 in wrong-
  KEEPs of the gated primary, no loss; (3) its margin on the rule is one
  error per cell (2.35% against 2.5%); (4) Java is weak for every method
  at 1.05 (3 to 20%), for noise reasons. It gains nothing it does not
  also have on the split except on `gpu_kernels`, where the split loses
  rounds (48.3 against 24.4 at the null; 60.7 against 20.5 at 1.10) and
  adds one wrong-KEEP.
- **Does "split by schedule" stand?** Not shown to be needed by this
  data (INFERENCE). The split exists to protect GPU power at paired
  correlation 0.6 to 0.7, and this data cannot test that: the only
  120-round real data is two short CPU workloads, both methods are at
  100% at 1.05 and 1.10 there (a saturated cell), and the real
  interleaved lag-1 is about 0 (at most +0.04 pooled in the null
  cells). What the data shows is that the split costs two procedures
  and a dispatch and, on `gpu_kernels`, keeps the gated primary's
  one extra wrong-KEEP and its 2x rounds, for no measured gain. The
  synthetic gap (up to 36 points at rho 0.6 to 0.7) is neither
  contradicted nor reached. The owner kept the split (decision above);
  the paid GPU run below is the test that could confirm or retire it.

**Proposed, deferred by the owner (2026-10-23), not run: one paid GPU
golden run on Modal.** Workload: a
small CUDA C++ benchmark binary (matrix multiply or a reduction, 0.1 to
0.3 s per execution, CUDA events as the reported value, spin added to
the baseline arm as in `wrap.py`), so a process start is about 0.3 s and
not the seconds a framework import costs. Cells: S = 1.00 and 1.03 at 200
comparisons each, `gpu_kernels` schedule (120 rounds, 240 executions per
comparison), 400 comparisons: about 96,000 executions, at about 0.4 s
each 10.7 h of one GPU. GPU: a T4 or L4 (the cheapest class that gives a
stable kernel timing); at an assumed 0.6 to 0.8 USD per hour that is
about 6 to 9 USD. The price is an estimate from memory of Modal's list
and must be checked against the current Modal page before approval;
`gpu-run` states the final figures. Fail-fast: a server-less binary, a
health check of the GPU's clocks before round 1, and the log tail on any
failure. It answers the one question this study cannot: whether `hac_lf`
on a real GPU's rounds loses power to the gated primary, and whether its
wrong-KEEP stays at or below 2.5%.

**Not shown.** One host (a KVM guest); four short workloads (0.06 to
0.15 s of work), against the T2.11 traces' 0.4 to 1.5 s; no GPU; no
loaded host (the box was otherwise idle); the golden run's own noise
level moved between hours (from the golden CSVs, the SD of
log(work_A / work_B) pooled over all cells by campaign hour has a
largest-to-smallest ratio of 1.40 Python, 2.12 native, 2.19 Node and
1.15 Java over hours with at least 35 comparisons, and 1.57, 2.69,
2.19 and 1.16 counting every hour, including hours of fewer than
35 comparisons, so native and Node moved by more than 2x and Java by
under 20% either way); the margin of `hac_lf` on the rule is one error
per cell, so the result is "not shown to fail at 200 comparisons", not
"controlled at 1.25%".

### T1.13 results (the split as shipped)

`perfproof compare` decides with `procedure_for_schedule` ("The T1.11
decision"). This run is the acceptance rule of "Acceptance rule", applied
to that selection itself: the harness procedure `split`
(`tests/calibration/montecarlo.py`) sends each calibration schedule
through `procedure_for_schedule` and `run_looks`, the code `compare`
calls per look, and needs no other procedure in the cell.

- **Seed** 5499072700741048927, drawn from the OS entropy pool before
  the first split run. `R` = 4000, both host modes, every speedup of the
  rule (0.95, 1.00, 1.03 for false KEEP; 1.04, 1.05, 1.10, 1.20 for
  false REJECT).
- **Cells:** the main grid (development and exposed scenarios, 1344
  cells) and the held-out grid (1680 cells), 3024 in all. Per-cell
  counts: `tests/calibration/results/t113-split-seed-5499072700741048927.csv`;
  `tests/calibration/test_split.py` reruns every cell under the
  `calibration` marker and requires the committed counts.
- **Result:** 0 of 3024 cells fail. Largest false-KEEP count 68 of 4000
  (1.70%, one-sided CP upper bound 2.08%; `llm_serving/RHO0.7/one_host/1.03`);
  largest false-REJECT count 49 (1.23%;
  `python_native/A20261052-04/cross_host/1.05`). The limit is 83.
- **Monte Carlo error:** a count's binomial standard error is
  `sqrt(R p (1 - p))`: 9.9 errors (0.25 percentage points) at `p` =
  2.5%, 8.2 at the worst observed rate of 1.7%. The worst cell is
  therefore about 1.8 standard errors below the count limit of 83, so
  another seed can move that cell's count past it; the Clopper-Pearson
  bound is the pass criterion, and the standard error says how far a
  rerun at another seed can move a count.

| Grid | Schedule (procedure) | Cells | Failing | Worst false KEEP | Worst false REJECT |
|---|---|---|---|---|---|
| main | python_native (`hac_lf`) | 336 | 0 | 50 A20261052-04/cross_host/1.03 | 49 A20261052-04/cross_host/1.05 |
| main | web_agentic (`hac_lf`) | 336 | 0 | 51 A20261052-04/cross_host/1.03 | 42 A20261052-04/one_host/1.04 |
| main | llm_serving (`hac_lf`) | 336 | 0 | 68 RHO0.7/one_host/1.03 | 45 A20261052-04/cross_host/1.04 |
| main | gpu_kernels (gated primary) | 336 | 0 | 51 student_t_df3/cross_host/1.03 | 28 lognormal_cv15/cross_host/1.04 |
| held-out | python_native (`hac_lf`) | 420 | 0 | 23 H3635800-07/one_host/1.03 | 18 H3635800-28/one_host/1.04 |
| held-out | web_agentic (`hac_lf`) | 420 | 0 | 25 H3635800-10/one_host/1.03 | 12 H3635800-28/one_host/1.04 |
| held-out | llm_serving (`hac_lf`) | 420 | 0 | 24 H3635800-28/one_host/1.03 | 18 H3635800-28/cross_host/1.04 |
| held-out | gpu_kernels (gated primary) | 420 | 0 | 46 H3635800-07/cross_host/1.03 | 30 H3635800-15/cross_host/1.04 |

What this shows and does not. It shows that the shipped selection, run
through the production look driver, meets the rule on the schedules it
names, at a seed chosen after the procedure was fixed. It does not
cover a schedule off the four (decided by the gated primary, whose own
calibration is under "Calibration results"), the real traces beyond the
19 of T1.11, or the golden set's cells beyond what "T1.12" reports;
the golden set's regression subset replays to the same verdicts
(`tests/golden/`, `test_golden_replay.py`).

**Real-trace cell `linux-picolm/fixed20/gpu_kernels/1.03` of the gated
primary.** The cell ran on an interim 141-pair prefix and failed at two
of three audit seeds (85, 90 and 98 false KEEPs of 4000, bound up to
2.89%); the failure stands. The prefix is the first 284 records of the
committed 1000-pair `tests/calibration/traces/data/linux-picolm.jsonl.gz`
(verified byte for byte). Over 40 further seeds at `R = 4000` its pooled
rate was 2.05% (95% interval 1.99% to 2.11%). Reruns at `R = 40000`
(about 2.1%) are diagnostics, not a pass: the acceptance rule above bars
a rerun of a failing cell at a larger `R` or another seed. The
diagnostic at seed `2287571529693778369` (block 20, `gpu_kernels`
schedule, one host, an unkeyed replicate stream) gave 830 false KEEPs of
40000 (2.075%, Clopper-Pearson 95% upper bound 2.196%); `run_real.py`'s
keyed stream at the same seed gives 785. The same seed on the full
1000-pair trace gave 200 of 40000 (0.50%). For scale, a cell of true
rate 2.11% passes the rule with probability 0.47 at `R = 4000` (83 the
largest passing count), 0.978 at 20000 and 0.9998 at 40000 (948). The
trace of record for `linux-picolm` is the 1000-pair file, in a whole
real-trace run at a fresh seed. The committed traces are not the 19
traces of the earlier real-trace runs: those read the Mac traces under
older names, and the committed set has more
(`tests/calibration/real_traces.py`).

### Guardrail calibration results

Written by T2.15, from the cells prespecified under "Guardrail cells".
Two complete runs, each of `R = 4000` replications in each of 1448 cells:
the run of record at seed `7282691080722525374` and the second at
`9113244239656782113`, each drawn from the operating system's entropy
pool after the last change to the procedure (a held allowance no longer
opens a limit check on the same guardrail) and before its run began; the
second was drawn after the first run finished. The per-cell counts,
bounds, rates and coverage counts are in
`tests/calibration/results/guardrails-seed-<seed>.csv`, one row
per cell (`tests/calibration/run_guardrails.py` writes them and
`tests/calibration/test_guardrail_calibration.py` rereads them). The
Monte Carlo error of a count is its binomial standard error,
`sqrt(R p (1 - p))`: 7.0 errors (0.18 points) at `p = 1.25%`, 8.4 (0.21
points) at 1.8%, and 9.9 at the limit's 2.5%. Five earlier pairs of
runs were discarded when the procedure changed under them: at seeds
`1207914073704812776` and `3446032127890382136`, before the audit's
resolution fix; at `2016041637316749836` and `1318306460384291183`,
before the owner's decision on outliers; at `1056003977118079300` and
`3221232511688091121`, under the outlier-hold rule that the owner's
decision of 2026-10-23 replaced with the rank-based bound; and at
`3085425174870641519` and `6809304517246236052`, before the held
allowance stopped opening limit checks (the audit found their counts
differ from a rerun of the code at one cell). None is evidence for what
shipped. The outlier-hold rule was replaced because the hold acts on
the sample: 43 and 44 of the 192 messy limit cells failed with false
passes of up to 49% of runs, because a spike a look has not yet seen is
not held and the data then look clean.

**Result.** Every one of the 1448 cells passes the acceptance rule in
both processes at both seeds, the 192 messy limit cells included, and
every limit cell passes the coverage rule. The largest bound is 2.05%
(INFERENCE of the margin: 83 errors is the most a cell can have and
pass): `web_agentic/RHO0.7/rel1/1.10` at the second seed, 67 false
breaches of 4000 by the guardrail alone (32 in the verdict); at the seed
of record the largest is `gpu_kernels/RHO0.7/rel1/1.10`, 60 alone (1.86%).

By configuration and true value, the most errors in any cell (any
schedule and scenario, both seeds), of 4000, and the largest bound of
the guardrail alone and the verdict:

| Configuration | True value | Truly | Cells (both seeds) | Most errors, alone | Most errors, verdict | Largest bound |
|---|---|---|---|---|---|---|
| `rel1` | 1.00 | within | 224 | 8 | 1 | 0.36% |
| `rel1` | 1.05 | within | 224 | 25 | 3 | 0.87% |
| `rel1` | 1.10 (on the bar) | within | 224 | 67 | 53 | 2.05% |
| `rel1` | 1.11 | beyond | 224 | 53 | 23 | 1.66% |
| `rel1` | 1.20 | beyond | 224 | 14 | 1 | 0.55% |
| `rel1` | 1.50 | beyond | 224 | 0 | 0 | 0.07% |
| `rel3` | 1.10 (on the bar) | within | 224 | 32 | 26 | 1.07% |
| `rel3` | 1.11 | beyond | 224 | 22 | 8 | 0.78% |
| `limit` | 0.90 | within | 184 | 5 | 1 | 0.26% |
| `limit` | 0.97 | within | 184 | 9 | 1 | 0.39% |
| `limit` | 1.00 (on the limit) | within | 184 | 12 | 6 | 0.49% |
| `limit` | 1.01 | beyond | 184 | 2 | 1 | 0.16% |
| `limit` | 1.03 | beyond | 184 | 2 | 1 | 0.16% |
| `limit` | 1.10 | beyond | 184 | 0 | 0 | 0.07% |

The error rate at the cells on the bar reaches 1.7% (67 of 4000, in
the AR(1) scenarios with `rho` 0.7), above the 1.25% the design aims at:
the union bound is a design target, not a measured guarantee, and the
margin to 83 errors there is 16, 1.9 binomial standard errors at that
rate, so a third seed could fail a cell there (INFERENCE, from the
binomial: about 3% for that cell at 1.7%). The `rel3` cells use the gated
batch means on python_native and gpu_kernels (`K G` of 18) and `hac_lf`
on the others (`K G` of 9); all pass. The bounds above are per cell: an
event that spans several guardrails at once (three exactly on their
allowances, `G = 3`) was not calibrated as one cell, and the audit
measured a breach somewhere at up to 96 of 4000 in the worst scenario,
so "held to 2.5%" is shown per check and per verdict, not for that
aggregate.

**Coverage of the limit bounds.** In every one of the 552 limit cells of
each run the bounds on the median cover it with their stated confidence
by the coverage rule above, at `n0` and at `max_rounds`, in clean and
messy cells alike. The rule divides the misses by all `R`
replications, a replication with no bound counting as a hit, so it
shows the bounds' error rate as a share of runs, not as a share of the
values a user is shown; early bounds exist only where the sample's
dependence looked low, and among those few bounded replications the miss
share is higher (the audit measured up to 7 of 18 in one cell at `n0`
and up to 8% of reported values in a held-out cell at the deciding
look). Pooled over the clean cells of both runs, at `max_rounds` the
upper bound fell below the true median in 163 of 1,839,822 bounded
replications (0.009%) and the lower bound rose above it in 319 (0.017%);
at `n0`, 452 of 235,415 (0.19%) and 436 (0.19%). In the messy cells, at
`max_rounds`, 87 of 1,204,251 (0.007%) and 153 (0.013%); at `n0`, 109 of
168,800 (0.06%) and 125 (0.07%). The bounded share at `n0` is small (8%
of replications in a clean cell on average), the replications that have
a bound that early may be those whose dependence the sample underrated
(INFERENCE), which is the effect the 2-standard-error bound on the
dependence is there to limit. A cell where the dependence is underrated
is where this check goes wrong, as in any bound that estimates the
dependence of a short series.

**Power: how often a limit ends `not_established`.** What follows is the
share of runs of the guardrail alone at the end of the comparison
(percent of replications, averaged over the cells of a kind, both seeds;
a clean cell is one without a slow mode or outliers), for a true median
below the limit (0.90, 0.97), on it (1.00) and beyond (1.01, 1.03, 1.10):

| True median over the limit | Clean: passed / breached / not established | Messy: passed / breached / not established |
|---|---|---|
| 0.90 | 49.8 / 0.0 / 50.2 | 28.7 / 0.0 / 71.3 |
| 0.97 | 26.8 / 0.0 / 73.2 | 21.3 / 0.0 / 78.7 |
| 1.00 | 0.0 / 0.0 / 99.9 | 0.0 / 0.0 / 100.0 |
| 1.01 | 0.0 / 11.4 / 88.6 | 0.0 / 35.7 / 64.3 |
| 1.03 | 0.0 / 28.2 / 71.8 | 0.0 / 61.5 / 38.5 |
| 1.10 | 0.0 / 54.0 / 46.0 | 0.0 / 75.3 / 24.7 |

Within the limit by 10%, a messy cell shows the pass in 28.7% of runs
and a clean one in 49.8%; the rest are not established, which withholds
a KEEP at INCONCLUSIVE with the numbers reported (`typical`,
`reliable_value`, `gap`), as "The numbers" above says. A mean-based
interval passed more (70% of clean runs at 0.90 and 45% at 0.97 under
the earlier rule) but also passed candidates beyond their limit (up to
49% of runs at 1.01 and 1.03; at most 2 of 4000 in any cell under this
rule). The cost is power, and it is the owner's decision (2026-10-23).
Clean and messy columns are different scenarios, not a like-for-like
comparison.

**Audit finding on dependence (stats-auditor, T2.15).** The coverage
above is measured on the simulator's dependence, which is AR(1). The
audit measured the rank bound outside it, with its own generators and on
real traces. A local-level series (slow share 0.5, `rho` 0.97) at fixed
`n = 120` missed the median above 10.6% and below 12.1% of the time
against a 0.21% tail, a persistent two-state regime 5.6% and 1.5%, and
on the repo's real A/A traces at `n = 120` loaded Linux rows missed up
to 10.9% and 18.4% and Mac rows up to 19.5% and 32.2%, 14 of 16 Mac rows
at `n = 120` above 4 times the tail (28 of 32 counts `n` = 60 and 120
together) (quiet Linux traces were fine, worst 0.91%; the
windows overlap, so those rates are noisy). Verdict-level false passes
at a true ratio of 1.01 reached 211 to 368 of 4000 on the local-level
series, and two drift scenarios (outside the cells, which exclude drift)
gave 136 and 270 of 4000, and a period-150 oscillation 781 of 4000.
So the stated confidence is a property of
series whose dependence is AR(1)-like; on persistent regimes and drift
it is not shown and the audit found it false. Decision (lead, following Faras's standing rule): keep the rank bound
and scope the claim to the session, as under "Scope of the claim"; no
redesign.
Two replacements were tried at fixed `n` (60 and 120, `R` = 4000, tail
0.21% per side, seeds in the PR) and neither held the tail on the
audit's persistent series at a usable power: a batch-indicator
t-bound (4 to 10 batches) missed the median 3% to 15% on the local-level
series and 2.6% to 8.5% on the regime series, and AR(1) with
`rho` 0.9 still 0.9% to 8%; a rank bound whose effective rounds come
from a Bartlett long-run variance of the exceedance indicator, widened
by a chi-square upper bound, missed 1.3% to 4% on the local-level
series at a 10% widening level and met the tail only at a level (0.001)
where it had a bound in 42% of AR(1) `rho` 0.5 runs at `n` = 120 and 10%
of local-level ones. Whatever widens enough to cover them withholds the
answer on ordinary autocorrelated data.

**What is not checked.** The audit found that a mean-based check fails on
real, unpaired time traces whose own values are strongly autocorrelated
(lag-1 up to 0.94), and that the relative allowance is not calibrated
under one-version skew (the primary's stated limit under "Assumptions"
applies to a guardrail too). The rank bound widens for dependence by the
AR(1) model; the audit ran it on the real traces and measured misses far
above its tail on loaded ones (see "Audit finding on dependence"), and
the claim is scoped to the session for that reason. An out-of-memory ceiling is a claim about the largest
value, and is not checked here.

**What is measured, not only what is judged.** How often the allowance's
guardrail decides (percent of runs, `rel1`, seed of record, guardrail
alone; the rest are undecided at the last look): at 1% noise every
schedule passes at ratios 1.00 and 1.05 (llm_serving 99.9% at 1.05) and
breaches at 1.20 and 1.50 in 100% of runs; at 5% noise the shares that
pass at 1.00 and at 1.05 and breach at 1.20 are 100%, 89% and 100% on
python_native, 98%, 46% and 97% on web_agentic, 88%, 26% and 83% on
llm_serving, and 100% each on gpu_kernels; at 15% noise llm_serving
passes at 1.00 in 10% of runs and breaches at 1.20 in 7%, and
python_native in 51% and 42%. Page-quantised memory with no noise
(`pages_exact`) decides every run at each ratio but 1.10 (on the bar),
where it never decides, correctly: the widening for the page step keeps
the exact ratio from showing a breach. Noisy guardrails hold a KEEP at
INCONCLUSIVE, as "Decision" says.

**Reported at an exploratory seed, not evidence:** the sensitivity
runs and the exploratory grid at seed `12345` under "Guardrail cells".

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
- Each guardrail's status, its worsening estimate and intervals, the
  candidate's interval against each absolute limit, where its allowance
  came from, and what the metric measured (`peak_memory` is the
  largest single process's peak resident set size); the report flags
  every regression within an allowance with how much it costs
- Machine fingerprint: CPU, cores, frequency policy, load, power source,
  thermal state, GPU, driver and clocks when present
- Warnings, for example running on battery or a busy machine
- Paths to the raw samples and any profiles
