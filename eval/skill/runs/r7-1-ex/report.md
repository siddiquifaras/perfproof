# Skill eval `r7-1-ex`

Skill `9dab86db52ce`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 17 of 20
- Trigger set: 10 of 12 matched
- Verdict accepted: 8 of 8; wrong KEEP: 0
- Result line correct: 8 of 8
- Hotspot in a real `perfproof profile` output and named: top1 2 of 2
- Fix passes tests, no locked file touched (prove: no code changed): 8 of 8
- Tree clean: 8 of 8
- Wording violations: claim_comparison 1
- Cost $1.96, 106 agent turns, 528s of `claude` wall time (509s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prove-rolling-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 5 | $0.13 | 72s | 6.4 |
| prove-rolling-wrong-output | FAIL | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | 1 FAIL | 9 | $0.17 | 45s | 10.1 |
| prove-rolling-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 7 | $0.14 | 24s | 6.7 |
| prove-membership-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 10 | $0.17 | 67s | 5.6 |
| prove-membership-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 8 | $0.15 | 40s | 4.8 |
| prove-membership-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 7 | $0.14 | 21s | 4.2 |
| find-rolling-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 13 | $0.32 | 106s | 4.0 |
| find-membership-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 11 | $0.30 | 92s | 4.8 |
| pos-make-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 5s | 7.0 |
| pos-why-slow | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 6.5 |
| pos-wrong-perf | ok | started | - | - | - | - | - | - | 3 | $0.05 | 5s | 7.1 |
| pos-find-inefficiencies | FAIL | no start FAIL | - | - | - | - | - | - | 3 | $0.03 | 5s | 6.8 |
| pos-speed-up-fn | FAIL | no start FAIL | - | - | - | - | - | - | 3 | $0.03 | 4s | 6.4 |
| pos-profile | ok | started | - | - | - | - | - | - | 3 | $0.05 | 5s | 6.3 |
| pos-pr-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 5s | 6.0 |
| pos-verify-claim | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 6.0 |
| neg-typo | ok | no start | - | - | - | - | - | - | 3 | $0.02 | 4s | 5.9 |
| neg-add-test | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 6s | 5.7 |
| neg-explain | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 4s | 6.6 |
| neg-rename | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 5s | 6.3 |

## Failures

- `prove-rolling-wrong-output`
  - claim_comparison: The timing:** none was taken, so there is no estimate or speed claim.
