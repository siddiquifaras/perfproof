# Go

Read this when the code to speed up is Go. The method is the same as
everywhere else; what differs is how to build and launch the benchmark
so the runtime can be identified, how the garbage collector enters the
noise, how to profile with pprof, and what usually goes wrong in Go
code.

## Build the benchmark, then time the executable

`compare` only keeps a change when it knows which Go built and runs
each version. It identifies a command that starts a native `go` with
`run`, `test`, `build` or `vet`: it asks that `go` which toolchain
`go.mod` selects (version, platform, settings that change the code it
generates, a hash of its `go`). It identifies a built executable by
the toolchain named in its header. It cannot identify a `go` that is a
script (a version manager's shim, a launcher that runs Go in a
container), because the program that script runs is not the one
launched: `runtime` is `not_available` and a KEEP is withheld. Use the
real `go` first on the PATH.

`go run` and `go test` compile, link and start the program as a child
every time, so the time of the command includes the `go` command's own
start-up (25 ms at the median for an empty program, against 1.3 ms for
its binary, in the runs in `docs/method.md`). Build in `setup`, which
is not timed and is not checked, and time the executable:

```json
"setup": {"commands": [["go", "mod", "download"],
                       ["go", "test", "-c", "-o", "bin/pkg.test", "./pkg"]],
          "timeout_seconds": 900},
"command": ["bin/pkg.test", "-test.run", "^$", "-test.bench", "^BenchmarkX$",
            "-test.benchtime", "2000000x", "-test.count", "1"]
```

- `go test -c` builds one package's test binary, benchmarks included;
  for a program, `go build -o bin/x ./cmd/x`. Both versions must be
  built by the same command; the binary does not need to be committed.
- Leave `GOTOOLCHAIN` alone. If one version's `go.mod` asks for another
  toolchain than the other's, the `runtime` gate fails, and that is a
  real difference. The first `go` that needs a toolchain it does not
  have downloads it.
- Download dependencies in `setup` (`go mod download`), so nothing is
  fetched while timing. `go run example.com/tool@version`, `go install
  ...@version` and `go get` fetch code, so `runtime` is
  `not_available` and a KEEP is withheld.
- Do not time with `-race`, `-cover` or `-gcflags=-N -l`: they change
  what runs. Give both versions the same `GOGC`, `GOMAXPROCS` and
  `GOMEMLIMIT`, and never set one in the spec to change a single
  version's collector.
- `-test.benchtime Nx` runs exactly N iterations, which is what to
  use: the default adapts N to the time, so the two versions would do
  different amounts of work. Choose N so one execution takes at least
  half a second and holds tens of collections (below).

## Noise: there is no warm-up, there is the collector

Go compiles ahead of time, so there is no JIT and no phase of slow
iterations to discard; the numbers are in "Warm-up (Go)" in
`docs/method.md`. What remains is the collector and the machine:

- **Count the collections.** `GODEBUG=gctrace=1 bin/pkg.test ...` prints
  one line starting `gc ` per collection on stderr: `grep -c '^gc '`.
  An execution with one or two collections is decided partly by whether
  they fall in the timed part; one with dozens is not. Raise N until it
  holds dozens.
- **Allocation is the cost to cut, not the noise to hide.** A version
  that allocates less is faster partly because the collector runs less.
  `-test.benchmem` prints `B/op` and `allocs/op`: report them next to the
  time.
- If you want the time of the work alone, measure inside the program:
  a `reported` value, the median of blocks, so one collection or one
  scheduling pause in a block does not decide it. These numbers are
  provisional (chosen by reasoning and one set of runs, not calibrated);
  a block should take tens of milliseconds. There is no stop rule, so
  nothing can end in "did not settle"; exit non-zero only for wrong
  output, which perfproof turns into a REJECT on correctness:

```go
var sink int // the result is live, or the compiler may drop the work

func TestPerfproof(t *testing.T) {
	if os.Getenv("PERFPROOF") == "" {
		t.Skip("run by perfproof")
	}
	const blocks, perBlock = 21, 20000 // a block is tens of milliseconds
	perOp := make([]float64, blocks)
	for b := range perOp {
		start := time.Now()
		for i := 0; i < perBlock; i++ {
			sink += work() // one operation of the real work
		}
		perOp[b] = time.Since(start).Seconds() / perBlock
	}
	sort.Float64s(perOp)
	fmt.Println(sink) // also what the oracle compares
	fmt.Printf("PERFPROOF {\"value\": %s}\n",
		strconv.FormatFloat(perOp[blocks/2], 'g', -1, 64))
}
```

  Run it as `bin/pkg.test -test.run '^TestPerfproof$'` with `PERFPROOF`
  set in the spec's `env`; the metric is `reported`, `lower_is_better`,
  unit `s`. The 21 blocks take `21 x seconds per block` an execution:
  say the expected time first.

## Profile

`perfproof profile` runs any command that writes folded stacks. pprof
writes its own format, so `perfproof.profilers.go` runs the benchmark
with a profile switched on, reads the pprof file itself (it needs no Go
on the machine that converts it) and writes folded stacks with
function, file and line:

```
perfproof profile <name> --json --profiler-command '["<python>", "-m",
  "perfproof.profilers.go", "record", "--output", "{output}",
  "--project-root", "<repo root>", "--", "{command}"]'
```

`<python>` is the Python that perfproof itself runs in: the `python`
beside the `perfproof` executable (`dirname $(command -v perfproof)`),
which has `perfproof.profilers.go` installed with it.

How the profile gets switched on depends on the command, and the wrapper
says so when nothing was written:

- `go test ...`: it adds `-cpuprofile` and `-o <dir>/` (so the test
  binary is not left in the working directory). One package only.
- a compiled test binary (`bin/pkg.test ...`): it adds
  `-test.cpuprofile=<file>`.
- anything else, `go run` and a built program included: it sets
  `PERFPROOF_PPROF` (the file to write) and `PERFPROOF_PPROF_KIND` in
  the environment. A program that has no `go test` to carry the flags
  needs this helper, in its `main` package, and `defer
  startProfile()()` first in `main`; with the variable unset it does
  nothing, so the same program is the one that is timed:

```go
// startProfile writes a pprof profile to $PERFPROOF_PPROF when it is
// set, of the kind named by $PERFPROOF_PPROF_KIND (cpu, mem, mutex or
// block; cpu when empty). The function it returns stops the profile
// and writes the file. With the variable unset it does nothing, so the
// same program is the one that is timed.
func startProfile() func() {
	path := os.Getenv("PERFPROOF_PPROF")
	if path == "" {
		return func() {}
	}
	file, err := os.Create(path)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	write := func(name string) func() {
		return func() {
			if err := pprof.Lookup(name).WriteTo(file, 0); err != nil {
				fmt.Fprintln(os.Stderr, err)
			}
			file.Close()
		}
	}
	switch os.Getenv("PERFPROOF_PPROF_KIND") {
	case "", "cpu":
		if err := pprof.StartCPUProfile(file); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(2)
		}
		return func() {
			pprof.StopCPUProfile()
			file.Close()
		}
	case "mem":
		runtime.MemProfileRate = 4096
		return write("allocs")
	case "mutex":
		runtime.SetMutexProfileFraction(1)
		return write("mutex")
	case "block":
		runtime.SetBlockProfileRate(1)
		return write("block")
	}
	fmt.Fprintln(os.Stderr, "PERFPROOF_PPROF_KIND must be cpu, mem, mutex or block")
	os.Exit(2)
	return nil
}
```

Add `"--profile", "mem"` (or `mutex`, `block`) to the command for the
other profiles; each is read by its own sample type (`cpu`; `alloc_space`
for memory, with `--sample-type alloc_objects`, `inuse_space` or
`inuse_objects`; `delay` for mutex and block, with `--sample-type
contentions`). The raw `.pprof` stays in `<output>.pprof.d`, and `go
tool pprof` reads it for anything the summary does not show.

What to know when reading the result:

- **Look twice.** The default view ranks the frame a sample ends in, and
  in Go that is nearly always the runtime or the standard library
  (`runtime.mallocgc`, `runtime.memmove`, `runtime.mapaccess1_faststr`),
  whoever called it. Run again with `"--own-frames"` before `--`: each
  stack is cut below its deepest frame in the project's own files, so
  the time of every call the project makes is charged to the line that
  made it. The shares then say which project line the time is spent for,
  not where the CPU was; read both.
- `function` of the project's own code is `Func`, `Type.Method` or
  `pkg.Func` (the module path is dropped; `file` says where it is) and
  a closure ends `.func1`; the standard library and dependencies keep
  their import path (`net/url.(*URL).EscapedPath`) and `is_library` is
  true. `file` and `line` point into the repository for the project's
  code. `frame_kind` says `native` for every Go frame; ignore it.
- A CPU profile is on-CPU time sampled 100 times a second. A run of a
  second has about a hundred samples, so a small share is a few samples;
  the wrapper says when less than a second was sampled. Time spent
  waiting (a lock, a channel, I/O) is not in it: if CPU time is well
  below wall time, take the `mutex` and `block` profiles.
- Time in the collector shows as `runtime.gcBgMarkWorker`,
  `runtime.scanobject`, `runtime.gcDrain`: a large share is an
  allocation or live-heap problem, not a loop. Background workers run
  on other cores, so the CPU profile's total can be above the wall time.
- When Go runs in a container with the repository mounted at `/src`, the
  profile names `/src/...`: add `"--path-map", "/src=<repo root>"` and
  put `"--profile-dir", "<repo root>/.perfproof/pprof"` (a new
  directory) inside the mount, so the command writes where the wrapper
  reads. The wrapper writes the paths as the command sees them.
  `-trimpath` builds name files `<module path>/file.go`, which the
  wrapper maps through `go.mod`.

## What usually goes wrong, and the cheap test for each

Run the test on the profile's top frames before writing a change; each
is a count or a share, never a timing to report.

| Suspect | What the profile shows | Cheap test |
|---|---|---|
| Allocation per call | `runtime.mallocgc`, `runtime.newobject`, `runtime.growslice` high; collector frames | `-test.benchmem`: `allocs/op` and `B/op`; the `mem` profile by `alloc_objects` names the lines: a value made per item that it did not need to |
| Escape to the heap | allocations at a line that makes a small value or a closure | the compiler's escape report for the package (`-gcflags=-m=1`, on stderr), read for that file: `escapes to heap` and `moved to heap: x`. What escapes differs per Go release, so only that output is believed |
| String and byte conversions | `runtime.concatstrings`, `runtime.slicebytetostring`, `runtime.stringtoslicebyte`, `fmt.Sprintf` frames | `allocs/op` per call; the same text built once into a `strings.Builder` (with `Grow`) or `strconv.Append*` gives the same output with fewer allocations |
| Slice and map growth | `runtime.growslice`, `runtime.mapassign`, `runtime.growWork`, `runtime.hashGrow` | the frame's share; build with the size known (`make([]T, 0, n)`, `make(map[K]V, n)`) and see whether the frame vanishes and `allocs/op` drops |
| Map lookups in a hot path | `runtime.mapaccess*`, `runtime.memhash*`, `runtime.aeshashbody` | the lookups per item as a count (a counter in a throwaway copy); the same key looked up twice in one pass, or a map that holds a handful of entries |
| Interfaces and reflection | `reflect.*`, `runtime.convT*`, `runtime.assertE2I`, `runtime.typeAssert` | the count of reflect calls against the count of distinct types: work that repeats per type can be done once and kept |
| Regular expressions and formatting in a loop | `regexp.compile`, `regexp/syntax.Parse`, `fmt.(*pp).doPrintf` | the count of `regexp.MustCompile` calls against distinct patterns; a pattern compiled once at package level gives the same matches |
| Lock contention | CPU time well below wall time; `sync.(*Mutex).lockSlow`, `runtime.semacquire` | `"--profile", "mutex"` (sample type `contentions`, then `delay`): the lock and the line that holds it; the same work on one goroutine against many |
| Blocking on channels or I/O | CPU well below wall; `runtime.gopark`, `runtime.selectgo`, `runtime.chanrecv` | `"--profile", "block"`: where goroutines wait and for how long; one operation per message is a count to take |
| Too many goroutines | `runtime.newproc`, scheduler frames | `runtime.NumGoroutine()` printed at the end of the benchmark; a goroutine started per item that does a few microseconds of work |
| Unbuffered writes | `syscall.write`, `internal/poll.(*FD).Write` with a high count of small calls | the count of calls per item (a counting `io.Writer` in a throwaway copy); going through a `bufio.Writer` gives the same output |
| Collector pressure | `runtime.gcBgMarkWorker`, `runtime.scanobject` large; a big live heap of pointers | `GODEBUG=gctrace=1`: the count of collections and the heap size each ends at; `perfproof measure <name> --set GOGC=400` bounds what the collector costs, and is a test, not a change to ship |
| Object reuse | many short-lived buffers of the same size | `allocs/op` before and after reusing one (`sync.Pool`, or a field the caller resets); a pooled value that escapes still allocates, so check `-gcflags=-m` on it |

Two checks apply to every item before it counts: the change must keep
the benchmark's output the same, and a statement about what the
compiler does ("this is inlined", "this does not escape", "the bounds
check is gone") is believed only with the compiler's output
(`-gcflags=-m=2`, `-gcflags=-d=ssa/check_bce/debug=1`) for the Go
version that built both versions.

## Coverage

`go test -coverprofile=cover.out -covermode=set ./pkg` writes the
coverage of one run of the tests, and a program built with `go build
-cover` writes counters to `$GOCOVERDIR` that `go tool covdata textfmt
-i=$GOCOVERDIR -o cover.out` turns into the same text. `python -m
perfproof.bench.go_coverage --profile cover.out --root <module root>
--output lcov.info` turns it into lcov, one record per file with the
lines that hold a statement and how often they ran; a line counts as
run only if every block with code on it ran. Never time a binary built
with `-cover`.

Declare it as the coverage command (`coverage_timeout_seconds` too);
`go` needs no install, and `<python>` is the Python that runs perfproof:

```json
"coverage_command": ["sh", "-c", "go test -coverprofile=cover.out -covermode=set ./... && <python> -m perfproof.bench.go_coverage --profile cover.out --root . --output \"$PERFPROOF_COVERAGE_REPORT\""]
```

Proven by running the two steps by hand on a small module (profile
then lcov: the line a test ran had count 1, the line of an uncalled
function 0). Not yet run through `perfproof compare` for Go.

## When to stop

As in [discovery.md](discovery.md): when, with `--own-frames`, the
project's share is small and the top frames are the runtime's or the
standard library's, the cap says what is left. A benchmark whose rounds
disagree widely is noisy, not slow; say so and report the range.
