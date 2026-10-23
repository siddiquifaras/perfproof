#!/bin/bash
# Loaded passes on the Hetzner CCX13 box: rolling and node, each with a
# steady and a bursty background load from stress-ng. Run there from the
# same work directory as record_linux.sh, after it has finished:
#   bash record_linux_loaded.sh
# The load runs on the box's other SMT thread as often as not: both
# vCPUs are siblings of one physical core, so it competes with the
# benchmark for the core even when the scheduler separates them.
set -u
w=$(pwd)
while pgrep -f "record_linux.sh" >/dev/null; do sleep 10; done
apt-get install -y -q stress-ng >/dev/null 2>&1 || {
  echo "stress-ng install failed"
  exit 1
}
py=$w/venv/bin/python
data=$w/data
base="real Linux, Hetzner CCX13 KVM, 1 physical core / 2 SMT threads (2 dedicated vCPUs of AMD EPYC-Milan)"
tools=(--tool "python=$py --version"
  --tool "numpy=$py -c 'import numpy;print(numpy.__version__)'"
  --tool "perf=perf --version" --tool "node=node --version"
  --tool "stress-ng=stress-ng --version" --tool "kernel=uname -r")
load_pid=""
cleanup() {
  [ -n "$load_pid" ] && kill "$load_pid" 2>/dev/null
  pkill -f stress-ng 2>/dev/null
  true
}
trap cleanup EXIT

steady() { exec stress-ng --cpu 1 --cpu-load 50 --timeout 20000s; }
bursty() { # seed
  RANDOM=$1
  while true; do
    on=$((20 + RANDOM % 41))
    stress-ng --cpu 1 --cpu-load 100 --timeout "${on}s" >/dev/null 2>&1
    sleep $((20 + RANDOM % 41))
  done
}

pass() { # kind name src cmd seed
  kind=$1 name=$2 src=$3 cmd=$4 seed=$5
  for arm in A B; do
    rm -rf "$w/trees/$name-$arm"
    cp -R "$w/src/$src" "$w/trees/$name-$arm"
  done
  if [ "$kind" = steady ]; then
    desc="loaded: stress-ng --cpu 1 --cpu-load 50 (one vCPU at 50%, steady)"
    steady >/dev/null 2>&1 &
  else
    desc="loaded: bursty stress-ng --cpu 1 --cpu-load 100, 20-60 s on / 20-60 s off, seed $seed"
    bursty "$seed" &
  fi
  load_pid=$!
  sleep 5
  "$py" "$w/collect.py" --out "$data/linux-$name" \
    --dir-a "$w/trees/$name-A" --dir-b "$w/trees/$name-B" --cmd "$cmd" \
    --rounds 1000 --seed "$seed" --label "linux-$name" --target "$src" \
    --note "$base. $desc" --linux --no-wait "${tools[@]}"
  kill "$load_pid" 2>/dev/null
  cleanup
  load_pid=""
}

mkdir -p "$w/src/node" && cp "$w/bench.js" "$w/src/node/"
n=0
for item in "steady rolling" "steady node" "bursty rolling" "bursty node"; do
  set -- $item
  kind=$1 src=$2
  n=$((n + 1))
  echo "$(date -u +%FT%TZ) start loaded $kind $src"
  case $src in
  rolling) cmd="$py bench.py --output output/sums.npy" ;;
  node) cmd="node bench.js" ;;
  esac
  pass "$kind" "loaded-$kind-$src" "$src" "$cmd" $((20261130 + n))
  echo "$(date -u +%FT%TZ) done loaded $kind $src"
done
echo "$(date -u +%FT%TZ) all loaded passes done; stress-ng left: $(pgrep -c stress-ng)"
