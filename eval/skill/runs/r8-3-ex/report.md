# Skill eval `r8-3-ex`

Skill `c64ca63af438`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 19 of 20
- Trigger set: 12 of 12 matched
- Verdict accepted: 8 of 8; wrong KEEP: 0
- Result line correct: 8 of 8
- Hotspot in a real `perfproof profile` output and named: top1 2 of 2
- Fix passes tests, no locked file touched (prove: no code changed): 8 of 8
- Tree clean: 8 of 8
- Wording violations: direction_word 1
- Cost $2.13, 103 agent turns, 613s of `claude` wall time (578s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prove-rolling-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 7 | $0.14 | 82s | 11.2 |
| prove-rolling-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 8 | $0.15 | 51s | 16.6 |
| prove-rolling-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 7 | $0.16 | 29s | 14.1 |
| prove-membership-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 7 | $0.14 | 73s | 12.0 |
| prove-membership-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 7 | $0.15 | 45s | 9.5 |
| prove-membership-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 8 | $0.15 | 30s | 10.2 |
| find-rolling-light | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | 1 FAIL | 12 | $0.35 | 119s | 10.7 |
| find-membership-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 11 | $0.35 | 114s | 8.6 |
| pos-make-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 5s | 10.9 |
| pos-why-slow | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 10.2 |
| pos-wrong-perf | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 9.7 |
| pos-find-inefficiencies | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 9.1 |
| pos-speed-up-fn | ok | started | - | - | - | - | - | - | 3 | $0.06 | 8s | 8.9 |
| pos-profile | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 8.2 |
| pos-pr-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 8.1 |
| pos-verify-claim | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 7.5 |
| neg-typo | ok | no start | - | - | - | - | - | - | 3 | $0.02 | 6s | 7.2 |
| neg-add-test | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 5s | 6.8 |
| neg-explain | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 5s | 6.5 |
| neg-rename | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 5s | 6.3 |

## Failures

- `find-rolling-light`
  - direction_word: I'm not calling the estimate a gain.
