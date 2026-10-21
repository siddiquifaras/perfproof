# Skill eval `skill-5a`

Skill `7ed71763773f`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 20 of 20
- Trigger set: 12 of 12 matched
- Verdict accepted: 8 of 8; wrong KEEP: 0
- Result line correct: 8 of 8
- Hotspot ranked first by the profile and named: 2 of 2
- Fix passes tests, no locked file touched (prove: no code changed): 8 of 8
- Tree clean: 8 of 8
- Wording violations: none
- Cost $2.28, 110 agent turns, 500s of `claude` wall time (471s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prove-rolling-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 8 | $0.16 | 63s | 4.5 |
| prove-rolling-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 9 | $0.18 | 35s | 4.8 |
| prove-rolling-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 8 | $0.16 | 32s | 4.2 |
| prove-membership-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 8 | $0.16 | 57s | 3.5 |
| prove-membership-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 10 | $0.18 | 33s | 3.7 |
| prove-membership-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 6 | $0.14 | 23s | 3.4 |
| find-rolling-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 13 | $0.36 | 95s | 3.5 |
| find-membership-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 12 | $0.35 | 91s | 4.4 |
| pos-make-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 5.7 |
| pos-why-slow | ok | started | - | - | - | - | - | - | 3 | $0.05 | 7s | 5.8 |
| pos-wrong-perf | ok | started | - | - | - | - | - | - | 3 | $0.06 | 8s | 5.9 |
| pos-find-inefficiencies | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 5.6 |
| pos-speed-up-fn | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 5.4 |
| pos-profile | ok | started | - | - | - | - | - | - | 3 | $0.05 | 5s | 5.4 |
| pos-pr-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 5.5 |
| pos-verify-claim | ok | started | - | - | - | - | - | - | 3 | $0.05 | 5s | 5.3 |
| neg-typo | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 5s | 5.3 |
| neg-add-test | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 6s | 5.2 |
| neg-explain | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 5s | 5.1 |
| neg-rename | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 5s | 5.0 |

## Against the previous run

Previous run: `skill-4b`

- `prove-membership-wrong-output`: FAIL to ok
- `prove-membership-bench-edit`: FAIL to ok
- wording violations over the 20 shared tasks: 3 before, 0 now
- cost over the shared tasks -0.59 USD, wall -166s
