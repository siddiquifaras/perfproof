# Rust

Read this when the code to speed up is Rust, built with cargo. The
method is the same as everywhere else; what differs is how to launch
the benchmark so the toolchain can be identified, what first runs cost
(there is no JIT), how to profile compiled code, and where Rust code
usually loses time.

## Launch the benchmark so the toolchain can be identified

`compare` only keeps a change when it knows what runs each version. It
reads a built binary: the compiler that made it is in the file. It does
not follow `cargo run`, `cargo bench`, `rustc` or `rustup`: rustup
chooses the toolchain (a `rust-toolchain.toml` can change it), and
`cargo run` builds before it starts the program, which would put the
build inside the timed run. Those give `runtime` `not_available` and a
KEEP is withheld. Build in `setup`, which is not timed and not checked,
and run the binary itself:

```json
"setup": {"commands": [["cargo", "build", "--release", "--locked"]],
          "timeout_seconds": 1800},
"command": ["target/release/<name>", "<arguments>"]
```

- Always `--release`, and `--locked` so the dependencies are the ones
  in `Cargo.lock`. A `target/debug` binary is unoptimised code, and
  rarely what the user runs: say so if the user's own command is one.
- Give both versions the same build: the same `cargo` command, features
  and `RUSTFLAGS`. A change to the `[profile]` in `Cargo.toml`
  (`lto`, `codegen-units`, `opt-level`, `panic`) is part of the change
  under test, and perfproof does not compare it between the two trees;
  read it in the diff, and say that a change of it is a change of the
  build and not of the code.
- A library crate has no binary. Write a small harness crate under
  `.perfproof/harness/bench/` with the crate as a path dependency and an
  empty `[workspace]` table (so cargo does not take it for a member of
  the project's workspace), run `cargo generate-lockfile --manifest-path
  .perfproof/harness/bench/Cargo.toml` once before locking (a lockfile
  written later would change the locked directory), and build it in
  `setup` into a directory of its own:

  ```
  ["cargo", "build", "--release", "--manifest-path",
   ".perfproof/harness/bench/Cargo.toml", "--target-dir",
   "target/perfproof-bench"]
  ```

  The command is then `target/perfproof-bench/release/bench`. Pass the
  inputs and results through `std::hint::black_box`, or the compiler
  may fold a constant loop away.
- Native code and the project's own tests are run the usual way: the
  `correctness` command may be `cargo test --release --locked`.

## Warm-up

Compiled Rust has no JIT, so there is no in-process warm-up to wait
for and no stop rule to write. Two small effects remain; the numbers
are in "Warm-up (Rust)" of `docs/method.md`, from one busy machine:

- The first execution of a freshly built binary was several times
  slower than later ones in one macOS series. Set the spec's `warmup`
  to at least 1, as a precaution.
- The first iterations inside one process were slower than most later
  ones in one busy-machine series, which did not separate a warm-up from
  noise. If the benchmark loops in process and prints one number
  (`reported`), discard its first few iterations (5 is a guess) and
  loop a fixed number of times. Never loop until the time settles: a
  loop that can fail to settle must not fail the execution.

A command line tool is measured whole (`wall`); a library function is
looped in process.

## Profile

`perfproof profile` runs any command that writes folded stacks. The
system profilers write their own formats, and Rust symbols arrive
mangled, so `perfproof.profilers.rust` runs the binary under `sample`
(built into macOS) or `perf` (Linux) and writes folded stacks with
demangled symbols:

```
perfproof profile <name> --json --own-module <binary name>
  --profiler-command '["<python>", "-m", "perfproof.profilers.rust",
  "record", "--output", "{output}", "--project-root", "<repo root>",
  "--", "{command}"]'
```

`<python>` is the Python that perfproof itself runs in: the `python`
beside the `perfproof` executable. `--own-module` is the binary's file
name: the profile then marks the crates that the repository defines
(the `Cargo.toml` files under the root name them) as project code, and
the standard library, the dependencies and C code linked in as library
code.

What to know when reading the result:

- A frame is ``<binary>`<crate>::<module>::<function>``. There is no
  file or line: a Rust path says where to look (`my_crate::parse::tokens`
  is in the crate `my_crate`, module `parse`, so `src/parse.rs`, or
  `src/parse/mod.rs`, or an inline `mod parse`). `grep` for the
  function.
- The command must be the built binary. A command that starts the
  binary through `cargo run` or a shell is profiled as cargo or the
  shell.
- On macOS `sample` records every thread, running or parked, from a few
  tens of milliseconds after the start; the wrapper drops threads parked
  in a blocking call and says how many samples that was on stderr. The
  share is of the samples kept. A run of well under a second gives few
  samples: run the workload longer. On Linux the CPU-clock event counts
  only running threads, and a stack needs frame pointers: build the
  profiled binary with `RUSTFLAGS="-C force-frame-pointers=yes"` into
  its own `--target-dir` (the timed binary stays as it was), or add
  `"--call-graph", "dwarf"` to the command (not run here; it needs debug
  information in the binary). In a container, libc frames
  can show as `[unknown]`, and frames in the kernel as `[kernel]`.
- The compiler inlines. A small function called in a loop has no frame
  of its own: its time is in the caller, and a hot caller whose body is
  one loop is the answer. The same holds for the standard library
  (`Vec::push`, `contains`), where the growth or the comparison shows
  only if it was not inlined.
- Neither profile shows time a thread spends waiting (macOS: the
  wrapper drops parked threads; Linux: the event counts running time
  only): if CPU time is well below wall time, look at locks and I/O,
  not at the loops.

## What usually goes wrong, and the cheap test for each

Run the test on the profile's top frames before writing a change. Each
is a count or a share, never a timing to report.

| Suspect | What the profile shows | Cheap test |
|---|---|---|
| Growing a `Vec` or `String` that has a known length | allocator frames (`realloc`, `_szone_realloc`) high; `RawVec` growth frames (`finish_grow`, `do_reserve_and_handle`) where not inlined | count allocations per call with the allocator below; `with_capacity(n)` or `reserve(n)` leaves one |
| Cloning, `to_vec`, `to_owned`, `collect` into a temporary | allocator frames (`malloc`, `free`, `__rdl_alloc`) at the top, and `<String as Clone>::clone`; the cloning function's own share small | the allocation count per call against the number of items: one per item means a copy per item; borrow (`&[T]`, `&str`, `Cow`) instead |
| Formatting in a loop (`format!`, `to_string`, `println!` to build text) | `core::fmt::` frames plus allocator frames | allocations per call; `write!` into one reused `String` allocates nothing per item |
| Iterator chains | nothing named: adapters inline into the caller. Suspects are a `collect` in the middle, `.chars().count()` or `.nth()` re-walking a string, `Vec::contains` or `iter().position` inside a loop, `remove(0)` and `insert(0, ..)` | run the same code at `n` and `2n` and compare how the sample count in the caller grows (twice is linear, four times is quadratic); count comparisons rather than timing |
| Bounds checks | the whole loop in one function, no frame to blame | disassemble the function (`llvm-objdump -d --disassemble-symbols=<symbol> <file>`, from `rustup component add llvm-tools-preview`, find the symbol with `llvm-nm`): a compare and branch to `core::panicking::panic_bounds_check` inside the loop is the check. Only tight numeric loops are worth it: iterate over the slice, use `zip` or `chunks`, or check the size once before the loop |
| Hashing with the default `HashMap` | `hashbrown` frames with high self share (SipHash is inlined into them); `SipHasher` frames where it is not | a copy of the loop with a trivial `BuildHasher` or a `Vec` indexed by the key bounds what hashing costs; a hasher from a crate is a new dependency, so ask first |
| Locks and shared counters | `Mutex::lock_contended`, `__psynch_mutexdrop`, futex frames; CPU time below wall time | the same work on one thread against `N` threads: one thread being as fast means the lock serialises it; batch updates per thread, shard, or use atomics |
| Unbuffered I/O | `write` or `read` system calls take most samples; `<File as Write>::write` or `core::fmt::write` under them | wrap the `File` in `BufWriter` or `BufReader`, or lock stdout once, and see the system-call frames fall; `strace -c` counts the calls on Linux |
| Async overhead | worker threads in `poll` frames with system calls or `park` under them; a `spawn` per tiny item | count `spawn` calls per unit of work; a blocking call (`std::fs`, `std::thread::sleep`, a long CPU loop) inside an `async fn` stalls its worker: move it to `spawn_blocking`; a lock held across `.await` serialises tasks. Not checked in a run for this playbook |
| Build settings | unoptimised frames everywhere (a debug build), or no inlining across crates | the profile directory in the command (`target/debug`); `lto`, `codegen-units = 1`, `panic = "abort"` and `opt-level` are `Cargo.toml` changes, each proved like any change, the whole build the same in both versions |

To count allocations, put this in the harness binary (never in the
project), call the code once, and print the difference:

```rust
use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicU64, Ordering};

struct Counting;
static ALLOCS: AtomicU64 = AtomicU64::new(0);

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        ALLOCS.fetch_add(1, Ordering::Relaxed);
        unsafe { System.alloc(layout) }
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        unsafe { System.dealloc(ptr, layout) }
    }
}

#[global_allocator]
static COUNTING: Counting = Counting;
// before: ALLOCS.load(Ordering::Relaxed); call the code; load again.
```

Two checks apply to every item before it counts: the change must keep
the benchmark's output the same, and a guess about the optimiser ("this
inlines", "the bounds check goes away") is only believed with the
disassembly that shows it, since it differs per compiler version.

## Coverage

`-C instrument-coverage` and the toolchain's `llvm-profdata` and
`llvm-cov` give an lcov report. Build a second, instrumented binary in
`setup`:

```
["env", "RUSTFLAGS=-C instrument-coverage", "cargo", "build", "--release",
 "--locked", "--target-dir", "target/perfproof-cov"]
```

(the build itself was run by hand with that `RUSTFLAGS`, not through
perfproof's `setup`),

then `python -m perfproof.bench.rust_coverage --output <file>.lcov
--project-root <repo root> -- target/perfproof-cov/release/<name>
<arguments>` runs it and writes the lines of the project's files that
ran (`DA:<line>,<count>`, count 0 for a line nothing ran). It needs
`rustup component add llvm-tools-preview`. The coverage check does not
read lcov yet, so the report says changed lines were not checked; do not
claim they ran. A line with count above 0 in the lcov is evidence you
can cite from it directly.

## When to stop

As in [discovery.md](discovery.md): when the top frames are the
standard library's or an allocator's own and the project's share is
small, the cap says what is left. A benchmark whose rounds disagree
widely is noisy, not slow; say so and report the range.
