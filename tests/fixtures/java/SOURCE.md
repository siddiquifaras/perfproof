# Java profiler fixtures

Output of the JDK's `jfr print --events jdk.ExecutionSample --stack-depth
128 <recording>.jfr`, untouched, for two recorders running the same
workload.

| File | Recorder |
|---|---|
| `jfr-print.txt` | Java Flight Recorder: `java -XX:+UnlockDiagnosticVMOptions -XX:+DebugNonSafepoints -XX:StartFlightRecording:filename=jfr.jfr,settings=profile,dumponexit=true,stackdepth=128,jdk.ExecutionSample#period=10ms -cp <classes> Base64Workload` |
| `jfr-print-linux.txt` | The same command as `jfr-print.txt`, inside the Linux box |
| `async-profiler-print-linux.txt` | The same command as `async-profiler-print.txt`, with `linux-arm64/libasyncProfiler.so`, inside the Linux box |
| `async-profiler-print.txt` | async-profiler 4.3 as an agent writing JFR: `java -XX:+UnlockDiagnosticVMOptions -XX:+DebugNonSafepoints -agentpath:libasyncProfiler.so=start,event=cpu,interval=10ms,jstackdepth=128,file=ap.jfr,jfr -cp <classes> Base64Workload` |

- Date: 2026-10-22.
- JDK: Eclipse Temurin 21.0.12.1+1 LTS, macOS 15.7.7 arm64 (M4
  MacBook Pro). async-profiler: `tools.profiler:async-profiler:4.3`
  from Maven Central, `macos/libasyncProfiler.so` (its `event=cpu`
  falls back to a CPU timer on macOS).
- Workload: `Base64Workload`, which encodes 16 byte, 1 KiB and 64 KiB
  payloads with `Base64Variants.MIME_NO_LINEFEEDS.encode(byte[],
  boolean)` of FasterXML/jackson-core at commit
  `1c360ab05ec5bd851961419e7b69cd87a47ec660` (the parent of #1674),
  built with `./mvnw -DskipTests package`. The program that ran
  is `eval/tasks/workloads/java/evidence/workload-source.txt`, kept as
  .txt and compiled saved as `Base64Workload.java` (sha256
  `92868074da3506f1d65f8e67b875aeb4076213a868888ccd0b851409db32f5ec`;
  the frame lines 27, 52 and 68 in the fixtures are lines of that file).
  The copy under `eval/tasks/workloads/fasterxml-jackson-core-pr1674/`
  was changed afterwards to print a checksum and is not what ran.
- In the two macOS captures `stackdepth=128` was given as a
  `StartFlightRecording` option, which the JVM ignores (its default of
  64 applied); no stack here is deeper than that. The Linux captures
  set it with `-XX:FlightRecorderOptions:stackdepth=128`.
- The project root for these files is that checkout, so
  `tools.jackson.core.Base64Variant` is `src/main/java/tools/jackson/
  core/Base64Variant.java` there.

What the two show, as the tools printed it:

- Frames are leaf first, `Class.method(params) line: n`, and a stack
  deeper than the limit ends in `...`.
- async-profiler samples every thread, so its file also holds the
  JIT compiler threads' native frames
  (`libjvm.dylib.Compile::Code_Gen() line: 0`) and a `state` of
  `STATE_DEFAULT`; Flight Recorder's samples are all `STATE_RUNNABLE`
  threads running Java code.
- 75 samples in `jfr-print.txt`, 108 in `async-profiler-print.txt`, 107
  in `jfr-print-linux.txt`, 103 in `async-profiler-print-linux.txt`.
- The Linux files: `eclipse-temurin:21-jdk` (Temurin 21.0.12.1+1) in
  Docker Desktop's VM, Linux 6.12.54-linuxkit aarch64, built from
  `eval/tasks/workloads/java/Dockerfile`, workload built there with
  `./mvnw package`. Native frames read `libjvm.so.<symbol>()`.
