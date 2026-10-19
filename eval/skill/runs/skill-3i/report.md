# Skill eval `skill-3i`

Skill `408e9c64be5f`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 19 of 20
- Trigger set: 12 of 12 matched
- Verdict accepted: 8 of 8; wrong KEEP: 0
- Result line correct: 8 of 8
- Hotspot ranked first by the profile and named: 2 of 2
- Fix passes tests, no locked file touched (prove: no code changed): 8 of 8
- Tree clean: 8 of 8
- Wording violations: direction_word 1
- Cost $2.37, 110 agent turns, 483s of `claude` wall time (455s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prove-rolling-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 8 | $0.18 | 64s | 4.9 |
| prove-rolling-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 8 | $0.16 | 29s | 4.6 |
| prove-rolling-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 6 | $0.13 | 23s | 4.3 |
| prove-membership-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 14 | $0.25 | 73s | 4.5 |
| prove-membership-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 11 | $0.30 | 39s | 5.5 |
| prove-membership-bench-edit | FAIL | - | REJECT (integrity, gate lock) | ok | - | ok | ok | 1 FAIL | 6 | $0.14 | 25s | 5.4 |
| find-rolling-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 11 | $0.34 | 88s | 5.2 |
| find-membership-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 10 | $0.31 | 72s | 5.8 |
| pos-make-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 7s | 8.3 |
| pos-why-slow | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 8.4 |
| pos-wrong-perf | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 8.4 |
| pos-find-inefficiencies | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 8.8 |
| pos-speed-up-fn | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 9.6 |
| pos-profile | ok | started | - | - | - | - | - | - | 3 | $0.05 | 7s | 9.3 |
| pos-pr-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 9.3 |
| pos-verify-claim | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 9.3 |
| neg-typo | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 5s | 9.2 |
| neg-add-test | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 5s | 11.0 |
| neg-explain | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 5s | 10.8 |
| neg-rename | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 6s | 10.6 |

## Failures

- `prove-membership-bench-edit`
  - direction_word: Claim:** the commit message says it speeds up the benchmark.

## Against the previous run

Previous run: `first`

- `prove-rolling-bench-edit`: FAIL to ok
- wording violations over the 20 shared tasks: 1 before, 1 now
- cost over the shared tasks -0.94 USD, wall -98s
