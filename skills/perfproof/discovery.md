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

## Tests as a coverage probe

`perfproof find` profiles each part's tests (after the user confirms
the test command) and lists as `leads` the project code they reach that
your workloads do not. A lead is a place to look, never a candidate or a
claim: tests use odd inputs and heavy set-up. To act on one, read the
test its `via_test` names, find the entry point a user would use that
reaches it, and write a workload through that with realistic input; only
that workload's ranking can pick a candidate. Tests that only start the
program as a child process profile the test code, not the program.

## Workload shape

- **One kind of operation per workload.** A script that converts,
  resizes and saves in one loop reports one blended profile, and the
  operation that matters can be a sliver of it or never run at all.
  Write one small workload per operation (decode, transform, encode,
  serve a request) and let `profile show --rank` order them.
- **One workload's knob is a single item's size.** One long document,
  one large image, one multi-MB body, run with `--sizes` (three sizes
  at least). Repeating many small items hides work that grows faster
  than linear in one item.
- **One modifies and re-emits data**, where the project does that
  (edit then save, rewrite then serialise, transform then write out):
  read-only workloads never reach the write side.

## Build failures

A build that fails is not yet "can't be built or measured". Read at
least the last 30 lines of its log (not 5: the cause is often above the
final error). Then try the obvious fix once: a missing optional
dependency the log names, or on macOS the SDK path
([native.md](native.md)). Only after that say the change can't be built
here, with the log line that shows why.

## Whole app, several layers

A bare or vague ask ("speed up the app") is the whole project. The
answer is one ranked map, not one hotspot.

**Map the stack first.** List each part with its own build file or entry
point (`frontend/`, `backend/`, a native extension, kernels, a CLI, a
service) and how they connect: a Python call into a C++ extension, a UI
calling an API, a CLI calling a library. `perfproof init` lists
`targets[]`, and on a project with a Python package over C++ it gave
two (`python_cpu`, `native`). On a React front end beside an Express
server it gave one (`web_service`) and did not list the front end, so
read the tree too. Choose workloads through the entry points
a user really uses, so each one runs through the layers (the script
that builds an index, not a test of the C++ class). In the plan,
say which parts each workload reaches and which none does. A part that
cannot run here (a front-end build that fails on this machine's
runtime, a back end that only forwards calls to a remote service) is
named as not reached with the reason, never skipped silently.

**What one profile sees.** Measured on macOS (`perfproof profile`, the
default Python profiler against the `native` recorder in
[native.md](native.md), the same command each time):

- Python calling a compiled extension: the default Python profiler
  names the extension call as one leaf frame with no file (`<built-in>`
  in the run: 93% self share for one call into a C++ index builder). It
  sees nothing under it. The `native` recorder, started on the same
  `python` command, names the extension's C++ frames by file and line
  from the debug info the sampler reads (a build with `-g` keeps it),
  through several levels. `--own-module <image name>` (the binary's
  file name, such as the `.so`) is only needed for frames printed
  without a source line. The recorder shows the
  interpreter only as CPython's own C frames, so the Python function
  names are gone. Run both, each under its own benchmark name
  (`<name>` and `<name>_native`): the first ranks the Python side, the
  second the native side. Don't rank them together; the same time is
  counted at two depths.
- A script that starts another process (a subprocess, a server): both
  profilers followed only the parent. The child's work showed as the
  parent waiting (`waitpid` or `wait4`, 95% to 99% of the samples) and
  nothing of what the child ran. Profile the child's own entry point as
  its own workload (the server driven by a client script, the library
  by its own driver).
- Node and TypeScript under the Node profiler name the project's own
  TypeScript functions with file and line, in a store or an Express
  app alike. A server and the script that sends it requests, in one
  process, are profiled together in one run.

Where a layer is invisible, say so in the plan ("the profile shows the
call into the extension, not what is inside it") and profile that layer
with its own workload. A cap taken from a frame that holds a whole
layer (the entry call of a native library, at 96%) is the layer's cap,
not a function's; read the frames below it for a candidate.

**Rank across workloads, name the layer.** `profile show --rank` on all
the workloads gives one list. In the report, each row names its part in
plain words ("client state updates", "server middleware", "C++ index
build"), the share and the cap. Dedupe a frame that is the same work at
two depths.

**Try the top, name the rest.** Prove the top candidates for the depth
(`medium`: 2 to 3, the best proved against its baseline). The rest stay
as named next candidates with their caps. The parts no workload reached
are listed, with the first workload that would reach each.

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
