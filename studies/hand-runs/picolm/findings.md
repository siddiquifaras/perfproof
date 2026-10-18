# picolm hand-run findings

Transcripts: `1-bare.md` (T1), `2-free-text.md` (T2),
`3-named-function.md` (T3), `4-existing-change.md` (T4). Severity uses
the scale in `../frames2py/findings.md`. Nothing here was patched.

## What happened, in one table

| Entry | Workload | Candidate | perfproof said | Interval | Load |
|---|---|---|---|---|---|
| T1 bare (contaminated, see #1) | gen128, -j 4 | thread pool **+ NEON attention** | INCONCLUSIVE 1.18x | 1.09x to 1.29x | 5.9 to 12 |
| T1 clean | gen128 | thread pool only | INCONCLUSIVE 1.02x | 0.95x to 1.09x | 9.8 |
| T2 free text (contaminated) | prefill 144 tokens | thread pool + NEON attention | INCONCLUSIVE 1.19x | 1.11x to 1.28x | 9.1 |
| T2 clean | prefill 144 tokens | thread pool only | INCONCLUSIVE 1.02x | 0.90x to 1.16x | 9.9 to 14 |
| T3 named fn | gen128 | Q4_K x-sums hoisted | INCONCLUSIVE 1.05x | 0.97x to 1.13x | 7.1 |
| T4 PR A | gen128 | NEON flash attention (`4914f5b`) | INCONCLUSIVE 1.20x | 1.12x to 1.28x | start 10.23 10.66 10.25; 87.26 1-min mid-run (unlogged hand `uptime`); end 49.66 74.36 48.60 |
| T4 PR B | gen128 | `-O3 -ffast-math` (#16) | REJECT lock, then INCONCLUSIVE 1.11x | 1.06x to 1.17x | start 11.57 34.32 37.42 |

Two comparisons have estimates above 1 with intervals entirely above
1 (uncalibrated), and both are Faras's existing PRs: NEON attention and #16.
PR A also ran under a load spike. The two hypotheses the skill
generated (thread pool, hoisted sums) are not shown to help.

## Findings

| # | Affects | Severity | Finding | Shown at |
|---|---|---|---|---|
| 1 | `compare` baseline vs candidate ancestry; skill | high | The candidate was cut from a branch other than the baseline, because the clone started on `feat/neon-flash-attention`. Compare measured two changes, credited the gain to the wrong one (1.18x, really about 1.02x), and every gate passed. perfproof should report the commits and files between baseline and candidate, and warn when the baseline isn't the candidate's merge-base. The skill must run `git merge-base` and show the diff it's proving. | T1 correction, T2 correction, T4 merge-base check |
| 2 | skill: prediction vs outcome | high | Under the contaminated comparison, the outcome "matched" the prediction (1.22 s saved vs 1.0 s predicted). Clean, the effect was within noise. An agreement between prediction and outcome is not evidence the mechanism was right. | T1 Step 8 and correction |
| 3 | `compare` runtime gate, native class | block | `sh -c 'make && ./picolm … > out'` fails the gate (`/bin/sh` "is a native Mach-O executable outside the tree and not a Python interpreter"), and the result reads REJECT `integrity`. The gate accepts only a native binary already built inside each tree. But the spec has no build step and compare's fresh worktrees have no binary, so the only way through is `allow_runtime_change: true`, which labels every verdict "Runtime change allowed" when nothing changed. | T1 Step 8 |
| 4 | spec: no build step | block | Native targets are "built per tree" (`docs/targets.md`), but the spec has no build field. The build has to live inside the timed command (`make -s && …`), so warmup absorbs the compile and every round re-runs make's up-to-date check. | T1 Step 4 |
| 5 | spec: output only from files | high | picolm writes its text to stdout and its timing to stderr. Output checks read only files, and a `reported` metric needs a stdout `PERFPROOF` line. Both force a shell wrapper, which then hits finding 3. | T1 Step 4, 8 |
| 6 | spec: the timing code is the program | med | picolm prints its own timing in `picolm.c`, so for a `reported` metric that file is the harness and gets locked. Any candidate touching `picolm.c` fails the lock. Here none did, by luck. | T1 Step 4 |
| 7 | spec: locking build config vs build-flag PRs | high | Locking `picolm/Makefile` gives REJECT `integrity` (lock) for a compiler-flag PR. That is right for the lock and wrong for the user. The skill had to explain it and define an unlocked variant, which is a new spec and needs the user's agreement. | T4 PR B |
| 8 | `bench define` validation | med | It accepted looks `n0=10, k=5`. `compare` then refused: "on one host n0 and k must be even". Define should refuse what compare can't run. | T1 Step 8 |
| 9 | spec versions | low | Four versions of `gen128-once-upon` piled up (schedule, runtime waiver, lock) under one name. `bench list` shows only the hash and the file count, so the skill must keep its own notes on what each one is. | T4 PR B |
| 10 | inputs outside the repo | med | The 638 MB model is gitignored and can't be locked. The model file could change between rounds or sessions and nothing would notice. LICENSE, in the repo, could be locked as an input. | T1 Step 4, T2 Step 2 |
| 11 | `profile`, native on macOS | block | There is no samply or perf on this Mac, and `doctor` doesn't say which native profilers exist. I wrote a `--profiler-command` wrapper around `/usr/bin/sample` plus a call-tree-to-folded converter. Its first version produced frames the CLI couldn't classify (all "(library)", `--own-module` ignored), until I read `profilers/folded.py` and emitted `module`symbol`. A playbook needs a shipped, tested macOS native profiler. | T1 Step 5 |
| 12 | `profile` | med | Per-thread names (`Thread_<id>`) split identical stacks across the 12,857 short-lived worker threads. The converter had to normalise them. That count was itself the evidence for the thread-pool hypothesis. | T1 Step 5 |
| 13 | `compare` load warning | med | `high_load` reports only the load at the start (10.23), while it reached 87 mid-run (by a hand-run `uptime`, not logged) and was 49.66 at the end. Generation took 2.4x as long under that load. The second fingerprint is taken but not surfaced as a warning. | T4 PR A |
| 14 | `compare` guardrails (T2.15) | med | `peak_memory` is unmeasurable, though `max_rss_bytes` is recorded per execution. By hand it was unchanged (about 610 MiB) in every picolm comparison. | all compares |
| 15 | correctness: one prompt | high | The oracle is one prompt at one length. `-ffast-math` passed it, but that doesn't show reassociation is safe on other prompts or at longer contexts. The spec allows one command, so several prompts need a wrapper, or several specs. | T4 PR B |
| 16 | correctness: no tests, CI builds other targets | high | picolm has no tests. CI builds cross-pi, RISC-V and MSVC, none of which can be built here. Every candidate's portability (scalar fallback, `_WIN32` path) went unverified except the one I built by hand with `-U__ARM_NEON`. | T1 Step 7, T3 Step 3 |
| 17 | skill: outputs must be non-empty | high | A failed build left two empty outputs, and `cmp` called them identical. perfproof's output check may have the same blind spot (not tested). | T3 Step 3 |
| 18 | skill: workload that measures nothing | high | The first prompt hit EOS after one token, so "-n 128" measured prefill only. The skill must check the token count before locking a prompt. | T1 Step 3 |
| 19 | skill: cheap tests that don't transfer | high | Microbenchmarks were bit-identical and stable: create and join cost 50 µs per matmul, and the Q4_K sums gave 1.24x on one thread. In the 4-thread program under load they gave 1.02x and 1.05x, within noise (CPU time −7% for the sums). Clean runs show microbenchmark caps over-predicting badly. There is no clean evidence of a cap being exceeded. | T1 Step 6, T3 Step 1, 4 |
| 20 | skill: confounded claims in PRs | med | PR A's table credited itself with #16's gain. The skill must measure each change against its own parent and say when a claim mixes changes. | T4 PR A |
| 21 | skill: settings angle (`measure --set` missing) | low | two quick tries timed `-j 8` below `-j 4` (11.91 vs 14.08 s, 8.38 vs 10.92 s), but `-j 4` alone moved 29% between reps. There is no command to sweep a setting, and the user's target hardware (a Pi) differs from this machine. | T2 Step 4 |
| 22 | `init` (missing) | block | No project model. picolm has no benchmark and no tests, so the skill had to invent the workload and the oracle from `docs/targets.md` and the README. | T1 Step 1 |
| 23 | load on a shared machine | high | CV ran from 0.10 to 0.31. The same baseline's median generation was 8.41 s at 23:28 and 21.27 s at 00:10. Each picolm compare took 6.2 to 16.3 min. Without `--detach`/`status`, the skill sat in a foreground call for up to 979 s. | T4, T2 correction |
| 24 | observed in picolm, not perfproof (not investigated) | info | `-n 128` generates 129 tokens. 2,000 bytes of BLOG.md tokenise to 1,102 tokens. Prefill runs at about 94 ms per position against about 68 ms in generation over similar positions. | T1 Step 3, T2 Step 1, 2 |
