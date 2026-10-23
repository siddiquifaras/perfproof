# .NET fixtures

Output of the real tools, untouched, on the small solution in `prog/`
(`App`: quadratic string building, a dictionary updated per item, a loop
of integer arithmetic, a method none of the tests reach; `Tests`: three
xunit tests with coverlet.collector). `capture.sh` makes every file
here, with `prog/` copied to `/work` in the container.

- Date: 2026-10-23.
- SDK and runtime: .NET SDK 10.0.402 and runtime 10.0.13, linux-arm64
  (`dotnet-version.txt`, `list-sdks.txt`, `list-runtimes.txt`,
  `info.txt`), the official `mcr.microsoft.com/dotnet/sdk:10.0` image
  (`mcr.microsoft.com/dotnet/sdk@sha256:747cbade5bfd918a175ac32f8f1d931edec2c7d0d5397a3de72eaef1ac1f66d2`)
  in Docker Desktop's VM, Linux 6.12.54-linuxkit aarch64, on a macOS
  15.7.7 M4 MacBook Pro.
- dotnet-trace 10.0.750501 (`dotnet-trace-version.txt`), installed with
  `dotnet tool install --global dotnet-trace` at capture time.
- coverlet.collector 6.0.4, xunit 2.9.3 (`prog/Tests/Tests.csproj`).
- Command: `tests/fixtures/dotnet/capture.sh <out-dir>`, then the files
  below copied from `<out-dir>`.

| File | What it is |
|---|---|
| `dotnet-version.txt`, `list-sdks.txt`, `list-runtimes.txt`, `info.txt` | `dotnet --version`, `--list-sdks`, `--list-runtimes`, `--info`: what the runtime gate asks and reads |
| `trace.speedscope.json` | `dotnet-trace collect --format Speedscope -- dotnet bin/App.dll 2`: 2 s of the workload, the tool's default profile (`dotnet-common` and `dotnet-sampled-thread-time`); `trace-run.txt` is what the tool printed |
| `run-run.txt`, `run-app-frames.txt` | the same under `dotnet run --no-build -c Release --project App/App.csproj -- 1`: the tool printed that it was stopping the trace (the command did not end by itself, so `timeout 30` stopped it, status 124), and the number of `App!` frames in the resulting Speedscope file, which is 0 (the file itself is not kept) |
| `coverage.cobertura.xml` | `dotnet test Tests/Tests.csproj --collect:"XPlat Code Coverage"`: coverlet's Cobertura report (`test-run.txt` is the test run) |

What the captures show:

- The Speedscope file is an *evented* profile per thread, with frames
  opened and closed at times in milliseconds. Each stack has a
  `CPU_TIME` or `UNMANAGED_CODE_TIME` frame at its leaf and the process,
  `(Non-Activities)`, `Threads` and thread frames at its root. A managed
  frame is `Assembly!Namespace.Type.Method(parameter types)`, with no
  file and no line.
- Coverlet's report names classes (`Prog.Work`) and files relative to its
  `<source>` directory (`Work.cs`, with `/work/App/` as the source), with
  a `hits` count per line.
- Under `dotnet run` the profile is of the SDK host, not of the program.

The Linux integration tests run in an image made by
`tests/fixtures/dotnet/Dockerfile` (the official image, dotnet-trace and
uv): `uv run pytest -m integration tests/profilers/test_dotnet.py
tests/bench/test_lock_dotnet.py`. This Mac has no .NET, so those tests
skip with their reason here.
