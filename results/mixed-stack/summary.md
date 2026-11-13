# Mixed-language repositories, perfproof skill against Claude Code alone

Source: four runs of the same 5 tasks, each a Python project over
native code (pillow, matplotlib, huggingface tokenizers, tiktoken,
mitmproxy), at the commit just before a known merged speedup. Two
prompt levels: **vague** ("speed up the app") and **detailed** (names
the workload). The skill arm ran 3 times (runs 1, 3 and 4) on a skill that changed
between runs; the baseline arm, the same agent without perfproof, ran
once. The harness timed the final tree against the base tree itself.
Run locally through the eval harness.

This is weak evidence and is published as weak. It is 5 tasks, one run
per cell, and the skill changed between rounds.

Per-run tables: [run 1](run1-compare.md), [run 3](run3-compare.md),
[run 4](run4-compare.md) (run 2 only repeated the baseline arm, whose
rows are in each table). "Found" means the known function is named in
the final answer or edited in the final tree, as the harness scores it.

## Found the known function (of 5)

| Level | perfproof, run 1 | run 3 | run 4 | Claude Code alone |
|---|---|---|---|---|
| vague | 2 | 2 | 0 (of 4 run) | 1 |
| detailed | 4 | 4 | 3 | 3 |

(Run 2 was a baseline-only run, so it has no column.)

## Harness-measured speedup, detailed prompts

| Task | perfproof (runs 1, 3, 4) | Claude Code alone |
|---|---|---|
| pillow | 1.09x, 1.65x, 1.29x | no change |
| matplotlib | no change in all three | no change |
| tokenizers | 34.16x, 31.10x, 29.30x | 15.61x |
| tiktoken | 0.96x, 0.98x, 1.00x (no difference) | baseline build failed |
| mitmproxy | 10.68x, 10.30x, 1.00x | 9.88x |

The mitmproxy cell in run 4 is not a false KEEP. The agent found a
different real bug from the task's hidden one: `infer_content_encoding`
is quadratic on HTML bodies with many unclosed `<meta` tags. It fixed
it, checked the new code against the old on about 700,000 random
inputs, added a regression test, and every gate passed. The KEEP
(371x, interval 358 to 384) was on its own benchmark, a 64 KB body of
unclosed tags, so it is a worst case, not a typical page. The harness
timed the task's hidden bottleneck, which that change never touched,
hence 1.00x, and the task correctly scored "not found". The source is
[an excerpt of the run transcript](mitmproxy-run4-excerpt.md), which
includes the full verdict line.

## What the "claims unbacked" column is, and is not

The scorer counted statements in the final answer that were not backed
by a KEEP (skill arm) or by timings of both versions (baseline arm).
For the baseline arm on vague prompts it counted 4 statements across 3
of 5 runs. Two of those 4 are not speedup claims (a clarifying
question, and "already fast" about a recent commit). The skill arm was
also flagged: 1 statement in run 1 and 2 in run 3 on vague prompts.
That column is therefore not a false-claim rate and is not used as one.
A measured false-claim comparison is the job of the held-out
evaluation, which has not run.

## Cost

Mean per task, from the harness totals: vague, perfproof $0.89 (run 1)
against $0.12 alone; detailed, perfproof $1.22 (run 1) against $0.21
alone. perfproof costs several times more per task and takes longer.
