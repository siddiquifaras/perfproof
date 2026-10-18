# Performance PR study: 10 pull requests

Task T3.11, part A. How performance claims in real pull requests were
made, checked and accepted, as their threads and later commits show.
Read only: nothing was posted, reacted to or opened on these
repositories.

## How this was collected and checked

- A read-only subagent searched with `gh search prs` (searches for
  Claude Code, Copilot, Devin and Codex markers, `[Perf]`, `speedup`,
  `faster`, `Revert` and `performance` in vLLM, SGLang, llama.cpp,
  CPython, NumPy, pandas, Django, NetBox, FastAPI, Rails, onnxruntime
  and others). It scanned about 300 hits by title and comment count,
  read about 25 threads in full (`gh pr view --comments`, and `gh api`
  for review comments, reviews and commits), and kept 10. It dropped
  PRs with no maintainer reply, such as an SGLang hashing PR written by
  Claude Code that claimed a 7.7x speedup, got no review and was closed
  by an idle bot ([sgl-project/sglang#23913][sglang-23913]).
- Checks were run with a throwaway script (not committed) on 2026-10-16, all with `gh` GETs:
  - **Metadata:** author, state, and created, merged and closed
    timestamps for all 10 PRs and both reverts all match the table.
  - **Quotes and numbers:** 107 checks of a phrase or number against
    the comment it is cited from, covering every quote. 104 matched as
    fetched; the other 3 were read by hand, and two wordings here were
    corrected to what the comment says.
  - **Agent trailers:** a Claude `Co-Authored-By` line is on 8 of 8
    commits of vllm#58737 and 26 commits of pandas#64347.
- Not checked: the failure-mode mapping is the collector's judgement
  from the thread, reviewed here but not re-derived line by line.
  Screenshots and charts in threads were not looked at. Revert searches
  were keyword searches, so a regression report that names neither the
  PR nor its subject would be missed.
- Days to decision is the merge or close time minus the creation time.
- Each PR has exactly one direct quote. Everything else is paraphrase
  with a link. Numbers are as posted in the thread; none were re-run.
- "Agent-written" means an agent account opened the PR, or the body or
  commits say an agent wrote it. "Agent-assisted" means the author
  disclosed that an agent helped.
- Failure numbers refer to [docs/failure-modes.md](../../docs/failure-modes.md).

## Summary

| # | PR | Class | Agent | Outcome | Days | Claimed | What was true | Evidence asked for | Failures |
|---|---|---|---|---|---|---|---|---|---|
| P1 | [pandas#64347][p1] | Python | written (Claude) | merged | 130.7 | 2–3x on large CSVs, one Mac, 10 runs, mean ± std | Reproduced on Linux after one reviewer's slowdown was traced to an unoptimized build; slower on Windows; row-loss and divergence bugs found before and after merge | Thread scaling, a way to cap threads, cold-cache timing, smaller tests | 4, 8, 10, 13, 17 |
| P2 | [pandas#66238][p2] | Python (C ext) | written (Claude Code) | closed, replaced | 17.8 | Median 1.09x over 123 cases, alternating A/B, null control, 99% CIs | Never reproduced by a maintainer; the same variant's loss read 4–8% one day and 5–13% the next; closed over maintainability | Use the library's public API; compile as C++; is regex faster | 1, 3, 18 |
| P3 | [cpython#118144][p3] | Python | no | merged | 319.4 | 3–5x I/O | Gain was for writes; reads under 10% by the author's own later account; no core dev posted numbers | Local-disk read numbers, other benchmarks, a memory cap | 13, 17, 20 |
| P4 | [llama.cpp#29182][p4] | native | assisted | merged, reverted after 5.8 days | 9.1 | +10% prefill, one iGPU, one model | About −10.6% and −12% on two AMD cards and about −52% and −28% on an Intel card; revert restored speed | A reviewer asked for more evidence; not answered before merge | 17, 20 |
| P5 | [onnxruntime#32301][p5] | native | written (Copilot agent) | merged | 4.2 | Removes a ~3,500x Windows regression | No timing after the fix anywhere; first tests could not fail; description wrong | Positive controls, an end-to-end test, a correct description | 11, 17, 20 |
| P6 | [netbox#21954][p6] | web | no | closed, replaced | 52.0 | Prefix list 5+ min to 2–3 s; detail page under 1 s | First paint got faster because work was deferred; full page still ~1.2 min; a caching change risked stale data | Address each review point; keep freshness semantics | 9, 10, 20 |
| P7 | [django#22141][p7] | web | assisted (Claude Code) | merged | 1.2 | 321 to 292 ms and 672 to 603 ms prefetch (about 9% and 10%), SQLite in memory only | Not reproduced in thread; maintainer found and fixed a pessimization; project benchmark had not run after merge | asv impact; a benchmark with thousands of objects | 17, 20 |
| P8 | [vllm#58737][p8] | LLM serving | written (Claude) | merged | 20.0 | 6x on a kernel (10.81 to 1.78 µs); explicitly no serving claim | Outside measurement ~1.5–1.9% end to end; stride, overflow and contract bugs; a test passed with the mask deleted | An end-to-end number; bit-identical tests incl. empty cases | 10, 11, 13, 20 |
| P9 | [vllm#48223][p9] | LLM serving | no | merged, reverted after 1.1 days, relanded | 32.7 | 3–4% better TPOT, one run per point | A later run flipped the sign at low concurrency; a race hurt accuracy; reverted | Low-concurrency data; recheck after rebase; Nvidia overlap | 1, 8, 10, 17 |
| P10 | [vllm#29957][p10] | LLM serving | no | closed, replaced | 110.8 | ~2% TPOT; bubbles gone in Nsight | Gain smaller without the profiler; supporting run had the profiler on and showed TTFT up 32%, unremarked | Any end-to-end benchmark; when it helps or hurts | 1, 8, 9, 13, 17 |

Agent involvement: 6 of 10 (P1, P2, P5, P8 written by an agent; P4, P7
assisted). Outcomes: 7 merged, of which 2 were reverted (P4, P9), and
3 closed in favour of another PR (P2, P6, P10). Classes: 3 Python,
2 native, 2 web, 3 LLM serving.

## P1. pandas#64347: parallelize `read_csv`

- **Who:** a pandas maintainer. The body says Claude wrote it and the
  author reviewed it, and the substantive commits carry Claude
  co-author trailers ([body][p1],
  [commit](https://github.com/pandas-dev/pandas/pull/64347/commits/7dbd7b91a930fd7c630a2c5a8c395b7815c36fbf)).
- **Decided:** merged 2026-07-09 after 130.7 days.
- **Claimed:** serial versus parallel inside the same build, for
  example a 2M-row float file from 499 ± 6 ms to 187 ± 37 ms, 2–3x
  where the parallel path engages, on a 12-core Apple Silicon machine,
  mean ± std over 10 runs ([body][p1]). The baseline is the branch with
  one thread, not `main`.
- **What was true:**
  - A reviewer measured a slowdown on Linux
    ([comment](https://github.com/pandas-dev/pandas/pull/64347#issuecomment-4421679193)),
    later traced to his own build being unoptimized, after which he
    saw about 3.5x
    ([comment](https://github.com/pandas-dev/pandas/pull/64347#issuecomment-4497028820)).
  - A second reviewer reproduced it on 16-core Linux (563 ms at one
    thread, 175 ms at 8, 272 ms at 32) and warned that `timeit` reads a
    warm page cache
    ([comment](https://github.com/pandas-dev/pandas/pull/64347#issuecomment-4470752982)).
  - On Windows it was slower at two threads and ran out of memory at 12
    threads on a larger file
    ([comment](https://github.com/pandas-dev/pandas/pull/64347#issuecomment-4468820601)).
    The default on Windows was set to serial
    ([commit](https://github.com/pandas-dev/pandas/pull/64347/commits/2b913d18b854165b751ab51e1d2d93710e48b439)).
  - Correctness: serial and parallel results diverged in several cases
    found in review
    ([comment](https://github.com/pandas-dev/pandas/pull/64347#issuecomment-4916130562)),
    and more after merge, including a row lost or added after a bare CR
    in the header ([issue](https://github.com/pandas-dev/pandas/issues/66259),
    [fix](https://github.com/pandas-dev/pandas/pull/70403)).
- **Evidence asked for:** scaling by thread count before defaulting to
  all cores
  ([comment](https://github.com/pandas-dev/pandas/pull/64347#issuecomment-4421694601)),
  a way to cap threads
  ([comment](https://github.com/pandas-dev/pandas/pull/64347#issuecomment-4040997980)),
  awareness that the CPU count can overstate what a process may use
  ([comment](https://github.com/pandas-dev/pandas/pull/64347#issuecomment-4672611183)).
- **Why decided:** the author asked for merge or close after four
  months
  ([comment](https://github.com/pandas-dev/pandas/pull/64347#issuecomment-4836285512));
  approving reviewers said they had looked only lightly at the
  implementation
  ([review](https://github.com/pandas-dev/pandas/pull/64347#pullrequestreview-4415393643)).
- **Quote:** "So it is not actually speeding up, but rather slowing
  down?" ([jorisvandenbossche](https://github.com/pandas-dev/pandas/pull/64347#issuecomment-4421679193))
- **Failures:** 8 (one side built differently), 4 (Mac result did not
  carry to Windows), 13 (memory at high thread counts), 17 (warm cache),
  10 (divergent results, some found only after merge).

## P2. pandas#66238: `fast_float` for integer parsing in the C engine

- **Who:** the same maintainer; the body ends with a Claude Code
  footer ([body][p2]).
- **Decided:** closed after 17.8 days in favour of
  [pandas#66459](https://github.com/pandas-dev/pandas/pull/66459).
- **Claimed:** 123 `read_csv` cases on an M3 Pro, alternating order,
  adaptive rounds to a ±0.75% target, 99% confidence intervals, a null
  control of two identical builds, 77 significant wins, no regressions,
  median 1.09x ([body][p2]). This is the most careful claim in the set.
- **What was true:**
  - No maintainer posted independent numbers.
  - The author reported the same variant's loss at 4–8%, then 5–13% on
    another day
    ([comment](https://github.com/pandas-dev/pandas/pull/66238#discussion_r3582870055)).
  - An explanation of a 1.5–3.7% gap by a C/C++ boundary was disputed
    by the reviewer
    ([comment](https://github.com/pandas-dev/pandas/pull/66238#discussion_r3580450691)).
  - With only the public library API the gains fell from 1.2–1.41x to
    1.05–1.31x
    ([comment](https://github.com/pandas-dev/pandas/pull/66238#issuecomment-4985364039)).
- **Evidence asked for:** use the library directly
  ([comment](https://github.com/pandas-dev/pandas/pull/66238#discussion_r3565227795)),
  a coherent plan rather than variant after variant
  ([review](https://github.com/pandas-dev/pandas/pull/66238#pullrequestreview-4704954003)),
  whether a simpler regex is faster
  ([comment](https://github.com/pandas-dev/pandas/pull/66238#discussion_r3595500185)).
- **Why decided:** maintainability, not measurement.
- **Quote:** "vendoring code should not be a performance solution"
  ([WillAyd](https://github.com/pandas-dev/pandas/pull/66238#issuecomment-4983988328))
- **Failures:** partial fits only. 3 (several implementations measured,
  the fastest pushed, though a null control was run), 1 (day-to-day
  drift of several percent), 18 (a mechanism asserted, contested).

## P3. cpython#118144: raise `io.DEFAULT_BUFFER_SIZE` to 128 KiB

- **Who:** a contributor; no agent marker.
- **Decided:** merged 2025-03-07 after 319.4 days.
- **Claimed:** 3–5x I/O performance on modern hardware ([body][p3]),
  from a download-and-write script on Python 3.11 with charts in the
  linked issue
  ([issue](https://github.com/python/cpython/issues/117151#issuecomment-2014910720)).
  No CPU, run count or variance stated.
- **What was true:** asked about reads, the author said larger reads
  gained less than 10% and the 3–5x was for writes
  ([comment](https://github.com/python/cpython/pull/118144#issuecomment-2213862435)).
  One developer confirmed an improvement without numbers
  ([comment](https://github.com/python/cpython/issues/117151#issuecomment-2066617535)).
  No core developer posted before and after numbers.
- **Evidence asked for:** local-disk read numbers for small and large
  files, other benchmarks
  ([comment](https://github.com/python/cpython/pull/118144#issuecomment-2085012437)),
  and a cap on buffer allocation; the author added an 8 MB cap
  ([comment](https://github.com/python/cpython/pull/118144#issuecomment-2637717433)).
- **Why decided:** reviewers accepted the reasoning and the issue's
  charts ([review](https://github.com/python/cpython/pull/118144#pullrequestreview-2665922624)).
- **Quote:** "do you have any performance numbers around local disk
  `read()` with this change?"
  ([cmaloney](https://github.com/python/cpython/pull/118144#issuecomment-2195977598))
- **Failures:** 20 (a write result headlined as I/O), 17 (a network
  download script on unstated machines), 13 (memory cap required).

## P4. llama.cpp#29182: MoE-aware tile selection for Vulkan

- **Who:** a contributor who disclosed help from a coding agent
  (opencode) ([body][p4]).
- **Decided:** merged after 9.1 days, reverted 5.8 days later by
  [llama.cpp#29936](https://github.com/ggml-org/llama.cpp/pull/29936).
- **Claimed:** prefill 384 to about 421 tokens/s (+10%) for one 30B MoE
  model on one AMD integrated GPU; byte-identical greedy output; math
  tests pass. No run count or variance ([body][p4]).
- **What was true:**
  - Before merge a contributor saw +14.6% at pp128 (the best result
    across quantizations) but −6.0% for q5_0 at pp512 on an Nvidia
    card
    ([comment](https://github.com/ggml-org/llama.cpp/pull/29182#issuecomment-5836678479)).
  - After merge: −10.6% prefill on an RDNA2 card, with interleaved
    runs and the revert restoring speed
    ([comment](https://github.com/ggml-org/llama.cpp/pull/29182#issuecomment-5915731590));
    about −12% on RDNA4
    ([issue](https://github.com/ggml-org/llama.cpp/issues/29892));
    an Intel Arc regression found by bisect, about −52% with flash
    attention off (3147 to 1526 tokens/s) and −28% with it on (1230 to
    880) in the revert body ([revert body](https://github.com/ggml-org/llama.cpp/pull/29936));
    and on the same iGPU family with another model, 1499.66 before
    against 1408.45 tokens/s after, with the revert at 1496.99
    ([comment](https://github.com/ggml-org/llama.cpp/pull/29936#issuecomment-5984846512)).
- **Evidence asked for:** one request for more evidence before merge,
  unanswered; the approval carried no text
  ([review](https://github.com/ggml-org/llama.cpp/pull/29182#pullrequestreview-5338511857)).
- **Why decided:** the merge reason was not stated; reverted for
  cross-vendor slowdowns
  ([comment](https://github.com/ggml-org/llama.cpp/pull/29936#issuecomment-5982742707)).
- **Quote:** "I think a bit more evidence and discussion would be
  useful" ([SG-Amadeus](https://github.com/ggml-org/llama.cpp/pull/29182#issuecomment-5836678479))
- **Failures:** 17 (one device, one model), 20 (applied to every Vulkan
  device).

## P5. onnxruntime#32301: gate FP16 GEMM on hardware support

- **Who:** opened by the `copilot-swe-agent` account after a maintainer
  started a Copilot session on the issue
  ([issue comment](https://github.com/microsoft/onnxruntime/issues/32255#issuecomment-5445399704)).
- **Decided:** merged after 4.2 days. It fixes a regression from
  [onnxruntime#29709](https://github.com/microsoft/onnxruntime/pull/29709).
- **Claimed:** removes an approximately 3,500x Windows regression
  ([body][p5]). The numbers come from the issue: 0.121 ms on 1.28
  against 430.339 ms on 1.29, warmed-up mean, CPU not stated
  ([issue](https://github.com/microsoft/onnxruntime/issues/32255)). The
  PR gives no timing after the fix.
- **What was true:**
  - Nobody posted a timing after the fix in the PR or the issue.
  - The first tests asserted only negatives, so an empty registry
    would pass; positive controls and end-to-end tests were added after
    review
    ([review](https://github.com/microsoft/onnxruntime/pull/32301#pullrequestreview-5056092125),
    [reply](https://github.com/microsoft/onnxruntime/pull/32301#issuecomment-5459454881)).
  - The description claimed to preserve a RISC-V behaviour that never
    existed, which the agent conceded
    ([comment](https://github.com/microsoft/onnxruntime/pull/32301#discussion_r3877068011)).
  - A CI fix by the agent did not change the failing job; the second
    silently changed minimal-build behaviour; a maintainer dictated the
    final fix, and the agent said targeted tests were not rerun locally
    ([comment](https://github.com/microsoft/onnxruntime/pull/32301#issuecomment-5483461876)).
- **Evidence asked for:** positive controls, an end-to-end test of the
  regressed path, binary size and test results, a correct description
  ([comment](https://github.com/microsoft/onnxruntime/pull/32301#issuecomment-5483372714)).
  Nobody asked for a latency number.
- **Quote:** "all four assertions are `false`, so an empty registry
  would also pass" ([tianleiwu](https://github.com/microsoft/onnxruntime/pull/32301#pullrequestreview-5056092125))
- **Failures:** 11 (tests that could not fail), 20 (speed fix claimed
  with no timing), 17 (the regressing PR shipped a path no benchmark
  covered; inference from its body).

## P6. netbox#21954: optimize the prefix view

- **Who:** a contributor; Codex was used only for impact analysis
  ([comment](https://github.com/netbox-community/netbox/pull/21954#issuecomment-4285146850)).
- **Decided:** closed after 52.0 days in favour of maintainer PR
  [netbox#22211](https://github.com/netbox-community/netbox/pull/22211).
- **Claimed:** the prefix list from more than 5 minutes to 2–3 seconds,
  the detail page's first load under 1 second, from screenshots
  ([comment](https://github.com/netbox-community/netbox/pull/21954#issuecomment-4268988556)).
  Dataset from the issue: 32,769 prefixes and 1,000,000 addresses, with
  a py-spy profile ([issue](https://github.com/netbox-community/netbox/issues/21870)).
  Machine and run count not stated.
- **What was true:** the reviewer found that most of the visible win
  came from deferring work behind a lazy load, with the full page still
  slow; a cached property risked stale results and was reverted
  ([review](https://github.com/netbox-community/netbox/pull/21954#pullrequestreview-4156682579),
  [commit](https://github.com/netbox-community/netbox/pull/21954/commits/d36a83066a2a50c3dc79235d57f15889013919b5)).
  The replacement reported `get_utilization()` from about 1429 ms to
  13.9 ms with identical results
  ([comment](https://github.com/netbox-community/netbox/pull/22211#issuecomment-4689879554)).
- **Evidence asked for:** answers to each automated review point;
  keeping freshness semantics; lazy loading as a reusable capability.
- **Why decided:** the approach did not address the underlying cost
  ([comment](https://github.com/netbox-community/netbox/pull/21954#issuecomment-4650461638)).
- **Quote:** "the full page still takes a long time to finish loading"
  ([pheus](https://github.com/netbox-community/netbox/pull/21954#pullrequestreview-4156682579))
- **Failures:** 9 (time to first paint moved, total time did not), 20,
  10 (a risk only, removed before close).

## P7. django#22141: cheaper many-to-many related managers

- **Who:** a contributor who disclosed that Claude Code profiled,
  drafted the change and the test, and ran the benchmarks ([body][p7]).
  A maintainer added a commit.
- **Decided:** merged after 1.2 days.
- **Claimed:** on SQLite in memory, median of repeated runs, prefetch
  over 2,000 authors from about 321 to 292 ms and over 20,000 from 672
  to 603 ms; other databases not run ([body][p7]). Machine and run
  count not stated.
- **What was true:** not reproduced in the thread; a maintainer found a
  pessimization for composite fields and changed it
  ([comment](https://github.com/django/django/pull/22141#discussion_r4252568599)),
  and added a large-prefetch benchmark to django-asv
  ([django-asv#103](https://github.com/django/django-asv/pull/103)).
  After merge the author noted the project benchmark had not yet run
  ([comment](https://github.com/django/django/pull/22141#issuecomment-6207907027)).
- **Evidence asked for:** the effect on the existing asv benchmark
  ([comment](https://github.com/django/django/pull/22141#issuecomment-6135994404)),
  a benchmark at scale. A maintainer also triggered the Oracle CI
  build ([comment](https://github.com/django/django/pull/22141#issuecomment-6133645738)).
- **Why decided:** small and well scoped, co-written by the maintainer
  ([review](https://github.com/django/django/pull/22141#pullrequestreview-5496531321)).
  Merged two days before collection, so a later revert can't be ruled
  out.
- **Quote:** "we'll need a bench that ... prefetches managers for
  thousands of objects" ([jacobtylerwalls](https://github.com/django/django/pull/22141#issuecomment-6137147469))
- **Failures:** 17 (one database, in memory), 20 (mild: merged on the
  author's numbers).

## P8. vllm#58737: fuse a Mamba block-table gather into one kernel

- **Who:** a contributor; all 8 commits carry a Claude co-author
  trailer ([commit](https://github.com/vllm-project/vllm/pull/58737/commits/fc0d8b11395a68d33f1a3ed3ba1cc71b582d55d0)).
- **Decided:** merged 2026-10-15 after 20.0 days.
- **Claimed:** title says 6x; body gives 10.81 to 1.78 µs for the
  kernel on an MI355X and states that no serving improvement is
  claimed, with run-to-run variation about 1% ([body][p8]).
- **What was true:**
  - An independent measurement on B200 found about 1.5–1.9% lower time
    per iteration in one configuration, 3 runs per arm
    ([comment](https://github.com/vllm-project/vllm/pull/58737#issuecomment-5999376222)).
  - Reviewers found a stride assumption that read wrong slots
    ([comment](https://github.com/vllm-project/vllm/pull/58737#issuecomment-5840329106)),
    an int32 index overflow that faults on large tables
    ([comment](https://github.com/vllm-project/vllm/pull/58737#discussion_r4253913713)),
    a mid-step JIT recompile costing 25–28 ms cold
    ([comment](https://github.com/vllm-project/vllm/pull/58737#discussion_r4253909242)),
    and a mask whose deletion still passed all 64 tests
    ([comment](https://github.com/vllm-project/vllm/pull/58737#discussion_r4253929310)).
    The author fixed them and showed the widened tests fail on that
    mutant
    ([comment](https://github.com/vllm-project/vllm/pull/58737#issuecomment-6137604420)).
- **Evidence asked for:** an end-to-end number; bit-identical tests
  including empty inputs; the overflow, alignment and contract checks.
- **Why decided:** approved after the fixes
  ([review](https://github.com/vllm-project/vllm/pull/58737#pullrequestreview-5521133050)).
- **Quote:** "The 6× figure is a kernel microbenchmark ... There is no
  end-to-end number." ([ChuanLi1101](https://github.com/vllm-project/vllm/pull/58737#issuecomment-5840329106))
- **Failures:** 20 (6x in the title, ~2% end to end), 10, 11 (tests
  that passed with the guard removed), 13 (cold recompile latency).

## P9. vllm#48223: dual-stream decode on ROCm

- **Who:** a contributor; no agent marker.
- **Decided:** merged after 32.7 days, reverted 1.1 days later by
  [vllm#52024](https://github.com/vllm-project/vllm/pull/52024),
  relanded as [vllm#52033](https://github.com/vllm-project/vllm/pull/52033).
- **Claimed:** about 3–4% better time per output token on 8x MI300
  with DeepSeek-V3 and data parallelism, against a nightly Docker image;
  one row per configuration, and the author noted TTFT varied a lot
  ([body][p9]).
- **What was true:** with tensor parallelism it was worse
  ([comment](https://github.com/vllm-project/vllm/pull/48223#issuecomment-4993855510));
  a later run at concurrency 1, 2 and 4 showed −3.09%, −2.51% and
  −4.87% where the body had shown gains, against a different baseline
  ([comment](https://github.com/vllm-project/vllm/pull/48223#issuecomment-5001713688)).
  A race on a shared tensor hurt accuracy on another model and CI broke;
  that caused the revert ([reland body](https://github.com/vllm-project/vllm/pull/52033)).
- **Evidence asked for:** data at low concurrency, a recheck after a
  rebase, confirmation of overlap on Nvidia.
- **Quote:** "It seems smaller concurrencies might have large
  degradation" ([tjtanaa](https://github.com/vllm-project/vllm/pull/48223#issuecomment-4953667957))
- **Failures:** 1 (single runs; the sign flipped), 8 (baseline a nightly
  image, not the same tree), 10 (race), 17 (gain only in one parallel
  mode).

## P10. vllm#29957: zero-bubble async speculative decoding

- **Who:** a contributor; a Codex review bot commented but did not
  author.
- **Decided:** closed after 110.8 days in favour of
  [vllm#32951](https://github.com/vllm-project/vllm/pull/32951), which
  reported about 3% on different hardware and model.
- **Claimed:** mean TPOT 1.018–1.026x across concurrencies on H20 with
  a 235B model, accuracy unchanged, Nsight traces showing the bubbles
  removed; runs per point and variance not stated ([body][p10]).
- **What was true:** the author found the gain smaller than the traces
  suggested, then offered a run with the profiler on (8 prompts) where
  TPOT improved but mean TTFT rose from 263.89 to 347.83 ms, which
  nobody discussed
  ([comment](https://github.com/vllm-project/vllm/pull/29957#issuecomment-3610836640)).
  A maintainer found CPU-side sequence lengths left stale during the
  forward pass
  ([comment](https://github.com/vllm-project/vllm/pull/29957#issuecomment-3662049528)).
- **Evidence asked for:** an end-to-end benchmark
  ([comment](https://github.com/vllm-project/vllm/pull/29957#issuecomment-3609178511));
  when the mode helps or hurts, to choose a default
  ([comment](https://github.com/vllm-project/vllm/pull/29957#issuecomment-3756348391)).
- **Why decided:** maintainers preferred a refactor with the GPU as
  source of truth.
- **Quote:** "the actual perf improvement isn't as large as shown in
  Nsight Systems" ([izhuhaoran](https://github.com/vllm-project/vllm/pull/29957#issuecomment-3610062272))
- **Failures:** 8 (measured under a profiler), 9 (trace metric
  overstated wall time), 1, 13 (TTFT up), 17 (8 prompts).

[sglang-23913]: https://github.com/sgl-project/sglang/pull/23913
[p1]: https://github.com/pandas-dev/pandas/pull/64347
[p2]: https://github.com/pandas-dev/pandas/pull/66238
[p3]: https://github.com/python/cpython/pull/118144
[p4]: https://github.com/ggml-org/llama.cpp/pull/29182
[p5]: https://github.com/microsoft/onnxruntime/pull/32301
[p6]: https://github.com/netbox-community/netbox/pull/21954
[p7]: https://github.com/django/django/pull/22141
[p8]: https://github.com/vllm-project/vllm/pull/58737
[p9]: https://github.com/vllm-project/vllm/pull/48223
[p10]: https://github.com/vllm-project/vllm/pull/29957
