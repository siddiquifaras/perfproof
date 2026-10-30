# Writing the benchmark spec

perfproof measures one command, the benchmark, and checks one test
command. You write both into a JSON spec and lock it with
`perfproof bench define <name> --spec <file> --json`. Locking records the
hashes of the files listed in `harness`, `oracle`, `inputs` and
`config`, so a later change that edits them is refused. Read `ok` in
the full `--json` output and never filter it: defining the same spec
again after a locked file changed is refused (`"ok": false`,
`spec_conflict`). To change a locked file, define a new benchmark under
a new name and compare with that.

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

## Where the benchmark lives

The baseline is a fresh checkout of the commit, so a file you wrote and
did not commit is not in it. Put a benchmark you wrote under
`.perfproof/harness/` (for example `.perfproof/harness/bench_x.py`) and
list it in `harness`. perfproof copies that directory, byte for byte,
into both versions before anything runs, and a change cannot touch it.
Once one file there is listed, every file in the directory is locked,
so put helper modules beside the benchmark and expect an edit to any of
them after locking to be refused. Only `.git` and `__pycache__` are
ignored: any other file that appears or changes there after the freeze
(a cache the benchmark writes beside itself, a `.DS_Store`) fails the
lock gate, so tell the benchmark to write caches outside that directory.
That includes a candidate directory the user hands you: its own
`.perfproof/harness/` is replaced, so keep nothing of theirs there.
Run it from the repository root with `cwd` `"."`.

Do not point the command at `.venv/bin/python` or another ignored path
without `setup`: the baseline checkout has no such directory. Add
`"setup": {"commands": [["uv", "sync", "--frozen"]], "timeout_seconds":
600}`, which builds each version's own environment, and run the
benchmark with `uv run --frozen python ...` (plain `uv run` may
re-resolve) or `.venv/bin/python ...`, which then
exists in both. Never use the main checkout's environment: an editable
install there would import its source in both versions.

## Choosing the checks

- `correctness.command`: the project's tests for that code, or the whole
  suite if it is fast. A failing test on the new code rejects the change.
- `correctness.outputs`: files the benchmark writes that must be the
  same in both versions. Use `numbers` for numeric output (with a
  tolerance only if the user says results may differ slightly), `text`
  or `bytes` otherwise. Make the benchmark write its result to a file
  if it doesn't.
- `correctness.coverage_command` (optional, with
  `correctness.coverage_timeout_seconds`): only for a change in a
  language other than Python, and only when the project has a coverage
  tool. A command that runs the tests under it and writes lcov,
  Cobertura or JaCoCo XML to the path in `$PERFPROOF_COVERAGE_REPORT`
  (Node: `node.md`, Coverage; Java: `java.md`, Coverage). Python needs
  none. Leave it out when unsure: changes in other languages are then
  measured but not kept, with "no coverage command declared".

## Template

```json
{
  "schema_version": 4,
  "name": "<name>",
  "target_class": "<the project's target class>",
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
  "budget": {"max_rounds": "<the class's rounds>", "max_seconds": 1800},
  "looks": "<the class's own schedule, from the list below>",
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

- `command`: run a Python script, or a program built in the
  repository. A shell in between (`sh -c`, a `.sh` script) or an
  installed program outside the repository can't be identified, and
  the runtime check refuses it.
- `metric.kind`: `wall` (whole command), `cpu`, `reported` (the
  `PERFPROOF` line), `counter` (Linux only). `wall` and `cpu` are in
  seconds and lower is better.
- `guardrails`: list all three as above. Mark one `applicable: false`
  with a short true reason when it does not apply.
- `setup`: `{"commands": [[...], ...], "cwd": ".", "timeout_seconds": N}`.
  Argv commands run once in each version before the first run, outside
  the timing, with the same environment as the benchmark. It must not
  need credentials or a remote service.
- `reset`: if the benchmark changes something outside its own process
  (a local database, a cache directory), add
  `"reset": {"command": [...], "timeout_seconds": 60}` to put it back
  before every run.
- `target_class` and `looks`: set `target_class` from the `target_class`
  of the `init` target the workload belongs to, and `looks` and
  `budget.max_rounds` to that class's own schedule (`n0`, `k`,
  `max_looks`, rounds): `python_cpu`, `native`, `node` and `jvm`
  10, 10, 6, 60;
  `web_service` and `agentic` 10, 10, 3, 30; `llm_serving` 10, 6, 3, 22;
  `gpu_kernel` 20, 20, 6, 120. Only with that class and its own
  schedule can a KEEP be confirmed. `target_class` is one of the
  `init` project model's target entries, never anything else, and
  perfproof checks it against that model. If exactly one target fits
  the workload, use it. If none or several fit, ask the user once,
  naming the candidates. If they don't know, leave the class out: the
  run still measures and KEEP is withheld with `target_class_unknown`.
  Depth changes how many ideas you try, not these. `n0` and `k` must be even.
- `min_effect`: the bar. A change is kept only if even the low end of
  its range beats `1 + min_effect` (3% by default). Change it only if
  the user states a different bar.
- `harness`, `oracle`, `inputs`, `config`: the benchmark files, test
  files, input data and config files to lock. Paths relative to the
  repository. Those under `.perfproof/harness/` are installed in both
  versions by perfproof. Never lock the code you are trying to speed
  up, or any file the change you are proving touches: a change to a
  locked file is refused, so the change could never be measured. A
  benchmark the change adds or edits goes into `.perfproof/harness/` as
  a copy instead (see SKILL.md, prove step 3).

If `bench define` refuses the spec, its JSON `error.message` says why.
Fix that field; don't loosen a check to get past it.
