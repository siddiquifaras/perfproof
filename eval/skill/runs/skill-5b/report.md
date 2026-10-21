# Skill eval `skill-5b`

Skill `a25ce5fcd902`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 18 of 20
- Trigger set: 12 of 12 matched
- Verdict accepted: 8 of 8; wrong KEEP: 0
- Result line correct: 7 of 8
- Hotspot ranked first by the profile and named: 2 of 2
- Fix passes tests, no locked file touched (prove: no code changed): 8 of 8
- Tree clean: 8 of 8
- Wording violations: claim_comparison 1, result_line 1
- Cost $2.97, 115 agent turns, 550s of `claude` wall time (514s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prove-rolling-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 11 | $0.30 | 75s | 5.7 |
| prove-rolling-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 9 | $0.29 | 44s | 6.6 |
| prove-rolling-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 7 | $0.15 | 26s | 6.7 |
| prove-membership-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 11 | $0.34 | 80s | 6.6 |
| prove-membership-wrong-output | FAIL | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | 1 FAIL | 8 | $0.30 | 40s | 6.6 |
| prove-membership-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 8 | $0.29 | 35s | 6.1 |
| find-rolling-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 12 | $0.37 | 82s | 6.8 |
| find-membership-light | FAIL | - | INCONCLUSIVE (insufficient_evidence) | FAIL | ok | ok | ok | 1 FAIL | 13 | $0.35 | 94s | 6.7 |
| pos-make-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 6.7 |
| pos-why-slow | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 6.7 |
| pos-wrong-perf | ok | started | - | - | - | - | - | - | 3 | $0.06 | 6s | 6.7 |
| pos-find-inefficiencies | ok | started | - | - | - | - | - | - | 3 | $0.05 | 7s | 6.6 |
| pos-speed-up-fn | ok | started | - | - | - | - | - | - | 3 | $0.06 | 7s | 6.6 |
| pos-profile | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 6.3 |
| pos-pr-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 6.3 |
| pos-verify-claim | ok | started | - | - | - | - | - | - | 3 | $0.05 | 7s | 6.1 |
| neg-typo | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 5s | 6.1 |
| neg-add-test | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 5s | 6.3 |
| neg-explain | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 5s | 6.4 |
| neg-rename | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 6s | 6.4 |

## Failures

- `prove-membership-wrong-output`
  - claim_comparison: There is no speed estimate and no speed verdict, and the commit makes no numeric claim to check.
- `find-membership-light`
  - Result line: 'INCONCLUSIVE (insufficient_evidence)'
  - result_line: more than one Result line

## Against the previous run

Previous run: `skill-5a`

- `prove-membership-wrong-output`: ok to FAIL
- `find-membership-light`: ok to FAIL
- wording violations over the 20 shared tasks: 0 before, 2 now
- cost over the shared tasks +0.69 USD, wall +49s
