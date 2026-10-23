#!/bin/sh
# Capture the Ruby fixtures in this directory from real runs of the
# tools, inside the official ruby image.
#
# Usage: tests/fixtures/ruby/capture.sh <out-dir>
#
# The fixture program (tests/fixtures/ruby/prog) is mounted at /src,
# the profiler hook directory at /hook, and <out-dir> at /out, so every
# file the tools name is /src/<file>. Output is each tool's own; nothing
# is edited.
set -eu
out=${1:?usage: capture.sh <out-dir>}
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../../.." && pwd)
mkdir -p "$out"
out=$(cd "$out" && pwd)

inner='
set -eu
cd /src
ruby -v > /out/ruby-v.txt
ruby --yjit -e "puts RubyVM::YJIT.enabled?" > /out/yjit-enabled.txt
gem install --no-document stackprof simplecov simplecov-lcov > /out/gem-install.txt 2>&1
gem list 2>/dev/null | grep -E "^(stackprof|simplecov|simplecov-lcov|simplecov_json_formatter|minitest) " > /out/gems.txt

mkdir -p /out/stackprof
RUBYOPT=-r/hook/ruby_hook.rb PERFPROOF_RUBY_PROFILE_DIR=/out/stackprof \
  PERFPROOF_RUBY_INTERVAL_US=1000 ruby bin/hot 2
cat /out/stackprof/ruby.*.folded > /out/hot.folded

ruby -e "
require \"simplecov\"
require \"simplecov-lcov\"
SimpleCov::Formatter::LcovFormatter.config.report_with_single_file = true
SimpleCov::Formatter::LcovFormatter.config.output_directory = \"/out/coverage\"
SimpleCov::Formatter::LcovFormatter.config.lcov_file_name = \"simplecov.lcov\"
SimpleCov.formatters = [SimpleCov::Formatter::LcovFormatter]
SimpleCov.coverage_dir(\"/out/coverage\")
SimpleCov.start { add_filter \"/test/\" }
load \"test/work_test.rb\"
" > /out/coverage-run.txt 2>&1
cp /out/coverage/.resultset.json /out/resultset.json
cp /out/coverage/simplecov.lcov /out/simplecov.lcov
'

docker run --rm \
  -v "$root/tests/fixtures/ruby/prog:/src" \
  -v "$root/src/perfproof/profilers:/hook:ro" \
  -v "$out:/out" \
  ruby:4.0 sh -c "$inner"
