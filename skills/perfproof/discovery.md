# Finding ideas

## The cap: the most a change could save

If a function takes a share `p` of the run (`total_fraction` in the
profile), making it infinitely fast saves at most `1 / (1 - p)`:

| `p` | Cap |
|---|---|
| 0.03 | 1.03x |
| 0.10 | 1.11x |
| 0.50 | 2x |
| 0.90 | 10x |

A cap at or below `1 + min_effect` (1.03 by default) can't clear the bar; drop the
idea before writing code. A cap is a bound, not a promise: tell the
user "at most", never "will". Say which profile it came from.

## Angles to check

Look past the top hotspot. Each angle has the cheap measurement that
confirms or rules it out. The counts are how many of 126 real
performance changes were of that kind (counts, not success rates).

| Angle | What it finds | Cheap check | Seen |
|---|---|---|---|
| CPU hotspots | Functions that dominate CPU time | the profile; a microbenchmark of the loop against a vectorised version | 22 |
| Repeated identical work | The same call with the same arguments, again and again | count distinct arguments over total calls | 19 |
| Skippable work | Work whose result is never used, or that an early exit, fast path or lazy branch could skip | time the run with that stage stubbed out | 16 |
| GPU idle time | Tiny kernels, launch overhead, host-device copies | kernels per step, gaps in the GPU timeline | 11 |
| Settings | Batch sizes, thread counts, flags, backends | change one setting, measure | 11 |
| Allocation churn | Objects created and thrown away in hot paths | allocations and bytes per call, peak memory | 10 |
| Waiting, not computing | I/O, locks, network calls | wall time well above CPU time; an off-CPU profile | 8 |
| Growth with input size | Worse than linear as inputs grow | time at n, 2n, 4n | 6 |
| Database patterns | N+1 queries, missing batching | queries per request against result size | 6 |
| Startup and imports | Slow starts, heavy imports | `python -X importtime` | 4 |
| Bandwidth against peak (thin evidence) | Loops or kernels moving bytes slower than the device can | achieved bytes per second over peak | 4 |

Coding agents usually write code that works, not code that is fast.
The common leftovers are loops that call the same thing repeatedly,
row-by-row work over data frames or arrays, a query per item, reading
the same file again, and building something on every call that could
be built once.

## Cheap tests

Before writing a real change, run the cheapest check that would prove
the idea wrong:

- Stub the stage out (return a cached result) and time it: that's the
  real ceiling for this workload.
- Count calls or queries instead of timing them. Counts don't move with
  machine load.
- Time the inner function alone at two input sizes.

Use these to choose what to try. They are never evidence that a change
is faster; only `compare` says that.

## When to stop

- Every remaining idea's cap is at or below the bar.
- The depth's attempt limit is reached.
- The profile is flat (no function above about 10%) and the angles find
  nothing. Say so: "nothing left could clear the bar" is a real answer,
  but only when no idea with a cap above the bar is left untried. When
  the attempt limit stops you first, name what is left and its cap.
