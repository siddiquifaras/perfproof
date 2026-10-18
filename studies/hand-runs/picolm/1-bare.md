# picolm, entry point 1: bare `/perfproof`

- Date: 2026-10-16, 22:48 to 23:02, M4 MacBook Pro (10 cores, 16
  GiB), AC power, Apple clang 17.0.0.
- Clone: `scratch/picolm`, cloned from Faras's checkout (upstream
  RightNow-AI/picolm; push disabled). HEAD at clone time was
  `4914f5b` on `feat/neon-flash-attention`, a branch of Faras's, not
  `main` (`cf3f2df`). **See the correction at the end: it invalidates
  this transcript's attribution.** The model
  `models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf` is a symlink to
  Faras's copy, not a copy.
- perfproof: `main` at `ce47494` for `doctor`, `bench` and `profile`;
  `compare` from PR #24 at `fad3631`. `pp` is `uv run --project
  <worktree> perfproof`.
- The user's lines are **simulated** by the agent. Load is `uptime`.
  Other sessions were running Docker and test suites throughout.
- No comparison here is a calibrated verdict.

> **user:** /perfproof

**Step 1: machine and model.** 22:48:21, 0.42 s, load 11.52; then 22:48:22, 0.07 s

```
$ pp doctor
  cpu_model Apple M4   load_average 11.15 9.93 9.41
  high_load: 1-minute load average is 11.15 on 10 logical CPUs …
  perf   unavailable … Linux-only
$ pp init
perfproof init: error: perfproof init is not built yet in perfproof 0.1.0.
```

`doctor` doesn't mention native profilers. samply isn't installed,
there's no `perf` on macOS, and only `/usr/bin/sample`, `dtrace` and
`xctrace` exist (`which`).

By hand: C11, about 3,000 lines, `picolm/Makefile`, whose first
target `native` adds `-march=native`. There are **no tests** in the
repo. CI (`.github/workflows/build.yml`) only builds: `make native`,
`make cross-pi`, RISC-V, and MSVC on Windows. The README claims
generation of "~13 tok/s" on x86. `.gitignore` ignores `*.gguf` and
the built `picolm/picolm`.

**Step 2: build.** 22:48:22, 0.93 s

```
$ make
gcc -O2 -std=c11 -D_GNU_SOURCE -Wall -Wextra -Wpedantic -march=native -o picolm picolm.c model.c tensor.c quant.c tokenizer.c sampler.c grammar.c -lm -lpthread
```

**Step 3: the target workload, first try.** 22:48:32, 1.37 s, load 10.35

```
$ ./picolm …/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf -p "Explain how a steam engine works." -n 128 -t 0 -s 42 -j 4
Prompt: 9 tokens, generating up to 128 (temp=0.00, top_p=0.90, threads=4)
</s>---
Prefill: 9 tokens in 0.56s (16.0 tok/s)
Generation: 1 tokens in 0.00s (12526.0 tok/s)
```

The chat model emitted EOS at once, so this "-n 128" workload is
prefill only. **Skill rule:** check the generated token count before
locking a prompt.

I tried two completion-style prompts. 22:48:40, 9.75 s, load 9.76,
and 22:48:50, 9.40 s, load 9.49:

```
-p "Once upon a time, in a small village by the sea,"   Prompt: 14 tokens … Generation: 129 tokens in 8.80s (14.7 tok/s)  Total: 9.73s
-p "The history of the steam engine begins"             Prompt: 8 tokens  … Generation: 129 tokens in 8.81s (14.6 tok/s)  Total: 9.35s
```

`-n 128` generates 129 tokens. picolm counts one more than asked
(observed, not investigated).

**Agent:** picolm has no benchmark and no tests. I'd measure the
generation time picolm itself prints, for 128 tokens after a fixed
14-token prompt, at `-j 4`. The oracle would be that the generated
text is identical. Questions:

1. Does `-j 4` on this M4 represent what you care about, or a Pi?
2. Is identical greedy text the right correctness bar, or may
   floating-point changes alter tokens?
3. Any constraints?

> **user (simulated):** -j 4 is fine; I can't benchmark on the Pi
> from here. Text must be byte-identical. Keep C11, zero
> dependencies, and the scalar fallback for non-SIMD targets.

**Step 4: define.** 22:49:40, 0.29 s, load 7.12

```
$ pp bench define gen128-once-upon --spec scratch/picolm-specs/spec1.json --json
  "spec_hash": "f59340620888b38197d26de9a5174055d6a08c39102e65a0de46aeea4daf607a"
```

The spec has no build field, so the command has to build and run:

```
sh -c "mkdir -p ../perfproof-out && make -s >/dev/null && ./picolm '<abs model>' -p 'Once upon a time, in a small village by the sea,' -n 128 -t 0 -s 42 -j 4 > ../perfproof-out/gen.txt 2> ../perfproof-out/stderr.txt && sed -n 's/^Generation: [0-9]* tokens in \([0-9.]*\)s .*/PERFPROOF {\"value\": \1}/p' ../perfproof-out/stderr.txt"
```

The rest of the spec: cwd `picolm`; metric `reported`, s, lower is
better, objective `generation_seconds`; correctness is
`perfproof-out/gen.txt` compared as text (there is no test command to
run); 20 rounds, looks n0=10, k=5, K=3.

- The model is gitignored and lives outside the repo, so it can't be
  locked.
- picolm prints the time itself in `picolm.c`, so for a `reported`
  metric that file *is* the harness, and I locked it. Any candidate
  that touches `picolm.c` will now fail the lock gate. In picolm the
  timing code is part of the program under test.
- The printed resolution is 0.01 s, about 0.1% of 8.8 s.

**Step 5: profile.** There is no samply or perf, so I wrote a
`--profiler-command` wrapper (`scratch/picolm-specs/sample_folded.sh`).
It runs the command, waits for the `picolm` process, samples it with
`/usr/bin/sample` at 1 ms, and converts the call graph to
`module`symbol` folded stacks (`sample_to_folded.py`).

The first converter run left addresses in frame names, so one
function split into many rows and everything showed as `(library)`
despite `--own-module picolm`. I fixed it after reading
`profilers/folded.py`. 22:49:40, 24.76 s, load 7.12 → 8.33:

```
$ pp profile gen128-once-upon --profiler-command '["…/sample_folded.sh","{output}","picolm","{command}"]' --own-module picolm --top 15
   9.8%   9.8%  -  vec_dot_q4_K_f32 [picolm] [0x100e15ae4,0x100e15aa4,...]  (library)
   6.6%   6.6%  -  __ulock_wait [libsystem_kernel.dylib] …
   …
Unprofiled run: wall 9.72 s, CPU 26.2 s (summed over all threads and processes).
CPU time is 2.70x wall time: other threads used CPU alongside the main one …
```

That is the CLI's output from the first converter. After fixing it, I
re-read the same capture with the fixed converter, outside the CLI:

```
12857  distinct "Thread_<id>" entries in one 14.7 s profiled run
 44.7% Thread;thread_start;_pthread_start;picolm`matmul_worker;picolm`vec_dot_q4_K_f32
 19.7% Thread;thread_start;_pthread_start;picolm`matmul_worker;picolm`vec_dot_q6_K_f32
 11.9% Thread;dyld`start;picolm`main;picolm`model_forward;picolm`matmul;picolm`vec_dot_q4_K_f32
  9.7% Thread;dyld`start;picolm`main;picolm`model_forward;picolm`matmul;_pthread_join;__ulock_wait
  5.6% Thread;dyld`start;picolm`main;picolm`model_forward;picolm`matmul;picolm`vec_dot_q6_K_f32
  3.1% Thread;thread_start
```

The quantised dot products take about 82% of samples. The main
thread spends 9.7% waiting in `pthread_join`. And `sample` saw 12,857
distinct worker threads.

**Step 6: hypothesis.** Done by hand: `log` doesn't exist.

- **H1.** `matmul` (`tensor.c:67`) creates `n_threads-1` pthreads and
  joins them on *every* call. A forward pass makes 7 matmuls × 22
  layers + 1 = 155 calls (counted with `grep matmul( model.c`), over
  14 + 129 positions.
- **Prediction:** if right, removing create and join saves about
  155 × 143 × (create and join cost). Generation time drops by that
  much, the text doesn't change, and CPU time falls slightly. If
  wrong, generation time doesn't move.
- Cheap test `cheap_threads.c` (cc -O2), 22:50:53, twice, load 6.92:
  ```
  create+join 3 threads: 49.8 us per matmul      | 50.3 us
  spinning pool handoff: 0.34 us per matmul      | 0.25 us
  at 155 matmuls x 143 positions: create+join 1.10 s, pool 0.008 s  | 1.12 s
  ```
  **Supported by the cheap test** (load 6.92). About 1.0 s of the 8.8 s generation is create and
  join (129 × 155 × 50 µs), so the cap is 8.8 / 7.8 = 1.13x for
  generation, before any gain from less join waiting.

**Step 7: candidate.** Branch `perfproof/matmul-thread-pool`, commit
`07db48c`, `picolm/tensor.c` +60/−9. Persistent workers wait on a
condition variable and run exactly the tasks a fresh thread would
have run (same row split), so the output stays bit-identical. The
Windows path is unchanged. I used a condvar rather than a spinning
pool: a spinning pool would burn the cores of a $10 board and of a
loaded machine.

It built without warnings. One sanity run (22:51:43, 12.45 s, load
5.24): candidate 9.41 s. Then, in an unlogged run (its output is in
the session, not in `scratch/logs`), the baseline after `git checkout
main && make` took 11.18 s, with the text identical (`cmp`). Those single runs mean nothing at this noise. I
could only build for macOS arm64: the cross-pi, RISC-V and MSVC CI
builds are **not verified**.

**Step 8: compare.** First try, 22:52:17, 1.16 s:

```
$ pp compare gen128-once-upon --candidate perfproof/matmul-thread-pool --baseline main
perfproof compare: error: the look schedule can't run on one host: on one host n0 and k must be even, so every look falls on complete order pairs; got n0=10, k=5
```

`bench define` had accepted that schedule. Redefined with n0=10, k=6,
22 rounds (`dcdaf0a1…`, 22:53:06). Second try, 22:53:14, 0.88 s, load 7.56:

```
$ pp compare gen128-once-upon --spec-hash dcdaf0a1 --candidate perfproof/matmul-thread-pool --baseline main
REJECT  integrity  gate runtime  0 rounds
  runtime: the baseline runtime is unknown: /bin/sh is a native Mach-O executable outside the tree and not a Python interpreter; its version is not probed; the candidate runtime is unknown: …
```

Here the native class is blocked, according to the runtime gate's
docstring (`bench/lock.py:1095`). The gate passes a native executable
only if it is *built inside each tree* before the gate runs. But:

- the spec has no build step, and compare's fresh worktrees contain
  no binary (it's gitignored);
- picolm writes its text to stdout and its timing to stderr. Without
  a shell, no output file exists for the oracle, and no `PERFPROOF`
  line exists for a `reported` metric.

The only way through was to waive the gate: `allow_runtime_change:
true` (`bb49d08c…`, 22:53:48). The verdict is then labelled "Runtime
change allowed", although nothing about the runtime differs.

Third try, 22:53:55, 460.79 s, load 5.93 → 11.97:

```
$ pp compare gen128-once-upon --spec-hash bb49d08c --candidate perfproof/matmul-thread-pool --baseline main
INCONCLUSIVE  insufficient_evidence  speedup 1.184x  interval 1.088x to 1.289x  22 rounds
The procedure is not calibrated yet: no KEEP or performance REJECT is given.
Gates: side_effects passed, lock passed, procedure passed, runtime waived, same_host passed, correctness passed, coverage not_available
  Labels: Runtime change allowed … Gate `runtime` waived by `allow_runtime_change`.
  correctness: 1 declared outputs compared
  cv_baseline 0.171  cv_candidate 0.244  sd_log_ratio 0.133
  wall_interval 1.082 to 1.272
```

From `samples.npz`, by hand (medians):

| | baseline | candidate |
|---|---|---|
| generation | 8.61 s | 7.39 s |
| wall | 9.50 s | 8.23 s |
| max RSS | 609.9 MiB | 610.7 MiB |
| CPU | 25.2 s | 24.1 s |

**Measured against prediction:** generation fell by 1.22 s against
the 1.0 s predicted from create and join alone. That is consistent
with less time waiting in `pthread_join` too (an inference: I didn't
measure join time separately).

**Agent (report):** One change is ready for your review, but not
kept. Reusing matmul's worker threads instead of creating them 155
times per token gave median generation of 8.61 s (baseline) and
7.39 s (candidate): estimate 1.18x (interval 1.09x to 1.29x,
uncalibrated, 22 rounds, load 5.93 at the start, 11.97 at the end).
This candidate was confounded (see the correction below). The text
is byte-identical and peak memory was unchanged (facts).

perfproof can't call it KEEP yet: its procedure is uncalibrated, and
I had to waive its runtime gate to measure a C program at all. I also
couldn't check the Windows, Pi and RISC-V builds your CI runs. The
branch is `perfproof/matmul-thread-pool` (`07db48c`).

## Correction (found during transcript 4)

Steps 2 to 7 ran in a tree checked out at `4914f5b`
(`feat/neon-flash-attention`), which the clone started on. The
candidate branch `perfproof/matmul-thread-pool` was cut from there,
so its parent is `4914f5b`, while compare's baseline was `main`
(`cf3f2df`). The comparison above therefore measured
**thread pool + NEON flash attention** against main. `git diff
--stat main perfproof/matmul-thread-pool` shows 3 files and +131
lines, not the pool's 1 file and +60/−9. perfproof didn't flag that
the candidate's diff against the baseline contains a commit the
candidate's author didn't write, and every gate passed.

The clean rerun: the pool cherry-picked onto `main` as
`perfproof/matmul-thread-pool-on-main` (`5ebcf45`, `tensor.c` only).
23:28:07, 458.03 s, load 9.80 → 9.91:

```
$ pp compare gen128-once-upon --spec-hash bb49d08c --candidate perfproof/matmul-thread-pool-on-main --baseline main
INCONCLUSIVE  insufficient_evidence  speedup 1.016x  interval 0.9512x to 1.085x  22 rounds
  cv_baseline 0.103  cv_candidate 0.122  sd_log_ratio 0.102   wall_interval 0.956 to 1.077
  (by hand) median generation 8.41 s baseline, 8.00 s candidate; CPU 24.4 s both; max RSS 610.8 vs 610.7 MiB
```

So for the thread pool **alone** the estimate is 1.02x (interval
0.95x to 1.09x, uncalibrated, load 9.80 at the start), which contains
1.0. The 1.18x above probably owed much to the NEON flash-attention
commit (an inference). Transcript 4 measures that commit on its own:
estimate 1.20x, uncalibrated INCONCLUSIVE, load 10.23 at the start
and higher later; not a proof. The
"measured against prediction" agreement in Step 8 (1.22 s saved
against 1.0 s predicted) was a coincidence of the wrong comparison.
The cheap test's 50 µs per create and join doesn't turn into a saved
1.0 s in the real program. My inference is that thread start-up
overlaps with the main thread's own share of each matmul, but I
didn't measure that.

**Corrected report:** reusing matmul's threads keeps the text
identical with estimate 1.02x (interval 0.95x to 1.09x, uncalibrated, load
9.80). That gives no support for merging it.
