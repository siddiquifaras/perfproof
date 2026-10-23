# Skill eval `r7-2-real`

Skill `bd7992eb865f`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 3 of 8
- Trigger set: 0 of 0 matched
- Verdict accepted: 4 of 5; wrong KEEP: 0
- Result line correct: 8 of 8
- Hotspot in a real `perfproof profile` output and named: top5 0 of 3
- Fix passes tests, no locked file touched (prove: no code changed): 7 of 8
- Tree clean: 7 of 8
- Wording violations: direction_word 2
- Cost $3.08, 153 agent turns, 2528s of `claude` wall time (2501s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| real-xarray-hidden-fix | FAIL | - | none | ok | FAIL | FAIL | FAIL | ok | 20 | $0.42 | 839s | 7.0 |
| real-fd-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | ok | 26 | $0.52 | 326s | 4.9 |
| real-django-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | 1 FAIL | 22 | $0.47 | 179s | 5.8 |
| real-arrow-known-change | FAIL | started | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | 1 FAIL | 17 | $0.33 | 134s | 4.1 |
| real-echo-known-change | FAIL | started | none FAIL | ok | - | ok | ok | ok | 14 | $0.29 | 119s | 4.2 |
| real-rails-known-change | ok | started | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 16 | $0.23 | 593s | 7.3 |
| real-rich-nothing-to-keep | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 18 | $0.41 | 133s | 7.2 |
| real-rails-nothing-to-keep | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 20 | $0.40 | 204s | 7.7 |

## Failures

- `real-xarray-hidden-fix`
  - hotspot: the first successful profile came after the first edit of the code
  - fix: uncommitted changes in the tree: ['?? out/']
  - tree: status: ?? out/
- `real-fd-hidden-fix`
  - hotspot: no profile output with hotspots in the transcript
- `real-django-hidden-fix`
  - hotspot: user_passes_test is not in the profile's top 5: ['ForNode.render', 'VariableNode.render', 'Col.as_sql', 'Local.__getattr__', 'Variable._resolve_lookup']
  - direction_word: Its loop is already tight, and the gains available there look small.
- `real-arrow-known-change`
  - direction_word: This is an estimate, so I'm not calling it faster or slower.
