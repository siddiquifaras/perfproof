# perfproof

**Find out where your code can get faster, and keep only the changes
that really are.**

Most developers hit a wall with performance. You know your codebase,
you've already fixed the obvious slow parts, and you're out of ideas.
Or you have ideas, but no reliable way to tell whether one helped: run
a benchmark twice and the number moves, so a "20% speedup" from one
before-and-after run is often just that movement.

perfproof does both halves of the job, inside your coding agent (Claude
Code, Codex, or any agent that can run commands). It's a skill that
does the thinking and a small command-line tool that does everything
that has to be exact:

- **It finds what you haven't tried.** It profiles your real workloads
  and looks from angles that are easy to miss: time spent waiting
  rather than computing, the same work done over and over, code that
  gets worse as inputs grow, database queries per request, idle GPUs,
  settings nobody tuned. Each idea becomes a hypothesis, tested cheaply
  before any code is written.
- **It proves which changes to keep.** Old and new code run in turns on
  the same machine, the answers are checked, and a change is kept only
  if the measurements say it's a real improvement.

It works on code you've never seen and on code you've worked on for
years. It remembers what it has already tried and why it was rejected,
so every session starts where the last one stopped.

## Install

One line puts the `perfproof` command on your PATH and the `/perfproof`
skill where Claude Code and Codex find it:

```bash
uv tool install --python 3.12 git+https://github.com/siddiquifaras/perfproof && perfproof install-skills
```

Then run `/perfproof` in your agent, in any repository. `perfproof
doctor` says what is missing and how to fix it. To remove everything:
`perfproof install-skills --uninstall && uv tool uninstall perfproof`.
Details: [docs/install.md](docs/install.md).

## How a session goes

Run `/perfproof` in your agent:

1. **Understand.** perfproof works out what your project is, which
   workloads matter, what "faster" means for you (latency, throughput,
   memory, startup, cost), and what must not change. You confirm it.
2. **Discover.** It ranks opportunities by the most each could gain,
   with the evidence for each one and anything tried before.
3. **Try.** The agent writes a change for the best opportunity, or
   several different ones.
4. **Prove.** perfproof checks the change still gives the same answers,
   measures it against the original, and gives a verdict. Kept changes
   stack up on their own branch; everything else is undone.
5. **Repeat** until the budget runs out or nothing left could clear the
   bar. You get one report with every attempt and its evidence.

## The verdict

Every comparison ends with one of three answers:

| Result | Speedup | 95% interval | Why |
|---|---|---|---|
| KEEP | +8% | +5% to +11% | Even the low end beats +3% |
| REJECT | +1% | -1% to +2% | Even the high end misses +3% |
| INCONCLUSIVE | +5% | +2% to +8% | Might clear +3%, might not, and the run budget is spent |

*These rows show how the rule works. They are not measurements.*

KEEP and REJECT are both claims the numbers back up. INCONCLUSIVE is
the honest answer when they back neither, and perfproof gives it
instead of guessing. A change that edits the benchmark or breaks the
answers is a REJECT however fast it runs.

## What it promises, and what it doesn't

No tool can prove a code change correct. Here's what perfproof does
promise: **it never keeps a change that failed a check, and every
report says what it checked and what it couldn't.**

The checks are built to catch "faster but wrong":

- **Your tests**, run on the old code first, so a flaky test gets set
  aside instead of blamed on the change.
- **Old and new code side by side** on the same inputs, including real
  inputs recorded while the benchmark ran.
- **Every changed line has to run** under those checks. If the tests
  never reach the code that changed, the change isn't kept.
- **No speed bought with something else.** Faster but using several
  times the memory, or with worse tail latency, gets refused.
- **No side effects.** perfproof won't run a benchmark that points at a
  real database or any other service that isn't on your machine.

If your tests are thin, perfproof's own checks fill part of the gap,
and the report tells you exactly which lines nothing verified.

## What it works on

| Kind of code | What perfproof measures | Where it looks for wins |
|---|---|---|
| Python | Benchmarks, slow tests, entry points | Vectorization, algorithms, caching, allocations, imports |
| C, C++, Rust | Benchmark binaries, instruction counts on Linux | Memory layout, SIMD, allocation, branches |
| Web and CRUD services | Latency percentiles and throughput under steady load | N+1 queries, serialization, connection pools, caching |
| LLM serving (vLLM, SGLang) | Throughput and latency from the serving benchmarks | Batching, scheduling, KV cache, attention backends, settings |
| GPU kernels and PyTorch | CUDA-event timing, Nsight, torch.profiler | Occupancy, memory access, launch overhead, fusion, `torch.compile` |
| Agentic and LLM apps | A recorded run of the app, replayed with no API cost | Wasted calls, calls that could run in parallel, prompt caching |

GPU work runs on a remote host over SSH or on Modal. The agent edits
code locally and perfproof ships the measurement to the GPU.

## Why it exists

During a hand-run investigation of a vLLM performance report, the exact
same server config, on the same GPU type, ran at **1172 tok/s** in one
session and **1360 tok/s** in another. Same code, same flags, 16% apart.
Comparing those two runs naively would have "found" a regression or a
speedup that wasn't there.

That investigation also showed why it pays to measure the mechanism and
not only the headline number. Reading the prefix-cache hit rate from the
server's metrics changed the conclusion. The full story, with every
number, is in the [vLLM case study](docs/case-studies/vllm-prefix-caching.md).

perfproof turns those lessons into defaults: look for the mechanism, run
both versions on one machine, record what that machine was, and report
the interval instead of a single number.

## Pick how deep it goes

People are on different plans, and a thorough session can use a lot of
an agent's budget. So `/perfproof` takes one word:

| Command | What it does |
|---|---|
| `/perfproof light` | One workload, the top idea or two, one attempt, a short measurement. Proving a change you already have is light by default. |
| `/perfproof` (medium) | Learns the project, tries the best three to five ideas across the angles above. |
| `/perfproof full` | Every angle, every profiler for your kind of code, as many attempts as the budget allows, the longest measurement. |

A lighter run explores less and measures for less time. It never
lowers the bar: a light run says INCONCLUSIVE more often, but it can't
keep a change a full run would reject. perfproof's own output is kept
short, so the agent spends its budget on your code, not on reading
logs.

## What works today

Early development. Nothing is released and there's no install command
yet. This is what runs now, as `perfproof <command>`:

| Part | State |
|---|---|
| `doctor`: is this machine fit to measure on | Works |
| `bench`: define and lock a benchmark | Works |
| `profile`: where the time goes, with wall versus CPU time | Works (py-spy on Linux, pyinstrument on macOS) |
| `compare`: old against new, with the safety checks | Runs end to end. Until calibration finishes it answers INCONCLUSIVE or a REJECT from a failed check, never KEEP |
| Differential checks (old and new code on the same real inputs) | Built, not yet wired into `compare` |
| Coverage check (every changed line must run) | In review |
| Calibration (the evidence that KEEP can be trusted) | In progress |
| Discovery: the project model, the angles, hypotheses, memory of past attempts | Not built yet |
| The `/perfproof` skill, depth tiers, install | Not built yet |
| Target classes beyond Python | Not built yet. Native, web, LLM serving, GPU and agentic apps are planned |

Discovery matters as much as proof. Coding agents write code that
works, not code that's fast, so the speedups have to be looked for
deliberately. The proving engine was built first only because an idea
is worth nothing until a verdict on it can be trusted.

## Docs

- [Who it is for](docs/product.md): one command for beginners and
  experts, on any plan
- [The CLI and a session](docs/cli.md): what using it looks like
- [Understand and discover](docs/discovery.md): how perfproof builds its
  picture of a project and finds what to try
- [Method](docs/method.md): how a change is proven, and the exact
  KEEP / REJECT / INCONCLUSIVE rule
- [Target classes](docs/targets.md): what "correct" means for each
  kind of code, and what's supported
- [Failure modes](docs/failure-modes.md): every way this could go wrong,
  and what stops it
- [Evaluation](docs/evaluation.md): how we test that it works, on tasks
  we didn't write
- [Architecture](docs/architecture.md): how the pieces fit, and what it
  reuses from existing tools
- [vLLM case study](docs/case-studies/vllm-prefix-caching.md)

## License

[Apache-2.0](LICENSE)
