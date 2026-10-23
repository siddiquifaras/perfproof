#!/bin/sh
# Capture the .NET fixtures in this directory from real runs of the
# tools, inside the official dotnet SDK image.
#
# Usage: tests/fixtures/dotnet/capture.sh <out-dir>
#
# The fixture program (tests/fixtures/dotnet/prog) is copied to /work
# in the container, so every path the tools name is /work/<file>.
# Output is each tool's own; nothing is edited.
set -eu
out=${1:?usage: capture.sh <out-dir>}
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$out"
out=$(cd "$out" && pwd)

inner='
set -eu
cp -r /src /work
cd /work
dotnet --version > /out/dotnet-version.txt
dotnet --list-runtimes > /out/list-runtimes.txt
dotnet --list-sdks > /out/list-sdks.txt
dotnet --info > /out/info.txt
dotnet tool install --global dotnet-trace > /out/tool-install.txt 2>&1
export PATH="$PATH:/root/.dotnet/tools"
dotnet-trace --version > /out/dotnet-trace-version.txt

dotnet build -c Release -o /work/bin App/App.csproj > /out/build.txt 2>&1
dotnet-trace collect --format Speedscope -o /out/trace -- \
  dotnet /work/bin/App.dll 2 > /out/trace-run.txt 2>&1
rm /out/trace
dotnet build -c Release App/App.csproj > /out/build-release.txt 2>&1
# `dotnet run` does not end under dotnet-trace (the SDK host waits), so
# it is stopped after 30 s; the status says so.
timeout 30 dotnet-trace collect --format Speedscope -o /out/run -- \
  dotnet run --no-build -c Release --project App/App.csproj -- 1 \
  > /out/run-run.txt 2>&1 || echo "stopped, status $?" >> /out/run-run.txt
grep -o "App!" /out/run.speedscope.json | wc -l > /out/run-app-frames.txt
rm /out/run /out/run.speedscope.json

dotnet test Tests/Tests.csproj --collect:"XPlat Code Coverage" \
  --results-directory /work/results > /out/test-run.txt 2>&1
cp /work/results/*/coverage.cobertura.xml /out/coverage.cobertura.xml
'

docker run --rm \
  -v "$here/prog:/src:ro" \
  -v "$out:/out" \
  mcr.microsoft.com/dotnet/sdk:10.0 sh -c "$inner"
