# Skill eval `r10-ex`

Skill `8d7428333413`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 23 of 24
- Prompt variants (`-msg-`): 3 of 4 passed
- Trigger set: 12 of 12 matched
- Verdict accepted: 12 of 12; wrong KEEP: 0
- Result line correct: 12 of 12
- Hotspot in a real `perfproof profile` output and named: top1 5 of 6
- Fix passes tests, no locked file touched (prove: no code changed): 12 of 12
- Tree clean: 12 of 12
- Wording violations: none
- Cost $3.58, 161 agent turns, 923s of `claude` wall time (880s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prove-rolling-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 14 | $0.23 | 106s | 1.9 |
| prove-rolling-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 9 | $0.18 | 37s | 2.3 |
| prove-rolling-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 8 | $0.16 | 24s | 2.0 |
| prove-membership-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 6 | $0.14 | 58s | 2.0 |
| prove-membership-wrong-output | ok | - | REJECT (correctness, gate correctness) | ok | - | ok | ok | ok | 6 | $0.14 | 32s | 1.7 |
| prove-membership-bench-edit | ok | - | REJECT (integrity, gate lock) | ok | - | ok | ok | ok | 7 | $0.15 | 22s | 1.4 |
| find-rolling-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 11 | $0.34 | 88s | 1.5 |
| find-rolling-msg-rambling | FAIL | started | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | ok | 18 | $0.38 | 108s | 3.2 |
| find-rolling-msg-bare | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 11 | $0.33 | 101s | 3.4 |
| find-membership-light | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 11 | $0.35 | 105s | 3.9 |
| find-membership-msg-claim | ok | started | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 12 | $0.30 | 88s | 4.0 |
| find-membership-msg-vague | ok | started | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 12 | $0.31 | 90s | 3.7 |
| pos-make-faster | ok | started | - | - | - | - | - | - | 3 | $0.06 | 5s | 3.1 |
| pos-why-slow | ok | started | - | - | - | - | - | - | 3 | $0.06 | 5s | 3.0 |
| pos-wrong-perf | ok | started | - | - | - | - | - | - | 3 | $0.06 | 6s | 3.0 |
| pos-find-inefficiencies | ok | started | - | - | - | - | - | - | 3 | $0.06 | 4s | 2.9 |
| pos-speed-up-fn | ok | started | - | - | - | - | - | - | 3 | $0.06 | 6s | 2.9 |
| pos-profile | ok | started | - | - | - | - | - | - | 3 | $0.06 | 6s | 3.4 |
| pos-pr-faster | ok | started | - | - | - | - | - | - | 3 | $0.06 | 6s | 6.3 |
| pos-verify-claim | ok | started | - | - | - | - | - | - | 3 | $0.06 | 6s | 6.0 |
| neg-typo | ok | no start | - | - | - | - | - | - | 3 | $0.02 | 4s | 6.0 |
| neg-add-test | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 5s | 5.7 |
| neg-explain | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 4s | 5.5 |
| neg-rename | ok | no start | - | - | - | - | - | - | 3 | $0.03 | 6s | 6.1 |

## Failures

- `find-rolling-msg-rambling`
  - hotspot: no profile output with hotspots in the transcript
