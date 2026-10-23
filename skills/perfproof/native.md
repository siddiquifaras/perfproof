# C and C++

Read this when the code to speed up is C or C++. The method is the same
as everywhere else; what differs is how to build and launch the
benchmark so the toolchain can be identified, why there is no warm-up
to discard, how to profile with `sample` or `perf`, how to check what
ran, and what usually goes wrong in native code.

## Build the benchmark, then time the executable

`compare` only keeps a change when it knows what built and runs each
version. For a native program it can tell:

- **The executable.** An untracked executable in the tree is a build of
  the code under test. Its libraries from outside the tree are hashed.
  The toolchain that built it is read from the file: an ELF file names
  its compilers in `.comment` (`GCC: ...`, `clang version ...`); a
  Mach-O file names only the SDK and the linker (`ld 1230.1`), not the
  compiler. Two versions built by different toolchains fail `runtime`.
  A file with the section stripped names nothing, which is not a
  difference.
- **A compiler as the command.** A native `cc`, `gcc`, `g++`, `c++`,
  `clang` or `clang++` (versioned names too) is asked for `--version`
  and its target, and its file is hashed. A driver that is a script
  (ccache's wrapper, a distribution's alternatives script) is a shell,
  so `runtime` is `not_available`.
- **Not identified.** `make`, `cmake`, `ninja` and shell scripts start
  other programs: `runtime` is `not_available` and a KEEP is withheld.
  Do not put them in the timed command.

Build in `setup` (not timed, not checked) and time the executable:

```json
"setup": {"commands": [["make", "-C", "lib", "libx.a"],
                       ["cc", "-O2", "-g", "-fno-omit-frame-pointer",
                        "-Ilib", "-o", "bin/bench", "bench.c", "lib/libx.a"]],
          "timeout_seconds": 900},
"command": ["bin/bench", "input.dat"]
```

- Build both versions with the same command, compiler and flags. A flag
  that differs (`-O3` against `-O2`, `-march=native`) changes the code
  under test as much as the source change does.
- The benchmark must be the optimised build the project ships: no
  `-O0`, no sanitizers, no `-DDEBUG`, assertions as released. Time what
  a user runs.
- `-g` and `-fno-omit-frame-pointer` let the profiler name `file:line`
  and unwind. Use them for the timed build too, so the profile and the
  measurement are of the same executable (a frame pointer costs a
  register, so a version built without it is a different build).
- Fixed work, not fixed time. A benchmark mode that runs "for N
  seconds" (many `-b` options) makes wall time the same for both
  versions. Use a count of iterations, or a program that prints its own
  rate as a `reported` value.
- Make the program print a checksum of what it computed, so the oracle
  can compare outputs and the compiler cannot drop the work.

## Noise: there is no JIT, there is the machine

Native code is compiled ahead of time: no interpreter, no JIT, no
phase that runs different code from the next. What remains is the cold
start (page faults, instruction and data caches, branch predictors),
frequency scaling and other load; the numbers are in "Warm-up (C and
C++)" in `docs/method.md`.

- Make one execution at least half a second, so start-up is a small part
  of it, and keep the work identical between rounds.
- A laptop throttles and shares its cores. If `perfproof` reports the
  machine as loaded, the interval widens or the comparison is
  inconclusive; do not read a single run.
- A version that loads a different `libc` or `libstdc++` is a
  different experiment; the gate hashes the libraries a built
  executable loads from outside the tree.

## Profile

`perfproof profile` runs any command that writes folded stacks.
`perfproof.profilers.native` runs the program under `sample` (macOS)
or `perf` (Linux), reads the tool's text, and writes folded stacks with
function, file and line:

```
perfproof profile <name> --json --profiler-command '["<python>", "-m",
  "perfproof.profilers.native", "record", "--output", "{output}",
  "--root", "<repo root>", "--", "{command}"]'
```

`<python>` is the Python that perfproof itself runs in: the `python`
beside the `perfproof` executable. Give the built program as the
command (`bin/bench ...`), not `make` or a script: the profiler attaches
to the process it starts. Add `"--tool", "perf"` or `"sample"` to
choose, `"--frequency", "<hz>"` for perf.

- **Never find the program by name.** The recorder starts the program
  and samples it (on macOS by process id). `pgrep <name>`, `sample <name>` and `ps`
  look other processes up and can be refused where the sandbox hides
  them; a profile made that way is not perfproof's.
- **macOS: `sample`.** It needs no privileges for a process of your own.
  It attaches a moment after the program starts, so the first tens of
  milliseconds are not in the profile, and it records a thread whether
  it runs or waits: a blocked thread shows as a frame
  (`__psynch_cvwait`, `mach_msg2_trap`). It reads a program's debug
  information, so build with `-g`; the `.dSYM` that Apple's `cc -g`
  writes beside the program is what it read in the runs here.
- **Linux: `perf`.** It needs `perf_event_open`. In a container that is
  `--cap-add SYS_ADMIN --security-opt seccomp=unconfined`; without them
  perf says "Operation not permitted" and the wrapper reports it and
  writes nothing. Run the program from the container's own file system:
  perf could not name symbols of a binary on a bind-mounted volume.
  Install it in the image (`apt-get install linux-perf` on the
  Debian-based official `gcc` image). A frame-pointer unwind stops in code built without frame
  pointers, so library frames above your code may be missing or
  `[unknown]`.
- A frame is `function (path:line)` when the profiler knows the source
  line, with the path relative to `--root` when one file under it has
  that name; with two files of the same name the base name is kept as
  printed, so the location is not guessed.
  Otherwise it is ``image`function``. Pass `--own-module <binary>` to
  `perfproof profile` so the program's own frames without a line are
  not marked as library code.
- Read the library frames. A hot libc routine (a copy, an allocation,
  a string scan, a lock) is not the cost to cut: its callers are. Look one frame up in the stack for who calls it and how often,
  then decide whether the call is needed at all.
- An optimised build inlines. A line in the profile may belong to a
  function that was inlined into the one named; read the source around
  it. `-O0` profiles are not the profile of the optimised build.

## Check that the change was exercised

The coverage gate asks that every changed line ran under the project's
checks. `perfproof.bench.native_coverage` turns the tool's own
coverage into lcov, the format the gate reads. Build with coverage,
run the project's tests, then:

```
# gcc: build with --coverage (and -O0 for exact lines)
<python> -m perfproof.bench.native_coverage gcov \
    --root . --output "$PERFPROOF_COVERAGE_REPORT" build/

# clang: build with -fprofile-instr-generate -fcoverage-mapping, run the
# tests with LLVM_PROFILE_FILE=build/%p.profraw
<python> -m perfproof.bench.native_coverage llvm \
    --root . --output "$PERFPROOF_COVERAGE_REPORT" \
    --object build/tests build/
```

Only files under `--root` are written, with paths relative to it. A
changed line that no test ran is reported by the gate, not rejected:
offer a test that reaches it. Coverage builds change the code, so the
timed build is a separate one.

## What usually goes wrong

Cheap tests first: a profile, then a counter or a one-line experiment.
A change is a hypothesis until `compare` says otherwise.

| Symptom in the profile | Usually | Cheap test |
|---|---|---|
| `malloc`, `free`, `operator new` high, or callers allocating in a loop | allocation per item that could be hoisted or pooled | count calls with a counter in a wrapper around the allocator; hoist one allocation out of the loop and compare output |
| A copy routine of libc high | copying a large value that could be passed by reference, a buffer grown by repeated copying, struct returned by value | the callers' frames and the sizes at the call sites; replace one copy with a pointer and compare output |
| A loop on a few lines with high self time | the algorithm or the memory access pattern | count iterations against input size at two sizes; if time grows faster than the count, it is cache misses |
| High self time on a load in a loop over structs | array-of-structs where one field is read | `perf stat -e cache-misses` on a Linux host that exposes the counter; reorder the fields or split the array in a copy and time it |
| A branch on random data in a hot loop | misprediction | sort the input in a copy; if time falls a lot, branches are the cost |
| A loop that is not vectorised | aliasing, a function call or a conditional in the body, an index the compiler cannot bound | `-Rpass-missed=loop-vectorize` (clang) or `-fopt-info-vec-missed` (gcc) names the reason |
| `std::string` or `std::vector` constructors and destructors high | temporary objects per call, passing by value | read the hot function for by-value parameters and `+` on strings; use `const&` or `string_view` |
| Virtual calls or function pointers in the hot path | indirect call the compiler cannot inline | profile callers; devirtualise one call site behind a final class and compare |
| `printf`, `fwrite`, `std::cout`, `std::endl` high | unbuffered or flushing output | redirect output to `/dev/null` and time again; buffer, or drop `endl` |
| Locks or atomics high with many threads | contention or false sharing | the same run with one thread; pad the shared counter to a cache line in a copy |
| `strlen`, `strcmp`, `strchr` in the condition of a loop | an `O(n)` call that repeats | hoist it out of the loop |
| `pow`, `exp`, `sqrt`, division inside a loop | an invariant or a cheaper form | check the arguments for loop invariants |

Native-specific ways a change goes wrong:

- **Undefined behaviour.** A change that "works" because the compiler
  happened to do what you expected can give another result at another
  `-O` level or compiler version. Run the oracle on the candidate built
  with `-fsanitize=address,undefined` once, as an extra test: it is not
  the timed build.
- **Floating point.** `-ffast-math` and reordering a sum change the
  result. Compare outputs with the tolerance the project's own tests
  use, never with a tolerance picked to make a change pass.
- **Platform paths.** Code under `#ifdef` for one CPU or OS does not
  run on another. Say which platform the measurement is on; a change to
  an ARM path is not measured by an x86 machine, and the reverse.
- **A faster build, not a faster program.** A flag, a compiler upgrade
  or link-time optimisation is a change to the build, not the source:
  it is a valid candidate only if both versions are built the same way
  afterwards. The gate does not see compiler flags (they are not
  recorded in the executable), so keep the build commands identical
  between baseline and candidate yourself. The exception is a flag
  change that is the candidate's intended improvement: then say so in
  the report.

## When to stop

Stop when the profile is flat (no frame above a few percent of the
time), when the remaining cost is in a library you do not own, or when
the cap on the next idea is below what the interval can resolve at the
budget. A change that is faster only in a build the project does not
ship is not a result.
