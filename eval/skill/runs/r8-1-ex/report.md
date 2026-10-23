# Skill eval `r8-1-ex`

Skill `d52555445b50`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 17 of 20
- Trigger set: 9 of 12 matched
- Verdict accepted: 8 of 8; wrong KEEP: 0
- Result line correct: 8 of 8
- Hotspot in a real `perfproof profile` output and named: top1 2 of 2
- Fix passes tests, no locked file touched (prove: no code changed): 8 of 8
- Tree clean: 8 of 8
- Wording violations: none
- Cost $2.05, 111 agent turns, 606s of `claude` wall time (572s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prove-rolling-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 8 | $0.16 | 88s | 10.8 |
| prove-rolling-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 8 | $0.16 | 46s | 7.7 |
| prove-rolling-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 8 | $0.16 | 30s | 9.2 |
| prove-membership-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 7 | $0.15 | 74s | 8.6 |
| prove-membership-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 9 | $0.16 | 47s | 7.7 |
| prove-membership-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 9 | $0.16 | 30s | 19.5 |
| find-rolling-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 13 | $0.34 | 129s | 15.5 |
| find-membership-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 13 | $0.32 | 96s | 8.3 |
| pos-make-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 5s | 9.2 |
| pos-why-slow | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 8.7 |
| pos-wrong-perf | FAIL | no start FAIL | - | - | - | - | - | - | 3 | $0.03 | 5s | 8.1 |
| pos-find-inefficiencies | FAIL | no start FAIL | - | - | - | - | - | - | 3 | $0.03 | 6s | 8.0 |
| pos-speed-up-fn | FAIL | no start FAIL | - | - | - | - | - | - | 3 | $0.03 | 6s | 7.5 |
| pos-profile | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 7.9 |
| pos-pr-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 7.7 |
| pos-verify-claim | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 7.8 |
| neg-typo | ok | no start | - | - | - | - | - | - | 3 | $0.02 | 4s | 7.6 |
| neg-add-test | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 5s | 7.2 |
| neg-explain | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 5s | 8.6 |
| neg-rename | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 5s | 8.1 |
