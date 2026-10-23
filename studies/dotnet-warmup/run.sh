#!/bin/sh
# Measure the .NET warm-up curve: PROCESSES fresh processes, each timing
# ITERATIONS iterations of the fixture workload, with the default tiered
# compilation and with DOTNET_TieredCompilation=0, inside the official
# dotnet SDK image (one container per process, so no process sees
# another's JIT state; the iterations time only the workload, so the
# build and the runtime's start-up are not in them).
#
# Usage: studies/dotnet-warmup/run.sh [out-dir]
# Output: <out-dir>/tiered-on-<n>.txt and tiered-off-<n>.txt, then
# `python studies/warmup_analyze.py <out-dir> tiered-on tiered-off`.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
out=${1:-$here/runs}
processes=${PROCESSES:-8}
iterations=${ITERATIONS:-1500}
image=mcr.microsoft.com/dotnet/sdk:10.0
mkdir -p "$out" "$root/scratch/dotnet-warmup-build"
out=$(cd "$out" && pwd)
build=$root/scratch/dotnet-warmup-build
rm -rf "$build"
mkdir -p "$build"

# Build a copy, so the source tree gets no bin/ or obj/.
docker run --rm -v "$root:/w:ro" -v "$build:/build" "$image" sh -c '
  set -eu
  mkdir -p /r/studies/dotnet-warmup /r/tests/fixtures/dotnet/prog
  cp -r /w/studies/dotnet-warmup/Curve /r/studies/dotnet-warmup/
  cp -r /w/tests/fixtures/dotnet/prog/App /r/tests/fixtures/dotnet/prog/
  cd /r/studies/dotnet-warmup/Curve
  dotnet build -c Release -o /build -p:UseAppHost=false
' > "$out/build.txt" 2>&1

n=1
while [ "$n" -le "$processes" ]; do
  for mode in on off; do
    flag=1
    [ "$mode" = off ] && flag=0
    docker run --rm --cpus 1 -e "DOTNET_TieredCompilation=$flag" \
      -v "$build:/build:ro" "$image" dotnet /build/Curve.dll "$iterations" \
      > "$out/tiered-$mode-$n.txt" 2> /dev/null
  done
  n=$((n + 1))
done
