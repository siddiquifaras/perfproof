# Writing the benchmark spec

perfproof measures one command, the benchmark, and checks one test
command. You write both into a JSON spec and lock it with
`perfproof bench define <name> --spec <file> --json`. Locking records the
hashes of the files listed in `harness`, `oracle`, `inputs` and
`config`, so a later change that edits them is refused.

Keep the spec file in `.perfproof/specs/<name>.json`. Show the user the
command, the test command and what is compared, in one short paragraph,
before you lock it.

## Choosing the benchmark

- It runs the code the user cares about, the way it is really used, on
  realistic input. Not a microbenchmark of one line.
- One run is long enough to be far above the timer's step, and short
  enough that dozens of runs fit in the time the user will wait. Tell
  the user the expected total before you start.
- It is deterministic: fixed seeds, fixed input files, no network.
- If the code under test has its own timing, have the benchmark print
  one line `PERFPROOF {"value": <seconds>}` on stdout and use the
  `reported` metric. That times only the work, not interpreter start-up.
  Otherwise use `wall`.

## Choosing the checks

- `correctness.command`: the project's tests for that code, or the whole
  suite if it is fast. A failing test on the new code rejects the change.
- `correctness.outputs`: files the benchmark writes that must be the
  same in both versions. Use `numbers` for numeric output (with a
  tolerance only if the user says results may differ slightly), `text`
  or `bytes` otherwise. Make the benchmark write its result to a file
  if it doesn't.

## Template

```json
{
  "schema_version": 3,
  "name": "<name>",
  "command": ["python", "bench.py", "--output", "out/result.npy"],
  "cwd": ".",
  "env": {},
  "metric": {"kind": "reported", "direction": "lower_is_better", "unit": "s"},
  "objective": "wall_time",
  "guardrails": [
    {"metric": "peak_memory", "direction": "lower_is_better", "unit": "bytes",
     "allowance": 0.1, "allowance_source": "default"},
    {"metric": "peak_gpu_memory", "direction": "lower_is_better", "unit": "bytes",
     "applicable": false, "reason": "no GPU is used"},
    {"metric": "p99_latency", "direction": "lower_is_better", "unit": "s",
     "applicable": false, "reason": "serves no requests"}
  ],
  "correctness": {
    "command": ["python", "-m", "pytest", "-q", "tests/test_thing.py"],
    "cwd": ".",
    "timeout_seconds": 300,
    "outputs": [{"path": "out/result.npy", "compare": "numbers"}]
  },
  "warmup": 1,
  "budget": {"max_rounds": 60, "max_seconds": 1800},
  "looks": {"n0": 10, "k": 10, "max_looks": 6},
  "min_effect": 0.03,
  "alpha": 0.05,
  "timeout_seconds": 120,
  "harness": ["bench.py"],
  "oracle": ["tests/test_thing.py"],
  "inputs": [],
  "config": [],
  "allow_runtime_change": false,
  "allow_cross_host": false
}
```

## Fields worth knowing

- `metric.kind`: `wall` (whole command), `cpu`, `reported` (the
  `PERFPROOF` line), `counter` (Linux only). `wall` and `cpu` are in
  seconds and lower is better.
- `guardrails`: list all three as above. Mark one `applicable: false`
  with a short true reason when it does not apply.
- `reset`: if the benchmark changes something outside its own process
  (a local database, a cache directory), add
  `"reset": {"command": [...], "timeout_seconds": 60}` to put it back
  before every run.
- `budget` and `looks`: use the template's values (60 rounds, `n0` 10,
  `k` 10, `max_looks` 6), the provisional defaults the decision rule is
  calibrated under. Depth changes how many ideas you try, not these.
  `n0` and `k` must be even.
- `min_effect`: the bar. A change is kept only if even the low end of
  its range beats `1 + min_effect` (3% by default). Change it only if
  the user states a different bar.
- `harness`, `oracle`, `inputs`, `config`: the benchmark files, test
  files, input data and config files to lock. Paths relative to the
  repository. Never lock the code you are trying to speed up: a change
  to a locked file is refused, so the change could never be measured.

If `bench define` refuses the spec, its JSON `error.message` says why.
Fix that field; don't loosen a check to get past it.
