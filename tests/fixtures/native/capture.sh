#!/bin/sh
# Capture the native fixtures in this directory from real runs of the
# real tools: Apple clang, sample, llvm-cov on this Mac, and gcc 14,
# perf, gcov, clang and llvm-cov in the official gcc image.
#
# Usage: tests/fixtures/native/capture.sh <out-dir>
#
# Output is the raw output of each tool; nothing is edited. The fixture
# program (tests/fixtures/native/prog) is built with -O1 -g and frame
# pointers.
set -eu
out=${1:?usage: capture.sh <out-dir>}
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$out"
out=$(cd "$out" && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/perfproof-native.XXXXXX")
trap 'rm -rf "$work"' EXIT

# --- macOS: Apple clang, sample, llvm-cov
{
  sw_vers
  clang --version
  xcrun llvm-cov --version
  uname -m
} > "$out/mac-versions.txt"
cp -r "$here/prog" "$work/prog"
(
  cd "$work"
  clang -O1 -g -fno-omit-frame-pointer -Iprog prog/hot.c prog/work.c -o hot
  cp hot "$out/hot-macho"
  ./hot 1 > /dev/null &
  pid=$!
  sample "$pid" 5 1 -file "$out/sample.txt" > /dev/null 2>&1
  wait "$pid"

  clang -O0 -g -fprofile-instr-generate -fcoverage-mapping -Iprog \
    prog/check.c prog/work.c -o check-llvm
  LLVM_PROFILE_FILE="$work/check.profraw" ./check-llvm > /dev/null
  xcrun llvm-profdata merge -o check.profdata check.profraw
  xcrun llvm-cov export -format=lcov -instr-profile=check.profdata \
    check-llvm > "$out/llvm-mac.lcov"
  xcrun llvm-cov report -instr-profile=check.profdata check-llvm \
    > "$out/llvm-mac-report.txt"
  xcrun llvm-cov show -instr-profile=check.profdata check-llvm \
    > "$out/llvm-mac-show.txt"
)

# --- Linux: gcc, perf, gcov, clang, llvm-cov in the gcc image
cp "$here/prog/"* "$work/" 2>/dev/null || true
inner='
set -eu
apt-get update -qq > /dev/null
apt-get install -y -qq linux-perf clang llvm > /dev/null 2>&1
mkdir -p /build && cp -r /prog /build/prog && cd /build
{
  gcc --version | head -1
  clang --version | head -1
  perf version
  gcov --version | head -1
  llvm-cov --version | head -3
  uname -m
} > /out/linux-versions.txt

gcc -O1 -g -fno-omit-frame-pointer -Iprog prog/hot.c prog/work.c -o hot-gcc
clang -O1 -g -fno-omit-frame-pointer -Iprog prog/hot.c prog/work.c \
  -o hot-clang
cp hot-gcc /out/hot-gcc-elf
cp hot-clang /out/hot-clang-elf
perf record -F 999 -g -o perf.data ./hot-gcc 1 > /dev/null 2> /out/perf-record.txt
perf script -i perf.data -F ip,sym,dso,srcline > /out/perf-script.txt 2> /dev/null

gcc -O0 --coverage -Iprog prog/check.c prog/work.c -o check-gcov
./check-gcov > /dev/null
gcov --json-format --stdout check-gcov-check.gcda check-gcov-work.gcda \
  > /out/gcov.json
gcov check-gcov-check.gcda check-gcov-work.gcda > /out/gcov-text.txt
cp work.c.gcov /out/work.c.gcov

clang -O0 -g -fprofile-instr-generate -fcoverage-mapping -Iprog \
  prog/check.c prog/work.c -o check-llvm
LLVM_PROFILE_FILE=/build/check.profraw ./check-llvm > /dev/null
llvm-profdata merge -o check.profdata check.profraw
llvm-cov export -format=lcov -instr-profile=check.profdata check-llvm \
  > /out/llvm-linux.lcov
'
docker run --rm --cap-add SYS_ADMIN --security-opt seccomp=unconfined \
  -v "$here/prog:/prog:ro" -v "$out:/out" gcc:14 sh -c "$inner"
