# pyright: basic, reportMissingImports=false
"""Mixed: causal scaled-dot-product attention, fp16, flash kernel.

Batch 8, 16 heads, sequence 2048, head size 64; 300 launches. The
fused kernel is neither purely compute- nor memory-bound.
"""

import torch
from _gpu_common import report, setup, time_batch
from torch.nn.functional import scaled_dot_product_attention

B, H, L, D = 8, 16, 2048, 64
ITERS = 300
WARMUP = 10


def main() -> None:
    setup()
    shape = (B, H, L, D)
    q = torch.randn(shape, device="cuda", dtype=torch.float16)
    k = torch.randn(shape, device="cuda", dtype=torch.float16)
    v = torch.randn(shape, device="cuda", dtype=torch.float16)

    def launch() -> object:
        return scaled_dot_product_attention(q, k, v, is_causal=True)

    report(time_batch(launch, ITERS, WARMUP), ITERS)


if __name__ == "__main__":
    main()
