# frozen_string_literal: true

# A small library with known hot spots for the profiler and coverage
# fixtures: quadratic string building, allocation-heavy hashes, and a
# function no test reaches.
module Work
  module_function

  def join_slow(count)
    out = +""
    count.times { |i| out = out + i.to_s + "," }
    out
  end

  def histogram(count)
    counts = Hash.new(0)
    count.times { |i| counts["k#{i % 97}"] += 1 }
    counts
  end

  def checksum(count)
    total = 0
    count.times { |i| total = (total * 31 + i) % 1_000_003 }
    total
  end

  def never_called
    :unreached
  end
end
