# Skill eval `r8-3-real`

Skill `c64ca63af438`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 0 of 2
- Trigger set: 0 of 0 matched
- Verdict accepted: 0 of 0; wrong KEEP: 0
- Result line correct: 2 of 2
- Hotspot in a real `perfproof profile` output and named: top5 0 of 2
- Fix passes tests, no locked file touched (prove: no code changed): 2 of 2
- Tree clean: 2 of 2
- Wording violations: cheap_timing 1
- Cost $1.08, 43 agent turns, 601s of `claude` wall time (596s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| real-xarray-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | ok | 22 | $0.50 | 495s | 13.2 |
| real-fd-hidden-fix | FAIL | - | none | ok | FAIL | ok | ok | 1 FAIL | 21 | $0.58 | 106s | 8.8 |

## Skipped (not scored)

- `real-arrow-known-change`: missing on this machine: cmake, ninja
- `real-django-hidden-fix`: setup `uv venv --python 3.14+gil .venv` exited 2: error: Failed to inspect Python interpreter from first executable in the search path at `/Users/ppeval/eval-work/runs/r8-3-real/bin/python`
  cause: Querying Python at `/Users/ppeval/eval-work/runs/r8-3-real/bin/python` failed with exit status exit status: 126

         [stderr]
         /bin/sh: /Users/ppeval/eval-work/runs/r8-3-real/bin/python: Operation not permitted
- `real-echo-known-change`: skipped: needs docker, not available to ppeval
- `real-rails-known-change`: skipped: needs docker, not available to ppeval
- `real-rails-nothing-to-keep`: skipped: needs docker, not available to ppeval
- `real-rich-nothing-to-keep`: setup `uv venv --python 3.10 .venv` exited 2: error: Could not create temporary file
  cause: Operation not permitted (os error 1) at path "/Users/ppeval/eval-work/work/r8-3-real/sandbox/real-rich-nothing-to-keep/private/home/.local/share/uv/python/.tmpr7jXXJ"

## Failures

- `real-xarray-hidden-fix`
  - hotspot: is_dask_collection is not in the first 5 of hotspots or frames_by_total of a saved profile: ['compile', '_call_with_frames_removed', 'stat', 'isinstance', 'exec_dynamic', 'main', 'one_pass', 'DataArrayRolling.method', 'DataArrayRolling._numpy_or_bottleneck_reduce', 'DataArrayRolling._mean']
- `real-fd-hidden-fix`
  - hotspot: fd::output::print_entry_colorized is not in the first 5 of hotspots or frames_by_total of a saved profile: ['libsystem_kernel.dylib`__open_nocancel', 'libsystem_kernel.dylib`__getdirentries64', 'libsystem_kernel.dylib`write', 'libsystem_kernel.dylib`fstatfs', 'libsystem_kernel.dylib`__close_nocancel', 'fd.deps`ignore::walk::Worker::run', 'libsystem_c.dylib`__opendir2', 'libsystem_kernel.dylib`open$NOCANCEL', 'libsystem_kernel.dylib`__open_nocancel', 'fd.deps`<std::fs::ReadDir as core::iter::traits::iterator::Iterator>::next']
  - cheap_timing: The benchmark changed from what you approved.** The `eval-tree` search takes about 13 ms.
