# Skill eval `r10-go`

Skill `8d7428333413`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 2 of 3
- Trigger set: 0 of 0 matched
- Verdict accepted: 2 of 2; wrong KEEP: 0
- Result line correct: 3 of 3
- Hotspot in a real `perfproof profile` output and named: top15 0 of 1
- Fix passes tests, no locked file touched (prove: no code changed): 3 of 3
- Tree clean: 3 of 3
- Wording violations: leak 1
- Cost $1.86, 62 agent turns, 840s of `claude` wall time (834s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| go-echo-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | 1 FAIL | 26 | $0.83 | 325s | 5.3 |
| go-gin-known-change | ok | started | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 14 | $0.30 | 207s | 4.2 |
| go-gin-nothing-to-keep | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 22 | $0.73 | 308s | 6.0 |

## Failures

- `go-echo-hidden-fix`
  - hotspot: Echo.ServeHTTP is in the profile but never named as the cost
  - leak: {"command": "mkdir -p .perfproof/harness/bench .perfproof/specs && cat > .perfproof/harness/bench/main.go <<'EOF'\n// Drives Echo end to end through ServeHTT...
