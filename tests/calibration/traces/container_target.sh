#!/bin/sh
# Runs inside the perfproof-traces container (Linux VM on the Mac, not
# real Linux hardware). Usage: container_target.sh TARGET SEED
# TARGET is rolling, membership or java. Expects the worktree at /work.
set -eu
target=$1
seed=$2
traces=/work/tests/calibration/traces
out=/work/scratch/traces/data
mkdir -p /bench "$out"
case "$target" in
rolling) cmd="python bench.py --output output/sums.npy" ;;
membership) cmd="python bench.py --output output/held.txt" ;;
java) cmd="java -cp . Bench" ;;
esac
for arm in A B; do
  dir=/bench/$target-$arm
  rm -rf "$dir"
  if [ "$target" = java ]; then
    mkdir -p "$dir"
    cp "$traces/Bench.java" "$dir/"
    (cd "$dir" && javac Bench.java)
  else
    cp -R "/work/scratch/traces/$target-$arm" "$dir"
    rm -rf "$dir/output" "$dir/__pycache__"
  fi
done
exec python "$traces/collect.py" \
  --out "$out/linuxvm-$target" \
  --dir-a "/bench/$target-A" --dir-b "/bench/$target-B" \
  --cmd "$cmd" --rounds "${ROUNDS:-1000}" --seed "$seed" \
  --label "linuxvm-$target" --target "$target" \
  --note "Linux VM on the Mac (Docker Desktop, arm64 container, native), not real Linux hardware. load1 is the Docker VM's own load average; the Mac's load is in the hostload CSV" \
  --tool "python=python --version" --tool "numpy=python -c 'import numpy;print(numpy.__version__)'" \
  --tool "java=java -version" --tool "javac=javac -version" \
  --tool "kernel=uname -r" \
  --no-time --no-pmset --no-wait
