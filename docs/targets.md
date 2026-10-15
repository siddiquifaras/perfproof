# Target classes

Every class is proven by the same generic workload: a command, a
metric, the gates and the verdict in [method.md](method.md). What
differs per class is written here: what "same output" means, which
guardrails apply and how they're measured, which helper runs the
workload, and the boundary of what the release supports.

The boundaries below are proposals until each class's contract task in
`.claude/plan.md` confirms them against its fixture and case study.
Anything outside a boundary still runs as a generic command workload:
it is measured and proven, but the playbook's advice and the class's
calibration don't apply, and the report says so.

## Python

| | |
|---|---|
| Workload | An existing benchmark, a slow test, or an entry point; `wall`, `cpu` or `reported` |
| Oracle | The project's tests, output comparison, replay of captured real calls |
| Coverage | coverage.py on changed lines |
| Guardrails | Peak memory of the process tree, 10% |
| Profiling | py-spy built in; Scalene and memray through the playbook |
| Supported | CPython 3.10 and later on macOS arm64 and Linux x86_64 |
| Outside | Free-threaded builds, PyPy, notebooks as workloads |

## Native: C, C++, Rust

| | |
|---|---|
| Workload | A benchmark binary or test binary, built per tree; `wall`, `reported`, or `counter` on Linux |
| Oracle | Output comparison between the two builds, and the project's tests |
| Coverage | llvm-cov or gcov on changed lines |
| Guardrails | Peak memory, 10% |
| Profiling | samply or perf, turned into folded stacks |
| Supported | Projects built with make, CMake or Cargo, on macOS arm64 and Linux x86_64 |
| Outside | Windows, embedded targets, builds that need hardware not present |

## Web and CRUD services

| | |
|---|---|
| Workload | `perfproof load`: the server started and health-checked, driven at a constant request rate; p50, p99 or throughput |
| Oracle | Responses to the recorded request mix compared between versions, and the project's tests |
| Coverage | coverage.py for Python services; reported as not checked for other server languages |
| Guardrails | Peak memory 10%; p99 latency 5% when it isn't the primary |
| Side effects | Database in a local container, reset between rounds; non-local services refused |
| Supported | HTTP services that start locally with one command and expose a health endpoint |
| Outside | Multi-service deployments, managed cloud databases, traffic that can't be recorded or described |

## LLM serving

| | |
|---|---|
| Workload | `vllm bench serve` or SGLang's benchmark against a server perfproof starts; throughput or latency, with `/metrics` counters |
| Oracle | Fixed-seed greedy decoding gives identical tokens, or logprobs agree within a declared tolerance |
| Coverage | coverage.py for the Python layers; kernels reported as not checked |
| Guardrails | Peak GPU memory 10%; p99 latency 5% when it isn't the primary |
| Where | A GPU host over SSH or Modal; one host per comparison when the versions share an image, the cross-machine design when they don't |
| Supported | vLLM and SGLang on one NVIDIA GPU |
| Outside | Multi-node serving, AMD GPUs, hosted inference APIs |

## GPU kernels and PyTorch

| | |
|---|---|
| Workload | A benchmark script timed with CUDA events (`reported`), or a training or inference step |
| Oracle | Outputs compared within a declared numeric tolerance, per dtype |
| Coverage | coverage.py for the Python layers; CUDA code reported as not checked |
| Guardrails | Peak GPU memory 10%; peak host memory 10% |
| Profiling | torch.profiler, Nsight Systems and Nsight Compute through the playbook |
| Supported | CUDA kernels and PyTorch code on one NVIDIA GPU |
| Outside | Multi-GPU and distributed training, AMD and Apple GPUs for kernel work |

## Agentic and LLM apps

| | |
|---|---|
| Workload | `perfproof replay`: a recorded run of the app's model and tool calls, replayed with each response's recorded latency; end-to-end time, calls, tokens |
| Oracle | The app's final output identical between versions under the same replay, and its requests a subset of the recorded ones |
| Coverage | coverage.py on changed lines |
| Guardrails | Peak memory 10%; p99 latency 5% for request-serving apps |
| Side effects | Model and tool calls served from the recording, never live, during measurement |
| Supported | Python apps that call model and tool APIs over HTTP that can be recorded |
| Outside | Apps whose behaviour depends on live, unrecordable state; changes to prompts or model choice (see below) |

How replay keeps the performance test valid:

- **Recording.** One run of the baseline with every outbound HTTP
  request and response captured, with its latency.
- **Replay.** Each request is matched by method, URL and a hash of its
  body, and answered with the recorded response after the recorded
  latency. Concurrency and ordering changes therefore show up in wall
  time as they would live, without live latency's noise or cost.
- **Divergence.** A candidate may make fewer of the recorded requests
  (removing a wasted call) in any order. A request with no recorded
  match can't be answered: the run stops and the result is
  INCONCLUSIVE, never a KEEP.
- **Correctness.** Both versions get the same responses, so their final
  outputs must be identical. A removed call whose result mattered
  changes the output and fails the gate.
- **Limits.** Replay can't measure provider-side effects: prompt
  caching, rate limits, latency that depends on load. Prompt caching is
  reported as a token and cost change only. A change to a prompt or a
  model makes new requests, so it can't be proven under replay and is
  outside the class's release boundary.

## Release case studies: workload and oracle

Each case study has a workload anyone can rerun and a correctness check
that doesn't depend on perfproof. Checked against each repository's
own files; anything still to confirm is marked.

| Class | Repository | Workload | Correctness check |
|---|---|---|---|
| Python | frames2py | Its own benchmark suites, `uv run python -m benchmarks run`, one cell picked with the developer | Its pytest suite; output comparison of the decoded frames |
| Native | picolm | `make`, then `picolm <model.gguf> -p <fixed prompt> -n 128 -t 0 -s 42 -j 4` on the TinyLlama 1.1B Q4_K_M model already in `models/` (`-t 0` is greedy) | Generated text identical between builds |
| Web | NetBox | Postgres and Redis in local containers, the demo data from `netbox-community/netbox-demo-data` loaded, `perfproof load` against a confirmed mix of REST endpoints | Responses compared on the recorded request mix; `python manage.py test --keepdb` on the touched apps, including NetBox's own query-count tests |
| Serving | vLLM | `vllm serve` with a small model on one L4, `vllm bench serve` on the random dataset; settings and code candidates in one image | A separate check at concurrency 1, temperature 0, on fixed prompts: identical tokens. Batched outputs can differ with batch composition, so the throughput run itself isn't the oracle |
| GPU and PyTorch | nanoGPT | `train.py` with the Shakespeare character config for a fixed number of steps on a T4, `float16` (the T4 has no bfloat16), seed fixed as `train.py` already does (`torch.manual_seed(1337)`) | Validation loss after the fixed steps within a declared tolerance; the declared tolerance is set from the baseline's own run-to-run spread, measured first |
| Agentic | gpt-researcher | One research run under `perfproof replay`, recorded with a local model through Ollama and the DuckDuckGo retriever, both supported in its code, so no API key or paid call is needed; or `REPORT_SOURCE` set to local documents for a fully offline run | Final report identical under the same replay; its pytest suite for the code touched |

To confirm in each class's contract task: the frames2py cell, the NetBox
endpoint mix, the vLLM model, the nanoGPT step count and tolerance, and
the gpt-researcher query and documents.
