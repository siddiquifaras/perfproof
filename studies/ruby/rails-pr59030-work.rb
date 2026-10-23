require "active_support"
require "active_support/core_ext/object/blank"
inputs = ["   ", " " * 136, " this is a longer test", "\n"]
1_000_000.times { inputs.each { |s| s.blank? } }
