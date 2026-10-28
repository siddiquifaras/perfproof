"""CPU stand-in for the GPU workloads, for dry runs without a GPU.

Same contract as the GPU benchmarks: a fresh process times a fixed
batch of work and prints ``PERFPROOF {...}`` with a seconds value and a
memory figure. It does a NumPy matmul, so it is compute-bound on the
CPU. It proves the recorder, not any GPU behaviour.
"""

import json
import sys
import time

import numpy as np

N = 512
ITERS = 20


def main() -> None:
    rng = np.random.default_rng(1337)
    a = rng.standard_normal((N, N), dtype=np.float32)
    b = rng.standard_normal((N, N), dtype=np.float32)
    start = time.perf_counter()
    for _ in range(ITERS):
        np.matmul(a, b)
    seconds = time.perf_counter() - start
    line = {"value": seconds, "iters": ITERS, "peak_gpu_mem_bytes": 0}
    sys.stdout.write("PERFPROOF " + json.dumps(line) + "\n")


if __name__ == "__main__":
    main()
