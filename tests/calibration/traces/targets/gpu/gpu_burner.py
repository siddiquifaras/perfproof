# pyright: basic, reportMissingImports=false
"""Background GPU load for the loaded-host trace.

Runs fp16 matmuls back to back on the same GPU as the benchmark until
SIGTERM, so the benchmark's kernels time-slice with another process's
(the default when no MPS daemon is running). It is a second tenant, the
GPU analogue of the CPU spinners in ``burner.py``.
"""

import signal
import sys

import torch

N = 4096


def main() -> None:
    running = [True]

    def stop(_signo: int, _frame: object) -> None:
        running[0] = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    a = torch.randn(N, N, device="cuda", dtype=torch.float16)
    b = torch.randn(N, N, device="cuda", dtype=torch.float16)
    out = torch.empty(N, N, device="cuda", dtype=torch.float16)
    while running[0]:
        for _ in range(20):
            torch.mm(a, b, out=out)
        torch.cuda.synchronize()
    sys.stdout.write("gpu_burner stopped\n")


if __name__ == "__main__":
    main()
