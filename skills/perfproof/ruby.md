# Ruby

Read this when the code to speed up is Ruby (a gem, a Rails app, a
script). The method is the same as everywhere else; what differs is how
to launch the benchmark so the runtime is identified, how YJIT and the
collector enter the noise, how to profile with stackprof, and what
usually goes wrong in Ruby code.

## Launch it so the runtime is identified

`compare` only keeps a change when it knows which Ruby runs each
version. It asks the `ruby` it finds for the real path of its own
executable and for `RUBY_DESCRIPTION` (the line `ruby -v` prints),
then hashes that executable; the version it records is the line and the
hash. A version manager's shim (rbenv, asdf, mise) is a script named
`ruby`: the probe reports the Ruby it starts, not the shim.

- Launch `ruby script.rb`, a binstub (`bin/rake`, `bin/rspec`: a script
  whose `#!` line names `ruby` or `env ruby` is followed to it) or
  `bundle exec <command>`; the command after `exec` is identified as if
  it were launched directly.
- These are unknown, so `runtime` is `not_available` and a KEEP is
  withheld: `rbenv exec`, `rvm`, `asdf exec`, `mise exec`,
  `chruby-exec`, `bundle` with any subcommand but `exec`, `bundle exec`
  with an option before the command, and a `ruby` that runs inside a
  container (`docker run ... ruby`): the program launched is Docker,
  not Ruby. Use a Ruby on the PATH of the machine.
- `bundle install`, `bundle update` and `gem install` download code, so
  the gate calls them fetching launches. Run them in `setup`, not as
  the benchmark.
- Give both versions the same flags: `--yjit` (or `--disable-yjit`) is
  in the command, so it is in what both arms launch; `RUBYOPT`,
  `RUBY_YJIT_ENABLE` and the `RUBY_GC_*` variables are recorded as
  environment, and a difference is a warning. `RUBYOPT` and `RUBYLIB`
  are not passed to the identifying probe, so a `-r` in it cannot
  change the answer.
- `bundle exec` adds Bundler's start-up to every execution. Time work
  inside the process (below) and not the command, unless the real use
  is a short command.

## Warm-up: YJIT, and what it does to the first iterations

Every execution is a new process, so the spec's `warmup` executions do
not warm the next one; they are a smoke test. With YJIT on, a method is
compiled after it has been called enough times (`--yjit-call-threshold`),
and the first iterations of a loop are slower. In the committed
curve (`studies/ruby-warmup/`, Ruby 4.0.7, 8 fresh processes with YJIT,
5.5 ms an iteration at the steady state) the first iteration took 3.1
times the steady one at the median and the first 30 iterations 1.39
times; the next 30 were at 1.00, and later blocks stayed within 2% at
the median. With YJIT off the first block was 1.14. That is one small
workload (hash updates, string building, integer arithmetic) on a Mac
that other work was using; a large Rails code path compiles more code,
so measure your own.

So **iterate in process, with a fixed warm-up, and never fail on it**:

```ruby
WARMUP = 300 # about 10 times what the curve needed
TIMED = 30

def work; end # one iteration of the real work, at least a few ms

WARMUP.times { work }
clock = Process::CLOCK_MONOTONIC
times = Array.new(TIMED) do
  started = Process.clock_gettime(clock)
  work
  Process.clock_gettime(clock) - started
end
puts "PERFPROOF {\"value\": #{times.sort[TIMED / 2]}}"
```

Use it as a `reported` metric, `lower_is_better`, unit `s` (divide by
the operations in one iteration for a per-operation value). The counts
are fixed, so a workload that never settles (a cache that keeps
growing) still ends, and its spread is what the interval widens by;
exit non-zero only for wrong output. An execution costs `(300 + 30) x
seconds per iteration`: at 50 ms an iteration that is 16 s, so say the
expected time first and lower `WARMUP` only if the curve of your own
workload shows it flat sooner. If the real use is a short-lived command
(a CLI, a test run), the warm-up is part of what the user feels: time
the whole command and do not warm in process.

The collector is part of the noise. `GC.stat(:count)` before and after
the timed loop counts collections; `GC.stat(:total_allocated_objects)`
counts allocations. A version that allocates less is faster partly
because the collector runs less, which is the point: report both counts
next to the time.

## Profile

`perfproof profile` runs any command that writes folded stacks.
`perfproof.profilers.ruby` runs the command with the stackprof gem
switched on in every Ruby it starts (`RUBYOPT=-rruby_hook`, with the
hook's directory in `RUBYLIB`), and writes folded stacks with function, file and line:

```
perfproof profile <name> --json --profiler-command '["<python>", "-m",
  "perfproof.profilers.ruby", "record", "--output", "{output}",
  "--", "{command}"]'
```

`<python>` is the Python that perfproof itself runs in, beside the
`perfproof` executable. The gem must be installed for the Ruby that
runs (`gem install stackprof` into the Ruby's own gem path: the hook
loads before Bundler sets the load path, so a gem only in a vendored or
`BUNDLE_PATH` bundle is not found); without it the wrapper says so and
exits 4. The sampling clock is CPU time every millisecond; `"--mode", "wall"` samples wall time, which is
the one to use when the process mostly waits. Each Ruby process writes
its own file in `<output>.stackprof/`, and the wrapper concatenates
them.

- `function` is the method as Ruby names it (`Work.join_slow`,
  `Integer#times`, `block in Foo#bar`); `file` and `line` are where the
  sample was in that frame, relative to the project root for the
  project's own files. A C function shows as `String#+ (<cfunc>)`, the
  collector as `(garbage collection)` with `(marking)` and `(sweeping)`
  below it; all count as library. `frame_kind` is `python` for frames
  of a pseudo-file (`<cfunc>`, `<internal:...>`) and `native` for the
  rest; ignore it.
- Gems are the project's when they sit under the project root
  (`vendor/bundle`): a gem's frames are marked library only when their
  path is outside the root, so read the file.
- A profile of a second holds about a thousand samples. A frame with a
  small share is a few samples; run longer, not harder.
- In a container, the hook and a profile directory must be visible at
  the same paths inside, and the variables passed through: `docker run
  -e RUBYOPT -e RUBYLIB -e PERFPROOF_RUBY_PROFILE_DIR
  -e PERFPROOF_RUBY_INTERVAL_US -e PERFPROOF_RUBY_MODE -v <hook dir>:<hook dir>:ro` and the profile
  directory (`<output>.stackprof`) mounted the same way. `python -m
  perfproof.profilers.ruby hook` prints the hook's path. The frames name
  the container's paths for the project; the profile shows the
  project's files as library unless the project is mounted at the same
  path it has on the host.

## What usually goes wrong, and the cheap test for each

Run the test on the profile's top frames before writing a change; each
is a count or a share, never a timing to report.

| Suspect | What the profile shows | Cheap test |
|---|---|---|
| Allocation per call | `(garbage collection)` high; `Class#new`, `String#+`, `Array#map` frames | `GC.stat(:total_allocated_objects)` before and after one iteration: objects per operation; a value built per item that it did not need to be |
| String building in a loop | `String#+`, `String#%`, `Kernel#sprintf` | the same text built into one `<<` buffer or `Array#join` gives the same text with fewer allocations; check the allocation count |
| Quadratic collection work | `Array#include?`, `Array#-`, `Array#index`, `Enumerable#find` inside a loop | the loop's count against the collection's size; a `Set` or `Hash` built once for the lookup |
| Repeated lookups and parsing | `Regexp#match`, `String#scan`, `Time.parse`, `Date.parse`, `JSON.parse` frames from the same call site | the count of calls against distinct inputs; a regexp literal in a loop that is rebuilt (`Regexp.new`, interpolation) against one made once |
| Late-bound calls | `method_missing`, `Object#send`, `Module#define_method`, `respond_to?` frames | the count of calls per item; whether the method set is fixed (then define it once) |
| Callbacks and blocks | `Proc#call`, `Symbol#to_proc`, `block in` frames at the top | the block's calls per item as a count; what the block closes over |
| ActiveRecord and queries | time outside the process (wall above CPU), `ActiveRecord::Base.connection` frames | count queries with `ActiveSupport::Notifications.subscribe("sql.active_record")` over one operation: one query per row is N+1 |
| Serialisation | `JSON.generate`, `to_json`, `Marshal.dump`, `YAML` frames | bytes and objects per call; whether the same value is serialised again unchanged |
| Collector pressure | `(garbage collection)` and `(marking)`/`(sweeping)` large | `GC.stat(:count)` and `GC.stat(:heap_live_slots)`; `GC.disable` for one run bounds what the collector costs, and is a test, not a change to ship |
| Boot and `require` | a short command whose time is mostly `Kernel#require` | `ruby -e 1` against the command, and `ruby -rfoo -e 1` for one library at a time. The fix is fewer or lazier requires |
| Memoisation that never hits | a method re-running with the same arguments | a counter in a throwaway copy: calls against distinct arguments |

Two checks apply to every item: the change must keep the benchmark's
output the same, and a statement about YJIT ("this is compiled", "this
side-exits") is believed only with YJIT's own output
(`--yjit-stats` and `RubyVM::YJIT.runtime_stats`) from the Ruby that ran
both versions.

## Coverage

SimpleCov writes `coverage/.resultset.json` when the project's tests
run with it (`require "simplecov"` and `SimpleCov.start` before the
code under test loads, which a `.simplecov` file or a test helper
does). `python -m perfproof.bench.ruby_coverage
coverage/.resultset.json --root <project root> --output lcov.info`
turns it into lcov, one record per file with the lines that hold code
and how often they ran. `--root` is the path SimpleCov saw (inside a
container, the container's), so file names come out relative to it. A result with more than
one suite adds their counts together.

The coverage check takes a command that writes lcov or Cobertura to the
path in `PERFPROOF_COVERAGE_REPORT` (`correctness.coverage_command`);
declare one that runs the tests under SimpleCov and then this
converter. Never time a run with SimpleCov on.

## When to stop

As in [discovery.md](discovery.md): when the project's share of the
profile is small and the top frames are Ruby's own, the cap says what is
left. A benchmark whose rounds disagree widely is noisy, not slow; say
so and report the range.
