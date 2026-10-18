# frames2py, entry point 2: free text

- Date: 2026-10-16, 22:14 to 22:28, same M4 and clone (`9e35f5f`)
  as transcript 1.
- perfproof: `main` at `ce47494` (with `profile` merged) for `bench`
  and `profile`; `compare` from PR #24 at `fad3631`.
- `pp` is `uv run --project <worktree> perfproof`. The user's lines
  are **simulated** by the agent. Load is `uptime` (1, 5, 15 min).
- No comparison here is a calibrated verdict.

> **user:** /perfproof decoding EVT3 recordings is slow

**Step 1: find what the words point at.** 22:14:47, 0.13 s, load 6.98

```
$ grep -rln 'Evt3Decoder\|evt.open' src benchmarks tests
src/frames2py/adapters/evt.py
src/frames2py/adapters/_evt_decode.py
src/frames2py/replay.py
tests/adapters/test_evt_golden.py  … (10 files)
```

"Decoding EVT3" points at `Evt3Decoder` in
`src/frames2py/adapters/_evt_decode.py`, which `adapters/evt.py`
reads in 1 MiB chunks. There is a project harness for it,
`python -m benchmarks adapters --recording NAME`, but it needs real
recordings. The committed fixture `tests/data/active_marker_head.evt3.raw`
is only 262 KB.

**Step 2: is there real data?** 22:14:51, 0.09 s, load 7.38

```
$ uv run --frozen python -m tests.recordings list
active_marker.raw   evt3   108.7 MB  present  CC0 1.0 …
faery_evt3.raw      evt3     9.4 MB  present  unknown: … states no licence for its test data
… directory: ~/.cache/frames2py/recordings
```

The recordings are already in the user's cache, so nothing needs
downloading. That data lives outside the repository: the lock can't
hash it, and the spec can't declare it as an input.

A first try on the 262 KB fixture (22:14:52, 5 runs in 0.95 s) took
about 2.5 ms per pass. That is far too small to stand for "slow
recordings".

**Step 3: the project's own measurement.** 22:14:59, 23.00 s, load 7.51 → 8.14

```
$ uv run --frozen python -m benchmarks adapters --recording active_marker.raw --runs 3 --out …
run 1/3 active_marker.raw: 22316758 events, decode 1390.4 ms
run 2/3 …  decode 1309.2 ms
run 3/3 …  decode 1309.9 ms
  decode                17.04 M events/s   (24.1x the recording's 0.706 M events/s)
  ingest only          235.65 M events/s
  end to end            10.29 M events/s   (adapter 96% of the time)
  decode peak traced memory 5.8 MiB; worker max RSS 387 MiB
```

**Agent:** You're right. Decoding takes 96% of reading and ingesting
`active_marker.raw` end to end: 17 M events/s, against 236 M events/s
for ingest alone. I'll measure decoding that 108.7 MB EVT 3.0
recording from your cache, three passes per process. The time is the
median pass, and the oracle is the SHA-256 of the decoded events plus
the EVT adapter tests. The recording sits outside the repo, so
perfproof can't lock it: if it changes, nothing will notice. Is this
the workload that matters, or do you decode EVT 2.0 too?

> **user (simulated):** active_marker is representative of EVK4
> recordings, EVT3 only. Go ahead.

**Step 4: the workload, and checking its noise.** Script
`scratch/f2py-specs/evt3_decode.py`. Version 1 hashed inside the
timed loop, so I moved the hash to one untimed extra pass. 22:15:56,
4 runs in 23.16 s, load 8.00 → 8.37:

```
PERFPROOF {"value": 1.516}   1.3777   1.3687   1.42
22316758 52420f6154adbffd7165b648ec16eb5b142ad3720aa6b5055a2e367d4ef5aa7a
```

The runs span 11% at load 8.

**Step 5: tests to gate on.** 22:16:25, 7.09 s:
`tests/adapters/test_evt_golden.py`, `test_chunking_properties.py`
and `test_core_integration.py`, 71 passed and 20 skipped.

**Step 6: define.** 22:16:41, 0.52 s, load 12.23

```
$ pp bench define evt3-decode-active-marker --spec scratch/f2py-specs/spec2.json --json
  "spec_hash": "fc920fc56c621af0e68c9599530c00e92befc1302b0cf57bfb21b74c4f92312b"
```

Command `.venv/bin/python <abs script>` with `PYTHONPATH=.`, so that
`profile` accepts it (transcript 1, Step 7). Metric `reported`, s,
lower is better, objective `wall_time`. The output check compares
`perfproof-out/events.sha256` as text.

`harness` needs at least one path in the repo, but the real harness
(the script) and the input (the recording) both live outside it. So
`harness` holds `src/frames2py/adapters/__init__.py` as a stand-in,
and nothing that decides the number is locked.

**Step 7: profile.** 22:16:53, 27.72 s, load 19.47 → 34.47

```
$ pp profile evt3-decode-active-marker --top 20
  12.9%  13.5%  src/frames2py/adapters/_evt_decode.py:208  Evt3Decoder._decode
   7.9%   7.9%  <built-in>  ndarray.cumsum  (library)
   7.8%   8.7%  src/frames2py/adapters/_evt_decode.py:245  Evt3Decoder._decode
   6.7%  21.7%  src/frames2py/adapters/_evt_decode.py:195  Evt3Decoder._decode
   5.4%   5.4%  <built-in>  ndarray.repeat  (library)
   3.7%   3.7%  src/frames2py/adapters/_evt_decode.py:255  Evt3Decoder._decode
   …
Unprofiled run: wall 11.8 s, CPU 6.68 s …
For at least 43% of wall time no thread of the benchmark was on a CPU: it was waiting, not computing.
Load average 19.5 on 10 CPUs.
The load exceeds the CPU count, so part of any gap between wall and CPU time is waiting for a CPU.
```

The load caveat is new in `main`'s `profile` since transcript 1. Line
attribution doesn't hold up for NumPy-heavy code. Line 208 is
`if not is_vector.any():` and line 255 is `return out`, yet they get
12.9% and 3.7% self time: pyinstrument charges them for the
vectorised statements before them. I had to read lines 186 to 255 as
one block.

**Step 8: hypothesis.** `log` and `measure` don't exist, so this was
done by hand.

- **H1.** `_decode` runs about 60 NumPy calls per 32,768-word slice,
  and a 108.7 MB body is about 1,660 slices per pass. A fixed cost per
  slice would shrink with larger slices. **Prediction:** if right,
  time per slice fits `a + b·words` with a large `a`, and raising the
  slice size cuts total time by about `a·slices`. If wrong, `a` is
  near 0.
  Cheap test `cheap_slice.py` on the first 32 MiB of the body, at
  sizes 2^11 to 2^15 (the uint32 packing is only exact up to 2^15).
  22:17:50, 11.01 s, **load 36.58**:
  ```
  slice   2048 words:   8192 slices,   1535.6 ms,   187.4 us/slice
  slice   4096 words:   4096 slices,    592.5 ms,   144.7 us/slice
  slice   8192 words:   2048 slices,    460.4 ms,   224.8 us/slice
  slice  16384 words:   1024 slices,    471.9 ms,   460.8 us/slice
  slice  32768 words:    512 slices,    371.7 ms,   726.0 us/slice
  fit: per-slice fixed cost a = 104.3 us, per-word cost b = 19.25 ns
  share of the 32768-word run that is fixed cost: 14.4%
  ```
  I read this as **supported**, with a cap of 1/(1-0.144) = 1.17x.
  It was measured at load 36 and only ever below 2^15, so the
  extrapolation was untested. I recorded that and went on, which was
  the mistake (Step 11).

**Step 9: candidate.** Branch `perfproof/evt3-large-slices`, commit
`3c08121`. `EVT3_SLICE_WORDS = 1 << 20`, with the cumsum packed as
uint64 (32/32 bits) so it stays exact. `evt.py` feeds 1 MiB (2^19
words) at a time, so in practice each feed is one slice.

Before that, the docstring says `batch_size=None` gives "the
decoder's own boundaries", so the change in array sizes is not an API
change. The whole adapter suite still passes: 22:18:33, 11.46 s, 277
passed and 213 skipped.

**Step 10: compare, first try.** 22:18:58, 1.20 s, load 17.88

```
$ pp compare evt3-decode-active-marker --candidate perfproof/evt3-large-slices --baseline main
REJECT  integrity  gate runtime  0 rounds
Gates: side_effects passed, lock passed, procedure passed, runtime failed, same_host passed
  runtime: the trees run different runtimes; the candidate runtime is unknown: '.venv/bin/python' is not an executable file
  Difference runtime_executable: baseline ~/.local/share/uv/python/cpython-3.11.14-…/python3.11, candidate absent
```

The baseline worktree `.perfproof/worktrees/baseline-9e35f5fed608/`
was reused from transcript 1's compare and still had the `.venv` that
`uv run` made there. The new candidate worktree had none. So the
same spec passes or fails depending on what an earlier run left
behind. The result is labelled **REJECT**, even though nothing about
the candidate was judged.

I redefined the spec as `evt3-decode-active-marker-uv` with
`uv run --frozen python <abs script>`: 22:19:18, `daf4fb30…`. Now
the two specs differ only in the launcher, one for `profile` and one
for `compare`.

**Step 11: compare.** 22:19:29, 449.73 s, load 13.83 → 10.38

```
$ pp compare evt3-decode-active-marker-uv --candidate perfproof/evt3-large-slices --baseline main
INCONCLUSIVE  insufficient_evidence  speedup 0.7319x  interval 0.6977x to 0.7677x  30 rounds
The procedure is not calibrated yet: no KEEP or performance REJECT is given.
Gates: side_effects passed, lock passed, procedure passed, runtime passed, same_host passed, correctness passed, coverage not_available
  correctness: 91 tests judged over 3 baseline and 1 candidate runs; 1 declared outputs compared
  cv_baseline 0.122  cv_candidate 0.125  sd_log_ratio 0.092
  wall_interval 0.711 to 0.771
```

By hand from `samples.npz`, the median `max_rss_bytes` was 67.7 MiB
for the baseline and 209.6 MiB for the candidate, about 3.1x. That is
the largest memory change in this study, and `peak_memory` reported
it as "unmeasurable".

Estimate 0.73x (interval 0.70x to 0.77x, uncalibrated, load 13.83
at the start). On this seconds metric 1/0.73 is about 37% more decode
time, if the estimate held. The whole interval lies below 1, yet the
verdict line says only INCONCLUSIVE: until calibration the thin path
can't give a performance REJECT. Whether and how a report should
convey that is an open question for T3.12.

**Step 12: after the fact, the cheap test that should have come
first.** I swept the candidate's own code (uint64 packing) from 2^14
to 2^20 words, on the same 32 MiB. 22:27, load 12.05:

```
slice  16384 words:   1024 slices,    530.0 ms
slice  32768 words:    512 slices,    488.2 ms
slice  65536 words:    256 slices,    482.2 ms
slice 131072 words:    128 slices,    446.3 ms
slice 262144 words:     64 slices,    545.8 ms
slice 524288 words:     32 slices,    503.6 ms
slice 1048576 words:    16 slices,    566.2 ms
```

Total time is roughly flat across sizes. The per-word cost rises
with slice size, which I infer (not measured) to be the working set
outgrowing the caches. So the "fixed cost" `a` from Step 8 was an
artefact of fitting below 2^15. The sweep also disagrees with compare
in size: it shows 2^19 taking only 3% more time than 2^15, against
compare's 0.73x (about 37% more time). One feed of 32 MiB is not the reader's 1 MiB feeds, so the
cheap test didn't represent the workload either.

**Agent (report):** Decoding EVT 3.0 is 96% of reading and ingesting
`active_marker.raw` (that is the project's own measurement). My idea,
that fixed per-slice overhead dominated, wasn't supported: with
2^20-word slices the estimate was 0.73x (interval 0.70x to 0.77x,
uncalibrated, 30 rounds, load 13.83 at the start, cv 0.12). The
decoded events were identical and the tests passed (facts). Nothing
is kept. perfproof printed INCONCLUSIVE because its procedure isn't
calibrated yet. The sweep afterwards put 2^17 at 446 ms against 488 ms
for 2^15, which is within this machine's noise; that is a lead to
test, on a quieter machine first.
