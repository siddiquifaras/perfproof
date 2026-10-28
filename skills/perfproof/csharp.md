# C# and .NET

Read this when the code to speed up is C# (a library, an ASP.NET
service, a console tool). The method is the same as everywhere else;
what differs is how to build and launch the benchmark so the runtime is
identified, how the tiered JIT and the collector enter the noise, how to
profile with dotnet-trace, and what usually goes wrong in .NET code.

## Build the benchmark, then time the application

`compare` only keeps a change when it knows which SDK and runtime run
each arm. It identifies a native `dotnet` started with `build`,
`run`, `test` or `exec`, or with the path of a `.dll`. It asks that
`dotnet` for `--version` (the SDK that `global.json` selects, as the
benchmark would run in its directory) and `--list-runtimes` (what a
target framework rolls forward to), and records both with a hash of the
`dotnet` host; `exec` and a `.dll` ask only for the runtimes. A
`global.json` that asks for an SDK that is not installed makes the
runtime unknown. Two versions whose `global.json` select different SDKs
differ, and the gate says so.

- A `dotnet` that is a script, a version manager's shim or a container
  launcher (`docker run ... dotnet`) is a shell to the gate, so
  `runtime` is `not_available` and a KEEP is withheld. Use the real
  `dotnet` first on the PATH.
- `dotnet tool install`, `tool update`, `tool exec`, `dnx`,
  `add package`, `restore` and `workload install` download code: the
  gate calls them fetching launches. Run `dotnet restore` and the build
  in `setup`, which is not timed and not checked.
- `dotnet run` builds, restores and starts the program as a child on
  every execution, so its time includes the SDK's own work, and it is
  not what the profiler can see (below). Build in `setup` and time the
  application:

```json
"setup": {"commands": [["dotnet", "build", "-c", "Release", "-o", "bin", "App/App.csproj"]],
          "timeout_seconds": 900},
"command": ["dotnet", "bin/App.dll", "--iterations", "300"]
```

- Both versions must be built by the same command, with the same
  `-c Release`. A Debug build, `-p:Optimize=false`, a profiler or a
  coverage collector changes what runs: never time them.
- Give both versions the same `DOTNET_TieredCompilation`,
  `DOTNET_TieredPGO`, `DOTNET_gcServer` and `DOTNET_GCgen0size`; they
  are recorded with their values, and a difference is a warning. Never
  set one in the spec to change a single version.

- A benchmark project you keep under `.perfproof/harness/` is locked, and
  `bin/` and `obj/` written there count as edits to locked files
  (`compare` refuses with the `lock` gate). Copy the sources to a folder
  outside the harness in `setup` and build there, or build with `-o` and
  `-p:BaseIntermediateOutputPath` pointing outside it.
- A build that prints nothing for minutes is hung, not slow. In a
  restricted environment (no network, a locked-down sandbox) the build
  server and node reuse can wedge; run it in one process: `dotnet build
  -m:1 -nodeReuse:false -p:UseSharedCompilation=false`, in `setup` and by
  hand. If restore fails on `NU1900`, the vulnerability audit is trying
  the network: add `-p:NuGetAudit=false`.
- A `global.json` that pins an SDK that is not installed fails every
  `dotnet` command run beneath it. Never edit it: it applies from the
  working directory upward, so run the command from a folder above it
  and pass the project path.

## Warm-up: the tiered JIT

Every execution is a new process, so the spec's `warmup` executions do
not warm the next one; they are a smoke test. Methods start in a
quick-to-compile tier and are promoted to optimised code in the
background once they have been called enough times and the runtime has
been busy for a while (`DOTNET_TC_CallCountingDelayMs`, which is time,
not calls), so the first iterations of a loop are slower. In the
committed curve (`studies/dotnet-warmup/`, .NET 10.0.13, 8 fresh
processes, 1.2 ms an iteration at the steady state) the first iteration
took 7.9 times the steady one at the median (3.4 to 9.4) and the first
30 iterations 1.34 times; the next 30 were at 1.07 and later blocks
within 5% at the median. With `DOTNET_TieredCompilation=0` the first
block was 1.30 and the steady iteration was 1.2 times slower than the
tiered one. One small workload on a Mac that other work was using: a
service with many methods promotes more code over longer, so measure
your own.

So **iterate in process, with a fixed warm-up, and never fail on it**:

```csharp
const int Warmup = 300; // about 10 times what the curve needed
const int Timed = 30;

for (var i = 0; i < Warmup; i++) sink += Work();
var times = new double[Timed];
for (var i = 0; i < Timed; i++)
{
    var start = Stopwatch.GetTimestamp();
    sink += Work(); // one iteration of the real work, at least 1 ms
    times[i] = Stopwatch.GetElapsedTime(start).TotalSeconds;
}
Array.Sort(times);
Console.WriteLine(sink); // the result is live, and is what the oracle compares
Console.WriteLine($"PERFPROOF {{\"value\": {times[Timed / 2].ToString("R", CultureInfo.InvariantCulture)}}}");
```

Use it as a `reported` metric, `lower_is_better`, unit `s` (divide by the
operations in an iteration for a per-operation value). The counts are
fixed, so a workload that never settles still ends, and its spread is
what the interval widens by; exit non-zero only for wrong output. Keep an
iteration at 1 ms or more, so that 300 of them are well past the
promotion delay; an execution costs `(300 + 30) x seconds per
iteration`. For a short-lived command (a CLI, a test run) the start-up
and the JIT are what the user waits for: time the whole
`dotnet bin/App.dll` (`wall`) and do not warm in process. If the project
has BenchmarkDotNet benchmarks, they warm up and measure by their own
rules; run the one that covers the changed code and print its mean as
the `PERFPROOF` line.

The collector is part of the noise: `GC.CollectionCount(0)` before and
after the timed loop counts collections, and
`GC.GetAllocatedBytesForCurrentThread()` counts bytes allocated. A
version that allocates less is faster partly because the collector runs
less, which is the point: report both next to the time.

## Profile

`perfproof profile` runs any command that writes folded stacks.
`perfproof.profilers.dotnet` runs the command under
`dotnet-trace collect --format Speedscope`, reads the trace and writes
folded stacks, weighted in microseconds:

```
perfproof profile <name> --json --own-module App --profiler-command
  '["<python>", "-m", "perfproof.profilers.dotnet", "record",
   "--output", "{output}", "--project-root", "<repo root>", "--",
   "{command}"]'
```

`<python>` is the Python that perfproof itself runs in, beside the
`perfproof` executable. Install the tool once with `dotnet tool install
--global dotnet-trace`; the wrapper finds it on the PATH or in
`~/.dotnet/tools` and says so if it cannot.

- **Profile `dotnet bin/App.dll`, never `dotnet run`.** dotnet-trace
  traces the one .NET process it launches. Under `dotnet run` it traced
  the SDK's host (no frame of the program in the trace, in the committed
  capture) and the command did not end until it was stopped. A command
  that starts no .NET process gives the tool's status 2, and the wrapper
  says why.
- A frame whose type is declared in a `.cs` file under the project root
  is shown as `Prog.Work.JoinSlow (App/Work.cs:8)`: the file of the
  type, and the line of the method's first mention below it (a regular
  expression, not a parser: a generated or partial type shows its first
  file). Every other frame, the runtime and every package, is
  `System.Private.CoreLib`System.String.Concat` with no file, and
  counts as library. Frames of your assemblies that no source is found
  for are shown the same way: name the assembly with `--own-module App`
  so they count as the project's.
- `[unmanaged code]` is time in native code and the runtime's own work
  (including the collector and the JIT); `Thread.PollGC` frames showed
  in the committed capture of an allocating loop. A large share of
  either is an allocation or start-up problem, not a loop.
- The sampling is dotnet-trace's default, a stack of each running thread
  about every millisecond; the share is of that time, summed over
  threads. Time in the runtime's start-up is in it: run the workload for
  seconds, not milliseconds. `frame_kind` says `native` for every .NET
  frame; ignore it.
- In a container, install dotnet-trace in the image, and run the
  wrapper where the application runs, with the project mounted at the
  path the profile will name.

## What usually goes wrong, and the cheap test for each

Run the test on the profile's top frames before writing a change; each is
a count or a share, never a timing to report.

| Suspect | What the profile shows | Cheap test |
|---|---|---|
| Allocation per call | `[unmanaged code]` high; `String.Concat`, `List<T>.Add`, `Enumerable` frames | `GC.GetAllocatedBytesForCurrentThread()` before and after one iteration: bytes per operation; a value made per item that it did not need to be |
| String building in a loop | `String.Concat`, `String.Join`, `SpanHelpers.Memmove` | the same text built into one `StringBuilder` (or `string.Create`) gives the same text with fewer bytes allocated |
| LINQ in a hot path | `Enumerable.*`, closure and iterator frames | the count of calls per item, and bytes allocated per call; the same loop written out gives the same result |
| Boxing | `Object.Box`-style frames, allocation from value types passed as `object` or to a non-generic interface | bytes allocated per call against zero for the same call through a generic overload |
| Collections | `List<T>.Contains`, `IndexOf`, `Dictionary.Resize` | the loop's count against the collection's size; a `HashSet<T>` built once, or a dictionary created with its capacity |
| Regular expressions | `Regex..ctor`, `RegexParser`, `Regex.IsMatch` from one call site | the count of `new Regex` or static `Regex.IsMatch` calls against distinct expressions; one instance made once gives the same results |
| Reflection and dynamic dispatch | `RuntimeMethodInfo.Invoke`, `Activator.CreateInstance`, `GetCustomAttributes` | the count of reflective calls against distinct members: lookups that repeat can be cached |
| Exceptions as control flow | `Exception..ctor`, stack trace frames | `AppDomain.CurrentDomain.FirstChanceException` counted over one iteration |
| Async overhead and blocking | CPU well below wall; thread pool frames; `Task.Wait`, `.Result` | `ThreadPool.ThreadCount` and `ThreadPool.PendingWorkItemCount` over the run; one task per item that does microseconds of work |
| Text conversion in loops | `Number.UInt32ToDecStr`, `DateTime.ToString`, `Int32.Parse` frames | the calls per item as a count; a span-based `TryFormat` into a reused buffer gives the same text |
| Collector pressure | `[unmanaged code]` large with collector frames | `GC.CollectionCount(0)`, `(1)` and `(2)` over one iteration; Gen 2 collections in a steady loop mean a large live heap |
| Database access | wall above CPU; `SqlClient` or provider frames | count commands per operation (a logging provider or an interceptor): one query per row is N+1 |
| Start-up and JIT | a short command whose time is mostly `[unmanaged code]` | `dotnet bin/App.dll` with no work against the command; ReadyToRun or trimming are build changes, not code changes |

Two checks apply to every item: the change must keep the benchmark's
output the same, and a statement about what the JIT does ("this is
inlined", "this does not allocate", "the bounds check is gone") is
believed only with the runtime's output for the version that ran both
(`DOTNET_JitDisasm` on a release runtime, the allocation counter above).

## Coverage

Put `coverlet.collector` in the test project, and run `dotnet test
--collect:"XPlat Code Coverage" --results-directory <dir>`: coverlet
writes `<dir>/<guid>/coverage.cobertura.xml`, whose `<source>` is the
project's directory and whose `filename` values are relative to it. The
coverage check takes a command that writes lcov or Cobertura to the path
in `PERFPROOF_COVERAGE_REPORT` (`correctness.coverage_command`), so
declare one that clears `<dir>`, runs the tests under coverlet and
copies that file there, for example `["sh", "-c", "rm -rf cov && dotnet
test --collect:\"XPlat Code Coverage\" --results-directory cov && cp
cov/*/coverage.cobertura.xml \"$PERFPROOF_COVERAGE_REPORT\""]` with one
test project. Never time a run with the collector on.

## When to stop

As in [discovery.md](discovery.md): when the project's share of the
profile is small and the top frames are the runtime's, the cap says what
is left. A benchmark whose rounds disagree widely is noisy, not slow; say
so and report the range.
