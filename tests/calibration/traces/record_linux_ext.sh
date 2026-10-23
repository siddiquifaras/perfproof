#!/bin/bash
# Extension passes on the Hetzner CCX13 box: agent, web and LLM-serving
# targets, each quiet and then loaded (steady stress-ng, one vCPU at
# 50%). Run there from the work directory, after record_linux.sh:
#   bash record_linux_ext.sh [name ...]
# names: agent web llm. Layout: ./targets/{agent,web,llm}, ./collect.py,
# ./venv, ./ollama/bin/ollama, ./ollama-models (qwen2.5:0.5b pulled).
set -u
w=$(pwd)
while pgrep -f "record_linux.sh" >/dev/null; do sleep 10; done
py=$w/venv/bin/python
data=$w/data
mkdir -p "$data" "$w/trees"
base="real Linux, Hetzner CCX13 KVM, 1 physical core / 2 SMT threads (2 dedicated vCPUs of AMD EPYC-Milan)"
tools=(--tool "python=$py --version"
  --tool "numpy=$py -c 'import numpy;print(numpy.__version__)'"
  --tool "perf=perf --version" --tool "kernel=uname -r"
  --tool "stress-ng=stress-ng --version")
load_pid=""
cleanup() {
  [ -n "$load_pid" ] && kill "$load_pid" 2>/dev/null
  pkill -f stress-ng 2>/dev/null
  pkill -x ollama 2>/dev/null
  true
}
trap cleanup EXIT

make_trees() { # name
  for arm in A B; do
    d=$w/trees/$1-$arm
    rm -rf "$d"
    mkdir -p "$d"
    cp "$w"/targets/"$1"/*.py "$d/"
    [ "$1" = agent ] && cp "$w/targets/agent/fixture.json" "$d/" &&
      "$py" "$w/targets/agent/make_corpus.py" "$d"
  done
  rm -f "$w"/trees/"$1"-?/make_corpus.py
}

# name cmd seed rounds kind(quiet|steady) extra-note [collect args]
go() {
  name=$1 cmd=$2 seed=$3 rounds=$4 kind=$5 note=$6
  shift 6
  label=linux-$name
  desc=""
  if [ "$kind" = steady ]; then
    label=linux-loaded-steady-$name
    desc=" loaded: stress-ng --cpu 1 --cpu-load 50 (one vCPU at 50%, steady)"
    stress-ng --cpu 1 --cpu-load 50 --timeout 40000s >/dev/null 2>&1 &
    load_pid=$!
    sleep 5
  fi
  "$py" "$w/collect.py" --out "$data/$label" \
    --dir-a "$w/trees/$name-A" --dir-b "$w/trees/$name-B" --cmd "$cmd" \
    --rounds "$rounds" --seed "$seed" --label "$label" --target "$name" \
    --note "$base. $note$desc" --linux --no-wait "${tools[@]}" "$@"
  [ -n "$load_pid" ] && kill "$load_pid" 2>/dev/null
  pkill -f stress-ng 2>/dev/null
  load_pid=""
}

names=("$@")
[ ${#names[@]} -eq 0 ] && names=(agent web llm)
for t in "${names[@]}"; do
  make_trees "$t"
  for kind in ${KINDS:-quiet steady}; do
    echo "$(date -u +%FT%TZ) start $t $kind"
    case $t in
    agent)
      go agent "$py agent.py" 20261141 1000 $kind \
        "agent loop replaying a fixture, real local file search and JSON work" ;;
    web)
      go web "$py web_bench.py" 20261142 600 $kind \
        "FastAPI+uvicorn started per run, 2000-request burst at concurrency 16 (httpx), 600 pairs" ;;
    llm)
      bash "$w/ollama_serve.sh" "$w/ollama/bin/ollama" "$w/ollama-models" \
        >"$data/ollama-$kind.log" 2>&1 &
      sleep 8
      export OLLAMA_HOST=127.0.0.1:11500
      go llm "$py llm_bench.py" 20261143 500 $kind \
        "Ollama CPU serving qwen2.5:0.5b, 4 prompts x 32 tokens per run, server kept running across runs and arms, 500 pairs" \
        --watch-process ollama
      pkill -x ollama
      sleep 3 ;;
    esac
    echo "$(date -u +%FT%TZ) done $t $kind"
  done
done
echo "$(date -u +%FT%TZ) all extension passes done; stress-ng/ollama left: $(pgrep -c stress-ng) $(pgrep -cx ollama)"
