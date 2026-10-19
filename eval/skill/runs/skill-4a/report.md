# Skill eval `skill-4a`

Skill `c84e126c6acd`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 20 of 20
- Trigger set: 12 of 12 matched
- Verdict accepted: 8 of 8; wrong KEEP: 0
- Result line correct: 8 of 8
- Hotspot ranked first by the profile and named: 2 of 2
- Fix passes tests, no locked file touched (prove: no code changed): 8 of 8
- Tree clean: 8 of 8
- Wording violations: none
- Cost $2.79, 108 agent turns, 568s of `claude` wall time (532s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prove-rolling-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 9 | $0.27 | 69s | 6.0 |
| prove-rolling-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 6 | $0.24 | 33s | 8.0 |
| prove-rolling-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 8 | $0.23 | 32s | 7.1 |
| prove-membership-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 7 | $0.25 | 69s | 6.9 |
| prove-membership-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 8 | $0.25 | 37s | 8.0 |
| prove-membership-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 7 | $0.24 | 31s | 9.2 |
| find-rolling-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 15 | $0.39 | 118s | 10.2 |
| find-membership-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 12 | $0.34 | 97s | 10.0 |
| pos-make-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 13.3 |
| pos-why-slow | ok | started | - | - | - | - | - | - | 3 | $0.05 | 7s | 13.1 |
| pos-wrong-perf | ok | started | - | - | - | - | - | - | 3 | $0.05 | 7s | 12.6 |
| pos-find-inefficiencies | ok | started | - | - | - | - | - | - | 3 | $0.05 | 9s | 12.1 |
| pos-speed-up-fn | ok | started | - | - | - | - | - | - | 3 | $0.06 | 9s | 11.5 |
| pos-profile | ok | started | - | - | - | - | - | - | 3 | $0.05 | 10s | 11.4 |
| pos-pr-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 10.8 |
| pos-verify-claim | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 10.7 |
| neg-typo | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 6s | 10.3 |
| neg-add-test | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 6s | 11.2 |
| neg-explain | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 5s | 11.0 |
| neg-rename | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 5s | 11.0 |

## Against the previous run

Previous run: `skill-3e`

- no task changed its pass state
- wording violations over the 20 shared tasks: 0 before, 0 now
- cost over the shared tasks +0.24 USD, wall +84s
