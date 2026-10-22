# Skill eval `real-3`

Skill `6afa9cf74700`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 2 of 8
- Trigger set: 0 of 0 matched
- Verdict accepted: 2 of 5; wrong KEEP: 0
- Result line correct: 7 of 8
- Hotspot in a real `perfproof profile` output and named: top5 0 of 3
- Fix passes tests, no locked file touched (prove: no code changed): 8 of 8
- Tree clean: 8 of 8
- Wording violations: cheap_timing 2, direction_word 2, result_line 1
- Cost $3.60, 157 agent turns, 1286s of `claude` wall time (1264s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| real-xarray-hidden-fix | FAIL | - | none | ok | FAIL | ok | ok | 1 FAIL | 18 | $0.51 | 140s | 10.6 |
| real-fd-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | 2 FAIL | 26 | $0.59 | 286s | 10.7 |
| real-django-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | ok | 13 | $0.38 | 135s | 10.3 |
| real-arrow-known-change | FAIL | started | none FAIL | ok | - | ok | ok | 1 FAIL | 12 | $0.23 | 66s | 7.1 |
| real-echo-known-change | FAIL | started | REJECT (integrity, gate lock) FAIL | ok | - | ok | ok | ok | 15 | $0.29 | 109s | 11.9 |
| real-rails-known-change | FAIL | started | none FAIL | FAIL | - | ok | ok | 1 FAIL | 12 | $0.23 | 176s | 11.9 |
| real-rich-nothing-to-keep | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 32 | $0.74 | 235s | 8.9 |
| real-rails-nothing-to-keep | ok | - | none | ok | - | ok | ok | ok | 29 | $0.64 | 140s | 6.4 |

## Failures

- `real-xarray-hidden-fix`
  - hotspot: no profile output with hotspots in the transcript
  - direction_word: Goal and bar:** reduce the time the `core` work takes, with peak memory as a guardrail.
- `real-fd-hidden-fix`
  - hotspot: no profile output with hotspots in the transcript
  - cheap_timing: A run takes about 25 ms, which is too short for the system sampler to catch.
  - cheap_timing: perfproof's profiler only covered the Python wrapper, and a 25 ms run is too short for the system sampler.
- `real-django-hidden-fix`
  - hotspot: no profile output with hotspots in the transcript
- `real-arrow-known-change`
  - direction_word: The PR looks correct, but on this machine I measured it slower, not faster.
- `real-rails-known-change`
  - Result line: None
  - result_line: no Result line
