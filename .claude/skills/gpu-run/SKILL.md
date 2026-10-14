---
name: gpu-run
description: Plan and run paid GPU work on Modal or a rented host for perfproof (GPU benchmarks, vLLM or SGLang serving, Nsight profiling). Use before any command that starts paid compute.
---

Paid compute comes out of a small budget. Nothing starts without the
user's approval of a written estimate.

## Before the run

Write the estimate and wait for a yes:

- GPU type and count, and why that GPU (memory needed, what the result
  must be comparable with)
- Expected wall time, including image pull, model download, server
  start, warmup and every measured run
- Cost: GPU rate plus CPU and memory rate, times wall time, rounded up
- What result the run produces, and what decision it informs

Check the budget notes in `.claude/local/HANDOFF.md` when that file is
present.

## During the run

- Use the official images for serving frameworks
  (`vllm/vllm-openai:v<version>`). Pip-installed vLLM lacks `nvcc`, so
  FlashInfer JIT fails.
- Fail fast. Wait for `/health` with a timeout, and on any startup
  failure print the last 80 lines of the server log and stop. Stop the
  remaining versions too: they almost always hit the same problem.
- Cache model weights in a volume. Modal can't mount a volume at a path
  the image's environment points to, so mount it elsewhere and point the
  variable inside it (for example `HF_HOME=/vol-cache/hf`).
- Read prefix-cache, KV-cache and scheduler counters from `/metrics`
  before and after each measured run. The mechanism matters as much as
  throughput.
- Use `--detach` for long Modal runs so a sleeping laptop doesn't kill
  them. Don't rely on `modal app logs` piped to a file; it stops early.
  Write results to a volume instead.

## Comparing results

- Baseline and candidate on one host, interleaved, whenever possible.
- When they can't share a host (different container images), use the
  cross-machine design in `docs/method.md`: explicit opt-in, sessions
  alternating A, B, A, B on freshly allocated hosts, at least `n0`
  session pairs. Price all of those sessions into the estimate. Fewer
  pairs give INCONCLUSIVE, so a cheaper run buys nothing.
- Report the measured cost next to the estimate.
