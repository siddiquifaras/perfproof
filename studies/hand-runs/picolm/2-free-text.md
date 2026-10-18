# picolm, entry point 2: free text

- Date: 2026-10-16, 23:02 to 23:15, same machine and clone as
  transcript 1. Baseline `main` (`cf3f2df`). **See the correction at the
  end: Step 5's candidate also carried the NEON flash-attention
  commit.**
- perfproof: `main` at `ce47494`; `compare` from PR #24 at `fad3631`.
- The user's lines are **simulated**. Load is `uptime`. No
  comparison here is a calibrated verdict.

> **user:** /perfproof the first token takes forever when I paste a
> long prompt

**Agent (reading).** "First token" means prefill.
`picolm.c:185-249` times prefill from `t_start` (after loading and
tokenising) to the first sampled token. Prefill feeds the prompt one
position at a time through `model_forward`, exactly like generation,
with no batching.

**Step 1: a long prompt.** I used the first 2,000 bytes of `BLOG.md`.
23:02, 145.69 s, load 7.26 → 9.11:

```
$ head -c 2000 ../BLOG.md | ./picolm <model> -n 1 -t 0 -s 42 -j 4
Prompt: 1102 tokens, generating up to 1 …
Prefill: 1102 tokens in 145.34s (7.6 tok/s)
```

That is 1,102 tokens for 2,000 bytes of Markdown (about 1.8 bytes per
token). I noted it and didn't investigate; it may be Markdown and
emoji falling back to byte tokens. At 145 s per run it's unusable as
a benchmark.

Plain prose instead: `LICENSE` (1,077 bytes). 23:05, 33.70 s, load 8.39:

```
Prompt: 357 tokens … Prefill: 357 tokens in 33.50s (10.7 tok/s)   Total: 33.63s
```

Process wall time was 33.70 s against 33.50 s of prefill, so model
loading (mmap) and tokenising take about 0.2 s. Time to first token
*is* prefill.

**Agent:** On this M4, a 357-token prompt waits 33.5 s for its first
token. Nearly all of that is the forward passes. How long are your
real prompts? I'll benchmark a 144-token prompt (the first 540 bytes
of LICENSE), so one run takes about 14 s instead of 34. Is that
representative enough?

> **user (simulated):** PicoClaw's system prompt plus a message is
> 150 to 300 tokens. 144 is fine. Keep -j 4.

**Step 2: define.** 23:06:25, 0.31 s, load 7.90

```
$ pp bench define prefill-license540 --spec scratch/picolm-specs/spec2.json --json
  "spec_hash": "5e24a7e01eaea87b0fa493ce1365e3dbf0b83e88dda08e6f541226ce2bb5c2d9"
  locked: LICENSE (input), picolm/Makefile (config), picolm/picolm.c (harness)
```

Command: `sh -c "… head -c 540 ../LICENSE | ./picolm <model> -n 1 -t 0 -s 42 -j 4 > ../perfproof-out/gen.txt 2> ../perfproof-out/stderr.txt && sed … Prefill: … s …"`.
Metric `reported`, s, objective `prefill_seconds`. It keeps
`allow_runtime_change: true` from transcript 1, without which compare
refuses `/bin/sh`. Looks n0=10, k=6, K=2, 16 rounds.

Here the input *can* be locked, because LICENSE is in the repo. The
model still can't be.

One trial: 23:06:25, 13.74 s, `PERFPROOF {"value": 13.57}`, 144 tokens.

That is 94 ms per position, against 68 ms per position in transcript
1's generation over a similar range (positions 14 to 143). I can't
explain the gap from the profile below. It is **left open**.

**Step 3: profile.** 23:06:49, 30.98 s, load 7.98 → 9.02

```
$ pp profile prefill-license540 --profiler-command '["…/sample_folded.sh","{output}","picolm","{command}"]' --own-module picolm --top 12
  54.8%  54.8%  -  picolm`vec_dot_q4_K_f32
  25.9%  25.9%  -  picolm`vec_dot_q6_K_f32
   9.2%   9.2%  -  libsystem_kernel.dylib`__ulock_wait  (library)
   2.5%   2.5%  -  picolm`fp16_to_fp32
   1.9%  32.4%  -  picolm`model_forward
   1.7%  67.6%  -  libsystem_pthread.dylib`thread_start  (library)
   0.9%   0.9%  -  libsystem_kernel.dylib`__bsdthread_create  (library)
Unprofiled run: wall 14.7 s, CPU 34.8 s …
CPU time is 2.36x wall time …
```

The shape is the same as generation: 81% in the quantised dot
products, and 9% of samples waiting in `__ulock_wait`
(`pthread_join`, transcript 1). Thread creation shows up directly
(`__bsdthread_create`, `_pthread_create`).

**Step 4: hypotheses.** Done by hand (`log` and `measure` don't
exist).

- **H1.** Thread create and join per matmul, the same mechanism as
  transcript 1. A candidate already exists
  (`perfproof/matmul-thread-pool`, not yet kept). Prediction from
  transcript 1's cheap test: 144 × 155 × 50 µs = 1.1 s of about
  13.6 s, a "cap" of 1.09x.
- **H2.** Batched prefill: run all prompt positions through each
  layer together, so each weight row is read once per layer. To stay
  byte-identical, every (row, token) dot product must keep its exact
  arithmetic, so only memory traffic could be saved. At 68 to 94 ms
  per token, 638 MB of weights per token is 7 to 9 GB/s, far below the
  M4's bandwidth. So this looks compute-bound (an inference: I didn't
  measure bandwidth), and an exact batched prefill would gain little.
  A dequantise-once variant would change the arithmetic, and so the
  text. **Not tried:** it's a 150-line rewrite of `model_forward` with
  a weak predicted gain.
- **H3, a setting.** Threads. `measure --set` doesn't exist, so by
  hand, two reps of `-j 4`, `-j 6` and `-j 8`. 23:07, load 8.10 → 9.67:
  ```
  -j 4: 14.08s   -j 6: 12.06s   -j 8: 11.91s
  -j 4: 10.92s   -j 6:  9.13s   -j 8:  8.38s
  ```
  `-j 4` alone moved 29% between reps. `-j 8` beat `-j 4` in both, which is weak evidence.
  That is a deployment setting, and the user's target is a 4-core
  board, so I report it and don't pursue it.

**Step 5: compare H1's existing candidate on this workload.** 23:08, 369.66 s, load 9.11 → 9.74

```
$ pp compare prefill-license540 --candidate perfproof/matmul-thread-pool --baseline main
INCONCLUSIVE  insufficient_evidence  speedup 1.192x  interval 1.114x to 1.276x  16 rounds
Gates: … runtime waived, … correctness passed, coverage not_available
  cv_baseline 0.160  cv_candidate 0.139  sd_log_ratio 0.093
  wall_interval 1.118 to 1.275
```

By hand from `samples.npz`: median prefill was 10.60 s for the
baseline and 8.65 s for the candidate, with max RSS 611.0 vs 610.9
MiB.

**Measured against prediction (superseded by the correction below,
because this candidate was confounded):** median prefill differed by
1.95 s against a "cap" of 1.1 s. The estimate (1.19x, lower bound
1.11x, uncalibrated, load 9.11) is above the cap (1.09x). The cap only counted creation cost, while
the persistent pool also cut join waiting and scheduling (an
inference). `docs/discovery.md` treats a cap as an upper bound. This
one, derived from a microbenchmark, wasn't.

**Agent (report):** Your first token is slow because picolm runs the
prompt one position at a time through the full model: about 94 ms
per position here, so 144 tokens take 10 to 14 s on this machine. The
earlier candidate gave median prefill of 10.60 s (baseline) and 8.65 s
(candidate), estimate 1.19x (interval 1.11x to 1.28x, uncalibrated,
16 rounds, load 9.11 at the start), with identical output. That
candidate was confounded (see the correction below), so this is not
evidence for the thread pool. It is still not a perfproof KEEP: the
procedure is uncalibrated and the runtime gate had to be waived.

On this M4, two quick tries each timed `-j 8` below `-j 4` (11.91 vs 14.08 s, 8.38 vs 10.92 s),
which is weak evidence (two runs each, `-j 4` alone moved 29%
between them). It may be worth testing properly if PicoClaw ever runs
on a bigger board. A
batched prefill is the larger structural fix, but it would only stay
byte-identical if it kept today's per-token arithmetic, and then I
expect little gain.

## Correction (found during transcript 4)

Step 5's candidate, `perfproof/matmul-thread-pool`, was cut from
`feat/neon-flash-attention` (transcript 1, correction), so it measured
the thread pool *plus* NEON flash attention. Clean rerun with
`perfproof/matmul-thread-pool-on-main` (`5ebcf45`). 23:35:45,
468.77 s, load 9.91 → 14.45:

```
$ pp compare prefill-license540 --candidate perfproof/matmul-thread-pool-on-main --baseline main
INCONCLUSIVE  insufficient_evidence  speedup 1.021x  interval 0.899x to 1.159x  16 rounds
  cv_baseline 0.306  cv_candidate 0.277  sd_log_ratio 0.177   wall_interval 0.901 to 1.156
  (by hand) median prefill 11.15 s baseline, 12.43 s candidate; CPU 30.0 vs 32.1 s; max RSS 610.1 vs 610.0 MiB
```

The pool alone is not shown to help prefill. The noise here is the
worst of the picolm runs: CV 0.28 to 0.31, with the load rising to
14. The 1.19x and the "measured above the cap" observation in Step 5
both belong to the combined change. The cap lesson stands only as a
caution: I have no clean evidence that a cap was exceeded.

**Corrected report:** the thread-pool change keeps the output
identical with estimate 1.02x on prefill (interval 0.90x to 1.16x,
uncalibrated, load 9.91 at the start, cv 0.28 to 0.31). The useful facts for your
complaint remain: time to first token is prefill, at about 70 to
100 ms per prompt token here, and two quick tries on this M4 timed `-j 8` below `-j 4`
(weak evidence).
