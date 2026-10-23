#!/bin/bash
# Extension passes on the Mac: agent, web and LLM-serving targets, each
# quiet and then loaded (4 steady + 1 bursty spinning processes), from
# the worktree root:
#   bash tests/calibration/traces/record_mac_ext.sh [agent web llm]
# Waits until no `claude -p` and no skill_eval process is running, holds
# scratch/TRACE_COLLECTING while measuring, and stops everything it
# started on any exit. Needs scratch/venv-web (fastapi, uvicorn, httpx),
# scratch/ollama-mac (binary, serve.sh) and scratch/ollama-models with
# qwen2.5:0.5b pulled.
set -u
root=$(pwd)
py=$root/.venv/bin/python
webpy=$root/scratch/venv-web/bin/python
traces=$root/tests/calibration/traces
tr=$root/scratch/traces
data=$tr/data
marker=${TRACE_MARKER:-$root/scratch/TRACE_COLLECTING}
burner_pid=""
cleanup() {
  [ -n "$burner_pid" ] && kill "$burner_pid" 2>/dev/null
  pkill -f "ollama-mac/ollama serve" 2>/dev/null
  rm -f "$marker"
}
trap cleanup EXIT
while pgrep -f "claude -p" >/dev/null || pgrep -f "skill_eval" >/dev/null; do
  sleep 30
done

make_trees() { # name
  for arm in A B; do
    d=$tr/$1-$arm
    rm -rf "$d"
    mkdir -p "$d"
    case $1 in
    agent)
      cp "$traces/targets/agent/agent.py" "$traces/targets/agent/fixture.json" "$d/"
      "$py" "$traces/targets/agent/make_corpus.py" "$d" ;;
    web) cp "$traces/targets/web/app.py" "$traces/targets/web/web_bench.py" "$d/" ;;
    llm) cp "$traces/targets/llm/llm_bench.py" "$d/" ;;
    esac
  done
}

pass() { # name cmd seed rounds kind note [collect args]
  name=$1 cmd=$2 seed=$3 rounds=$4 kind=$5 note=$6
  shift 6
  label=mac-$name
  desc=""
  [ "$kind" = loaded ] && label=mac-loaded-$name &&
    desc=" loaded: 4 steady + 1 bursty (20-60 s on/off) spinning python processes at nice 0 on 10 cores"
  guard=quiet
  [ "$kind" = loaded ] && guard=busy
  while true; do
    touch "$marker"
    "$py" "$traces/wait_idle.py" >"$data/$label.idle.json"
    if [ "$kind" = loaded ]; then
      "$py" "$traces/burner.py" 4 1 "$seed" >"$data/$label.burner.log" 2>&1 &
      burner_pid=$!
      sleep 5
    fi
    rm -f "$data/$label.jsonl" "$data/$label.header.json"
    "$py" "$traces/collect.py" --out "$data/$label" \
      --dir-a "$tr/$name-A" --dir-b "$tr/$name-B" --cmd "$cmd" \
      --rounds "$rounds" --seed "$seed" --label "$label" --target "$name" \
      --note "M4 MacBook Pro, macOS, native. $note.$desc" --no-wait \
      --guard $guard --per-run-state --tool "python=$py --version" "$@"
    rc=$?
    if [ -n "$burner_pid" ]; then
      kill "$burner_pid" 2>/dev/null
      wait "$burner_pid" 2>/dev/null
      burner_pid=""
    fi
    [ $rc -ne 75 ] && break
    echo "$(date -u +%FT%TZ) $label aborted (guard); waiting for quiet and restarting"
  done
}

names=("$@")
[ ${#names[@]} -eq 0 ] && names=(agent web llm)
for t in "${names[@]}"; do
  make_trees "$t"
  rounds=1000
  if [ "$t" = web ]; then
    touch "$marker"
    "$py" "$traces/wait_idle.py" >/dev/null
    start=$(date +%s)
    for i in 1 2 3; do (cd "$tr/web-A" && "$webpy" web_bench.py >/dev/null 2>&1); done
    per=$(($(date +%s) - start))
    # three runs took $per s: above 12 s means a round over 4 s
    [ "$per" -gt 12 ] && rounds=600
    echo "web probe: 3 runs in ${per}s, rounds $rounds"
  fi
  for kind in quiet loaded; do
    echo "$(date -u +%FT%TZ) start $t $kind"
    case $t in
    agent)
      pass agent "$py agent.py" 20261151 "$rounds" $kind \
        "agent loop replaying a fixture, real local file search and JSON work" ;;
    web)
      pass web "$webpy web_bench.py" 20261152 "$rounds" $kind \
        "FastAPI+uvicorn started per run, 2000-request burst at concurrency 16 (httpx), $rounds pairs" ;;
    llm)
      nohup bash "$root/scratch/ollama-mac/serve.sh" >"$data/ollama-mac-$kind.log" 2>&1 &
      sleep 8
      export OLLAMA_HOST=127.0.0.1:11500
      pass llm "$py llm_bench.py" 20261153 "$rounds" $kind \
        "Ollama CPU-only (num_gpu 0) qwen2.5:0.5b, 4 prompts x 32 tokens per run, server kept running across runs and arms" \
        --watch-process ollama
      pkill -f "ollama-mac/ollama serve"
      sleep 3 ;;
    esac
    echo "$(date -u +%FT%TZ) done $t $kind"
  done
done
echo "$(date -u +%FT%TZ) all done"
