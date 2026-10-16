# py-spy fixtures

Folded stacks written by `py-spy record --format raw`, untouched.

| File | Command |
|---|---|
| `workload.txt` | `py-spy record --format raw --rate 100 -o out/pyspy-raw.txt -- python run.py 5` |
| `workload-native.txt` | `py-spy record --format raw --rate 100 --native --full-filenames -o out/pyspy-native.txt -- python run.py 5` |
| `workload-native-long.txt` | `py-spy record --format raw --rate 1000 --native --full-filenames -o out/pyspy-large.txt -- python run.py 120` |

- Date: 2026-10-16, captured 03:36 to 03:38 +0500.
- Tool: py-spy 0.4.2 (`pip install py-spy==0.4.2`).
- Python 3.12.15, numpy 2.5.4.
- OS: Linux 6.12.54-linuxkit aarch64, in Docker Desktop 29.1.3 on an
  M4 MacBook Pro (macOS 15.7.7). Image
  `python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1`
  (`python:3.12-slim`), run with
  `docker run --rm --cap-add SYS_PTRACE -v <workload>:/srv/project python:3.12-slim sh /srv/project/capture.sh`.
- The workload was mounted at `/srv/project`, so that is the project
  root for these files.

Why Linux in Docker: on macOS itself py-spy 0.4.2 refuses to run
without root, even for a child it launches:

```
This program requires root on OSX.
Try running again with elevated permissions by going 'sudo !!'
```

Notes from the run, as py-spy printed them:

- The 120 s run printed many
  `py-spy> 4.75s behind in sampling, results may be inaccurate`
  warnings, then `Wrote raw flamegraph data to 'out/pyspy-large.txt'.
  Samples: 115023 Errors: 53`, then
  `Error: No child process (os error 10)`. The counts in the file sum
  to 115023. Only the tail of the container's output was kept, so the
  two 5 s runs' summaries were not recorded; their counts sum to 514
  (`workload.txt`) and 512 (`workload-native.txt`).
- With `--native`, many native frames are unsymbolised and appear as
  `0x13ffffa5a723e4 (?)`.
- Without `--full-filenames`, py-spy shortens paths to their `sys.path`
  entry (`app/kernels.py`, `json/encoder.py`, `numpy/__init__.py`).
- `workload-native-long.txt` has a line ` 8`: an empty stack, from
  samples where py-spy read no frames.

## The workload

`run.py`:

```python
"""Run every kernel in a loop for the given number of seconds."""

import sys
import time

from app import kernels


def main(seconds: float) -> None:
    """Call each kernel repeatedly until ``seconds`` have passed."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        kernels.fib(20)
        kernels.slow_sum(range(50_000))
        kernels.membership(list(range(2_000)), range(0, 4_000, 3))
        kernels.encode([{"i": i, "s": str(i)} for i in range(5_000)])
        kernels.vector_work(200)


if __name__ == "__main__":
    main(float(sys.argv[1]))
```

`app/__init__.py` holds only a docstring. `app/kernels.py`:

```python
"""Small CPU kernels for capturing real profiler fixtures."""

import json

import numpy as np


def fib(n: int) -> int:
    """Return the n-th Fibonacci number, recursively."""
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)


def slow_sum(values: range) -> int:
    """Return the sum of squares with a pure-Python loop."""
    total = 0
    for v in values:
        total += v * v
    return total


def membership(items: list[int], probes: range) -> int:
    """Count probes found in a list, quadratically."""
    return sum(1 for p in probes if p in items)


def encode(rows: list[dict[str, object]]) -> str:
    """Serialise rows to JSON."""
    return json.dumps(rows)


def vector_work(n: int) -> float:
    """Multiply a random square matrix by itself."""
    a = np.random.default_rng(0).random((n, n))
    return float((a @ a).sum())
```

`capture.sh`, run inside the container:

```sh
#!/bin/sh
set -eu
cd /srv/project
pip install --quiet --root-user-action=ignore py-spy==0.4.2 numpy
mkdir -p out
python --version > out/versions.txt
py-spy --version >> out/versions.txt
python -c "import numpy; print('numpy', numpy.__version__)" >> out/versions.txt
uname -srm >> out/versions.txt
py-spy record --format raw --rate 100 -o out/pyspy-raw.txt \
    -- python run.py 5
py-spy record --format raw --rate 100 --native --full-filenames \
    -o out/pyspy-native.txt -- python run.py 5
py-spy record --format raw --rate 1000 --native --full-filenames \
    -o out/pyspy-large.txt -- python run.py 120
```

SHA-256 of the files as written by py-spy:

```
1ab29bf311f51b7ea9dc867b7f9ae2a8ce9465e5345821af7926808967e8353f  workload.txt
4baef3ecd0ef55d3c67b872563f98c5dd5c87e585b0a0482b2f5a7b7080ad5be  workload-native.txt
02d73f84747db21627e3f2d682d7c6630df202e05a58a38bf4330831d415e9d9  workload-native-long.txt
```

## The examples, through `perfproof profile`

`rolling.txt` and `membership.txt` are the `py-spy.txt` files that
`perfproof profile --profiler py-spy` wrote for `examples/rolling` and
`examples/membership`, untouched. Each example was copied without its
`candidates/` into its own git repository at `/srv/rolling` and
`/srv/membership` (the project roots for these files), its
`spec.json` defined with `perfproof bench define`, and then profiled
with `perfproof profile rolling-sum --profiler py-spy --repo
/srv/rolling --top 5 --json` (and `held-orders`, `/srv/membership`).
perfproof launched py-spy as:

```
/usr/local/bin/py-spy record --format raw --rate 100 --full-filenames \
    --subprocesses -o <run directory>/py-spy.txt -- \
    /w/.venv/bin/python bench.py --output output/sums.npy
```

(`output/held.txt` for `membership`), in the example's directory.

- Date: 2026-10-16, finished 13:04:29 UTC.
- Tool: py-spy 0.4.2 (`pip install py-spy==0.4.2`); perfproof at the
  T2.10 branch's working tree, run with uv 0.12.20.
- Python 3.12.15 (perfproof's `uv sync --frozen` virtualenv at
  `/w/.venv`), numpy 2.5.4.
- OS: Linux 6.12.54-linuxkit x86_64, the `linux/amd64` variant of
  `python:3.12-bookworm` (index digest
  `sha256:5560e9ab8709f459489e5b8aa696eda8a07ef821e14bb122be62d91234bfa98b`),
  emulated by Docker Desktop 29.1.3 on an M4 MacBook Pro, run with
  `docker run --rm -i --platform linux/amd64 --cap-add SYS_PTRACE`.
  The script is `scratch/pyspy_capture.sh` on the T2.10 branch's
  machine, not committed.
- The counts sum to 88 (`rolling.txt`) and 117 (`membership.txt`)
  samples. py-spy printed nothing to stderr when it was not a
  terminal.
- With `--subprocesses`, py-spy puts a frame
  `process <pid>:"<command line>"` at the root of every stack; a
  sample in which it read no Python frame is a stack of that frame
  alone.

SHA-256 of the files as written by py-spy:

```
1e5505fc81432621d8e61f4d535c79e3cd386b47fc87530235d96737e576bf80  rolling.txt
770ed512d729f83effa94f45fe6913fbc6268ecd2d188ed197926093d048722b  membership.txt
```
