# pyright: basic, reportMissingImports=false
"""Shared timing for the GPU trace workloads.

Every workload allocates its tensors, runs untimed warm-up launches
(this is where cuBLAS, cuDNN and the kernel's first-use cost land),
then times a fixed number of launches between two CUDA events and
prints ``PERFPROOF {...}``. The value is seconds of GPU time for the
whole fixed batch of launches, so the reported work does not depend on
how fast the host starts the process. Process start-up (importing
torch, creating the CUDA context) is outside the value and inside the
wall time, as it is in a real benchmark round.
"""

import json
import sys

import torch

SEED = 1337


def setup() -> None:
    """Fail loudly without a CUDA device; seed the generators."""
    if not torch.cuda.is_available():
        sys.stderr.write("no CUDA device visible to torch\n")
        raise SystemExit(1)
    torch.manual_seed(SEED)


def time_batch(launch, iters: int, warmup: int) -> float:
    """Return GPU seconds for ``iters`` calls of ``launch()``.

    ``launch`` is called ``warmup`` times first, untimed. Events are
    recorded on the current stream and the end event is waited on, so
    the value is device time and not host launch time.
    """
    for _ in range(warmup):
        launch()
    torch.cuda.synchronize()
    start = torch.cuda.Event(enable_timing=True)
    end = torch.cuda.Event(enable_timing=True)
    start.record()
    for _ in range(iters):
        launch()
    end.record()
    end.synchronize()
    return start.elapsed_time(end) / 1000.0


def report(seconds: float, iters: int) -> None:
    """Print the PERFPROOF line with the allocator's peak GPU memory."""
    line = {
        "value": seconds,
        "iters": iters,
        "peak_gpu_mem_bytes": int(torch.cuda.max_memory_allocated()),
    }
    sys.stdout.write("PERFPROOF " + json.dumps(line) + "\n")
