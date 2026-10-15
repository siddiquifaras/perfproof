# macOS `sample` fixtures

Native stacks of a Python process, sampled by Apple's `/usr/bin/sample`
and folded by inferno's `inferno-collapse-sample`. Both files are
untouched tool output.

| File | Written by |
|---|---|
| `workload.sample.txt` | `/usr/bin/sample <pid> 3 1 -file sample-out.txt` |
| `workload.folded.txt` | `inferno-collapse-sample sample-out.txt > sample-folded.txt` |

- Date: 2026-10-16 03:31:48 +0500 (from the report header).
- OS: macOS 15.7.7 (24G720), arm64, M4 MacBook Pro. `sample` runs
  without root on a process of the same user.
- `sample`: the one shipped with macOS 15.7.7; it prints no version.
  It ran for 3 s at 1 ms intervals and its report gives 2503 samples
  for the main thread. The folded file's counts sum to 2353, 150 fewer;
  the cause was not investigated. The top-of-stack
  counts in `sample`'s own report for `list_contains` (921) and
  `long_richcompare` (879) equal the self counts in the folded file.
- inferno 0.12.8, built with
  `cargo install --locked --root scratch/cargo inferno` (cargo 1.94.1).
- Python 3.12.12 from the project's `uv` virtualenv, numpy 2.5.4.
- Target: the same workload as `tests/fixtures/py-spy/SOURCE.md`, run as
  `.venv/bin/python run.py 6` from the workload directory; `sample`
  attached about 1 s after it started.

`sample` replaces user paths with `/Users/USER/*` and `/Users/*` in its
own report. The folded file has no paths: native frames are
`module`symbol`, and the root frame is the thread's description, which
contains spaces.

SHA-256 of the files as written:

```
677a1eadbcaf9c1427091acf657318ef05453ac367d3c587395a71632bb08cee  workload.sample.txt
dd96ee4ecf67b04e81d2bf180360ddf3061b8eb40b11395a8306e8c2186c16be  workload.folded.txt
```
