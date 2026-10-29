# Node and TypeScript

Read this when the code to speed up runs on Node: JavaScript, or
TypeScript run through `tsx`, `ts-node`, `esbuild-register`, Node's own
type stripping, or compiled first. It covers how to launch, benchmark
and profile such code so the numbers mean something, and what usually
makes Node code slow, each with the cheap test that settles it.

## Launch it in a form perfproof can follow

The benchmark command decides whether the `runtime` check can name the
Node that runs it. These are followed (the Node's path, version and
hash are recorded):

- `node bench.js`, `node --import tsx bench.ts`,
  `node --require esbuild-register bench.ts`, `node bench.ts` (Node
  with type stripping)
- `node_modules/.bin/tsx bench.ts`, `npx --no-install tsx bench.ts`,
  `npm exec --no -- tsx bench.ts`, when the tool is in
  `node_modules/.bin`

These are not, so the check is "not available" and a change cannot be
kept: `npm run <script>`, `pnpm`, `yarn`, `bun`, `deno`, a shell script,
and `npx tsx` for a tool that is not installed (it would download it;
perfproof stops). If the project's own script is `npm run bench`, read
its `package.json` and launch what it launches.

Use one launch form for both versions. A loader changes what runs:
`tsx` wraps functions in `__name`, so code under it is slower in ways
the same code compiled and run by `node` is not. `npx` starts an extra
Node (npm) before yours, which adds to every start. Neither matters for a `reported` number, which
excludes start-up; both matter for `wall`.

If the code must be built first (`tsc`, `esbuild`, a bundler), put the
build in the spec's `setup` so each version builds its own, and run
the built file. Never point at a `dist/` from your own tree.

## Keep V8's warm-up out of the measured value

Every execution is a fresh process, so V8 starts cold each time and
a discarded `warmup` execution warms nothing. Warm up inside the
process and report only the steady window, with the `reported` metric
(the full rule and its measurements are in `docs/method.md`, "Warm-up
(Node)"):

```js
// .perfproof/harness/bench.mjs
import { performance } from 'node:perf_hooks'
import { work } from '../../src/the-code-under-test.js'

const WARM = 44000, MEASURE = 10000   // fixed; never "until stable"
let sink = 0
for (let i = 0; i < WARM; i++) sink += await work()
const start = performance.now()
for (let i = 0; i < MEASURE; i++) sink += await work()
const seconds = (performance.now() - start) / 1000 / MEASURE
console.error('checksum', sink)
process.stdout.write(`PERFPROOF {"value": ${seconds.toPrecision(12)}}\n`)
```

- `work()` is one real unit of use (a request through the app object,
  one parse of a real file, one render), with inputs made once
  outside the loop and the same every time. Drop `await` when `work`
  is synchronous.
- Choose `WARM` and `MEASURE` on **both** trees, the baseline and the
  candidate, each from its own recording, by the rule in
  `docs/method.md` ("Warm-up (Node)"). A candidate that settles later
  than the baseline is the case at risk, and counts chosen on the
  baseline alone gave a false KEEP of 83.8% (a baseline settling 2.5
  times later) and 89.8% (3 times) of no change, and a false REJECT of
  a true 6% gain 84.3% (a candidate 2.5 times later), in the model
  (`studies/node-warmup/out/ab-model.txt`). All constants below are
  provisional: chosen by synthetic models, not calibrated on real
  data, and real quiet-machine data does not exist yet.
  - Record each tree: 12 fresh processes, each printing the time per
    batch of 500 iterations for R batches, R = 320 to start (160,000
    iterations; the recording must be about 10 times the settle point
    or longer).
  - `W(s)` is the mean, over the processes, of the median of the 20
    batches that start at batch `s`.
  - The **tail windows** are `W(R - 20)`, `W(R - 40)` and `W(R - 60)`.
    Their mean is the reference `ref`.
  - The tolerance is `tol = min(max(0.02, 3 se), 0.04)`. `se` is a
    robust relative standard error of a tail window value, taken from
    the same 12 processes: divide each process's three tail window
    values by their own mean, take the absolute deviations of those 36
    ratios from their median over the processes in the same window,
    take the median of those deviations, multiply it by 1.4826 and
    divide by the square root of 12. (Dividing by the process's own
    mean keeps a process that is simply slower from inflating it.) It
    is 2% on a quiet machine, grows with the noise of the batches, and
    stops at 4%, so a few huge batches cannot widen it.
  - The tail windows **agree** when the largest is at most
    `(1 + tol)` times the smallest.
  - The tree's `N` is the smallest `s` for which `W(s)`, `W(s + 20)`
    and `W(s + 40)` each satisfy `|W / ref - 1| <= tol`; in iterations,
    `500 s`. Nothing else is checked: not the windows between `s + 40`
    and the measured window, and not whether a window is below `ref`.
  - When the tail windows do not agree, record again with fresh
    processes at twice the length (640, then 1,280 batches, 640,000
    iterations at most). If the longest recording still does not
    agree, that tree's warm-up is **unknown**.
  - `WARM` is 8 times the larger of the two trees' `N`, in iterations,
    the same in both versions; `MEASURE` is the same for both, at
    least 10,000 (20 batches).
  - **Unknown is a note, not a gate.** If either tree's warm-up is
    unknown, `WARM` is 160,000 iterations (or 8 times the other tree's
    `N` if that is larger), and the comparison proceeds normally:
    perfproof's verdict stands, a KEEP included. Nothing fails over
    this, and an execution is never failed for it. The skill reports,
    as a note, that the warm-up was not pinned down and that a long
    warm-up was used, and says what would pin it (a longer recording or
    a quieter machine). A noisy tail widens `tol` up to 4%; past that
    the tail windows can disagree at every length and the tree is
    unknown, which is common above noise 0.5 ("Why the tolerance is
    robust and capped" in `docs/method.md`). In the model 51% of pairs
    are unknown at noise 1.0, 5% at 0.5 and none at 0.1. Cost: on a busy
    machine more pairs take the long warm-up, so their executions take
    about 3.6 s each (435 s for 60 rounds) instead of about 1.3 s, and
    comparisons take longer. Measured with unknown pairs allowed to KEEP
    (`out/unknown-keep-w160000.txt`, seed 20261051, 2,000 comparisons
    per row, sharp and exponential grid, clean and with outliers q 2%
    x10 and x30, margin 8): all pairs combined, the worst row's CP95
    upper bound is at most 1.74% at noise 0.1 to 1.0, and 2.09% at 1.5
    and 1.74% at 2.0, as measured and not tuned. Among the pairs that
    ended unknown, pooled over the grid, it is at most 1.34% at every
    noise run. 640,000 was run on the same rows and seed
    (`out/unknown-keep-w640000.txt`, at most 1.68% up to noise 1.0);
    160,000 is used because it meets the same bar at a quarter of the
    cost (13.4 s per execution).
  - What the outlier fix means: a loaded host's spikes no longer
    inflate `tol` and collapse `N`; the price is that on a noisy tail
    the windows often cannot agree within 4%, and the tree is unknown.
    The long warm-up covers that case (the bullet above).
  - The margin, 8, is the closest of the margins run (2, 3, 4, 5, 6,
    8), not one the rule picked: the rule, fixed before the runs, was
    the smallest margin with a CP95 upper bound of at most 2.5% on the
    sharp-drop pairs and exponential tails at every noise from 0.1 to
    2.0, and none qualified (those margin runs used the previous,
    mean-based tolerance). Margin 8 was then run with the robust
    estimator, and passed its pre-registered rule at noise 0.1 to 1.0
    (1.80% at most, with and without outliers). A larger margin was not
    run.
  - **What it covers.** Up to noise 1.0 (the committed curve and
    quieter): sharp-drop pairs, and exponential tails on a grid (a of
    0.5 and 0.75; tau of 2,000, 5,000, 10,000, 20,000 and 40,000), with
    and without outliers in 0.5% to 2% of batches at 10 or 30 times
    the usual, and a finer grid, also clean at 0.1 to 1.0
    (`docs/method.md`, "What the rule covers"). Nothing is claimed
    above noise 1.0 with this estimator.
  - **What it does not cover.** A hump or a drift that changes the
    steady value by more than the bar is not detected: the rule looks
    at three windows after `N` and the tail, and which later shape the
    measured window catches depends on where it falls. A second plateau
    of +2% to +6% and a drift of +2% to +4% gave a false KEEP of a true
    ratio of 1.03 of 0.6% to 38% at margin 8 and noise 0.25 or 1.0, and
    a +10% hump up to 87% (`docs/method.md`, "Stated limits", has the
    table). Also not covered: noise above 1.0, exponential amplitudes
    below 0.5, outliers that cluster in time, and a cap other than 4%. The golden set (T1.12) runs
    real `perfproof compare` with injected speedups, Node included, end
    to end; it does not scan warm-up shapes, so it does not bound
    these, and it has not run yet. Data from real hardware on warm-up
    shapes does not exist. The shape figures above come from the
    previous, mean-based tolerance and were not re-run with this one.
- Cost (these times are for the one workload measured, and the
  factors are provisional): choosing takes 12 processes times the
  recording time per tree, 3.4 s for 320 batches, 6.7 s for 640 and
  13.2 s for 1,280 per process, so about 41 s per tree when the first
  recording is enough, 121 s when it takes two, up to 280 s if all
  three are needed; for both trees on average in the model, 334 s at
  the committed noise, 82 s at noise 0.1 and 156 s at 0.5.
  An execution then takes about `WARM` x the
  time per iteration while warming up (more than the steady one) plus
  `MEASURE` x the steady one, plus start-up: 1.27 s at `WARM` 44,000,
  2.28 s and 3.30 s on a tree that settles 2 and 3 times later (`WARM`
  88,000 and 132,000), 3.6 s on the unknown path (`WARM` 160,000,
  435 s for 60 rounds; 13.4 s and 1,606 s at 640,000),
  and, on the slowest claimed exponential tails at margin 8 over noise
  0.1 to 1.0, up to 18.75 s at the median `WARM` and 24.77 s at its
  95th percentile (`out/exp-grid-robust.txt`). The longest execution
  is a slow tree's where one is present, the unknown path's otherwise.
  Set the spec's `timeout_seconds` to at least 3 times the
  longest execution you measured on either tree (74.3 s for 24.77 s), and
  `budget.max_seconds` to at least 1.2 x 2 x (`warmup` + `max_rounds`)
  x that time (3,626 s for 24.77 s with one `warmup` and 60 rounds). A
  timeout is a failed execution: in the baseline the comparison stops
  with no verdict; in the candidate, once the baseline reruns fine, it
  is rejected on correctness. Never a keep.
- Print the mean in seconds at full precision: `toPrecision(12)`, as
  above. `toFixed(9)` prints `0.000000000` for a fast workload (a
  failed execution) and keeps only four digits at 2 µs; `JSON.stringify`
  prints 1.0 as `1`, which perfproof stops on as too coarse.
- `work()` returns a number the harness adds to a checksum (printed to
  stderr), so V8 cannot drop the work.
- Write the harness in `.perfproof/harness/` and list it in `harness`.
  TypeScript is fine there under the same launch form as the project.

## Profile it

```
perfproof profile <name> --json --profiler-command \
  '["<python>","-m","perfproof.profilers.node","run",
    "--output","{output}","--","{command}"]'
```

`<python>` is the Python of the environment the `perfproof` command runs
in, so the module is the one shipped with that CLI. Read it from the
command's first line: `head -1 "$(command -v perfproof)"` prints
`#!/path/to/python` for a `uv tool`, `pipx` or virtualenv install; use
that path. If perfproof is run through `uvx --from <source> perfproof`,
use `"uvx","--from","<the same source>","python","-m",...` instead.
(Write the command as one JSON array.) No other source works: the
package is not fetched from anywhere by this recipe.
This samples every Node process the command starts, including the one
behind `npx` or `tsx`, with V8's profiler, and maps positions back to
the TypeScript. Read `result.hotspots[]` as for any profile, plus:

- **Shares are of time the process was running.** The event loop
  waiting (`(idle)`) is left out; compare CPU with wall time in the
  result to see waiting. CPU a little above wall time is normal for
  Node (V8 compiles and collects on helper threads) and shows no
  waiting. Add `"--idle"` before `"--output"` to keep `(idle)`.
- **Worker threads that are terminated** (`worker.terminate()`, some
  test-runner pools) write no profile, and nothing says so; the share
  of such work is missing.
- **The root frame names the process**, `process <pid>:"<argv>"`.
  With `npx`, one process is npm itself and a short run is mostly that;
  profile `node ...` directly, or lengthen the run.
- **A frame's line** is where the function starts, except for the
  function that was running, which gets the line the samples landed on
  where V8 knows it. Under `tsx` all of a file is one generated line,
  so only function starts are known there: a hot loop inside a long
  function shows as the function. To get the loop's line, run the
  profile through a launch form that keeps lines (`ts-node`,
  `esbuild-register`, Node's own stripping, or a build with source
  maps). A frame with a file and no line has no line to give.
- **Not the project's:** `node:internal/...` and anything under
  `node_modules` are marked as library code. `__name` at the top of a
  file is `tsx`'s own wrapper, not the project's code. `(program)` is
  native code outside JavaScript; `(garbage collector)` is the
  collector.
- **Start-up is in a short profile.** `compileSourceTextModule`,
  `compileForInternalLoader`, `readFileUtf8` and the loader's own files
  are Node loading and compiling code. If they lead, the run was too
  short or start-up is the work (see the last row below).

The profile run is separate from the timed rounds, so profiling does
not touch a verdict.

## What usually goes wrong, and the cheap test for each

Each row is a hypothesis. Run its test before writing a change; drop
the idea when the test refutes it, and say why as a mechanism or a
count, never as a timing.

| Suspect | How it shows | Cheap test |
|---|---|---|
| **Event loop blocked** by one long synchronous task, so everything queued behind it waits | One function with large self time on the request path; latency p99 far above p50; CPU close to wall | In the harness, `perf_hooks.monitorEventLoopDelay({resolution: 1})`, enable it, print `max` and `percentile(99)` (nanoseconds) at the end. A `max` near the length of one task confirms a block (a 40 ms busy loop gave a max of 41.8 ms and a p99 of 41.8 ms in
`tests/fixtures/node/warmup/event-loop-and-sync-io.out`, from the script beside it, one run: its loop ran only five blocks, so the p99 saw them). With many short ticks around one long block the p99 can stay low while `max` shows it, so read `max` first (that follows from how the delay is sampled; it was not tested here). A `max` near the tick length refutes it |
| **Synchronous I/O** (`fs.*Sync`, `execSync`, `spawnSync`, sync crypto or compression) on the hot path | `readFileSync`, `statSync`, `openSync` frames with real self time; wall far above CPU if the disk is slow | `node --trace-sync-io <entry>` prints a stack for each sync call made after start-up, with `file:line`. Count the calls per unit of work. No stack in the hot path refutes it |
| **JSON.parse / stringify** of large or repeated values | Built-in `parse` and `stringify` frames (no file); a share that grows with payload size | Time `JSON.parse(text)` and `JSON.stringify(value)` on the real payload alone, in a loop with the same warm-up, and divide by the stage's time: that share is the cap. Check whether the same text is parsed or the same value serialised more than once per unit (count with a counter) |
| **Regex catastrophe** (nested or overlapping quantifiers, backtracking) | Self time on the line that makes the matching call, not under a regex frame; a few inputs far slower than the rest | Time the regex on inputs of length n, n+2, n+4 with a non-matching tail. Time that multiplies at each step (about 4x per 2 characters for `/^(a+)+$/`) is exponential; roughly doubling with n is quadratic; linear is fine. Test with the longest real input, not the typical one |
| **Allocation and GC pressure** from many short-lived objects, arrays or strings | `(garbage collector)` with a large share; time spread over many small frames; memory sawtooth | `node --trace-gc <entry>` and count the `Scavenge` and `Mark-Compact` lines per unit of work and add up the pauses; or read `(garbage collector)` in the profile. `node --heap-prof` lists the allocation sites (`.heapprofile`, top self size). A change that removes allocations should cut the count in `--trace-gc`, which is a fact, whatever the timing does |
| **Closures and objects built in a hot loop** (`arr.map(x => ...)` with a new inner function per call, a fresh options object per call, spread copies `{...o}`, an array rebuilt per item) | Anonymous `(anonymous)` frames from the same file, `(garbage collector)` above, many allocations per call | Hoist the closure or object out of the loop in a scratch copy and time both on one input in the harness; or count allocations per call with `--heap-prof`. If the hoisted version allocates no more than before and the inner share is small, drop it |
| **Deoptimisation** (a function V8 keeps optimising and abandoning, from changing object shapes or types) | A hot function that never gets faster across the warm-up curve; self time stays high in a function that looks simple | `node --trace-deopt <entry>` and look for the same function name repeated after the warm-up, with reasons such as `wrong map` (a few `Insufficient type feedback` lines at start-up are normal). Few lines for it refute it |
| **Buffer and string conversions**: bytes turned into strings and back (`toString()`, `Buffer.from(str)`, `TextEncoder`/`TextDecoder`), `+=` building large strings, `Buffer.concat` in a loop | `encode`, `utf8Slice`, `toString`, `TextDecoder` frames from `node:internal`; a share that grows with data size | Count the conversions per unit of work with a counter at the call sites; if a value is decoded, handled and encoded again unchanged, the conversions are removable. Keep it as a `Buffer` through the stage in a scratch copy and compare outputs byte for byte |
| **Many small `await`s or promises** where one batch would do (a promise per item, `await` in a loop that could run together, `Promise.all` over thousands of items) | Time in promise and microtask internals; many tiny frames; CPU well above the useful work | Count the awaits per unit of work. If the work per await is a few microseconds, the awaits are the cost |
| **Objects built per call that could be built once or skipped** (a costly constructor, formatter or parser called inside the hot path with the same arguments) | `node:internal/...` frames with a large library share under one of the project's own functions | Count constructions per unit of work, and for each ask whether the result is the same every time (build it once) or is used at all (skip it) |
| **Start-up and bundling cost**: loading and compiling many modules, a loader's transform, a large bundle, work done at import time | A short process dominated by `compileSourceTextModule`, `wrapSafe`, `readFileUtf8`; wall far above the in-process work | Time `node -e 0` against the real entry with nothing run; print `performance.now()` at the first line of the entry and when it is ready; `process.moduleLoadList.length` before and after start-up. If start-up is the work the user cares about (a CLI, a function that cold-starts), the benchmark is `wall` over the whole command and the playbook is: load less at start (lazy `import()`), bundle, or cache compiled code. If not, a `reported` benchmark already excludes it |

## Proving a change

- The oracle is the project's tests (`vitest`, `jest`, `node --test`)
  plus the output the harness writes; give the test command only the
  files for the code you changed when the whole suite is slow.
- A KEEP needs the coverage check, which for Node means declaring
  `correctness.coverage_command` (see Coverage below). Without it the
  change is measured but not kept.
- A change to a dependency (`package.json`, the lockfile) is a runtime
  change: `node_modules` differs between the versions, and the check
  that names the runtime does not see the loader's or the library's
  version beyond the lockfile hash. Never set `allow_runtime_change`
  without asking.
- Compare in one launch form on one machine; Node's version and the
  JIT's behaviour change between releases and hosts.

## Coverage

A Node change is kept only if every changed line ran under the checks.
Declare a coverage command that runs the project's test command under
`c8` and leaves lcov at exactly `$PERFPROOF_COVERAGE_REPORT`. `c8`
writes `lcov.info` into a report directory and has no flag for a file
path, so a tiny argv-only wrapper moves it. Save it as
`.perfproof/harness/coverage.mjs` and list it in `harness`:

```js
import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, renameSync, rmSync } from 'node:fs'
import { dirname, join } from 'node:path'

const out = process.env.PERFPROOF_COVERAGE_REPORT
const c8 = join(process.cwd(), 'node_modules', '.bin', 'c8')
if (!out || !existsSync(c8)) {
  console.error('need PERFPROOF_COVERAGE_REPORT and node_modules/.bin/c8')
  process.exit(2)
}
const dir = mkdtempSync(join(dirname(out), 'c8-'))
const run = spawnSync(
  c8,
  ['--reporter=lcovonly', `--reports-dir=${dir}`,
    `--temp-directory=${join(dir, 'tmp')}`, '--', ...process.argv.slice(2)],
  { stdio: 'inherit' },
)
if (existsSync(join(dir, 'lcov.info'))) {
  renameSync(join(dir, 'lcov.info'), out)
}
rmSync(dir, { recursive: true, force: true })
process.exit(run.status ?? 1)
```

The spec then has `"coverage_command": ["node",
".perfproof/harness/coverage.mjs", <the test command>]` (for example
`node_modules/.bin/borp`, or `node --test`) and a
`coverage_timeout_seconds`.

- `c8` must be in `node_modules/.bin` in each version. If the project
  lacks it, add `["npm", "install", "--no-save", "--ignore-scripts",
  "c8"]` to `setup` (no lockfile change). The wrapper exits 2 rather
  than download it.
- TypeScript run through `tsx` (`node --import tsx --test x.ts`) or
  Node's own type stripping (`node --test x.ts`) reports the source's
  line numbers; both were checked with an enum above the function, so
  compiled and source lines differ. Code compiled without a source map
  would report lines of the output.
- Child processes the tests start are covered too (`c8` sets
  `NODE_V8_COVERAGE`). c8 counts a line as run when the range around
  it ran, so a one-line function nobody calls can still show a hit; a
  changed line on a line of its own is judged exactly.
- Never time a run under `c8`: it is for the check only.

## When to stop

Stop when the profile's biggest project frame is a share too small to
clear the bar even if removed (the cap), when the hypothesis list is
refuted by its cheap tests, or when what remains is start-up and the
user did not ask about start-up.
