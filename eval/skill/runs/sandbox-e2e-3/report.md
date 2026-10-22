# Skill eval `sandbox-e2e-3`

Skill `dee1520fbfdd`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 0 of 1
- Trigger set: 0 of 0 matched
- Verdict accepted: 1 of 1; wrong KEEP: 0
- Result line correct: 1 of 1
- Hotspot in a real `perfproof profile` output and named: top1 1 of 1
- Fix passes tests, no locked file touched (prove: no code changed): 1 of 1
- Tree clean: 1 of 1
- Wording violations: direction_word 1
- Cost $0.32, 14 agent turns, 112s of `claude` wall time (111s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| find-rolling-light | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | 1 FAIL | 14 | $0.32 | 112s | 6.5 |

## Failures

- `find-rolling-light`
  - direction_word: Goal:** cut the time the `rolling_sum` calls take in `bench.py`.
