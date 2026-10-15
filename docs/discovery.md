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

### Project model contract

**Persistence.**

- `project.json` holds the current model; `project.history.jsonl` holds
  every earlier version of every entry, append-only. Nothing is deleted:
  an entry that no longer applies is retired, with the reason.
- The file carries a schema version and is written atomically (a
  temporary file, then a rename), so a crash never leaves half a model.
- Every entry has an ID, a status, and its provenance: the files it was
  derived from with their content hashes, the commands that produced it,
  the perfproof version, and when. A confirmed entry also records that
  the developer confirmed it, in which session, and when.

**Status.** Each entry is in exactly one state:

| Status | Meaning |
|---|---|
| `proposed` | perfproof derived it; nobody has confirmed it |
| `confirmed` | The developer confirmed it |
| `stale` | Something it depends on changed; the trigger is recorded |
| `retired` | No longer applies; the reason is recorded |

**Invalidation.** An entry becomes `stale`, never silently updated,
when:

- any file it was derived from changes content;
- the command that defines a workload fails, or its output changes
  shape;
- a dependency lockfile its workload runs under changes;
- the machine differs from the one a hardware-bound goal or limit was
  set for;
- the developer edits a goal or constraint it depends on;
- a perfproof upgrade changes how that kind of entry is derived.

**Revalidation.** Before a session uses an entry, and before anything
is measured:

1. Every stale entry the session needs is derived again.
2. If the result is identical, the entry returns to its previous
   status, and the revalidation is recorded.
3. If it differs, the new version is `proposed`. A confirmed workload,
   goal or constraint needs the developer's confirmation again before
   it is used.
4. A measurement never runs against a `proposed`, `stale` or `retired`
   workload, goal or constraint.

Unknowns stay unknown throughout: a field perfproof can't establish from
evidence is left empty and asked about, never filled with a default
that looks plausible. Switching goals reuses every entry the new goal
doesn't depend on.

## Discover

### Hypotheses

The unit of discovery is a hypothesis, not a code change. Experts don't
start by editing code: they look at evidence, form a belief about the
mechanism, try to kill that belief cheaply, and only then write and
prove the change. perfproof makes every one of those steps a record.

**The chain.** A hypothesis links six steps, and none may be skipped
without a recorded reason:

| Step | What is recorded |
|---|---|
| Observation | The evidence: measurements, profiles, captured calls, each by path and content hash so it can be re-read later |
| Mechanism | Why the observed cost exists and why a change would remove it, stated as a claim that could be false |
| Prediction | The gain cap with its derivation, and what the cheap test should show if the mechanism is right and if it is wrong |
| Falsification | The cheap test that was run and its result: `supported`, `refuted` or `inconclusive` |
| Implementation | The candidate: its diff hash, its worktree, and the approach in the agent's words |
| Outcome | The prove verdict, its run record, and the measured effect next to the prediction |

A hypothesis whose cheap test refuted it is not implemented. One with
no possible cheap test may be implemented, but the record says why no
test was possible. Comparing predictions with outcomes over time shows
how well the agent's reasoning holds up on this codebase, and the
report says so.

**Persistence.** `.perfproof/hypotheses/<workload>.jsonl` is
append-only. A hypothesis is a sequence of events, one per step and
status change, each with a schema version and timestamp. Nothing is
edited in place; a correction is a new event that names the one it
corrects. A line that can't be read is skipped with a warning and kept,
never dropped.

**Anchors.** Each hypothesis is anchored to what it was about: the
code (file, qualified name, and a hash of the source at observation
time), the workload's spec hash, and the project-model entries it used.

**Invalidation.** A hypothesis becomes stale when any anchor changes:
the code it is about, the workload, or an entry of the project model
it relied on. Its history is kept, and it is shown as "on older code"
or "on an older workload".

**Revalidation.** A stale hypothesis's conclusion is not used to rank
or skip anything until its evidence is produced again on the current
code: the observation measured again, and the cheap test run again if
the result depended on it. A hypothesis refuted on older code may be
tried again; the agent must say what differs. A kept change is a
commit, so it stays kept; if the code it touched changes again, the
hypothesis records that its outcome describes an earlier version.

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
The loop is the same for all of them, and every class implements one
shared target interface, so the six can't drift apart.

A class's release case study runs the whole cycle on a real
repository: understand, discover with at least one falsification test,
prove. It also includes negative cases: a candidate that gives wrong
results, one that is slower, and one that can't be validated, all of
which must be refused for the right reason. One successful case study
is evidence that the cycle works on that repository, not proof that
every codebase of that class is supported, and the release says so.

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
