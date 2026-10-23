# Golden set: summary

## Injection check (measured, per cell)

| Workload | Cell | Comparisons | Achieved spin (%) | Expected mean d (%) | Measured mean d (%) | SE (%) |
|---|---|---|---|---|---|---|
| python | 1.00 | 200 | 0.0180 | 0.0000 | -0.0292 | 0.0167 |
| python | 1.02 | 100 | 1.9805 | 1.9803 | 1.9187 | 0.0242 |
| python | 1.03 | 200 | 2.9563 | 2.9559 | 2.9380 | 0.0177 |
| python | 1.05 | 100 | 4.8816 | 4.8790 | 4.8635 | 0.0199 |
| python | 1.10 | 100 | 9.5315 | 9.5310 | 9.5048 | 0.0332 |
| native | 1.00 | 200 | 0.0139 | 0.0000 | -0.0128 | 0.0068 |
| native | 1.02 | 100 | 1.9805 | 1.9803 | 1.9859 | 0.0142 |
| native | 1.03 | 200 | 2.9572 | 2.9559 | 2.9331 | 0.0077 |
| native | 1.05 | 100 | 4.8792 | 4.8790 | 4.8675 | 0.0154 |
| native | 1.10 | 100 | 9.5312 | 9.5310 | 9.4771 | 0.0212 |
| node | 1.00 | 200 | 0.0090 | 0.0000 | 0.0375 | 0.0151 |
| node | 1.02 | 100 | 1.9812 | 1.9803 | 1.9806 | 0.0180 |
| node | 1.03 | 200 | 2.9560 | 2.9559 | 2.9681 | 0.0136 |
| node | 1.05 | 100 | 4.8792 | 4.8790 | 4.8793 | 0.0176 |
| node | 1.10 | 100 | 9.5311 | 9.5310 | 9.4684 | 0.0270 |
| java | 1.00 | 200 | 0.0080 | 0.0000 | -0.0063 | 0.0544 |
| java | 1.02 | 100 | 1.9807 | 1.9803 | 1.9542 | 0.0758 |
| java | 1.03 | 200 | 2.9561 | 2.9559 | 3.0723 | 0.0559 |
| java | 1.05 | 100 | 4.8792 | 4.8790 | 5.0114 | 0.0796 |
| java | 1.10 | 100 | 9.5311 | 9.5310 | 9.7150 | 0.0771 |

## Admissibility (wrong-KEEP CP95 upper bound <= 2.5%)

**As registered** (`docs/method.md`, "Choosing"): every cell. A cell of 100 comparisons (S = 1.02) cannot show 2.5% even with no error (bound 2.95%), so under the rule as registered no method is admissible. **Amended after the data**, proposed by the task author and accepted by the owner on 2026-10-23: judge only the cells of 200 (S = 1.00 and 1.03). Both are shown. Every method had 0 wrong-KEEPs in the S = 1.02 cells.

| Method | Cells | Cells of 200 | Wrong-KEEPs in cells of 200 | Worst cell of 200 | Worst CP95 | Cells above 2.5%, as registered (all cells) | Cells above 2.5%, amended (cells of 200) | Wrong-KEEPs in S=1.02 cells |
|---|---|---|---|---|---|---|---|---|
| gated | 42 | 28 | 16 | java/llm_serving/1.03 (4/200) | 0.0452 | 19 | 5 | 0 |
| hac_lf | 42 | 28 | 3 | node/python_native/1.03 (1/200) | 0.0235 | 14 | 0 | 0 |
| split | 42 | 28 | 4 | native/gpu_kernels/1.03 (1/200) | 0.0235 | 14 | 0 | 0 |

Cells of 200 above 2.5% (amended rule):

| Method | Workload | Schedule | S | Wrong-KEEP | CP95 |
|---|---|---|---|---|---|
| gated | native | web_agentic | 1.03 | 2/200 | 0.03114 |
| gated | native | llm_serving | 1.03 | 2/200 | 0.03114 |
| gated | node | web_agentic | 1.03 | 2/200 | 0.03114 |
| gated | java | web_agentic | 1.03 | 2/200 | 0.03114 |
| gated | java | llm_serving | 1.03 | 4/200 | 0.04518 |

## Where `hac_lf` keeps fewer than the gated primary

Power cells (S = 1.05, 1.10); KEEP counts out of 100.

| Workload | Schedule | S | gated | hac_lf | Difference |
|---|---|---|---|---|---|

## Rounds and time to verdict, mean over workloads

| Schedule | S | Method | Rounds | Seconds of executions | Statistics ms per verdict |
|---|---|---|---|---|---|
| python_native | 1.00 | gated | 44.8 | 9.7 | 0.62 |
| python_native | 1.00 | hac_lf | 29.4 | 7.0 | 0.39 |
| python_native | 1.00 | split | 29.4 | 7.0 | 0.41 |
| python_native | 1.05 | gated | 49.8 | 10.9 | 0.65 |
| python_native | 1.05 | hac_lf | 33.9 | 8.1 | 0.46 |
| python_native | 1.05 | split | 33.9 | 8.1 | 0.45 |
| python_native | 1.10 | gated | 47.1 | 10.0 | 0.72 |
| python_native | 1.10 | hac_lf | 21.7 | 5.1 | 0.28 |
| python_native | 1.10 | split | 21.7 | 5.1 | 0.28 |
| web_agentic | 1.00 | gated | 27.6 | 6.0 | 0.38 |
| web_agentic | 1.00 | hac_lf | 23.0 | 5.1 | 0.29 |
| web_agentic | 1.00 | split | 23.0 | 5.1 | 0.30 |
| web_agentic | 1.05 | gated | 28.6 | 6.3 | 0.37 |
| web_agentic | 1.05 | hac_lf | 24.8 | 5.6 | 0.32 |
| web_agentic | 1.05 | split | 24.8 | 5.6 | 0.32 |
| web_agentic | 1.10 | gated | 28.2 | 6.4 | 0.41 |
| web_agentic | 1.10 | hac_lf | 21.0 | 4.9 | 0.27 |
| web_agentic | 1.10 | split | 21.0 | 4.9 | 0.26 |
| llm_serving | 1.00 | gated | 21.7 | 4.7 | 0.35 |
| llm_serving | 1.00 | hac_lf | 18.2 | 4.0 | 0.30 |
| llm_serving | 1.00 | split | 18.2 | 4.0 | 0.30 |
| llm_serving | 1.05 | gated | 21.7 | 4.8 | 0.34 |
| llm_serving | 1.05 | hac_lf | 19.2 | 4.3 | 0.34 |
| llm_serving | 1.05 | split | 19.2 | 4.3 | 0.33 |
| llm_serving | 1.10 | gated | 21.9 | 5.0 | 0.37 |
| llm_serving | 1.10 | hac_lf | 16.9 | 3.9 | 0.28 |
| llm_serving | 1.10 | split | 16.9 | 3.9 | 0.27 |
| gpu_kernels | 1.00 | gated | 48.3 | 6.6 | 0.42 |
| gpu_kernels | 1.00 | hac_lf | 24.4 | 3.4 | 0.23 |
| gpu_kernels | 1.00 | split | 48.3 | 6.6 | 0.42 |
| gpu_kernels | 1.05 | gated | 57.9 | 8.5 | 0.46 |
| gpu_kernels | 1.05 | hac_lf | 33.3 | 4.8 | 0.30 |
| gpu_kernels | 1.05 | split | 57.9 | 8.5 | 0.46 |
| gpu_kernels | 1.10 | gated | 60.7 | 9.2 | 0.52 |
| gpu_kernels | 1.10 | hac_lf | 20.5 | 3.1 | 0.21 |
| gpu_kernels | 1.10 | split | 60.7 | 9.2 | 0.52 |
