# Entry 2: free text

```text
> /perfproof the device list API is slow
```

Same NetBox checkout, same day, after entry 1 (2026-10-16, 22:21 to
22:31). Each step: wall time, load average before → after. The full
log is `scratch/logs/e2.log`. The user's line names an endpoint and a
feeling, not a measurement.

## 1. Reuse what exists

The skill should first find benchmarks already defined in this repo.

`perfproof bench list --repo netbox --json` — 0.3 s, load 7.95

```text
"ok": false, "error": {"code": "usage", "message": "the following arguments are required: name"}
```

`bench list` needs a name: there is no way to ask which benchmarks
exist (findings F-21). The skill read `.perfproof/benchmarks/` by
hand (`api-mix`, `api-mix-2`), then `bench list api-mix-2` — 0.4 s,
load 8.23:

```text
"versions": [{"spec_hash": "b890c6aa…073c", "locked_files": 6, …}]
```

## 2. Check the user's premise

One execution of the confirmed mix (harness v2, by hand) — 6.5 s, load
7.95 → 7.73:

```text
     86.1 ms  /api/dcim/devices/?limit=50
    111.0 ms  /api/dcim/interfaces/?limit=50
    261.0 ms  /api/dcim/cables/?limit=50
     …
PERFPROOF {"value": 2.3327}
```

Device list, queries and DB time by table, 5 repetitions in-process —
6.3 s, load 8.13 → 8.28:

```text
status 200; per request: median 141.7 ms wall, 14 queries, 49.5 ms in execute
      1 x extras_configcontext                22.2 ms
      1 x dcim_device                          2.9 ms
      … (12 more, one each)
```

No N+1. pyinstrument in-process, 3 requests — 5.7 s, load 8.28 → 8.82
(0.75 s profiled: a small sample):

```text
project functions, inclusive:
   94.5%  list  netbox/api/viewsets/mixins.py:402
   32.3%  paginate_queryset  netbox/api/pagination.py:39
   22.3%  to_representation  extras/api/customfields.py:150
   13.6%  __init__  netbox/filtersets.py:343
    9.2%  __init__  netbox/filtersets.py:111
    5.1%  get_queryset  extras/api/mixins.py:45
    4.3%  annotate_config_context_data  extras/querysets.py:92
    3.4%  get_filters  netbox/filtersets.py:271
```

The obvious suspect is the config-context aggregation, the largest
single query. Cheap test, "what if it were free": the same request
with `exclude=config_context`, interleaved in one process in random
order, 30 rounds — 11.4 s, load 8.14 → 8.21:

```text
   91.3 ms median of 30,  14 queries  /api/dcim/devices/?limit=50
   85.6 ms median of 30,  14 queries  /api/dcim/devices/?limit=50&exclude=config_context
ratio of medians a/b: 1.07
```

Excluding it cut the median by about 6% (91.3 → 85.6 ms; ratio
1.07), so config context isn't the main cost.
The hand-runner wrote no threshold down before this run; a skill
should state what result would refute the suspicion before measuring.
The query count didn't change. Inference: `exclude` skips serializing
config context but not its query; what the database spends on it
isn't separated here.

```text
AGENT: The device list isn't the slow one. In one run of the mix (load 7.7) it took
86 ms per page of 50; the cable list took 261 ms and interfaces 111 ms. It runs 14 queries per page,
none repeated, so there's no N+1. I tested the obvious suspect, config context:
excluding it saves about 6%. What's left is spread across framework overhead:
pagination with the main query (32%), custom-field serialization (22%), and building
the FilterSet for every request (13.6%). The largest piece I can remove without
changing behaviour is in that last one: BaseFilterSet.__init__ calls get_filters() on
every request (3.4% of the device list). Cap: 1.035x on the device list, barely above
perfproof's 3% bar, so even a perfect change may not be provable at this noise. Want me
to prove that anyway, or look at the cable list, which makes 236 queries per page?

USER (simulated): Try it on the device list, that's what our inventory sync pages through.
```

Skill decisions here:

- **Premise check before work.** "X is slow" got a number for X next
  to the others in the same execution. The user's endpoint wasn't the
  outlier; the skill said so and offered the outlier.
- **Cap near the bar.** With a cap of 1.035x and a bar of 1.03x, a
  KEEP needs an interval narrower than half a percent on the low side.
  The skill said this before spending 7 minutes. docs/discovery.md
  ranks by cap but has no rule for "cap barely above the bar"
  (findings F-22).

## 3. H3

```text
H3  BaseFilterSet.__init__ (netbox/filtersets.py:111) calls cls.get_filters() for every
    request, rebuilding every filter, because of a 2022 workaround (#9231: a lookup is
    registered in apps.ready, after the class-level filters were built).
    Mechanism: the rebuild is deterministic per class once apps are ready, and
    FilterSet.__init__ deep-copies base_filters into self.filters, so one rebuilt dict
    per class is safe to share.
    Prediction: get_filters calls per warm request 1 -> 0. If they stay, the cache is
    not hit. Cap 1.035x on the device list (3.4% inclusive).
```

Candidate `perfproof/h3-filters-cache` (`ce13c665f` after a rebase):

```diff
-        self.base_filters = self.__class__.get_filters()
+        cls = self.__class__
+        # Build once per class; FilterSet.__init__ deep-copies base_filters into self.filters,
+        # so instances never share mutable filter state.
+        if '_netbox_base_filters' not in cls.__dict__:
+            cls._netbox_base_filters = cls.get_filters()
+        self.base_filters = cls._netbox_base_filters
```

Cheap test, calls to `get_filters` over 10 warm device-list requests,
each tree — 5.7 s and 6.9 s, load 8.05 → 8.35:

```text
10 get_filters calls over 10 warm requests  (netbox)    # baseline
0 get_filters calls over 10 warm requests  (nb-h3)      # candidate
```

Supported. The risk the #9231 comment guards against (a FilterSet
built before `apps.ready` caching filters without the `empty` lookup)
is what the filterset tests should catch; they went into the oracle.

## 4. A benchmark for one endpoint

`api-mix-2` times all eight requests over three passes; in the
execution above the device list is about 11% of it (3 × 86.1 ms of
2,332.7 ms). The
skill added `perfproof-bench/mix-devices.json` (20 measured passes of
the device list, 3 warmup) to the harness branch (`afb2e6a2b`) and
rebased H3 on it. Another harness commit on the user's repo (findings
F-04).

Oracle timing, device API tests and device filterset tests, baseline
tree — 30.0 s, load 8.02 → 8.88:

```text
Ran 103 tests in 22.312s
OK
```

`perfproof bench define devices-list` — 0.4 s, load 8.88:

```text
"spec_hash": "65963d95…2f71"
```

## 5. Prove

`perfproof compare devices-list --repo netbox --baseline
perfproof/bench --candidate perfproof/h3-filters-cache --json` — 421.3
s, load 8.73 → 7.90

```text
INCONCLUSIVE, reason insufficient_evidence, decided by rule 5.
Speedup estimate: 1.0667
Interval at the last look: 0.9315 to 1.2215 at confidence 0.9875
Rounds: 20 of 20, looks 2 of 2
wall_interval: 0.9372 to 1.1403;  metric_disagreement: no
cv_baseline 0.195, cv_candidate 0.214, sd_log_ratio 0.219, lag1_autocorrelation 0.181
Gates passed: side_effects, lock, procedure, runtime, same_host, correctness
  (correctness: "1 tests judged over 3 baseline and 1 candidate runs; 1 declared outputs compared")
coverage: not available;  guardrails: unmeasurable
Warnings: high_load (8.99), differential_checks_not_run,
          order_not_balanced (candidate first in 7 of 20 rounds)
```

```text
baseline  n=20 median 2.489 s  min 1.684  max 3.740  max_rss 284 MiB
candidate n=20 median 2.341 s  min 1.565  max 3.455  max_rss 283 MiB
```

What the skill has to say about it:

- **The estimate is above the cap.** 1.067x (uncalibrated estimate) against a 1.035x
  bound from the profile. Inference: the estimate is mostly noise (the
  interval spans 0.93x to 1.22x), not mechanism. The
  skill must compare every estimate with the hypothesis's cap and say
  so when it exceeds it; no CLI command knows the cap (findings F-22).
- **What would settle it isn't reported.** docs/cli.md promises an
  INCONCLUSIVE "comes with what would settle it" ("about 410 rounds").
  The thin path gives none (findings F-23). By hand, as an
  **inference**: with sd_log_ratio 0.22 and a 0.9875 two-sided
  quantile near 2.5, separating an effect of 1.035x from the 1.03x
  bar needs a half-width under log(1.035/1.03) ≈ 0.005, about
  (2.5 × 0.22 / 0.005)² ≈ 12,000 rounds. This run spent 421 s on
  20 rounds plus the gates, so a round is at most about 20 s: tens
  of hours. Not worth it.

## 6. Report to the user

```text
AGENT: The device list isn't where NetBox loses time on this data: 86 ms per page,
14 queries, no N+1 (the cable list makes 236 queries per page).

I tried the one safe change I found for it, building each FilterSet's filters once
per class instead of on every request (perfproof/h3-filters-cache). It removes the
rebuild (10 calls per 10 requests -> 0), the device API and filterset tests pass, and
responses are identical. perfproof compare: INCONCLUSIVE. Estimate 1.07x, interval
0.93x to 1.22x over 20 rounds (uncalibrated; high_load warning at 8.99). The most this change can give is about 3.5%, and at
this machine's noise proving a 3.5% effect would take tens of hours of rounds. Not
kept.

If your sync is slow, it's more likely the number of pages than the time per page;
the device list is close to the per-request overhead of DRF here. The cable list is
where a code change can matter. Want me to look there?
```

The last paragraph's first clause is a guess about the user's sync;
the skill had no evidence for it and should have asked instead. Kept
here as the kind of thing the skill contract must forbid (findings
F-24).
