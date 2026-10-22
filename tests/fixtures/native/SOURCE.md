# Native fixtures

Captured by `capture.sh` from real runs of the tools below, on 2026-10-23.
Nothing is edited. The program is `prog/` in this directory, built with
`-O1 -g -fno-omit-frame-pointer` (the coverage builds with `-O0`).

## macOS (`mac-versions.txt`)

macOS 15.7.7 (24G720), arm64, Apple clang 17.0.0 (clang-1700.6.3.2) from the
Command Line Tools, `llvm-cov` and `llvm-profdata` of Apple LLVM 17.0.0, and
the system `sample`.

| File | Command |
|---|---|
| `hot-macho` | `clang -O1 -g -fno-omit-frame-pointer -Iprog prog/hot.c prog/work.c -o hot` |
| `sample.txt` | `sample <pid of ./hot 1> 5 1 -file sample.txt` (one millisecond between samples) |
| `llvm-mac.lcov` | `clang -O0 -g -fprofile-instr-generate -fcoverage-mapping ... check.c work.c`, run `check` with `LLVM_PROFILE_FILE`, `llvm-profdata merge`, `llvm-cov export -format=lcov` |
| `llvm-mac-report.txt`, `llvm-mac-show.txt` | `llvm-cov report` and `llvm-cov show` of the same profile: the independent reading the tests compare the lcov against |

`llvm-mac.lcov` and `llvm-mac-show.txt` name the temporary directory the
capture built in (`/var/folders/.../perfproof-native.XXXXXX/prog/`).

## Linux (`linux-versions.txt`)

The official `gcc:14` image (Debian-based, aarch64) on Docker Desktop 29.1.3,
with `linux-perf`, `clang` and `llvm` from apt added: gcc 14.4.0, Debian clang
19.1.7, perf 6.12.113, gcov 14.4.0, Debian LLVM 19.1.7. Run with
`--cap-add SYS_ADMIN --security-opt seccomp=unconfined`: with the default
options `perf record` fails with "Operation not permitted". The program is
copied to the container's own file system first: perf could not name
the symbols of a binary on a bind-mounted volume.

| File | Command |
|---|---|
| `hot-gcc-elf`, `hot-clang-elf` | `gcc` and `clang` with the macOS flags above |
| `perf-script.txt`, `perf-record.txt` | `perf record -F 999 -g ./hot-gcc 1`, then `perf script -F ip,sym,dso,srcline`; `perf-record.txt` is perf's own stderr, with its sample count (1001) |
| `gcov.json`, `gcov-text.txt`, `work.c.gcov` | `gcc -O0 --coverage`, run `check-gcov`, `gcov --json-format --stdout` on the two `.gcda` files; `gcov` (text) on the same, whose `work.c.gcov` is the independent reading |
| `llvm-linux.lcov` | the llvm-cov pipeline above, with Debian's clang and llvm 19.1.7 |

`perf-script.txt` has 1001 samples of `hot-gcc`; its frames in libc are
`[unknown]` where perf could not name a function.
