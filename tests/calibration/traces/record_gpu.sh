#!/bin/bash
# Record the GPU-kernel A/A traces on a rented GPU host. Run on the host,
# from the work directory, with the script next to collect.py,
# gpu_collect.py, burner.py and targets/gpu/ (GPU.md has the copy
# command), under tmux or setsid:
#   bash record_gpu.sh probe    # health + start-up cost + pair counts
#   bash record_gpu.sh record   # record per the plan; rerun to resume
#   bash record_gpu.sh verify   # check every finished trace
#   bash record_gpu.sh deadman  # arm the dead-man stop (also by probe)
#   STANDIN=1 bash record_gpu.sh standin   # CPU dry run, no GPU
# Environment: PY (python3), DATA ($PWD/data), TREES ($PWD/trees),
# BUDGET_S (14400, the recording budget), GPU_NOTE (free text for the
# headers), SYNC_CMD (a command with {files}, optional), ROUNDS
# (stand-in pairs, 6), HARD_STOP_EPOCH (unix time at which the dead-man
# stops the pod; needs runpodctl and RUNPOD_POD_ID).
set -eu
w=$(cd "$(dirname "$0")" && pwd)
data=${DATA:-$PWD/data}
trees=${TREES:-$PWD/trees}
py=${PY:-python3}
budget=${BUDGET_S:-14400}
note=${GPU_NOTE:-rented GPU host (see header gpu block)}
names=(matmul sgemm membound attention matmul_loaded)
extra=()
tools=(--tool "kernel=uname -r"
  --tool "os=sh -c '. /etc/os-release; echo \$PRETTY_NAME'")
if [ "${STANDIN:-0}" = 1 ]; then
  names=(matmul matmul_loaded)
  extra+=(--no-gpu)
  note="CPU stand-in dry run, no GPU"
else
  tools+=(--tool "driver=nvidia-smi --query-gpu=driver_version --format=csv,noheader"
    --tool "torch=$py -c 'import torch;print(torch.__version__, torch.version.cuda, torch.backends.cudnn.version())'"
    --tool "nvcc=sh -c 'nvcc --version | tail -1'"
    --tool "numpy=$py -c 'import numpy;print(numpy.__version__)'")
fi
mkdir -p "$data" "$trees"
"$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "$py is older than 3.11 (collect.py needs datetime.UTC): set PY"
  exit 3
}

deadman() { # stop the pod at HARD_STOP_EPOCH (GPU.md, "Hard stop")
  [ "${STANDIN:-0}" = 1 ] && return 0
  if [ -z "${HARD_STOP_EPOCH:-}" ]; then
    echo "DEAD-MAN OFF: HARD_STOP_EPOCH not set; the owner must terminate"
    return 1
  fi
  if ! command -v runpodctl >/dev/null || [ -z "${RUNPOD_POD_ID:-}" ]; then
    echo "DEAD-MAN OFF: runpodctl or RUNPOD_POD_ID missing; the owner must terminate"
    return 1
  fi
  if [ -f "$data/deadman.pid" ] && kill -0 "$(cat "$data/deadman.pid")" 2>/dev/null; then
    echo "dead-man already armed (pid $(cat "$data/deadman.pid"))"
    return 0
  fi
  left=$((HARD_STOP_EPOCH - $(date +%s)))
  if [ "$left" -le 0 ]; then
    echo "DEAD-MAN OFF: HARD_STOP_EPOCH is in the past"
    return 1
  fi
  detach=nohup
  command -v setsid >/dev/null && detach="setsid nohup"
  $detach sh -c "sleep $left; runpodctl pod stop \"\$RUNPOD_POD_ID\"" \
    >"$data/deadman.log" 2>&1 &
  echo $! >"$data/deadman.pid"
  echo "dead-man armed: stops the pod in $left s (pid $!); this cannot be tested until it fires"
}

cmd_for() {
  if [ "${STANDIN:-0}" = 1 ]; then echo "$py cpu_standin.py"; return; fi
  case $1 in
  matmul | matmul_loaded) echo "$py matmul_bench.py" ;;
  membound) echo "$py membound_bench.py" ;;
  attention) echo "$py attention_bench.py" ;;
  sgemm) echo "./sgemm" ;;
  esac
}
seed_for() {
  case $1 in
  matmul) echo 20261201 ;; sgemm) echo 20261202 ;;
  membound) echo 20261203 ;; attention) echo 20261204 ;;
  matmul_loaded) echo 20261205 ;;
  esac
}
background_for() {
  [ "$1" = matmul_loaded ] || return 0
  if [ "${STANDIN:-0}" = 1 ]; then
    echo "$py $w/burner.py 1 0 1"
  else
    echo "$py $w/targets/gpu/gpu_burner.py"
  fi
}

prep() { # name: two trees, built once (kept across resumes)
  for arm in A B; do
    t=$trees/$1-$arm
    [ -d "$t" ] || { mkdir -p "$t" && cp "$w"/targets/gpu/* "$t/"; }
  done
  if [ "$1" = sgemm ] && [ "${STANDIN:-0}" != 1 ]; then
    # built once and copied, so both arms run one binary; cmp proves it
    [ -x "$trees/sgemm-A/sgemm" ] ||
      (cd "$trees/sgemm-A" && nvcc -O3 -std=c++17 -arch=native -o sgemm sgemm.cu)
    cp "$trees/sgemm-A/sgemm" "$trees/sgemm-B/sgemm"
    cmp "$trees/sgemm-A/sgemm" "$trees/sgemm-B/sgemm" || {
      echo "sgemm binaries of A and B differ"
      exit 3
    }
  fi
}

rec() { # name rounds [extra gpu_collect args]
  name=$1 n=$2
  shift 2
  bg=$(background_for "$name")
  args=(--out "$data/linux-gpu-$name"
    --dir-a "$trees/$name-A" --dir-b "$trees/$name-B"
    --cmd "$(cmd_for "$name")" --rounds "$n" --seed "$(seed_for "$name")"
    --label "linux-gpu-$name" --target "$name" --note "$note")
  [ -n "$bg" ] && args+=(--background "$bg")
  [ -n "${SYNC_CMD:-}" ] && args+=(--sync-cmd "$SYNC_CMD")
  "$py" "$w/gpu_collect.py" "${args[@]}" "${tools[@]}" ${extra[@]+"${extra[@]}"} "$@"
}

target_pairs() { # name: planned target when not planned yet
  case $1 in sgemm) echo 500 ;; *) echo 300 ;; esac
}

case ${1:-} in
probe)
  deadman || true
  for n in "${names[@]}"; do
    prep "$n"
    echo "== probe $n"
    rec "$n" "$(target_pairs "$n")" --health-only || echo "probe $n failed"
  done
  rc=0
  "$py" "$w/gpu_collect.py" plan --budget-s "$budget" \
    "$data"/linux-gpu-*.health.json >"$data/plan.env" || rc=$?
  cat "$data/plan.env"
  [ "$rc" -eq 0 ] || { echo "PLAN FAILED (exit $rc): do not record"; exit "$rc"; }
  ;;
record | standin)
  if [ "$1" = standin ]; then
    for n in "${names[@]}"; do
      prep "$n"
      rec "$n" "${ROUNDS:-6}" --run-timeout 60 --background-settle-s 1 --probes 2
    done
    MIN_PAIRS=${ROUNDS:-6} exec bash "$0" verify
  fi
  [ -f "$data/plan.env" ] || { echo "run probe first"; exit 3; }
  deadman || true
  # shellcheck disable=SC1091
  . "$data/plan.env"
  [ "${PAIRS_matmul:-0}" -gt 0 ] && [ "${PAIRS_sgemm:-0}" -gt 0 ] || {
    echo "plan has no matmul or no sgemm: refusing to record"
    exit 3
  }
  [ -f "$data/record_started" ] || date +%s >"$data/record_started"
  deadline=$(($(cat "$data/record_started") + budget))
  for n in "${names[@]}"; do
    pv=PAIRS_$n
    ps=PAIRSEC_$n
    pairs=${!pv:-0}
    [ "$pairs" -gt 0 ] || { echo "== skip $n (0 pairs planned)"; continue; }
    left=$((deadline - $(date +%s)))
    [ "$left" -gt 120 ] || { echo "== budget spent, skipping $n"; continue; }
    cap=$(awk -v p="$pairs" -v s="${!ps}" 'BEGIN{printf "%d", p*s*1.25}')
    [ "$cap" -lt "$left" ] || cap=$left
    echo "== record $n: $pairs pairs, at most $cap s"
    prep "$n"
    rc=0
    rec "$n" "$pairs" --time-budget-s "$cap" || rc=$?
    case $rc in
    0 | 75 | 76) echo "== $n exit $rc" ;;
    *) echo "== $n exit $rc: stopping"; exit "$rc" ;;
    esac
  done
  bash "$0" verify || true
  ;;
deadman)
  deadman
  ;;
verify)
  "$py" "$w/gpu_collect.py" verify --min-pairs "${MIN_PAIRS:-300}" \
    "$data"/linux-gpu-*.jsonl
  ;;
*)
  echo "usage: record_gpu.sh probe|record|verify|deadman|standin" >&2
  exit 3
  ;;
esac
