# Arm comparison

## mixed-python-pillow-pillow-pr10293-hidden-fix

| level | arm | found | correct | claims backed / unbacked | speedup (harness) | cost $ | wall s |
|---|---|---|---|---|---|---|---|
| vague | skill | yes | yes | 0 / 1 | 1.00x [1.00, 1.01] no_difference (perfproof/rank3) | 0.93 | 291 |
| vague | baseline | no | n/a | 0 / 1 | unchanged | 0.11 | 27 |
| detailed | skill | yes | yes | 0 / 0 | 1.65x [1.63, 1.69] faster (perfproof/resize) | 0.19 | 2462 |
| detailed | baseline | no | n/a | 0 / 1 | unchanged | 0.12 | 32 |

## mixed-matplotlib-matplotlib-pr32064-hidden-fix

| level | arm | found | correct | claims backed / unbacked | speedup (harness) | cost $ | wall s |
|---|---|---|---|---|---|---|---|
| vague | skill | no | n/a | 0 / 0 | unchanged | 0.87 | 432 |
| vague | baseline | no | n/a | 0 / 0 | unchanged | 0.07 | 14 |
| detailed | skill | no | n/a | 0 / 1 | unchanged | 0.90 | 308 |
| detailed | baseline | no | n/a | 0 / 0 | unchanged | 0.20 | 88 |

## mixed-huggingface-tokenizers-pr1413-hidden-fix

| level | arm | found | correct | claims backed / unbacked | speedup (harness) | cost $ | wall s |
|---|---|---|---|---|---|---|---|
| vague | skill | no | yes | 0 / 0 | 1.00x [0.99, 1.00] no_difference (perfproof/speedups) | 0.91 | 423 |
| vague | baseline | no | n/a | 0 / 1 | unchanged | 0.03 | 9 |
| detailed | skill | yes | yes | 0 / 0 | 31.10x [30.62, 31.36] faster (perfproof/metaspace-split) | 1.06 | 329 |
| detailed | baseline | yes | yes | 0 / 1 | 15.61x [11.73, 22.28] faster (working) | 0.31 | 157 |

## mixed-openai-tiktoken-pr31-hidden-fix

| level | arm | found | correct | claims backed / unbacked | speedup (harness) | cost $ | wall s |
|---|---|---|---|---|---|---|---|
| vague | skill | yes | yes | 0 / 1 | 1.25x [1.23, 1.29] faster (perfproof/bpe-merge) | 0.95 | 473 |
| vague | baseline | yes | yes | 0 / 2 | unknown: build failed in base tree (exit 71) | 0.24 | 70 |
| detailed | skill | yes | yes | 0 / 0 | 0.98x [0.98, 1.01] no_difference (perfproof/tokenize-speed) | 1.10 | 349 |
| detailed | baseline | yes | yes | 0 / 0 | unknown: build failed in base tree (exit 71) | 0.21 | 116 |

## mixed-mitmproxy-mitmproxy-pr8055-hidden-fix

| level | arm | found | correct | claims backed / unbacked | speedup (harness) | cost $ | wall s |
|---|---|---|---|---|---|---|---|
| vague | skill | no | n/a | 0 / 0 | unchanged | 0.61 | 182 |
| vague | baseline | no | n/a | 0 / 0 | unchanged | 0.13 | 25 |
| detailed | skill | yes | yes | 0 / 0 | 10.30x [10.20, 10.41] faster (perfproof/body-edit) | 1.43 | 683 |
| detailed | baseline | yes | yes | 0 / 0 | 9.88x [9.79, 9.97] faster (working) | 0.18 | 77 |

## Totals

| level | arm | tasks | found | correct | claims backed / unbacked | speedup checks | cost $ | wall s |
|---|---|---|---|---|---|---|---|---|
| vague | skill | 5 | 2/5 | 3/3 | 0 / 2 | 1 faster, 2 none, 0 slower, 2 unchanged, 0 unknown (median 1.00x) | 4.28 | 1801 |
| vague | baseline | 5 | 1/5 | 1/1 | 0 / 4 | 0 faster, 0 none, 0 slower, 4 unchanged, 1 unknown (median n/a) | 0.58 | 145 |
| detailed | skill | 5 | 4/5 | 4/4 | 0 / 1 | 3 faster, 1 none, 0 slower, 1 unchanged, 0 unknown (median 5.97x) | 4.68 | 4130 |
| detailed | baseline | 5 | 3/5 | 3/3 | 0 / 2 | 2 faster, 0 none, 0 slower, 2 unchanged, 1 unknown (median 12.75x) | 1.03 | 469 |

found: the known function named in the final answer or its file edited. correct: the tests pass on the final tree (n/a: nothing changed or not run). Claims are backed by a KEEP verdict (skill) or by timings of both versions the agent ran (baseline). Speedup: the harness's own A/B timing of the final tree against the base tree, ratio of medians with a 95% bootstrap interval.
