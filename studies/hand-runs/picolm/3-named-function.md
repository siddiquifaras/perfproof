# picolm, entry point 3: a named function

- Date: 2026-10-16, 23:15 to 23:28, same machine and clone. The
  baseline is the clone's `main`, `cf3f2df` (see the correction in
  transcript 1).
- perfproof: `main` at `ce47494`; `compare` from PR #24 at `fad3631`.
- The user's lines are **simulated**. Load is `uptime`. No
  comparison here is a calibrated verdict.

> **user:** /perfproof vec_dot_q4_K_f32

**Agent (reading).** `picolm/quant.c:378`. It is the Q4_K × f32 dot
product that `matmul` calls once per output row. Both profiles
attribute most samples to it: 56.6% in transcript 1 (44.7% in workers
plus 11.9% on the main thread), though that profile ran on the
`4914f5b` tree, not on `main`; and 54.8% in transcript 2, on `main`. Six of the
seven per-layer matmuls in TinyLlama Q4_K_M call it. The other is
Q6_K, as is the output matrix.

Inside its NEON loop, every 8 weights it does:

- nibble extraction, two widenings and a `vcvtq` per half;
- two `vmlaq_f32` for `sum_qx`;
- two `vaddq_f32` for `sum_x1` and `sum_x2`, the sums of `x` over each
  32-element half-chunk.

`sum_x` depends only on `x`, not on the weight row, yet every one of
the `d` rows computes it again.

- **H1.** Compute the `n/32` chunk sums of `x` once per matmul, in
  exactly the same operation order, and pass them in. That removes a
  quarter of the vector arithmetic per element. The results stay
  bit-identical because the same additions run in the same order,
  only once.
- **Prediction:** a Q4_K matmul takes measurably less time
  with bit-identical outputs. If the function is bound by loads rather than
  arithmetic, there's no gain.

**Step 1: cheap test.** I wrote `scratch/picolm-specs/cheap_xsums.c`,
linked against the clone's `quant.c`, on random Q4_K rows with sane
fp16 scales. One thread, best of 30. 23:16:02, 0.83 s, and 23:16:03,
0.14 s, load 6.73:

```
2048x2048 Q4_K matmul, 1 thread, best of 30: current 422.0 us, precomputed sums 341.0 us (1.24x); bit-identical: yes
5632x2048 Q4_K matmul, 1 thread, best of 30: current 1160.0 us, precomputed sums 939.0 us (1.24x); bit-identical: yes
2048x2048 … current 422.0 us, precomputed sums 341.0 us (1.24x); bit-identical: yes
5632x2048 … current 1161.0 us, precomputed sums 939.0 us (1.24x); bit-identical: yes
```

**Supported**: the result was identical across both runs. Cap from the
profile share: if 56% of time speeds up 1.24x, the whole run goes
1/(1 - 0.56 × (1 - 1/1.24)) = 1.12x.

**Step 2: candidate.** Branch `perfproof/q4k-precomputed-xsums`,
commit `07852a9`, from `main`. Files: `quant.c`, `quant.h` and
`tensor.c`, +69/−19.

- New `vec_dot_q4_K_xsums()` (NEON and scalar, each in the order the
  dot product used).
- New `vec_dot_q4_K_f32_xsums()`. `matmul` computes the sums once
  when `qtype == Q4_K`, and the tasks carry them.
- `vec_dot_q4_K_f32()` keeps its signature for any other caller.

It builds without warnings. One run, 23:16: 129 tokens in 9.13 s, text
byte-identical to the baseline's (`cmp`).

**Step 3: the scalar fallback, which the user asked to keep.** I built
both trees with `-U__ARM_NEON`, so `quant.h` doesn't define
`PICOLM_NEON` and the `#else` source paths compile. I didn't
disassemble to confirm what ran. 23:17:49, 5.33 s, and 23:17:54, 3.80 s, load 6.48:

```
base: Generation: 33 tokens in 2.59s (12.8 tok/s)
cand: Generation: 33 tokens in 2.38s (13.9 tok/s)
134 base.txt  134 cand.txt   scalar-identical
```

The "scalar" build runs almost as fast as NEON, which suggests clang
auto-vectorises the scalar loop. That is an inference. A first attempt
at this step failed to build anything: zsh didn't split the source
list, and both output files came out empty. `cmp` then reported two
empty files as "identical". I caught it by checking file sizes. The
skill must check that outputs are non-empty before trusting an
identity check, and so should perfproof's output gate (not tested
here).

The x86 SSE2 build, which also takes the scalar path for Q4_K,
and the cross-pi, RISC-V and MSVC builds are **not verified**.

**Step 4: compare.** Spec `gen128-once-upon` at `bb49d08c`, the
version with `allow_runtime_change` from transcript 1. 23:18:12,
576.47 s, load 7.13 → 10.55:

```
$ pp compare gen128-once-upon --spec-hash bb49d08c --candidate perfproof/q4k-precomputed-xsums --baseline main
INCONCLUSIVE  insufficient_evidence  speedup 1.048x  interval 0.9732x to 1.129x  22 rounds
Gates: side_effects passed, lock passed, procedure passed, runtime waived, same_host passed, correctness passed, coverage not_available
  cv_baseline 0.123  cv_candidate 0.139  sd_log_ratio 0.116
  wall_interval 0.982 to 1.126
```

By hand from `samples.npz` (44 executions), medians:

| | baseline | candidate |
|---|---|---|
| generation | 10.84 s | 10.84 s |
| CPU | 32.5 s | 30.2 s |
| max RSS | 610.0 MiB | 609.7 MiB |

**Measured against prediction:** CPU time fell about 7%, which is weak
evidence that arithmetic was saved (one comparison, load 7 to 10, no
uncertainty estimate for CPU time). Wall time did not settle. The estimate is
1.05x and the interval (0.97x to 1.13x) contains both 1.0 and the
predicted 1.12x. At 4 threads on a machine at load 7 to 10, the
single-thread microbenchmark's 1.24x doesn't transfer one-for-one.
That is an inference: thread scheduling noise dominates.

**Agent (report):** `vec_dot_q4_K_f32` redid a sum that doesn't
depend on the weight row for every one of thousands of rows per
matmul. Hoisting it out keeps the output byte-identical on both the
NEON and the plain-C paths, and took a single-threaded Q4_K matmul from 422 to 341 µs in an
isolated microbenchmark (load 6.73). In the full program on this busy machine,
CPU time fell about 7%, but generation time didn't move clearly: the
estimate is 1.05x (interval 0.97x to 1.13x, uncalibrated, 22 rounds,
load 7.13 at the start). That says nothing either way. A quieter machine, or more rounds,
would settle it. perfproof doesn't yet say how many. The branch is
`perfproof/q4k-precomputed-xsums` (`07852a9`).
