# Entry 1: bare `/perfproof`

The user opens a session in their NetBox checkout (v4.7.3) and types
nothing else. The hand-runner plays the skill. Each step lists its
wall time and the 1-minute load average before and after (`load
a → b`). Command output is trimmed to what matters; `…` marks a cut.
Every command ran through `scratch/step.sh`; the full log is
`scratch/logs/e1.log` (not committed: it holds machine paths and is
long).

Clock: 2026-10-16, 19:27 to 20:01 and 22:12 to 22:21, local time.
The two hours between are a usage-limit pause; the load was 10–48
before it and 3–11 after (one spike to 28.84 during the second
compare), so nothing is compared across that gap.

```text
> /perfproof
```

## 1. Machine check

`perfproof doctor --json` — 1.5 s, load 25.40 → 25.40

```text
"load_average": [25.39, 23.90, 19.99], "logical_cores": 10, "cpu_model": "Apple M4",
"power_source": "ac", "tools": {"git": available, "perf": not available (darwin)}
"warnings": [{"code": "high_load",
  "message": "1-minute load average is 25.40 on 10 logical CPUs, above 0.5 per CPU; …",
  "advice": "Pause other work (builds, indexers, other benchmarks) before comparing, …"}]
```

Skill decision: the load comes from other agents, which the user
can't pause from here. The hand-runner looked once, outside the step
log, with `ps -Ao pcpu,comm -r | head -8` at about 19:27:

```text
309.3 …/rustc
100.2 …/com.apple.Virtualization.VirtualMachine
 84.6 …/rustc
 52.6 ../.venv/bin/python      (the hand-runner's own setup gunicorn, stopped right after)
 38.4 …/agent-a9ed10504c12cef5a/.venv/bin/python
 37.0 …/t3-11-pr-study-0f488d/…/frames2py-base/.venv/bin/python
```

So the skill should say so and go on
with work that isn't timing-sensitive. `doctor` doesn't check what a
web workload needs: Docker, the database and cache ports, gunicorn, or
a profiler (findings F-16).

## 2. Understand the project

`perfproof init --json` — 0.1 s, load 25.13

```text
{"command": "init", "ok": false,
 "error": {"code": "not_built", "message": "perfproof init is not built yet in perfproof 0.1.0."}}
```

Done by hand instead (0.1 s plus reading): the repository root has
`netbox/manage.py`, `requirements.txt` (Django 6.1.2, DRF 3.18.0,
gunicorn 26.2.0), `pyproject.toml` (`requires-python >=3.12`), its
own `AGENTS.md`, and `docs/best-practices/performance-handbook.md`,
which is advice for operators (workers, page size, `brief` mode), not
a workload. No benchmark, locustfile or load script anywhere. Tests
are Django `TestCase`, run by `manage.py test` with
`NETBOX_CONFIGURATION` set; NetBox's own test config points at
Postgres and Redis on `localhost` default ports.

```text
AGENT: I couldn't run `perfproof init` (not built), so I read the repo by hand.
Proposed, nothing confirmed:
  target   web service (Django 6.1 + DRF, gunicorn)   evidence: netbox/manage.py, requirements.txt, netbox/netbox/wsgi.py
  target   python                                      evidence: pyproject.toml requires-python >=3.12
  workload none found: no benchmarks, no locustfile
  workload candidate "api-mix": GET list endpoints (devices, interfaces, ip-addresses,
           prefixes, sites, cables, vlans) at limit=50 + one device detail, on the demo data
  oracle   `manage.py test` (Django TestCase), needs NETBOX_CONFIGURATION and a Postgres
           it can create test_netbox in
  services Postgres 15 + Redis 7 in local containers (127.0.0.1:55432 / :56379)
  goal     ?  unknown
Which requests represent real use? What matters: latency of one request, or throughput
under concurrency? Anything that must not change (API output, schema/migrations)?

USER (simulated): Our automation hits those list endpoints with the default page size.
Per-request latency at low concurrency is what hurts. Don't change API output, no
migrations, no new dependencies.
```

Skill decisions made here, none of which a CLI command supports yet:

- **Which services and where.** NetBox's stock test config would use
  `localhost:5432`. On this Mac that port is `prstudy-pg`, the T3.11
  session's Postgres (`docker ps`, run at 22:14, long after this step; that
  `docker ps` is not in the step log, and the skill did not check the port before choosing its own). Running the stock tests
  would have created and dropped `test_netbox` in another project's
  database, and the side-effect guard would pass it, because the host
  is local (findings F-18). The skill started its own containers on
  other ports.
- **The request mix** was the skill's proposal, confirmed by the
  (simulated) user. Nothing recorded it: there is no `project
  confirm` (findings F-09).
- **Read-only mix**: all GETs, so no database reset is needed between
  rounds. A write mix would need one, and `compare` refuses a spec
  with a reset (findings F-07).

## 3. Write the benchmark harness

There is no `perfproof load` (findings F-06). The skill wrote
`perfproof-bench/api_mix.py` ([harness.md](harness.md)): it starts
gunicorn (one worker, one thread) from the tree it sits in, waits for
`/api/status/`, runs the mix once as warmup and three times measured
at concurrency 1, writes the first measured pass's response bodies
for output comparison, and prints `PERFPROOF {"value": <sum of
request latencies in s>}`. It also carries its own NetBox config
module, because `configuration.py` is gitignored and won't exist in
the trees `compare` checks out (findings F-05).

Smoke run, by hand — 14.2 s, load 27.38 → 23.25

```text
    126.3 ms  /api/dcim/devices/?limit=50
    223.0 ms  /api/dcim/interfaces/?limit=50
     47.1 ms  /api/ipam/ip-addresses/?limit=50
     68.7 ms  /api/ipam/prefixes/?limit=50
     61.8 ms  /api/dcim/sites/?limit=50
    844.1 ms  /api/dcim/cables/?limit=50
     82.2 ms  /api/ipam/vlans/?limit=50
    110.8 ms  /api/dcim/devices/1/
PERFPROOF {"value": 5.14}
```

(Medians of 3 passes, load ~25.) The cable list stands out.

The harness was committed to a branch of the user's repository,
`perfproof/bench` (`ca51bb61e`), because `compare` checks the baseline
out of git: an untracked harness would not exist in the baseline tree
(findings F-04). That is a commit the user didn't ask for.

## 4. The oracle

Guessing test labels failed first — 6.7 s, load 18.68 → 18.06:

```text
AttributeError: module 'dcim.tests.test_api' has no attribute 'InterfaceTest'. Did you mean: 'Interface'?
…
FAILED (errors=3)
```

The classes are `CableTestCase`, `InterfaceTestCase`, `DeviceTestCase`
(found with `grep '^class ' dcim/tests/test_api.py`; findings F-17).
With the right names — 369.7 s, load 16.83 → 31.54 — the first run
also built `test_netbox` with every migration. **This run was started
in the background at 19:29:25 and finished at about 19:35:35, so it
overlapped most of §5 and all of §6** (the per-request query count at 19:29:00 finished before it started): the by-table query counts, the stack, both
`bench define` calls, `perfproof profile`, both in-process profiles
and the H1 cheap test. It built a database and ran 146 tests on
`nb-pg`, the same Postgres container (limited to 2 CPUs) that served
the demo database to those measurements. Their timings, the 28.5%
psycopg share and the caps derived from them carry that contention as
well as the machine's load (findings F-15). The query counts don't.

```text
Found 146 test(s).
Ran 146 tests in 151.467s
OK
Preserving test database for alias 'default'...
```

## 5. Discover

### `perfproof profile` (PR #23, `055eca4`)

`bench define` first. The first attempt failed — 0.8 s, load 10.49:

```text
"code": "invalid_spec", "message": "… guardrails need the primary objective named …"
```

The spec has no target class, so `compare` applies the guardrails of
an unknown class: `peak_memory`, `p99_latency` and `peak_gpu_memory`.
The skill had to declare a GPU guardrail as not applicable for a web
service, and invent an objective name, `request_latency_sum`, because
there is no named latency-sum or p50 objective (findings F-10, F-11).
The correctness command has no environment of its own, so the test
config (`NETBOX_CONFIGURATION=nbtestconfig`, `PYTHONPATH=../perfproof-bench`)
went into the spec's shared `env`, and the harness overrides it for
the server (findings F-12). Second attempt — 0.8 s, load 14.31:

```text
"ok": true, "action": "define", "name": "api-mix", "new": true,
"spec_hash": "febd8ae7…6c86", "lock": 6 files (2 oracle, 1 harness, 1 input, 2 config)
```

`perfproof profile api-mix --repo netbox --top 12 --json` — 40.1 s,
load 16.24 → 21.81

```text
"profiler": "pyinstrument", "tool_version": "5.1.3", "basis": "wall",
"unprofiled": {"wall_seconds": 19.83, "cpu_seconds": 8.30, "cpu_fraction": 0.418},
"side_effects": {"status": "passed", "detail": "every service address is local: 3 spec env
  and 7 other environment variables, the commands and 2 config files checked"},
"hotspots":
  96.8% self  socket.recv_into  <built-in>   (library)
   2.3% self  sleep             <built-in>   (library)
   0.5%       JSONEncoder.encode encoder.py:202 (library)
   …
```

The profile is of the load-driving client, which spends its time
waiting on the socket. Every request is handled in the gunicorn
worker, a child process pyinstrument doesn't follow. Nothing in the
output says so (findings F-01). The CPU figure includes that child
(`profilers/run.py` sums CPU over waited-for descendants), so the
0.418 CPU/wall ratio mixes client and server and can't be read as
"58% waiting".

### By hand: queries per request

No CLI command counts queries (findings F-08). In-process, with
Django's test client and `CaptureQueriesContext` — 9.3 s, load
17.34 → 15.75:

```text
200   14 queries    87.7 ms  /api/dcim/devices/?limit=50
200   32 queries   130.0 ms  /api/dcim/interfaces/?limit=50
200    7 queries    42.5 ms  /api/ipam/ip-addresses/?limit=50
200   10 queries    47.6 ms  /api/ipam/prefixes/?limit=50
200    9 queries    74.6 ms  /api/dcim/sites/?limit=50
200  236 queries   385.1 ms  /api/dcim/cables/?limit=50
      repeated x101: SELECT "dcim_cable"."id", "dcim_cable"."created", …
200   10 queries    37.4 ms  /api/ipam/vlans/?limit=50
200   12 queries   245.9 ms  /api/dcim/devices/1/
```

Where the repeated cable query comes from (stack of the 5th one) —
5.5 s, load 15.95 → 15.47:

```text
  /netbox/netbox/api/viewsets/mixins.py:414 list
  /netbox/extras/api/customfields.py:171 to_representation
  /netbox/netbox/api/serializers/generic.py:41 to_representation
  /netbox/netbox/api/serializers/generic.py:50 get_object
matching queries: 102
```

By table, 5 repetitions in-process — 8.3 s, load 13.55 → 12.93:

```text
status 200; per request: median 577.8 ms wall, 236 queries, 308.1 ms in execute
    102 x dcim_cable                         125.3 ms
     74 x dcim_device                        109.9 ms
     26 x circuits_circuit                    30.8 ms
     26 x circuits_provider                   27.4 ms
```

### By hand: where the server's time goes

pyinstrument in-process over 3 passes of the mix (the first attempt
crashed on a bug in the hand-runner's script) — 45.4 s, load 32.11 →
42.33:

```text
profiled 20.31s, 3 passes x 8 requests
project functions, inclusive:
   97.1%  dispatch  netbox/api/viewsets/__init__.py:229
   88.8%  list  netbox/api/viewsets/mixins.py:402
   40.6%  to_representation  extras/api/customfields.py:150
   29.1%  to_representation  netbox/api/serializers/generic.py:34
   27.9%  get_object  netbox/api/serializers/generic.py:45
   18.9%  paginate_queryset  netbox/api/pagination.py:39
   12.3%  __init__  netbox/filtersets.py:343
    8.7%  __init__  netbox/filtersets.py:111
top self time (by file):
   28.5%  psycopg/connection.py
   13.8%  copy.py
```

```text
AGENT: `perfproof profile` ran the harness under pyinstrument and saw only the client:
96.8% of samples in socket.recv_into. The server is a child process it doesn't follow,
so I profiled request handling in-process by hand. Ranked hypotheses (caps are upper
bounds from the in-process profile at load ~40, not speedups):
  H1  Cable list N+1: each termination's nested serializer reads `.cable`, one query per
      termination (102 dcim_cable queries per page of 50). Prefetching
      `terminations__termination__cable` removes them.
      Prediction: dcim_cable queries 102 -> <=3. If they stay, the mechanism is wrong.
      Cap: the whole get_object family is 27.9% of mix time, so at most 1.39x on api-mix
      for H1+H2 together; H1 alone is part of that.
  H2  Same family: 74 dcim_device + 26 circuit + 26 provider lookups per cables page.
  H3  BaseFilterSet.__init__ (netbox/filtersets.py:111) rebuilds every filter with
      get_filters() on every request: 8.7% inclusive, cap 1.10x.
  H4  copy.py is 13.8% of self time; callers not yet attributed.
I'll start with H1: highest cap, a one-line change, falsifiable by a query count.
```

The cap arithmetic is Amdahl on a share measured in-process under a
profiler at load ~40, while the oracle run of §4 was loading the same
database; it is a bound on the mix, not a prediction of
what `compare` will see over HTTP.

## 6. Cheap test of H1

Candidate: `perfproof/h1-cable-fk` (`13161806b`), one line in
`netbox/dcim/api/views.py`:

```diff
-    queryset = Cable.objects.prefetch_related('terminations__termination')
+    queryset = Cable.objects.prefetch_related('terminations__termination__cable')
```

Query count on the candidate tree — 30.4 s, load 34.45 → 32.63:

```text
status 200; per request: median 1324.1 ms wall, 137 queries, 824.3 ms in execute
     74 x dcim_device                        446.0 ms
     26 x circuits_circuit                   159.7 ms
     26 x circuits_provider                  161.4 ms
      3 x dcim_cable                          11.8 ms
```

Prediction held: `dcim_cable` queries 102 → 3, total 236 → 137. The
time went *up* (577.8 → 1324.1 ms), but the two runs were five minutes
apart at load 12.9 and 32.6, and both overlapped the hand-runner's own
test run on the same Postgres container (§4), at different stages of
it. The time says nothing about H1 (findings F-15). A cheap test can trust counts; timings need interleaving,
which only `compare` does.

## 7. Prove H1, first attempt

`perfproof compare api-mix --repo netbox --baseline perfproof/bench
--candidate perfproof/h1-cable-fk --json` (PR #24, `463161b`) —
**1505.9 s**, load 28.80 → 48.79

No progress was visible for 25 minutes: the thin path writes no
`progress.json`, and `--detach` is not built (findings F-14). Not one
timed round ran. The whole 25 minutes went to the correctness gate:
the 46 cable tests three times on the baseline, then, because a test
failed on the candidate, five more times on each tree (8 baseline
runs, 6 candidate runs), at load up to 48.

```text
REJECT, reason correctness, decided by rule 1, gate correctness.
- side_effects: passed (3 spec env and 7 other environment variables, the commands and 2 config files)
- lock: passed (6 locked files match in both trees)
- procedure: passed;  runtime: passed (both trees run cpython 3.12.12);  same_host: passed
- correctness: failed
  Detail: 1 tests fail on the candidate and never on the baseline in 8 runs;
          output perfproof-out/responses.json differs (text): 1982 differences
  Difference <exit status>: baseline passed in all 8 runs, candidate failed ×6
  Difference perfproof-out/responses.json line 4:
    baseline  "next": "http://127.0.0.1:56158/api/dcim/cables/?limit=50&offset=50",
    candidate "next": "http://127.0.0.1:56198/api/dcim/cables/?limit=50&offset=50",
  …
FAIL: test_list_objects (dcim.tests.test_api.CableTestCase.test_list_objects)
AssertionError: Query count for dcim/cable:api_list_objects changed: expected 23, got 20.
If this change is intentional, re-run with UPDATE_QUERY_COUNTS=1 to update the baseline.
```

## 8. What the REJECT was

Two separate causes, settled by running things, not by reading.

**The 1,982 output differences are the harness's fault.** The harness
binds a free port per run, and NetBox writes absolute URLs (`url`,
`display_url`, `next`) built from the request's host and port. The
baseline differs from itself the same way — 13.3 s, load 3.41 → 3.93:

```text
$ tools/bb_diff.sh <baseline tree> <scratch>      # harness twice on the same tree
PERFPROOF {"value": 2.0256}
PERFPROOF {"value": 2.8272}
raw: differ, 1982 lines
<   "next": "http://127.0.0.1:57583/api/dcim/cables/?limit=50&offset=50",
>   "next": "http://127.0.0.1:57625/api/dcim/cables/?limit=50&offset=50",
port-normalized: identical
```

And the compare's own two output files, from its baseline and
candidate trees — 0.0 s, load 7.52 (run at 23:15, after the review
asked for the command):

```text
$ python3 -I tools/norm_diff.py <baseline tree>/perfproof-out/responses.json <candidate tree>/perfproof-out/responses.json
sizes: [584126, 584126] raw identical: False
normalized sizes: [562324, 562324] normalized identical: True
```

(1,982 replacements of `http://127.0.0.1:<port>` by `http://HOST`,
11 bytes shorter each.) So H1 changed no response byte.
`compare` produced each tree's output from one execution
(`pp-compare/src/perfproof/bench/compare.py:549-552`; on main
`src/perfproof/bench/compare.py:599-602`) and never
compared the baseline's output with itself, so nondeterministic output
became a correctness REJECT (findings F-02). Had the candidate been
any change at all, the verdict would have been the same.

**The failing test is real, and it fails because H1 removed queries.** NetBox
pins query counts per test in `netbox/dcim/tests/query_counts.json`
(`"cable:api_list_objects": 23`). H1 removed 3 of them in the test's
fixture, so the pin fails. NetBox's own workflow is to rerun with
`UPDATE_QUERY_COUNTS=1` and commit the new count with the change. The
spec didn't lock `query_counts.json`, so a candidate may update it,
which is what NetBox wants and also lets a candidate loosen any pin.
Locking it would make every query-count improvement fail forever
(findings F-03).

## 9. Fix the harness, redo the candidate

Harness v2 sends `Host: netbox.bench` so URLs don't carry the port
(`perfproof/bench` `784751191`). Baseline against itself, twice — 15.6
s, load 4.62 → 5.61:

```text
PERFPROOF {"value": 2.642}
PERFPROOF {"value": 3.7309}
raw: identical
```

(The two totals, 2.64 and 3.73 s back to back at load ~5, show how
noisy one execution is.)

`bench define api-mix` with the unchanged spec JSON — 0.4 s, load 5.80:

```text
"code": "spec_conflict", "message": "… is a frozen record of spec febd8ae7… with a different
lock manifest; it is never overwritten. Define a new spec instead."
```

Right to refuse, but it doesn't say which field to change; a harness
fix is the commonest reason (findings F-13). Defined as `api-mix-2`
(`b890c6aa…`), same JSON with a new name.

H1 rebased on harness v2, and the pin updated the NetBox way — 17.2 s,
load 5.75 → 7.42:

```text
$ UPDATE_QUERY_COUNTS=1 manage.py test --keepdb dcim.tests.test_api.CableTestCase   # candidate tree
-  "cable:api_list_objects": 23,
+  "cable:api_list_objects": 20,
```

Candidate `perfproof/h1-cable-fk` is now `374ec3bb2` (H1) +
`f7378c8bf` (pin update).

## 10. Prove H1, second attempt

`perfproof compare api-mix-2 --repo netbox --baseline perfproof/bench
--candidate perfproof/h1-cable-fk --json` — 370.5 s, load 7.29 →
10.35. Another workload on the Mac pushed the load up mid-run; an
`uptime` the hand-runner ran outside the step log at 22:17 read
`load averages: 28.84 11.18 5.88`.

```text
INCONCLUSIVE, reason insufficient_evidence, decided by rule 5.
Speedup estimate: 1.1146 (above 1 means the candidate is faster)
Interval at the last look: 1.0229 to 1.2144 at confidence 0.9875
Rounds: 20 of 20, looks 2 of 2, procedure t_log_ratio
wall_interval: 1.0061 to 1.1608;  metric_disagreement: no
cv_baseline 0.371, cv_candidate 0.290, sd_log_ratio 0.139, lag1_autocorrelation -0.193
Gates: side_effects, lock (6 files), procedure, runtime, same_host, correctness: passed
  correctness: "1 tests judged over 3 baseline and 1 candidate runs; 1 declared outputs compared"
  coverage: not available (T2.14)
Guardrails: peak_memory, p99_latency: unmeasurable (T2.15); peak_gpu_memory: not applicable
Warnings: high_load (7.29), differential_checks_not_run,
          order_not_balanced (candidate first in 11 of 20 rounds)
```

Per-arm values from the run's `samples.npz` (`tools/samples.py`):

```text
baseline  n=20 median 2.673 s  min 1.942  max 6.415  max_rss 286 MiB
candidate n=20 median 2.341 s  min 2.062  max 4.601  max_rss 288 MiB
```

Three things the skill has to read out of this:

- **The thin path can't KEEP.** INCONCLUSIVE is what every gate-passing
  comparison gives until calibration. The interval's lower bound
  (1.023) sits below the bar (1 + min_effect = 1.03), so even a
  calibrated rule 2 wouldn't have kept it at this noise.
- **The noise is the machine.** A coefficient of variation of 0.37 on
  the baseline means one execution of the mix ranged 1.94 to 6.42 s.
  The skill should say that a quieter machine would give a narrower
  interval, not that H1 is unproven in principle.
- **"1 tests judged"** means the whole Django suite counted as one
  test: `manage.py test` writes no JUnit XML, so the flaky-test logic
  works on the exit status of all 46 tests at once (findings F-19).
  `max_rss_bytes` is recorded per sample, but the `peak_memory`
  guardrail still reports "unmeasurable" (findings F-20).

## 11. Report to the user

```text
AGENT: One change tried, none kept.

H1, prefetch each cable termination's cable (perfproof/h1-cable-fk):
  - Cuts the cable list from 236 to 137 queries per page of 50 (measured in-process).
  - Response bodies are byte-identical to the baseline over the whole mix.
  - It changes NetBox's pinned query count for the cable list test (23 -> 20). I updated
    the pin the NetBox way (UPDATE_QUERY_COUNTS=1); review that file in the diff.
  - perfproof compare: INCONCLUSIVE. Estimate 1.11x on the whole mix, interval 1.02x to
    1.21x over 20 interleaved rounds. perfproof can't keep anything yet (its interval
    procedure isn't calibrated), and this machine was loaded (7 to 29 during the run),
    so the interval is wide. Nothing was kept.

My first comparison was wrong, and it was my fault: the benchmark put its random port
into every URL, so baseline and candidate always differed. Fixed; the benchmark is now
deterministic, and I checked the baseline against itself.

What's left, by cap (upper bounds, not predictions):
  H2  the same N+1 for devices, circuits and providers on the cable list
      (126 more queries per page)
  H3  BaseFilterSet rebuilds every filter on every request (8.7% of the mix)
  H4  deepcopy is 13.8% of self time, callers unknown
Next: H2 on the same benchmark, or run H1 again on a quiet machine to settle it?
```

The session ends here: the user's answer is not simulated further.
Entries 3 and 4 pick up H3 and H2 from other entry points.
