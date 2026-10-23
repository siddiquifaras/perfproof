#!/bin/sh
# Measure the Ruby warm-up curve: PROCESSES fresh processes, each timing
# ITERATIONS iterations of the fixture workload, with YJIT on and off,
# inside the official ruby image (one container per process, so no
# process sees another's JIT state).
#
# Usage: studies/ruby-warmup/run.sh [out-dir]
# Output: <out-dir>/yjit-on-<n>.txt and yjit-off-<n>.txt, then
# `python studies/warmup_analyze.py <out-dir> yjit-on yjit-off`.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
out=${1:-$here/runs}
processes=${PROCESSES:-8}
iterations=${ITERATIONS:-1500}
mkdir -p "$out"
out=$(cd "$out" && pwd)
n=1
while [ "$n" -le "$processes" ]; do
  for mode in on off; do
    flag=--disable-yjit
    [ "$mode" = on ] && flag=--yjit
    docker run --rm --cpus 1 -v "$root:/w:ro" -w /w ruby:4.0 \
      ruby "$flag" studies/ruby-warmup/curve.rb "$iterations" \
      > "$out/yjit-$mode-$n.txt"
  done
  n=$((n + 1))
done
