# Go fixtures

Output of the real Go toolchain, untouched, on the small module in
`prog/` (quadratic string building, a map that grows without a size
hint, goroutines on one lock, a function none of the tests reach).
`capture.sh` makes every file here, with `prog/` mounted at `/src`, so
the profiles name `/src/work.go`.

- Date: 2026-10-22.
- Toolchain: `go version go1.25.14 linux/arm64` (`go-version.txt`), the
  official `golang:1.25` image
  (`golang@sha256:699337d620559a59b4a2bb298ad59611e535d2ee755a34cf2d2a98f37578dc80`)
  in Docker Desktop's VM, Linux 6.12.54-linuxkit aarch64, on a macOS
  15.7.7 M4 MacBook Pro. `GOTOOLCHAIN=local`.
- Command: `tests/fixtures/go/capture.sh <out-dir>`, then the files
  below copied from `<out-dir>`.

| File | What it is |
|---|---|
| `go-version.txt`, `go-env.json` | `go version`, and `go env -json GOROOT GOVERSION GOOS GOARCH GOFLAGS GOEXPERIMENT CGO_ENABLED GOAMD64 GOARM64 GOTOOLCHAIN` |
| `cpu.pprof` | `go test -run '^$' -bench '^BenchmarkJoin$' -benchtime 400x -cpuprofile cpu.pprof -o <dir>/ .` |
| `mem.pprof` | the same with `-memprofile mem.pprof -memprofilerate 4096` |
| `mutex.pprof` | `-bench '^BenchmarkContend$' -benchtime 5x -mutexprofile mutex.pprof -mutexprofilefraction 1` |
| `block.pprof` | the same with `-blockprofile block.pprof -blockprofilerate 1` |
| `hot-cpu.pprof` | `cmd/hot` built with `go build -o hot ./cmd/hot`, run with `PERFPROOF_PPROF` set (`cmd/hot/profile.go` is the helper `skills/perfproof/go.md` gives) |
| `cpu-raw.txt`, `mem-raw.txt`, `mutex-raw.txt`, `block-raw.txt` | `go tool pprof -raw` of the four profiles above: the tool's own reading of the same files, which `tests/profilers/test_go.py` compares the converter with, stack by stack |
| `cpu-top.txt` | `go tool pprof -top cpu.pprof` (its "Total samples" is the total the converter's weights add up to) |
| `cover-set.out`, `cover-count.out` | `go test -count 1 -covermode=set\|count -coverprofile=... .` |
| `cover-func.txt` | `go tool cover -func=cover-set.out`: per-function percentages, which `tests/bench/test_go_coverage.py` checks the block counts against |
| `covdata.txt` | `go build -cover -o hot-cover ./cmd/hot`, run with `GOCOVERDIR` set, then `go tool covdata textfmt` |
| `hot-version-m.txt` | `go version -m hot`: what the Go tool says `hot` was built with |
| `hot-buildinfo-region.bin` | 512 bytes of the built executable `hot` (sha256 `0a8226831fe4dc0d1dfddc0872fa006d2ce4b453437239c9e56b4536f13bbcf7`) from offset 1900544, where its build-info header starts (`\xff Go buildinf:`, then the version `go1.25.14`); bytes 1900544 to 1901056 of the file, cut out unedited. Its own sha256 is `90881ef778659471e58cade0d9ea5fe5487678afc27fcb6ed301901fd09fda20` |

What the captures show, as the tools printed them:

- A pprof file is gzip-compressed `profile.proto`; each location lists
  its inlined callees first (`sync.(*Mutex).Unlock` before the
  `Contend.func1` it is inlined into), and a stack lists the leaf
  location first.
- The CPU profile has two sample types, `samples/count` and
  `cpu/nanoseconds`; the memory profile four (`alloc_objects`,
  `alloc_space`, `inuse_objects`, `inuse_space`); the mutex and block
  profiles `contentions/count` and `delay/nanoseconds`.
- Coverage file names are import paths (`example.com/prog/work.go`),
  and a block is a range of positions, so one line can hold the end of
  one block and the start of the next.
- The CPU profile's total (`Total samples` in `cpu-top.txt`) is above
  the run's duration (2.42 s against 2.01 s). The cause is not
  measured here; threads running at once (the collector's workers
  beside the benchmark) would do it.

The Linux integration tests (`uv run pytest -m integration tests/bench/test_lock_go.py tests/profilers/test_go.py`) run the real `go` and need it on the
`PATH`; they skip otherwise.
