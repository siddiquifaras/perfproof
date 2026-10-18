# Entry 4: prove an existing change

```text
> /perfproof I had an agent speed up the cable list API. It made two branches,
  agent/cable-prefetch and agent/cable-defer. Are they faster?
```

2026-10-16, 22:33 to 22:57. Each step: wall time, load average before
→ after. Full log: `scratch/logs/e4.log`.

The two branches were written by the hand-runner acting as an AI
agent asked to "speed up the cable list API", before any of this
entry's measurements, starting from `v4.7.3` as an agent working in
the user's checkout would:

| Branch | Commit | What the agent did |
|---|---|---|
| `agent/cable-prefetch` | `e0f2da620` | A real attempt: declares `field_prefetches` for `a_terminations`/`b_terminations` with a `GenericPrefetch` that `select_related`s each termination's device (or circuit and provider, or power panel) and cable, following NetBox's own `InterfaceViewSet` idiom |
| `agent/cable-defer` | `6fd4e686b` | Plausible-looking: `.defer('comments', 'custom_field_data')` on the cable queryset, with a comment claiming list pages are dominated by row transfer. The serializer outputs both fields |

Neither agent updated NetBox's pinned query counts. This is the
prove-only path: no discovery, a spec already exists (`cables-list`,
entry 3).

## 1. Read the changes

Static — 0.1 s, load 8.73:

```text
== agent/cable-prefetch
e0f2da620 Claude (agent): Fix N+1 queries in the cable list API
base: v4.7.3
 netbox/dcim/api/views.py | 18 +++++++++++++++++-
== agent/cable-defer
6fd4e686b Claude (agent): Speed up the cable list API by deferring large columns
base: v4.7.3
 netbox/dcim/api/views.py | 4 +++-
-    queryset = Cable.objects.prefetch_related('terminations__termination')
+    # Skip the large free-text and JSON columns when loading cable rows; list pages
+    # are dominated by row transfer from Postgres.
+    queryset = Cable.objects.prefetch_related('terminations__termination').defer('comments', 'custom_field_data')
```

Skill decision: the spec to prove against. `cables-list` measures
exactly the endpoint both commits claim to speed up, and its oracle
holds the cable API tests. The baseline is `perfproof/bench`
(`v4.7.3` + harness), not `v4.7.3` itself, because only that tree has
the harness (findings F-04).

## 2. The branch as given

`perfproof compare cables-list --repo netbox --baseline
perfproof/bench --candidate agent/cable-prefetch --json` — 1.5 s, load
7.51

```text
REJECT, reason integrity, decided by rule 1, gate side_effects.
- side_effects: failed
  Detail: candidate tree: config perfproof-bench/nbconfig.py is missing, so it cannot be
  checked; config perfproof-bench/nbtestconfig.py is missing, so it cannot be checked
- lock, procedure, runtime, same_host, correctness, coverage: not listed in this record
```

Right not to run anything, but the record says the experiment broke a
safety gate when the real state is "this candidate isn't built on the
spec's baseline". The skill has to recognise that state (the candidate
lacks the harness commit), fix it, and not show the user a
side-effects REJECT. `compare` writes a run record and a verdict for
it, as if the candidate had been judged (findings F-29).

## 3. Put each change on the harness, unchanged

Cherry-pick each agent commit onto `perfproof/bench` — 1.4 s, load
8.05:

```text
perfproof/agent-prefetch: e96ce6c56 Fix N+1 queries in the cable list API
perfproof/agent-defer: 93971562e Speed up the cable list API by deferring large columns
```

A cherry-pick can conflict when the change touches files the harness
branch also changed; here it didn't. The skill must say it built a
derived branch, and the report must name both the user's commit and
the derived one. Today's record says "Not recorded: the repository
and the candidate's commit" (findings F-29).

## 4. Cheap tests

Queries by table on each derived tree, 5 repetitions in-process — 6.3
s and 6.2 s, load 7.97 → 7.51:

```text
# perfproof/agent-prefetch
status 200; per request: median 64.8 ms wall, 10 queries, 19.2 ms in execute
# perfproof/agent-defer
status 200; per request: median 295.4 ms wall, 336 queries, 200.4 ms in execute
    202 x dcim_cable                         110.4 ms
     74 x dcim_device                         49.2 ms
     26 x circuits_circuit                    16.5 ms
     26 x circuits_provider                   14.4 ms
```

Baseline, same measurement in entry 3: 236 queries. The defer branch
adds 100 `dcim_cable` queries: each deferred column is loaded one
cable at a time when the serializer reads it. Expected before any
timing: agent-prefetch fewer queries (236 -> 10), agent-defer more (236 -> 336).

## 5. agent/cable-prefetch

`compare … --candidate perfproof/agent-prefetch` — 221.3 s, load 7.74
→ 11.15

```text
REJECT, reason correctness, decided by rule 1, gate correctness.
  Detail: 1 tests fail on the candidate and never on the baseline in 8 runs;
          1 tests judged over 8 baseline and 6 candidate runs; 1 declared outputs compared
  Difference <exit status>: baseline passed in all 8 runs, candidate failed ×6
AssertionError: Query count for dcim/cable:api_list_objects changed: expected 23, got 15.
If this change is intentional, re-run with UPDATE_QUERY_COUNTS=1 to update the baseline.
```

The declared output (the cable list response) matched; only NetBox's
query-count pin failed.

```text
AGENT: Both branches started from v4.7.3, without the benchmark harness, so perfproof
refused the first one before running anything. I put each agent commit, unchanged, on
top of the harness branch: perfproof/agent-prefetch and perfproof/agent-defer.
  Query counts per page of 50 cables (in-process): baseline 236,
  agent/cable-prefetch 10, agent/cable-defer 336.
  agent/cable-prefetch: responses identical, but NetBox's own test
  CableTestCase.test_list_objects fails: it pins the cable list at 23 queries and the
  branch now does 15. That's the test doing its job; the agent didn't update the pin
  (NetBox's workflow: UPDATE_QUERY_COUNTS=1). May I add the pin update as a separate
  commit, marked as mine, and prove the speed?

USER (simulated): Yes.
```

Pin update on the derived branch — 16.1 s, load 12.12 → 12.78:

```text
Ran 37 tests in 9.672s
OK
-  "cable:api_list_objects": 23,
+  "cable:api_list_objects": 15,
```

Committed separately (`66f7e11ad`). Then `compare` again — 289.8 s,
load 12.32 → 11.30:

```text
INCONCLUSIVE, reason insufficient_evidence, decided by rule 5.
Speedup estimate: 5.7360
Interval at the last look: 5.1689 to 6.3653 at confidence 0.9875
Rounds: 20 of 20
wall_interval: 1.8262 to 2.0515
cv_baseline 0.210, cv_candidate 0.125, sd_log_ratio 0.167, lag1_autocorrelation -0.060
Gates passed: side_effects, lock, procedure, runtime, same_host, correctness
Warnings: high_load (12.32), differential_checks_not_run, order_not_balanced (7 of 20)
```

```text
baseline  n=20 median 3.302 s  min 2.484  max 5.496  max_rss 276 MiB
candidate n=20 median 0.597 s  min 0.514  max 0.813  max_rss 275 MiB
```

Entry 3's own fix (H1+H2, also 10 queries) measured 4.73x to 5.44x on
the same spec, 10 minutes earlier at load ~8. The two intervals don't
overlap. That doesn't make the agent's version better: each interval
is conditional on its own session (the baseline's median was 2.74 s
in one run and 3.30 s in the other). To rank them, they must be
compared against each other or through `confirm`, which isn't built
(findings F-28).

## 6. agent/cable-defer

`compare … --candidate perfproof/agent-defer` — 164.8 s, load 11.52 →
6.68

```text
REJECT, reason correctness, decided by rule 1, gate correctness.
  Detail: 1 tests fail on the candidate and never on the baseline in 8 runs; …
AssertionError: Query count for dcim/cable:api_list_objects changed: expected 23, got 27.
```

NetBox's pin failed on a change the agent called a speedup: the query
count rose (236 -> 336, 23 -> 27 in the test). That deterministic fact
is the whole answer for this branch.

The user asked "are they faster?". To answer for this branch the
skill would have to raise the pin, which loosens the oracle. It did
so once, in a commit that exists only for this measurement, and said
so:

```text
AGENT: agent/cable-defer is rejected: NetBox's pinned query-count test fails (expected
23, got 27 in the test fixture; on the demo data a page goes from 236 to 336 queries).
Deferring comments and custom_field_data doesn't skip them: the serializer still
outputs both, so Django loads them one cable at a time. To answer how the timing compares
I'll raise the pin in a commit that only exists for this measurement; it will never be
kept.
```

Pin raised (23 → 27) — 7.3 s, load 5.87 → 5.72. `compare` — 326.0 s,
load 5.72 → 8.85:

```text
INCONCLUSIVE, reason insufficient_evidence, decided by rule 5.
Speedup estimate: 0.7720
Interval at the last look: 0.6453 to 0.9236 at confidence 0.9875
Regression: no
wall_interval: 0.7957 to 0.9531
cv_baseline 0.457, cv_candidate 0.463, sd_log_ratio 0.290, lag1_autocorrelation -0.300
Warnings: high_load (5.72), differential_checks_not_run, order_not_balanced (7 of 20)
```

```text
baseline  n=20 median 2.332 s  min 1.489  max 5.638  max_rss 275 MiB
candidate n=20 median 3.083 s  min 1.893  max 7.193  max_rss 274 MiB
```

The whole interval is below 1: in this run the candidate takes 1.08x
to 1.55x as long as the baseline (1/0.9236 to 1/0.6453). The report still says **"Regression: no"**, because the
flag is set only on a performance REJECT (`pp-compare/src/perfproof/schema.py:1300`),
and rule 3 can't fire before calibration. A skill that reads
`regression` instead of the interval would tell the user nothing was
wrong (findings F-30). The noise was high (cv 0.46 on both arms), so
this run shows an interval entirely below 1 (uncalibrated), not a reliable size.

## 7. Report to the user

```text
AGENT: agent/cable-prefetch cuts the cable list to 10 queries per page (236 -> 10);
agent/cable-defer raises it to 336. Both statements are query counts, not timing.

agent/cable-prefetch (measured on perfproof/agent-prefetch = your commit + the
benchmark harness + a pin update I added):
  - Queries per page of 50 cables: 236 -> 10. Response identical to before.
  - Timing, uncalibrated: estimate 5.7x (interval 5.2x to 6.4x, 20 interleaved rounds,
    load 12.3). The run carried warnings: load 12.3,
    candidate first in 7 of 20 rounds, no differential checks. perfproof can't keep
    changes until its statistics are calibrated, so the verdict reads INCONCLUSIVE.
    A separate run on the same spec gave 4.7x to 5.4x for a different change with the
    same query count, so treat the size as rough; no ranking between the two.
  - It needs the pin in netbox/dcim/tests/query_counts.json updated 23 -> 15, or
    NetBox's own test fails. That's in a separate commit for you to take or redo.

agent/cable-defer:
  - NetBox's query-count test fails: deferring the two columns makes Django fetch them
    one cable at a time, 100 more queries per page (236 -> 336).
  - Timing, uncalibrated, with the pin raised only to measure it: estimate 0.77x
    (interval 0.65x to 0.92x, load 5.7). That run had load 5.7, candidate first in 7 of
    20 rounds, and a coefficient of variation of 0.46 on both arms, so the size is
    rough.
  - The commit's comment ("list pages are dominated by row transfer") doesn't match
    the query counts: per-row queries rise from 236 to 336 per page.
  - Recommendation: don't take it. The reason is the deterministic query count
    (236 -> 336) and NetBox's failing pin, not the timing.

Not checked for either: coverage of the changed lines, memory and p99 latency
(perfproof can't measure them yet), filtered or brief requests, and writes.
```
