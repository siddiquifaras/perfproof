# Skill eval `r10-dotnet`

Skill `8d7428333413`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 2 of 3
- Trigger set: 0 of 0 matched
- Verdict accepted: 2 of 2; wrong KEEP: 0
- Result line correct: 3 of 3
- Hotspot in a real `perfproof profile` output and named: top15 0 of 1
- Fix passes tests, no locked file touched (prove: no code changed): 3 of 3
- Tree clean: 3 of 3
- Wording violations: direction_word 1
- Cost $2.54, 83 agent turns, 2301s of `claude` wall time (2295s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dotnet-markdig-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | 1 FAIL | 37 | $1.24 | 752s | 10.9 |
| dotnet-markdig-known-change | ok | started | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 25 | $0.50 | 1430s | 8.1 |
| dotnet-serilog-nothing-to-keep | ok | - | none | ok | - | ok | ok | ok | 21 | $0.80 | 119s | 5.1 |

## Failures

- `dotnet-markdig-hidden-fix`
  - hotspot: Markdig.Parsers.InlineProcessor.ProcessInlineLeaf is in the profile but never named as the cost
  - direction_word: A faster ASCII-only hash is also out, because it would change matching: under the current comparer `e` and `é` are the same label.
