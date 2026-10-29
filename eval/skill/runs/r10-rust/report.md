# Skill eval `r10-rust`

Skill `8d7428333413`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 3 of 3
- Trigger set: 0 of 0 matched
- Verdict accepted: 2 of 2; wrong KEEP: 0
- Result line correct: 3 of 3
- Hotspot in a real `perfproof profile` output and named: top15 1 of 1
- Fix passes tests, no locked file touched (prove: no code changed): 3 of 3
- Tree clean: 3 of 3
- Wording violations: none
- Cost $1.78, 71 agent turns, 734s of `claude` wall time (727s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| rust-fd-hidden-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 22 | $0.65 | 241s | 6.2 |
| rust-fd-known-change | ok | started | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 22 | $0.43 | 257s | 5.0 |
| rust-fd-nothing-to-keep | ok | - | none | ok | - | ok | ok | ok | 27 | $0.70 | 236s | 11.5 |
