# pyright: basic, reportMissingImports=false
"""Memory-bound: an elementwise add over 1 GiB read and 1 GiB written.

Each launch moves 2 GiB through device memory and does one add per
element, so it is limited by memory bandwidth, not arithmetic. 70
launches.
"""

import torch
from _gpu_common import report, setup, time_batch

ELEMENTS = 256 * 1024 * 1024
ITERS = 70
WARMUP = 5


def main() -> None:
    setup()
    x = torch.randn(ELEMENTS, device="cuda", dtype=torch.float32)
    y = torch.empty_like(x)
    report(time_batch(lambda: torch.add(x, 1.0, out=y), ITERS, WARMUP), ITERS)


if __name__ == "__main__":
    main()
