# profilers

One summariser for every profiler. `perfproof profile` runs a profiler
on a workload and turns its folded stacks into a short, ranked list of
`perfproof.schema.Hotspot`, so the agent sees `file:line` and a share
of time, not a raw dump. There is no adapter per profiler: a profiler
plugs in as a command that emits folded stacks, and the playbooks say
how to get them from each tool.

## Rules

- Launch the profiler with an argv list and a timeout; write its raw
  output to the run directory untouched.
- Stream the folded stacks: they can be gigabytes. Aggregate self and
  total time per frame as you read, and keep a bounded heap of the top
  N.
- Normalise frames: absolute paths under the project root become
  relative, library frames are marked as such, and Python and native
  frames are not merged by name alone.
- Report a missing tool with the reason (missing binary, no permission
  for `perf_event_open`, no GPU), never an empty profile.

## Fixtures

Tests use folded stacks captured from a real run of the profiler on one
of the `examples/` programs. Store them under
`tests/fixtures/<profiler>/` with a `SOURCE.md` naming the tool version,
OS, command, and date. Never edit a fixture by hand. A test that passes
on a hand-written fixture proves nothing about the real format.

Each built-in profiler also has an `integration` test that runs the
real tool end to end. It skips with a stated reason when the tool is
missing.
