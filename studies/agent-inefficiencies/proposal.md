# Proposal: an evidence column for the angle checklist

For the lead to apply to `docs/discovery.md` ("Measurement angles").
Nothing in `docs/discovery.md` was edited by this task.

The counts come from [data.csv](data.csv) and are explained in
[catalogue.md](catalogue.md). 126 changes were classified by their
primary kind: 117 map to an angle below, 8 are `none` (no inefficiency
in the diff) and 1 is `mixed` (a 29-file squash with no single primary
kind). They are counts of changes I classified, not rates: reverted and
regression changes are the complete population among the non-held-out
pool, landed changes are a draw plus an 18-change keyword supplement, so
the landed/reverted split says nothing about how often a kind is
reverted. One reader classified, from the head of each diff; n is small.

## What to change

1. Add a column, **Seen in classified diffs (counts, not rates)**, to the
   angle table: how many classified changes were of this kind and how
   each ended (landed, reverted, introduced a regression). It does not
   say how often an angle pays off or how large the payoff is.
2. Add two angles the evidence supports and the table lacks:
   **Skippable work** and **Bandwidth against peak** (the second is thin:
   4 changes).
3. Keep every existing angle. Each has at least 4 changes except
   Serialization (see "Not covered").

## The table

The first three columns are today's text, with the Evidence cell of the
rows I touched extended; the last column is new. `n` is primary-kind
changes; "draw" is how many of them were in the 55-change random draw.
The landed, reverted and regression cells leave out closed-unmerged and
not-submitted changes (9 in all: 3 closed PRs and 6 unsubmitted agent
runs), so they can sum to less than `n`.

| Angle | What it finds | Evidence | Seen in classified diffs (counts, not rates) |
|---|---|---|---|
| CPU hotspots | Functions that dominate CPU time | `perfproof profile` on folded stacks; on native code also instructions per work unit (Linux counters) and a microbenchmark of the loop against a vectorised variant | n 22 (draw 13): 14 landed, 4 reverted, 1 regression. Kinds: codegen 11 (native 8, gpu 3), cheaper primitive 7, per-element loop 4 |
| Waiting, not computing | Time lost to I/O, locks, the GIL or network calls | Gap between wall time and CPU time; off-CPU profiles. CPU time is summed over every thread, so an idle thread pool that spins (OpenBLAS under NumPy on Linux) can push it above wall time and hide the gap; check the off-CPU profile when CPU exceeds wall. Also syscalls and bytes per operation, and on GPU the overlap between streams | n 8 (draw 2): 4 landed, 2 reverted, 1 regression. serialized 5, repeated I/O 3, lock contention 0 |
| Repeated identical work | The same call with the same arguments, again and again | Calls captured by the differential checks: distinct arguments over total calls is the hit potential | n 19 (draw 7): 13 landed, 5 reverted, 1 regression |
| **Skippable work** (new) | Work whose result the path does not use, or that a fast path, early exit, pushdown or lazy branch could skip | The "what if this were free?" cheap test: workload time with the stage stubbed out; rows scanned against rows returned; share of calls whose result is discarded | n 16 (draw 8): 9 landed, 6 reverted, 1 regression. 2 of the 6 reverts were for broken behaviour and 4 give no reason. Needs an edge-case oracle |
| Growth with input size | Code that gets worse than linear as inputs grow | `perfproof measure --sizes` at n, 2n, 4n; for membership and lookup, profile share of those calls | n 6 (draw 4): 5 landed, 1 reverted. quadratic 3, wrong data structure or algorithm family 3 |
| Startup and imports | Slow starts and cold paths | `python -X importtime`, startup timing; wall time with the import stubbed | n 4 (draw 4): 4 landed. All lazy imports |
| Allocation churn | Objects created and thrown away in hot paths | Allocation profiles: allocations and bytes per call, peak memory, GC share | n 10 (draw 2): 5 landed, 3 regression, 2 not submitted. 3 of the 8 regressions in the sample were copies or GC cost; 2 agent attempts here gained nothing or lost (T3.11) |
| Database patterns | N+1 queries, missing batching | Queries counted per request, and their slope against result size | n 6 (draw 2): 3 landed, 1 reverted, 2 not submitted. Fewer queries was not faster in one agent run (T3.11 X3) |
| GPU idle time | Data loading, tiny kernels, host-to-device copies | GPU timelines: kernels per step, mean duration, gap fraction | n 11 (draw 4): 7 landed, 3 reverted, 1 not submitted. Fusion and launch overhead; serving 6, gpu 4, python 1 |
| **Bandwidth against peak** (new, thin) | Kernels and loops that move bytes slower than the device or memcpy can | Achieved bytes per second over device peak; sectors per request and L2 hit rate (Nsight Compute); roofline position | n 4 (draw 4): 4 landed. Thin: vector or coalesced loads, loop order, L2 locality |
| Serialization | Time spent encoding and decoding | Stage timing in service workloads | n 0 as a primary kind. 2 parser changes (serde_json `\u` escapes, pandas integer parsing) were filed under codegen and cheaper primitive |
| Settings | Batch sizes, thread counts, flags, backends | `perfproof measure --set`, then `perfproof confirm` across settings; log which kernel or backend ran | n 11 (draw 3): 8 landed, 2 reverted, 1 regression. settings and backend selection 7, serial where parallel 4 |

## Why these two additions

- **Skippable work** has no row today. It is 16 of the 126 classified
  changes and 8 of the 55 in the random draw, the same order as repeated
  work (7) and instruction-level changes (7). Its cheap test already
  exists in "Cheap tests" (stub the component out); the angle only says
  to look for it. The reverts with a stated breaking reason cluster
  here and in repeated work (4 of 5), so the angle should be paired with
  a correctness check on inputs the author did not think of. That
  pairing is INFERENCE from 5 reasons, not a measured effect.
- **Bandwidth against peak**: 4 changes are primarily about access
  pattern or achieved bandwidth (vector or coalesced loads, loop order,
  L2 locality), and none of the table's current evidence cells measures
  it: GPU idle time finds gaps between kernels, not kernels that are
  slow while busy. That last point is INFERENCE from how the evidence
  cells are worded, and 4 changes is thin, so the row rests on a
  mechanism the diffs name, not on a count. A fifth candidate (vllm#48597,
  a 29-file squash that includes a bandwidth-bound GEMM) is `mixed` and
  not counted.

## Notes on what the evidence does not support

- **Agent against human.** 12 agent changes against 114 human ones show
  no kind the humans lack. What they show is the failure the table's
  Evidence column exists for: 4 of the 12 (T3.11 X1, X2, X3 and
  llama.cpp#29182) targeted a kind that sounded right and the measured
  or later result did not follow. INFERENCE: this supports requiring the
  confirmation step on every kind rather than a per-kind warning; the
  sample cannot test that.
- **Ranking.** Do not rank angles by these counts alone: the draw is 55
  changes, the keyword supplement over-represents rare kinds, and the
  pool was selected by the miner for performance words and numbers in
  the change's text.
- **Counters.** Instruction-level changes are the largest native group
  and the macOS-only workflow cannot read counters (AGENTS.md: counter
  metrics are Linux-only in v1), so for those the evidence column
  names a microbenchmark.

## Not covered

- **Lock contention and false sharing**: no change in the sample. The
  Waiting row names off-CPU profiles, which is general practice, not
  something this data adds to.
- **Blocking call in async code**: none as stated; the nearest are 5
  `serialized` changes (a blocking network call at startup, a CPU-GPU
  sync, a serial graph build, sequential awaited writes, an unoverlapped
  stream).
- **Serialization**: no primary-kind change; the row is unchanged.

## Not verified

Classification was by one reader from the head of each diff; 56 of 120
diffs were read in part. The measurements named in the Evidence cells
were not run. See [catalogue.md](catalogue.md), "Not verified".
