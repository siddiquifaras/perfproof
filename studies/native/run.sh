#!/bin/sh
# Capture the native warm-up runs quoted in docs/method.md "Warm-up
# (C and C++)".
#
# Usage: studies/native/run.sh <lz4-tree> <out-dir>
#
# <lz4-tree> is lz4/lz4 at the task commit of the manifest
# eval/tasks/known-change/lz4-lz4-pr1040.json (the tree `check.py`
# extracts under eval/mine/work/lz4-lz4-pr1040/task). The program is
# built once with the system cc, then run in 12 fresh processes, each
# printing 60 blocks of 300 decompressions. Runs on this machine, not in
# a container, one process at a time.
set -eu
tree=${1:?usage: run.sh <lz4-tree> <out-dir>}
out=${2:?usage: run.sh <lz4-tree> <out-dir>}
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$out"
out=$(cd "$out" && pwd)
tree=$(cd "$tree" && pwd)

{
  cc --version | head -1
  uname -srm
  sysctl -n machdep.cpu.brand_string 2> /dev/null || true
  sysctl -n hw.ncpu 2> /dev/null || true
  uptime
} > "$out/environment.txt"
cc -O2 -I"$tree/lib" -o "$out/blocks" "$here/blocks.c" "$tree/lib/lz4.c"
cd "$tree"
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
  echo "process $i"
  "$out/blocks" lib/lz4hc.c lib/lz4frame.c programs/lz4io.c tests/fuzzer.c tests/frametest.c \
    2> /dev/null
done > "$out/blocks.txt"
rm -f "$out/blocks"
