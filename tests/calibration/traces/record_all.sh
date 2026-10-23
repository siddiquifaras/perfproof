#!/bin/bash
# Record every target, one at a time, from the worktree root:
#   bash tests/calibration/traces/record_all.sh [target ...]
# Targets: rolling membership picolm node lvm-rolling lvm-membership
# lvm-java. Raw output goes to scratch/traces/data (gitignored);
# compress.py moves the finished traces into tests/calibration/traces.
set -eu
root=$(pwd)
py=$root/.venv/bin/python
traces=$root/tests/calibration/traces
tr=$root/scratch/traces
data=$tr/data
node=${NODE:-$(command -v node)}
mkdir -p "$data"
mac_tools=(--tool "python=$py --version" --tool "numpy=$py -c 'import numpy;print(numpy.__version__)'"
  --tool "uv=uv --version")

mac() { # name dir cmd seed [extra args]
  name=$1 dir=$2 cmd=$3 seed=$4
  shift 4
  "$py" "$traces/collect.py" --out "$data/mac-$name" \
    --dir-a "$tr/$dir-A" --dir-b "$tr/$dir-B" --cmd "$cmd" \
    --rounds 1000 --seed "$seed" --label "mac-$name" --target "$name" \
    --note "M4 MacBook Pro, macOS, native" "${mac_tools[@]}" "$@"
}

lvm() { # target seed
  "$py" "$traces/wait_idle.py" >"$data/linuxvm-$1.idle.json"
  (while true; do
    echo "$(date -u +%FT%TZ),$(sysctl -n vm.loadavg)"
    sleep 10
  done) >"$data/hostload-linuxvm-$1.csv" &
  sampler=$!
  docker run --rm --platform linux/arm64 -v "$root":/work \
    perfproof-traces sh /work/tests/calibration/traces/container_target.sh "$1" "$2" || rc=$?
  kill "$sampler" 2>/dev/null || true
  return "${rc:-0}"
}

targets=("$@")
[ ${#targets[@]} -eq 0 ] && targets=(rolling membership picolm node lvm-rolling lvm-membership lvm-java)
for t in "${targets[@]}"; do
  echo "$(date -u +%FT%TZ) start $t"
  case $t in
  rolling) mac rolling rolling "$py bench.py --output output/sums.npy" 20261101 ;;
  membership) mac membership membership "$py bench.py --output output/held.txt" 20261102 ;;
  picolm) mac picolm picolm "picolm/picolm models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf -p 'The capital of France is' -n 16 -t 0 -j 4" 20261103 \
    --tool "cc=gcc --version" ;;
  node)
    mkdir -p "$tr/node-A" "$tr/node-B"
    cp "$traces/bench.js" "$tr/node-A/bench.js"
    cp "$traces/bench.js" "$tr/node-B/bench.js"
    mac node node "$node bench.js" 20261104 --tool "node=$node --version" ;;
  lvm-rolling) lvm rolling 20261105 ;;
  lvm-membership) lvm membership 20261106 ;;
  lvm-java) lvm java 20261107 ;;
  esac
  echo "$(date -u +%FT%TZ) done $t"
done
