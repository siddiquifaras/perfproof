# Golden set: replay results

## Wrong-KEEP, worst cell per method and schedule

| Schedule | Method | Cells | Worst wrong-KEEP (cell) | Worst CP95 upper | Cells above 2.5%, as registered (all) | Cells above 2.5%, amended (cells of 200 only) |
|---|---|---|---|---|---|---|
| python_native | gated | 12 | 0/100 (python/1.02) | 0.0295 | 4 | 0 |
| python_native | hac_lf | 12 | 0/100 (python/1.02) | 0.0295 | 4 | 0 |
| python_native | split | 12 | 0/100 (python/1.02) | 0.0295 | 4 | 0 |
| web_agentic | gated | 12 | 2/200 (native/1.03) | 0.0311 | 7 | 3 |
| web_agentic | hac_lf | 12 | 0/100 (python/1.02) | 0.0295 | 4 | 0 |
| web_agentic | split | 12 | 0/100 (python/1.02) | 0.0295 | 4 | 0 |
| llm_serving | gated | 12 | 4/200 (java/1.03) | 0.0452 | 6 | 2 |
| llm_serving | hac_lf | 12 | 0/100 (python/1.02) | 0.0295 | 4 | 0 |
| llm_serving | split | 12 | 0/100 (python/1.02) | 0.0295 | 4 | 0 |
| gpu_kernels | gated | 6 | 0/100 (python/1.02) | 0.0295 | 2 | 0 |
| gpu_kernels | hac_lf | 6 | 0/100 (python/1.02) | 0.0295 | 2 | 0 |
| gpu_kernels | split | 6 | 0/100 (python/1.02) | 0.0295 | 2 | 0 |

## Power (KEEP rate) at 1.05 and 1.10

| Workload | Schedule | Speedup | gated | hac_lf | split |
|---|---|---|---|---|---|
| python | python_native | 1.05 | 87.0 | 96.0 | 96.0 |
| python | python_native | 1.10 | 94.0 | 100.0 | 100.0 |
| python | web_agentic | 1.05 | 14.0 | 76.0 | 76.0 |
| python | web_agentic | 1.10 | 14.0 | 100.0 | 100.0 |
| python | llm_serving | 1.05 | 14.0 | 66.0 | 66.0 |
| python | llm_serving | 1.10 | 12.0 | 100.0 | 100.0 |
| python | gpu_kernels | 1.05 | 100.0 | 100.0 | 100.0 |
| python | gpu_kernels | 1.10 | 100.0 | 100.0 | 100.0 |
| native | python_native | 1.05 | 93.0 | 99.0 | 99.0 |
| native | python_native | 1.10 | 89.0 | 100.0 | 100.0 |
| native | web_agentic | 1.05 | 10.0 | 96.0 | 96.0 |
| native | web_agentic | 1.10 | 1.0 | 100.0 | 100.0 |
| native | llm_serving | 1.05 | 14.0 | 88.0 | 88.0 |
| native | llm_serving | 1.10 | 0.0 | 100.0 | 100.0 |
| native | gpu_kernels | 1.05 | 100.0 | 100.0 | 100.0 |
| native | gpu_kernels | 1.10 | 100.0 | 100.0 | 100.0 |
| node | python_native | 1.05 | 85.0 | 100.0 | 100.0 |
| node | python_native | 1.10 | 82.0 | 100.0 | 100.0 |
| node | web_agentic | 1.05 | 76.0 | 98.0 | 98.0 |
| node | web_agentic | 1.10 | 59.0 | 100.0 | 100.0 |
| node | llm_serving | 1.05 | 36.0 | 92.0 | 92.0 |
| node | llm_serving | 1.10 | 48.0 | 100.0 | 100.0 |
| java | python_native | 1.05 | 19.0 | 20.0 | 20.0 |
| java | python_native | 1.10 | 89.0 | 100.0 | 100.0 |
| java | web_agentic | 1.05 | 7.0 | 7.0 | 7.0 |
| java | web_agentic | 1.10 | 63.0 | 94.0 | 94.0 |
| java | llm_serving | 1.05 | 3.0 | 3.0 | 3.0 |
| java | llm_serving | 1.10 | 18.0 | 76.0 | 76.0 |

## McNemar on KEEP, pooled over cells

| Schedule | Cells | A | B | KEEP only A | KEEP only B | p |
|---|---|---|---|---|---|---|
| python_native | S <= 1.03 | gated | hac_lf | 2 | 2 | 1 |
| python_native | S <= 1.03 | gated | split | 2 | 2 | 1 |
| python_native | S <= 1.03 | hac_lf | split | 0 | 0 | 1 |
| python_native | S >= 1.05 | gated | hac_lf | 5 | 82 | 5.078e-19 |
| python_native | S >= 1.05 | gated | split | 5 | 82 | 5.078e-19 |
| python_native | S >= 1.05 | hac_lf | split | 0 | 0 | 1 |
| web_agentic | S <= 1.03 | gated | hac_lf | 5 | 0 | 0.0625 |
| web_agentic | S <= 1.03 | gated | split | 5 | 0 | 0.0625 |
| web_agentic | S <= 1.03 | hac_lf | split | 0 | 0 | 1 |
| web_agentic | S >= 1.05 | gated | hac_lf | 5 | 432 | 7.4e-121 |
| web_agentic | S >= 1.05 | gated | split | 5 | 432 | 7.4e-121 |
| web_agentic | S >= 1.05 | hac_lf | split | 0 | 0 | 1 |
| llm_serving | S <= 1.03 | gated | hac_lf | 7 | 0 | 0.01563 |
| llm_serving | S <= 1.03 | gated | split | 7 | 0 | 0.01563 |
| llm_serving | S <= 1.03 | hac_lf | split | 0 | 0 | 1 |
| llm_serving | S >= 1.05 | gated | hac_lf | 5 | 485 | 1.458e-136 |
| llm_serving | S >= 1.05 | gated | split | 5 | 485 | 1.458e-136 |
| llm_serving | S >= 1.05 | hac_lf | split | 0 | 0 | 1 |
| gpu_kernels | S <= 1.03 | gated | hac_lf | 1 | 0 | 1 |
| gpu_kernels | S <= 1.03 | gated | split | 0 | 0 | 1 |
| gpu_kernels | S <= 1.03 | hac_lf | split | 0 | 1 | 1 |
| gpu_kernels | S >= 1.05 | gated | hac_lf | 0 | 0 | 1 |
| gpu_kernels | S >= 1.05 | gated | split | 0 | 0 | 1 |
| gpu_kernels | S >= 1.05 | hac_lf | split | 0 | 0 | 1 |

## Disagreement classes (KEEP by one method only)

| Schedule | KEEP by | Other method's result | Count | Mean speedup estimate at the keeper's verdict |
|---|---|---|---|---|
| python_native | gated (vs hac_lf) | INCONCLUSIVE | 7 | 1.0543 |
| python_native | hac_lf (vs gated) | INCONCLUSIVE (autocorrelated_rounds) | 76 | 1.0813 |
| python_native | hac_lf (vs gated) | INCONCLUSIVE | 8 | 1.0564 |
| python_native | gated (vs split) | INCONCLUSIVE | 7 | 1.0543 |
| python_native | split (vs gated) | INCONCLUSIVE (autocorrelated_rounds) | 76 | 1.0813 |
| python_native | split (vs gated) | INCONCLUSIVE | 8 | 1.0564 |
| web_agentic | gated (vs hac_lf) | INCONCLUSIVE | 10 | 1.0487 |
| web_agentic | hac_lf (vs gated) | INCONCLUSIVE (outlier_rounds) | 271 | 1.0811 |
| web_agentic | hac_lf (vs gated) | INCONCLUSIVE (autocorrelated_rounds) | 119 | 1.0864 |
| web_agentic | hac_lf (vs gated) | INCONCLUSIVE | 42 | 1.0604 |
| web_agentic | gated (vs split) | INCONCLUSIVE | 10 | 1.0487 |
| web_agentic | split (vs gated) | INCONCLUSIVE (outlier_rounds) | 271 | 1.0811 |
| web_agentic | split (vs gated) | INCONCLUSIVE (autocorrelated_rounds) | 119 | 1.0864 |
| web_agentic | split (vs gated) | INCONCLUSIVE | 42 | 1.0604 |
| llm_serving | gated (vs hac_lf) | INCONCLUSIVE | 12 | 1.0530 |
| llm_serving | hac_lf (vs gated) | INCONCLUSIVE | 303 | 1.0765 |
| llm_serving | hac_lf (vs gated) | INCONCLUSIVE (outlier_rounds) | 129 | 1.0929 |
| llm_serving | hac_lf (vs gated) | INCONCLUSIVE (autocorrelated_rounds) | 53 | 1.0880 |
| llm_serving | gated (vs split) | INCONCLUSIVE | 12 | 1.0530 |
| llm_serving | split (vs gated) | INCONCLUSIVE | 303 | 1.0765 |
| llm_serving | split (vs gated) | INCONCLUSIVE (outlier_rounds) | 129 | 1.0929 |
| llm_serving | split (vs gated) | INCONCLUSIVE (autocorrelated_rounds) | 53 | 1.0880 |
| gpu_kernels | gated (vs hac_lf) | INCONCLUSIVE | 1 | 1.0301 |
| gpu_kernels | split (vs hac_lf) | INCONCLUSIVE | 1 | 1.0301 |
