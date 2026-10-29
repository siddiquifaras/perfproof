# Skill eval `r10-ruby`

Skill `8d7428333413`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 2 of 2; excluded from the bar: 0 of 1 passed (listed apart below)
- Trigger set: 0 of 0 matched
- Verdict accepted: 2 of 2; wrong KEEP: 0
- Result line correct: 3 of 3
- Hotspot in a real `perfproof profile` output and named: top15 0 of 1
- Fix passes tests, no locked file touched (prove: no code changed): 3 of 3
- Tree clean: 3 of 3
- Wording violations: none
- Cost $1.50, 65 agent turns, 735s of `claude` wall time (728s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ruby-rails-prove | ok | started | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 16 | $0.28 | 190s | 6.0 |
| ruby-rails-find | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | ok | 28 | $0.70 | 381s | 4.7 |
| ruby-rails-nothing-to-keep | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 21 | $0.52 | 164s | 5.4 |

## Excluded from the pass bar (scored, not counted)

- `ruby-rails-find`: FAIL. Judged, not measured here: a micro win of the same kind as the Django one. String#blank? is already optimised and the change (avoid repeated bytesize calls) is judged too small to show above the 3% bar end to end.

## Failures

- `ruby-rails-find`
  - hotspot: String#blank? is not in the first 15 of hotspots or frames_by_total of a saved profile: ['String#gsub!', 'String#sub!', '(sweeping)', 'Kernel.require', 'String#gsub', 'String#sub', '(garbage collection)', 'Kernel#require_relative', 'String#truncate_words', 'ActiveSupport::Inflector#apply_inflections', 'ActiveSupport::NumberHelper::NumberToRoundedConverter#format_number', '(marking)', 'Time#to_s', 'Hash#inspect', 'I18n.interpolate', 'Object#work', 'block in <main>', 'Object#iter', '(garbage collection)', 'ActiveSupport::Inflector#humanize', '(sweeping)', 'ActiveSupport::Inflector#apply_inflections', 'ActiveSupport::Inflector#titleize', 'String#titleize', 'ActiveSupport::Inflector#pluralize', 'String#humanize', 'ActiveSupport::Inflector#singularize', 'ActiveSupport::Inflector#camelize', 'block (2 levels) in replace_require', 'String#pluralize']
