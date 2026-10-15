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

**Budget.** Free credit only: nothing out of pocket, ever, and no paid run without Faras present.

1. Read live rates and the remaining credit before every paid run:
   `modal billing rates` and `modal billing summary`. Never use
   remembered rates.
2. Estimate the **worst case**, not the expected cost: the run's hard
   timeout times the hourly rate of everything it reserves (GPU, CPU,
   memory). Sandboxes bill CPU and memory at three times the Function
   rate, so reserve the fewest cores and least memory the job needs.
3. Keep the ledger in `.claude/local/gpu-spend.md`: date, task,
   provider, GPU, reserved CPU and memory, timeout, worst-case cost,
   actual cost afterwards from `modal billing report`, and how much
   credit covered it.
4. Refuse to start a run whose worst case is more than the free credit
   the billing summary shows as remaining.
5. Every `modal run`, `modal deploy` and `modal sandbox` command also
   needs Faras's approval through the project's permission rules.

Use free tiers first: Modal's monthly credit, Lightning AI's free hours
through the SSH runner, Kaggle's weekly quota for jobs that fit a
notebook. Size work for T4 and L4 GPUs and small models.

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
