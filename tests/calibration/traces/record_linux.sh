#!/bin/bash
# Record the real-Linux traces on the Hetzner CCX13 box. Run there, from
# the work directory that holds this script's siblings (see the layout
# below), under setsid/nohup:
#   bash record_linux.sh [target ...]
# Targets: rolling membership node java picolm pinned-rolling.
# Layout: ./collect.py ./bench.js ./Bench.java ./src/{rolling,membership,
# picolm} (each one clean tree) ./models/*.gguf ./venv (python + numpy).
set -eu
w=$(pwd)
py=$w/venv/bin/python
data=$w/data
mkdir -p "$data" "$w/trees"
note="real Linux, Hetzner CCX13 KVM, 1 physical core / 2 SMT threads (2 dedicated vCPUs of AMD EPYC-Milan)"

tools=(--tool "python=$py --version"
  --tool "numpy=$py -c 'import numpy;print(numpy.__version__)'"
  --tool "perf=perf --version" --tool "node=node --version"
  --tool "java=java -version" --tool "javac=javac -version"
  --tool "gcc=gcc --version" --tool "kernel=uname -r"
  --tool "os=sh -c '. /etc/os-release; echo \$PRETTY_NAME'")

fresh() { # name: two clean copies of src/<src> as trees/<name>-A and -B
  for arm in A B; do
    rm -rf "$w/trees/$1-$arm"
    cp -R "$w/src/$2" "$w/trees/$1-$arm"
  done
}

run() { # name cmd seed [extra collect args]
  name=$1 cmd=$2 seed=$3
  shift 3
  "$py" "$w/collect.py" --out "$data/linux-$name" \
    --dir-a "$w/trees/$name-A" --dir-b "$w/trees/$name-B" --cmd "$cmd" \
    --rounds "${ROUNDS:-1000}" --seed "$seed" --label "linux-$name" \
    --target "${name#pinned-}" --note "$note" --linux --no-wait \
    "${tools[@]}" "$@"
}

targets=("$@")
[ ${#targets[@]} -eq 0 ] && targets=(rolling membership node java pinned-rolling picolm)
for t in "${targets[@]}"; do
  echo "$(date -u +%FT%TZ) start $t"
  case $t in
  rolling)
    fresh rolling rolling
    run rolling "$py bench.py --output output/sums.npy" 20261111 ;;
  membership)
    fresh membership membership
    run membership "$py bench.py --output output/held.txt" 20261112 ;;
  node)
    mkdir -p "$w/src/node" && cp "$w/bench.js" "$w/src/node/"
    fresh node node
    run node "node bench.js" 20261113 ;;
  java)
    mkdir -p "$w/src/java" && cp "$w/Bench.java" "$w/src/java/"
    fresh java java
    for arm in A B; do (cd "$w/trees/java-$arm" && javac Bench.java); done
    run java "java -cp . Bench" 20261114 ;;
  pinned-rolling)
    fresh pinned-rolling rolling
    run pinned-rolling "$py bench.py --output output/sums.npy" 20261115 \
      --taskset 1 ;;
  picolm)
    for arm in A B; do
      rm -rf "$w/trees/picolm-$arm"
      mkdir -p "$w/trees/picolm-$arm/models"
      cp -R "$w/src/picolm" "$w/trees/picolm-$arm/picolm"
      ln -s "$w"/models/*.gguf "$w/trees/picolm-$arm/models/"
      make -C "$w/trees/picolm-$arm/picolm" >/dev/null
    done
    run picolm "picolm/picolm models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf -p 'The capital of France is' -n 16 -t 0 -j 4" 20261116 ;;
  esac
  echo "$(date -u +%FT%TZ) done $t"
done
