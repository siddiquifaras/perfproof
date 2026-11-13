# vLLM: prefix caching that costs throughput when nothing hits

I did this by hand in October 2026, before perfproof existed. It's the
template for what perfproof should automate. It has not been re-run
through perfproof, so none of these numbers is a perfproof verdict.

## Question

vLLM issue [#51663](https://github.com/vllm-project/vllm/issues/51663)
reported a large output-throughput drop for
`Qwen/Qwen3.6-35B-A3B-FP8` between v0.23.0 and v0.24.0 and later. The
model is a hybrid: 30 of its 40 layers are Gated DeltaNet.

## Setup

- Official `vllm/vllm-openai:v<version>` images on Modal, one fresh GPU
  container per version
- One A100 80GB, tensor parallel 1. An L40S 48GB ran out of memory at
  these settings. On A100 the MoE layers run through Marlin.
- `vllm bench serve`, random dataset, 512 output tokens with
  `--ignore-eos`, concurrency 40, 400 prompts
- One warmup run, then three measured runs. Tables show the median.
- Prefix-cache hit rate read from `/metrics` before and after each run
- Scripts: [`reference/vllm-case-study/`](../../reference/vllm-case-study/), also in a [public gist](https://gist.github.com/siddiquifaras/16543eddb933800734674debe11f5573)

## Results

Output tokens per second.

**The reported regression did not reproduce:**

| Prompt | v0.23.0 | v0.26.0 |
|---|---|---|
| random 1024 | 1487 | 1521 |
| 768 shared + 256 random | 1542 | 1549 |

This matched another contributor's H100 result.

**Something else did show up in v0.31.0:**

| Prompt | v0.26.0, cache on | v0.31.0, cache on | v0.31.0, cache off | Hit rate |
|---|---|---|---|---|
| random 1024 | 1521 | 1333 | not run | 0% |
| 768 shared + 256 random | 1541 | 1172 / 1360 | 1482 | 0% on both versions |
| 2048 shared + 256 random | 1379 | 1379 | 1099 | hits |

The 1172 and 1360 are the same config run in two sessions on different
hosts. v0.26.0 stayed within about 1% across three sessions.

## Findings

1. On v0.26.0, turning prefix caching on costs almost nothing when no
   request hits the cache. On v0.31.0 the same no-hit case is 10 to 24%
   slower. The overhead appeared somewhere between 0.26 and 0.31.
2. The prompts in the no-hit rows are shorter than the 1056-token KV
   block, so nothing can hit. Once the shared prefix is long enough to
   hit (2048 tokens), v0.31.0 matches v0.26.0 and caching is 25% faster
   than no caching.
3. Median inter-token latency stayed at about 21 ms while mean time per
   output token rose from 25.0 to between 28.6 and 32.9 ms. That is
   consistent with per-step decode overhead.
4. `VLLM_USE_V2_MODEL_RUNNER=0` on v0.31.0 gave 1361 tok/s in the
   768-prefix case, about the same as the V2 runner in the second
   session, so the runner alone does not explain it.
5. `--prefix-match-unit 16` on v0.31.0 did not crash, but the hit rate
   for "same system prompt, different user message" stayed at 0% and
   throughput was unchanged (1388 vs 1360 tok/s).

## Posted upstream

Comments on 14 October 2026:

- [#60008](https://github.com/vllm-project/vllm/issues/60008): the main
  data. That thread already had a root-cause analysis and candidate
  fixes.
- [#51250](https://github.com/vllm-project/vllm/issues/51250): the
  `--prefix-match-unit` result.
- [#51663](https://github.com/vllm-project/vllm/issues/51663): could not
  reproduce.

## Limits of this study

- Three measured runs per cell. No confidence intervals were computed
  for the posted numbers.
- Every version ran in its own container, so every version comparison
  crossed hosts. The 1172 vs 1360 pair shows that alone can move the
  result by 16%.
- 0.26 to 0.31 was not bisected.

## What perfproof takes from it

- Interleave both versions on one host, or say loudly that a comparison
  crossed hosts.
- Fingerprint every machine.
- Measure the mechanism, not only throughput. The hit rate changed the
  conclusion.
- Keep the starting claim apart from what the data shows.
- Fail fast on startup errors and print the log tail. On paid GPUs this
  saved credit more than once.
