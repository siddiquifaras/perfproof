# Skill eval `skill-3j-prove`

Skill `7fef1e430cb3`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 5 of 6
- Trigger set: 0 of 0 matched
- Verdict accepted: 6 of 6; wrong KEEP: 0
- Result line correct: 5 of 6
- Hotspot ranked first by the profile and named: 0 of 0
- Fix passes tests, no locked file touched (prove: no code changed): 6 of 6
- Tree clean: 6 of 6
- Wording violations: result_line 1
- Cost $1.48, 49 agent turns, 250s of `claude` wall time (238s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prove-rolling-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 8 | $0.27 | 63s | 9.3 |
| prove-rolling-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 9 | $0.29 | 36s | 6.2 |
| prove-rolling-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 7 | $0.25 | 29s | 6.0 |
| prove-membership-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 8 | $0.27 | 61s | 5.6 |
| prove-membership-wrong-output | FAIL | - | REJECT (correctness, gate correctness) | FAIL | - | ok | ok | 1 FAIL | 9 | $0.26 | 36s | 5.2 |
| prove-membership-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 8 | $0.15 | 25s | 4.7 |

## Failures

- `prove-membership-wrong-output`
  - Result line: 'REJECT (correctness)'
  - result_line: differs from the verdict
