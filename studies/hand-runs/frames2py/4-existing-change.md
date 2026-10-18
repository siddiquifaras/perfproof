# frames2py, entry point 4: prove an existing change

- Date: 2026-10-16, 22:32 to 22:48, same machine and clone
  (`9e35f5f`).
- perfproof: `main` at `ce47494`; `compare` from PR #24 at
  `fad3631`.
- Both branches were written by the agent, playing an AI coding
  agent that had been asked to "make frames2py faster", before the
  skill saw them. Their commit messages are the kind of claim such an
  agent makes. The user's lines are **simulated**. No comparison here
  is a calibrated verdict.

> **user:** /perfproof I have two agent PRs, `agent/evt3-slices-128k`
> and `agent/exp-decay-bincount`. Are they faster, and did they break
> anything?

This is the fast path: no discovery. For each branch the skill reads
the diff, finds which existing benchmark covers the changed code,
checks the claim the commit makes, and compares.

## Branch 1: `agent/exp-decay-bincount` (`88400b6`)

```
perf(kernels): use bincount instead of add.at in ExpDecay.accumulate

np.add.at scatters one event at a time; bincount counts all events in one pass,
then a single multiply-add applies the weight.
```

```diff
-        np.add.at(state.stored, _flat_index(events, state.width), 1.0 / state.scale)
+        # bincount is a single vectorised pass, much faster than the scatter of add.at.
+        counts = np.bincount(_flat_index(events, state.width), minlength=state.stored.size)
+        state.stored += counts * (1.0 / state.scale)
```

**Agent (reading).** The change touches `ExpDecay.accumulate`, which
the existing benchmark `exp-decay-640-100k` (engine level, transcript
1) covers. Two things are already known before measuring:

- `git log -S bincount` shows the developer moved the count kernels
  *off* bincount (`6b09b2d`) to get rid of a frame-sized int64
  temporary.
- Transcript 1's cheap test timed `add.at` at 119.5 µs per call and
  `bincount*w` at 703.6 µs. The commit's claim ("much faster") points the other way from
  that microbenchmark (load 25; microbenchmarks moved 3.4x between
  runs here, so this is weak evidence).

It is also not bit-identical: repeated addition and one
multiplication round differently.

**Step 1: tests the gate will run, on the branch.** 22:36, 5.83 s, load 8.87

```
$ uv run --frozen pytest -q -p no:cacheprovider tests/contract/test_kernels.py tests/contract/test_engine.py tests/contract/test_properties.py tests/test_oracle.py
======================== 604 passed, 2 skipped in 5.23s ========================
```

**Step 2: wider tests, on the branch.** 22:36, 130.40 s

```
$ uv run --frozen pytest -q -p no:cacheprovider -x tests/contract tests/test_benchmarks.py tests/test_oracle.py
================= 1443 passed, 5 skipped in 130.40s (0:02:10) ==================
```

**Step 3: does the float32 output ever move?** `scratch/f2py-specs/drift.py`
runs both accumulations side by side on the gate stream. 22:38, load 7.97:

```
after     1 calls: float64 state pixels differing       0; float32 frame pixels differing 0
after    10 calls: float64 state pixels differing   16183; float32 frame pixels differing 0
after   100 calls: float64 state pixels differing   56886; float32 frame pixels differing 0
after  1000 calls: float64 state pixels differing   62609; float32 frame pixels differing 0
after  2000 calls: float64 state pixels differing   71681; float32 frame pixels differing 0
first call (checked every 50) with a float32 frame difference: None
```

On this stream the public output (float32 frames) never changes over
2,000 calls, even though the internal float64 state differs at up to
71,681 pixels. Whether that holds for other decays, rates or much
longer runs is **not checked**. "Bit-identical", in frames2py's own
words, is about outputs, so this needs the developer's ruling. The
correctness gate can't tell the two cases apart.

**Step 4: compare.** 22:38:57, 128.19 s, load 7.51 → 9.09

```
$ pp compare exp-decay-640-100k --candidate agent/exp-decay-bincount --baseline main
INCONCLUSIVE  insufficient_evidence  speedup 0.4237x  interval 0.3957x to 0.4537x  30 rounds
Gates: side_effects passed, lock passed, procedure passed, runtime passed, same_host passed, correctness passed, coverage not_available
  correctness: 606 tests judged over 3 baseline and 1 candidate runs; 1 declared outputs compared
  cv_baseline 0.104  cv_candidate 0.167  sd_log_ratio 0.132
  wall_interval 0.781 to 0.856
```

From `samples.npz`, read by hand, the median `max_rss_bytes` was
399.8 MiB for the baseline and 428.7 MiB for the candidate (+7.2%).
`report.md` lists `peak_memory` as "unmeasurable (T2.15)", even
though the run recorded every sample needed to judge it.

## Branch 2: `agent/evt3-slices-128k` (`8b25a82`)

```
perf(evt): decode EVT 3.0 in 128k-word slices

Fewer, larger slices amortise the fixed NumPy call overhead of _decode; the cumsum
is packed in uint64 so larger slices stay exact. Decoded events are unchanged.
```

The diff is the same as transcript 2's candidate, but with
`EVT3_SLICE_WORDS = 1 << 17`. **Agent (reading):** it touches
`Evt3Decoder._decode`, which `evt3-decode-active-marker-uv`
(transcript 2) covers. The commit's mechanism is the same one
transcript 2 measured at 2^20 (estimate 0.73x, interval 0.70x to
0.77x, uncalibrated, load 13.83). Transcript 2's after-the-fact sweep
favoured 2^17 (446 vs 488 ms at load 12), within noise.

**Step 5: compare.** 22:41:13, 387.31 s, load 8.78 → 8.59

```
$ pp compare evt3-decode-active-marker-uv --candidate agent/evt3-slices-128k --baseline main
INCONCLUSIVE  insufficient_evidence  speedup 0.9072x  interval 0.8805x to 0.9347x  30 rounds
Gates: side_effects passed, lock passed, procedure passed, runtime passed, same_host passed, correctness passed, coverage not_available
  correctness: 91 tests judged over 3 baseline and 1 candidate runs; 1 declared outputs compared
  cv_baseline 0.068  cv_candidate 0.082  sd_log_ratio 0.057
  wall_interval 0.897 to 0.939
```

By hand from `samples.npz`, the median `max_rss_bytes` was 69.2 MiB
for the baseline and 106.9 MiB for the candidate (**+55%**).

## Report

**Agent:** Everything below comes from an uncalibrated procedure, on
a loaded machine (load 7.5 and 8.8 at the start, 10 cores), so
these are estimates, not verdicts. The test results and the output
checks are facts, and they are stated as such.

- **`agent/exp-decay-bincount`:** estimate 0.42x, interval 0.40x to
  0.45x (uncalibrated), 30 rounds, cv 0.10 and 0.17. The commit says
  bincount is "much faster"; the estimate points the other way, and
  the earlier cheap test (119.5 vs 703.6 µs per call) points the same way in
  direction. Facts: the 1,443 tests in contract, benchmarks and
  oracle pass, and the float32 frames were identical over 2,000 calls,
  while the internal float64 state differs, so it isn't bit-identical
  inside. Median peak RSS per process, by hand: 400 vs 429 MiB. You
  also moved away from bincount on purpose in `6b09b2d`.
- **`agent/evt3-slices-128k`:** estimate 0.91x, interval 0.88x to
  0.93x (uncalibrated), 30 rounds, cv 0.07 and 0.08. Facts: the
  adapter tests and the event digest match. Median peak RSS, by hand:
  69 to 107 MiB (+55%).

Both intervals lie entirely below 1, which the first line of the
verdict (INCONCLUSIVE) doesn't show. Whether that should change what
the skill says is an open question for T3.12 (findings 1). Neither
change shows any evidence of being faster here, and I wouldn't merge
either on this evidence. `peak_memory` was unmeasurable, so no
guardrail was applied.
