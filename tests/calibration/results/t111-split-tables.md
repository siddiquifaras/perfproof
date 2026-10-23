### Acceptance, synthetic grids

| Seed | Grid | Cells | Failing | Worst false KEEP | Worst false REJECT |
|---|---|---|---|---|---|
| record (3598) | main/development | 560 | 0 | 47 gpu_kernels/lognormal_cv5/cross_host/1.03 | 23 gpu_kernels/bimodal/cross_host/1.04 |
| record (3598) | main/exposed | 784 | 0 | 67 llm_serving/A20261052-04/one_host/1.03 | 52 llm_serving/A20261052-04/one_host/1.04 |
| record (3598) | heldout/heldout | 1680 | 0 | 44 gpu_kernels/H3635800-28/cross_host/1.03 | 25 gpu_kernels/H3635800-03/one_host/1.04 |
| record (3598) | cond/probe | 252 | 0 | 32 python_native/COND-armbias-base-poorcov/one_host/1.03 | 31 python_native/COND-armbias-cand/one_host/1.05 |
| record (3598) | skew/probe | 168 | 11 | 293 python_native/SKEW-cand-5pct-x2/one_host/1.03 | 125 python_native/SKEW-base-5pct-x2/one_host/1.04 |
| fresh (2483) | main/development | 560 | 0 | 48 gpu_kernels/bimodal/cross_host/1.03 | 22 gpu_kernels/lognormal_cv15/cross_host/1.04 |
| fresh (2483) | main/exposed | 784 | 0 | 60 llm_serving/RHO0.7/one_host/1.03 | 50 web_agentic/A20261052-04/cross_host/1.04 |
| fresh (2483) | heldout/heldout | 1680 | 0 | 48 gpu_kernels/H3635800-15/one_host/1.03 | 28 gpu_kernels/H3635800-01/cross_host/1.04 |
| fresh (2483) | cond/probe | 252 | 0 | 46 python_native/COND-armbias-base-poorcov/one_host/1.03 | 27 python_native/COND-armbias-cand/one_host/1.04 |
| fresh (2483) | skew/probe | 168 | 11 | 281 python_native/SKEW-cand-5pct-x2/one_host/1.03 | 127 python_native/SKEW-base-5pct-x2/one_host/1.04 |

### Failing skew-probe cells by schedule

| Seed | Schedule | Primary | Split |
|---|---|---|---|
| record (3598) | python_native | 2 | 3 |
| record (3598) | web_agentic | 2 | 3 |
| record (3598) | llm_serving | 0 | 2 |
| record (3598) | gpu_kernels | 3 | 3 |
| fresh (2483) | python_native | 2 | 3 |
| fresh (2483) | web_agentic | 2 | 3 |
| fresh (2483) | llm_serving | 0 | 2 |
| fresh (2483) | gpu_kernels | 3 | 3 |

### Acceptance, real traces

| Procedure | Rule | Cells (1.00, 1.03) | Failing | Worst false KEEPs |
|---|---|---|---|---|
| split | pw | 152 | 0 | 45 linux-node/pw/gpu_kernels/1.03 |
| batch_means_6 | pw | 152 | 0 | 45 linux-node/pw/gpu_kernels/1.03 |
| hac_lf | pw | 152 | 0 | 34 mac-loaded-rolling/pw/gpu_kernels/1.03 |
| split | fixed6 | 152 | 0 | 50 mac-loaded-node/fixed6/gpu_kernels/1.03 |
| batch_means_6 | fixed6 | 152 | 0 | 50 mac-loaded-node/fixed6/gpu_kernels/1.03 |
| hac_lf | fixed6 | 152 | 0 | 30 mac-loaded-node/fixed6/gpu_kernels/1.03 |
| split | fixed20 | 152 | 0 | 76 linux-picolm/fixed20/gpu_kernels/1.03 |
| batch_means_6 | fixed20 | 152 | 0 | 76 linux-picolm/fixed20/gpu_kernels/1.03 |
| hac_lf | fixed20 | 152 | 0 | 62 linux-java/fixed20/gpu_kernels/1.03 |

### Dominance against the gated primary
Each entry: the smallest difference of the split to the gated primary in points, then the cells worse than 3 standard errors of the cells at that speedup.

Synthetic, record seed 3598798392042499920 (development, exposed, held-out, power cells; both host modes)

| Schedule | KEEP 1.05 | KEEP 1.10 | KEEP 1.20 | REJECT 0.90 | REJECT 0.80 |
|---|---|---|---|---|---|
| python_native | -1.5, 11 of 108 | -1.6, 1 of 108 | +1.9, 0 of 108 | +5.5, 0 of 20 | +5.8, 0 of 20 |
| web_agentic | -1.2, 29 of 108 | -1.4, 4 of 108 | -0.5, 0 of 108 | +5.9, 0 of 20 | +20.3, 0 of 20 |
| llm_serving | -0.8, 21 of 108 | -1.0, 1 of 108 | -0.2, 0 of 108 | +3.7, 0 of 20 | +25.4, 0 of 20 |
| gpu_kernels | +0.0, 0 of 108 | +0.0, 0 of 108 | +0.0, 0 of 108 | +0.0, 0 of 20 | +0.0, 0 of 20 |

Synthetic, fresh seed 2483202639896524218 (development, exposed, held-out, power cells; both host modes)

| Schedule | KEEP 1.05 | KEEP 1.10 | KEEP 1.20 | REJECT 0.90 | REJECT 0.80 |
|---|---|---|---|---|---|
| python_native | -1.8, 6 of 108 | -1.5, 0 of 108 | +2.4, 0 of 108 | +5.6, 0 of 20 | +5.8, 0 of 20 |
| web_agentic | -1.0, 19 of 108 | -1.5, 3 of 108 | +0.6, 0 of 108 | +6.7, 0 of 20 | +20.5, 0 of 20 |
| llm_serving | -0.9, 11 of 108 | -0.8, 3 of 108 | +0.5, 0 of 108 | +3.8, 0 of 20 | +24.7, 0 of 20 |
| gpu_kernels | +0.0, 0 of 108 | +0.0, 0 of 108 | +0.0, 0 of 108 | +0.0, 0 of 20 | +0.0, 0 of 20 |

Real traces, block rule pw, seed 7257639517108691388

| Schedule | KEEP 1.05 | KEEP 1.10 | KEEP 1.20 | REJECT 0.90 | REJECT 0.80 |
|---|---|---|---|---|---|
| python_native | -0.8, 0 of 19 | +5.4, 0 of 19 | +4.8, 0 of 19 | +5.5, 0 of 19 | +5.2, 0 of 19 |
| web_agentic | -0.1, 0 of 19 | +24.3, 0 of 19 | +22.2, 0 of 19 | +21.7, 0 of 19 | +20.0, 0 of 19 |
| llm_serving | +0.8, 0 of 19 | +29.1, 0 of 19 | +30.6, 0 of 19 | +30.2, 0 of 19 | +29.4, 0 of 19 |
| gpu_kernels | +0.0, 0 of 19 | +0.0, 0 of 19 | +0.0, 0 of 19 | +0.0, 0 of 19 | +0.0, 0 of 19 |

Real traces, block rule fixed6, seed 7257639517108691388

| Schedule | KEEP 1.05 | KEEP 1.10 | KEEP 1.20 | REJECT 0.90 | REJECT 0.80 |
|---|---|---|---|---|---|
| python_native | -1.5, 0 of 19 | +4.6, 0 of 19 | +4.4, 0 of 19 | +4.1, 0 of 19 | +4.6, 0 of 19 |
| web_agentic | -1.1, 0 of 19 | +25.3, 0 of 19 | +19.8, 0 of 19 | +20.5, 0 of 19 | +17.8, 0 of 19 |
| llm_serving | -0.4, 0 of 19 | +26.4, 0 of 19 | +25.9, 0 of 19 | +25.1, 0 of 19 | +24.5, 0 of 19 |
| gpu_kernels | +0.0, 0 of 19 | +0.0, 0 of 19 | +0.0, 0 of 19 | +0.0, 0 of 19 | +0.0, 0 of 19 |

Real traces, block rule fixed20, seed 7257639517108691388

| Schedule | KEEP 1.05 | KEEP 1.10 | KEEP 1.20 | REJECT 0.90 | REJECT 0.80 |
|---|---|---|---|---|---|
| python_native | +0.1, 0 of 19 | +3.2, 0 of 19 | +2.6, 0 of 19 | +2.2, 0 of 19 | +2.8, 0 of 19 |
| web_agentic | -0.9, 0 of 19 | +19.1, 0 of 19 | +18.9, 0 of 19 | +18.2, 0 of 19 | +14.8, 0 of 19 |
| llm_serving | -0.1, 0 of 19 | +24.0, 0 of 19 | +20.4, 0 of 19 | +23.0, 0 of 19 | +21.3, 0 of 19 |
| gpu_kernels | +0.0, 0 of 19 | +0.0, 0 of 19 | +0.0, 0 of 19 | +0.0, 0 of 19 | +0.0, 0 of 19 |
