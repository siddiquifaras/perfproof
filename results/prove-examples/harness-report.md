# Skill eval `prove2-ex`

Skill `88f012bf94f3`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 6 of 6
- Trigger set: 0 of 0 matched
- Verdict accepted: 6 of 6; wrong KEEP: 0
- Result line correct: 6 of 6
- Hotspot in a real `perfproof profile` output and named: no task has one
- Fix passes tests, no locked file touched (prove: no code changed): 6 of 6
- Tree clean: 6 of 6
- Wording violations: none
- Cost $1.51, 51 agent turns, 286s of `claude` wall time (275s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prove-rolling-fix | ok | - | KEEP (performance) | ok | - | ok | ok | ok | 11 | $0.38 | 82s | 4.9 |
| prove-rolling-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 8 | $0.18 | 49s | 6.1 |
| prove-rolling-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 7 | $0.15 | 27s | 4.6 |
| prove-membership-fix | ok | - | KEEP (performance) | ok | - | ok | ok | ok | 9 | $0.32 | 52s | 4.1 |
| prove-membership-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 8 | $0.18 | 41s | 4.2 |
| prove-membership-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 8 | $0.30 | 35s | 4.2 |
