#!/bin/sh
# Profile a Ruby workload in the Rails task tree through the stackprof
# wrapper, with Ruby in the official image under Docker: the profile the
# playbook (skills/perfproof/ruby.md) describes for containers.
#
# Usage: studies/ruby/rails-profile.sh <task tree> <workload.rb> <out-dir>
#
# <task tree> is a checkout of rails/rails mounted at /src (the one
# `uv run python eval/mine/check.py rails-rails-pr59030` builds under
# eval/mine/work/), already bundled into the perfproof-gems volume with
# `gem install stackprof` run once in it:
#   docker run --rm -v perfproof-gems:/usr/local/bundle \
#     perfproof-ruby-4.0-tzdata-legacy gem install --no-document stackprof
# Writes <out-dir>/out.folded and <out-dir>/out.folded.stackprof/.
set -eu
tree=$(cd "$1" && pwd)
workload=$(cd "$(dirname "$2")" && pwd)/$(basename "$2")
mkdir -p "$3"
out=$(cd "$3" && pwd)
root=$(cd "$(dirname "$0")/../.." && pwd)
rm -rf "$out/out.folded" "$out/out.folded.stackprof"
cd "$root"
uv run python -m perfproof.profilers.ruby record \
  --output "$out/out.folded" --timeout 900 -- \
  docker run --rm --init \
  -e RUBYOPT -e PERFPROOF_RUBY_PROFILE_DIR -e PERFPROOF_RUBY_INTERVAL_US \
  -e PERFPROOF_RUBY_MODE \
  -e BUNDLE_WITHOUT=rubocop:mdl:doc:job:cable:storage \
  -v "$tree:/src" -v perfproof-gems:/usr/local/bundle \
  -v "$root/src/perfproof/profilers:$root/src/perfproof/profilers:ro" \
  -v "$out/out.folded.stackprof:$out/out.folded.stackprof" \
  -v "$workload:/work.rb:ro" -w /src/activesupport \
  perfproof-ruby-4.0-tzdata-legacy bundle exec ruby --yjit -Ilib /work.rb
