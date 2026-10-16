## Performance

Measured on an NVIDIA H200 with `N=K=4096`, identical preallocated inputs, 50 ms warmup, 250 ms measurement, and five alternating trials. Values are median p50 latency.

| M | Triton µs | Gluon µs | DeepGEMM µs | Gluon TFLOP/s | Gluon/Triton | Gluon/DeepGEMM |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 24.30 | 13.42 | 14.94 | 2.50 | 0.55x | **0.90x** |
| 8 | 24.83 | 14.21 | 12.58 | 18.89 | 0.57x | 1.13x |
| 9 | 25.12 | 14.24 | 12.58 | 21.21 | 0.57x | 1.13x |
