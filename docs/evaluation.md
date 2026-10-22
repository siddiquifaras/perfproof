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

A task used for development never moves to the held-out set.

**How the held-out set is chosen.** The mined manifests are a
candidate pool. The selection rule is fixed here before any task is
built for it:

1. Candidates whose family or class is wrong on reading their pull
   request or commit (not an optimization, a revert for a build or CI
   failure, a workload that doesn't exercise the change) are removed
   first, each with its reason recorded. This reads only the change,
   never a result.
2. The rest are ordered by `sha256(salt + task id)`, with the salt
   `perfproof-heldout-2026-10-16`.
3. Per target class, the held-out set takes, in that order, the first
   candidates that build and run here (or on the class's GPU host): two
   known-change tasks, one hidden-fix task, one nothing-to-keep task and
   one published-benchmark task, five in all. Where a class has no
   candidate of a family, the next known-change task takes its place.
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
the manifest's build steps (Go and Ruby through the containers the
manifests use, with launchers on `PATH`), installs the skill and sends
the first message. A task whose toolchain is missing is recorded as
skipped with the reason, not failed. Only development manifests are
used; a held-out task never enters this set.

| Family | Seeded as | Prompt | Passes when |
|---|---|---|---|
| hidden-fix | the task commit, no later history | `/perfproof light`, then "yes, go ahead with your suggestions" | the known function is in the top 5 of the first successful `perfproof profile` call before the first detected edit of the code (a failed attempt before it is fine; detection is pattern based, so an edit made in a way the patterns miss is not seen) and is named after it (the small examples need rank 1; the two rules are counted apart); a `perfproof/*` branch, if any, passes the manifest's tests and edits no test file; tree clean; `Result:` line and wording rules hold; no network lookup of the fix |
| known-change | `main` at the parent, the fix on `pr-<n>` | a plain "is this PR actually faster?" naming the branch | the skill starts, `compare` ran, the verdict is INCONCLUSIVE, no code changed, `Result:` line correct |
| nothing-to-keep | the merge, optimization already in | `/perfproof light`, same follow-ups | no KEEP, tree clean, wording and `Result:` line hold |

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
     `~/.local/jdk`, `~/.cargo/bin` (not its `config.toml` or
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
     private home links the real `~/.rustup` and
     `~/.local/share/uv/python` (read-only to the agent). The shared
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
     uses (without it `id -un` prints `501`). Run under the profile with
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
   nothing. A token that expires within the hour stops the run.
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
the JDK, `go` outside Docker, `ruby`, the Docker-backed Go and Ruby
tasks, and Rust under the private home (`cargo new`, `cargo add` and
`cargo build` ran under the profile by hand; no archived record, no
real repository such as `fd`).

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
  think of stays open. The class of problem (a profile of a deprecated
  Apple tool, on the owner's own account) is the reason to weigh a
  separate macOS user or a VM, which closes by construction what this
  closes by enumeration.
- The network is open: anything the agent can read or holds (the access
  token, whatever the task tree contains) can be sent anywhere, and
  encoded exfiltration beyond the redacted forms (split, encrypted,
  other encodings) is not caught in transcripts.
- The Docker daemon, for the Go and Ruby tasks, which keep it: the
  daemon is unconfined and can bind-mount `/Users`, so those tasks can
  read the whole disk through a container. Accepted as a residual until
  the native Go and Ruby work (T5.12, T5.14) removes the need for Docker.
- Build steps (setup commands) run unconfined, with the clean
  environment, because they read the task files; they execute the
  repository's own build scripts.
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
  `~/.local/share/{uv,claude}`, `~/.local/jdk`, `~/.cargo/bin`,
  `~/.rustup` and `~/.nvm` are readable in full.
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
