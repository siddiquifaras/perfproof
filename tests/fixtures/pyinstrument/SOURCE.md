# pyinstrument fixtures

Sessions written by pyinstrument's `pyisession` renderer, untouched:
the files `perfproof profile` wrote, with its default profiler on
macOS, for `examples/rolling` (`rolling.pyisession`) and
`examples/membership` (`membership.pyisession`).

Each example was copied without its `candidates/` into its own git
repository, its `spec.json` defined with `perfproof bench define`,
and then profiled with `perfproof profile rolling-sum --repo <root>
--top 5 --json` (and `held-orders`). The project roots, which are
also the profiled processes' working directories, were:

```
/Users/faras/code/perfproof/.claude/worktrees/agent-a0cbb9b58dc2a1d26/scratch/fixture-repos/rolling
/Users/faras/code/perfproof/.claude/worktrees/agent-a0cbb9b58dc2a1d26/scratch/fixture-repos/membership
```

perfproof launched pyinstrument as:

```
/Users/faras/.local/bin/uv run --no-project --with pyinstrument==5.1.3 \
    --python <perfproof checkout>/.venv/bin/python -- \
    python -m pyinstrument --renderer pyisession \
    --outfile <run directory>/pyinstrument.pyisession \
    bench.py --output output/sums.npy
```

(`output/held.txt` for `membership`).

- Date: 2026-10-16, 13:02:31 and 13:02:33 UTC (the run directories'
  names).
- Tool: pyinstrument 5.1.3, installed by uv 0.12.20
  (2274b80d6 2026-09-28 aarch64-apple-darwin) into an ephemeral
  environment layered over the perfproof checkout's virtualenv.
- Python 3.12.12, numpy 2.5.4.
- OS: macOS 15.7.7 (24G720), arm64, M4 MacBook Pro, with other agents'
  work on the machine (one-minute load average 6.8).
- Each session holds absolute paths of this machine: the uv cache
  under `/Users/faras/.cache/uv`, the uv-managed interpreter, and the
  roots above. `bench.py`, run by pyinstrument through `runpy`, is
  recorded relative to the working directory; `rolling.py` and
  `orders.py`, imported, are absolute.
- pyinstrument samples at the first call or return event after each
  1 ms interval and gives the record all the time since the previous
  one (`setstatprofile` in pyinstrument 5.1.3), so there are 58
  (`rolling`) and 51 (`membership`) records for sessions of 0.459 s
  and 0.349 s, and a record's line is where that event happened. In
  `membership.pyisession`, `held_orders` spends its time on
  `orders.py:53` (the `in` test, which makes no call) and is recorded
  at line 52, where the comprehension starts.

SHA-256 of the files as written by pyinstrument:

```
778608b73c24aed8614a276b31ce206a4fac0cdbeaccdfb4fc7a76fda02b7f7b  rolling.pyisession
61c83853a05c1cb8191ac2db88a30267301faacec6cbd47cb89c28ffcb0075c8  membership.pyisession
```
