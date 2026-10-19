# Skill eval `skill-4b`

Skill `6449b5ce2df3`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 18 of 20
- Trigger set: 12 of 12 matched
- Verdict accepted: 8 of 8; wrong KEEP: 0
- Result line correct: 8 of 8
- Hotspot ranked first by the profile and named: 2 of 2
- Fix passes tests, no locked file touched (prove: no code changed): 8 of 8
- Tree clean: 8 of 8
- Wording violations: claim_comparison 1, direction_word 2
- Cost $2.86, 117 agent turns, 666s of `claude` wall time (626s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prove-rolling-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 10 | $0.26 | 92s | 10.1 |
| prove-rolling-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 10 | $0.30 | 52s | 10.7 |
| prove-rolling-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 9 | $0.29 | 35s | 10.5 |
| prove-membership-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 8 | $0.25 | 84s | 10.1 |
| prove-membership-wrong-output | FAIL | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | 2 FAIL | 10 | $0.28 | 47s | 10.9 |
| prove-membership-bench-edit | FAIL | - | REJECT (integrity, gate lock) | ok | - | ok | ok | 1 FAIL | 9 | $0.24 | 37s | 11.8 |
| find-rolling-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 11 | $0.32 | 101s | 12.7 |
| find-membership-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 14 | $0.36 | 133s | 12.3 |
| pos-make-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 7s | 13.9 |
| pos-why-slow | ok | started | - | - | - | - | - | - | 3 | $0.05 | 8s | 13.5 |
| pos-wrong-perf | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 13.8 |
| pos-find-inefficiencies | ok | started | - | - | - | - | - | - | 3 | $0.06 | 8s | 15.4 |
| pos-speed-up-fn | ok | started | - | - | - | - | - | - | 3 | $0.06 | 9s | 15.2 |
| pos-profile | ok | started | - | - | - | - | - | - | 3 | $0.06 | 9s | 15.4 |
| pos-pr-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 8s | 14.9 |
| pos-verify-claim | ok | started | - | - | - | - | - | - | 3 | $0.05 | 7s | 14.6 |
| neg-typo | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 5s | 14.4 |
| neg-add-test | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 6s | 14.1 |
| neg-explain | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 6s | 13.6 |
| neg-rename | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 5s | 14.0 |

## Failures

- `prove-membership-wrong-output`
  - direction_word: The diff itself points to one.
  - claim_comparison: Timing and speed:** nothing was measured, so there is no estimate and no speed claim either way.
- `prove-membership-bench-edit`
  - direction_word: Perfproof will check whether the measured code got faster.

## Against the previous run

Previous run: `skill-4a`

- `prove-membership-wrong-output`: ok to FAIL
- `prove-membership-bench-edit`: ok to FAIL
- wording violations over the 20 shared tasks: 0 before, 3 now
- cost over the shared tasks +0.07 USD, wall +98s
