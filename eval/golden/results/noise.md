# Noise at the source

## Golden null cell (S = 1.00)

### python

| Metric | SD of d (%) | Order-centred lag-1 | Mean d (%) |
|---|---|---|---|
| wall | 2.7670 | +0.006 | -0.0294 |
| cpu | 1.6712 | +0.016 | -0.0194 |
| instructions | 0.0677 | -0.005 | +0.0015 |
| cycles | 1.9326 | +0.006 | -0.0335 |

Wall-time d regressed on the frequency ratio and the steal difference: R^2 = 0.000, SD 2.7670% to 2.7668%.

### native

| Metric | SD of d (%) | Order-centred lag-1 | Mean d (%) |
|---|---|---|---|
| wall | 1.0070 | -0.023 | -0.0128 |
| cpu | 0.6938 | -0.028 | -0.0058 |
| instructions | 0.0000 | -0.094 | +0.0000 |
| cycles | 0.5790 | -0.011 | -0.0042 |

Wall-time d regressed on the frequency ratio and the steal difference: R^2 = 0.197, SD 1.0070% to 0.9024%.

### node

| Metric | SD of d (%) | Order-centred lag-1 | Mean d (%) |
|---|---|---|---|
| wall | 1.5466 | +0.037 | +0.0375 |
| cpu | 1.4764 | +0.035 | +0.0334 |
| instructions | 0.4396 | -0.003 | +0.0387 |
| cycles | 1.4881 | +0.035 | +0.0123 |

Wall-time d regressed on the frequency ratio and the steal difference: R^2 = 0.041, SD 1.5466% to 1.5148%.

### java

| Metric | SD of d (%) | Order-centred lag-1 | Mean d (%) |
|---|---|---|---|
| wall | 6.1138 | -0.007 | -0.0063 |
| cpu | 7.0409 | -0.004 | -0.0385 |
| instructions | 7.4737 | -0.001 | -0.0117 |
| cycles | 7.6982 | -0.001 | -0.0046 |

Wall-time d regressed on the frequency ratio and the steal difference: R^2 = 0.053, SD 6.1138% to 5.9480%.

## A/A recordings by condition

| Workload | Order | Pinned | Pass | Metric | SD of d (%) | Lag-1 | Mean d (%) |
|---|---|---|---|---|---|---|---|
| native | blocked | no | 1 | wall | 2.9118 | +0.508 | -0.1071 |
| native | blocked | no | 1 | cpu | 2.3925 | +0.626 | -0.0554 |
| native | blocked | no | 1 | instructions | 0.0000 | +0.276 | -0.0000 |
| native | blocked | no | 1 | cycles | 2.0628 | +0.595 | -0.0488 |
| native | blocked | no | 2 | wall | 3.2768 | +0.459 | -0.1174 |
| native | blocked | no | 2 | cpu | 2.7666 | +0.548 | -0.0959 |
| native | blocked | no | 2 | instructions | 0.0000 | +0.299 | -0.0000 |
| native | blocked | no | 2 | cycles | 2.3938 | +0.527 | -0.0614 |
| native | blocked | 0 | 1 | wall | 2.8479 | +0.371 | -0.0293 |
| native | blocked | 0 | 1 | cpu | 2.2237 | +0.545 | -0.0835 |
| native | blocked | 0 | 1 | instructions | 0.0000 | +0.274 | -0.0000 |
| native | blocked | 0 | 1 | cycles | 1.8603 | +0.486 | -0.0224 |
| native | blocked | 0 | 2 | wall | 3.8621 | +0.387 | -0.1658 |
| native | blocked | 0 | 2 | cpu | 2.9128 | +0.524 | -0.1146 |
| native | blocked | 0 | 2 | instructions | 0.0000 | +0.211 | +0.0000 |
| native | blocked | 0 | 2 | cycles | 2.5232 | +0.508 | -0.0805 |
| native | interleaved | no | 1 | wall | 2.2125 | +0.095 | +0.0656 |
| native | interleaved | no | 1 | cpu | 1.3330 | +0.189 | +0.0913 |
| native | interleaved | no | 1 | instructions | 0.0000 | -0.044 | +0.0000 |
| native | interleaved | no | 1 | cycles | 1.2308 | +0.131 | +0.0649 |
| native | interleaved | no | 2 | wall | 2.1650 | +0.062 | +0.1247 |
| native | interleaved | no | 2 | cpu | 1.7531 | +0.064 | +0.0697 |
| native | interleaved | no | 2 | instructions | 0.0000 | +0.024 | -0.0000 |
| native | interleaved | no | 2 | cycles | 1.5341 | +0.034 | +0.0765 |
| native | interleaved | 0 | 1 | wall | 1.9638 | +0.055 | -0.0058 |
| native | interleaved | 0 | 1 | cpu | 1.3651 | +0.056 | -0.0829 |
| native | interleaved | 0 | 1 | instructions | 0.0000 | -0.051 | -0.0000 |
| native | interleaved | 0 | 1 | cycles | 1.2416 | +0.066 | -0.0721 |
| native | interleaved | 0 | 2 | wall | 2.1817 | +0.222 | -0.1073 |
| native | interleaved | 0 | 2 | cpu | 1.3706 | +0.298 | -0.0609 |
| native | interleaved | 0 | 2 | instructions | 0.0000 | -0.050 | -0.0000 |
| native | interleaved | 0 | 2 | cycles | 1.2211 | +0.314 | -0.0761 |
| python | blocked | no | 1 | wall | 4.7726 | +0.590 | +0.4803 |
| python | blocked | no | 1 | cpu | 4.3179 | +0.662 | +0.4343 |
| python | blocked | no | 1 | instructions | 0.0676 | +0.016 | +0.0039 |
| python | blocked | no | 1 | cycles | 4.7509 | +0.638 | +0.4559 |
| python | blocked | no | 2 | wall | 6.5224 | +0.603 | +0.1155 |
| python | blocked | no | 2 | cpu | 6.0348 | +0.653 | +0.1105 |
| python | blocked | no | 2 | instructions | 0.0669 | +0.038 | -0.0060 |
| python | blocked | no | 2 | cycles | 6.7247 | +0.630 | +0.0617 |
| python | blocked | 0 | 1 | wall | 5.3438 | +0.543 | +0.5072 |
| python | blocked | 0 | 1 | cpu | 4.8361 | +0.592 | +0.4412 |
| python | blocked | 0 | 1 | instructions | 0.0651 | +0.020 | +0.0023 |
| python | blocked | 0 | 1 | cycles | 5.1628 | +0.559 | +0.5194 |
| python | blocked | 0 | 2 | wall | 5.4516 | +0.540 | +0.5665 |
| python | blocked | 0 | 2 | cpu | 4.8553 | +0.631 | +0.4828 |
| python | blocked | 0 | 2 | instructions | 0.0638 | +0.052 | -0.0016 |
| python | blocked | 0 | 2 | cycles | 5.3373 | +0.606 | +0.5518 |
| python | interleaved | no | 1 | wall | 2.9087 | -0.025 | +0.0801 |
| python | interleaved | no | 1 | cpu | 2.3164 | -0.030 | +0.0897 |
| python | interleaved | no | 1 | instructions | 0.0663 | +0.004 | +0.0008 |
| python | interleaved | no | 1 | cycles | 2.7107 | -0.044 | +0.0992 |
| python | interleaved | no | 2 | wall | 4.0812 | +0.083 | +0.0173 |
| python | interleaved | no | 2 | cpu | 3.5382 | +0.094 | -0.0186 |
| python | interleaved | no | 2 | instructions | 0.0680 | +0.025 | -0.0054 |
| python | interleaved | no | 2 | cycles | 4.1138 | +0.077 | +0.0478 |
| python | interleaved | 0 | 1 | wall | 4.7566 | +0.094 | +0.0399 |
| python | interleaved | 0 | 1 | cpu | 4.3405 | +0.090 | -0.0116 |
| python | interleaved | 0 | 1 | instructions | 0.0694 | +0.032 | +0.0042 |
| python | interleaved | 0 | 1 | cycles | 4.9901 | +0.101 | -0.0597 |
| python | interleaved | 0 | 2 | wall | 3.4507 | +0.032 | -0.0535 |
| python | interleaved | 0 | 2 | cpu | 2.9451 | +0.025 | -0.0032 |
| python | interleaved | 0 | 2 | instructions | 0.0661 | +0.020 | +0.0051 |
| python | interleaved | 0 | 2 | cycles | 3.3482 | +0.040 | +0.0110 |
