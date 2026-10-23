# Skill eval `r8-3-rust`

Skill `c64ca63af438`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 0 of 3
- Trigger set: 0 of 0 matched
- Verdict accepted: 2 of 2; wrong KEEP: 0
- Result line correct: 3 of 3
- Hotspot in a real `perfproof profile` output and named: top5 0 of 1
- Fix passes tests, no locked file touched (prove: no code changed): 3 of 3
- Tree clean: 3 of 3
- Wording violations: cheap_timing 2, direction_word 4
- Cost $1.37, 54 agent turns, 704s of `claude` wall time (696s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| rust-fd-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | 3 FAIL | 24 | $0.60 | 284s | 6.6 |
| rust-fd-known-change | FAIL | started | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | 1 FAIL | 15 | $0.29 | 314s | 12.1 |
| rust-fd-nothing-to-keep | FAIL | - | none | ok | - | ok | ok | 2 FAIL | 15 | $0.48 | 106s | 12.8 |

## Failures

- `rust-fd-hidden-fix`
  - hotspot: fd::output::print_entry is not in the first 5 of hotspots or frames_by_total of a saved profile: ['libsystem_kernel.dylib`__getdirentries64', 'libsystem_kernel.dylib`write', 'libsystem_kernel.dylib`__psynch_mutexdrop', 'libsystem_kernel.dylib`__open_nocancel', 'libsystem_platform.dylib`_platform_memmove', 'fd.deps`ignore::walk::Worker::run', 'fd.deps`<std::fs::ReadDir as core::iter::traits::iterator::Iterator>::next', 'libsystem_c.dylib`readdir_r', 'libsystem_c.dylib`_readdir_unlocked', 'libsystem_kernel.dylib`__getdirentries64']
  - direction_word: I locked a longer one, `fd_search_x40`.
  - cheap_timing: I'll lock that as the real benchmark (replacing the 20 ms one) and profile it.
  - cheap_timing: The workload.** The first benchmark was a 20 ms run, too short to profile.
- `rust-fd-known-change`
  - direction_word: Under 1.0 points toward a shorter run on `pr-1422`, but the range crosses 1.0.
- `rust-fd-nothing-to-keep`
  - direction_word: Speed up `fd`'s own walk code (`src/walk.rs`) | at most 1.04x, from the profile's 4% share | The cap is barely above the 3% bar.
  - direction_word: Speed up `.gitignore` matching and hashing in the `ignore` crate | at most about 1.1x, from the 10% share | Not tried.
