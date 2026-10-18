# frames2py, entry point 1: bare `/perfproof`

- Date: 2026-10-16, M4 MacBook Pro (10 cores, 16 GiB), AC power.
- Clone: `scratch/frames2py` at `9e35f5f` (never Faras's checkout).
- perfproof: `main` at `98c22d3` for `doctor` and `bench`;
  `profile` from `origin/task/t2.10-profile` at `055eca4`;
  `compare` from `origin/task/t2.5-compare` at `463161b`.
- `pp` below is `uv run --project <worktree> perfproof`.
- The agent plays the skill. The user's lines are **simulated** by
  the agent, using what frames2py's `CONTRIBUTING.md` says the
  developer cares about. They are not Faras's answers.
- Load average (1, 5, 15 min) is `uptime` at each step. The machine
  was shared with other agents the whole time (Docker VM at ~195% CPU,
  several `rustc` jobs, and the T3.11 session running frames2py
  benchmarks). Load ran from 9 to 41 on 10 cores.
- No comparison here is a calibrated verdict. The compare thin path
  can't return KEEP.

## Conversation

> **user:** /perfproof

**Step 1: machine check.** 19:17:45, 0.85 s, load 16.01 17.10 15.43

```
$ pp doctor
  cpu_model Apple M4  physical_cores 10  power_source ac
  load_average 16.01 17.10 15.43
  python_version 3.12.12
Warnings
  high_load: 1-minute load average is 16.01 on 10 logical CPUs, above 0.5 per CPU ...
Tools
  git available ...   perf unavailable ... Linux-only
```

`python_version` is perfproof's own interpreter (3.12.12), not the
project's (3.11.14 from `.python-version`). `doctor` doesn't say
whether pyinstrument or py-spy can run here.

**Step 2: understand the project.** 19:17:53, 0.25 s, load 16.25

```
$ pp init
perfproof init: error: perfproof init is not built yet in perfproof 0.1.0.
```

By hand: read `pyproject.toml`, `CONTRIBUTING.md` and `benchmarks/`.
I found:

- target: Python (NumPy only), CPython 3.11 to 3.14t.
- workloads: `python -m benchmarks` with three suites. `gate` is 150
  cells (5 kernels × 3 resolutions × 5 batch/interval pairs × 2
  distributions) at two levels, `v1-kernel` and `v1-engine`. Each
  cell's own bar is ≥ 20M events/s on an Apple M4.
- oracle: pytest. In the full suite, 2237 passed and 320 skipped in
  523.9 s (load 22.9 → 15.7). CONTRIBUTING asks for outputs that are
  "bit-identical to the current implementation", before and after
  measured in the same session.
- constraint: the 1.x public API changes only in compatible ways.
- the v1 targets already check each run against a reference
  (`checks.valid`).

**Agent:** frames2py has 150 gate cells at two levels, and you name
both "sustained" events/s and per-call latency. Which cell matters
most to real use, and which statistic? The full test suite takes
about 9 minutes under the current load, and compare runs it 4 times
(3 on the baseline, 1 on the candidate). May I use the kernel
contract tests and the oracle for compare, and keep the full suite
for the end?

> **user (simulated):** exp_decay, 640x480, 100k events per call at
> 16 ms, uniform, engine level. That's the typical live setup.
> Throughput is what matters. Contract tests plus the oracle are fine
> per comparison.

**Step 3: try the cell as the project runs it.** 19:18:11, 10.10 s,
load 18.41 → 19.93

```
$ uv run python -m benchmarks run --suite gate --kernel exp_decay --resolution 640x480 \
    --batch-size 100000 --interval 16 --distribution uniform --target v1-engine --out …/f2py-cell1.json
| exp_decay 640x480 100k @ 16 ms uniform | yes | 31.4 | 21.8–60.4 | 1.017 / 13.569 / 40.078 (200) | 0.8 | 10 | ok | yes |
```

Five runs ranged from 21.8 to 60.4 M events/s. The harness writes a
JSON document and never prints a `PERFPROOF` line, so `reported`
needs a wrapper. `wall` for the whole command would mostly time
interpreter start, event generation and the reference check.

**Step 4: a reported-metric command.** I wrote an inline `python -c`
script (`scratch/f2py-specs/cell.py`). It runs the one cell through
`run_suite(..., Policy(runs=1), process_per_run=False)`, exits
non-zero if any check is invalid, saves the final `Accumulator` frame
to `perfproof-out/frame.npy`, and prints
`PERFPROOF {"value": <M events/s>}`.

19:19:51, 6 runs in 14.77 s, load 23.51 → 22.72. Sustained statistic:

```
PERFPROOF {"value": 69.59}   34.429   19.695   69.672   38.997   22.925
```

That is a 3.5x spread on an identical tree. Next I tried the median
call with 200 timed calls (`cell_median.py`). 19:20:15, 28.47 s, load
23.47 → 26.57:

```
PERFPROOF {"value": 161.83}   60.25   81.1   130.67   194.87   114.29
```

Still a 3.2x spread. The skill had to decide this. **Decision:**
median-call M events/s (`batch / median_ns`), 200 timed calls. It
resists the 30–55 ms preemption spikes visible in `call_ns`, but at
this load nothing comes close to the 3% bar.

**Agent:** This machine is too busy to settle anything near 3%. Six
identical runs vary 3x, so I expect INCONCLUSIVE. Pausing Docker and
the builds would help. I'll go on so you can see the full loop.

**Step 5: test subset.** 19:30:16, 7.69 s: `tests/contract/test_kernels.py
test_engine.py test_properties.py tests/test_oracle.py`, 604 passed and
2 skipped.

**Step 6: define and lock.** 19:30:38, 0.44 s, load 11.30

```
$ pp bench define exp-decay-640-100k --spec scratch/f2py-specs/spec1.json --json
  "ok": true … "spec_hash": "cb5cd1884bc416b0ea11c74d198ce7737fd35845fa7877a4750e51ffc3dc9312"  (34 locked files)
$ pp bench list exp-decay-640-100k
  cb5cd188…  34 locked files
```

The spec: command `uv run --frozen python -c <script>`; metric
`reported`, higher is better, `M events/s`; objective `throughput`;
correctness is the four test files plus `perfproof-out/frame.npy`
compared as bytes; warmup 1; 30 rounds or 1500 s; looks n0=10, k=10,
K=3; 10 `benchmarks/` modules as harness; `tests/contract`, the
oracle and the conftest as oracle; `pyproject.toml`, `uv.lock` and
`.python-version` as config.

The spec has no target class, so I had to declare all three default
guardrails. `peak_gpu_memory` and `p99_latency` are marked not
applicable, with reasons. `bench list` needs a name, so it can't
list every benchmark. `bench show` needs the full hash.

**Step 7: profile.** 19:30:55, 0.41 s

```
$ pp profile exp-decay-640-100k --top 15
perfproof profile: error: pyinstrument wraps a Python interpreter, and ~/.local/bin/uv is not one; use py-spy or --profiler-command
```

pyinstrument also needs a script file or `-m`, not `-c`. py-spy
needs root on macOS. `--profiler-command` can't help either:
`{command}` is the whole argv, so pyinstrument can't be put after
`uv run`. Workaround: a second, profile-only spec
`exp-decay-640-100k-prof` with `.venv/bin/python <abs script>` and
`PYTHONPATH=.`.

19:31:26, 10.53 s, load 9.32 → 11.81:

```
$ pp profile exp-decay-640-100k-prof --top 15
   self  total  location  function
  10.7%  10.7%  <built-in>  ufunc.at  (library)
  10.3%  10.3%  <built-in>  poll.poll  (library)
   9.7%  13.8%  benchmarks/targets/v1.py:131  reference
   9.5%   9.6%  benchmarks/workloads.py:122  Workload._fill_uniform
   8.5%   8.5%  src/frames2py/kernels/_builtin.py:31  _flat_index
   8.1%   9.2%  benchmarks/workloads.py:117  Workload._timestamps
   …
Unprofiled run: wall 3.19 s, CPU 1.83 s …
For at least 43% of wall time no thread of the benchmark was on a CPU: it was waiting, not computing.
Load average 9.3 on 10 CPUs.
```

The profile covers the whole process: harness, event generation, the
reference check, hashing and subprocess polls. The "43% waiting" is
mostly CPU contention at load 9 to 12, not I/O. The profile can't be
limited to the timed region, so I filtered `pyinstrument.folded.txt`
by hand to stacks under `Engine.ingest`. That is 14.1% of the
profile:

```
 51.2% ExpDecay.accumulate > ufunc.at
 27.6% ExpDecay.accumulate > _flat_index
  8.3% Accumulator._accumulate:80 > t.max (ufunc.reduce)
  6.5% Accumulator._in_bounds:99 > x.max / y.max
  4.6% Engine._publish > ExpDecay.read
```

`profile` also ran the benchmark in the user's working tree and left
`perfproof-out/` there, untracked.

**Step 8: hypotheses and cheap tests.** `measure` and `log` don't
exist, so I did this by hand. History matters here: commit
`6b09b2d perf: use add.at for count kernels` deliberately replaced
`bincount` to get rid of a frame-sized int64 temporary.

- **H1.** `np.add.at` is slow. An exact alternative (bincount, then
  add the weight by rank) or `bincount*w` would beat it. Cap from the
  profile share: 1/(1-0.512) = 2.05x of ingest. **Prediction:** if the
  mechanism is right, the alternative's median is below add.at's. If
  wrong, add.at is already on NumPy's fast path.
  Cheap test `cheap_h1.py`, 19:32:59, load 25.30:
  ```
  add.at      median    119.5 us
  rank        median   2008.0 us
  bincount*w  median    703.6 us
  rank: bit-identical to add.at (float64): True
  bincount*w: bit-identical to add.at (float64): False  differing pixels 162007; float32 identical: True
  ```
  **Not supported by the cheap test** (a single-process microbenchmark at load 25, uncalibrated), so not implemented. Side finding: `bincount*w` changes
  162,007 float64 state pixels but leaves the float32 frame
  identical, so a frame-bytes oracle can't see it.
- I timed each stage (`stages.py`, 19:33:16, load 30.30): validate
  1.0, t.max 139.8, x/y max 59.0, `_flat_index` 117.8, add.at 120.0,
  read 75.1, total 516.3 µs. Microbenchmarks are unreliable at this
  load. The same `t.max` measured 41.5 µs 20 s later at load 32.11.
- **H2.** The 13-byte packed `EVENT_DTYPE` makes field reductions
  strided and unaligned, so a contiguous copy of `t` reduces faster.
  Cap: 1/(1-0.083) = 1.09x of ingest. Cheap test `cheap_h2.py`: 41.5
  vs 37.9 µs. **Inconclusive** at this noise, and most likely below
  the 3% bar. I implemented it anyway to exercise the prove path, and
  say so here.

**Step 9: candidate.** Branch `perfproof/h2-contiguous-max`, commit
`bca121d` in the clone. It is a 3-line change to
`Accumulator._accumulate`.

**Step 10: compare.** 19:34:11, 424.90 s, load 40.54 → 24.96

```
$ pp compare exp-decay-640-100k --candidate perfproof/h2-contiguous-max --baseline main
INCONCLUSIVE  insufficient_evidence  speedup 0.8228x  interval 0.5603x to 1.208x  30 rounds
The procedure is not calibrated yet: no KEEP or performance REJECT is given.
Gates: side_effects passed, lock passed, procedure passed, runtime passed, same_host passed, correctness passed, coverage not_available
```

From `report.md`: cv_baseline 0.54, cv_candidate 0.66, sd_log_ratio
0.74. `wall_interval` was 0.83x to 1.10x, narrower than the reported
metric's. The `peak_memory` guardrail is "unmeasurable (T2.15)".
"Not recorded: the repository and the candidate's commit."

Where the time went, from file times in the run dir: worktrees, uv
venvs and the first outputs took about 15 s, the correctness gate
about 90 s, and 30 rounds about 5 min (about 3.5 s per execution).
There was no progress output during the 7 minutes.

**Agent (report):** Nothing is kept. On `exp_decay 640x480 100k @16ms
uniform`, in a microbenchmark at load 25, `np.add.at` took 119.5 µs against
703.6 µs and 2008.0 µs for the alternatives, so H1 was not
supported there. H2 (a contiguous `t` before
the max) could be worth at most 9% of ingest, and probably far less.
Its comparison was INCONCLUSIVE (estimate 0.82x, interval 0.56x to
1.21x, uncalibrated, load 40.54 at the start)
because this machine's load (25 to 41 on 10 cores) makes identical
runs vary 3x. Two things would settle it: a quiet machine, or many
more rounds. perfproof doesn't yet estimate how many. A uncalibrated
compare can't return KEEP in any case.

## Addendum: the same compare on a quiet machine

Three hours later the load was 2.69. Same spec, candidate and
baseline, with compare at `fad3631` (the PR #24 head after main was
merged in). 22:12:43, 99.51 s, load 2.69 2.44 2.38 → 7.23:

```
$ pp compare exp-decay-640-100k --candidate perfproof/h2-contiguous-max --baseline main
INCONCLUSIVE  insufficient_evidence  speedup 1x  interval 0.9407x to 1.064x  30 rounds
Gates: side_effects passed, lock passed, procedure passed, runtime passed, same_host passed, correctness passed, coverage not_available
```

`report.md`: cv_baseline 0.081, cv_candidate 0.111, sd_log_ratio
0.119; `wall_interval` 0.965 to 1.053. Compared with the loaded run,
the coefficient of variation fell from 0.54–0.66 to 0.08–0.11 and
the whole compare took 99.5 s instead of 424.9 s. The estimate is
1.000, consistent with H2's cheap test (no effect worth having), and
the interval (0.94 to 1.06, uncalibrated) still straddles the 1.03
bar. Load looks like the dominant term (two runs, so an inference). The skill should check it and wait or warn before measuring,
not after.
