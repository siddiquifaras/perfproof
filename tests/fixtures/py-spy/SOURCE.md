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
