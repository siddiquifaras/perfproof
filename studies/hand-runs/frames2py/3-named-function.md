# frames2py, entry point 3: a named function

- Date: 2026-10-16, 22:28 to 22:35, same machine and clone
  (`9e35f5f`).
- perfproof: `main` at `ce47494` for `bench` and `profile`; `compare`
  from PR #24 at `fad3631`.
- The user's lines are **simulated**. Load is `uptime`. No comparison
  here is a calibrated verdict.

> **user:** /perfproof TimeSurface.accumulate

**Agent (reading).** `src/frames2py/kernels/_builtin.py:152`:

```python
def accumulate(self, events, state, watermark):
    np.maximum.at(state.latest, _flat_index(events, state.width), np.ascontiguousarray(events["t"]))
```

The function is one scatter-max over a flat index. In the benchmark
suite it runs in every `time_surface` gate cell, at both levels. A
named function has no workload of its own, so the skill has to choose
the cells that run it. I chose the same condition as transcript 1
(640x480, 100k uniform, 16 ms) at **kernel level** (`v1-kernel`), so
the engine's publication overhead doesn't dilute the function.

**Step 1: cheap test, is the scatter already fast?** 22:28:47, 0.34 s, load 10.85

```
$ .venv/bin/python scratch/f2py-specs/cheap_ts.py
time-ordered within every call: True
maximum.at           median    117.0 us  min    112.5 us
add.at (same args)   median    117.7 us  min    112.4 us
ordered assign       median    204.3 us  min    189.8 us
ordered assign identical to maximum.at: True
```

`np.maximum.at` is on NumPy's fast indexed loop, the same speed as
`add.at`. The obvious alternative assigns `latest[idx] =
max(latest[idx], t)`, and it is exact only if events are time-ordered
within the call and fancy assignment keeps the last write, which
NumPy doesn't promise. In this microbenchmark (load 10.85) it took 204.3 µs against
117.0 µs. **H1 (replace the scatter) wasn't supported.**

**Step 2: cheap test, is the `t` copy redundant?** 22:29:18, 0.63 s, load 9.50

```
$ .venv/bin/python scratch/f2py-specs/cheap_contig.py
rep 0 copy then maximum.at   median    151.0 us
rep 0 strided maximum.at     median   2698.8 us
rep 1 copy then maximum.at   median    145.0 us
rep 1 strided maximum.at     median   2470.3 us
```

Dropping `np.ascontiguousarray` looks like a free cleanup, but it
makes the scatter take about 17x as long in a microbenchmark
(2698.8 vs 151.0 µs, load 9.50): the strided, unaligned uint64
values fall off the fast path. The copy is load-bearing, and the code
gives no comment saying so. History doesn't help either: `git log -S`
leads only to `fa29107 feat: add kernel protocol and the five v1
kernels`. **The skill must not propose removing it.**

**Step 3: cheap test, H2 `_flat_index`.** It reads `x` and `y` as two
strided, unaligned uint16 fields. They are adjacent at offsets 8 and
10, so one strided uint32 view gives both. 22:29:01, 0.26 s, load 10.41:

```
$ .venv/bin/python scratch/f2py-specs/cheap_index.py
rep 0 _flat_index    median   168.7 us
rep 0 packed uint32  median   150.3 us
rep 1 _flat_index    median   129.5 us
rep 1 packed uint32  median   153.9 us
identical: True
```

**Inconclusive**: the two reps disagree in sign. It is exact, so it
is still a candidate.

**Step 4: define, kernel level.** 22:29:30, about 0.35 s each

```
$ pp bench define time-surface-640-100k-kernel      --spec spec3.json      → e8608aea…
$ pp bench define time-surface-640-100k-kernel-prof --spec spec3-prof.json → 2edeb9b2…
```

Same shape as transcript 1: `uv run … python -c` for compare and a
`-prof` twin with `.venv/bin/python <script>` for `profile`. Metric:
median-call M events/s, 200 timed calls.

Noise check, 22:29:31, 5 runs in 75.62 s, load 9.26 → 8.66:

```
PERFPROOF {"value": 368.24}  360.69  350.9  391.71  330.78
```

That is a 17% spread, and each execution took about 15 s.

**Step 5: how much of the cell is this function?** 22:30:51, 23.95 s, load 8.45

```
$ pp profile time-surface-640-100k-kernel-prof --top 8 --json
  unprofiled: wall 11.10 s, cpu 10.93 s (cpu_fraction 0.98), load_average 8.45
```

Filtering the folded stacks by hand (the CLI can't focus on a
function):

```
total 11.86 s; under TimeSurface.accumulate 0.12 s = 1.0%
   61.3% _flat_index
   32.8% ufunc.at
    6.0% ascontiguousarray
by call in the script: run_suite 97.1%
   90.8% measure_run > finish (targets/v1.py:234) > reference (targets/v1.py:123)
```

The function the user named is **1.0%** of each execution. The
harness's own correctness check (`reference()` over 201 batches)
takes 90.8%, outside the timed region. The `reported` metric still
measures only the timed calls, so the verdict isn't diluted. But each
round spends about 22 s to time well under 1 s, and nothing in
perfproof reports how small the measured region is next to the
execution.

**Decision:** cut `timed_calls` from 200 to 50. That makes a new spec
version under the same name. 22:31:50:

```
$ pp bench define time-surface-640-100k-kernel --spec spec3b.json → 27b14bc3…
$ pp bench list time-surface-640-100k-kernel
  27b14bc3…  34 locked files
  e8608aea…  34 locked files
```

Cap for H2 if `_flat_index` were free: 1/(1-0.613) = 2.6x of the
function. That is about 2.6x of the kernel-level timed calls, and
~1% of an execution.

**Step 6: candidate.** Branch `perfproof/packed-flat-index`. The
contract tests passed: 22:31:51, 5.53 s, 604 passed and 2 skipped.
The project's own CI check failed, though (22:32:03, 3.31 s):

```
$ uv run --frozen mypy src/frames2py/kernels/_builtin.py
src/frames2py/kernels/_builtin.py:34: error: Value of type "MappingProxyType[...] | None" is not indexable  [index]
```

The correctness gate runs only the spec's pytest command, so it would
have passed a change that breaks the project's CI. I fixed it with the
codebase's own idiom (`# type: ignore[index]`, as in `_events.py`) and
committed `1be27e1` (22:32:14, mypy: Success).

**Step 7: compare.**

```
$ pp compare time-surface-640-100k-kernel --candidate perfproof/packed-flat-index --baseline main
perfproof compare: error: benchmark 'time-surface-640-100k-kernel' has 2 matching specs; pick one with --spec-hash
```

That came back in 0.81 s, without saying which spec is newer. Then,
22:32:20, 168.60 s, load 7.13 → 10.34:

```
$ pp compare time-surface-640-100k-kernel --spec-hash 27b14bc3 --candidate perfproof/packed-flat-index --baseline main
INCONCLUSIVE  insufficient_evidence  speedup 0.9019x  interval 0.8698x to 0.9353x  30 rounds
Gates: side_effects passed, lock passed, procedure passed, runtime passed, same_host passed, correctness passed, coverage not_available
  correctness: 606 tests judged over 3 baseline and 1 candidate runs; 1 declared outputs compared
  cv_baseline 0.040  cv_candidate 0.050  sd_log_ratio 0.070  lag1 -0.071
  wall_interval 0.981 to 1.026   metric_disagreement: no
```

**Agent (report):** `TimeSurface.accumulate` is already close to
what NumPy offers. Its scatter (`np.maximum.at`) runs on NumPy's
fast path, and its copy of `t` is what keeps it there: without the
copy the microbenchmark took about 17x as long. I'd suggest a comment there so nobody
"cleans it up". The one exact rewrite I tried, reading `x` and `y`
in a single pass, gave estimate 0.90x on the kernel-level benchmark (interval 0.87x
to 0.94x, uncalibrated, 30 rounds, load 7.13 at the start). Nothing
is kept. It also shows two things about your harness:

- This function is only 1% of a benchmark execution. The reference
  check takes 91%, so each execution spends about 99x as long outside
  the function as inside it (1.0% of 11.86 s).
- The packed version first broke your mypy CI, which the gate I ran
  doesn't include.

The verdict reads INCONCLUSIVE and the interval is entirely below 1.
What a calibrated procedure would say is unknown.
