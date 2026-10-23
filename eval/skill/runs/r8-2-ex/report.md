# Skill eval `r8-2-ex`

Skill `7cbea51e4466`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 20 of 20
- Trigger set: 12 of 12 matched
- Verdict accepted: 8 of 8; wrong KEEP: 0
- Result line correct: 8 of 8
- Hotspot in a real `perfproof profile` output and named: top1 2 of 2
- Fix passes tests, no locked file touched (prove: no code changed): 8 of 8
- Tree clean: 8 of 8
- Wording violations: none
- Cost $2.14, 110 agent turns, 610s of `claude` wall time (572s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prove-rolling-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 10 | $0.18 | 103s | 16.9 |
| prove-rolling-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 9 | $0.16 | 43s | 13.0 |
| prove-rolling-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 7 | $0.14 | 24s | 10.7 |
| prove-membership-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 8 | $0.15 | 73s | 10.0 |
| prove-membership-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 8 | $0.16 | 42s | 11.5 |
| prove-membership-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 7 | $0.14 | 28s | 12.8 |
| find-rolling-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 15 | $0.38 | 135s | 13.4 |
| find-membership-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 10 | $0.31 | 87s | 10.3 |
| pos-make-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 10.0 |
| pos-why-slow | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 11.5 |
| pos-wrong-perf | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 11.2 |
| pos-find-inefficiencies | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 11.2 |
| pos-speed-up-fn | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 10.8 |
| pos-profile | ok | started | - | - | - | - | - | - | 3 | $0.05 | 7s | 10.1 |
| pos-pr-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 9.7 |
| pos-verify-claim | ok | started | - | - | - | - | - | - | 3 | $0.05 | 7s | 9.5 |
| neg-typo | ok | no start | - | - | - | - | - | - | 3 | $0.02 | 6s | 9.3 |
| neg-add-test | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 7s | 9.1 |
| neg-explain | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 5s | 9.3 |
| neg-rename | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 6s | 10.5 |
