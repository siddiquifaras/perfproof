#!/bin/bash
# Loaded Mac passes: rolling and node with a controlled background load,
# run from the worktree root after record_all.sh has finished:
#   bash tests/calibration/traces/record_loaded.sh
# Load: 4 steady spinning processes plus 1 bursty one (20-60 s on/off)
# at nice 0, on a 10-core M4 (4 performance, 6 efficiency cores).
# Keeps scratch/TRACE_COLLECTING present until the end, and stops every
# load process it started on any exit.
set -u
root=$(pwd)
py=$root/.venv/bin/python
traces=$root/tests/calibration/traces
tr=$root/scratch/traces
data=$tr/data
node=${NODE:-$(command -v node)}
marker=${TRACE_MARKER:-$root/scratch/TRACE_COLLECTING}
burner_pid=""
cleanup() {
  [ -n "$burner_pid" ] && kill "$burner_pid" 2>/dev/null
  rm -f "$marker"
}
trap cleanup EXIT
touch "$marker"
while pgrep -f "traces/record_all.sh" >/dev/null; do
  touch "$marker"
  sleep 10
done

load_note="loaded: 4 steady + 1 bursty (20-60 s on/off) spinning python processes at nice 0 on 10 cores"
tools=(--tool "python=$py --version" --tool "numpy=$py -c 'import numpy;print(numpy.__version__)'")

pass() { # name dir cmd seed
  touch "$marker"
  "$py" "$traces/wait_idle.py" >"$data/mac-loaded-$1.idle.json"
  "$py" "$traces/burner.py" 4 1 "$4" >"$data/mac-loaded-$1.burner.log" 2>&1 &
  burner_pid=$!
  sleep 5
  "$py" "$traces/collect.py" --out "$data/mac-loaded-$1" \
    --dir-a "$tr/$2-A" --dir-b "$tr/$2-B" --cmd "$3" \
    --rounds 1000 --seed "$4" --label "mac-loaded-$1" --target "$1" \
    --note "M4 MacBook Pro, macOS, native. $load_note" --no-wait \
    "${tools[@]}"
  kill "$burner_pid" 2>/dev/null
  wait "$burner_pid" 2>/dev/null
  burner_pid=""
}

echo "$(date -u +%FT%TZ) start loaded rolling"
pass rolling rolling "$py bench.py --output output/sums.npy" 20261121
echo "$(date -u +%FT%TZ) done loaded rolling"
echo "$(date -u +%FT%TZ) start loaded node"
pass node node "$node bench.js" 20261122
echo "$(date -u +%FT%TZ) done loaded node"
