# Target classes

Every class is proven by the same generic workload: a command, a
metric, the gates and the verdict in [method.md](method.md). What
differs per class is written here: what "same output" means, which
guardrails apply and how they're measured, which helper runs the
workload, and the boundary of what the release supports.

The boundaries below are proposals until each class's contract task in
`.claude/plan.md` confirms them against its fixture and case study.
Anything outside a boundary still runs as a generic command workload:
it is measured and proven, but the playbook's advice and the class's
calibration don't apply, and the report says so.

## Python

| | |
|---|---|
| Workload | An existing benchmark, a slow test, or an entry point; `wall`, `cpu` or `reported` |
| Oracle | The project's tests, output comparison, replay of captured real calls |
| Coverage | coverage.py on changed lines |
| Guardrails | Peak memory of the process tree, 10% |
| Profiling | py-spy on Linux; on macOS an in-process sampler (pyinstrument), since py-spy needs root there; Scalene and memray through the playbook |
| Supported | CPython 3.10 and later on macOS arm64 and Linux x86_64 |
| Outside | Free-threaded builds, PyPy, notebooks as workloads |

## Node and TypeScript

| | |
|---|---|
| Workload | An existing benchmark, a slow test, or an entry point run with `node` (`node bench.js`, `node --import tsx bench.ts`, `node_modules/.bin/tsx bench.ts`, `npx --no-install tsx bench.ts`, `npm exec --no -- tsx bench.ts`); `reported` for in-process work, `wall` only when start-up is the work |
| Oracle | The project's tests (`vitest`, `jest`, `node --test`), output comparison |
| Coverage | `c8` (lcov) on changed lines: designed, not wired until the coverage gate takes a command that writes lcov (T2.24); until then reported as not checked |
| Guardrails | Peak memory of the process tree, 10% |
| Profiling | `python -m perfproof.profilers.node run` as a `--profiler-command`: V8's CPU profiler through a preload, source maps applied, so `tsx` and bundled TypeScript show TypeScript `file:line` |
| Warm-up | In the process, a fixed count of iterations discarded and a fixed window reported ("Warm-up (Node)" in [method.md](method.md#warm-up-node)) |
| Supported | Node 18.20.8, 20.20.2 and 24.13.0 (`tests/fixtures/node/versions` holds the profiler's output on each) on macOS arm64; TypeScript through `tsx` and Node's own type stripping (both with committed tests), or compiled with `esbuild` and a source map. `ts-node`, `esbuild-register` and `@swc-node/register` gave correct lines once on Node 20, with no committed fixture |
| Outside | `pnpm`, `yarn`, `bun`, `deno` and `npm run` as the benchmark command (the runtime gate cannot follow them); browser code; Node programs that mostly wait on a service |

What the runtime gate sees and does not (measured with
`probe_environment` on Node 24.13.0): `node`, `node --import tsx`,
`node --require esbuild-register`, `node -r ts-node/register`,
`node --loader tsx`, `node --experimental-strip-types`,
`node_modules/.bin/tsx` and `ts-node`, `npx --no-install tsx` and
`npm exec --no -- tsx` are identified, with the Node's path, version
and SHA-256. `npm run`, a bare `tsx` or `bun` that is not on `PATH`
are not. The loader's own version (`tsx`, `esbuild`, `typescript`) is
not in the runtime record; it is covered only by the lockfile hash, and
`bun.lock` is not among the lockfiles hashed.

TypeScript run through a loader is not the code that ships. `tsx` adds
`__name` calls (esbuild's `keepNames`) around every function, which
shows as time in `__name` at line 1 of the file: 1.4% of the total
for one project file in the committed tsx fixture (1.4% total, 0.7% self;
the only `__name` in it), and none in the project's files in the
committed `esbuild` bundle fixture. A profile to find
hotspots can use the loader; a number to compare versions should come
from one launch form used for both versions.

## Native: C, C++, Rust

| | |
|---|---|
| Workload | A benchmark binary or test binary, built per tree; `wall`, `reported`, or `counter` on Linux |
| Oracle | Output comparison between the two builds, and the project's tests |
| Coverage | llvm-cov or gcov on changed lines |
| Guardrails | Peak memory, 10% |
| Profiling | samply or perf, turned into folded stacks |
| Supported | Projects built with make, CMake or Cargo, on macOS arm64 and Linux x86_64 |
| Outside | Windows, embedded targets, builds that need hardware not present |

## Java and other JVM code

| | |
|---|---|
| Workload | A JMH benchmark run through a runner that prints its primary score (`reported`), or a program that warms up in process and prints its post-warm-up time (`reported`), or the whole command for a short-lived tool (`wall`); see "Warm-up (Java)" in [method.md](method.md) |
| Oracle | The project's tests (`./mvnw test -Dtest=...`, `./gradlew test --tests ...`) and the program's output compared between versions |
| Coverage | JaCoCo on changed lines; not wired into the coverage gate until T2.24 lands, so reported as not checked |
| Guardrails | Peak memory of the process tree, 10% |
| Runtime | The command must start `java` itself, so that the runtime gate can identify it (version banner and a hash of the launcher). `./mvnw`, `mvn`, `./gradlew` and `gradle` are shell scripts, so they are `not_available`; the build runs in `setup` |
| Profiling | Java Flight Recorder or async-profiler, turned into folded stacks with class, method, file and line by `perfproof.profilers.java` |
| Supported | JDK 21 (Temurin) on macOS arm64 and on Linux arm64 in Docker, with the profiler wrapper's fixtures captured on both, as run for T5.11; other JDKs and x86_64 are expected to work and have not been run |
| Outside | Source files of Kotlin and Scala classes (shown as library code), Android, GraalVM native images, JDK 8, code that needs a running application server |

## Rust

| | |
|---|---|
| Workload | The built release binary (`target/release/<name>`, built in `setup`), or a benchmark program in `.perfproof/harness/` that loops in process and prints its time (`reported`); see "Warm-up (Rust)" in [method.md](method.md) |
| Oracle | The project's tests (`cargo test --release --locked`) and the binary's output compared between versions |
| Coverage | `-C instrument-coverage` and the toolchain's `llvm-profdata` and `llvm-cov`, written as lcov by `perfproof.bench.rust_coverage`; not read by the coverage gate until T2.24 lands, so reported as not checked |
| Guardrails | Peak memory of the process tree, 10% |
| Runtime | The command must be the built binary, so that the runtime gate can read the compiler that built it (commit of `rustc`, from the binary). `cargo run`, `cargo bench` and `rustc` are `not_available`, because rustup picks the toolchain and cargo builds inside the timed run |
| Profiling | `sample` on macOS and `perf` (CPU-clock event) on Linux, turned into folded stacks with demangled symbols by `perfproof.profilers.rust`; symbols only, no source lines |
| Supported | rustc 1.94.1 on macOS arm64 (profiled with `sample`) and on Linux arm64 in Docker (profiled with perf 6.12), as run for T5.13; x86_64 Linux, other toolchains and `cargo-llvm-cov` were not run |
| Outside | Windows, `no_std` and embedded targets, async runtime tracing (the playbook says what to look at), the v0 symbol mangling (not decoded) |
## Go

| | |
|---|---|
| Workload | A Go benchmark or program built in `setup` and timed as the built executable (`go test -c` and the test binary, or `go build -o`), by `wall` or by a `reported` median of in-process blocks; see "Warm-up (Go)" in [method.md](method.md) |
| Oracle | The project's tests (`go test -run ... ./pkg`) and the program's output compared between versions |
| Coverage | `go test -coverprofile` (or `go build -cover` with `go tool covdata textfmt`), turned into lcov by `perfproof.bench.go_coverage`; it plugs into the coverage gate when T2.24 lands and is reported as not checked until then |
| Guardrails | Peak memory of the process tree, 10% |
| Runtime | A native `go` with `run`, `test`, `build` or `vet` is identified by the toolchain `go.mod` selects (version, platform, settings and a hash of its `go`); a built executable is compared by the toolchain named in its header. A `go` that is a script (a version manager's shim, a container launcher) is a shell, so `not_available` |
| Profiling | pprof (CPU, memory, mutex, block) turned into folded stacks by `perfproof.profilers.go`, which reads the profile itself and needs no Go on the machine that converts it |
| Supported | Go 1.25 on Linux arm64 in Docker (the official `golang:1.25` image), with the fixtures captured there and the profiler's integration tests run there, as run for T5.12; macOS and x86_64 are expected to work for the converters and have not been run with a Go toolchain |
| Outside | Windows, cgo-heavy programs (not run), programs with no way to write a profile (see `skills/perfproof/go.md`), Go older than 1.18 for the built-executable check |

## Ruby

| | |
|---|---|
| Workload | A Ruby program or a gem's test or benchmark code run with a native `ruby`, timed as a whole (`wall`) or by a `reported` median of in-process iterations after a fixed warm-up (YJIT); see "Warm-up (Ruby)" in [method.md](method.md) |
| Oracle | The project's tests (Minitest, RSpec) and the program's output compared between versions |
| Coverage | SimpleCov's `.resultset.json`, turned into lcov by `perfproof.bench.ruby_coverage` (checked line by line against simplecov-lcov's own report); declared as `correctness.coverage_command` |
| Guardrails | Peak memory of the process tree, 10% |
| Runtime | A `ruby` (a version manager's shim included) is identified by `RUBY_DESCRIPTION` and a hash of the executable it reports; `bundle exec COMMAND` and binstubs are followed. `rbenv exec`, `rvm`, `asdf`, `mise`, other `bundle` subcommands and a `ruby` inside a container launcher are `not_available` |
| Profiling | stackprof (CPU or wall) turned into folded stacks with method, file and line by `perfproof.profilers.ruby`, which starts it in every Ruby the command runs |
| Supported | CRuby 4.0.7 with YJIT, stackprof 0.2.28 and SimpleCov 1.3.2 on Linux arm64 in Docker (the official `ruby:4.0` image), with the fixtures captured there and the wrapper's integration tests run there; the gate's probe also ran against this Mac's system Ruby 2.6.10. A Rails task runs only in Docker here (Rails needs a newer Ruby than the Mac's), so its runtime is `not_available` and a KEEP is withheld there until a native Ruby is installed |
| Outside | JRuby and TruffleRuby (the probe may identify them; nothing else was run), `--zjit`, Windows, Ruby older than 3.0 (stackprof's `raw_lines` and the YJIT flags were not run on it) |

## C# and .NET

| | |
|---|---|
| Workload | A .NET application built in `setup` and timed as `dotnet bin/App.dll` by `wall`, or by a `reported` median of in-process iterations after a fixed warm-up (tiered JIT); see "Warm-up (.NET)" in [method.md](method.md) |
| Oracle | The project's tests (`dotnet test`, xunit, NUnit, MSTest) and the program's output compared between versions |
| Coverage | coverlet's Cobertura report (`dotnet test --collect:"XPlat Code Coverage"`), which the coverage check reads directly; declared as `correctness.coverage_command` |
| Guardrails | Peak memory of the process tree, 10% |
| Runtime | A native `dotnet` with `build`, `run`, `test` or `exec`, or a `.dll`, is identified by the SDK `global.json` selects, the installed runtimes and a hash of the host. A `dotnet` that is a script or a container launcher, and every other subcommand, is `not_available` |
| Profiling | dotnet-trace (`--format Speedscope`) turned into folded stacks by `perfproof.profilers.dotnet`, with file and line found by reading the project's `.cs` files; the application is profiled as `dotnet bin/App.dll`, never `dotnet run` |
| Supported | .NET SDK 10.0.402 and runtime 10.0.13 on Linux arm64 in Docker (the official `mcr.microsoft.com/dotnet/sdk:10.0` image), with the fixtures captured there and the integration tests run there, as run for T5.14. This Mac has no .NET, so the eval tasks run in Docker, where `runtime` is `not_available` and a KEEP is withheld until a native .NET is installed |
| Outside | Windows and .NET Framework, Mono, Native AOT, ASP.NET services (not run), source files the regular-expression index cannot place (partial and generated types show their first file) |

## C and C++

| | |
|---|---|
| Workload | A C or C++ program or test binary built in `setup` and timed as the built executable, by `wall` or by a `reported` rate printed by the program; fixed work, not a fixed time; see "Warm-up (C and C++)" in [method.md](method.md) |
| Oracle | The project's tests (its fuzzer or test executable) and the program's output compared between versions |
| Coverage | gcc `--coverage` read with `gcov --json-format`, or clang `-fprofile-instr-generate -fcoverage-mapping` read with `llvm-cov export`, turned into lcov by `perfproof.bench.native_coverage`; it plugs into the coverage gate when T2.24 lands and is reported as not checked until then |
| Guardrails | Peak memory of the process tree, 10% |
| Runtime | A built executable is compared by the toolchain it names: an ELF file's `.comment` (compiler lines), a Mach-O file's SDK and linker version (it names no compiler). A native `cc`, `gcc`, `g++`, `c++`, `clang` or `clang++` as the command is identified by its `--version`, target and a hash of its file. `make`, `cmake`, `ninja` and scripts (including a `gcc` that is a script) are `not_available` |
| Profiling | macOS `sample` and Linux `perf`, turned into folded stacks with `file:line` by `perfproof.profilers.native` |
| Supported | Apple clang 17.0.0 on macOS arm64 (`sample`, llvm-cov, the runtime gate) and gcc 14.4.0, clang 19.1.7 and perf 6.12.113 on Linux arm64 in the official `gcc` image on Docker Desktop (`tests/fixtures/native/SOURCE.md`, `studies/native/`); x86_64 and other compilers are expected to work and have not been run |
| Outside | Windows and MSVC, Mach-O universal binaries and 32-bit or big-endian ELF files (their toolchain is not read), `perf stat` counters (not run), programs started through `make` or a script as the profiled command |

## Web and CRUD services

| | |
|---|---|
| Workload | `perfproof load`: the server started and health-checked, driven at a constant request rate; p50, p99 or throughput |
| Oracle | Responses to the recorded request mix compared between versions, and the project's tests |
| Coverage | coverage.py for Python services; reported as not checked for other server languages |
| Guardrails | Peak memory 10%; p99 latency 5% when it isn't the primary |
| Side effects | Database in a local container, reset between rounds; non-local services refused |
| Supported | HTTP services that start locally with one command and expose a health endpoint |
| Outside | Multi-service deployments, managed cloud databases, traffic that can't be recorded or described |

## LLM serving

| | |
|---|---|
| Workload | `vllm bench serve` or SGLang's benchmark against a server perfproof starts; throughput or latency, with `/metrics` counters |
| Oracle | Fixed-seed greedy decoding gives identical tokens, or logprobs agree within a declared tolerance |
| Coverage | coverage.py for the Python layers; kernels reported as not checked |
| Guardrails | Peak memory 10%; peak GPU memory 10%; p99 latency 5% when it isn't the primary |
| Where | A GPU host over SSH or Modal; one host per comparison when the versions share an image, the cross-machine design when they don't |
| Supported | vLLM and SGLang on one NVIDIA GPU |
| Outside | Multi-node serving, AMD GPUs, hosted inference APIs |

## GPU kernels and PyTorch

| | |
|---|---|
| Workload | A benchmark script timed with CUDA events (`reported`), or a training or inference step |
| Oracle | Outputs compared within a declared numeric tolerance, per dtype |
| Coverage | coverage.py for the Python layers; CUDA code reported as not checked |
| Guardrails | Peak GPU memory 10%; peak host memory 10% |
| Profiling | torch.profiler, Nsight Systems and Nsight Compute through the playbook |
| Supported | CUDA kernels and PyTorch code on one NVIDIA GPU |
| Outside | Multi-GPU and distributed training, AMD and Apple GPUs for kernel work |

## Agentic and LLM apps

| | |
|---|---|
| Workload | `perfproof replay`: a recorded run of the app's model and tool calls, replayed with each response's recorded latency; end-to-end time, calls, tokens |
| Oracle | The app's final output identical between versions under the same replay, and its requests a subset of the recorded ones |
| Coverage | coverage.py on changed lines |
| Guardrails | Peak memory 10%; p99 latency 5% for request-serving apps |
| Side effects | Model and tool calls served from the recording, never live, during measurement |
| Supported | Python apps that call model and tool APIs over HTTP that can be recorded |
| Outside | Apps whose behaviour depends on live, unrecordable state; changes to prompts or model choice (see below) |

How replay keeps the performance test valid:

- **Recording.** One run of the baseline with every outbound HTTP
  request and response captured, with its latency.
- **Replay.** Each request is matched by method, URL and a hash of its
  body, and answered with the recorded response after the recorded
  latency. Concurrency and ordering changes therefore show up in wall
  time as they would live, without live latency's noise or cost.
- **Divergence.** A candidate may make fewer of the recorded requests
  (removing a wasted call) in any order. A request with no recorded
  match can't be answered: the run stops and the result is
  INCONCLUSIVE, never a KEEP.
- **Correctness.** Both versions get the same responses, so their final
  outputs must be identical. A removed call whose result mattered
  changes the output and fails the gate.
- **Limits.** Replay can't measure provider-side effects: prompt
  caching, rate limits, latency that depends on load. Prompt caching is
  reported as a token and cost change only. A change to a prompt or a
  model makes new requests, so it can't be proven under replay and is
  outside the class's release boundary.

## Release case studies: workload and oracle

Each case study has a workload anyone can rerun and a correctness check
that doesn't depend on perfproof. Checked against each repository's
own files; anything still to confirm is marked.

| Class | Repository | Workload | Correctness check |
|---|---|---|---|
| Python | frames2py | Its own benchmark suites, `uv run python -m benchmarks run`, one cell picked with the developer | Its pytest suite; output comparison of the decoded frames |
| Native | picolm | `make`, then `picolm <model.gguf> -p <fixed prompt> -n 128 -t 0 -s 42 -j 4` on the TinyLlama 1.1B Q4_K_M model already in `models/` (`-t 0` is greedy) | Generated text identical between builds |
| Web | NetBox | Postgres and Redis in local containers, the demo data from `netbox-community/netbox-demo-data` loaded, `perfproof load` against a confirmed mix of REST endpoints | Responses compared on the recorded request mix; `python manage.py test --keepdb` on the touched apps, including NetBox's own query-count tests |
| Serving | vLLM | `vllm serve` with a small model on one L4, `vllm bench serve` on the random dataset; settings and code candidates in one image | A separate check at concurrency 1, temperature 0, on fixed prompts: identical tokens. Batched outputs can differ with batch composition, so the throughput run itself isn't the oracle |
| GPU and PyTorch | nanoGPT | `train.py` with the Shakespeare character config for a fixed number of steps on a T4, `float16` (the T4 has no bfloat16), seed fixed as `train.py` already does (`torch.manual_seed(1337)`) | Validation loss after the fixed steps within a declared tolerance; the declared tolerance is set from the baseline's own run-to-run spread, measured first |
| Agentic | gpt-researcher | One research run under `perfproof replay`, recorded with a local model through Ollama and the DuckDuckGo retriever, both supported in its code, so no API key or paid call is needed; or `REPORT_SOURCE` set to local documents for a fully offline run | Final report identical under the same replay; its pytest suite for the code touched |

To confirm in each class's contract task: the frames2py cell, the NetBox
endpoint mix, the vLLM model, the nanoGPT step count and tolerance, and
the gpt-researcher query and documents.
