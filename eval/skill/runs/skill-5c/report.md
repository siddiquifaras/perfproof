# Skill eval `skill-5c`

Skill `aff27ce65baf`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 20 of 20
- Trigger set: 12 of 12 matched
- Verdict accepted: 8 of 8; wrong KEEP: 0
- Result line correct: 8 of 8
- Hotspot ranked first by the profile and named: 2 of 2
- Fix passes tests, no locked file touched (prove: no code changed): 8 of 8
- Tree clean: 8 of 8
- Wording violations: none
- Cost $2.31, 110 agent turns, 497s of `claude` wall time (472s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prove-rolling-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 8 | $0.17 | 61s | 4.5 |
| prove-rolling-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 7 | $0.16 | 30s | 4.5 |
| prove-rolling-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 8 | $0.16 | 29s | 3.8 |
| prove-membership-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 7 | $0.16 | 56s | 3.8 |
| prove-membership-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 11 | $0.21 | 45s | 3.6 |
| prove-membership-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 7 | $0.15 | 27s | 2.8 |
| find-rolling-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 14 | $0.39 | 90s | 2.7 |
| find-membership-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 12 | $0.34 | 83s | 3.7 |
| pos-make-faster | ok | started | - | - | - | - | - | - | 3 | $0.06 | 6s | 2.6 |
| pos-why-slow | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 3.1 |
| pos-wrong-perf | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 2.9 |
| pos-find-inefficiencies | ok | started | - | - | - | - | - | - | 3 | $0.05 | 7s | 3.2 |
| pos-speed-up-fn | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 3.1 |
| pos-profile | ok | started | - | - | - | - | - | - | 3 | $0.05 | 8s | 2.9 |
| pos-pr-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 5s | 2.5 |
| pos-verify-claim | ok | started | - | - | - | - | - | - | 3 | $0.05 | 8s | 2.4 |
| neg-typo | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 7s | 2.2 |
| neg-add-test | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 6s | 2.1 |
| neg-explain | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 5s | 2.0 |
| neg-rename | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 6s | 1.9 |

## Against the previous run

Previous run: `skill-5a`

- no task changed its pass state
- wording violations over the 20 shared tasks: 0 before, 0 now
- cost over the shared tasks +0.04 USD, wall -4s
