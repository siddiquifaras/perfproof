# frozen_string_literal: true

# One process of the warm-up curve: run one iteration of the fixture
# workload many times and print each iteration's time in nanoseconds,
# one per line, preceded by a header line naming the Ruby and whether
# YJIT is on.
require_relative "../../tests/fixtures/ruby/prog/lib/work"

iterations = Integer(ARGV.fetch(0, "1500"))
clock = Process::CLOCK_MONOTONIC
yjit = defined?(RubyVM::YJIT) && RubyVM::YJIT.enabled?
puts "# #{RUBY_DESCRIPTION} yjit=#{yjit ? 'on' : 'off'}"
iterations.times do
  started = Process.clock_gettime(clock, :nanosecond)
  Work.join_slow(600)
  Work.histogram(36_000)
  Work.checksum(90_000)
  puts Process.clock_gettime(clock, :nanosecond) - started
end
