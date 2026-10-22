# Go task support

Three manifests for the Go task (T5.12), each with a workload that is a
Go benchmark copied into the tree and an oracle that is the project's own
tests:

| Manifest | Family | Workload |
|---|---|---|
| `labstack-echo-pr1799` | hidden-fix | `ServeHTTP` on a path with one percent-encoded segment (`BenchmarkEvalServeHTTPPath`) |
| `gin-gonic-gin-pr4339` | known-change | `GetQueryMap` on a query of about 450 parameters (`BenchmarkEvalQueryMap`) |
| `gin-gonic-gin-pr4246-landed` | nothing-to-keep | registering 240 routes on a new engine (`BenchmarkEvalAddRoutes`) |

Their workloads and oracles ran on both trees with `eval/mine/check.py`
(status `runs`) with **native Go on the PATH**, `GOTOOLCHAIN=local`
(go1.25.14 darwin/arm64 from go.dev, in `~/.local/go`); the commands and
the output tails are in each manifest's `runs`. **These tasks need
native Go**: a `go` that is a script (a version manager's shim, a
container launcher) is a shell to the runtime gate, so `runtime` would be
`not_available` and no comparison could be a KEEP. The first runs, in the
official `golang:1.25` image under Docker, gave the same pass or fail
and are replaced by the native ones.

The workload runs are single runs on a Mac that other work was using,
and no decision used them. The two trees of the landed task are the same
commit; in the Docker runs they recorded 123,100 and 92,009 ns/op, so a
difference of that size between two runs was noise there (the native
runs are in the manifest).

`eval/skill/tasks-go.json` is the skill eval's task file for them
(`uv run python eval/skill/skill_eval.py --tasks-file
eval/skill/tasks-go.json run --label go-1`, with `go` on the PATH). The
tasks have not been run through the skill: that waits for the
evaluation harness to run as its separate user. The unit and
integration tests of `perfproof.profilers.go` and of the runtime gate
run real Go natively (`tests/fixtures/go/SOURCE.md`).

The three manifests are `development-candidate`, and
`realrepo.load_spec` accepts only `development` ones, a split that
`eval/mine/draw.py` assigns and `test_committed_splits_match_the_draw`
enforces. So `tasks-go.json` loads with `taskset.load` (checked), and
its tasks run once they are drawn into development or the loader
admits candidates that are not held-out; neither is done here.
(`tasks-java.json` is in the same position, and its `match` key is not
a field of the loader's `Hotspot`.)


`studies/go/echo-profile.txt` is `perfproof profile` on the echo task through the Docker
launcher (before native Go was installed).
