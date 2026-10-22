#!/bin/sh
# Capture the Go warm-up runs under docs/method.md "Warm-up (Go)".
#
# Usage: studies/go/run.sh <echo-tree> <out-dir>
#
# <echo-tree> is labstack/echo at the task commit of the task whose
# manifest is eval/tasks/hidden-fix/labstack-echo-pr1799.json (the
# parent of that pull request). Everything runs in the official
# golang:1.25 image, one container, niced:
#
# 1. blocks.txt: the test binary built once, then run in 12 fresh
#    processes, each printing 60 blocks of 100000 requests.
# 2. startup.txt: the wall time of 30 `go run .` and 30 runs of the
#    built binary of an empty program, in nanoseconds, interleaved.
set -eu
tree=${1:?usage: run.sh <echo-tree> <out-dir>}
out=${2:?usage: run.sh <echo-tree> <out-dir>}
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$out"
out=$(cd "$out" && pwd)
cp "$here/blocks_test.go" "$tree/eval_blocks_test.go"

inner='
set -eu
cd /src
go version > /out/go-version.txt
go test -c -o /tmp/echo.test .
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
  echo "process $i"
  /tmp/echo.test -test.run "^TestEvalBlocks$" -test.v | grep "^block "
done > /out/blocks.txt
cd /noop
go build -o /tmp/noop .
go run . ; /tmp/noop
for i in $(seq 1 30); do
  a=$(date +%s%N); go run . ; b=$(date +%s%N)
  /tmp/noop; c=$(date +%s%N)
  echo "go-run $((b - a))"
  echo "binary $((c - b))"
done > /out/startup.txt
'

nice docker run --rm --init --stop-timeout 10 -e GOTOOLCHAIN=local \
  -v "$tree":/src -v "$here/noop":/noop -v "$out":/out \
  -v perfproof-gomod:/go/pkg/mod golang:1.25 sh -c "$inner"
rm -f "$tree/eval_blocks_test.go"
