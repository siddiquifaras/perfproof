# Skill eval `r8-2-real`

Skill `7cbea51e4466`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 1 of 2
- Trigger set: 0 of 0 matched
- Verdict accepted: 0 of 0; wrong KEEP: 0
- Result line correct: 2 of 2
- Hotspot in a real `perfproof profile` output and named: top5 1 of 2
- Fix passes tests, no locked file touched (prove: no code changed): 2 of 2
- Tree clean: 2 of 2
- Wording violations: none
- Cost $1.02, 42 agent turns, 593s of `claude` wall time (588s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| real-xarray-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | ok | 19 | $0.46 | 361s | 8.2 |
| real-fd-hidden-fix | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 23 | $0.55 | 232s | 5.0 |

## Skipped (not scored)

- `real-arrow-known-change`: missing on this machine: cmake, ninja
- `real-django-hidden-fix`: setup `uv venv --python 3.14+gil .venv` exited 2: error: Failed to inspect Python interpreter from first executable in the search path at `/Users/ppeval/eval-work/runs/r8-2-real/bin/python`
  cause: Querying Python at `/Users/ppeval/eval-work/runs/r8-2-real/bin/python` failed with exit status exit status: 126

         [stderr]
         /bin/sh: /Users/ppeval/eval-work/runs/r8-2-real/bin/python: Operation not permitted
- `real-echo-known-change`: skipped: needs docker, not available to ppeval
- `real-rails-known-change`: skipped: needs docker, not available to ppeval
- `real-rails-nothing-to-keep`: skipped: needs docker, not available to ppeval
- `real-rich-nothing-to-keep`: setup `uv venv --python 3.10 .venv` exited 2: error: Could not create temporary file
  cause: Operation not permitted (os error 1) at path "/Users/ppeval/eval-work/work/r8-2-real/sandbox/real-rich-nothing-to-keep/private/home/.local/share/uv/python/.tmpFT9TE8"

## Failures

- `real-xarray-hidden-fix`
  - hotspot: is_dask_collection is not in the first 5 of hotspots or frames_by_total of a saved profile: ['compile', '_call_with_frames_removed', 'stat', 'loads', 'ufunc.reduce', 'work', 'DataArrayRolling._mean', 'DataArrayRolling.method', 'DataArrayRolling._numpy_or_bottleneck_reduce', 'apply_ufunc']
