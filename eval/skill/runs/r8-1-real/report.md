# Skill eval `r8-1-real`

Skill `d52555445b50`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 0 of 2
- Trigger set: 0 of 0 matched
- Verdict accepted: 0 of 0; wrong KEEP: 0
- Result line correct: 2 of 2
- Hotspot in a real `perfproof profile` output and named: top5 0 of 2
- Fix passes tests, no locked file touched (prove: no code changed): 2 of 2
- Tree clean: 2 of 2
- Wording violations: none
- Cost $1.11, 53 agent turns, 479s of `claude` wall time (474s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| real-xarray-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | ok | 25 | $0.52 | 288s | 6.6 |
| real-fd-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | ok | 28 | $0.58 | 191s | 6.8 |

## Skipped (not scored)

- `real-arrow-known-change`: missing on this machine: cmake, ninja
- `real-django-hidden-fix`: setup `uv venv --python 3.14+gil .venv` exited 2: error: Failed to inspect Python interpreter from first executable in the search path at `/Users/ppeval/eval-work/runs/r8-1-real/bin/python`
  cause: Querying Python at `/Users/ppeval/eval-work/runs/r8-1-real/bin/python` failed with exit status exit status: 126

         [stderr]
         /bin/sh: /Users/ppeval/eval-work/runs/r8-1-real/bin/python: Operation not permitted
- `real-echo-known-change`: skipped: needs docker, not available to ppeval
- `real-rails-known-change`: skipped: needs docker, not available to ppeval
- `real-rails-nothing-to-keep`: skipped: needs docker, not available to ppeval
- `real-rich-nothing-to-keep`: setup `uv venv --python 3.10 .venv` exited 2: error: Could not create temporary file
  cause: Operation not permitted (os error 1) at path "/Users/ppeval/eval-work/work/r8-1-real/sandbox/real-rich-nothing-to-keep/private/home/.local/share/uv/python/.tmpLA1l3Y"

## Failures

- `real-xarray-hidden-fix`
  - hotspot: is_dask_collection is not in the first 5 of hotspots or frames_by_total of a saved profile: ['ufunc.reduce', 'compile', '_call_with_frames_removed', 'stat', '_path_join', 'work', 'DatasetRolling._dataset_implementation', 'DataArrayRolling._numpy_or_bottleneck_reduce', 'DataArrayRolling._mean', 'DatasetRolling.method']
- `real-fd-hidden-fix`
  - hotspot: fd::output::print_entry_colorized is not in the first 5 of hotspots or frames_by_total of a saved profile: ['libsystem_kernel.dylib`__getdirentries64', 'libsystem_kernel.dylib`write', 'libsystem_kernel.dylib`__psynch_mutexdrop', 'fd.deps`ignore::walk::Worker::run', 'libsystem_platform.dylib`_platform_memmove', 'fd.deps`std::sys::backtrace::__rust_begin_short_backtrace', 'fd.deps`ignore::walk::Worker::run', 'fd.deps`<std::fs::ReadDir as core::iter::traits::iterator::Iterator>::next', 'libsystem_c.dylib`_readdir_unlocked', 'libsystem_kernel.dylib`__getdirentries64']
