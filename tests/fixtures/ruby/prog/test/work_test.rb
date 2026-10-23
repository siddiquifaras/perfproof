# frozen_string_literal: true

require "minitest/autorun"
require_relative "../lib/work"

class WorkTest < Minitest::Test
  def test_join_slow
    assert_equal "0,1,2,", Work.join_slow(3)
  end

  def test_histogram
    assert_equal 2, Work.histogram(98)["k0"]
  end

  def test_checksum
    assert_equal 1, Work.checksum(2)
  end
end
