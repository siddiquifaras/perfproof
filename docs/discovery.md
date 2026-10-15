# Understand and discover

perfproof has three parts. This page is the spec for the first two:
**understand** the project, and **discover** what could make it faster.
The third, **prove**, decides whether a change is kept, and is specified
in [method.md](method.md). Where a part is not built yet,
`.claude/plan.md` tracks it.

```
        ┌──────────────────────────────────────────────┐
        ▼                                              │
  understand ──▶ discover ──▶ prove ──▶ record ────────┘
  project,       hypotheses,  gates,    hypotheses log,
  workloads,     cheap tests, compare,  kept changes,
  goals          candidates   verdict   run records
```

The cycle repeats: after a kept change, after the developer changes the
code, when a new workload appears, or when the developer asks about a
different goal.

## Understand

Discovery is only as good as perfproof's picture of the project. A
developer who has worked on a codebase for years knows things no
profiler shows: which workloads matter, what "faster" means here, and
what must not change. perfproof records that picture in
`.perfproof/project.json` and keeps it between sessions.

| Part | What it holds |
|---|---|
| Targets | The kinds of code present (Python, native, web service, LLM serving, GPU kernels, agentic app), each with the evidence it was detected from |
| Workloads | Named benchmarks, each with where it came from (an existing benchmark, a slow test, an entry point, a route list, recorded traffic, a recorded agent run) and whether the developer confirmed it represents real use |
| Goals | Per workload: the metric (wall time, CPU time, p50 or p99 latency, throughput, peak memory, startup time, cost per token), its direction, the bar, and the guardrails |
| Constraints | Public APIs that must not change, numeric tolerance, target hardware, dependencies that are off-limits, files perfproof must not edit |
| How to run | Build and setup commands, services the workload needs, the reset command between rounds |

Rules:

- **The developer confirms** workloads, goals and constraints before
  anything is measured. perfproof proposes; it doesn't assume.
- **Unknown stays unknown.** A field perfproof can't establish from
  evidence is left empty and asked about, never filled with a default
  that looks plausible.
- **Staleness is detected.** Each entry records the files it was derived
  from. When those change, the entry is flagged and checked again
  rather than trusted.
- **Switching goals is cheap.** Asking "now make it use less memory"
  reuses the model; only the goal and its guardrails change.

## Discover

### Hypotheses

The unit of discovery is a hypothesis, not a code change. Experts don't
start by editing code; they form a belief about where time goes, test it
cheaply, and only then write the change. Each hypothesis is recorded in
`.perfproof/hypotheses/<workload>.jsonl`, append-only:

| Field | Meaning |
|---|---|
| Claim | What is believed, such as "serializing the response is 30% of request time" |
| Mechanism | Why it would be slow, and why a change would help |
| Evidence | The measurements behind the claim, with paths to profiles and records |
| Cap | The most a fix could gain, with how that bound was derived |
| Cheap test | An experiment that confirms or kills the claim before code is written, with the result expected if the claim is true |
| Status | Proposed, confirmed, refuted, implemented, then the verdict |
| Links | The candidate's diff hash and the run records that judged it |

The log is what makes perfproof useful on a repo it has worked on
before: past hypotheses, their tests and their verdicts are shown with
every new opportunity. An idea already refuted on the same code is not
tried again unless the agent says what is different this time.

### Where hypotheses come from

**Measurement angles**, computed by code:

| Angle | What it finds | Evidence |
|---|---|---|
| CPU hotspots | Functions that dominate CPU time | Profiler adapters |
| Waiting, not computing | Time lost to I/O, locks, the GIL or network calls | Gap between wall time and CPU time; off-CPU profiles |
| Repeated identical work | The same call with the same arguments, again and again | Calls captured by the differential checks |
| Growth with input size | Code that gets worse than linear as inputs grow | The workload at two or three input sizes, fitted |
| Startup and imports | Slow starts and cold paths | `python -X importtime`, startup timing |
| Allocation churn | Objects created and thrown away in hot paths | Allocation profiles |
| Database patterns | N+1 queries, missing batching | Queries counted per request |
| Serialization | Time spent encoding and decoding | Stage timing in service workloads |
| GPU idle time | Data loading, tiny kernels, host-to-device copies | GPU timelines |
| Settings | Batch sizes, thread counts, flags, backends | Settings search |

**Code reasoning**, by the agent, guided by the target's playbook:
expensive abstractions, redundant work, algorithmic alternatives,
batching, caching, data layout, concurrency.

**History**: the hypotheses log, and the record of kept and rejected
changes.

### Cheap tests

A cheap test checks a hypothesis before any real change is written:

- time one stage of the workload;
- count queries, calls or allocations;
- microbenchmark an alternative implementation in isolation;
- bound a component's cost by measuring the workload with it stubbed
  out ("what if this were free?");
- toggle one setting.

A cheap test is evidence for what to try next. It is never a verdict.
Only `compare` on the confirmed workload, through every gate, decides
whether a change is kept.

### Ranking

Opportunities are ranked by their cap. The agent may reorder them, but
must say why, and the reason is recorded. A cap is an upper bound from a
model of the workload (for a hotspot, Amdahl's law: a share `p` of
runtime caps the speedup at `1 / (1 - p)`), always labelled as a bound
and never shown as a speedup.

### Many candidates, one claim

Trying several candidates for one hypothesis, or many settings in a
search, gives many chances to be fooled by noise. The rule is the same
everywhere: candidates are measured one at a time, the best KEEP is
chosen, and it is then confirmed by a fresh, independent `compare`
against the baseline. Only the confirmation run's verdict is claimed.

## Targets

Each target class has its own workload, discovery angles and playbook.
The loop is the same for all of them.

| Target | Workload and metrics | Typical hypotheses | Release case study |
|---|---|---|---|
| Python | Benchmarks, slow tests, entry points; wall or CPU time | Vectorization, algorithm choice, caching, allocations, imports | frames2py |
| Native (C, C++, Rust) | Benchmark binaries; wall time, instruction counts on Linux | Memory layout, SIMD, allocation, branches | picolm |
| Web and CRUD services | Server started, health-checked, driven at a constant request rate; p50, p99, throughput; database reset between rounds | N+1 queries, serialization, connection pools, caching | open question |
| LLM serving | `vllm bench serve`, SGLang bench, server metrics; throughput, latency | Batching, scheduling, KV-cache pressure, prefix caching, attention backend, quantization, settings | vLLM |
| GPU kernels and PyTorch | CUDA-event timing, Nsight Systems and Compute, torch.profiler | Occupancy, memory coalescing, launch overhead, fusion, `torch.compile`, data types | open question |
| Agentic and LLM apps | A recorded run of the app's model and tool calls, replayed; end-to-end time, calls, tokens | Wasted calls, sequential calls that could run in parallel, prompt caching, payload size, retries | open question |

## What discovery never does

- Present a cap, a cheap test or a candidate's selection-stage result as
  a speedup.
- Keep a change that hasn't been through prove.
- Retry a refuted idea on the same code without saying what changed.
- Treat a generated workload as representative before the developer
  confirms it.
