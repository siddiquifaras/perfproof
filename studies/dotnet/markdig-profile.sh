#!/bin/sh
# Profile the built Markdig pipe-table workload through the dotnet-trace
# wrapper, inside the Linux image the .NET integration tests use.
#
# Usage: studies/dotnet/markdig-profile.sh <task tree> <out-dir>
#
# <task tree> is a checkout of xoofx/markdig with the workload built,
# as `uv run python eval/mine/check.py xoofx-markdig-pr922` leaves it
# under eval/mine/work/. The image comes from
# `docker build -t perfproof-dotnet-it tests/fixtures/dotnet`.
# Writes <out-dir>/out.folded, <out-dir>/top.txt and the raw trace in
# <out-dir>/out.folded.dotnet-trace/.
set -eu
tree=$(cd "$1" && pwd)
mkdir -p "$2"
out=$(cd "$2" && pwd)
root=$(cd "$(dirname "$0")/../.." && pwd)
rm -rf "$out/out.folded" "$out/out.folded.dotnet-trace"
docker run --rm -e DOTNET_CLI_TELEMETRY_OPTOUT=1 \
  -e UV_PROJECT_ENVIRONMENT=/venv \
  -v "$root:/w" -v "$tree:/src:ro" -v "$out:/out" -w /w \
  perfproof-dotnet-it sh -c '
  set -eu
  uv run python -m perfproof.profilers.dotnet record \
    --output /out/out.folded --project-root /src --timeout 600 -- \
    dotnet /src/EvalPipeTable/bin/Release/net10.0/EvalPipeTable.dll
  uv run python studies/dotnet/top.py /out/out.folded > /out/top.txt
'
