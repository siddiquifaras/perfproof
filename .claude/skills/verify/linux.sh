#!/bin/sh
# Run the full check on Linux x86_64 in Docker against one commit, as
# CI's ubuntu job would. Used while GitHub Actions is manual-only.
#
# Usage: .claude/skills/verify/linux.sh <commit> [python-version]
set -eu
commit=${1:?usage: linux.sh <commit> [python-version]}
py=${2:-3.12}

# The container has no machine-id and the archive is not a repository;
# the machine probes and the lock tests need both.
inner='
set -e
python -c "import uuid; print(uuid.uuid4().hex)" > /etc/machine-id
mkdir /w && cd /w && tar -x
pip -q install --root-user-action=ignore uv==0.12.20
git init -q
git -c user.name=v -c user.email=v@v add -A
git -c user.name=v -c user.email=v@v commit -qm v
uv sync -q --frozen
uv lock --check
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen pyright
uv run --frozen pytest -q
'

# pyright installs Node through nodeenv; pin it, because nodeenv's
# "latest" lookup fails on pre-release version strings (2026-10-30).
git archive --format=tar "$commit" | docker run --rm -i \
  --platform linux/amd64 -e "UV_PYTHON=$py" \
  -e PYRIGHT_PYTHON_NODE_VERSION=22.11.0 \
  -e UV_PYTHON_PREFERENCE=only-system "python:$py-bookworm" sh -c "$inner"
