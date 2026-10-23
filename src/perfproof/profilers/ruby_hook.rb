# Preload for `python -m perfproof.profilers.ruby record`; not for
# direct use. Ruby loads it through RUBYOPT=-rruby_hook (its directory in
# RUBYLIB), so it also
# reaches the Ruby processes a launcher such as `bundle exec` starts. It
# samples the process with the stackprof gem from before the program's
# first line until it exits, and writes one folded-stacks file per
# process, as the wrapper's output directory names:
#
#   Class#method (path/file.rb:LINE);...  COUNT
#
# LINE is the line a sample was on in that frame. A process killed by a
# signal writes nothing.
begin
  require "stackprof"
rescue LoadError => e
  warn "perfproof.profilers.ruby: the stackprof gem is not installed: #{e.message}"
  exit!(4)
end

dir = ENV["PERFPROOF_RUBY_PROFILE_DIR"]
if dir && !dir.empty?
  interval = Integer(ENV.fetch("PERFPROOF_RUBY_INTERVAL_US", "1000"))
  mode = ENV.fetch("PERFPROOF_RUBY_MODE", "cpu").to_sym
  StackProf.start(mode: mode, raw: true, interval: interval)

  # The sampling timer survives exec, and the new program has no
  # handler for its signal until this hook loads in it, so a process
  # that execs (`bundle exec`, a binstub) would be killed by
  # "Profiling timer expired". The profiler is stopped before the exec
  # and started again if the exec fails; the exec'd Ruby loads the hook
  # itself.
  stopping_exec = Module.new do
    define_method(:exec) do |*args, **opts|
      StackProf.stop
      begin
        super(*args, **opts)
      rescue SystemCallError
        StackProf.start(mode: mode, raw: true, interval: interval)
        raise
      end
    end
  end
  Kernel.prepend(stopping_exec)
  Process.singleton_class.prepend(stopping_exec)
  Kernel.singleton_class.prepend(stopping_exec)

  # A forked child inherits the parent's samples and its at_exit, so
  # without a reset it would write them a second time.
  child_reset = Module.new do
    define_method(:_fork) do
      pid = super()
      if pid.zero?
        StackProf.stop
        StackProf.results
        StackProf.start(mode: mode, raw: true, interval: interval)
      end
      pid
    end
  end
  Process.singleton_class.prepend(child_reset)

  at_exit do
    StackProf.stop
    result = StackProf.results
    next unless result
    frames = result[:frames]
    raw = result[:raw] || []
    lines = result[:raw_lines]
    path = File.join(dir, "ruby.#{Process.pid}.folded")
    # stackprof's raw array repeats a stack for every run of samples;
    # adding them up keeps the file to the distinct stacks.
    counts = Hash.new(0)
    File.open("#{path}.tmp", "w") do |out|
      i = 0
      while i < raw.size
        depth = raw[i]
        names = Array.new(depth) do |k|
          frame = frames[raw[i + 1 + k]]
          line = lines && lines[i + 1 + k]
          line = frame[:line] if line.nil? || line.zero?
          name = frame[:name].to_s.tr(";\n", ":.")
          file = frame[:file].to_s
          if file.empty?
            name
          elsif line
            "#{name} (#{file}:#{line})"
          else
            "#{name} (#{file})"
          end
        end
        counts[names.join(";")] += raw[i + 1 + depth]
        i += depth + 2
      end
      counts.each { |stack, count| out.puts "#{stack} #{count}" }
    end
    File.rename("#{path}.tmp", path)
  end
end
