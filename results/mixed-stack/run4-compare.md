# Arm comparison

## mixed-python-pillow-pillow-pr10293-hidden-fix

| level | arm | found | correct | claims backed / unbacked | speedup (harness) | cost $ | wall s |
|---|---|---|---|---|---|---|---|
| vague | skill | no | yes | 0 / 0 | 1.00x [0.99, 1.01] no_difference (perfproof/pillow-speedups-point) | 0.82 | 293 |
| vague | baseline | no | n/a | 0 / 1 | unchanged | 0.11 | 27 |
| detailed | skill | yes | yes | 0 / 0 | 1.29x [1.22, 1.36] faster (perfproof/resize-speed) | 1.94 | 693 |
| detailed | baseline | no | n/a | 0 / 1 | unchanged | 0.12 | 32 |

## mixed-matplotlib-matplotlib-pr32064-hidden-fix

| level | arm | found | correct | claims backed / unbacked | speedup (harness) | cost $ | wall s |
|---|---|---|---|---|---|---|---|
| vague | skill | no | n/a | 0 / 0 | unchanged | 0.61 | 222 |
| vague | baseline | no | n/a | 0 / 0 | unchanged | 0.07 | 14 |
| detailed | skill | no | n/a | 0 / 0 | unchanged | 0.90 | 371 |
| detailed | baseline | no | n/a | 0 / 0 | unchanged | 0.20 | 88 |

## mixed-huggingface-tokenizers-pr1413-hidden-fix

| level | arm | found | correct | claims backed / unbacked | speedup (harness) | cost $ | wall s |
|---|---|---|---|---|---|---|---|
| vague | skill | no | yes | 0 / 0 | 1.03x [0.96, 1.05] no_difference (perfproof/bert-normalizer-guards) | 0.98 | 863 |
| vague | baseline | no | n/a | 0 / 1 | unchanged | 0.03 | 9 |
| detailed | skill | yes | n/a | 0 / 0 | 29.30x [26.90, 29.95] faster (working) | 0.84 | 373 |
| detailed | baseline | yes | yes | 0 / 1 | 15.61x [11.73, 22.28] faster (working) | 0.31 | 157 |

## mixed-openai-tiktoken-pr31-hidden-fix

| level | arm | found | correct | claims backed / unbacked | speedup (harness) | cost $ | wall s |
|---|---|---|---|---|---|---|---|
| vague | skill | not run | | | | | |
| vague | baseline | yes | yes | 0 / 2 | unknown: build failed in base tree (exit 71) | 0.24 | 70 |
| detailed | skill | yes | yes | 0 / 0 | 1.00x [0.98, 1.01] no_difference (perfproof/bpe-heap) | 0.88 | 399 |
| detailed | baseline | yes | yes | 0 / 0 | unknown: build failed in base tree (exit 71) | 0.21 | 116 |

## mixed-mitmproxy-mitmproxy-pr8055-hidden-fix

| level | arm | found | correct | claims backed / unbacked | speedup (harness) | cost $ | wall s |
|---|---|---|---|---|---|---|---|
| vague | skill | no | n/a | 0 / 0 | unchanged | 1.19 | 371 |
| vague | baseline | no | n/a | 0 / 0 | unchanged | 0.13 | 25 |
| detailed | skill | no | yes | 0 / 0 | 1.00x [0.99, 1.00] no_difference (perfproof/body-edit) | 1.13 | 672 |
| detailed | baseline | yes | yes | 0 / 0 | 9.88x [9.79, 9.97] faster (working) | 0.18 | 77 |

## Totals

| level | arm | tasks | found | correct | claims backed / unbacked | speedup checks | cost $ | wall s |
|---|---|---|---|---|---|---|---|---|
| vague | skill | 4 | 0/4 | 2/2 | 0 / 0 | 0 faster, 2 none, 0 slower, 2 unchanged, 0 unknown (median 1.02x) | 3.60 | 1749 |
| vague | baseline | 5 | 1/5 | 1/1 | 0 / 4 | 0 faster, 0 none, 0 slower, 4 unchanged, 1 unknown (median n/a) | 0.58 | 145 |
| detailed | skill | 5 | 3/5 | 3/3 | 0 / 0 | 2 faster, 2 none, 0 slower, 1 unchanged, 0 unknown (median 1.15x) | 5.69 | 2509 |
| detailed | baseline | 5 | 3/5 | 3/3 | 0 / 2 | 2 faster, 0 none, 0 slower, 2 unchanged, 1 unknown (median 12.75x) | 1.03 | 469 |

found: the known function named in the final answer or its file edited. correct: the tests pass on the final tree (n/a: nothing changed or not run). Claims are backed by a KEEP verdict (skill) or by timings of both versions the agent ran (baseline). Speedup: the harness's own A/B timing of the final tree against the base tree, ratio of medians with a 95% bootstrap interval.
