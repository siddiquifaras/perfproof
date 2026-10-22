# Skill eval `first`

Skill `6087f6fa28cf`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 18 of 20
- Trigger set: 12 of 12 matched
- Verdict accepted: 6 of 8; wrong KEEP: 0
- Result line correct: 7 of 8
- Hotspot in a real `perfproof profile` output and named: top1 2 of 2
- Fix passes tests, no locked file touched (prove: no code changed): 8 of 8
- Tree clean: 8 of 8
- Wording violations: result_line 1
- Cost $3.31, 118 agent turns, 580s of `claude` wall time (543s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prove-rolling-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 8 | $0.22 | 74s | 7.5 |
| prove-rolling-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 11 | $0.26 | 47s | 11.7 |
| prove-rolling-bench-edit | FAIL | - | none FAIL | ok | - | ok | ok | ok | 7 | $0.22 | 26s | 11.1 |
| prove-membership-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 8 | $0.24 | 64s | 10.0 |
| prove-membership-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 11 | $0.28 | 54s | 9.4 |
| prove-membership-bench-edit | FAIL | - | none FAIL | FAIL | - | ok | ok | 1 FAIL | 7 | $0.21 | 33s | 7.3 |
| find-rolling-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 18 | $0.87 | 114s | 7.7 |
| find-membership-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 12 | $0.47 | 96s | 6.7 |
| pos-make-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 11.5 |
| pos-why-slow | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 11.2 |
| pos-wrong-perf | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 10.9 |
| pos-find-inefficiencies | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 10.4 |
| pos-speed-up-fn | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 9.8 |
| pos-profile | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 9.4 |
| pos-pr-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 10.6 |
| pos-verify-claim | ok | started | - | - | - | - | - | - | 3 | $0.05 | 8s | 11.3 |
| neg-typo | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 6s | 10.7 |
| neg-add-test | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 5s | 10.5 |
| neg-explain | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 6s | 10.2 |
| neg-rename | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 5s | 10.1 |

## Failures

- `prove-membership-bench-edit`
  - Result line: None
  - result_line: no Result line
