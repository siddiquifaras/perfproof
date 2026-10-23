# Rust task support

- `Dockerfile`: the official `rust:1.94.1` image plus Debian's
  `linux-perf`, git and Python (venv and pip), built locally as
  `perfproof-rust:1.94.1` (never pushed). It is the Linux box for the
  profiler wrapper: the fixtures `perf-script-*.txt` and
  `llvm-cov-export.lcov` in `tests/fixtures/rust/` came from it, and it
  runs `perf record` with `--cap-add SYS_ADMIN --cap-add PERFMON`.
- `make_tree.py`: the fixed 200,000-file tree the two new fd manifests
  search (`sharkdp-fd-pr1422`, `sharkdp-fd-pr1914-landed`); their setup
  runs it. The older `sharkdp-fd-pr853` has its own, smaller one in
  `../sharkdp-fd-pr853/`.
- `warmup_runs.py` and `warmup-runs.txt`: one release binary run 30
  times in each of five fresh copies of it; every wall time. The numbers
  in "Warm-up (Rust)" of `docs/method.md` are read from it.
- `warmup_iterations.rs` and `warmup-iterations.txt`: 200 iterations of
  the same work in one process; the other half of that section.
- `pathologies.rs`, `pathology_frames.py` and `pathologies-frames.txt`:
  one hot loop per slowness class of `skills/perfproof/rust.md`, run
  under `perfproof.profilers.rust` on macOS (`sample`) and on Linux
  (`perf`), and the five frames with the largest self share of each. The
  "what the profile shows" column of the playbook's table is checked
  against it: the allocator frames of the clone and format modes, the
  `hashbrown` frames of the hash mode, the lock frames of the mutex
  mode, the `write` calls of the unbuffered mode, and the whole loop in
  one function for `contains`, `grow` and `bounds`.
- `pathologies-bounds-disasm.txt`: the disassembly of the `bounds` mode's
  function, with the compare and the branch to `panic_bounds_check` the
  playbook names.

Reproduce the profiler runs (the Linux ones inside the box, with the
repository's `src` mounted and `PYTHONPATH` set):

```bash
rustc -C opt-level=3 -C force-frame-pointers=yes -o pathologies \
    eval/tasks/workloads/rust/pathologies.rs
uv run python eval/tasks/workloads/rust/pathology_frames.py \
    <work dir> "<label>" ./pathologies 1.5
```

The warm-up series were run once, on a Mac with other work running (the
load average at the start of `warmup-runs.txt` is in its header), so
their times are not clean measurements.

`eval/skill/tasks-rust.json` is in the real-repository task format. The
tasks need `cargo` on the `PATH`. `skill/realrepo.py` loads only tasks
whose manifest is split `development` and status `runs`, so the two new
tasks, split `development-candidate` until the draw's step-1 review, do
not load yet (see "Rust tasks" in `eval/README.md`).

In `tasks-rust.json` the find task's hotspot is `print_entry`, not the
`print_entry_colorized` that `tasks-real.json` expects: in the release
build the compiler inlined `print_entry_colorized` into `print_entry`,
and neither profile fixture in `tests/fixtures/rust/` has a frame of the
first name (24 lines of `sample-fd.txt` name the second).
