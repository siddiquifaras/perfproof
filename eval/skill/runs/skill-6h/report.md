# Skill eval `skill-6h`

Skill `6afa9cf74700`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 20 of 20
- Trigger set: 12 of 12 matched
- Verdict accepted: 8 of 8; wrong KEEP: 0
- Result line correct: 8 of 8
- Hotspot ranked first by the profile and named: 2 of 2
- Fix passes tests, no locked file touched (prove: no code changed): 8 of 8
- Tree clean: 8 of 8
- Wording violations: none
- Cost $2.18, 101 agent turns, 509s of `claude` wall time (481s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prove-rolling-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 8 | $0.17 | 68s | 6.2 |
| prove-rolling-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 10 | $0.19 | 40s | 6.9 |
| prove-rolling-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 6 | $0.15 | 27s | 7.1 |
| prove-membership-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 7 | $0.15 | 64s | 7.0 |
| prove-membership-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 6 | $0.15 | 33s | 6.8 |
| prove-membership-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 6 | $0.15 | 25s | 6.5 |
| find-rolling-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 12 | $0.36 | 93s | 6.7 |
| find-membership-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 10 | $0.29 | 86s | 6.8 |
| pos-make-faster | ok | started | - | - | - | - | - | - | 3 | $0.06 | 7s | 6.7 |
| pos-why-slow | ok | started | - | - | - | - | - | - | 3 | $0.06 | 6s | 6.7 |
| pos-wrong-perf | ok | started | - | - | - | - | - | - | 3 | $0.06 | 9s | 6.9 |
| pos-find-inefficiencies | ok | started | - | - | - | - | - | - | 3 | $0.06 | 6s | 6.8 |
| pos-speed-up-fn | ok | started | - | - | - | - | - | - | 3 | $0.06 | 7s | 6.7 |
| pos-profile | ok | started | - | - | - | - | - | - | 3 | $0.06 | 7s | 6.7 |
| pos-pr-faster | ok | started | - | - | - | - | - | - | 3 | $0.06 | 6s | 6.9 |
| pos-verify-claim | ok | started | - | - | - | - | - | - | 3 | $0.06 | 6s | 6.9 |
| neg-typo | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 5s | 6.8 |
| neg-add-test | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 6s | 6.8 |
| neg-explain | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 5s | 7.1 |
| neg-rename | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 5s | 7.1 |

## Against the previous run

Previous run: `skill-6d`

- no task changed its pass state
- wording violations over the 20 shared tasks: 0 before, 0 now
- cost over the shared tasks -0.08 USD, wall +39s
