#!/bin/bash
# Record mac-java, mac-web-node and mac-web-go, one at a time, from the
# worktree root (the procedure of the 2026-10-29 managed-classes run):
#   JDK_HOME=<jdk home> GO_BIN=<go bin dir> bash \
#     tests/calibration/traces/record_managed.sh
# JDK_HOME defaults to the Temurin 21 under ~/.local/jdk, GO_BIN to
# ~/.local/go/bin. Needs `uv sync`, node >= 22.13 on PATH and
# scratch/venv-web with httpx (uv venv scratch/venv-web; uv pip install
# --python scratch/venv-web/bin/python httpx). Trees and raw output go
# in scratch/traces (gitignored). Holds TRACE_MARKER (default: the main
# checkout's scratch/TRACE_COLLECTING, which the eval runner watches) and
# samples the host load every 10 s into a hostload CSV, as record_all.sh
# does. Does not wait for quiet. The host load goes in the header note
# afterwards.
set -eu
root=$(pwd)
py=$root/.venv/bin/python
webpy=$root/scratch/venv-web/bin/python
traces=$root/tests/calibration/traces
tr=$root/scratch/traces
data=$tr/data
jdk=${JDK_HOME:-$(echo "$HOME"/.local/jdk/jdk-21*/Contents/Home)}
gobin=${GO_BIN:-$HOME/.local/go/bin}
common=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")
marker=${TRACE_MARKER:-$common/scratch/TRACE_COLLECTING}
export PATH=$gobin:$PATH
mkdir -p "$data" "$(dirname "$marker")"

for arm in A B; do
  mkdir -p "$tr/web-node-$arm" "$tr/web-go-$arm" "$tr/java-$arm"
  cp "$traces/targets/web/web_bench.py" "$traces/targets/web-node/server.js" \
    "$traces/targets/web-node/package.json" "$tr/web-node-$arm/"
  cp "$traces/targets/web/web_bench.py" "$tr/web-go-$arm/"
  (cd "$traces/targets/web-go" && go build -o "$tr/web-go-$arm/webgo" .)
  cp "$traces/Bench.java" "$tr/java-$arm/"
  (cd "$tr/java-$arm" && "$jdk/bin/javac" Bench.java)
done

sampler=""
toucher=""
cleanup() {
  [ -n "$sampler" ] && kill "$sampler" 2>/dev/null
  [ -n "$toucher" ] && kill "$toucher" 2>/dev/null
  rm -f "$marker"
}
trap cleanup EXIT
touch "$marker"
(while true; do
  touch "$marker"
  sleep 30
done) &
toucher=$!

rec() { # name dir cmd seed rounds note [tools...]
  name=$1 dir=$2 cmd=$3 seed=$4 rounds=$5 note=$6
  shift 6
  (while true; do
    echo "$(date -u +%FT%TZ),$(sysctl -n vm.loadavg)"
    sleep 10
  done) >"$data/hostload-mac-$name.csv" &
  sampler=$!
  rm -f "$data/mac-$name.jsonl" "$data/mac-$name.header.json"
  "$py" "$traces/collect.py" --out "$data/mac-$name" \
    --dir-a "$tr/$dir-A" --dir-b "$tr/$dir-B" --cmd "$cmd" \
    --rounds "$rounds" --seed "$seed" --label "mac-$name" --target "$name" \
    --note "M4 MacBook Pro, macOS, native, normal day-to-day use (not waited for quiet). $note" \
    --no-wait --per-run-state --tool "python=$py --version" "$@"
  kill "$sampler" 2>/dev/null
  sampler=""
}

rec java java "$jdk/bin/java -cp . Bench" 20261171 1000 \
  "Bench.java (12M-key sort) on JDK 21, fresh JVM per run, 1000 pairs" \
  --tool "java=$jdk/bin/java -version" --tool "javac=$jdk/bin/javac -version"
rec web-node web-node "$webpy web_bench.py node server.js" 20261172 600 \
  "Node node:http + node:sqlite server started per run, 2000-request burst at concurrency 16 (httpx), 600 pairs" \
  --tool "node=node --version" \
  --tool "httpx=$webpy -c 'import httpx;print(httpx.__version__)'"
rec web-go web-go "$webpy web_bench.py ./webgo" 20261173 600 \
  "Go net/http server started per run, 2000-request burst at concurrency 16 (httpx), 600 pairs" \
  --tool "go=go version" \
  --tool "httpx=$webpy -c 'import httpx;print(httpx.__version__)'"
