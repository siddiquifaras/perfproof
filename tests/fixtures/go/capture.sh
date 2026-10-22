#!/bin/sh
# Capture the Go fixtures in this directory from real runs of the Go
# toolchain, inside the official golang image.
#
# Usage: tests/fixtures/go/capture.sh <out-dir>
#
# The fixture module (tests/fixtures/go/prog) is mounted at /src and
# <out-dir> at /out, so every profile names its files as /src/<file>.
# Output is the raw output of each tool; nothing is edited.
set -eu
out=${1:?usage: capture.sh <out-dir>}
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$out"
out=$(cd "$out" && pwd)

inner='
set -eu
cd /src
go version > /out/go-version.txt
go env -json GOROOT GOVERSION GOOS GOARCH GOFLAGS GOEXPERIMENT CGO_ENABLED GOAMD64 GOARM64 GOTOOLCHAIN > /out/go-env.json

go test -run "^$" -bench "^BenchmarkJoin$" -benchtime 400x \
  -cpuprofile /out/cpu.pprof -o /out/ . > /out/cpu-test.txt
go test -run "^$" -bench "^BenchmarkJoin$" -benchtime 400x \
  -memprofile /out/mem.pprof -memprofilerate 4096 -o /out/ . > /out/mem-test.txt
go test -run "^$" -bench "^BenchmarkContend$" -benchtime 5x \
  -mutexprofile /out/mutex.pprof -mutexprofilefraction 1 -o /out/ . > /out/mutex-test.txt
go test -run "^$" -bench "^BenchmarkContend$" -benchtime 5x \
  -blockprofile /out/block.pprof -blockprofilerate 1 -o /out/ . > /out/block-test.txt

go build -o /out/hot ./cmd/hot
go version -m /out/hot > /out/hot-version-m.txt
PERFPROOF_PPROF=/out/hot-cpu.pprof /out/hot > /out/hot-run.txt

go test -count 1 -covermode=set -coverprofile=/out/cover-set.out . > /out/cover-set-test.txt
go test -count 1 -covermode=count -coverprofile=/out/cover-count.out . > /out/cover-count-test.txt
go tool cover -func=/out/cover-set.out > /out/cover-func.txt

go build -cover -o /out/hot-cover ./cmd/hot
mkdir -p /out/covdata
GOCOVERDIR=/out/covdata /out/hot-cover > /dev/null
go tool covdata textfmt -i=/out/covdata -o /out/covdata.txt

go tool pprof -raw /out/cpu.pprof > /out/cpu-raw.txt
go tool pprof -raw /out/mem.pprof > /out/mem-raw.txt
go tool pprof -raw /out/mutex.pprof > /out/mutex-raw.txt
go tool pprof -raw /out/block.pprof > /out/block-raw.txt
go tool pprof -top /out/cpu.pprof > /out/cpu-top.txt
'

nice docker run --rm --init --stop-timeout 10 -e GOTOOLCHAIN=local \
  -v "$here/prog":/src -v "$out":/out -v perfproof-gomod:/go/pkg/mod \
  golang:1.25 sh -c "$inner"
