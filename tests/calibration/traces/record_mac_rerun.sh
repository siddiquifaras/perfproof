#!/bin/bash
# Re-run the first Mac traces under the strict quiet rule, after
# record_mac_ext.sh has finished, from the worktree root:
#   bash tests/calibration/traces/record_mac_rerun.sh [target ...]
# Targets: rolling membership picolm node lvm-rolling lvm-membership
# lvm-java loaded-rolling loaded-node (4 steady + 1 bursty burners). Each pass waits for 10 quiet minutes (load below 3, none of
# the other workers' jobs), is aborted and restarted if the load stays
# above 3 for a minute or a foreign job appears, and holds
# scratch/TRACE_COLLECTING while it measures. The container passes are
# watched from the host and the container is killed on a breach.
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
  pkill -f "docker run --rm --platform linux/arm64" 2>/dev/null
  [ -n "${sampler:-}" ] && kill "$sampler" 2>/dev/null
  rm -f "$marker"
}
trap cleanup EXIT
while pgrep -f "traces/record_mac_ext.sh" >/dev/null; do
  sleep 10
done
mac_tools=(--tool "python=$py --version" --tool "numpy=$py -c 'import numpy;print(numpy.__version__)'"
  --tool "uv=uv --version")

mac() { # name dir cmd seed [extra args]
  name=$1 dir=$2 cmd=$3 seed=$4
  shift 4
  while true; do
    touch "$marker"
    "$py" "$traces/wait_idle.py" >"$data/mac-$name.idle.json"
    rm -f "$data/mac-$name.jsonl" "$data/mac-$name.header.json"
    "$py" "$traces/collect.py" --out "$data/mac-$name" \
      --dir-a "$tr/$dir-A" --dir-b "$tr/$dir-B" --cmd "$cmd" \
      --rounds 1000 --seed "$seed" --label "mac-$name" --target "$name" \
      --note "M4 MacBook Pro, macOS, native, strict quiet rule (re-run)" \
      --no-wait --guard quiet --per-run-state "${mac_tools[@]}" "$@"
    [ $? -ne 75 ] && break
    echo "$(date -u +%FT%TZ) mac-$name aborted (guard); restarting"
  done
}

loaded() { # name dir cmd seed
  name=$1 dir=$2 cmd=$3 seed=$4
  shift 4
  while true; do
    touch "$marker"
    "$py" "$traces/wait_idle.py" >"$data/mac-loaded-$name.idle.json"
    "$py" "$traces/burner.py" 4 1 "$seed" >"$data/mac-loaded-$name.burner.log" 2>&1 &
    burner_pid=$!
    sleep 5
    rm -f "$data/mac-loaded-$name.jsonl" "$data/mac-loaded-$name.header.json"
    "$py" "$traces/collect.py" --out "$data/mac-loaded-$name" \
      --dir-a "$tr/$dir-A" --dir-b "$tr/$dir-B" --cmd "$cmd" \
      --rounds 1000 --seed "$seed" --label "mac-loaded-$name" --target "$name" \
      --note "M4 MacBook Pro, macOS, native, strict quiet rule apart from the load. loaded: 4 steady + 1 bursty (20-60 s on/off) spinning python processes at nice 0 on 10 cores (re-run)" \
      --no-wait --guard busy --per-run-state "${mac_tools[@]}" "$@"
    rc=$?
    kill "$burner_pid" 2>/dev/null
    wait "$burner_pid" 2>/dev/null
    burner_pid=""
    [ $rc -ne 75 ] && break
    echo "$(date -u +%FT%TZ) mac-loaded-$name aborted (guard); restarting"
  done
}

lvm() { # target seed
  while true; do
    touch "$marker"
    "$py" "$traces/wait_idle.py" >"$data/linuxvm-$1.idle.json"
    rm -f "$data/linuxvm-$1.jsonl" "$data/linuxvm-$1.header.json"
    (while true; do
      echo "$(date -u +%FT%TZ),$(sysctl -n vm.loadavg)"
      sleep 10
    done) >"$data/hostload-linuxvm-$1.csv" &
    sampler=$!
    docker run --rm --platform linux/arm64 -v "$root":/work \
      perfproof-traces sh /work/tests/calibration/traces/container_target.sh "$1" "$2" &
    dpid=$!
    over=0
    breached=0
    while kill -0 "$dpid" 2>/dev/null; do
      sleep 15
      load=$(sysctl -n vm.loadavg | awk '{print $2}')
      if "$py" - "$load" <<'PYEOF'
import sys
sys.path.insert(0, "tests/calibration/traces")
from collect import IDLE_LOAD, busy_processes, on_ac_power
sys.exit(
    0
    if float(sys.argv[1]) > IDLE_LOAD or busy_processes() or not on_ac_power()
    else 1
)
PYEOF
      then over=$((over + 15)); else over=0; fi
      if [ $over -gt 60 ]; then
        breached=1
        pkill -f "docker run --rm --platform linux/arm64"
        break
      fi
    done
    wait "$dpid" 2>/dev/null
    kill "$sampler" 2>/dev/null
    sampler=""
    [ $breached -eq 0 ] && break
    echo "$(date -u +%FT%TZ) linuxvm-$1 aborted (host busy); restarting"
  done
}

targets=("$@")
[ ${#targets[@]} -eq 0 ] && targets=(rolling membership picolm node lvm-rolling lvm-membership lvm-java loaded-rolling loaded-node)
for t in "${targets[@]}"; do
  echo "$(date -u +%FT%TZ) start $t"
  case $t in
  rolling) mac rolling rolling "$py bench.py --output output/sums.npy" 20261161 ;;
  membership) mac membership membership "$py bench.py --output output/held.txt" 20261162 ;;
  picolm) mac picolm picolm "picolm/picolm models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf -p 'The capital of France is' -n 16 -t 0 -j 4" 20261163 \
    --tool "cc=gcc --version" ;;
  node) mac node node "$node bench.js" 20261164 --tool "node=$node --version" ;;
  lvm-rolling) lvm rolling 20261165 ;;
  lvm-membership) lvm membership 20261166 ;;
  lvm-java) lvm java 20261167 ;;
  loaded-rolling) loaded rolling rolling "$py bench.py --output output/sums.npy" 20261168 ;;
  loaded-node) loaded node node "$node bench.js" 20261169 --tool "node=$node --version" ;;
  esac
  echo "$(date -u +%FT%TZ) done $t"
done
