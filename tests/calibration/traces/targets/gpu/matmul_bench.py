# pyright: basic, reportMissingImports=false
"""Compute-bound: fp16 matmul (tensor cores through cuBLAS).

8192 x 8192 x 8192, 30 launches (about 33 TFLOP). On an L4 the target
is about half a second of device time; the real figure is measured by
the health check, not assumed.
"""

import torch
from _gpu_common import report, setup, time_batch

N = 8192
ITERS = 30
WARMUP = 5


def main() -> None:
    setup()
    a = torch.randn(N, N, device="cuda", dtype=torch.float16)
    b = torch.randn(N, N, device="cuda", dtype=torch.float16)
    out = torch.empty(N, N, device="cuda", dtype=torch.float16)
    report(time_batch(lambda: torch.mm(a, b, out=out), ITERS, WARMUP), ITERS)


if __name__ == "__main__":
    main()
