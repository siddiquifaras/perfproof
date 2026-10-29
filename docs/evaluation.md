# Evaluation

How we show perfproof works, using evidence neither its authors nor its
own test suite produce. The design and the scoring are fixed here
before any results exist.

The release's evaluation is an **initial external evaluation**: about
30 held-out tasks and 240 agent runs. It is evidence about those tasks
under those conditions, with the uncertainty its counts imply. It is
not proof that perfproof is reliable on every codebase, and nothing
published may describe it that way.

## What is being tested

| Dimension | Question | Measured as |
|---|---|---|
| Discovery | Does it find real opportunities nobody pointed it at? | Share of hidden-fix tasks where a hypothesis names the known bottleneck before any candidate is written; also reported: how often a refuted idea is tried again on unchanged code |
| Implementation | Does it turn a hypothesis into a working change? | Share of tasks with a candidate that passes the independent correctness check |
| Correctness | Does it ever keep a change that breaks behaviour? | Share of kept changes that fail the independent check; target 0 |
| Performance | Are its wins real and worth having? | Precision: share of claimed wins confirmed by the independent measurement. Recall: share of available real wins found |
| Safety | Does it refuse what it should? | False-KEEP rate on negative tasks and decoys |
| Efficiency | What does a verified result cost? | Wall time, tokens and compute to the first verified KEEP |
| Reproducibility | Can someone else get the same verdict? | Verdict agreement when a saved spec is re-run on a second machine |

Precision of claims and the false-KEEP rate are the primary scores.
The rest are reported alongside them; none is traded against a primary
score.

## Task families

1. **Known changes from public repositories.** Merged performance pull
   requests with evidence of their effect, and the other kinds that
   matter just as much: pull requests reverted because they didn't
   help, regressions, and changes reverted for breaking behaviour.
   Mined with `gh search prs` and from release notes, kept only where
   the parent commit builds and a workload exercises the change.
2. **Published benchmarks.** GSO, SWE-Perf and SWE-fficiency tasks
   whose environments run here. The
   [2026 cross-machine audit](https://arxiv.org/abs/2607.01211) is used
   to pick and flag tasks (it found reference patches pass in every
   replay for 39 of 102 GSO, 11 of 140 SWE-Perf and 411 of 498
   SWE-fficiency tasks), not as a source of labels.
3. **Hidden-fix tasks.** The code as it was just before a known merged
   optimization: the change's base commit, exported without later
   history, or a later commit with the change reverted where the base
   no longer builds. The agent gets only the repository and "make this
   workload faster".
   No issue text, no pull request, no hint of where. This is the test
   of discovery.
4. **Nothing to keep.** Code after its optimization landed, or code
   where public attempts to speed it up failed. The right result is no
   KEEP.

Every target class gets tasks. Python and native tasks are plentiful;
web, serving, GPU and agentic tasks are fewer and dearer, and their
counts are reported as they are, with wide intervals.

## Ground truth, independent of perfproof

A label is never produced by perfproof's own code, or the evaluation
would be measuring perfproof against itself.

- **Correctness**: the repository's full test suite at that commit,
  plus any task-specific checks, run on the reference change.
- **Performance**: measured with a separate procedure. Many interleaved
  repetitions (at least 200 pairs) on a quiet machine, with instruction
  counts on Linux where possible, analysed with a different
  implementation (pyperf's tooling and an independent bootstrap), on
  two machines. The label is one of: an improvement at or above the
  bar, below the bar, a regression, or unstable.
- **Unstable** tasks, where the two machines disagree or the effect
  doesn't reproduce, are excluded from the scores and reported
  separately with their count.

## How the runs are matched

- Each task runs under two conditions: an agent with perfproof (skill
  and CLI), and the same agent without it, told to make the workload
  faster and report the speedup, free to benchmark however it likes.
- Same model, effort level, tools (apart from perfproof), wall-time cap
  and token cap in both conditions. Claude Code runs headless with
  `claude -p`, Codex with `codex exec`.
- Every run starts in a fresh, isolated checkout. Git history after the
  task commit is removed, and the network is limited to package
  mirrors, so the answer can't be fetched.
- Leakage is checked after each run: transcripts are searched for the
  reference commit, its title and its pull request number.
- Each pair of task and condition runs at least twice, so run-to-run
  variance of the agent shows up.

The headline comparison is about claims. Without perfproof, an agent's
reported speedups are checked against the independent measurement; with
perfproof, its verdicts are. The question is which claims can be
trusted.

## Counting rules

Fixed before any run, so no result can be dropped after the fact.

| Situation | How it counts |
|---|---|
| Run hits a rate limit before finishing | Retried with the same task and settings, at most twice, after waiting out the limit. Retries are recorded. If it still can't finish, it is **incomplete**. |
| Run crashes, times out, or the agent gives up | **Incomplete**. No retry: that is the agent's real behaviour. |
| Incomplete run | Counts as a failure for discovery and implementation. It never counts as a correct refusal for safety or as a correct "nothing to keep". Incomplete counts are reported per condition. |
| Task's environment breaks (won't build, dependency gone) for both conditions | The task is **failed** and excluded from every score, listed with the reason. If it breaks for only one condition, the run is incomplete for that condition. |
| Unstable ground truth | Excluded from performance scores and reported apart, as above. |
| A KEEP that independent measurement shows is not a real win | A false claim. Counted against precision, whatever the verdict's reason. |
| A KEEP that fails the independent correctness check | A correctness failure. Counted against correctness and precision. |
| Negative results: perfproof scoring worse than no perfproof on any dimension | Published as they came out, in the same table. |

The two repetitions of each run are scored separately, and their
disagreement is reported as run-to-run variance of the agent.

## Splits

- **Development tasks**: used while writing the skills and playbooks.
- **Held-out tasks**: never seen during development, run once per
  release candidate, scored by the rules above.

A task used for development never moves to the held-out set. A task
named in a development task file (`eval/skill/tasks*.json`) counts as
used for development, and the held-out draw skips it, recording that as
the reason.

**How the held-out set is chosen.** The mined manifests are a
candidate pool. The selection rule is fixed here before any task is
built for it:

1. Candidates whose family or class is wrong on reading their pull
   request or commit (not an optimization, a revert for a build or CI
   failure, a workload that doesn't exercise the change) are removed
   first, each with its reason recorded. This reads only the change,
   never a result. A task a development task file names is skipped
   too (see above).
2. The rest are ordered by `sha256(salt + task id)`, with the salt
   `perfproof-heldout-2026-10-16`.
3. Per target class, the held-out set takes, in that order, the first
   candidates that build and run here (or on the class's GPU host): two
   known-change tasks, one hidden-fix task, one nothing-to-keep task and
   one published-benchmark task, five in all. Where a class has no
   candidate of a family, the next known-change task takes its place. A
   slot no candidate can fill is recorded as unfilled and the draw goes
   on to the next slot, so a small pool gives a held-out set of fewer
   than five.
4. Apart from step 1, whether a candidate builds is the only thing
   looked at, and it is judged on the task tree alone: its setup, its
   workload and its required oracle at the task's starting commit.
   What the reference change does there (fails to build, hangs, fails
   its tests) is a result, recorded for labelling, and never decides
   selection. No perfproof run, agent run or label of a held-out
   candidate is seen before the list is committed.
5. Every candidate passed over is recorded with the reason it didn't
   build, so the selection can be re-derived.

The development set is drawn from the rest of the pool the same way,
with the salt `perfproof-development-2026-10-16`.

**Discovery supplement (fixed 2026-10-28, before any task is built
for it).** The per-class rule above gives about one hidden-fix task per
built class, too few to measure discovery. A second held-out draw takes
hidden-fix tasks only:

1. The pool is every mined hidden-fix candidate. Step 1 above applies,
   and so does the skip of any task a development task file names, or
   that the per-class held-out draw already took.
2. The rest are ordered by `sha256(salt + task id)` with the salt
   `perfproof-heldout-discovery-2026-10-28`.
3. It takes, in that order, the first 8 that build and run on this Mac,
   judged as in step 4 above, with at most 2 from any one language. A
   pool too small for 8 gives fewer; more candidates may be mined first
   with `eval/mine`, by the same mining rules, before the draw is made.
4. Steps 4 and 5 above apply. The skill is frozen at a recorded commit
   before the first run, and the set is run once for that commit and
   scored by the hidden-fix rule. Nobody who edits `skills/` reads the
   tasks before that run.

These tasks live in `eval/skill/tasks-heldout-discovery.json`; they
never become development tasks.

## What we publish

`eval/` holds every task manifest (repository, commit, workload,
oracle, label, the evidence for the label), the runners, the pinned
environments, and the raw results. Rates are reported with
Clopper-Pearson intervals, per target class and overall. Small classes
are reported with their real counts, never folded into an average that
hides them.

## Skill eval on real repositories

`eval/skill/` also runs the skill, through `claude -p`, on real
repositories built from the development manifests above
(`eval/skill/tasks-real.json`; the examples-based set stays in
`tasks.json`). Each task clones the public repository at the manifest's
commit from a local cache into a clone with no remote, in a work
directory outside the perfproof checkout (`--work-root`, default
`~/.cache/perfproof-eval`), runs
the manifest's build steps (Go, Ruby and .NET natively: the toolchain
directory of each task's `requires` goes first on `PATH`, from
`~/.local/go`, `~/.local/ruby` or `~/.local/dotnet` when it is not
already on `PATH`; a manifest may still name a Docker launcher with
`shim`, which is run only with `--no-eval-user`; the task's Go module
cache is filled by `go mod download` before the agent starts),
installs the skill and sends
the first message. A task whose toolchain is missing is recorded as
skipped with the reason, not failed. Only development manifests are
used, with one addition. A pool candidate drawn into neither split
(`development-candidate`) is used when it built and ran here with its
workload and oracle verified, and the selection rule's own draw
(`eval/mine/draw.py`, run by `realrepo.load_spec`) shows its class's
held-out draw complete (every slot filled or recorded as unfilled),
with no unbuilt picks, under the current pool and statuses: it is then not a held-out task. The `split` field is
written only by that script, so it is never edited to promote a task.
A held-out task never enters this set. Serving and GPU (unbuilt
picks) stay out until their held-out draws are made and built; a change to the pool or statuses
can change this answer.

| Family | Seeded as | Prompt | Passes when |
|---|---|---|---|
| hidden-fix | the task commit, no later history | `/perfproof light` (the Django task adds "Our login-protected views are slower than they should be.": a user names the slow area, never the function or the fix), then "yes, go ahead with your suggestions" | the known function is in the first 15 rows of either list (`taskset.SKILL_READ_ROWS`: the rows the skill tells the agent to print and read, kept equal to the skill by a test) (when the task directory holds `profile-<name>-<id>.json`, the `result.json` files `perfproof profile` saved, the lists are those files': the union of the first N of `hotspots` and of `frames_by_total` of every result whose `finished_at` is before the `timestamp` of the event carrying the first detected edit, with no reading of what the agent printed; a profile id in a tool call is not used, since a piped call does not show it; runs archived before the CLI saved results, and any with a file that cannot be read, use the printed text as follows) as the agent printed it (`hotspots`, by self share, or `frames_by_total`, by total share; the output must name the list, by the CLI's table titles or by the key or heading, not a list of key names, else it is read as one list by total share, and a row is never re-ranked by the other list's share) of the first successful `perfproof profile` call before the first detected edit of the code (a failed attempt before it is fine; detection is pattern based, so an edit made in a way the patterns miss is not seen) and is named after it (the small examples need rank 1; the two rules are counted apart); a function matches by its short name as profilers print it (argument list, Rust hash, package path and receiver punctuation stripped; the owner path compared as a trailing match when the manifest gives one; a frame with no file needs such an owner; `#` and `.` stay distinct), and is named after it only in its owner-qualified form (`Echo.ServeHTTP`, not `ServeHTTP`) and only for the accepted function the profile showed, and a manifest may accept further (function, file) pairs with a stated reason, for a function the compiler inlined or a wrapper that runs per call; a `perfproof/*` branch, if any, passes the manifest's tests and edits no test file; tree clean; `Result:` line and wording rules hold; no network lookup of the fix |
| known-change | `main` at the parent, the fix on `pr-<n>` | a plain "is this PR actually faster?" naming the branch | the skill starts, `compare` ran, the verdict is INCONCLUSIVE, no code changed, `Result:` line correct |
| nothing-to-keep | the merge, optimization already in | `/perfproof light`, same follow-ups | no KEEP, tree clean, wording and `Result:` line hold |

**What the hotspot check reads, `--sizes` included.** The runner keeps
every `.perfproof/profiles/<name>/<id>/result.json` as
`profile-<name>-<id>-<hash>.json`. A `perfproof profile --sizes` run
saves one such result per size (with a `size` key) and writes
`growth-<group>.json` beside them: the per-size results are kept and
read by the rule above, as plain ones are; the growth file is not kept
and not read. A `profile show --rank` print is not "a real perfproof
profile output" here: when saved results exist the check uses only
them, and the printed text is the fallback only for runs archived
without any. (Found on `heldout-1`: the archive had kept the size
results; the hotspot check read none of them, because the first-edit
detector took a spec edit, a Python here-document writing
`.perfproof/specs/<name>.json` through a path it builds, for an edit of
the code, so every profile counted as after the edit. The detector now
reads a path built from a literal `.perfproof/` or `.claude/` start as
scratch, and a Python here-document that takes arguments after `-`, or
runs inside `do`/`then`/`else`, as the one it is. A computed tail that
climbs out of the directory is not seen: a crafted `python3 - ../src/x`
with `open(".perfproof/" + sys.argv[1], "w")` is now missed, and a
string-concatenated tail that climbs out with `..` was already missed.)
Setup also fails, before any agent runs, with "setup modified tracked
files: <paths>" when the build leaves a tracked file (outside
`.perfproof/`) changed, as an `npm install` that rewrites a lockfile
would; such a task's setup uses `--no-package-lock`.

**Held-out setup in the eval user's sandbox.** The confined setup may
not write the real `~/.rustup`, so a Rust task whose
`rust-toolchain.toml` names a release the eval user lacks failed in
rustup's sync before any build. `realrepo.prefill_rust_toolchain` runs
the one `rustup toolchain install <channel> --profile <profile> -c ...`
the pin names, outside the profile and before the confined setup, as
the Go module download is (`runner.before_setup_for`, eval user and
`cargo` tasks only). Only a plain release, `stable`, `beta` or
`nightly` channel and plain component, target and profile names are
read from the repo's file; anything else is left to the confined build.
This keeps the sandbox as it was; the alternative, allowing writes to
`~/.rustup/tmp` (and, for the install itself, `toolchains/`) during
setup, would let a build script alter the toolchains every later task
uses. A task whose build needs git submodules lists them in its
`checks.json` entry as `submodules`; `realrepo.seed_repo` checks out
those paths shallowly at the commits the task commit names. The
mining check (`eval/mine/check.py`) extracts every submodule already.
`rolldown-rolldown-pr10678` does not build in the eval user's sandbox,
and the cause is not the build order. With the pinned toolchain
installed, `build-native:release` compiles the binding, then
`@napi-rs/cli` (3.8.6, pinned by the lockfile) runs `/bin/ps -o lstart=
-p <pid>` for its file lock and dies with `spawn EPERM`; the later
`build-node` and `tsc` steps fail as a result (no binding, no
`dist/*.d.ts`: "No inputs were found"). `/bin/ps` is setuid, and
`sandbox-exec` refuses a setuid exec under any profile (probed:
`(allow default)` alone gives EPERM; unconfined as the same user it
runs). A `ps` earlier on `PATH` does not help, the path is absolute.
Running that setup step unconfined would give a repo build script the
eval user's token and the staged harness, so it was not done. The
draw record passes it over (`passed_over`, a human input of
`eval/mine/heldout-discovery-draw.json`, dated, with the confined
setup's exit status) and the walk continues in the committed order:
`uuidjs-uuid-pr677` (JavaScript, built here by `check.py` and as the
eval user through the confined setup, exit 0) is the replacement.

**Hidden-fix trees hold no workload made for the task.** A hidden-fix
agent writes its own workload. Setup used to copy the task's own
benchmark into the tree (`eval_bench.js` for fastify, whose comment named
the PR and the child logger; `eval_servehttp_bench_test.go` for Echo;
`eval-wl/Base64Workload.class` for Jackson, and the same kind of file
for simdjson, Markdig and fd's `eval-tree`). Each `checks.json` entry
that does this lists the paths in `planted`, and for a hidden-fix task
(`taskset.Task.is_hidden_fix`: a `find` task with a known hotspot)
`realrepo.run_setup(..., hide_planted=True)` deletes them after the
build and before the untracked files are written to `.git/info/exclude`,
so neither the files nor their names are left. The harness's own
workload and oracle checks (`eval/mine/check.py`) run the same setup
commands in their own trees and are untouched. Known-change and
nothing-to-keep tasks hand the agent a workload on purpose and keep
their files. `eval_builder_tests` (simdjson) stays: it is the oracle's
binary, built from the project's own tests. Traces outside the tree
(the toolchain's build cache in the task's private home) are not
removed; whether they hold anything the agent could read as a hint
was not checked.
`test_hidden_fix_tasks.py` checks that a hidden-fix manifest whose setup
uses a task workload file lists at least one planted path, that each
listed name appears in its setup or workload script (not that the list
is complete), that a setup run with `hide_planted` leaves none of it in
the tree or in `exclude`, and that the runner passes the flag for
hidden-fix tasks only.

**Tasks excluded from the pass bar.** A task whose win cannot be seen
end to end cannot be passed honestly, and counting it would hide real
results. Such a task carries `bar_exclusion` (a reason and the
evidence) in its task file; the scorer still scores it, `scores.json`
keeps its `bar_excluded` reason, and the report lists it under
"Excluded from the pass bar". `Summary.tasks` and `passed` leave it
out and `excluded`, `excluded_passed` count it. The wrong-KEEP count,
cost and the per-check rates still include it, so no guard is
loosened. Two tasks are excluded, with the evidence in their task
files: `real-django-hidden-fix` (the cap of the fix is about 1.017, under
the 3% bar; the PR's win, 749 ns to 262 ns per call, is invisible in a
login-protected request) and `ruby-rails-find` (`String#blank?`, a
micro win of the same kind). The Django cap comes from the round 9 run 1
diagnosis and was not recomputed for the fixed function; nothing was
measured for the Rails task.

**Prompt variants.** Real users often give no workload, ramble, or
overstate. Eight tasks repeat a task with only the prompt changed, and
keep its repository, follow-ups, locks and scoring; their ids carry
`-msg-` and the report counts them on their own line. A prompt never
names the hidden function (a test checks the manifest's names).

| Style | `tasks.json` | `tasks-real.json` |
|---|---|---|
| no workload | `find-rolling-msg-bare` (`/perfproof` alone) | `real-fd-msg-bare` ("make this faster") |
| vague | `find-membership-msg-vague` | `real-fd-msg-vague` |
| rambling, one hint buried | `find-rolling-msg-rambling` | `real-xarray-msg-rambling` |
| wrong claim | `find-membership-msg-claim` (the database layer; this repo has none) | `real-xarray-msg-claim` (reading netCDF from disk; the workload is in memory) |

The wrong-claim tasks have no extra check. They pass on the task's own
rule: the known function ranks in a real profile before any edit and is
named without a negation, so an agent that chased the claim without
profiling, or never named the real cost, fails.

**What the agent can reach.** The agent has unrestricted `Bash`, so
three layers (`eval/skill/sandbox.py`) stand between it and the owner's
accounts and the answers. The profile is an allowlist; an earlier
deny-list version was beaten by renaming a denied directory, by copying
a denied binary, and by files nobody had listed.

1. *Environment.* An allowlist of variable names (an unknown one is
   dropped), `gh` replaced by a stub that fails with `GH_TOKEN` set to a
   sentinel (`gh auth token` returns the owner's keychain login even
   with an empty config directory), and git told through
   `GIT_CONFIG_COUNT` to use no credential helper, fsmonitor, hooks or
   ssh (command-line scope, so a repo's `.git/config` cannot undo it).
   The harness's own git, test and archive steps on an agent's tree get
   the same environment and confinement.
2. *Confinement.* On macOS the agent's `claude -p`, and the harness
   steps that run on its tree, start under `sandbox-exec`. Without it
   the agent run refuses to start.
   - **Reads:** the whole home directory and the perfproof checkout are
     unreadable (metadata only, so `stat` of a parent works), and so is
     `/private/tmp/claude-<uid>` (also unwritable) and the per-user
     `/private/var/folders` (which holds other apps' caches). Allowed
     back: the task tree and the
     run's launchers; the perfproof environment (`.venv`, `src`); and
     the toolchains, `~/.local/bin`, `~/.local/share/uv` (managed
     Pythons), `~/.local/share/claude` (the `claude` install),
     `~/.local/jdk`, `~/.local/go` (without `$GOROOT/go.env`, `go env
     GOPROXY` is empty and module downloads fail), `~/.local/dotnet`,
     `~/.local/ruby`, `~/.dotnet/tools` (the eval user's `dotnet-trace`; only when the agents run as that user), `~/.cargo/bin`
     (not its `config.toml` or
     `credentials*`), `~/.rustup`, `~/.nvm`. System paths stay readable.
     Nothing of `~/.claude`, `~/.claude.json`, `~/.ssh`, `~/.config`,
     `~/Library`, shell history and rc files, other projects, or earlier
     runs under `scratch/` is allowed back.
   - **Writes:** denied everywhere except the task tree, the agent's own
     `claude` config directory (`CLAUDE_CONFIG_DIR`, a fresh directory in
     the work root, which is where its transcripts and session files
     go), `/dev`, and a private directory per task that holds its
     `HOME` and `TMPDIR`. A private `HOME`, not cache variables, because `perfproof`
     gives its benchmark and profiler commands only `HOME`, `TMPDIR` and
     a few others: a first run with `UV_CACHE_DIR` failed in
     `perfproof profile` (`uv` fell back to the owner's `~/.cache/uv`,
     denied; that run was not archived, so this is my observation, not
     a record, but `BASE_ENV_VARIABLES` in
     `src/perfproof/bench/side_effects.py` shows the cause). The
     private home links the real `~/.rustup` (read-only to the agent). The shared
     caches under the real home are not writable, because the unconfined
     setup builds and the owner's own work read them; the cost is that
     crates and wheels are downloaded again for every task. With writes
     confined, a denied directory cannot be
     renamed into a place that can be read (`test_a_denied_directory_cannot_be_renamed_into_view`, which also
     runs the same attack on the old read-only profile and shows it works
     there).
   - **Docker:** outbound connections to the daemon's unix sockets
     (`/var/run/docker.sock`, `~/.docker/run/docker.sock`,
     Docker Desktop's container directory) are denied for tasks that
     do not need Docker; the tasks that do get `docker.sock` only (no
     mutagen socket), so a copy of the client cannot get through
     (`test_the_docker_sockets_are_closed_even_to_a_copied_client`).
   - **Mach (XPC) services:** an allowlist, like files. The profile
     starts from `(deny mach-lookup)`, because a service the agent can
     look up is a service it can use as the owner: the pasteboard (the
     clipboard often holds a token), LaunchServices (`open`), AppleEvents,
     the keychain's securityd (`keychain_probe.py`: reachable without the
     rule, an error with it), a password manager's helper. Only two
     names are allowed back, each found by running the commands the
     tasks use under the profile with none allowed:
     `com.apple.coresymbolicationd`, which macOS `sample` (the skill's
     fallback profiler for native programs) needs to symbolicate (it
     fails with "Invalid connection: com.apple.coresym..." without it),
     and `com.apple.system.opendirectoryd.libinfo`, which `getpwuid`
     uses (without it `id -un` prints `501`). `com.apple.sysmond` is
     deliberately not allowed. Round 7's fd run failed with "sysmond
     service not found" on `sample fd 3` and `pgrep`, which look a
     process up by name. Measured here: `sample <pid>` of a child, which
     is what `perfproof.profilers.native record` does, works without it
     (`test_perfproof_native_recorder_works_under_the_profile`); with it
     allowed, `pgrep -fl` printed other sessions' full command lines,
     and `sample <name>` still failed on the closed `kern.proc` sysctl
     (`test_other_processes_command_lines_stay_hidden_without_sysmond`).
     So a name lookup stays unavailable and the agent has to sample by
     pid. Run under the profile with
     at most those two names, and working (`claude -p`, `probe-6`, with
     none): `claude auth
     status`, `git`, `python` (including `getaddrinfo`), `perfproof
     doctor` and `init`, `clang` compile and link, `cargo new`/`add`/
     `build`, `uv venv` and `uv pip install`, `npm view`, `node`, `curl`
     to PyPI, and the end-to-end task (`perfproof profile`, `measure`,
     `compare`). Tests: the clipboard cannot be read through `pbpaste`
     or through `NSPasteboard` from `osascript`; `lsappinfo` cannot see
     Finder and `open` of a URL fails; both names work. I took the
     allowlist, not the fallback list of explicit denies.
     `osascript -e 'return 1'` still works: a script that talks to no
     other process needs no Mach service. One that sends an AppleEvent
     does, and is closed (I did not test that, to avoid a permission
     prompt on the owner's machine).
   - **Temp dirs:** `/private/tmp`, `/private/var/tmp` and
     `/private/var/folders` are closed to reads and writes (metadata
     only). Every task's `TMPDIR` (and zsh's `TMPPREFIX`, which is where
     zsh puts here-documents) is inside its private directory, so
     Python's `tempfile`, here-documents, `git init` and `clang` work; a
     hard-coded `/tmp/x` fails with "Operation not permitted". A first
     version that left zsh's `TMPPREFIX` alone made every heredoc fail
     under the profile (seen in an end-to-end run, not archived). `clang`
     and `git` through the `/usr/bin` shims call `xcrun`, which writes a
     cache file into the per-user temp directory that `confstr` returns,
     ignoring `TMPDIR`. No exception is made for that file (a prefix
     rule let an agent plant a lookalike and write the owner's real
     one). Instead the agent's environment names `DEVELOPER_DIR` and
     `SDKROOT`, and a private directory of links to the compiler and
     linker tools (`clang`, `ld`, `ar`, `git`, `make` and a few more) is
     put on `PATH` ahead of the system's, so the real tools run and
     `xcrun` is not called. Only those names are linked: putting all of
     `$DEVELOPER_DIR/usr/bin` first shadowed 77 tools, `python3` and
     `pip3` among them (tested that they still resolve to the project's).
     `GIT_TEMPLATE_DIR` is an empty directory, since Xcode's `git` has
     no templates and warns on every `init` (`clang` compile and link and
     `cargo build` verified under the profile, without the exception).
     `NODE_USE_SYSTEM_CA` is dropped from the environment: node's
     system-CA loading calls the Security framework, which the keychain
     rule closes (`npm view` failed with `SecItemCopyMatching failed
     -50`).
   - **Unix sockets:** `connect()` ignores file-read denies, so before
     this rule an agent could use any socket the owner had open: it
     listed the owner's key through the ssh-agent socket (`ssh-add -l`),
     and could connect to a live `claude` process's socket and to a
     root service's. Now every outbound unix-socket connection is
     denied, with three kinds allowed back: the name-resolution socket
     `/private/var/run/mDNSResponder`, sockets inside the task's own
     writable directories, and the Docker daemon's sockets for the tasks
     that need Docker. DNS and HTTPS still work (`getaddrinfo`, `curl`
     to PyPI, `uv pip install`, `npm view`, `cargo add` and `cargo build`
     with a dependency, and a `claude -p` probe, `probe-5`, all run
     under the profile). `test_unix_sockets_cannot_be_connected_to`
     plants listening sockets in a denied home directory and in shared
     temp and asserts that `connect` fails there and succeeds in a
     writable task path.
   - **Process introspection:** the arguments and environment of any
     other process of the same user can be read with `sysctl`
     (`KERN_PROCARGS2`), which would bypass the environment allowlist:
     the harness's own environment is the owner's, and a live `claude`
     holds tokens. Three rules close it for processes outside the
     agent's own: `(deny process-info* (target others))`, a deny of
     `sysctl-read` for `kern.proc*`, and `(deny mach-priv-task-port
     (target others))`. They only work for a reader in another session:
     a process in the reader's own session (the harness, or its
     siblings) was still readable in my tests, so the harness starts
     every confined command, and the agent, in a session of its own
     (`start_new_session`; asserted for each call in a test). Measured
     with a reader in its own session: the proposed pair (`process-info*`
     plus `kern.proc*`) stops the procargs read (`-1`) and `vmmap` on
     another process but not `sample`; adding the task-port rule stops
     `sample`, `vmmap` and procargs on others. The agent's own children
     keep all of it (`sample` and `vmmap` of its benchmark, procargs of
     its child, `multiprocessing.Pool`, `wait`, `kill`), so the skill's
     `sample` fallback still works. `ps` is refused. Tests plant a
     target with a marker in its environment and fail without the rules
     (mutation run).
   - **Exec rules** for `security` and the real `gh` are kept but not
     relied on: a copy of either is stopped by the read, keychain and
     socket rules (tested for `gh` and `docker`), not by the exec rule.
3. *Credentials in transcripts.* `claude` signs in through the keychain,
   which the profile closes (probe 0: not logged in). The harness reads
   the short-lived access token outside the sandbox and passes it as
   `CLAUDE_CODE_OAUTH_TOKEN`; the refresh token stays with the harness.
   Every sign-in value in the environment handed to `claude`, from
   whichever source, is redacted (as is, reversed, hex and base64 forms)
   from the recorded events and the stderr file, and `archive` scans the
   files it would copy for each value and form and refuses on a hit.
   Forms also match when whitespace splits them (wrapped base64,
   `xxd -p`), in upper-case hex, and URL-encoded. The values this
   process handed out are kept in memory only; `run --archive-to` scans
   with them. A separate `archive` command knows only the current
   sign-in, and refuses when it cannot read one rather than scanning for
   nothing. Each sign-in path ends a run differently. The keychain's
   access token (`--no-eval-user`) carries an expiry: a task needs at
   least thirty minutes left (`sandbox.TOKEN_MIN_REMAINING_S`; the longest
   task wall time in round 7 was 1009 s, but a task that runs to its
   timeout of 30 to 50 minutes can outlive the token, which the agent
   cannot renew). With less, the harness runs one cheap `claude -p` turn
   outside the sandbox, with no MCP servers or tools, which makes the CLI
   renew its token if it is about to expire, and reads the expiry again;
   only expiry times are looked at. The CLI seems not to renew early (one
   observation, not recorded: a turn with 44 minutes left changed
   nothing), so if the token is still short the run stops with the time
   left and says to wait or sign in with `claude auth login`. The eval user's
   token file is an opaque long-lived token with no expiry to read, so
   two checks stand in for it: a tiny `claude -p` call, run once per
   host run by a guest `auth-probe` before the first task (the task guests
   are told `--sign-in-checked`; a guest started on its own probes before
   its first task), stops the run with `SandboxUnavailableError` if it
   fails or times out (recorded as `timed_out`, not signed in), and a call whose `result` event is an error with HTTP status 401
   or 403 (observed for a rejected token: `is_error` true,
   `api_error_status` 401, `terminal_reason` `api_error`) stops the run,
   drops that task's partial record and is not scored as a task failure.
   Both are stubbed unit tests. The classifier test reads a real capture,
   `eval/skill/fixtures/claude-rejected-token.jsonl` (claude 2.1.291, in
   `claude-rejected-token.version`): one `claude -p` call as `ppeval`
   inside the profile with a made-up token, rc 1. The file holds no
   token-shaped string (the made-up token is not echoed); it names
   `/Users/ppeval` paths only.
   The harness's own unconfined reads of the agent's tree do not follow
   links: the test copy keeps symlinks as symlinks (a planted `ln -s
   ~/.ssh/id_rsa lnk` is only ever read by the confined test run),
   `git archive` stores links as links, and a run record that is a
   symlink is skipped.
4. *Scoring.* Any call, of any tool, that touches `eval/tasks`, the
   reference commit, pull request or title, a network lookup, or an
   install of the project from an index is a `leak` or
   `harness_access`; any edit of a test file, test configuration or a
   file the oracle names is `locked_file_edited`.

Which paths the profile needed was found by running the commands the
tasks use under it: `git`, `python`, `perfproof doctor` and `init`,
`cargo new` and `cargo build`, `uv venv` and `uv pip install`, `clang`,
`node`, `npm`, `sample` and `claude auth status`. All ran. The one
thing the profile blocked that `claude` needs was its temp directory
`/tmp/claude-<uid>` (it also holds other sessions' scratch, so it stays
closed): `claude` failed with `EEXIST ... mkdir '/tmp/claude-501'`
(`probe-3`). It is redirected with `CLAUDE_CODE_TMPDIR` into the agent's
config directory (`probe-4`). A full `find` task then ran under the
profile (`runs/sandbox-e2e`): two calls with `--resume`, fourteen Bash
calls (init, profile, measure, a branch commit, compare), no
"Operation not permitted" in any recorded output. It was run again
after the private home, private `TMPDIR` and closed
`/private/var/folders` (`runs/sandbox-e2e-2`), and again after sockets and shared temp were
closed (`runs/sandbox-e2e-3`). Not exercised under it as eval tasks:
the JDK, Go, Ruby and .NET (only their manifests' commands were run
behind the profile, see "Native Go, Ruby and .NET"), and Rust under
the private home (`cargo new`, `cargo add` and
`cargo build` ran under the profile by hand; no archived record, no
real repository such as `fd`).

**Running as a separate macOS user (T3.20).** The profile above
works by listing what is allowed, and review kept finding same-user
leaks in the list. By default the eval now runs every agent session,
and every harness step that touches an agent's tree, as the account
`ppeval` (uid 502, its own group, hidden, home `/Users/ppeval`), inside
that same profile: two layers. The owner's account may run commands as
it with `sudo -n -u ppeval -H`, which grants that user only, never root.
`--no-eval-user` is today's behaviour (the agent runs as the owner,
behind the profile only). It is an explicit flag, never chosen
automatically: with no flag and no `ppeval` (or no passwordless sudo to
it) `skill_eval.py run` and `auth-probe` stop with a message that names
the flag.

*Layout.* The harness is split along the user boundary
(`eval/skill/eval_user.py`):

- The *host* (the owner) runs `uv build --wheel` on the checkout, packs
  a bundle (the harness modules without their tests, `eval/tasks`,
  `eval/mine/checks.json`, `examples/`, the skill, the task file and the
  wheel) and extracts it as `ppeval` through `tar -x` on standard input,
  so every file is owned by `ppeval`. It installs the wheel with `uv
  tool install --force --reinstall --python 3.12 --with pytest` as
  `ppeval`, so the agent's `perfproof` is the package a user would get
  from the wheel (`pytest` is in the same environment for the examples
  tasks' tests). The agent never needs the owner's checkout.
- The *guest* is the ordinary `skill_eval.py run --no-eval-user`,
  started as `ppeval` from the bundle, one task per call so the host can
  honour `--pause-marker` between tasks. It seeds the task repositories
  under `/Users/ppeval/eval-work/work/<run>/repos/<task>`, runs the
  setup builds and the oracle, starts each agent behind the profile
  (adapted by construction: `Path.home()` is `ppeval`'s, so the home
  deny, the toolchain allow-list, `HOME` and `TMPDIR` are all under
  `/Users/ppeval`; the `(deny mach-lookup)` allow-list, the unix-socket
  deny and the process-introspection denies are unchanged), redacts and
  scans, scores and archives into `eval-work/archive/<run>`.
- `PATH`, `JAVA_HOME` and the toolchains come from `ppeval`'s own
  installs (`~/.local/bin`, `~/.cargo/bin`, Temurin under
  `~/.local/jdk`, node under `~/.nvm`; Go, Ruby and .NET under
  `~/.local/go`, `~/.local/ruby` and `~/.local/dotnet` are found by
  the harness from a task's `requires`, `realrepo.toolchain_dirs`),
  set by a launcher that its own shell runs. `sudo` passes the owner's `PATH` through, so the command
  is `env -i` plus a short list of names.

*Results come back as data, by copy-out, not a drop directory.* The host
runs `sudo -n -u ppeval -H tar -c` on the scanned archive and extracts
the stream itself, accepting only regular files and directories with
names that stay inside the target, up to 1 GiB (a link or device entry
is refused, so nothing in the archive can aim a later write at the
owner's files); then it re-scores the files as data. Nothing from them is
ever executed as the owner. A drop directory would need a location that
the owner can read and `ppeval` can write but not read back (an ACL
setup that is itself easy to get wrong, and a second path by which an
agent-controlled user writes into the owner's files); the copy-out has
one direction, one command, and one filter.

*Secret scanning and redaction run on the `ppeval` side, fail closed.*
The token is in `/Users/ppeval/.config/perfproof-eval/token` (mode 0600,
owned by `ppeval`). The host never reads it, so the host cannot redact
or scan with it. This is a convention, not a barrier: `(ppeval) NOPASSWD:
ALL` lets any process running as the owner run commands as `ppeval` and
so read the token and every tree in its home. The guest harness reads the file itself
(`PERFPROOF_EVAL_TOKEN_FILE` names the path; the value never appears on
a command line, in an environment passed through `sudo`, or on disk
elsewhere), hands it to the agent as `CLAUDE_CODE_OAUTH_TOKEN`, redacts
every form from the recorded events and stderr as before, and
`archive` scans with all forms before the copy, refusing on a hit (or
when it has no token to scan with). A scan hit makes the guest exit
non-zero before the archive directory exists, and the host copies
nothing. I chose this over passing salted hashes or patterns to the
owner's side because the scan needs the token's forms at the byte
level in the files, which is cheaper and safer to do where the token
already is than to approximate from hashes (a hash cannot find a token
split by whitespace or re-encoded). The cost: the owner's side cannot
re-verify the scan; the pulled run carries `eval-user.json` (the wheel
and bundle hashes, the exit codes, "redaction and scan run by the eval
user") and the owner's `archive` command, which has no way to scan for a
different user's token, copies such a run without rescanning only when
told to (`archive --scanned-by-eval-user`); the file's presence is not
trusted. The host also kills every process of `ppeval` (`pkill -KILL
-U`, `eval_user.sweep`: that account exists only for the evals) before
staging and after every guest call, and when the host is interrupted,
because `sudo` dying leaves the guest running and the owner cannot signal
it. That would kill a second eval's guest, so one eval holds the eval
user at a time: `eval_user.exclusive`, an `flock` on
`~/.cache/perfproof-eval/eval-user.lock`; a second run stops with "another
eval holds the eval user", `sweep` refuses to run without the lock, and the
integration tests take the same lock or skip.

*Setup builds.* The setup commands and the oracle run as `ppeval`
and, for the eval user, behind the same profile as the agent
(`realrepo.run_setup(..., confine=True)`): writes only in the task tree
and the task's private home and temp directory, no clipboard, Apple
Events or keychain, and the workload files readable. A repository's
build script can therefore neither reach the owner's account nor
rewrite `/Users/ppeval/eval-work/bundle`. Test: a setup script that
touches a file in a "bundle" directory and asks Finder for the owner's
home items fails at both (`touch` exit 1, Finder call fails), and the
same script run unconfined does write the bundle
(`test_a_hostile_setup_cannot_write_outside_its_tree_or_reach_the_gui`).
With `--no-eval-user` setup is as it was: unconfined, as the owner. Not
exercised until T3.8: a real task's whole setup under the profile. It
ran one task of each of django, arrow, Go, native, Node and Java then
(see "Real-repository setup" below); the other tasks' setups, and the
Rust and rich/xarray ones beyond what the unit tests cover, have not.

*The harness that scans is tamper-evident.* At staging the host hashes
the bundle: every entry's kind (file, directory, link), permission bits
and length-prefixed name and payload (bytes, or a link's target). Before each guest and after
it, the host runs a short script, passed on its own command line, as
`ppeval` that hashes the bundle again; a difference, an extra file, a
link or bytecode stops the run (`BundleTamperedError`) and nothing is
copied. The guest runs with `PYTHONDONTWRITEBYTECODE=1` so the bundle
stays byte-identical. Limits: the check runs outside the guest, so a
tampered guest cannot skip it, but it relies on `ppeval`'s Python
interpreter and the `uv tool` environment, which a confined process
cannot write either (home is write-denied) and which are not hashed
here; a change made and reverted between two checks is not seen.

*`auth-probe` exit status.* `skill_eval.py auth-probe --no-eval-user`
(the guest's form) exits 0 when the call is signed in and 2 when it is
not or timed out; the host's once-per-run probe relies on that.

*Docker.* `ppeval` has no Docker and the owner's Docker sockets are
closed to it. The Go, Ruby and C# tasks run natively (next
paragraph), so no task file names a launcher (`shim`) any more. A task
that did is recorded in the report as skipped, with the reason
"skipped: needs docker, not available to ppeval", without probing the
daemon (`realrepo.NEEDS_DOCKER`;
`test_docker_tasks_are_skipped_with_a_reason_for_the_eval_user`, on a
task with `shim` set by the test). It would still run with
`--no-eval-user`.

*Native Go, Ruby and .NET (T3.8, 2026-10-23).* `ppeval` has Go
1.25.14 in `~/.local/go`, CRuby 4.0.9 (with OpenSSL 3.5.9 and libyaml
0.2.5 built beside it; Bundler 4.0.20 ships with it) in `~/.local/ruby`
and the .NET SDK 10.0.402 (host 10.0.13) in `~/.local/dotnet`, each
checked against the checksum its publisher lists for Go, the .NET SDK,
CRuby and OpenSSL; libyaml's hash is the one in `rbenv/ruby-build`
(its release page lists none) and Bundler ships inside the Ruby
tarball (steps and hashes in
`.claude/local/eval-user-setup.md`, which is not committed). The
harness finds them from a task's `requires` (`realrepo.toolchain_dirs`)
and the profile reads them (`sandbox._HOME_READ`). Running each
manifest through `eval/mine/check.py` gave status `runs` for
`labstack-echo-pr3023`, `rails-rails-pr58964`, `pr59030`,
`pr58751-landed`, `xoofx-markdig-pr922`, `pr866` and
`serilog-serilog-pr2215-landed`, each with workload and oracle exit 0
on the task and the reference tree. The committed `check.py` runs
unconfined; the confinement came from a throwaway driver that wraps
each setup, oracle and workload command in `sandbox.wrap` (not in the
repository, like the saved compare records below). Three things
failed under the profile, found by running the commands under it
(the errors are observed; the causes marked as such are inferred):

- TLS certificate checks fail in Go and .NET (Go: `x509: OSStatus
  -26276`; NuGet: `bad certificate format`). Inferred, not shown: they
  go through the Security framework, whose Mach services the profile
  closes. Allowing
  `com.apple.trustd` alone did not help for Go (one probe). A Go
  task's modules must be downloaded outside the profile before its
  confined setup runs (`realrepo.prefill_go_modcache` does it, and a
  `go mod download` runs no code from the repository), and the runner
  calls it before the setup (`build_real_repo(before_setup=...)`, #87).
  Run as `ppeval` through that path, the setup of `real-echo-known-change`
  and of `real-rails-known-change` passed (one run each; the .NET tasks
  were not run this way). The `check.py` runs above did the download
  themselves. For .NET the packages of the three C# tasks
  (both trees of each) were restored once into
  `~/.local/dotnet/nuget-feed`, a read-only folder in NuGet's
  global-packages layout, which is also a valid local feed; the
  private home's `NuGet.Config` names it as the only source
  (`sandbox._nuget_feed`, written only when the folder exists, so
  `--no-eval-user` keeps NuGet's defaults). A fallback folder was not
  enough: markdig pr866 asks for `Microsoft.SourceLink.GitHub 8.0.*`,
  a floating version, which needs a source. Seeding ran the
  repositories' own MSBuild evaluation unconfined, once, as `ppeval`
  on the pinned public commits; a task whose dependencies are not in
  the feed fails at restore instead of fetching them.
- `dotnet build` under the profile hung for ten minutes and then
  failed with no error, until it was run in one process; the
  manifests pass `-m:1 -nodeReuse:false
  -p:UseSharedCompilation=false`. The same build takes about a second
  outside the profile. Which flag matters and why was not isolated
  (an `MSBuild.rsp` with the same options was not honoured). An agent
  that runs a bare `dotnet build` under the profile will hit the hang.
- Ruby needs a bundler path that is not in the Ruby prefix (read-only
  to the profile): the Rails manifests set bundler's global `path` to
  `$HOME/.bundle-gems` and its `without` list in the private home, so
  `bundle exec` finds the gems in every later command, perfproof's
  included (it passes `HOME`). The `db` gem group is left out too
  (`pg`, `mysql2` and `trilogy` need client libraries), and `sqlite3`
  builds against the SDK's sqlite.

Not covered: `ruby -C activesupport` replaces the Docker form's
`-w /src/activesupport`, so the Ruby workloads are adapted, not the
same commands. A real `perfproof compare` per language ran as
`ppeval` on one task tree (task commit as `main`, reference tree as the
candidate; echo, markdig pr922 and rails pr58964; 20 rounds, two looks,
each INCONCLUSIVE, which is the point of that run): the `runtime` gate
passed in all three and named the native `go` (go1.25.14), `dotnet`
(runtimes 10.0.13) and `ruby` (4.0.9) of `ppeval`'s home. Those
compares were not run inside the profile. The `coverage` gate was
`not_available` in all three (the specs declared no coverage command),
so no KEEP was possible there either.

*Real-repository setup under the profile (T3.8, found in skill round 8).*
Five defects stopped most real tasks before the agent ran; each is
reproduced and fixed, with a test that fails without the fix:

- The bundle lacked `eval/mine/draw.py` and `task.py`, which
  `realrepo.draw_state` imports for every development-candidate (the Go,
  native and Node tasks). They are in the bundle now; the draw check is
  unchanged (`test_every_task_file_loads_from_the_guests_bundle` loads
  every task file from an extracted bundle and requires the same answers
  as the checkout).
- Setup ran the launchers on `PATH` (`runs/<id>/bin/python`) without read
  access to them, so `uv` could not inspect `python` ("Operation not
  permitted", exit 126). Setup is given the same `PATH` directories to
  read as the agent (`test_a_confined_setup_can_run_the_launchers_on_its_path`).
- `~/.local/share/uv/python` in a task's private home was one link to the
  eval user's shared interpreters, read-only, so `uv venv --python 3.10`
  could not write its temporary file. The link is gone: `uv` finds the
  installed interpreters through the `~/.local/bin` links on `PATH` and
  downloads a missing one into the private home's own
  `~/.local/share/uv/python`, which the harness never touches (an
  interpreter that is managed but has no `~/.local/bin` link, such as a
  prerelease, is no longer found). `--python 3.14+gil` is a variant
  filter and never downloads, so 3.14 is installed for `ppeval`
  (`.claude/local/eval-user-setup.md`, untracked, as are `cmake` and
  `ninja` for arrow, installed with `uv tool`;
  `test_the_eval_user_has_the_build_tools_the_tasks_ask_for`).
- A JVM takes `user.home` from the account database, not `$HOME`, so Maven
  wanted `/Users/ppeval/.m2`. The private home has a `.mavenrc` that sets
  `-Duser.home=$HOME` for `mvn` (also with `--no-eval-user`, where Java
  setup now uses the task's private `~/.m2`, not the owner's); `perfproof` passes `HOME` to what it
  runs, so benchmarks get it too, and the wrapper's script already uses
  `$HOME` (`test_a_jvm_started_by_maven_has_the_private_home_as_user_home`;
  the file is opened with `O_NOFOLLOW | O_EXCL`, and anything unexpected
  is recorded in `TAMPERED` and skipped).
- The harness manages `home/.rustup` and `home/.mavenrc` unconfined, in a
  directory the agent can write, so a process of the agent that outlived
  its group could swap one of them (or `home`, or `private`) for a link
  between a check and the action. The profile denies the agent every
  write to those four paths (`sandbox.guarded`); the rest of the private
  home stays writable. `O_NOFOLLOW` covers only the last component; the
  profile covers the rest
  (`test_the_agent_cannot_swap_what_the_harness_manages`,
  `test_the_maven_file_is_not_written_through_a_link_planted_mid_write`).
  The Go prefill (unconfined) uses `GOPROXY=https://proxy.golang.org`
  with no `direct` and `GOFLAGS=-modcacherw -mod=readonly`.
- Go on macOS verifies certificates through `trustd`, a Mach service the
  profile closes, so a confined `go test` could not download a module
  (`x509: OSStatus -26276`). The module cache is now filled, outside the
  profile, before the confined setup instead of after it
  (`test_go_modules_are_fetched_after_the_seed_and_before_the_confined_setup`).
  The agent's own `go get` of a new module still fails behind the profile.

Measured after the fixes (2026-10-23, claude 2.1.291, one task of each
fixed group, once each, as `ppeval`; $2.68 in all, plus a $0.57 django rerun after the review fixes; from the run
reports and transcripts in the author's gitignored
`scratch/runs/t38-*`, not archived in the repository): the setup of all six
ran under the profile and the agent started. Passed: arrow
(`real-arrow-known-change`), native (`native-lz4-known-change`), node
(`real-fastify-known-change`). Not passed, for task reasons and not
harness ones: django (`real-django-hidden-fix`, the agent's profile did
not rank the target function in the first five), Java
(`java-jackson-core-hidden-fix`, same check), Go (`go-gin-known-change`,
the agent's own test wrote `/tmp/out_main.txt`, which the profile closes,
so the correctness gate failed and the verdict was REJECT). One run per
task: these say the harness reaches the agent, not how well the skill
does. Docker tasks (echo, rails, ruby) and dotnet are still skipped.

*Sudo scope.* `/etc/sudoers.d/ppeval` is `root:wheel` mode 0640 and not
readable by the owner, so I did not read it. `sudo -n -l` as the owner
lists `(ALL) ALL` (with a password; the owner's existing admin rule) and
`(ppeval) NOPASSWD: ALL`: passwordless sudo reaches the `ppeval` user
only, not root. A command run as root without a password is refused
(`sudo -n` fails).

*Probes.* Each row ran as `ppeval`, once bare and once inside the
profile, on 2026-10-22 (macOS 24.6.0, arm64), from the throwaway driver
`scratch/t320/` (not committed). The control rows show the probe can
succeed. Real-account integration tests for the rows marked (T) are in
`eval/skill/test_eval_user.py` (`pytest -m integration`; they skip where
`ppeval` does not exist).

| # | Probe (as `ppeval`) | Bare | Inside the profile |
|---|---|---|---|
| 1a | `ls /Users/faras` (T) | Permission denied | Permission denied |
| 1b | `cat /Users/faras/.ssh/id_ed25519.pub` (T) | Permission denied | Permission denied |
| 1c | `ls /Users/faras/.claude` | Permission denied | Permission denied |
| 1d | `ls` the checkout, and `head` a file in it (T) | Permission denied | Permission denied |
| 2a | `security find-generic-password -s "Claude Code-credentials"` | rc 44, item not found (inference: it searched `ppeval`'s own keychain, not the owner's) | rc 71, `execvp() of 'security' failed: Operation not permitted` |
| 2b | Security framework query (`keychain_probe.py`) | -25300 (reached a keychain, found nothing) | -50 (service closed) |
| 3 | `sysctl` KERN_PROCARGS2 on a faras process | rc -1, errno 22, 0 bytes | rc -1, errno 1, 0 bytes |
| 4 | `pbpaste` with a 21-byte marker on the owner's clipboard (restored to empty afterwards; byte count only, contents never printed) | **21 bytes: succeeds** | 0 bytes |
| 5a | AppleScript `tell application "Finder" to get name` | "Finder" | "Finder" with `Connection Invalid` on stderr (the name comes from the app bundle, no event is sent) |
| 5b | AppleScript `tell application "Finder" to count items of home` (T) | 12 (the owner's home): **succeeds** | rc 1, `Connection invalid` |
| 5c | AppleScript `tell application "System Events" to get name of current user` | the owner's user name: **succeeds** | rc 1, error -10827 |
| 6 | `open -g -a Finder` (T) | rc 0: **succeeds** | rc 1, `Unable to find application named 'Finder'` |
| 7a | connect to the owner's ssh-agent socket | PermissionError 13 | PermissionError 13 |
| 7b | connect to `/private/tmp/cc-socks/<pid>.sock` | PermissionError 13 | PermissionError 13 |
| 7c | connect to `/Users/faras/.docker/run/docker.sock` | PermissionError 13 | PermissionError 13 |
| 7d | connect to `/var/run/docker.sock` | PermissionError 13 | PermissionError 13 |
| 8a | `touch /Users/faras/.t320-probe` (T) | Permission denied | Permission denied |
| 8b | `touch /private/tmp/claude-501/.t320-probe` | Permission denied | Permission denied |
| 8c | `touch` in the checkout | Permission denied | Permission denied |
| ctl | write in the task directory; run the system Python | works | works |
| 9 | `claude -p "reply with the single word ok"` (`claude-sonnet-5-5`, budget 0.30 USD), token read from the file by the process that started it | n/a | rc 0, `ok`; the token was not in the argv (checked in the same process); observed in this session, no record archived |

Interrupting the host (SIGINT) sends `sudo` a SIGTERM, which it relays;
a `sleep` started through `run_guest` as `ppeval` was gone one second
later (`test_interrupting_the_host_stops_the_guest_process`). The guest
`skill_eval.py` turns SIGTERM into the unwinding that kills a running
agent's process group (the handler is installed when it runs as a
script). Observed once, not a test (2026-10-22): a guest started as
`ppeval` with an agent running was sent SIGTERM through `sudo` with no
host sweep afterwards; `sudo` exited by SIGINT (the handler's
`KeyboardInterrupt`), and 3 seconds later `pgrep -u 502` found no
`claude` or `skill_eval` process.

*One end-to-end run as the eval user (2026-10-22, claude 2.1.291).*
`skill_eval.py run --only find-rolling-light --label t320-e2e`, host
wheel `perfproof-0.1.0-py3-none-any.whl` (sha256 in the run's
`eval-user.json`), on a machine under ordinary load (load average 4.1
at the task's start). The host built, staged and installed the wheel as
`ppeval`, probed the sign-in once, ran the one task as `ppeval` behind
the profile, and copied the scanned archive back (6 files); the host's
re-score agrees with the guest's. Timings: 2 min 31 s of host wall time
in all (54 s of the host's CPU, mostly `uv build`); the task 121 s (the
guest's setup 0.3 s, then two `claude` calls of 14.9 s and 103.7 s);
cost $0.28, 11 turns. Score: 1 of 1 passed; verdict INCONCLUSIVE
(insufficient evidence), `Result:` line correct, hotspot found at
rank 1, tree clean, no wording violations, no wrong KEEP. Sandbox
denials in the recorded output: one, the agent writing `/tmp/cmp.json`
(`Operation not permitted`; shared temp is closed on purpose, the agent
carried on). `find-rolling-light` is an example task, so it has no setup
commands: the confined setup path of a real repository was not exercised
by this run (only by the unit tests above).

Rows 1, 3, 7 and 8 are closed by the user boundary alone (the profile
adds nothing there, the bare column already fails; on 7 the parent
directory is 700 and owned by the owner, so the profile's socket rule
was not exercised). Rows 4, 5b, 5c and 6 are the reason both layers
exist: `sudo -u` keeps the process in the owner's GUI session, so a bare
`ppeval` process reads the owner's clipboard, drives the owner's Finder
(which runs as the owner and reads the owner's home) and System Events,
and opens things in the owner's session. Only `(deny mach-lookup)` closes
them. The "bare" column describes what the guest harness itself (not
confined) could do; setup builds and the oracle run in the "profile"
column. Row 3's errno differs (22, 1) but both fail.

Not covered by a probe: Apple Events to apps other than Finder and
System Events; `pbcopy` (write to the owner's clipboard); LaunchServices
beyond `open -g -a`; whether another route into the GUI session exists.

**What is still open after this.**
- Process introspection beyond the three rules, and any reader in the
  harness's own session (see above): untested routes such as `lsof`,
  `fs_usage`, `dtrace` and `kdebug` are not blocked by name.
- `launchctl print gui/<uid>` still works under the profile (it reads the
  user's launchd domain: the loaded services and their paths; `launchctl
  list` is refused). `ioreg -l` works too (hardware identifiers). I left
  both open: denying `iokit-open` would break more than it protects.
- SysV IPC (`ipcs`, `shmget`, `msgget`) and POSIX shared memory and
  semaphores are not restricted: I created a POSIX shared-memory segment
  and a semaphore under the profile and did not test reading someone
  else's.
- The Mach allowlist is only as complete as the commands I ran. A tool
  that needs another service fails silently in the sandbox (the kernel
  log did not show these denials here), so a task that depends on one
  would fail in a way that looks like the tool's. A script that uses no
  Mach service at all (`osascript -e 'return 1'`) still runs.
- Anything the agent can do without a file, a socket or a Mach service
  is open: for example it can still read its own environment, which
  holds the access token, and use CPU, memory and the network.
- Unix sockets, Mach services and files are each a deny-by-default list
  that I tested only where I could name the route; a route I did not
  think of stays open. With the eval user (above) the owner's files,
  keychain, process environments and sockets are closed by construction;
  what the enumerated profile still has to close alone is the
  GUI-session reach (clipboard, Apple Events, `open`), and anything
  `ppeval` itself owns (its token, and the earlier runs and task trees
  in its home, which the profile denies to the agent).
- The network is open: anything the agent can read or holds (the access
  token, whatever the task tree contains) can be sent anywhere, and
  encoded exfiltration beyond the redacted forms (split, encrypted,
  other encodings) is not caught in transcripts.
- The Docker daemon, for a task with a `shim`: it is unconfined and
  can bind-mount `/Users`. No task file names one now, and the eval
  user cannot reach the daemon.
- Build steps (setup commands) run unconfined by the profile with
  `--no-eval-user` (as the owner, clean environment); they execute the
  repository's own build scripts. With the eval user they run behind the
  profile (see above).
- A resumed run restages the bundle, and `eval-user.json` keeps every
  staging's hashes (`stagings`) because tasks finished earlier ran with
  the earlier wheel and skill.
- Whatever the agent writes into its private directory is read by the
  harness afterwards. Links in it are not followed, and anything it
  replaces in the private home (the toolchain links, its temp
  directory) is removed and recorded in `tampered.json`, but a file
  the harness opens by name there is still agent-controlled.
- The cross-process archive scan cannot know a token that has rotated
  since the run.
- `sandbox-exec` is deprecated by Apple; the profile language is
  undocumented and may change.
- The allowlist is only as tight as its entries: `~/.local/bin`,
  `~/.local/share/{uv,claude}`, `~/.local/jdk`, `~/.local/go`,
  `~/.local/dotnet`, `~/.local/ruby`, `~/.cargo/bin`, `~/.rustup` and
  `~/.nvm` are readable in full.
- Results from before this confinement (`runs/real-3`) came from a
  harness where the agent could read most of the home directory; they
  say nothing about the allowlisted profile.

Until perfproof is calibrated, INCONCLUSIVE is the only accepted
verdict of a prove task, so any KEEP counts as a wrong KEEP. That
changes when calibration lands, not before. A known-change task would
accept REJECT only if its manifest says the change breaks correctness;
none in the first batch does. Every task has a per-call time limit and
a cap on total spend, recorded in the task file, and a call that runs
past the limit is killed with its process group and any container
the launchers started.

These are pass/fail checks on the skill's behaviour, not performance
labels: the labels come from the independent procedure above.

## Limits

- A task set is a sample of real performance work, not all of it.
- Hidden-fix tasks reward finding one known answer; a different real
  win is scored by the independent measurement, not missed.
- One case study per class shows the cycle works on that repository. It
  is evidence, not proof that the class is supported everywhere.
