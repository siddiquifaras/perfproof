<!--
Three comments, each between the ===== lines. Paste the part between the lines into GitHub as is (it's markdown).
Order: #60008 first, then #51250, then #51663.
Gist: https://gist.github.com/siddiquifaras/16543eddb933800734674debe11f5573
-->

===== https://github.com/vllm-project/vllm/issues/60008 =====

Adding a data point from a GDN hybrid in case it's useful. Same "PC on, nothing hits, still slower" pattern, but on **Qwen3.6-35B-A3B-FP8**, a **single A100 80GB, TP=1, no DP/EP**, so it's not only the DP all2all making the host overhead visible.

Official `vllm/vllm-openai` images, `vllm bench serve` random dataset, 512 out with `--ignore-eos`, concurrency 40, 400 prompts, 1 warmup + 3 runs. Hit rate is from `/metrics` before/after each run. On A100 the MoE goes through Marlin. Script: https://gist.github.com/siddiquifaras/16543eddb933800734674debe11f5573

Output tok/s (median of 3):

| prompt | v0.26.0, PC on | v0.31.0, PC on | v0.31.0, PC off | PC hit rate |
|---|---|---|---|---|
| random 1024 | 1521 | 1333 | – | 0% |
| 768 shared + 256 random | 1541 | 1172 / 1360 * | 1482 | 0% on both versions |
| 2048 shared + 256 random | 1379 | 1379 | 1099 | hits (1056-token block) |

\* same config in two sessions on different hosts. v0.26.0 stayed within ~1% across three sessions.

What I think is new vs the thread:

- On v0.26.0, PC on with 0% hits costs basically nothing (it lands within a few % of v0.31.0 with PC off). On v0.31.0 the same no-hit case is 10-24% slower. So for this model the overhead showed up somewhere between 0.26 and 0.31.
- The prefill split shouldn't be the main factor here, since GDN already has the in-prefill checkpoint path and these prompts are under one block. Median ITL stays at ~21 ms and only mean TPOT goes up (25.0 -> 28.6-32.9 ms), which matches the per-step decode overhead described above.
- `VLLM_USE_V2_MODEL_RUNNER=0` on v0.31.0 gave 1361 tok/s in the 768-prefix case, about the same as V2 in the second session, so I can't pin it on the runner.
- Once the prefix is long enough to actually hit, 0.31 matches 0.26 and PC is +25% over PC off. So this mostly hurts traffic with short or no shared prefixes.

I can run #58851 or the prototype branch on this setup (GDN, single A100) if that helps, or bisect 0.26 -> 0.31.

===== https://github.com/vllm-project/vllm/issues/51250 =====

Tried `--prefix-match-unit 16` on **v0.31.0** (official image) with **Qwen3.6-35B-A3B-FP8** on a single A100 80GB.

- No engine crash (so the `SlidingWindowManager` assertion above doesn't seem to apply to the 35B-A3B on 0.31).
- But for a "same system prompt, different user message" pattern (768 shared tokens + 256 random tokens per request, concurrency 40, 400 requests) the hit rate is still **0%** (`prefix_cache_hits` / `prefix_cache_queries` from `/metrics`), and throughput is unchanged (1388 vs 1360 tok/s without the flag).

My guess from the #46384 description is that the recurrent state only gets materialized at the end of each prompt, so `--prefix-match-unit` helps when a later request continues an earlier prompt (multi-turn), but not when requests only share a prefix that ends somewhere inside other prompts. Is that the intended behavior? If so it might be worth a line in the docs, since the shared system prompt case is probably the most common one.

Script: https://gist.github.com/siddiquifaras/16543eddb933800734674debe11f5573

===== https://github.com/vllm-project/vllm/issues/51663 =====

@wizche I tried to reproduce this on a single **A100 80GB** with the official images and your flags (including prefix caching on, since that's in your Helm values rather than `extraArgs`). I couldn't get the drop:

| prompt | v0.23.0 | v0.26.0 |
|---|---|---|
| random 1024 in / 512 out, concurrency 40 | 1487 tok/s | 1521 tok/s |
| 768 shared + 256 random | 1542 tok/s | 1549 tok/s |

So same as @matan-arkin's H100 result. Your GPU model and the `moe backend` line from both startup logs would still help narrow it down.

One thing I did find while testing: on **v0.31.0**, prefix caching costs this model 10-24% throughput when nothing actually hits the cache (prompts shorter than the 1056-token block), see #60008. If your system prompts are short, comparing PC on vs off might be worth a try.

Script if you want to run it on your setup: https://gist.github.com/siddiquifaras/16543eddb933800734674debe11f5573
