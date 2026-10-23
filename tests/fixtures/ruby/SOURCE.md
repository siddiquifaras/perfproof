# Ruby fixtures

Output of the real tools, untouched, on the small program in `prog/`
(quadratic string building, a hash updated per item, a loop of integer
arithmetic, a method none of the tests reach). `capture.sh` makes every
file here, with `prog/` mounted at `/src`, so the profiles name
`/src/lib/work.rb`.

- Date: 2026-10-23.
- Ruby: `ruby 4.0.7 (2026-09-15 revision 229531a6cf) +PRISM
  [aarch64-linux]` (`ruby-v.txt`), the official `ruby:4.0` image
  (`ruby@sha256:d907e08a8558207d221f257afe1bfc3e274a12bac035d8bbc9049b01b5364140`)
  in Docker Desktop's VM, Linux 6.12.54-linuxkit aarch64, on a macOS
  15.7.7 M4 MacBook Pro.
- Gems, as installed by `gem install` at capture time (`gems.txt`):
  stackprof 0.2.28, simplecov 1.3.2, simplecov-lcov 0.9.0, minitest
  6.0.0.
- Command: `tests/fixtures/ruby/capture.sh <out-dir>`, then the files
  below copied from `<out-dir>`.

| File | What it is |
|---|---|
| `ruby-v.txt`, `yjit-enabled.txt`, `gems.txt` | `ruby -v`; `ruby --yjit -e "puts RubyVM::YJIT.enabled?"`; the gems named above |
| `hot.folded` | what `src/perfproof/profilers/ruby_hook.rb` wrote inside `ruby bin/hot 2` (2 s of the workload under stackprof, CPU time, 1 ms interval), as the wrapper's `RUBYOPT` sets it up |
| `resultset.json` | `coverage/.resultset.json` of SimpleCov 1.3.2 running `test/work_test.rb` (3 Minitest tests) |
| `simplecov.lcov` | the lcov report the simplecov-lcov gem wrote from the same run: what `tests/bench/test_ruby_coverage.py` compares the converter with, line by line |
| `coverage-run.txt` | the output of that run (Minitest's report, and SimpleCov's deprecation notice for `add_filter`) |

What the captures show:

- stackprof's `raw` array is `[depth, frame ids root first, count]`
  repeated, with `raw_lines` parallel to it holding the line each
  frame was on; the hook folds them with the sampled line of each frame.
  A C function's file is `<cfunc>`, an internal one `<internal:numeric>`,
  and the collector is `(garbage collection)`.
- SimpleCov 1.3.2 writes `{"lines": [...]}` per file (not the bare list
  of older versions) and absolute container paths; its own lcov writer
  gives paths as `./lib/work.rb`.
- A process that execs (`bundle exec`, a binstub) keeps the sampling
  timer across the exec, and without the hook stopping stackprof first
  the new program died with "Profiling timer expired" (status 155)
  before it could install its handler; seen on the Rails task tree
  (`studies/ruby/`), and `test_the_real_profile_survives_an_exec`
  covers it.

The Linux integration tests run in an image made by
`tests/fixtures/ruby/Dockerfile` (the official image, stackprof and
uv): `uv run pytest -m integration tests/profilers/test_ruby.py
tests/bench/test_lock_ruby.py`. On this Mac, whose system Ruby is
2.6.10 with no stackprof, `tests/bench/test_lock_ruby.py` runs against
that Ruby and the stackprof tests skip with their reason.
