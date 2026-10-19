# Skill eval `broken`

Skill `0674202571f3`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 0 of 2
- Trigger set: 0 of 0 matched
- Verdict accepted: 2 of 2; wrong KEEP: 0
- Result line correct: 2 of 2
- Hotspot ranked first by the profile and named: 0 of 0
- Fix passes tests, no locked file touched (prove: no code changed): 2 of 2
- Tree clean: 2 of 2
- Wording violations: direction_word 4
- Cost $0.51, 19 agent turns, 168s of `claude` wall time (164s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prove-rolling-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | 2 FAIL | 10 | $0.25 | 106s | 9.3 |
| prove-membership-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | 2 FAIL | 9 | $0.26 | 62s | 9.3 |

## Failures

- `prove-rolling-fix`
  - direction_word: Estimate (not a verdict):** about 62x faster on the benchmark's own timer, with a range of 61x to 64x.
  - direction_word: That is faster than claimed, and the range is far above the 3% bar.
- `prove-membership-fix`
  - direction_word: The claim to check is about 100x faster at screening orders.
  - direction_word: On this estimate the change is much faster, and it's on `agent-speedup`.

## Against the previous run

Previous run: `first`

- `prove-rolling-fix`: ok to FAIL
- `prove-membership-fix`: ok to FAIL
- wording violations over the 2 shared tasks: 0 before, 4 now
- 18 tasks of the previous run were not run here, 0 tasks here were not in it
- cost over the shared tasks +0.05 USD, wall +30s
