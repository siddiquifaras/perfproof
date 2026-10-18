# Examples and decoys

Small programs with a known bottleneck, its real fix, and decoy
candidates that perfproof must not keep. They are the fixtures for the
end-to-end loop (T2.8), the profiler integration test (T2.10) and the
real noise traces (T2.11).

| Directory | Language | Bottleneck | Fix |
|---|---|---|---|
| `rolling/` | Python, NumPy | a pure-Python loop over a NumPy array | `np.cumsum` and one slice difference |
| `membership/` | Python | `in` on a list inside a loop, O(n m) | build a `set` once, O(n + m) |
| `colsum/` | C | column-by-column walk of a row-major matrix | walk it row by row |

## Layout

Each example directory is a self-contained project: its own
`pytest.ini`, benchmark, tests, and benchmark specs. To use one with
perfproof, copy it into its own git repository. Paths in its specs are
relative to the example directory.

A **candidate** is a directory `<example>/candidates/<name>/` holding a
full copy of every file the candidate changes, at the same path relative
to the example; nothing else. A candidate tree is the example without
its `candidates/` directory, with those files copied over it.
`materialize.py` builds one:

```bash
uv run python examples/materialize.py rolling fix scratch/rolling-fix
uv run python examples/materialize.py rolling scratch/rolling-baseline
```

Full copies were chosen over patches so every candidate is plain source
that ruff and pyright check, and so a candidate can be run in place
with no patch tool. `diff -r` against the example shows the change.

## Running

```bash
uv run pytest examples/rolling       # rolling's own suite (includes the flaky test)
uv run pytest examples/membership    # membership's own suite
uv run pytest examples               # the checks below, 25 to 55 s here
make -C examples/colsum check        # build baseline and fix, compare sums
```

None of these run in the main `uv run pytest`: `testpaths` there is
`tests/`, and each directory here has its own `pytest.ini`. The two
example suites take under a second each; `uv run pytest examples` builds
every candidate tree in a temporary directory and runs it in
subprocesses, so it is slower and kept out of the default run.

`uv run pytest examples` (`tests/test_examples.py`) checks, for both
Python examples:

- the baseline, the fix and every decoy except `wrong-output` pass the
  example's suite (the deliberately flaky test deselected), and
  `wrong-output` fails it;
- the benchmark output of the fix and of every decoy except
  `wrong-output` and `bench-edit` is identical to the baseline's, and
  `wrong-output`'s differs;
- only `bench-edit` changes a file the spec locks, and it changes only
  the harness;
- every spec validates as a `perfproof.schema.BenchmarkSpec` and names
  files that exist; the remote-database spec differs from `spec.json`
  only in a non-local `DATABASE_URL`;
- the flaky test both passes and fails across seeds;
- each `untested-branch` decoy behaves differently from the baseline on
  an input no test uses;
- each `memory-cache` decoy peaks at least 2.5 times the baseline's
  maximum RSS, and each fix at most 1.25 times, on the median of three
  runs per tree (one tree's peak RSS varied by up to 9% across ten
  runs, see "Measured effects"); every benchmark run there is killed with its
  process group if it outlives a timeout;
- the C fix gives byte-identical sums (skipped without `make` and `cc`).

## Benchmarks

Every benchmark is a command that runs the whole workload once, so
`wall` and `cpu` metrics work on it as it is. The timing code is in the
locked harness, never in the code a candidate changes, and it prints
one line, `PERFPROOF {"value": <seconds>}`, for a `reported` metric
that excludes interpreter start-up and input generation. With
`--output PATH` the Python benchmarks also write their result, outside
the timed region, for output comparison.

| Example | Command | Harness (locked) | Oracle (locked) | Output |
|---|---|---|---|---|
| `rolling` | `python bench.py --output output/sums.npy` | `bench.py` | `tests/test_rolling.py` | `.npy` of int64 sums, compared as `numbers` |
| `membership` | `python bench.py --output output/held.txt` | `bench.py` | `tests/test_orders.py` | held order IDs, one per line, compared as `text` |
| `colsum` | `make bench`, or `build/colsum ROWS COLS OUTPUT` | `bench.c`, `kernel.h`, `Makefile`, once it has a spec | none | `build/sums.txt`, column sums to 17 significant digits, compared as `bytes` |

`make check` in `colsum/` builds the baseline and `candidates/fix/`
from the same harness, runs both on the same 4096 x 4096 matrix and
fails unless `cmp` finds their sums byte-identical. Both versions add
each column's values in the same order, from row 0 down, so the
comparison can demand the same bits rather than a tolerance.

`spec.json` in each Python example is the benchmark spec, in schema
version 3. It names `wall_time` as the objective and declares the three
default guardrails of `docs/method.md` (the target class is unknown, so
all three are required): peak memory at the 10% default allowance, and
peak GPU memory and p99 latency not applicable, each with a reason.
Its budget and look schedule are a starting point for T2.8, not a
measured choice.
`pytest.ini` is locked as config, so a candidate can't deselect tests.
`colsum` has no spec: the schema has no build step yet.

## The examples

**`rolling/`.** `rolling.rolling_sum(values, width)` sums every window
of `width` consecutive int64 counts. The baseline keeps a running total
in a Python loop that indexes the NumPy array one element at a time
(`rolling.py:48-50`), two NumPy scalar reads and one write per step.
The benchmark sums windows of 3,000 over 20 series of
100,000 counts. The fix (`candidates/fix/`) takes one `np.cumsum` into
a prefix array with a leading zero and returns the difference of two
slices of it. Its results are exact, so outputs compare equal bit for
bit. The suite holds the deliberately flaky test.

**`membership/`.** `orders.held_orders(orders, blocked_customers)`
returns the orders placed by blocked customers. The baseline tests
`order.customer_id in blocked_customers` on the list it was given
(`orders.py:53`), a linear scan per order. The benchmark screens 40,000
orders against a blocklist of 2,000 distinct IDs plus 30 repeats. The
fix builds `set(blocked_customers)` once. This example ships the
remote-database spec.

**`colsum/`.** `column_sums` in `kernel.c` sums each column of a
row-major 4096 x 4096 matrix of doubles by walking down the column
(`kernel.c:9`), so consecutive reads are 32 KiB apart. The fix in
`candidates/fix/kernel.c` walks the matrix row by row and adds each row
into the sums array. It is here for the native work in M4; it has a fix
and no decoys.

## Decoys

Each Python example ships the same six decoys. None may be kept. The
"refused by" column names the gate or rule in `docs/method.md` that
should stop it. `side_effects`, `coverage`, the `guardrail` reason and
flaky-test quarantine are specified there but not yet in the schema
(T2.12) or the code.

| Candidate | What it changes | What it demonstrates | Refused by |
|---|---|---|---|
| `bench-edit` | The benchmark, not the code: `rolling` sums 5 series instead of 20, `membership` screens 10,000 orders instead of 40,000 | A candidate that gets faster by shrinking the workload (failure mode 7) | `lock` gate: `bench.py` is a locked harness file. REJECT, `integrity`, gate `lock`, before any timing |
| `wrong-output` | The fast version with a bug. `rolling` drops the prefix array's leading zero, so it returns one window fewer, each shifted by one step. `membership` keys the held orders by customer, keeping only each blocked customer's last order | Faster but wrong (failure mode 10) | `correctness` gate: the suite fails and the declared output differs. REJECT, `correctness` |
| `noop-refactor` | Renames and an extracted helper (`rolling`) or a renamed local (`membership`); the same operations run | A change with no real effect must never come out KEEP (failure mode 1) | Decision rule 3 (REJECT, `performance`) once the interval's upper bound is at or below 1.03, otherwise rule 5 (INCONCLUSIVE) when the budget runs out |
| `slight-speedup` | A real but small saving. `rolling` sums the first window with one NumPy call instead of a Python loop. `membership` drops repeated IDs from the blocklist before scanning it | A genuine speedup below the 3% bar ("What counts as an error" in `docs/method.md`) | Decision rule 3 or 5: REJECT, `performance`, or INCONCLUSIVE; never KEEP |
| `untested-branch` | The real fix plus one fast path that no test and no benchmark run reaches. `rolling` returns the caller's array itself when `width == 1`, so writing to the result changes the input. `membership`, for a one-entry blocklist, compares that ID with `order_id` instead of `customer_id` | Faster, passes every test, and still wrong in code nothing checked (failure mode 11) | `coverage` gate: the fast path's lines never run under the suite or the captured calls. REJECT, `correctness`, gate `coverage` |
| `memory-cache` | Faster by caching everything. `rolling` keeps a Python list of every series it has seen and of its prefix sums. `membership` builds a list of 2^24 booleans, one per possible customer ID, and keeps it per blocklist | Speed bought with memory (failure mode 13) | Peak-memory guardrail, default allowance 10%. REJECT, `guardrail`, with the speedup estimate kept |

The fix and every decoy except `bench-edit` change only the module
under test (`rolling.py`, `orders.py`); `bench-edit` changes only
`bench.py`.

**Deliberately flaky test.**
`rolling/tests/test_rolling.py::test_last_window_is_at_least_average`
draws 64 counts from an unseeded generator and asserts that the last
window's sum is at least its mean, so it fails on about half of all
runs whatever the code. It is marked `deliberately_flaky`: deselect it
with `-m "not deliberately_flaky"`, or set `ROLLING_FLAKY_SEED` to make
a run reproducible. perfproof should find it in the three baseline runs
of the suite, quarantine it, and blame no candidate for it. With a
failure chance of one half, three runs show it flaky with probability
1 - 2 (1/2)^3 = 0.75, so about one session in four won't see it
(failure mode 12, "flakiness that never shows in three runs").

**Remote-database spec.** `membership/spec-remote-db.json` is
`spec.json` with `DATABASE_URL=postgresql://db.example.com:5432/orders`
in the spec's environment. `example.com` is reserved for documentation
(RFC 2606), so no real service is behind it. The benchmark never reads
the variable: perfproof must refuse the spec before anything runs, with
gate `side_effects` and reason `integrity`.

## Measured effects

Every figure below is one measured run on one Apple M4 MacBook Pro on
AC power, on 2026-10-16 (times are PKT, UTC+5), with other agents' work
running on the same machine; the load average at the start of each run
is given. In every run, each round ran the baseline and the candidate
benchmark once each as fresh processes, in an order drawn at random per
round, after one discarded run of each. A speedup is the geometric mean
of `baseline / candidate` over the rounds, with a 95% t interval on the
mean log ratio. "Reported" is the benchmark's `PERFPROOF` value; "wall"
is the whole process, start-up included. These were one-off checks, not
perfproof's procedure, and they are not perfproof verdicts. Another
run, or another machine, will give other numbers.

### The slightly faster decoys

Both are near 1.5% by construction:

- `rolling` saves the warm-up loop over the first `WIDTH` values of
  each series. With `N = 100,000` values per series, each warm-up
  iteration costing `r` times a main-loop iteration, the saved fraction
  is `f = W r / (W r + N - W)`. Timed in isolation, `r` was 0.32 to 0.36
  (warm-up 59 to 65 ns, main loop 163 to 193 ns per iteration, three
  repeats, 40 calls each, minimum). The reviewer's 150-round run at
  `WIDTH = 4,500` measured 2.25%, which implies `r` = 0.49. At
  `WIDTH = 3,000` that gives `f` from 3000 x 0.34 / 98020 = 1.0% to
  3000 x 0.49 / 98470 = 1.5%.
- `membership` drops 30 repeats from a 2,030-entry blocklist. A scan
  for a customer who isn't blocked, about 95% of orders, gets
  30 / 2030 = 1.48% shorter. Scans that find their customer stop early
  in both versions.

| Decoy | Run | Start, load | Reported speedup (95% interval) |
|---|---|---|---|
| `rolling` A/A control (the baseline against a copy of itself) | 60 rounds | 04:58, 5.8 | 0.9935 (0.9814 to 1.0057) |
| `rolling` `slight-speedup`, `WIDTH = 3,000` | 150 rounds | 04:59, 6.2 | 1.0180 (1.0109 to 1.0252) |
| `membership` `slight-speedup` | 150 rounds | 05:01, 8.5 | 1.0166 (1.0094 to 1.0238) |

Measured on this Mac, both intervals' upper bounds sat under the 3%
bar, by about half a point, and both lower bounds sat above 1, so
both decoys are measurably faster. The same probe during heavier load
(load average 13 to 18, 04:27) gave wider intervals that crossed the
bar: `rolling` 1.0307 (1.0119 to 1.0499) and `membership` 1.0257
(1.0092 to 1.0424). On a busy machine a fixed number of rounds may not
place these decoys below the bar, and INCONCLUSIVE is then the right
answer. Neither has been measured on Linux.

### The other candidates

Run at 05:03 to 05:05 for `rolling` (`WIDTH = 3,000`, load 6.4 to 7.6) and
at 03:58 to 04:05 for `membership` (load 5 to 7). Peak RSS here is the
largest `ru_maxrss` of any candidate run over the largest of any
baseline run in the same rounds, from `wait4`.

| Candidate | Rounds | `rolling` reported | `rolling` wall | `rolling` peak RSS | `membership` reported | `membership` wall | `membership` peak RSS |
|---|---|---|---|---|---|---|---|
| `fix` | 30 | 63.9x (63.1 to 64.8) | 3.34x (3.31 to 3.36) | 1.00x | 495x (488 to 502) | 3.22x (3.16 to 3.28) | 1.05x |
| `noop-refactor` | 20 | 1.002 (0.992 to 1.011) | 0.998 (0.990 to 1.006) | 1.02x | 1.009 (0.992 to 1.027) | 1.008 (0.993 to 1.023) | 1.05x |
| `memory-cache` | 20 | 2.32x (2.19 to 2.45) | 1.43x (1.35 to 1.51) | 3.18x | 26.7x (24.9 to 28.6) | 2.05x (1.98 to 2.13) | 3.62x |
| `untested-branch` | 20 | 65.0x (63.2 to 66.8) | 3.28x (3.18 to 3.38) | 1.01x | 472x (450 to 494) | 3.16x (3.10 to 3.22) | 0.99x |
| `wrong-output` | 20 | 67.2x (64.8 to 69.6) | 3.27x (3.17 to 3.37) | 1.02x | 422x (394 to 452) | 3.19x (3.13 to 3.24) | 1.00x |
| `bench-edit` | 20 | 4.04x (3.92 to 4.17) | 2.20x (2.15 to 2.25) | 0.69x | 4.03x (3.98 to 4.09) | 2.17x (2.13 to 2.20) | 0.90x |

`colsum`, 30 rounds of the 4096 x 4096 kernel at 03:58 (load 6.1): the
fix is 15.7x faster (15.0 to 16.4) on the reported time.

### Peak memory

Ten runs of each tree, interleaved baseline, fix, `memory-cache`, at
04:35 (load about 10), in MiB of `ru_maxrss`:

| Tree | `rolling` min, median, max | `membership` min, median, max |
|---|---|---|
| baseline | 72.7, 74.6, 77.5 | 51.9, 52.9, 55.9 |
| `fix` | 75.7, 79.0, 82.3 | 51.4, 53.7, 55.2 |
| `memory-cache` | 230.0, 234.6, 237.6 | 178.3, 180.5, 182.3 |

One tree's peak RSS varied by up to 9% across its runs, and a single
fix-over-baseline pair ranged from 1.009 to 1.133 for `rolling` (0.973
to 1.039 for `membership`), so a single-run ratio can't be held to the
10% allowance. Medians: fix over baseline 1.059 (`rolling`) and 1.015
(`membership`). The smallest `memory-cache` run is 2.97x (`rolling`)
and 3.19x (`membership`) the largest baseline run. The meta suite
compares medians of three runs, with the fix bound at 1.25x and the
`memory-cache` floor at 2.5x.

`rolling`'s fix allocates one extra transient array per series: the
800 KB prefix array, about 1% of the baseline's peak. Five in-process
runs each, keeping the results but writing no output, gave overlapping
ranges (baseline 69.9 to 74.8 MiB, fix 69.6 to 79.0 MiB), so the 6%
median gap above is not explained by that array alone. It could be allocator behaviour or noise
between runs; this hasn't been settled. For T2.8 it means the fix's
peak-memory guardrail may be judged near its 10% allowance on this
machine.
- cProfile on the baselines puts 79% of `rolling`'s profiled time in
  `rolling_sum` and 69% of `membership`'s in `held_orders`, the rest
  being interpreter start-up and imports. Line-level attribution with
  py-spy is not checked here: py-spy is not installed on this machine
  and T2.10 owns that test.
- Under coverage.py 7.10.7 (run once with `uv run --with`, not a
  project dependency), each example's suite runs every line of the
  changed module in the fix and in every decoy except
  `untested-branch`, whose fast path never runs: `rolling.py:41` and
  `orders.py:53-54` in those candidates. The benchmark doesn't reach
  them either, since it uses a width of 3,000 and a 2,030-entry
  blocklist.
