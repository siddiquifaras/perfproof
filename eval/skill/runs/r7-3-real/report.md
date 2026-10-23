# Skill eval `r7-3-real`

Skill `8487dacd81e2`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 1 of 6
- Trigger set: 0 of 0 matched
- Verdict accepted: 2 of 3; wrong KEEP: 0
- Result line correct: 6 of 6
- Hotspot in a real `perfproof profile` output and named: top5 0 of 3
- Fix passes tests, no locked file touched (prove: no code changed): 6 of 6
- Tree clean: 6 of 6
- Wording violations: direction_word 1
- Cost $2.36, 123 agent turns, 1344s of `claude` wall time (1331s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| real-xarray-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | ok | 32 | $0.64 | 524s | 3.3 |
| real-fd-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | ok | 23 | $0.44 | 203s | 4.8 |
| real-django-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | ok | 20 | $0.44 | 173s | 5.2 |
| real-arrow-known-change | FAIL | started | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | 1 FAIL | 20 | $0.34 | 128s | 4.4 |
| real-echo-known-change | FAIL | started | none FAIL | ok | - | ok | ok | ok | 14 | $0.27 | 79s | 6.2 |
| real-rails-known-change | ok | started | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 14 | $0.23 | 237s | 4.4 |

## Failures

- `real-xarray-hidden-fix`
  - hotspot: the first successful profile came after the first edit of the code
- `real-fd-hidden-fix`
  - hotspot: no profile output with hotspots in the transcript
- `real-django-hidden-fix`
  - hotspot: user_passes_test is not in the profile's top 5: ['NodeList.render', 'Variable.resolve', 'render_value_in_context', 'Template.render', 'wrapper']
- `real-arrow-known-change`
  - direction_word: The PR didn't produce a result I can call faster, and I found no sign it is incorrect.
