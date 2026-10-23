# Rust profiler and coverage fixtures

Output of the tools, untouched. Nothing here was written or edited by
hand. Date: 2026-10-22. Toolchain: rustc 1.94.1 (e408947bf 2026-03-25),
cargo 1.94.1.

| File | Tool and command |
|---|---|
| `sample-fd.txt` | macOS 15.7.7 (24G720) on an M4 MacBook Pro, `/usr/bin/sample` (the report says `Report Version: 7`), as run by `python -m perfproof.profilers.rust record`: `sample <pid> 3660 1 -file <raw>` on `fd --color=always -HI -j1 . bigtree`; this is the raw report `<output>.sample.txt` of that run |
| `perf-script-fd.txt` | `perf script -F comm,tid,ip,sym,dso` of `perf record -q -e cpu-clock -F 1000 --call-graph fp` on the same program in the Linux box, searching four of the tree's subdirectories; perf 6.12.113, Linux 6.12.54-linuxkit aarch64 (Docker Desktop VM) |
| `perf-script-pathologies.txt` | the same `perf script` of `pathologies.rs` in its `clone` and `unbuffered` modes (0.12 s each), one after the other; it holds the raw legacy names perf did not decode (`_ZN...E.llvm.<digits>`), the v0 names of the allocator shim (`_RNvCs..._7___rustc11___rdl_alloc`) and `(deleted)` mappings |
| `llvm-cov-export.lcov` | `llvm-cov export --format=lcov --instr-profile=<merged> <binary>` (LLVM 21.1.8-rust-1.94.1-stable, `rustup component add llvm-tools-preview`) of `coverage-main.rs` built with `rustc -C instrument-coverage` and run once, in the Linux box |
| `coverage-main.rs` | the program behind `llvm-cov-export.lcov`: one function that runs and one that does not; source paths in the report are `/work/cov/main.rs` |

- The program profiled is fd 8.2.1 at commit
  `21fd01307313e7442670f6f2047371e465c53bba` of sharkdp/fd, built with
  `cargo build --release --locked` (macOS) and, in the Linux box, with
  `RUSTFLAGS="-C force-frame-pointers=yes"` and
  `CARGO_PROFILE_RELEASE_DEBUG=line-tables-only`. `bigtree` is 40
  directories of 5,000 empty files each (200,000 files), made by a
  throwaway script; the Linux box searched `dir00` to `dir03`.
- The Linux box is the `rust:1.94.1` image plus Debian's `linux-perf`
  (`eval/tasks/workloads/rust/Dockerfile`), run with
  `--cap-add SYS_ADMIN --cap-add PERFMON`. Its kernel hides kernel
  symbols (`kptr_restrict`), so kernel frames are addresses in perf's
  output.
- The `fd` binary in the Linux capture is a copy at `/tmp/fd`. perf
  could not read symbols for it while it sat on the host-mounted
  volume, so every frame was `[unknown]` there.

What the fixtures show, as the tools printed it:

- `sample` prints a call tree, one line per distinct frame and offset,
  with the count of samples under it; every thread is listed, parked
  ones included (`__ulock_wait` under the main thread, `read` under the
  signal-handler thread). Symbols are legacy-mangled with their hash and
  `$LT$`-style escapes undecoded. The report names its binary image
  `(in fd)`. It starts about 43 ms after the process launched (the
  header's `Launch Time` and `Date/Time`).
- `perf script` prints one block per sample, leaf first, and decodes
  most Rust names, but leaves `$u7b$$u7b$closure$u7d$$u7d$` in a name and
  prints a name that carries an LLVM suffix raw. Kernel frames are
  `[unknown]` at addresses with the top bit set. A frame in a mapping
  perf calls deleted has the dso `/ (deleted)`.

## `fd-threads.*`

Date 2026-10-23, macOS 15.7.7 (24G720), M4 MacBook Pro, rustc 1.94.1,
fd 8.2.1 built with `cargo build --release` (release profile of the
checkout, frame pointers as the build left them). `fd-threads.sample.txt`
is `/usr/bin/sample`'s raw report (`Report Version: 7`) and
`fd-threads.folded.txt` is what `perfproof profile` wrote from it with
`python -m perfproof.profilers.native record`, both untouched, for
`fd --color=always -HI -j1 . <tree of about 3,000 files>` with the
profiler attached after start. Project root for tests: any path
(frames carry no files); own module: `fd`. Worker threads start at
`thread_start;_pthread_start;fd`std::sys::thread...`, the main thread at
`dyld`start`.

```
dfe4b1b617ea3ca382e4219d85d8ba4ed8479b753b735792d3d0af11794be9ac  fd-threads.folded.txt
b91f737dc0a450b3015654a69e9b4af10e089326075721cc96364cd0e6322465  fd-threads.sample.txt
```
