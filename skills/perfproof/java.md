# Java and other JVM code

Read this when the code to speed up is Java, built with Maven or
Gradle (Kotlin and Scala classes are profiled too, but their source
files are not mapped, so they show as library code). The method is the same as
everywhere else; what differs is how to launch the benchmark so it can
be identified, how to profile it, and how to keep the JIT from
deciding the answer.

## Launch the benchmark so the runtime can be identified

`compare` only keeps a change when it knows which Java runs each
version. It identifies a command that starts `java` directly. It
cannot identify a build tool, because each is a shell script that
chooses a JDK itself: `./mvnw`, `mvn`, `./gradlew` and `gradle`, like
`sh -c`, give `runtime` `not_available` and a KEEP is withheld
(`env java ...` is followed and fine). Build in `setup`, which is not timed and is not
checked, and run `java` itself:

```json
"setup": {"commands": [["./mvnw", "-q", "-B", "-DskipTests", "package"],
                       ["javac", "-cp", "target/classes", "-d",
                        "target/perfproof-wl", ".perfproof/harness/Bench.java"]],
          "timeout_seconds": 900},
"command": ["java", "-cp", "target/classes:target/perfproof-wl", "Bench"]
```

- Use the project's wrapper (`./mvnw`, `./gradlew`) in `setup` when it
  has one; do not install a build tool for it. Gradle is untested here:
  the same shape should work (`./gradlew classes`, then `java -cp
  build/classes/java/main:...`), but check it before relying on it.
- The benchmark program is yours: put its source under
  `.perfproof/harness/` and compile it in `setup`, as above.
- `java` must resolve to a real JDK (`java -version` exits 0). On macOS
  `/usr/bin/java` is a stub: with no JDK it fails and the runtime is
  unknown, and with one it is recorded as the stub, not as the JDK
  behind it. Put the JDK's `bin` first on the PATH instead.
- Give both versions the same flags. `JAVA_TOOL_OPTIONS` is recorded by
  name only: never set it in the spec to change heap or GC for one
  version.
- With a `peak_memory` guardrail, put the same `-Xmx` in both commands:
  the one the project runs with, or a size the workload fits in.
  With no fixed `-Xmx` the peak can track how far the JVM grows its
  heap rather than what the code keeps, and a faster version that
  allocates at a higher rate can then show far more memory. If a memory
  breach came from runs with no fixed `-Xmx`, say so and compare both
  again with one before calling it a regression; that run's guardrail
  decides, and its timing replaces the earlier one, since a smaller
  heap changes how often the JVM collects.

## Warm-up

The JIT compiles while the program runs, so a short run measures
start-up and the interpreter. First decide what the user feels. A
command line tool or a test run is short-lived: measure the whole
command (`wall`) and do not warm up. A library, service or batch job is
long-lived: report the steady state from inside the process, as below.
Each execution is a fresh JVM that may compile differently, and a loop
that has stopped drifting can still land on a different level later;
perfproof's alternating rounds cover that random variation. They do
not cover one arm settling more slowly than the other, which is why the
loop below discards at least 200 iterations whatever they look like.

**If the project has JMH** and a benchmark covers the changed code, use
it. Compile this runner next to it (JMH's `jmh-core`, `jopt-simple` and
`commons-math3` jars on the class path) and run one benchmark and one
parameter set; the metric is `reported`, `lower_is_better` for average
time and `higher_is_better` for throughput:

```java
import java.util.Collection;
import org.openjdk.jmh.results.RunResult;
import org.openjdk.jmh.runner.Runner;
import org.openjdk.jmh.runner.options.*;

public final class PerfproofJmh {
    public static void main(String[] args) throws Exception {
        OptionsBuilder b = new OptionsBuilder();
        b.include(args[0]).forks(Integer.getInteger("jmh.forks", 1))
                .warmupIterations(Integer.getInteger("jmh.warmup", 5))
                .warmupTime(TimeValue.seconds(Long.getLong("jmh.time", 2)))
                .measurementIterations(Integer.getInteger("jmh.measure", 5))
                .measurementTime(TimeValue.seconds(Long.getLong("jmh.time", 2)));
        for (int i = 1; i < args.length; i++) {
            int eq = args[i].indexOf('=');
            if (eq < 0) {
                System.err.println("expected key=value, got " + args[i]);
                System.exit(2);
            }
            b.param(args[i].substring(0, eq), args[i].substring(eq + 1));
        }
        Collection<RunResult> r = new Runner(b.build()).run();
        if (r.size() != 1) {
            System.err.println("expected one result, got " + r.size());
            System.exit(3);
        }
        System.out.println("PERFPROOF {\"value\": "
                + r.iterator().next().getPrimaryResult().getScore() + "}");
    }
}
```

JMH forks its own JVM, so `java ... PerfproofJmh 'Regex.method' size=1000`
is the command the runtime check sees. Look at what the benchmark
really calls: a JMH benchmark that times a copy of the code, not the
project's method, cannot prove a change to the project.

JMH is slow, so fit it to the budget before you start. A round runs
both versions once and costs `2 x forks x (warm-up + measured
iterations) x seconds per iteration`; the first look is at 10 rounds,
and `max_seconds` is 1800 by default, so a round should cost well
under 180 s. The defaults above (one fork, 5 + 5 iterations of 2 s) are
40 s a round, but the warm-up of 10 s a fork may end before a level
change that a run of 20 s or more shows, so raise `-Djmh.warmup` where
the budget allows. Set `-Djmh.forks`, `-Djmh.warmup`, `-Djmh.measure` and
`-Djmh.time` to fit, or set `budget.max_seconds` higher and tell the user
why. Say the expected time first either way.

**Otherwise iterate in process.** Do not look at stability before 200
iterations; after that, stop warming up when the median of the last 10
iteration times is within 1% of the median of the 10 before, give up
at 600, then time 20 more and print their median per operation. If it
gives up it still prints the median of the last 20 and exits 0: a
non-zero exit would be a failed execution, which perfproof turns into a
REJECT on correctness. Print how it ended on stderr, one line per
execution. Keep the result live (a field the loop adds to, printed at
the end) or the JIT removes the work. These numbers are provisional
(chosen by reasoning and a model, not measured); each iteration should
be tens of milliseconds:

```java
long[] t = new long[600];
int n = 0;
boolean stable = false;
while (n < 600 && !stable) {
    long t0 = System.nanoTime();
    iteration();                      // one batch of the real work, tens of ms
    t[n++] = System.nanoTime() - t0;
    if (n >= 200) {                   // last 10 against the 10 before, within 1%
        double a = median(t, n - 10, n), b = median(t, n - 20, n - 10); // median of a slice
        stable = Math.abs(a - b) < 0.01 * a;
    }
}
long[] m = new long[20];
if (stable) {
    for (int i = 0; i < 20; i++) {
        long t0 = System.nanoTime();
        iteration();
        m[i] = System.nanoTime() - t0;
    }
    System.err.println("stabilised after " + n + " iterations");
} else {
    System.arraycopy(t, n - 20, m, 0, 20);
    System.err.println("did not stabilise after " + n + " iterations; reporting the last 20");
}
java.util.Arrays.sort(m);
System.out.println(sink);             // the live result, also the oracle's output
System.out.println("PERFPROOF {\"value\": " + (double) m[10] / opsPerIteration + "}");
```

Print the value with all its digits.

Set the spec's `timeout_seconds` above the cost of an execution that
hits the cap: about `2 x (600 x seconds per iteration + 10)`. A timeout
is a failed execution, which perfproof turns into a REJECT on
correctness for the candidate, and the cap exists to avoid that. The
loop costs at least 220 iterations, so tens of milliseconds an iteration
is seconds an execution: say the expected time first.

After a comparison, count the executions that did not settle: each
execution's stderr is kept under `run_dir`, in
`logs/rounds/*.baseline.stderr` and `logs/rounds/*.candidate.stderr`
(under `logs/resume-N/rounds/` for a resumed run), and a grep for the
lines that start with `did not stabilise` is the check. Do not compare
the two versions' iteration counts: with the 200 iteration floor they
are all about the same and say nothing. In a model, and
likely in practice (it is not JVM-measured), a capped execution means
runaway or very noisy iterations (a deoptimising method, background
work), not slow settling, and a higher floor makes it more likely, so
do not rerun with one. Look at the JIT deoptimisation row and at what
else the machine was doing. Say in the report that those values are
medians of warm-up iterations and the comparison includes warm-up; the
verdict perfproof gave stands. The floor is what protects the
comparison from one version settling more slowly than the other, and
a version that settles much more slowly can still bias it.

## Profile

`perfproof profile` runs any command that writes folded stacks. A JDK
profiler writes its own format, so `perfproof.profilers.java` runs the
benchmark under Java Flight Recorder (part of the JDK, no install) or async-profiler and writes folded stacks with class, method,
file and line:

```
perfproof profile <name> --json --profiler-command '["<python>", "-m",
  "perfproof.profilers.java", "record", "--output", "{output}",
  "--project-root", "<repo root>", "--", "{command}"]'
```

`<python>` is the Python that perfproof itself runs in: the `python`
beside the `perfproof` executable (`dirname $(command -v perfproof)`),
which has `perfproof.profilers.java` installed with it.
Add `"--source-root", "<dir>"` for a project whose Java sources are not
under the repository root, and `"--jfr-setting", "<event>#<key>=<value>"`
to record extra events (below). It has been run on JDK 21.

What to know when reading the result:

- `function` is `package.Class.method`, `file` and `line` point into the
  repository for the project's classes. A class that is in the JDK or a
  jar is shown by its package path (`java/util/HashMap.java`) with
  `is_library` true. `frame_kind` says `native` for every Java frame;
  ignore it.
- The share is a share of samples of threads running Java code. The
  wrapper asks for one every 2 ms per thread and the JVM may deliver
  far fewer, so look at how many samples there are before trusting a
  small share; run the workload longer if there are few. Time in the garbage collector, in native code and while
  blocked is not in it: if CPU time is well below wall time, look at
  locks and I/O, not at the loops.
- Flight Recorder does not see time the VM spends in its own native
  code. A program that throws very many exceptions gave almost no
  execution samples under it, because the cost is in the VM building
  the stack trace; async-profiler on the same program showed
  `fill_in_stack_trace` plainly. For exceptions, native code and
  garbage collection use async-profiler.
- The wrapper turns on `-XX:+DebugNonSafepoints`. Without it a compiled
  loop's samples can all land on one line.
- The wrapper sets `JAVA_TOOL_OPTIONS`, which a JVM started by a build
  tool (`mvn exec:java`, `gradle run`) also reads, and every JVM writes
  its own recording, all of which are merged, build tool included.
  That has not been run through perfproof here: profile with `java`
  directly, which is also the command that will be timed.

**async-profiler** (also CPU in native and VM code, and the JIT
compiler's own threads): its library is in the `tools.profiler:async-profiler`
jar on Maven Central, which holds `linux-x64`, `linux-arm64` and `macos`
builds. Fetch it with the project's wrapper
(`./mvnw dependency:get -Dartifact=tools.profiler:async-profiler:4.3
-Dtransitive=false`), unzip the jar from `~/.m2`, and add
`"--async-lib", "<path>/libasyncProfiler.so"` to the command above.
`--async-event` defaults to `cpu` (a CPU timer where the kernel will not
allow `perf_events`, as in a container). Native frames come out as
``libjvm.so`symbol``.

Raw recordings stay next to the folded file in the run directory:
`jfr print --events <event> --stack-depth 8 <file>.jfr` answers the cheap
tests below.

## What usually goes wrong, and the cheap test for each

Run the test on the profile's top frames before writing a change; each
is a count or a share, never a timing to report.

| Suspect | What the profile shows | Cheap test |
|---|---|---|
| Allocation and GC pressure | many `new` in the top frames, `Arrays.copyOf`; wall above the Java-thread samples | `jfr print --events jdk.GarbageCollection <f>.jfr` (`sumOfPauses`) and the bytes by class from `jdk.ObjectAllocationSample` (its `weight` and `objectClass`): a class the loop makes per item that it did not need to |
| Boxing and unboxing | `Integer.valueOf`, `Long.valueOf`, `Double.valueOf` frames; `HashMap.merge` or `List<Integer>` in the loop | `java.lang.Integer`, `Long` or `Double` among the top allocated classes; the same loop on primitives (a `long[]`, an `int`-keyed array) allocates nothing |
| String building | `StringConcatHelper`, `String.format`, `Formatter`, `String.valueOf` in a loop; `s += x` on a growing string | allocation by class: `byte[]` and `String`; count of `String.format` calls per item; the same text built once, outside the loop, allocates nothing |
| Collection choice and resizing | `HashMap.resize`, `ArrayList.grow`, `Arrays.copyOf`; `LinkedList`, `contains` on a `List` | the frame's share in `total_fraction`; construct with the known capacity and see whether the frame vanishes; `contains` on a list grows with n (time at n and 2n, as a count of comparisons) |
| Streams in a hot path | `java.util.stream.*`, `ReferencePipeline`, lambda frames wrapping a tiny body | allocation by class (stream and lambda objects per call); the same body as a plain loop gives the same output and no allocation |
| Reflection | `Method.invoke`, `Class.forName`, `getDeclaredMethods`, `getMethod` in the top frames | the count of `getMethod` or `forName` calls against distinct names (repeated identical work): lookups can be done once and kept |
| Exceptions as control flow | in async-profiler, `Throwable.fillInStackTrace` and `fill_in_stack_trace`; in Flight Recorder almost nothing | add `--jfr-setting jdk.JavaExceptionThrow#enabled=true` for one short run and count events by `thrownClass` in the raw recording: thousands per second means control flow (it slows the run, so use the count, not the time) |
| Lock contention | CPU time well below wall time; threads parked | `--jfr-setting jdk.JavaMonitorEnter#threshold=0ms` in a short run, count events by `monitorClass` (the default threshold is 10 ms and shows nothing for short critical sections, where a threshold of 0 showed a great many events for a few threads on one lock); one thread against N threads on the same work |
| JIT deoptimisation | a method compiled, then slow again; iterations that never settle (the program says `did not stabilise`) | `jfr print --events jdk.Deoptimization` after the warm-up: each event names the method, line and `reason` (for example `unstable_if`); a growing count in the steady state is the sign; or `-Xlog:deoptimization=debug` |

Two checks apply to every item before it counts: the change must keep
the benchmark's output the same, and a guess about the JIT ("this
inlines", "escape analysis removes it") is only believed with the
`jfr` or `-XX:+PrintCompilation` output that shows it, since it differs
per JDK.

## Coverage

JaCoCo is the Java tool. It is not wired into the coverage check yet,
so the report says changed lines were not checked; do not claim they
ran.

## When to stop

As in [discovery.md](discovery.md): when the top frames are the JDK's
own (`HashMap.get`, `String.hashCode`) and the project's share is small,
the cap says what is left. A benchmark whose rounds disagree widely is
noisy, not slow; say so and report the range.
