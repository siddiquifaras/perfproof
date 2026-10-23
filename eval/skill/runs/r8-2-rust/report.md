# Skill eval `r8-2-rust`

Skill `7cbea51e4466`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 2 of 3
- Trigger set: 0 of 0 matched
- Verdict accepted: 2 of 2; wrong KEEP: 0
- Result line correct: 3 of 3
- Hotspot in a real `perfproof profile` output and named: top5 0 of 1
- Fix passes tests, no locked file touched (prove: no code changed): 3 of 3
- Tree clean: 3 of 3
- Wording violations: none
- Cost $0.86, 42 agent turns, 401s of `claude` wall time (394s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| rust-fd-hidden-fix | FAIL | - | none | ok | FAIL | ok | ok | ok | 8 | $0.15 | 28s | 9.7 |
| rust-fd-known-change | ok | started | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 13 | $0.21 | 196s | 14.8 |
| rust-fd-nothing-to-keep | ok | - | none | ok | - | ok | ok | ok | 21 | $0.50 | 177s | 19.4 |

## Failures

- `rust-fd-hidden-fix`
  - hotspot: no profile output with hotspots in the transcript
