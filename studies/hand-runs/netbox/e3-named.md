# Entry 3: a named function

```text
> /perfproof GenericObjectSerializer.get_object in netbox/api/serializers/generic.py
```

2026-10-16, 22:24 to 22:38. Each step: wall time, load average before
→ after. Full log: `scratch/logs/e3.log`. The user names code, not a
workload. The skill has to find a workload that exercises it, find out
whether it is worth anything, and prove a change on that workload.

One hand-run artifact: the H2 candidate's code was written at 22:26,
while entry 2's compare was running, to avoid adding load during its
rounds. In a real session it would come after step 3. Nothing measured
depends on that order.

## 1. Find the function and its workloads

Static search — 0.3 s, load 10.58:

```text
46:    def get_object(self, obj):
47-        if obj.__class__ not in self._serializer_cache:
48-            self._serializer_cache[obj.__class__] = get_serializer_for_model(obj)(nested=True, context=self.context)
49-        serializer = self._serializer_cache[obj.__class__]
50-        return serializer.to_representation(obj)
---
./dcim/api/serializers_/cables.py          # the only user outside its own module
```

`GenericObjectSerializer` is used only by `CableSerializer`, for
`a_terminations` and `b_terminations`. So the workload is the cable
list; `api-mix-2` covers it among seven other requests. No CLI command
maps a function to the workloads that run it (findings F-25); the
skill used `grep`. A skill that relies on grep will miss dynamic
dispatch, such as a serializer looked up by model at run time, as
`get_serializer_for_model` itself does.

## 2. How much of the cable list is it?

pyinstrument in-process, 3 cable-list requests — 5.8 s, load 6.96 →
7.04:

```text
profiled 1.42s, 3 passes x 1 requests
   52.2%  [self]  psycopg/connection.py
   99.2%  list  netbox/api/viewsets/mixins.py:402
   80.1%  to_representation  extras/api/customfields.py:150
   77.5%  to_representation  netbox/api/serializers/generic.py:34
   76.6%  get_object  netbox/api/serializers/generic.py:45
```

The function's own body is four lines and a cached lookup. Its 76.6%
is what the nested serializers it calls do, and more than half the
profile is psycopg: waiting on the database.

```text
AGENT: get_object is 77% of the cable list's time, but not because of its own code: it
calls a nested serializer for each cable termination, and those read the
termination's cable and its device (or circuit and provider) one query at a time. A
page of 50 cables runs 236 queries. Changing get_object itself can't fix that; loading
those objects in bulk can. That's in the cable viewset, not in the function you named.
I'll do that and prove it on the cable list.
```

Skill decision: the user named a function; the cause is elsewhere.
The skill says so before changing a different file. The contract has
to define whether the skill may move off the named target (findings
F-26).

## 3. H1 + H2

```text
H1+H2  Nested termination serializers trigger 102 dcim_cable, 74 dcim_device, 26
       circuits_circuit and 26 circuits_provider queries per page (entry 1). One
       GenericPrefetch with a select_related per termination type loads them in a
       constant number of queries.
       Prediction: queries per page 236 -> about 10, all repeated shapes gone. If the
       repeated shapes stay, the prefetch isn't used (for example a dynamic prefetch
       overriding it).
       Cap: get_object is 76.6% of the cable list in-process, so at most 1/(1-0.766) =
       4.27x on cables-list, if all of it were removed.
```

Candidate `perfproof/h2-generic-prefetch`: H1 (`8d8111461`), H1's pin
update (`619323dbe`), then H2 (`18931e714`):

```diff
-    queryset = Cable.objects.prefetch_related('terminations__termination__cable')
+    queryset = Cable.objects.prefetch_related(
+        # Nested termination serializers read each termination's cable and its device (or
+        # circuit and provider, or power panel): load them per type in one query each.
+        GenericPrefetch('terminations__termination', [
+            *(
+                model.objects.select_related('device', 'cable')
+                for model in (ConsolePort, ConsoleServerPort, FrontPort, Interface, PowerOutlet, PowerPort, RearPort)
+            ),
+            CircuitTermination.objects.select_related('circuit__provider', 'cable'),
+            PowerFeed.objects.select_related('power_panel', 'cable'),
+        ]),
+    )
```

Cheap test, queries by table, 5 repetitions in-process on each tree —
5.6 s and 4.8 s, load 7.12 → 7.31:

```text
# baseline (perfproof/bench)
status 200; per request: median 300.5 ms wall, 236 queries, 197.9 ms in execute
    102 x dcim_cable                          78.4 ms
     74 x dcim_device                         73.4 ms
     26 x circuits_circuit                    19.7 ms
     26 x circuits_provider                   16.7 ms
# candidate (perfproof/h2-generic-prefetch)
status 200; per request: median 53.6 ms wall, 10 queries, 12.9 ms in execute
      3 x core_objecttype                      3.5 ms
      2 x dcim_cable                           1.8 ms
      1 x dcim_interface                       2.4 ms
      … (one each)
```

Prediction held: 236 → 10 queries. The two timings ran 10 s apart at
the same load, back to back, not interleaved: suggestive, not
evidence (findings F-15).

The pin moves again. `UPDATE_QUERY_COUNTS=1` on the candidate — 10.5
s, load 7.13 → 8.14:

```text
Ran 37 tests in 5.879s
OK
-  "cable:api_list_objects": 20,
+  "cable:api_list_objects": 15,
```

Committed as `bc21e59ea` (findings F-03: the spec doesn't lock this
file, so the candidate may move the pin).

## 4. A benchmark for the cable list

`perfproof-bench/mix-cables.json` (10 measured passes of the cable
list, 2 warmup) went into the harness branch with the device mix
(`afb2e6a2b`). `perfproof bench define cables-list` — 0.3 s, load 8.37:

```text
"spec_hash": "7df39256…3237"
```

Oracle: the 46 cable and cable-termination API tests, as in entry 1.

## 5. Prove

`perfproof compare cables-list --repo netbox --baseline
perfproof/bench --candidate perfproof/h2-generic-prefetch --json` —
257.4 s, load 8.02 → 8.72

```text
INCONCLUSIVE, reason insufficient_evidence, decided by rule 5.
Speedup estimate: 5.0715
Interval at the last look: 4.7279 to 5.4402 at confidence 0.9875
Rounds: 20 of 20
wall_interval: 1.7175 to 1.8586
cv_baseline 0.142, cv_candidate 0.056, sd_log_ratio 0.113
Gates passed: side_effects, lock, procedure, runtime, same_host, correctness
coverage: not available;  guardrails: unmeasurable
Warnings: high_load (8.02), differential_checks_not_run,
          order_not_balanced (candidate first in 11 of 20 rounds),
          autocorrelated_rounds (lag-1 autocorrelation of the log ratios is 0.328, above 0.3)
```

```text
baseline  n=20 median 2.743 s  min 2.192  max 3.314  max_rss 276 MiB
candidate n=20 median 0.533 s  min 0.496  max 0.613  max_rss 274 MiB
```

What the skill has to read out of this:

- **INCONCLUSIVE with an interval of 4.7x to 5.4x.** The thin path
  can't KEEP anything; the verdict says nothing about this candidate.
  The skill must explain that in one sentence the user can't misread:
  "perfproof can't keep changes yet; estimate 5.07x, interval 4.7x to
  5.4x, uncalibrated, load 8.0, candidate first in 11 of 20 rounds,
  lag-1 autocorrelation 0.33" is an honest set; "INCONCLUSIVE" alone
  reads as "didn't work", and the interval alone reads as a verdict
  (findings F-27).
- **The estimate exceeds the cap.** 5.07x measured against a 4.27x
  bound. Inference, not tested: the cap came from an in-process
  profile, where the profiler's overhead and the test client change
  each part's share, while the benchmark goes through gunicorn and
  HTTP; the interval's warnings (above) could also contribute. A cap is only a bound for the workload it was
  measured on (findings F-22).
- **Two metrics, both above 1.** The wall interval (1.72x to 1.86x)
  includes gunicorn's startup in every execution, which the change
  doesn't touch, so it's much smaller; `metric_disagreement` is about
  direction only, and is "no".
- **Autocorrelated rounds** (0.328) and an unbalanced order are
  reported as warnings. The skill has no rule for what to do about
  them.

## 6. Report to the user

```text
AGENT: get_object itself is fine. Its cost was 228 of the 236 queries per page of
50 cables, issued by the nested serializers it calls. I changed the cable viewset to load
each termination's cable and its device, circuit and provider in bulk
(perfproof/h2-generic-prefetch: GenericPrefetch with a select_related per
termination type).

  - Queries per page: 236 -> 10.
  - Response: the cable list page is byte-identical to before (compared once per tree).
  - NetBox's pinned query count for the cable list test moves 23 -> 15; the pin
    update is a separate commit for you to review.
  - perfproof compare on the cable list: timing, uncalibrated: estimate 5.07x
    (interval 4.7x to 5.4x, 20 interleaved rounds, load 8.0). The run carried warnings: load 8.0,
    candidate first in 11 of 20 rounds, autocorrelated rounds (lag-1 0.33), no
    differential checks. The verdict is INCONCLUSIVE because perfproof can't keep
    changes until its statistics are calibrated; the interval is a measurement to
    read with those warnings, not a verdict.
  - Not checked: coverage of the changed lines, memory and p99 latency (perfproof
    can't measure them yet), and request mixes other than the plain cable list
    (filters, brief mode, writes).
```
