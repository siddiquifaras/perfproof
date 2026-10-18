# Captured tool output

Each file is output a real tool wrote, captured on 17 October 2026.
Paths under this checkout are rewritten relative to the repository.

| File | Tool and version | How it was made |
|---|---|---|
| `cargo-test-failing.txt` | cargo 1.94.1 (29ea6fb6a 2026-03-24), macOS arm64 | `cargo test --no-fail-fast` on a three-test crate: one passing, one failing `assert_eq!`, one `#[ignore]` |
| `perf-stat-not-supported.csv` | perf 6.1.187 (Debian bookworm `linux-perf`), in `python:3.12-bookworm` under Docker Desktop on this Mac, with `--cap-add PERFMON` | `perf stat -x, -e instructions:u -o <file> -- python3 -c pass`: the VM has no hardware counters, so the count is `<not supported>` |

Without `--cap-add PERFMON` the same command exits 255 with "No
permission to enable instructions:u event." and writes only the
`# started on` line.
