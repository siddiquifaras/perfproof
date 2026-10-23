# Skill eval `r7-1-real`

Skill `9dab86db52ce`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 3 of 8
- Trigger set: 0 of 0 matched
- Verdict accepted: 4 of 5; wrong KEEP: 0
- Result line correct: 8 of 8
- Hotspot in a real `perfproof profile` output and named: top5 0 of 3
- Fix passes tests, no locked file touched (prove: no code changed): 8 of 8
- Tree clean: 8 of 8
- Wording violations: direction_word 1
- Cost $2.96, 145 agent turns, 1692s of `claude` wall time (1675s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| real-xarray-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | ok | 27 | $0.61 | 331s | 5.9 |
| real-fd-hidden-fix | FAIL | - | none | ok | FAIL | ok | ok | ok | 16 | $0.36 | 64s | 8.9 |
| real-django-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | ok | 19 | $0.40 | 169s | 4.2 |
| real-arrow-known-change | ok | started | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 14 | $0.25 | 314s | 4.3 |
| real-echo-known-change | FAIL | started | none FAIL | ok | - | ok | ok | ok | 15 | $0.26 | 71s | 12.4 |
| real-rails-known-change | ok | started | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 12 | $0.20 | 228s | 7.9 |
| real-rich-nothing-to-keep | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 14 | $0.34 | 117s | 7.5 |
| real-rails-nothing-to-keep | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | 1 FAIL | 28 | $0.55 | 397s | 4.5 |

## Failures

- `real-xarray-hidden-fix`
  - hotspot: no profile output with hotspots in the transcript
- `real-fd-hidden-fix`
  - hotspot: no profile output with hotspots in the transcript
- `real-django-hidden-fix`
  - hotspot: no profile output with hotspots in the transcript
- `real-rails-nothing-to-keep`
  - direction_word: It also means the obvious formatting win may already be taken.
