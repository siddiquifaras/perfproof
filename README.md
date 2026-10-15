# perfproof

**Your coding agent says it made the code 20% faster. perfproof checks.**

Agents are good at changing code. They're bad at knowing whether a
change actually helped. Run a benchmark twice and the number moves.
Run it after the laptop warms up, or on a different cloud machine, and
it moves more. A "20% speedup" from one before-and-after run is often
nothing more than that movement.

perfproof gives the agent a process that won't let it fool itself, or
you:

1. **Lock the benchmark.** If the change edits the benchmark or the
   tests, it's rejected. No winning by moving the goalposts.
2. **Run old and new code in turns** (old, new, old, new...) on the same
   machine, so heat, caches and background load hit both equally.
3. **Profile** to find where the time actually goes, instead of guessing.
4. **Check the answers still match**, within a tolerance for numeric code.
5. **Keep the change only if the whole confidence interval clears the
   bar.** By default that bar is a 3% speedup.

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

perfproof turns those lessons into defaults: run both versions on one
machine, record what that machine was, and report the interval instead
of a single number.

## What v1 will cover

| Target | Profilers | Runs on a laptop? |
|---|---|---|
| Python | py-spy, Scalene, memray | Yes |
| C, C++, Rust | samply, perf | Yes (samply), Linux for perf |
| PyTorch | torch.profiler | CPU and Apple MPS locally, CUDA remotely |
| CUDA kernels | Nsight Systems, Nsight Compute | Remote GPU host |
| LLM serving | `vllm bench serve`, SGLang bench, `/metrics` | Remote GPU host |

GPU work runs on a remote host over SSH or on Modal. The agent edits
code locally and perfproof ships the measurement to the GPU.

perfproof talks to agents over MCP, so it works with Claude Code, Codex
and any other MCP client. The same engine is available as a CLI for CI
and for people who'd rather run it themselves.

## Status

Early development. Nothing is released yet, and there is no install
command until there is something worth installing. The statistics core
comes first, because every other part of perfproof depends on its
verdicts being trustworthy.

## Docs

- [Method](docs/method.md): the measurement loop and the exact
  KEEP / REJECT / INCONCLUSIVE rule
- [Architecture](docs/architecture.md): how the pieces fit
- [vLLM case study](docs/case-studies/vllm-prefix-caching.md)

## License

[Apache-2.0](LICENSE)
