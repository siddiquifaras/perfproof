# Skill eval `skill-3e`

Skill `6a89c12d4d34`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 20 of 20
- Trigger set: 12 of 12 matched
- Verdict accepted: 8 of 8; wrong KEEP: 0
- Result line correct: 8 of 8
- Hotspot ranked first by the profile and named: 2 of 2
- Fix passes tests, no locked file touched (prove: no code changed): 8 of 8
- Tree clean: 8 of 8
- Wording violations: none
- Cost $2.55, 115 agent turns, 483s of `claude` wall time (454s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prove-rolling-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 10 | $0.19 | 66s | 4.0 |
| prove-rolling-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 11 | $0.33 | 38s | 5.5 |
| prove-rolling-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 8 | $0.14 | 25s | 5.0 |
| prove-membership-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 8 | $0.17 | 54s | 4.7 |
| prove-membership-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 8 | $0.16 | 30s | 4.7 |
| prove-membership-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 8 | $0.29 | 35s | 4.7 |
| find-rolling-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 15 | $0.40 | 89s | 6.1 |
| find-membership-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 11 | $0.31 | 73s | 5.9 |
| pos-make-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 6.1 |
| pos-why-slow | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 5.6 |
| pos-wrong-perf | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 5.4 |
| pos-find-inefficiencies | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 5.2 |
| pos-speed-up-fn | ok | started | - | - | - | - | - | - | 3 | $0.05 | 7s | 5.3 |
| pos-profile | ok | started | - | - | - | - | - | - | 3 | $0.05 | 7s | 5.2 |
| pos-pr-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 5.2 |
| pos-verify-claim | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 5.0 |
| neg-typo | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 8s | 4.9 |
| neg-add-test | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 6s | 4.9 |
| neg-explain | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 4s | 5.0 |
| neg-rename | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 6s | 5.2 |

## Against the previous run

Previous run: `first`

- `prove-rolling-bench-edit`: FAIL to ok
- `prove-membership-bench-edit`: FAIL to ok
- wording violations over the 20 shared tasks: 1 before, 0 now
- cost over the shared tasks -0.76 USD, wall -97s
