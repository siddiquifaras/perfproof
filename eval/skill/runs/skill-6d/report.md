# Skill eval `skill-6d`

Skill `89961cdcb2fb`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 20 of 20
- Trigger set: 12 of 12 matched
- Verdict accepted: 8 of 8; wrong KEEP: 0
- Result line correct: 8 of 8
- Hotspot ranked first by the profile and named: 2 of 2
- Fix passes tests, no locked file touched (prove: no code changed): 8 of 8
- Tree clean: 8 of 8
- Wording violations: none
- Cost $2.26, 106 agent turns, 470s of `claude` wall time (442s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prove-rolling-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 11 | $0.21 | 71s | 3.4 |
| prove-rolling-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 8 | $0.17 | 33s | 3.3 |
| prove-rolling-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 8 | $0.15 | 26s | 3.7 |
| prove-membership-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 7 | $0.15 | 52s | 3.5 |
| prove-membership-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 7 | $0.15 | 30s | 3.5 |
| prove-membership-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 6 | $0.15 | 26s | 4.2 |
| find-rolling-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 11 | $0.35 | 78s | 3.6 |
| find-membership-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 12 | $0.35 | 88s | 3.2 |
| pos-make-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 3.0 |
| pos-why-slow | ok | started | - | - | - | - | - | - | 3 | $0.06 | 7s | 3.0 |
| pos-wrong-perf | ok | started | - | - | - | - | - | - | 3 | $0.06 | 5s | 3.1 |
| pos-find-inefficiencies | ok | started | - | - | - | - | - | - | 3 | $0.05 | 5s | 3.1 |
| pos-speed-up-fn | ok | started | - | - | - | - | - | - | 3 | $0.06 | 6s | 3.1 |
| pos-profile | ok | started | - | - | - | - | - | - | 3 | $0.06 | 7s | 3.0 |
| pos-pr-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 5s | 3.0 |
| pos-verify-claim | ok | started | - | - | - | - | - | - | 3 | $0.06 | 6s | 3.0 |
| neg-typo | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 4s | 3.0 |
| neg-add-test | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 5s | 3.3 |
| neg-explain | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 4s | 3.1 |
| neg-rename | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 6s | 3.0 |

## Against the previous run

Previous run: `skill-5c`

- no task changed its pass state
- wording violations over the 20 shared tasks: 0 before, 0 now
- cost over the shared tasks -0.05 USD, wall -27s
