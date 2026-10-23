# go-echo fixture

`benchmark.folded.txt`: folded stacks written, untouched, by
`perfproof profile` with its Go recorder (`python -m
perfproof.profilers.go record`, passed with `--profiler-command`) on a Go
benchmark of the `labstack/echo` router. The benchmark calls
`(*Echo).ServeHTTP`, which does no work of its own: its time is in
`net/url` calls.

- Date: 2026-10-23.
- Repository: `github.com/labstack/echo`, commit
  `cffd3efa91c8cb95d104ec1cbebd2e1c0a8981c0`, with one benchmark file
  (`eval_servehttp_bench_test.go`) added.
- Toolchain: `go version go1.25.14 linux/arm64`, the official
  `golang:1.25` image
  (`golang@sha256:699337d620559a59b4a2bb298ad59611e535d2ee755a34cf2d2a98f37578dc80`)
  in Docker Desktop's VM, the checkout mounted at `/src`, so `/src` is
  the project root for this file.
- Command: `go test -count 1 -run '^$' -bench
  '^BenchmarkEvalServeHTTPPath$' -benchtime 2000000x .`, under the
  recorder, on an M4 MacBook Pro (macOS 15.7.7).
- The counts are pprof sample weights in nanoseconds.
