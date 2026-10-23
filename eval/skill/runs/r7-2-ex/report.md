# Skill eval `r7-2-ex`

Skill `bd7992eb865f`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 20 of 20
- Trigger set: 12 of 12 matched
- Verdict accepted: 8 of 8; wrong KEEP: 0
- Result line correct: 8 of 8
- Hotspot in a real `perfproof profile` output and named: top1 2 of 2
- Fix passes tests, no locked file touched (prove: no code changed): 8 of 8
- Tree clean: 8 of 8
- Wording violations: none
- Cost $2.06, 111 agent turns, 526s of `claude` wall time (490s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prove-rolling-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 8 | $0.16 | 70s | 9.2 |
| prove-rolling-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 8 | $0.15 | 36s | 5.5 |
| prove-rolling-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 9 | $0.15 | 25s | 8.0 |
| prove-membership-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 8 | $0.15 | 68s | 7.6 |
| prove-membership-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 9 | $0.15 | 37s | 7.2 |
| prove-membership-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 8 | $0.15 | 32s | 5.9 |
| find-rolling-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 14 | $0.36 | 94s | 5.5 |
| find-membership-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 11 | $0.29 | 94s | 5.4 |
| pos-make-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 4.3 |
| pos-why-slow | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 4.3 |
| pos-wrong-perf | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 4.2 |
| pos-find-inefficiencies | ok | started | - | - | - | - | - | - | 3 | $0.05 | 5s | 4.2 |
| pos-speed-up-fn | ok | started | - | - | - | - | - | - | 3 | $0.05 | 7s | 4.1 |
| pos-profile | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 4.0 |
| pos-pr-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 4.0 |
| pos-verify-claim | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 4.0 |
| neg-typo | ok | no start | - | - | - | - | - | - | 3 | $0.02 | 5s | 4.1 |
| neg-add-test | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 5s | 4.2 |
| neg-explain | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 6s | 4.1 |
| neg-rename | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 6s | 4.0 |
