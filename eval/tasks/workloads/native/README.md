# C and C++ task support

Three manifests for the native task (T5.4), each with a workload that is a
C or C++ program copied into the tree and built with the system compiler,
and an oracle that is the project's own tests:

| Manifest | Family | Workload |
|---|---|---|
| `simdjson-simdjson-pr2805` | hidden-fix | serialize a parsed array of 100,000 doubles 400 times (`eval_to_chars.cpp`) |
| `lz4-lz4-pr1040` | known-change | decompress one 370 KB block 20,000 times (`eval_decode.c`) |
| `facebook-zstd-pr3152-landed` | nothing-to-keep | compress 160,000 blocks of 4 KiB against a digested dictionary (`eval_cdict.c`), on the tree where the change is already in |

Their workloads and oracles ran on both trees with `eval/mine/check.py`
(status `runs`) with the system `cc` and `c++` (Apple clang 17.0.0,
arm64 macOS, Command Line Tools) and `make` on the PATH; the commands,
the exit statuses and the output tails are in each manifest's `runs`.
**These tasks need a native C compiler named `cc` (and `c++` for
simdjson)**: a compiler that is a script is a shell to the runtime gate,
so `runtime` would be `not_available` and no comparison could be a KEEP.

Each workload does a fixed amount of work and has no timer, so a
version's wall time is its cost. The runs in the manifests are single
runs on a Mac that other work was using (load average above 17 while
they ran), and no decision used them: do not read the task and
reference timings as an effect.

`perfproof profile` with `perfproof.profilers.native` on the simdjson
task tree (the hidden fix) ranks the library copy routine first and the
changed function second and third (`studies/native/simdjson-profile.json`,
built with `-g -fno-omit-frame-pointer`); `eval/skill/tasks-native.json`
expects that function, `to_chars` in `src/to_chars.cpp`, in the top
five.

`eval/skill/tasks-native.json` is the skill eval's task file for them
(`uv run python eval/skill/skill_eval.py --tasks-file
eval/skill/tasks-native.json run --label native-1`, with `cc`, `c++` and
`make` on the PATH). The tasks have not been run through the skill: that
waits for the evaluation harness to run as its separate user. The unit
and integration tests of `perfproof.profilers.native`, of
`perfproof.bench.native_coverage` and of the runtime gate run the real
tools natively (`tests/fixtures/native/SOURCE.md`).

The three manifests are `development-candidate`, and
`realrepo.load_spec` accepts only `development` ones, a split that
`eval/mine/draw.py` assigns and `test_committed_splits_match_the_draw`
enforces. So `tasks-native.json` loads with `taskset.load` (checked), and
its tasks run once they are drawn into development or the loader admits
candidates that are not held-out; neither is done here.
