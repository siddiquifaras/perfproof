# profilers

One adapter per profiler. Each turns a real profiler run into a short,
ranked list of `perfproof.schema.Hotspot` so the agent sees
`file:line` and a share of time, not a raw dump.

## Adapter contract

- `available()` reports whether the tool is installed and usable here,
  with the version and the reason when it isn't (missing binary, no
  permission for `perf_event_open`, no GPU).
- `run()` launches the user's command under the profiler with an argv
  list and a timeout, and writes the raw output to the run directory
  untouched.
- `parse()` reads that raw output and returns the top N hotspots. It
  streams: profiler outputs can be gigabytes, so aggregate as you read
  and keep a bounded heap of the top N.
- Frames are normalized: absolute paths under the project root become
  relative, library frames are marked as such, and Python and native
  frames are not merged by name alone.

## Fixtures

Parser tests use output captured from a real run of the profiler on one
of the `examples/` programs. Store it under
`tests/fixtures/<profiler>/` with a `SOURCE.md` naming the tool version,
OS, command, and date. Never edit a fixture by hand. A parser test that
passes on a hand-written fixture proves nothing about the real format.

Each adapter also has an `integration` test that runs the real tool
end to end. It skips with a stated reason when the tool is missing.
