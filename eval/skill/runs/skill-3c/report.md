# Skill eval `skill-3c`

Skill `497393751704`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 19 of 20
- Trigger set: 12 of 12 matched
- Verdict accepted: 7 of 8; wrong KEEP: 0
- Result line correct: 7 of 8
- Hotspot ranked first by the profile and named: 2 of 2
- Fix passes tests, no locked file touched (prove: no code changed): 7 of 8
- Tree clean: 7 of 8
- Wording violations: result_line 1
- Cost $2.76, 107 agent turns, 480s of `claude` wall time (447s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prove-rolling-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 7 | $0.16 | 56s | 4.8 |
| prove-rolling-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 7 | $0.23 | 32s | 4.9 |
| prove-rolling-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 6 | $0.12 | 25s | 5.3 |
| prove-membership-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 9 | $0.25 | 61s | 5.9 |
| prove-membership-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 7 | $0.13 | 34s | 5.9 |
| prove-membership-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 8 | $0.26 | 35s | 6.3 |
| find-rolling-light | FAIL | - | none FAIL | FAIL | ok | FAIL | FAIL | 1 FAIL | 16 | $0.64 | 82s | 7.7 |
| find-membership-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 11 | $0.42 | 77s | 7.0 |
| pos-make-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 6.6 |
| pos-why-slow | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 6.5 |
| pos-wrong-perf | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 6.6 |
| pos-find-inefficiencies | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 6.5 |
| pos-speed-up-fn | ok | started | - | - | - | - | - | - | 3 | $0.05 | 7s | 6.5 |
| pos-profile | ok | started | - | - | - | - | - | - | 3 | $0.05 | 8s | 6.4 |
| pos-pr-faster | ok | started | - | - | - | - | - | - | 3 | $0.05 | 8s | 6.5 |
| pos-verify-claim | ok | started | - | - | - | - | - | - | 3 | $0.05 | 6s | 6.5 |
| neg-typo | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 5s | 6.5 |
| neg-add-test | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 9s | 6.3 |
| neg-explain | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 5s | 6.4 |
| neg-rename | ok | no start | - | - | - | - | - | - | 3 | $0.04 | 7s | 6.5 |

## Failures

- `find-rolling-light`
  - fix: uncommitted changes in the tree: ['?? __pycache__/', '?? out/']
  - tree: status: ?? __pycache__/; status: ?? out/
  - Result line: None
  - result_line: no Result line

## Against the previous run

Previous run: `first`

- `prove-rolling-bench-edit`: FAIL to ok
- `prove-membership-bench-edit`: FAIL to ok
- `find-rolling-light`: ok to FAIL
- wording violations over the 20 shared tasks: 1 before, 1 now
- cost over the shared tasks -0.55 USD, wall -100s
