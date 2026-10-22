#!/bin/sh
# Run perfproof's native profiler and coverage commands for real in the
# official gcc image: perf record under perfproof.profilers.native, and
# gcov under perfproof.bench.native_coverage, on the fixture program.
#
# Usage: studies/native/linux.sh <out-dir>
#
# The repository is mounted read-only at /repo; the commands run with
# uv's Python 3.12 (the image's own Python is 3.11) in a venv with perfproof
# installed from the mounted repository.
set -eu
out=${1:?usage: linux.sh <out-dir>}
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
mkdir -p "$out"
out=$(cd "$out" && pwd)

inner='
set -eu
apt-get update -qq > /dev/null
apt-get install -y -qq linux-perf python3-pip > /dev/null 2>&1
pip install -q --break-system-packages uv > /dev/null 2>&1
mkdir -p /build && cp -r /repo/tests/fixtures/native/prog /build/prog
cd /build
uv venv -q --python 3.12 /venv > /dev/null 2>&1
uv pip install -q --python /venv/bin/python /repo > /dev/null 2>&1
py=/venv/bin/python
{
  gcc --version | head -1
  perf version
  gcov --version | head -1
  uv --version
  $py --version
} > /out/versions.txt 2>&1

gcc -O1 -g -fno-omit-frame-pointer -Iprog prog/hot.c prog/work.c -o hot
$py -m perfproof.profilers.native record --output /out/perf.folded \
  --root /build/prog --frequency 999 -- ./hot 2 > /out/perf-run.txt \
  2> /out/perf-stderr.txt && rc=0 || rc=$?
echo "exit $rc" >> /out/perf-run.txt

gcc -O0 --coverage -Iprog prog/check.c prog/work.c -o check
./check > /dev/null
$py -m perfproof.bench.native_coverage gcov --root /build \
  --output /out/gcov.lcov /build 2> /out/gcov-stderr.txt && rc=0 || rc=$?
echo "exit $rc" >> /out/gcov-stderr.txt
'
docker run --rm --cap-add SYS_ADMIN --security-opt seccomp=unconfined \
  -v "$repo:/repo:ro" -v "$out:/out" gcc:14 sh -c "$inner"
