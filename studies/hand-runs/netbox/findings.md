# Findings: NetBox hand-runs

Each item names the CLI command or skill decision it affects, a
severity, and where a transcript shows it (`e1 §7` is entry 1, section
7). Basis is marked: **measured** (a command in a transcript produced
it), **code** (read in the source, with `path:line`), or **inference**.

Severity: **high** blocks a correct verdict or makes the skill give a
wrong one on this class of project; **medium** costs the user a lot of
time or forces a hack the skill contract must define; **low** is
friction.

Code citations name the code that ran: `pp-profile/…` is PR #23 at
`055eca4`, `pp-compare/…` is PR #24 at `463161b`. Both PRs merged into
`main` during this study. Rechecked on `main` at `4c7561b`: the cited
behaviour is unchanged, at `src/perfproof/bench/compare.py:599-602`
(outputs from one execution per tree), `src/perfproof/schema.py:1301`
and `:1356` (`regression` documented and enforced as rule 3 with an
interval below 1), `src/perfproof/profilers/run.py:7-9` (CPU summed
over descendants) and `src/perfproof/bench/compare.py:471` (reset
refused).

None of the verdicts in these transcripts is calibrated (`compare` is
the PR #24 thin path). The findings are about procedure, not about
what the measured intervals mean for NetBox.

## Summary

| ID | Affects | Severity | One line |
|---|---|---|---|
| F-01 | `profile` | high | With pyinstrument (macOS default) it profiles the load client, not the server it starts: 96.8% `socket.recv_into`, no warning. Linux py-spy not run |
| F-02 | `compare` correctness gate (outputs) | high | Declared outputs are never compared baseline-against-baseline; a port in the output gave a false correctness REJECT |
| F-03 | `bench define` oracle lock; skill decision | high | Projects that pin query counts fail their own tests when a change removes queries; locking the pin file makes such a KEEP impossible, not locking it lets a candidate loosen pins |
| F-04 | `compare`; skill decision | high | The harness has to be committed into the user's repo, and a candidate branch made without it can't be compared |
| F-05 | `compare` checkouts | medium | Gitignored local config (`configuration.py`) is absent from every checked-out tree |
| F-06 | `load` (missing) | high | The skill hand-writes server start, health check, port, Host header, warmup and the request loop |
| F-07 | `compare` reset | medium | A spec with a reset is refused, so any write workload can't be compared |
| F-08 | `profile`/`measure` (missing angle) | high | No query-count angle; the decisive evidence for every hypothesis here was gathered by hand |
| F-09 | `init`, `project` (missing) | medium | No workload in the repo; the mix is a proposal the user confirms, and nothing records it |
| F-10 | `bench define` guardrails | low | A web service must declare a GPU-memory guardrail as not applicable: the spec has no target class |
| F-11 | `bench define` objective | low | No named objective for a latency sum or p50; the skill invents one |
| F-12 | `bench define` correctness env | medium | The correctness command can't have its own environment; the test config rides in the benchmark's env |
| F-13 | `bench define` spec_conflict | low | Refusal after a harness fix doesn't say which field to change |
| F-14 | `compare` duration and visibility | high | 25 minutes in the correctness gate, no round run, no progress file, no detach |
| F-15 | skill decision: cheap tests | medium | In-process timings taken minutes apart moved 2.3x with load and the skill's own test run on the same database; only counts are safe outside `compare` |
| F-16 | `doctor` | low | Checks load, not the services or tools a web workload needs |
| F-17 | skill decision: test selection | low | Guessed test labels were wrong; look them up |
| F-18 | `side_effects` gate | medium | "Local" isn't "ours": NetBox's stock test config points at a port where a sibling project's Postgres listens |
| F-19 | `compare` correctness gate (test granularity) | medium | `manage.py test` writes no JUnit XML, so 46 tests count as one; flaky-test logic works on the whole suite's exit status |
| F-20 | `compare` guardrails | low | `max_rss_bytes` is recorded per sample, yet `peak_memory` is reported "unmeasurable" |
| F-21 | `bench list` | low | Needs a name: no way to ask which benchmarks exist |
| F-22 | skill decision: caps | medium | A cap near the bar isn't worth proving; an estimate above the cap is noise or a cap from a different workload |
| F-23 | `compare` INCONCLUSIVE | medium | Doesn't say what would settle it (rounds, time), as docs/cli.md promises |
| F-24 | skill decision: report | low | The skill guessed at the user's workload in its report; it must ask instead |
| F-25 | discovery: function to workload | medium | No command maps a named function to the workloads that run it |
| F-26 | skill decision: named target | medium | The named function was not where the cost was; the contract must say whether the skill may move off it |
| F-27 | skill decision: reading INCONCLUSIVE | high | A 4.7x-to-5.4x interval reads as INCONCLUSIVE; the skill must report the interval with the reason |
| F-28 | skill decision: ranking candidates | medium | Two equivalent fixes gave non-overlapping intervals in separate runs; intervals from different runs can't rank candidates |
| F-29 | `compare` candidate not on the baseline | high | A user's branch without the harness gets a recorded `side_effects` REJECT; the record names neither the user's commit nor the derived one |
| F-30 | `compare` record: regression flag | high | A candidate whose interval is entirely below 1 (0.65x to 0.92x, uncalibrated, cv 0.46) is reported "Regression: no" |

## Details

### F-01 `profile` (pyinstrument, the macOS default) sees the client, not the server — high

**Measured** (e1 §5): `perfproof profile api-mix` ran the harness
under pyinstrument. 96.8% of self time was `socket.recv_into`, the
next 2.3% `sleep`; no NetBox frame appeared. The work happens in the
gunicorn worker, a child process. The output has no warning that the
profiled process spent its time waiting on a child.

**Code**: `unprofiled.cpu_seconds` sums CPU over every descendant the
process waited for (`pp-profile/src/perfproof/profilers/run.py:7-9`), so
its 0.418 CPU/wall ratio mixes client and server.

**Not run**: the Linux default. There, `profile` runs py-spy with
`--subprocesses` (`src/perfproof/profilers/run.py:32-40` on main),
which follows child processes and may well see the gunicorn worker.
So this finding is established for pyinstrument on macOS only.

For the skill: on a web service, "profile the benchmark" must mean
profile the server. Options the contract has to pick from: py-spy's
`--subprocesses` on Linux (to be checked against a real server), on
macOS pyinstrument inside the worker, or profile request handling
in-process with the framework's test client, as done by hand here.
`profile` should at least warn when the profiled process's own
samples are dominated by a blocking read and a child used the CPU.

### F-02 No baseline-against-baseline output check — high

**Measured** (e1 §7–8): the first `compare` REJECTed H1 with 1,982
output differences. All were the port in absolute URLs, which the
harness picks fresh per run. Running the harness twice on the same
baseline tree gave the same 1,982-line difference; after replacing
`http://127.0.0.1:<port>`, both the baseline-vs-baseline and the
baseline-vs-candidate files were identical.

**Code**: each tree's declared outputs come from one execution
(`pp-compare/src/perfproof/bench/compare.py:549-552`); nothing runs
the baseline twice and compares its outputs, as the gate does for
flaky tests.

Consequence: any nondeterministic output (ports, timestamps, UUIDs,
dict or set order, pagination over unordered querysets) makes every
candidate fail `correctness`, and the record blames the candidate.
For the skill: before `bench define`, run the harness twice on the
baseline and diff the outputs; refuse or normalise anything that
differs. For the CLI: produce the baseline's outputs twice and treat a
self-difference as a broken oracle, not a candidate failure.

### F-03 Pinned query counts — high

**Measured** (e1 §7, §9): NetBox pins per-test query counts in
`netbox/<app>/tests/query_counts.json`. H1 removed 3 queries from the
cable list test, so `CableTestCase.test_list_objects` failed on the
candidate in all 6 runs (`expected 23, got 20`). NetBox's own workflow
is to rerun with `UPDATE_QUERY_COUNTS=1` and commit the new pin with
the change.

The spec can't express that. If `query_counts.json` is in `oracle`,
the lock gate refuses any candidate that updates it, so no query-count
improvement can ever be kept. If it isn't, a candidate can loosen any
pin, including one it made worse. The skill needs a rule: allow
changes to the pin file only in the direction of fewer queries, and
show the diff of the pin file in the report. That check is
deterministic, so it belongs in the CLI.

### F-04 The harness must live in the user's repository — high

**Code and measured** (e1 §3): `compare` checks the baseline out of
git (`pp-compare/src/perfproof/commands/compare.py`, module docstring)
and checks the harness files' hashes in both trees. A harness the
skill writes must therefore be committed, on a branch of the user's
repo, before anything can be compared. That's a commit the user didn't
ask for, on a project whose `AGENTS.md` has strict branch and PR
rules.

### F-05 Gitignored configuration is absent from checked-out trees — medium

**Code** (e1 §3): NetBox's `netbox/netbox/configuration.py` is
gitignored (`.gitignore:24`) and holds the database, Redis and secret
key. A tree checked out from git doesn't have it, so NetBox won't
start there. The harness carried its own config module and selected it
with `NETBOX_CONFIGURATION`. Every Django, Rails or Node service with a
local `.env` or settings file will hit this. The skill contract has to
say where such config comes from in each tree (declared, outside the
tree, read-only), and the side-effect guard has to read it there.

### F-06 No load generator — high

**Measured** (e1 §3): there is no `perfproof load` (T5.7). The skill wrote ~100 lines:
gunicorn start in the tree under test, a free port, a health check,
warmup, a fixed request loop at concurrency 1, response capture,
the `PERFPROOF` line, and process-group shutdown. It got one thing
wrong (the port in the output, F-02). Each of those is deterministic
and belongs in the CLI.

### F-07 Reset refused — medium

**Code**, not run (e1 §2 for why a reset wasn't needed): the thin path refuses any spec with `reset`
(`pp-compare/src/perfproof/bench/compare.py`, "What the thin path does
not do yet"). The mixes here were GET-only, so no reset was needed,
but the docs/targets.md web contract requires one and a write mix
(create a device, change a prefix) can't be compared at all. A
template database (`CREATE DATABASE … TEMPLATE netbox_pristine`) was
prepared for it.

### F-08 No query-count angle — high

**Measured** (e1 §5, e2 §2, e3 §3, e4 §4): the deciding evidence in every entry was a query count per request,
gathered by hand with Django's `CaptureQueriesContext`. docs/discovery.md
lists "Database patterns: queries counted per request" as an angle,
but no command implements it. For Django it is a small in-process
hook; for other stacks it is `pg_stat_statements` deltas.

### F-09 Understanding by hand — medium

**Measured** (e1 §2): `init` is not built. NetBox has no benchmark; the workload was the
skill's proposal (list endpoints at the default page size), confirmed
by a simulated user. Nothing recorded that confirmation, so a later
session couldn't know the mix was the user's.

### F-10 GPU guardrail on a web service — low

**Measured** (e1 §5): `bench define` refused the spec until it named
an objective. **Code**, not run: `compare` refuses a spec that omits a
default guardrail (`pp-compare/src/perfproof/bench/compare.py:480-492`),
so the skill declared all three up front. With no target class in the spec, the defaults are the
unknown class's (`peak_memory`, `p99_latency`, `peak_gpu_memory`).

### F-11 No objective for a latency sum — low

**Measured** (e1 §5): the harness reports the sum of 24 request latencies. The objective
had to be invented (`request_latency_sum`). p50 isn't a named
objective either, though docs/targets.md names p50 for the web class.

### F-12 One environment for benchmark and tests — medium

**Measured** (e1 §5, spec in harness.md): the tests need `NETBOX_CONFIGURATION=nbtestconfig` and a `PYTHONPATH`
to find it; the server needs `nbconfig`. `CorrectnessSpec` has no
`env`, so both went into the spec's `env` and the harness overrides
them for the server.

### F-13 `spec_conflict` without guidance — low

**Measured** (e1 §9): after fixing the harness, `bench define
api-mix` with the same JSON was refused ("Define a new spec
instead"). Right, but it doesn't say how; renaming to `api-mix-2` is
what the skill did.

### F-14 Twenty-five silent minutes — high

**Measured** (e1 §7): 1,505.9 s, no round run. The correctness gate
ran the 46 cable tests 3 times on the baseline, then, after the
candidate failed one, 5 more times on each tree: 14 runs of a Django
suite at load up to 48. The thin path writes no `progress.json`, and
`--detach` isn't built, so the skill could say nothing for 25 minutes,
and a caller with a command timeout would have killed it.

### F-15 Cheap-test timings drift with load — medium

**Measured** (e1 §6): the same in-process measurement of the cable
list gave 577.8 ms at load 12.9 and 1,324.1 ms five minutes later at
load 32.6, the second on a candidate with 99 fewer queries. Both ran
while the hand-runner's own oracle run (e1 §4: test-database build
plus 146 tests) was using the same Postgres container, at different
stages of it, so the drift is the machine's load and the hand-runner's
own database work together; they can't be separated after the fact.
The same contention sits under e1's in-process profile and caps.
Query counts were stable. A skill must not run anything that touches
the benchmark's services while it measures, including its own test
runs. The skill must never present an in-process timing
as evidence of speed; only `compare` interleaves.

### F-16 `doctor` doesn't check what a web workload needs — low

**Measured** (e1 §1): it reports load, cores, power, git and perf. Not
Docker, the database and cache ports, gunicorn, or a profiler.

### F-17 Test labels — low

**Measured** (e1 §4): `dcim.tests.test_api.CableTest` doesn't exist;
the class is `CableTestCase`. Look test IDs up; never guess them.

### F-18 Local isn't ours — medium

**Measured** (e1 §2): NetBox's stock `configuration_testing.py` uses
`localhost` and the default port 5432. On this Mac, `127.0.0.1:5432`
is `prstudy-pg`, the T3.11 session's Postgres (`docker ps`). The
stock test command would have created and dropped `test_netbox` in
another project's database; the side-effect guard would pass it as
local. The skill should start its own services on ports it chose and
check what is listening before it points a benchmark at a local port.

### F-19 Test granularity for Django — medium

**Measured** (e1 §7, e1 §10, e4 §5): the correctness detail reads
"1 tests judged" for a command that ran 46 tests. Django's test runner
writes no JUnit XML, so perfproof sees one result: the exit status.
One flaky test anywhere in the suite would quarantine the whole oracle
or blame the candidate for all of it, and the record can't name the
failing test (only the stderr tail does). The skill could add
`unittest-xml-reporting` or run the tests through pytest-django where
the project allows, but the project said "no new dependencies";
docs/method.md's `PERFPROOF_JUNIT_XML` path doesn't help a runner that
can't write XML.

### F-20 Memory is recorded but not used — low

**Measured** (every `samples.npz`): `max_rss_bytes` is there for each
sample (e.g. 286 vs 288 MiB median in e1 §10). The `peak_memory`
guardrail is still "unmeasurable" (T2.15). Whether `max_rss` covers
the gunicorn grandchild isn't established here; the values (~280 MiB)
suggest it does. **Inference.**

### F-21 `bench list` needs a name — low

**Measured** (e2 §1): `perfproof bench list --repo netbox` fails with
"the following arguments are required: name". The skill's first move
on a returning project is "what benchmarks exist"; it read
`.perfproof/benchmarks/` by hand.

### F-22 Caps: near the bar, and from the wrong workload — medium

**Measured**: H3's cap on the device list was 1.035x against a 1.03x
bar (e2 §2); the compare's uncalibrated estimate was 1.067x (interval 0.93x to 1.22x), above the cap (e2 §5).
H1+H2's cap was 4.27x on the cable list from an in-process profile;
the compare's uncalibrated estimate was 5.07x (interval 4.73x to 5.44x, load 8.0; e3 §5). Whether the profile or the
noisy run is at fault was not tested (**inference** either way). docs/discovery.md ranks by
cap, but a cap only bounds the workload it was measured on (here an
in-process profile, not HTTP through gunicorn). The skill contract
needs: don't prove a hypothesis whose cap is within noise of the bar
without saying so; compare every estimate with its cap; and record
which workload a cap came from.

### F-23 INCONCLUSIVE without what would settle it — medium

**Measured** (e1 §10, e2 §5): the thin path's INCONCLUSIVE carries
the interval and diagnostics, but no estimate of rounds or time to a
decision. docs/cli.md promises one ("At this noise a decision needs
about 410 rounds (~25 min)"). The skill computed it by hand in e2 §5
as an **inference**.

### F-24 Don't guess the user's workload in a report — low

e2 §6: the skill's report said the user's sync was "more likely the
number of pages than the time per page", with no evidence. Kept in the
transcript as an example of what the contract must forbid: a claim
about the user's system that nothing measured.

### F-25 From a function to its workloads — medium

e3 §1: the skill found that only `CableSerializer` uses
`GenericObjectSerializer` with `grep`. That works for a direct use.
NetBox picks serializers dynamically (`get_serializer_for_model`), so
grep would miss callers in general. A coverage or call-count run of
each candidate workload ("which workloads execute this function, and
how often") would answer it from evidence; no command does.

### F-26 Moving off a named target — medium

e3 §2: the user named `get_object`; its 76.6% share was the queries of
the serializers it calls. The fix went into `CableViewSet`, a
different file. The skill said so before editing. The contract must
define whether a named target is a scope ("only change this") or a
pointer ("start here").

### F-27 Reading INCONCLUSIVE — high

**Measured** (e3 §5, e4 §5): intervals of 4.73x to 5.44x and 5.17x to
6.37x both read INCONCLUSIVE, because the thin path can't KEEP before
calibration. Until then, a skill that relays the verdict alone tells
the user the work "didn't work", whatever the interval. The contract needs one fixed
sentence for "uncalibrated: here is the interval", and a different one
for "the interval straddles the bar".

### F-28 Intervals from separate runs can't rank candidates — medium

**Measured** (e3 §5, e4 §5): two fixes with the same query count (10
per page) measured 4.73x to 5.44x (load ~8) and 5.17x to 6.37x (load
~12), ten minutes apart. The intervals don't overlap. The baseline's
own median moved from 2.74 s to 3.30 s between the runs. "Many
candidates, one claim" in docs/discovery.md already requires
`confirm`; this is the evidence that it is needed even on one host,
within minutes.

### F-29 A candidate that isn't built on the baseline — high

**Measured** (e4 §2): `compare --candidate agent/cable-prefetch`
recorded `REJECT integrity`, gate `side_effects`: "candidate tree:
config perfproof-bench/nbconfig.py is missing, so it cannot be
checked". The candidate wasn't judged; it wasn't comparable. That
state should be refused before a run record exists, with the advice
"the candidate lacks files the spec locks; is it based on <baseline>?".
After the skill grafted the commit, the record still says "Not
recorded: the repository and the candidate's commit", so the report
can't show that the commit proven (`e96ce6c56` + `66f7e11ad`) isn't the
one the user named (`e0f2da620`).

### F-30 "Regression: no" for an interval entirely below 1 — high

**Measured and code** (e4 §6): agent/cable-defer's interval was
0.645x to 0.924x, entirely below 1 (uncalibrated, cv 0.46 on both
arms, load 5.7), and the report printed
"Regression: no". The flag is set only on a performance REJECT
(`pp-compare/src/perfproof/schema.py:1300`), which rule 3 can't give
before calibration. Until then the flag reads as "no regression". The
report should not print the line at all when the rule that sets it
can't fire, or should say why.

## Skill decisions made by hand, for T3.12

Every decision below was the hand-runner's, with no CLI support. Each
needs one defined behaviour in `docs/skill.md`.

| Decision | Where | What was done |
|---|---|---|
| Which services, on which ports | e1 §2 | Own containers on ports nothing else used; checked `docker ps` (F-18) |
| The request mix | e1 §2 | Proposed GET list endpoints at the default page size; the user confirmed (F-09) |
| Read-only or write mix | e1 §2 | Read-only, so no reset needed (F-07) |
| Where the harness lives | e1 §3 | A branch of the user's repo (F-04) |
| Where local config comes from | e1 §3 | Carried by the harness, selected by env (F-05) |
| Is the output deterministic | e1 §8 | Not checked first; found after a false REJECT (F-02) |
| What to lock in the oracle | e1 §8, e4 §5 | Tests locked, query pins not (F-03) |
| Whether to touch a pin | e1 §9, e3 §3, e4 §5–6 | Lowered with the change, in its own commit, after asking; raised once for a measurement only, labelled |
| Is the user's premise true | e2 §2 | Measured the named endpoint next to the others first |
| Prove a near-bar cap or not | e2 §2 | Said the cap was barely above the bar; proved it because the user asked (F-22) |
| A named target that isn't the cause | e3 §2 | Said so, then changed another file (F-26) |
| A candidate not built on the baseline | e4 §3 | Cherry-picked it onto the harness, unchanged (F-29) |
| How to read INCONCLUSIVE | e1 §10, e3 §5 | Reported the interval and the uncalibrated reason together (F-27) |
| How to read an interval below 1 | e4 §6 | Read the interval, not the `regression` flag (F-30) |
| Ranking two candidates | e4 §5 | Didn't; separate runs can't rank (F-28) |
