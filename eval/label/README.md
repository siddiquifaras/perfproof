# Independent labeller

The ground truth for the evaluation in
[docs/evaluation.md](../../docs/evaluation.md), "Ground truth,
independent of perfproof". Each task gets a correctness label and a
performance label from a procedure that shares no code with perfproof:
nothing here imports `src/perfproof/`, and the statistics are
pyperf's plus a bootstrap written here.

The decision rule below was committed before any held-out task was
labelled. It is not changed after a held-out result has been seen.

## Decision rule

### Terms

- **Arm A** is the task tree (the task commit). **Arm B** is the
  reference tree (the reference change). In the A/A mode both arms
  are the task tree, in the same directory.
- **bar** is `1 + 0.03` in float64, the `min_effect` of
  docs/method.md. Its mirror for slowdowns is `1 / bar`.
- **Speedup** of B over A: a value above 1 means B is faster.

### Measurement on one machine

1. **Quiet check.** Before the first pair, and again whenever a run
   resumes, the 1-minute load average must be at most `0.25 x` the
   logical CPU count. The labeller polls every 30 s for up to 15
   minutes, then refuses to start and writes no result. The load
   average is recorded before the warm-up, after every pair, and after
   the last pair. Load during the run is recorded, not used to decide.
2. **Warm-up.** Each arm's workload runs twice, A B A B, and these
   runs are discarded.
3. **Pairs.** At least 200 pairs. In pair `i` both arms run
   back to back, in an order set by a fair coin:
   `random.Random(seed).random() < 0.5` puts A first, one draw per
   pair in pair order. The seed is a 64-bit integer drawn with
   `secrets.randbits(64)` when the run starts, unless one is given,
   and is recorded. A resumed run reuses the recorded seed, so the
   order of every pair is fixed by the seed alone.
4. **Metric.** Wall time of the workload's process in seconds,
   `time.perf_counter_ns()` around the child, the clock pyperf uses.
   The command is run from its argv list without a shell, with its
   output discarded. Child CPU time (`getrusage`) is recorded too.
5. **Instruction counts.** On Linux each execution runs under
   `perf stat -x, -e instructions:u`, when a probe shows the counter
   works; otherwise the result records why not (perf missing, counter
   not supported, a Docker workload whose processes are not perf's
   children). They are analysed with the bootstrap below and reported,
   never used for the label: they measure a different quantity from
   the workload's wall time, and macOS has none, so a rule built on
   them could not be applied on both machines.
6. **Validity.** A machine's result is valid only when at least 200
   pairs completed, every execution of both arms exited 0, the quiet
   check passed, and the run is not a pilot. An invalid result gets no
   label and is re-run, never turned into `unstable`.

### Analysis of one machine's result

Let `a_i` and `b_i` be the wall times of pair `i`, `r_i = ln(a_i /
b_i)` in float64, `n` the number of pairs and `S = exp(mean(r))` the
geometric-mean speedup.

1. **Bootstrap (written here).** A moving-block bootstrap of the mean
   of `r`, for drift and autocorrelation between neighbouring pairs:
   block length `l = ceil(n ** (1/3))` (6 at `n = 200`), `B = 10000`
   resamples, each made of `ceil(n / l)` blocks whose starts are
   uniform on `0 .. n - l`, concatenated and cut to `n`. The interval
   `[L, U]` is `exp` of the 2.5% and 97.5% quantiles of the resampled
   means (NumPy's default linear quantile). Its generator is
   `numpy.random.default_rng(seed)` with the run's seed. When every
   ratio `a_i / b_i` is the same float, `L = U = S` = that ratio.
   The bootstrap's category is the first that matches:
   - `improvement` if `L > bar`;
   - `regression` if `U < 1 / bar`;
   - `below-bar` if `U <= bar`;
   - `indeterminate` otherwise (`L <= bar < U`).
2. **pyperf.** The A and B times go into one `pyperf.Benchmark`
   each, saved in pyperf's JSON format beside the result so
   `python -m pyperf compare_to` and `check` can be re-run on them.
   `m` is the ratio of their means, `mean(a) / mean(b)` from
   `Benchmark.mean()`, and `significant` is pyperf's two-sample
   Student t-test at 95%, the function `compare_to` uses
   (`pyperf._utils.is_significant`, pyperf 2.10.0 pinned in
   `requirements.txt`). Its category is the first that matches:
   - `improvement` if `significant` and `m > bar`;
   - `regression` if `significant` and `m < 1 / bar`;
   - `below-bar` if `m <= bar`;
   - `indeterminate` otherwise (`m > bar`, not significant).
3. **Reproduction within the run.** The pairs are split into the
   first `floor(n / 2)` and the rest, in the order they ran, and each
   half's `S_h = exp(mean(r))` is computed. An `improvement`
   reproduces when both `S_h > bar`, a `regression` when both
   `S_h < 1 / bar`, and `below-bar` when both `S_h <= bar`.
4. **The machine's label.** When both categories are the same
   `improvement`, `below-bar` or `regression` and it reproduces, that
   is the machine's label. Otherwise the machine's label is `unstable`,
   with the first reason that applies: `indeterminate` (either
   analysis), `analyses-disagree`, `not-reproduced`.

The comparisons with `bar` use the float64 bounds as computed, with
no tolerance. As in docs/method.md, an improvement is strict (`L >
bar`): a change exactly at the bar is not an improvement.

### Two machines: `combine`

The final performance label needs two valid results for the same
task, mode, task commit and reference change, from two machines with
different names, neither a pilot. `combine` refuses anything else and
gives no label.

- Both machines' labels are the same `improvement`, `below-bar` or
  `regression`: that is the final label.
- Otherwise the final label is `unstable`, with both machines' labels
  and reasons. That covers machines that disagree and an effect that
  didn't reproduce on either one. Unstable tasks are excluded from
  the scores and reported apart with their count
  (docs/evaluation.md).

### Correctness

- **The full suite** is the repository's test suite, from the recipe
  for the task in `suites.json` (command, how to read its results,
  where it came from). It runs once on each tree after the pairs, so
  it cannot disturb them. Recorded per tree: exit status, counts of
  passed, failed, errored and skipped tests, and the ids of failed and
  errored tests.
- **The task's own check** is the oracle in `eval/mine/checks.json`,
  run on both trees.
- **The machine's correctness label:**
  - `fail` if the reference tree has a failed or errored test id that
    is not failing on the task tree, or the oracle exits non-zero on
    the reference tree (the oracle is read whether or not the task has
    a suite recipe);
  - `pass` if the reference tree's suite ran and its results were
    read (a run in which no test passed counts as unread), no test id is newly failing, and the oracle exits 0 there;
  - `unknown` otherwise: no recipe, a timeout, results that could not
    be read, or a non-zero exit with no failing id to explain it.
  Test ids failing on both trees are recorded and do not fail the
  change.
- **Two machines:** `fail` if either machine says `fail`, `pass` if
  both say `pass`, else `unknown`.

### A/A check

The A/A mode runs the task tree against itself through the same
procedure and rule. The check passes when no A/A label is
`improvement`. A `regression` from an A/A run is reported as a
finding against the procedure as well.

### Held-out tasks

The labeller refuses a held-out task unless it is given
`--held-out`, the run is not a pilot, and every file of the rule is
committed and unchanged in the working tree: this README, `decide.py`,
`boot.py`, `analyse.py`, `measure.py`, `pyperf_stats.py`, `suites.py`,
`suites.json`, `label.py`, `trees.py`, `machine.py` and
`../mine/checks.json` (the oracles and build recipes), `RULE_FILES` in
`label.py`. The rule a held-out
label was made under is then the one recorded in git. The commit and
every file's SHA-256 are written into every result.

## What the bootstrap was measured to do

Simulated with fixed seeds in `test_label_boot.py` (marker
`calibration`; `uv run pytest eval/label -m calibration`), `n = 200`
pairs, 2000 resamples per interval:

| Case | Replications | Measured | Nominal |
|---|---:|---:|---:|
| Coverage, independent pairs | 1000 | 0.944 (MC SE 0.007) | 0.95 |
| Coverage, AR(1) log ratios, coefficient 0.3 | 1000 | 0.914 (MC SE 0.009) | 0.95 |
| `improvement` from the bootstrap alone, true speedup exactly at the bar | 400 | 0.0375 (MC SE 0.0095) | at most 0.025 |

The interval runs short of 95%, a little with independent pairs and
more under autocorrelation, so on its own it calls a change at the bar
an improvement more often than its nominal 2.5% upper tail. A label
needs more than the bootstrap: pyperf's t-test, both halves, and
since `combine` needs it, a second machine. The second machine does
almost all of the work of lowering false improvements, as the table
below shows: the single-machine rule (bootstrap, t-test and halves)
removes only a few percent of the bootstrap's false improvements, and
requiring a second, independent machine to agree takes the rate from
several percent to well under 2.5%.

### The full two-machine rule, simulated

Measured in a review of this task (not by the tests in this
directory): the whole rule at `n = 200` pairs per machine, the true
speedup exactly at the bar unless the row says 1.0, two machines
drawn independently, `R = 4000` replications per row, each machine
through `boot.interval`, pyperf's `is_significant`, the halves and
`decide.machine_label`, then `decide.combine_performance`. Seeds are
`default_rng(1000 + i)` for row `i` of the probe's cell list, and
each replication's bootstrap seeds are `2 * rep` and `2 * rep + 1`.
Intervals are Clopper-Pearson, two-sided 95%. Probes:
`scratch/audit-pr29-probes/` in the main checkout (not committed).
The skewed rows use the generator as corrected in the probe's later
`fix*.json` runs; the first, uncorrected runs of those rows are not
reported. The probe's noise models are the reviewer's choices, not
measurements of real workloads.

| Row (log-ratio noise sd 0.05 unless stated) | Bootstrap alone | One machine | Two machines | Two machines, 95% CI |
|---|---:|---:|---:|---|
| independent | 116 | 114 | 2 | 0.006% to 0.18% |
| AR(1) 0.3 | 145 | 141 | 5 | 0.04% to 0.29% |
| AR(1) 0.6 | 249 | 236 | 17 | 0.25% to 0.68% |
| AR(1) 0.3, sd 0.20 | 157 | 154 | 3 | 0.02% to 0.22% |
| skewed | 64 | 61 | 2 | 0.006% to 0.18% |
| skewed, AR(1) 0.3 | 83 | 80 | 1 | 0.0006% to 0.14% |
| skewed, AR(1) 0.6 | 184 | 172 | 6 | 0.06% to 0.33% |
| AR(1) 0.9, sd 0.02 (extreme) | 730 | 651 | 103 | 2.1% to 3.1% |
| true speedup 1.0, AR(1) 0.3 | 0 | 0 | 0 | 0% to 0.09% |
| true speedup 1.0, skewed | 0 | 0 | 0 | 0% to 0.09% |

Counts are false `improvement` labels out of 4000. In every row but
the extreme one the two-machine rate is under 0.7%, and its upper
confidence bound is under 2.5%. At lag-1 autocorrelation 0.9 the
point estimate is 2.6% and the interval straddles 2.5%, so for
workloads that drift that slowly the rule is not shown to hold its
level. Two machines are simulated as independent; a shared cause of
bias (the same drift in both trees, a build difference) would not be
independent, and this simulation does not cover it. The AR(1) test first ran with 400 replications and measured
0.8975, below its floor of 0.90; a 1000-replication probe with other
seeds gave 0.928, so the test now uses 1000 replications with fresh
seeds. Its floor was not moved.

## Running it

From the repository root, on a machine with uv:

```bash
L="uv run --with-requirements eval/label/requirements.txt python eval/label/label.py"
$L machine --machine mac-m4            # what a result records
$L quiet                               # the quiet check, once
$L run --machine mac-m4 <task-id> ...  # label tasks (or --tasks-file F)
$L run --machine mac-m4 --aa <task-id> # A/A: the task tree against itself
$L run --machine mac-m4 --pilot --pairs 20 <task-id>  # timing only
$L combine eval/label/results/label/mac-m4/<id>.ab.json \
           eval/label/results/label/hetzner-ccx13/<id>.ab.json \
           -o eval/label/results/final/<id>.ab.json
```

Tests and types (pyperf is not in the project's environment, so the
pyperf tests skip without the requirements file):

```bash
uv run --with-requirements eval/label/requirements.txt pytest eval/label
uv run --with-requirements eval/label/requirements.txt pyright -p eval/label
```

`run` writes, per task, `results/<label|pilot>/<machine>/<id>.<ab|aa>.json`
and pyperf's two JSON files beside it. The result holds the machine
(CPU model, kernel, logical CPUs, memory, whether perf counts and why
not), the rule's provenance, the tree each arm ran (`arm_trees`: the task
tree on both arms in A/A mode), every setup line, each segment's quiet
check and load averages, every pair, both analyses, the correctness
runs and the phase timings. Work in progress lives in
`work/<id>/` (gitignored): the trees, `<tree>.ready.json` markers, and
per run `pairs.jsonl`, `segments.json` and `correctness.json`.

**Resuming.** Run the same command again. A task with a valid result
is skipped, and so is a pilot result. A result that is invalid for
any other reason (setup failed, too few pairs, a non-zero exit, a
quiet-check failure) is renamed to `<id>.<mode>.invalid-<time>.json`,
its pair log and segments are set aside the same way, and the task
runs again from the start. A pair log continues from its last whole pair with its
recorded seed (a line cut off by a crash is dropped), after a fresh
warm-up and quiet check; a log whose header differs (other pairs,
commits or workload) is refused rather than mixed. Trees whose setup
finished are reused, except for recipes with `teardown` lines, whose
services (PostgreSQL) must start again, so those trees are rebuilt.
The correctness runs are kept once done.

**The quiet check** stops the whole list (exit 3) when it refuses, so
nothing runs on a loaded machine; re-run when it is quiet. A pilot
records the check and runs anyway.

### Full-suite recipes

`suites.json` has a recipe for the three pilot tasks only. Every other
task's correctness label is `unknown` until its recipe is written.
Readers: `pytest-junit` (pytest's `--junitxml=eval-label-junit.xml`),
`unittest` (unittest's summary, which Django's runner prints) and
`cargo` (`cargo test --no-fail-fast`).

## Running on the Linux VM

The second machine is a Hetzner CCX13 (x86_64, Ubuntu 24.04). Faras's
approval covers about 20 VM hours, capped at $5. Nothing here creates
or touches a server.

1. On the new VM, as root: copy and run `bootstrap-vm.sh` (git, build
   tools, Docker, perf, uv, CMake, Ninja, Node 24, Rust). It prints
   the tool versions and whether `perf stat` counts.
2. The repository is private, so copy it as a bundle rather than
   cloning: `git bundle create perfproof.bundle HEAD`, `scp` it, and
   `git clone perfproof.bundle perfproof` there. A clone keeps git, so
   the rule's provenance check works. A published-benchmark task also
   needs its dataset rows: copy that benchmark's pages under
   `eval/mine/cache/` (gitignored) too.
3. Start the run in tmux, so a dropped SSH session doesn't stop it:

   ```bash
   ssh root@<vm> tmux new -d -s label \
     "cd perfproof && uv run --with-requirements eval/label/requirements.txt \
      python eval/label/label.py run --machine hetzner-ccx13 \
      --tasks-file <list> > eval/label/work/run.log 2>&1"
   ```

4. Fetch results with `rsync -a root@<vm>:perfproof/eval/label/results/
   eval/label/results/`. If the VM has to be stopped, the same command
   resumes on it later.

Some recipes are written for this Mac and need a Linux variant first,
in `eval/mine/checks.json` (T4.1's file), before they can build on the
VM: `scipy-scipy-pr24913-landed` builds against Apple Accelerate
(`-Dblas=accelerate`), and `scikit-learn-scikit-learn-pr19328` turns
OpenMP off because Apple clang has none. Others call `clang++`, `cmake`
or `cargo`, which the bootstrap installs. None has been built on Linux.

## Pilot timings on this Mac

Measured on 17 October 2026 on this M4 MacBook Pro (10 logical CPUs)
while it was shared and loaded: the 1-minute load average was 10.7 to
16.8 during the pairs, against a quiet threshold of 2.5. These runs
are for timing only. They are `--pilot` results under
`results/pilot/mac-m4/`, invalid by construction, and never labels.

| Task | Setup (both trees) | 200 pairs | Median pair | Full suite + oracle (both trees) |
|---|---:|---:|---:|---:|
| `textualize-rich-pr2217-landed` | 25.3 s | 79.1 s | 0.387 s | 7.8 s |
| `django-django-pr21460` | 41.4 s | 133.2 s | 0.644 s | 255.4 s |
| `sharkdp-fd-pr853` | 53.2 s | 49.3 s | 0.220 s | 40.7 s |
| `textualize-rich-pr2217-landed`, A/A | reused | 77.6 s | 0.381 s | not run |

"200 pairs" includes the warm-up (two runs per arm). Rich's setup is
from a 10-pair trial run just before, whose trees the pilot reused;
the other two were built in the pilot. Fetches were fast on this
network that day.

For the other development tasks, an estimate of the 200-pair time from
the single workload runs in their manifests: `204 x (task-tree run +
reference-tree run)`, which counts the warm-up. This is an inference,
not a measurement. Against the three pilot runs it was 33% low for
rich, 31% low for Django and 37% high for fd. A single first run
includes one-off work (bytecode compilation, container start-up), and
the pilot ran on a loaded machine, so the error goes both ways. Setup
and the full suites come on top.

| Task | Class | Single-run pair (s) | Inferred 200 pairs (min) |
|---|---|---:|---:|
| `apache-arrow-pr48898` | native | 1.81 | 6.2 |
| `astropy-astropy-pr18455` | python | 20.45 | 69.5 |
| `deepset-ai-haystack-pr12997` | agentic | 4.55 | 15.5 |
| `duckdb-duckdb-pr25547` | native | 1.98 | 6.7 |
| `gso-huggingface-transformers-d51b589` | python | 24.95 | 84.8 |
| `labstack-echo-pr3023` | web | 15.40 | 52.4 |
| `langchain-ai-langchain-pr38073` | agentic | 2.48 | 8.4 |
| `mem0ai-mem0-pr1260` | agentic | 2.23 | 7.6 |
| `openhands-openhands-pr12105` | agentic | 29.50 | 100.3 |
| `pola-rs-polars-pr29813-landed` | native | 0.67 | 2.3 |
| `pydata-xarray-pr6107` | python | 3.90 | 13.3 |
| `rails-rails-pr58751-landed` | web | 3.12 | 10.6 |
| `rails-rails-pr58964` | web | 2.68 | 9.1 |
| `run-llama-llama-index-pr18289` | agentic | 1.64 | 5.6 |
| `saleor-saleor-pr15725` | web | 23.47 | 79.8 |
| `scikit-learn-scikit-learn-pr19328` | python | 2.78 | 9.5 |
| `tokio-rs-tokio-pr5513` | native | 2.06 | 7.0 |

Their sum is about 8 hours of pairs for these 17 (489 min); with the
three pilots (4.4 min) the development set's pairs come to about 8.2
hours on this Mac. Setup of both trees, summed from the single
build runs in the same 20 manifests, is 4078 s (1.1 h), of which
polars (1530 s), duckdb (743 s), saleor (493 s), astropy (353 s),
arrow (309 s) and tokio (245 s) are the builds that use every core.
The oracles on both trees sum to 13 min. Only the three pilot tasks
have a full-suite recipe (5.1 min for the three, Django 255 s of it);
the other 17 suites are not written, so their time is not estimated.
Development set on this Mac, then: about 9.6 hours, pairs 85% of it.

### Estimate for the 40 CPU tasks on the Hetzner CCX13

This is an estimate, not a measurement: no CCX13 has run anything
here, and no held-out manifest's runs were read, so the held-out set
is taken to cost what the development set costs (both are 20 CPU
tasks, five per class, drawn by the same rule). The assumptions:

- Pairs run on one core. A CCX13 core is taken as 2x slower than an
  M4 core on these workloads (an assumption, not a measurement;
  server x86 cores commonly fall between 1.5x and 3x behind the M4 on
  single-thread work).
- Builds that use every core get 2 vCPUs instead of 10, so setup is
  taken as 3x slower (2x per core, less than the 5x core-count loss
  since not all of setup is compilation).
- The Mac's pair times were measured or inferred on a loaded machine
  (load 10.7 to 16.8 on 10 CPUs), which may have inflated them; the
  VM runs pairs only after its quiet check passes.

| Part | Development set on this Mac | On the CCX13 (x2 pairs, x3 setup) | 40 tasks (x2) |
|---|---:|---:|---:|
| Pairs, 200 each | 8.2 h | 16.4 h | 33 h |
| Setup, both trees | 1.1 h | 3.4 h | 7 h |
| Oracles, both trees | 0.2 h | 0.4 h | 1 h |
| Full suites | 3 of 20 known, 0.1 h | not estimated | not estimated |
| **Total** | **9.6 h** | **20 h** | **about 40 h** |

With the per-core factor at 1.5x instead of 2x (setup held at 3x) the
40 tasks come to about 32 hours; at 3x, about 57 hours. At about
$0.07 per hour the cost of the central 40 hours is about $2.80 (32 h
is $2.24, 57 h is $4.00), an estimate that rests on the same
assumptions and on the quoted hourly price. All of the hours exceed the 20 VM hours Faras approved, which cover roughly the
development set alone at the central assumption. The two heaviest
pair budgets, `openhands-openhands-pr12105` (100 min inferred) and
`gso-huggingface-transformers-d51b589` (85 min, and its reference
tree fails its oracle, so it cannot be labelled), are more than a
third of the development set's pairs. The first CCX13 pilot (`--pilot --pairs 20`
on `textualize-rich-pr2217-landed`) replaces the per-core factor with
a measurement; the estimate should be redone from it before the full
list is started.
