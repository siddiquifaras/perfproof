# Evaluation tasks

The task set for the external evaluation in
[docs/evaluation.md](../docs/evaluation.md). This directory holds the
miner that finds candidates (`mine/`) and one manifest per task
(`tasks/<family>/<id>.json`).

The held-out and development splits are drawn by the selection rule in
docs/evaluation.md: the inputs are in `tasks/selection.json`, the
draw, with every candidate it looked at, in
[tasks/SPLITS.md](tasks/SPLITS.md), and each picked task's manifest
says `held-out` or `development` in `split` (see "Splits" below).
Every other task is a `development-candidate`. A task used for
development never moves to the held-out set.

Nothing in a manifest is a performance label. Labels come from the
independent procedure in T4.2. The miner's classifications (family,
change kind, revert reason) are keyword rules over GitHub text, kept
with the text they matched so each one can be checked by hand.

## Counts

Mined on 16 October 2026 (`uv run python eval/mine/mine.py counts`).
There are 382 candidates from 106 repositories. These counts are after
the review fixes listed under "Removed after review" below.

**This is a candidate pool, not a reviewed task set.** The rules are
keyword and pattern rules, so the pool has known noise: changes that
aren't runtime optimizations, revert reasons the text doesn't support,
and evidence that isn't about speed. Four review rounds sampled it
against GitHub and fixed what they found by rule, but more noise
remains. Step 1 of the selection rule in docs/evaluation.md guards any
task that is drawn: before the hash-order draw, each candidate's change
is read, and misclassified candidates are removed with the reason
recorded.

| Family | python | native | web | serving | gpu | agentic | java | Total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| hidden-fix | 7 | 6 | 3 | 2 | 12 | 0 | 1 | 31 |
| known-change | 79 | 64 | 37 | 32 | 29 | 30 | 3 | 274 |
| nothing-to-keep | 13 | 11 | 7 | 3 | 6 | 1 | 2 | 43 |
| published-benchmark | 34 | 0 | 0 | 0 | 0 | 0 | 0 | 34 |
| **Total** | 133 | 81 | 47 | 37 | 47 | 31 | 6 | 382 |

| Change kind | Tasks |
|---|---:|
| after-landed | 43 |
| benchmark-instance | 34 |
| improvement | 250 |
| kernel-version | 12 |
| regression | 8 |
| reverted-breaking | 7 |
| reverted-unknown-reason | 28 |

| Status | Tasks |
|---|---:|
| builds | 11 |
| candidate | 325 |
| reference-fails | 1 |
| runs | 45 |

Read these counts with these caveats:

- **Missing commands.** 136 of the 382 have a workload command and 167
  have an oracle command (counted from the manifests: a `command` that
  is not `null`). The rest have `null` and have to be
  completed by hand.
- **Workload commands.** A workload quoted over several lines with
  backslash continuations is joined into one command. If it can't be
  recovered whole, it is `null`. A `vllm bench` line counts only with
  a `serve`, `latency` or `throughput` subcommand.
- **No no-gain reverts.** No revert was classified `reverted-no-gain`.
  So the nothing-to-keep family is all `after-landed` for now. Reverts
  with no stated reason, or reverted for a build or CI failure, are
  known-change `reverted-unknown-reason`.
- **Dropped and skipped pull requests.**
  - 4 selected pull requests were dropped because `mine.pr_base` found
    no base whose diff touches exactly their files: tqdm#955,
    flashinfer#6506, agno#9694 and agno#7708.
  - A reviewer checked tqdm#955 and agno#9694. They are genuine drops:
    a merge commit and a squash whose diff doesn't match the pull
    request's file list. The other 2 weren't investigated.
  - 2 more were skipped because they are also SWE-fficiency instances:
    xarray#5661 and dask#11625.
- **GPU and agentic.** All 12 GPU hidden-fix tasks are from llm.c
  `dev/cuda`. No agentic hidden-fix candidate passed the rules.
- **Published benchmarks** are Python only: GSO 7, SWE-Perf 9,
  SWE-fficiency 18.

### Removed after review

**Round 2** (after 77130ae). The second review found manifests
that were wrong. The rules
were fixed, rather than single files edited, and the manifests were
regenerated. Each fix and the rule it changed:

1. **Kernel versions.** An llm.c commit that renames a kernel into a
   numbered slot no longer counts as a new version. The parent must
   not already define or call that version's launcher
   (`<name><N>(`).
2. **Kernel file lists.** An llm.c task's `reference_change.files`
   lists every file the commit touched, read from the API.
3. **Workload commands.** Backslash continuations are joined, as in
   the caveat above. Nine manifests had stored a first-line fragment,
   such as matplotlib's `python3 -m timeit \`, which timed `pass`.
4. **Source files.** An improvement or a reverted change must touch a
   source file. CI configuration and build or packaging scripts no
   longer count as source.
5. **Build and CI reverts.** A revert whose text puts a failure word
   next to a build word ("breaks the AMD CI image build", "wheel
   creation to fail", "breaks tutorials") is `reverted-unknown-reason`,
   not `reverted-breaking`.
6. **Names in titles.** `optimize` or `vectorize` used as a name
   (after a dot, or before a dot, slash or colon) is not a performance
   word. This excludes SciPy's `optimize.` module and
   `numpy.vectorize`.

39 candidates were removed, each for the reason below.

| Task | Removed because |
|---|---|
| `agno-agi-agno-pr7708-landed` | Moved to known-change when caps freed a slot, then `pr_base` found no base whose diff matches its files |
| `agno-agi-agno-pr9706` | Changes no source file (`cookbook/performance/README.md`, `cookbook/performance/comparison/README.md`, `scripts/perf_setup.sh`) |
| `aio-libs-aiohttp-pr12606` | Changes no source file (`CHANGES/12606.contrib.rst`, `tests/test_client_functional.py`, `tests/test_http_parser.py`) |
| `astral-sh-uv-pr21055` | Changes no source file (`.github/workflows/bench.yml`) |
| `berriai-litellm-pr45551-landed` | Changes no source file (`.github/ci-coverage-allowlist.yml`, `.github/workflows/test-unit.yml`, `tests/local_testing/test_anthropic_prompt_caching.py` and 17 more) |
| `berriai-litellm-pr46713` | Changes no source file (`.github/workflows/test-linting.yml`, `tests/e2e/AGENTS.md`, `tests/e2e/e2e_http.py` and 30 more) |
| `deepset-ai-haystack-pr5828` | Changes no source file (`.github/workflows/linting.yml`) |
| `fastapi-fastapi-pr13583` | Changes no source file (`tests/test_tutorial/test_security/test_tutorial005.py`, `tests/test_tutorial/test_sql_databases/test_tutorial001.py`, `tests/test_tutorial/test_sql_databases/test_tutorial002.py`) |
| `ggml-org-llama-cpp-pr23869` | Changes no source file (`docs/speculative.md`, `requirements/requirements-server-bench.txt`, `scripts/server-bench.py` and 4 more) |
| `huggingface-diffusers-pr12366` | Changes no source file (`docs/source/en/_toctree.yml`, `docs/source/en/optimization/cache_dit.md`) |
| `huggingface-smolagents-pr907` | Changes no source file (`tests/test_local_python_executor.py`) |
| `hypothesisworks-hypothesis-pr456-landed` | Changes no source file (`tests/common/__init__.py`, `tests/conftest.py`, `tests/cover/test_conjecture_engine.py` and 4 more) |
| `karpathy-llm-c-layernorm-forward-f28613e0b9` | A rename, not a new kernel: the parent already defines and calls the launcher `layernorm_forward3(` |
| `lz4-lz4-pr1330` | Changes no source file (`appveyor.yml`, `tests/fullbench.c`) |
| `numpy-numpy-pr23061` | Title matched only a name (`optimize` module, `numpy.vectorize`): 'Revert "ENH: Enabled the use of numpy.vectorize as a decorator"' |
| `ollama-ollama-pr8643` | Changes no source file (`benchmark/server_benchmark_test.go`, `docs/benchmark.md`) |
| `openai-openai-agents-python-pr4197-landed` | Changes no source file (`.github/scripts/run_serial_tests.py`, `Makefile`, `tests/README.md` and 1 more) |
| `openai-openai-agents-python-pr4200` | Changes no source file (`Makefile`) |
| `openai-openai-agents-python-pr4874` | Changes no source file (`.github/scripts/run-asyncio-teardown-stability.sh`, `tests/test_asyncio_stability_command.py`) |
| `openhands-openhands-pr12256` | Changes no source file (`openhands/runtime/utils/runtime_templates/Dockerfile.j2`) |
| `openhands-openhands-pr13590` | Changes no source file (`.github/workflows/ghcr-build.yml`, `containers/build.sh`, `openhands/runtime/utils/runtime_templates/Dockerfile.j2`) |
| `pyca-cryptography-pr6511` | Changes no source file (`tox.ini`) |
| `pydantic-pydantic-ai-pr5389` | Changes no source file (`.github/workflows/ci.yml`, `tests/models/test_model.py`, `tests/models/test_openai_responses.py` and 5 more) |
| `pytest-dev-pytest-pr15151` | Changes no source file (`testing/test_debugging.py`, `testing/test_terminal.py`) |
| `pytorch-ao-pr2973` | Changes no source file (`.github/scripts/torchao_model_releases/eval_latency.sh`) |
| `pytorch-ao-pr4807` | Changes no source file (`.github/workflows/regression_test_pt2e_cpu.yml`, `dev-requirements.txt`, `test/quantization/pt2e/test_graph_utils.py` and 1 more) |
| `saleor-saleor-pr18686` | Changes no source file (`.devcontainer/docker-compose.yml`) |
| `sanic-org-sanic-pr63` | Changes no source file (`requirements-dev.txt`, `tests/performance/tornado/simple_server.py`) |
| `scipy-scipy-pr10083` | Title matched only a name (`optimize` module, `numpy.vectorize`): 'Revert "Clean ``_clean_inputs`` in optimize.linprog"' |
| `scipy-scipy-pr127` | Title matched only a name (`optimize` module, `numpy.vectorize`): 'Revert "ENH: optimize/minimize: combine hess and hessp parameters" (#161...' |
| `sgl-project-sglang-pr43762` | Changes no source file (`test/registered/basic_perf/test_vlm_serving_flashinfer.py`) |
| `simdjson-simdjson-pr2819` | Changes no source file (`benchmark/json2msgpack/simdjson_ondemand.h`) |
| `tile-ai-tilelang-pr3191-landed` | Changes no source file (`CMakeLists.txt`) |
| `tile-ai-tilelang-pr3315` | Changes no source file (`.github/workflows/dist.yml`) |
| `triton-inference-server-server-pr5032` | Changes no source file (`qa/L0_lifecycle/lifecycle_test.py`, `qa/L0_lifecycle/test.sh`) |
| `valkey-io-valkey-pr3157-landed` | Changes no source file (`src/valkey-benchmark.c`) |
| `valkey-io-valkey-pr3463` | Changes no source file (`tests/unit/cluster/faster-failover.tcl`) |
| `vllm-project-vllm-pr60511` | Changes no source file (`tests/benchmarks/test_serve_cli.py`, `vllm/benchmarks/serve.py`) |
| `wagtail-wagtail-pr6158` | Changes no source file (`.squash.yml`) |

8 more kept their pull request but changed family when the caps were re-filled, so they got a new id: `ggml-org-llama-cpp-pr30119-landed` is now `ggml-org-llama-cpp-pr30119`, `ollama-ollama-pr11863-landed` is now `ollama-ollama-pr11863`, `openhands-openhands-pr12105-landed` is now `openhands-openhands-pr12105`, `pytest-dev-pytest-pr851-landed` is now `pytest-dev-pytest-pr851`, `pytorch-ao-pr4310-landed` is now `pytorch-ao-pr4310`, `saleor-saleor-pr11900-landed` is now `saleor-saleor-pr11900`, `sgl-project-sglang-pr43570-landed` is now `sgl-project-sglang-pr43570`, `vllm-project-vllm-pr58584-landed` is now `vllm-project-vllm-pr58584`.

14 candidates are new. They fill cap slots the removals freed, by the same rules: `agno-agi-agno-pr3013-landed`, `berriai-litellm-pr45228-landed`, `berriai-litellm-pr45252`, `ggml-org-llama-cpp-pr29857-landed`, `karpathy-llm-c-layernorm-forward-cb791c4ef5`, `ollama-ollama-pr10678-landed`, `openai-openai-agents-python-pr2563`, `openhands-openhands-pr9925`, `pytorch-ao-pr4113-landed`, `saleor-saleor-pr11874-landed`, `sgl-project-sglang-pr43551-landed`, `tile-ai-tilelang-pr2659`, `valkey-io-valkey-pr3033`, `vllm-project-vllm-pr58194-landed`.

**Round 3** (after 96c0a42). Rules changed:

1. **llm.c versions** come from the dispatch switch (`case N:` calling `<name><N>(`), not from kernel function names.
2. **Source files** exclude lockfiles, docs trees and `.md`/`.mdx`, `scripts/`, examples and cookbooks, and JSON data. A title about speeding up tests, CI or docs doesn't qualify.
3. **A multiplier counts as a speedup** only with performance context: a speed, time or benchmark word within a few words, on its line, or in its table's header. It must also have no device, memory unit or further number right after it, so `1x GPU`, `4x GiB` and `64 x 4096` don't count.
4. **`vllm bench`** must be followed by `serve`, `latency` or `throughput` to count as a workload. That moves vllm#48223 from the PR's typo `vllm bench deepseek-ai/...` to its next quoted block, `vllm bench serve ...`.

20 candidates were removed:

| Task | Removed because |
|---|---|
| `agno-agi-agno-pr9705` | Changes no source file (`cookbook/performance/README.md`, `cookbook/performance/baselines/2026-08-22-apple-m4-max-post-9700.json`) |
| `astral-sh-uv-pr21634` | Title is about tests, CI, docs or examples: 'Use Packse scenarios to speed up tool tests' |
| `cline-cline-pr12568` | Changes no source file (`apps/examples/desktop-app/sidecar/ARCHITECTURE.md`, `apps/examples/desktop-app/sidecar/commands.ts`, `apps/examples/desktop-app/sidecar/context.ts` and 12 more) |
| `deepset-ai-haystack-pr9451` | Title is about tests, CI, docs or examples: 'test: speed up some tests + minor refactorings' |
| `duckdb-duckdb-pr26837` | Title is about tests, CI, docs or examples: 'Speed up prepare CI job' |
| `internlm-lmdeploy-pr4830-landed` | Its multipliers (`0.55x`, `0.57x`, ...) sit in a table whose header reads `\| M \| Triton µs \| Gluon µs \| ... \| Gluon TFLOP/s \|` under a paragraph ending "Values are median p50 latency". The round-3 rule read neither. Restored in round 4 |
| `karpathy-llm-c-attention-backward-e278112c19` | Its kernel3 is a softmax helper dispatched as `case 3: attention_backward1(...)`, not a new numbered launcher. The file no longer has a task |
| `karpathy-llm-c-attention-forward-5f99fb30d2` | Its workload ran `./attention_forward 5`, but the dispatch switch only has cases 1 to 4 (`softmax_forward_kernel5` is a helper). The file's task is now `bb4f04bdf8`, which adds `attention_forward2` |
| `karpathy-llm-c-layernorm-forward-cb791c4ef5` | Not the earliest commit that adds a dispatched version. `466723bb06` adds `case 3: layernorm_forward3` and is now the file's task |
| `karpathy-llm-c-matmul-forward-bb89efdcb1` | Its kernel4 isn't dispatched by `case 4: matmul_forward4`. The file's task is now `8822e7803e`, which adds `matmul_forward3` |
| `ollama-ollama-pr10678-landed` | `1x GPU: 5.7 GiB`, `2x GPU: 7.8 GiB`, ... count GPUs in a VRAM list. Nothing else in the description counts as evidence |
| `openai-codex-pr42638` | Changes no source file (`codex-rs/models-manager/models.json`) |
| `openai-openai-agents-python-pr2563` | Title is about tests, CI, docs or examples: 'perf: speed up make tests with xdist and deterministic waits' |
| `saleor-saleor-pr19294` | Title is about tests, CI, docs or examples: 'fix(tests): slow tests caused by pytest-socket' |
| `scikit-learn-scikit-learn-pr34982-landed` | Changes no source file (`examples/applications/plot_wikipedia_principal_eigenvector.py`) |
| `sgl-project-sglang-pr43551-landed` | Title is about tests, CI, docs or examples: '[Docs] Document Qwen3-4B/8B H200 mixed-chunk latency tradeoffs' |
| `simdjson-simdjson-pr2876` | `3 ×` is the repeat count in "best of 3 × 300 iterations". Nothing else in the description counts as evidence |
| `tile-ai-tilelang-pr2659` | Title is about tests, CI, docs or examples: '[Example][Opt] deepseek_v32 topk_selector kernel memory access optimization (~1.9× faster)' |
| `tile-ai-tilelang-pr3419` | Changes no source file (`examples/kpool/benchmark_glm53_kpool_hadamard.py`, `examples/kpool/example_glm53_kpool_compress.py`, `examples/kpool/example_glm53_kpool_decode_tail.py`) |
| `unslothai-unsloth-pr13404-landed` | Its one multiplier is in "prefill 1997 to 2121 tok/s (1.062x)", on a line beginning "measured in the Zoo PR". The round-3 rule counted neither `tok/s` nor "measured". Restored in round 4 as `unslothai-unsloth-pr13404` (known-change) |

6 changed family when the caps were re-filled, so they got a new id: `agno-agi-agno-pr3013-landed` is now `agno-agi-agno-pr3013`, `astral-sh-uv-pr20118-landed` is now `astral-sh-uv-pr20118`, `cline-cline-pr4676-landed` is now `cline-cline-pr4676`, `duckdb-duckdb-pr25547-landed` is now `duckdb-duckdb-pr25547`, `openai-codex-pr17039-landed` is now `openai-codex-pr17039`, `saleor-saleor-pr11874-landed` is now `saleor-saleor-pr11874`.

10 are new: `astral-sh-uv-pr18311-landed`, `duckdb-duckdb-pr25527-landed`, `internlm-lmdeploy-pr4827-landed`, `karpathy-llm-c-attention-forward-bb4f04bdf8`, `karpathy-llm-c-gelu-forward-45252d434c`, `karpathy-llm-c-layernorm-forward-466723bb06`, `karpathy-llm-c-matmul-forward-8822e7803e`, `karpathy-llm-c-residual-forward-4ffcf5b90a`, `saleor-saleor-pr11175-landed`, `unslothai-unsloth-pr13255-landed`. Five of them are llm.c tasks the dispatch rule now selects. The rest fill freed cap slots by the same rules.

**Round 4** (after eb54fd1). Rules changed:

1. **Speed context** also accepts unit words without a leading digit
   (`ns`, `ms`, `µs`, `us` as a column or in parentheses, `tok/s`,
   `tokens/s`, `TFLOP/s`, `GB/s`, `req/s`, `it/s`) and "measured". For
   a multiplier in a table, the four lines above the table (its heading
   or lead paragraph) are read too. lmdeploy#4830 and unsloth#13404
   come back, with tests on their captured text in `mine/fixtures/`.
2. **File lists** for pull requests with 100 or more changed files are
   paged from `gh api repos/<r>/pulls/<n>/files`. `gh pr view` stops at
   100. `reference_change.files_truncated` is true only when GitHub's
   own 3000-file listing limit is reached, or paging failed. No
   current manifest is truncated. flashinfer#6543-landed now lists all
   118 files.
3. **Test paths:** `spec` counts as a test path only as a `spec/`
   directory or a `*_spec.rb` / `*.spec.js` file, and a pytest oracle
   names only `test_*.py` or `*_test.py` files. lmdeploy's
   `spec_decode/` (speculative decoding) is no longer passed to pytest.
4. **Class fit:** a change whose source files are all browser frontend
   code (a `frontend/`, `web/` or `ui/` tree, TypeScript, JavaScript,
   CSS, HTML) is outside the python, native, serving and GPU classes,
   and is dropped there.

3 candidates were removed:

| Task | Removed because |
|---|---|
| `internlm-lmdeploy-pr4827-landed` | lmdeploy's one after-landed slot went back to the restored `internlm-lmdeploy-pr4830-landed`. Its oracle had also passed `spec_decode/` source files to pytest, which rule 3 now prevents |
| `unslothai-unsloth-pr13255-landed` | All its source files are in `studio/frontend/` (`low-device-scale.ts` and others, 195 files listed in full). A frontend change in a GPU-class repository |
| `unslothai-unsloth-pr13602` | All its source files are in `studio/frontend/src/features/browser/agent/` (`executor.ts`, `page-agent.ts`, `page-wait.ts`). A frontend change in a GPU-class repository |

3 are new or restored: `internlm-lmdeploy-pr4830-landed` and
`unslothai-unsloth-pr13404` (restored), and
`unslothai-unsloth-pr13236-landed` ("Studio: faster image LoRA
training", in `studio/backend/core/training/`), which fills the freed
after-landed slot. The family and class counts are unchanged: each
removal was replaced in the same cell.

## Splits

The held-out list is committed here, with the pool, before any label,
perfproof run or agent run touches a held-out task. It lives in
`tasks/` rather than `eval/label/` because the split is a property of
the task set: T4.1 owns splitting the pool, each manifest carries its
`split`, and T4.2's labelling has to start from a list that is already
fixed.

```bash
uv run python eval/mine/draw.py          # print the draw
uv run python eval/mine/draw.py --write  # set manifests' split, rewrite tasks/SPLITS.md
```

`mine/draw.py` applies the rule in docs/evaluation.md to the manifests
and to `tasks/selection.json`, which holds:

- **Salts.** Held-out `perfproof-heldout-2026-10-16`, from
  docs/evaluation.md. Development `perfproof-development-2026-10-16`,
  chosen here and written into `selection.json` before the
  development order was first computed.
- **Step-1 reviews.** One entry per candidate the draw reached: keep
  or remove, what the reading found, and what was read.
- **Classes not buildable here.** Serving and GPU: "needs a GPU host".
  Paid GPU runs wait for approval.

`mine/test_draw.py` checks each step of the rule on synthetic pools,
and that the committed manifests and `tasks/SPLITS.md` are what the
rule gives for the committed reviews and statuses.
[tasks/SPLITS.md](tasks/SPLITS.md) lists every candidate each draw
looked at, in order, with what it did and why.

### The held-out set

Slot order is the rule's: two known-change, then hidden-fix,
nothing-to-keep and published-benchmark; "(KC)" marks a slot a
known-change task took because the class had no candidate of that
family left. CPU tasks reached `runs` here; serving and GPU tasks are
drawn but unbuilt.

| Class | Known-change | Known-change | Hidden-fix | Nothing-to-keep | Published-benchmark |
|---|---|---|---|---|---|
| python | `psf-black-pr3467` | `python-mypy-pr20802` | `networkx-networkx-pr7810` | `pydata-xarray-pr10921-landed` | `swe-perf-sympy-sympy-26546` |
| native | `libuv-libuv-pr2064` | `astral-sh-uv-pr20879` | `tokio-rs-tokio-pr5829` | `scipy-scipy-pr24913-landed` | (KC) `nlohmann-json-pr5836` |
| web | `gin-gonic-gin-pr4466` | `django-django-pr20911` | `gin-gonic-gin-pr2365` | `honojs-hono-pr5118-landed` | (KC) `nestjs-nest-pr18136` |
| agentic | `crewaiinc-crewai-pr5584` | `crewaiinc-crewai-pr5187` | (KC) `agno-agi-agno-pr3013` | `crewaiinc-crewai-pr2382-landed` | (KC) `run-llama-llama-index-pr21755` |
| serving (unbuilt) | `vllm-project-vllm-pr58634` | `mlc-ai-mlc-llm-pr1033` | `sgl-project-sglang-pr41223` | `internlm-lmdeploy-pr4830-landed` | (KC) `huggingface-text-embeddings-inference-pr35` |
| gpu (unbuilt) | `flashinfer-ai-flashinfer-pr3761` | `triton-lang-triton-pr11565` | `karpathy-llm-c-matmul-backward-bias-55fbd7e6ad` | `triton-lang-triton-pr11138-landed` | (KC) `dao-ailab-flash-attention-pr2819` |

Passed over on the way, for not reaching `runs` here:
`sanic-org-sanic-pr412` (its tests hang until the 30-minute timeout
under the oldest Python and current dependencies) in web. The
held-out section of `tasks/SPLITS.md` differs from the list as first
committed (f595745) in one line only: the wording of sanic#412's pass-over
reason, rewritten when the status rule was corrected. The picks are
identical. Step 1 read 111 candidates and removed 25 (reasons in
`tasks/selection.json`; those a draw reached are also listed in
`tasks/SPLITS.md`).

### The development set

Drawn the same way with salt `perfproof-development-2026-10-16`, from
the candidates the held-out draw did not take. All 20 CPU tasks build
and run here; serving and GPU are not drawn yet (see "How the rule
was applied", point 6). One is `reference-fails`:
`gso-huggingface-transformers-d51b589`, whose reference commit fails
two of the tree's XLNet tests (`test_attention_outputs`,
`test_headmasking`) under current torch while the task tree passes
them. Whether that is the change or the newer torch is for labelling
to settle. It took the published-benchmark slot that
`swe-fficiency-pydata-xarray-7382` held while reference-tree failures
were wrongly passed over.

| Class | Known-change | Known-change | Hidden-fix | Nothing-to-keep | Published-benchmark |
|---|---|---|---|---|---|
| python | `scikit-learn-scikit-learn-pr19328` | `astropy-astropy-pr18455` | `pydata-xarray-pr6107` | `textualize-rich-pr2217-landed` | `gso-huggingface-transformers-d51b589` |
| native | `duckdb-duckdb-pr25547` | `apache-arrow-pr48898` | `sharkdp-fd-pr853` | `pola-rs-polars-pr29813-landed` | (KC) `tokio-rs-tokio-pr5513` |
| web | `saleor-saleor-pr15725` | `labstack-echo-pr3023` | `django-django-pr21460` | `rails-rails-pr58751-landed` | (KC) `rails-rails-pr58964` |
| agentic | `openhands-openhands-pr12105` | `deepset-ai-haystack-pr12997` | (KC) `langchain-ai-langchain-pr38073` | (KC) `run-llama-llama-index-pr18289` | (KC) `mem0ai-mem0-pr1260` |

### How the rule was applied

The rule leaves some choices open. These were made before the draws
and apply to every class:

1. **Step 1 is read in hash order, as far as the draw reaches.** The
   rule removes misclassified candidates first, then orders the rest.
   Reading each candidate when the draw reaches it, and none past
   the last one a draw needs, gives the same result, because a
   removal depends only on the change. What was read: the pull
   request's or commit's description and file list, the revert's
   description where there is one, and for llm.c the kernel file's
   header at the commit. Nothing was run for step 1.
2. **"Class wrong" means outside the class as docs/targets.md defines
   it.** Agentic: Python apps calling model and tool APIs, so Rust TUI
   and TypeScript frontend or extension changes in agentic
   repositories were removed. Serving: LLM inference servers, AMD GPUs
   outside. GPU: NVIDIA, AMD and Apple GPUs outside.
3. **A published-benchmark instance's family is right by
   construction.** Its change is the dataset's reference; all reached
   were kept, with what the patch does.
4. **"No candidate of a family"** covers a family with no candidate
   left that builds and runs, as well as an empty family. Either way
   the next known-change task takes the slot.
5. **"Builds and runs here" is `check.py` status `runs` or
   `reference-fails`.** Buildability is judged on the task tree alone.
   Only that passes a candidate over: `failed` (the task tree's setup
   failed) or `builds` (the task tree's workload, or its oracle where
   required, timed out, could not start or failed; or the workload was run
   only in an earlier form and must be re-run, as for the Java tasks; a
   failure on both trees, such as sanic#412's hang, is one of these). Anything that
   happens only on the reference tree, whether a failed oracle or
   workload, a hang, a missing entry point or a setup that doesn't
   build, is `reference-fails`. Whether the change breaks behaviour or
   its own build is an outcome for labelling, and docs/evaluation.md
   step 4 lets only buildability decide, so the task is drawn. A clone or fetch that fails for the network is not
   recorded, and the candidate is tried again. A failure caused by a
   mistake in the spec written here (a wrong flag, a wrong output
   path) was fixed and the task re-run; a failure of the tree or its
   environment (tests that hang, dependencies that will not build)
   stands.
6. **Classes not buildable here are drawn unbuilt.** Their held-out
   picks are provisional: a pick that later fails on the GPU host is
   passed over and the next candidate is drawn. Their development draw
   waits until then, since the held-out draw may still need
   candidates further down the order.
7. **What was seen.** Building prints and records each command's exit
   code, wall time and output tail, including test output. These are
   single runs, not measurements, and no decision used them. No label,
   perfproof run or agent run of any held-out candidate exists. The 20
   CPU held-out tasks were built before `check.py` stopped recording
   it, so their reference workloads' wall times and output are in
   their manifests. From now on a held-out task's reference workload
   is recorded by exit status only (wall time `null`, output not
   kept), so the serving and GPU held-out builds, still to come, show
   no effect size while their workloads are being written.

## Manifest schema

One JSON file per task, `tasks/<family>/<id>.json`. The schema is the
`Task` model in [mine/task.py](mine/task.py). The root `uv run pytest`
validates every committed manifest against it (its `testpaths` include
`eval/mine`, whose tests are offline), and the root `uv run pyright`
type-checks `eval/mine` in strict mode.

| Field | Meaning | Source |
|---|---|---|
| `id` | Unique id, also the file name | Built from repository and pull request, commit or instance |
| `repository` | `owner/name` on GitHub | Search or dataset row |
| `stars`, `pushed_at` | Popularity and last push at mining time | `gh repo view` |
| `task_commit` | The commit an agent starts from | See `task_commit_basis` |
| `task_commit_basis` | How `task_commit` was found | For pull requests, the base found with `gh api` commits and compare (see "Base of a pull request" below) |
| `reference_change` | The known change: `kind` (`pull-request`, `commit`, `benchmark-instance`), `url`, `title`, `commit`, `merged_at`, `files`, `files_truncated` (true only if GitHub's listing limit may have cut `files` short) | `gh pr view`, `gh api`, or the dataset row |
| `revert` | The pull request that reverted the change, same shape, or `null` | `gh pr view` |
| `family` | `known-change`, `published-benchmark`, `hidden-fix`, `nothing-to-keep` | Rules below |
| `change_kind` | `improvement`, `after-landed`, `reverted-no-gain`, `reverted-breaking`, `reverted-unknown-reason`, `regression`, `kernel-version`, `benchmark-instance` | Rules below |
| `target_class` | `python`, `native`, `web`, `serving`, `gpu`, `agentic`, `java`, `rust` | The seed list's class for the repository |
| `workload` | `command`, `source` (where it came from, or `unknown`), `verified` (the command ran here and exited 0; it does not say the change showed a gain) | Quoted in the pull request, a dataset field, a file header, or a command run here |
| `oracle` | Same shape: the independent correctness check | Test files the change touched, a dataset field, or a command run here |
| `evidence` | Links with notes, one per source used above | |
| `audit` | For published-benchmark tasks: the 2026 cross-machine audit's per-task row | Audit data repository, pinned commit |
| `status` | `candidate` (not built here), `runs`, `reference-fails`, `builds`, `failed` | `mine/check.py` (see its docstring and `classify`) |
| `status_reason` | What was and wasn't checked | |
| `runs` | Every command run here: step, tree (`task` or `reference`), command, exit code, wall time, date, machine, output tail | `mine/check.py` |
| `split` | `held-out` or `development` for a task `mine/draw.py` picked, else `development-candidate` | `mine/draw.py --write` |
| `mined_at`, `miner` | When and by which command the manifest was written | |

An unknown stays unknown: a workload or oracle the miner couldn't
identify has `command: null` and `source: "unknown"`, and a revert
whose reason the text doesn't state is `reverted-unknown-reason`.

## How candidates were found

All GitHub data comes through `gh` and is cached by argv under
`mine/cache/gh/` (gitignored), with 2.5 s between search calls (the
search API allows 30 a minute) and 0.5 s between other calls.

```bash
uv run python eval/mine/mine.py repos       # gh repo view each seed
uv run python eval/mine/mine.py search      # gh search prs per repository
uv run python eval/mine/mine.py view        # gh pr view / gh api for hits
uv run python eval/mine/mine.py llmc        # llm.c dev/cuda kernel versions
uv run python eval/mine/mine.py benchmarks  # GSO, SWE-Perf, SWE-fficiency
uv run python eval/mine/mine.py manifests   # write tasks/
uv run python eval/mine/mine.py counts      # the tables above
uv run python eval/mine/check.py <id> ...   # build and run here
```

1. **Repositories.** [mine/seeds.json](mine/seeds.json) is a
   hand-picked list of repositories per target class. Each is checked
   with `gh repo view` and kept only with at least 2000 stars, a push
   in the last 365 days and not archived. Rejected seeds, with the
   reason, are printed by the `repos` stage.
2. **Search.** In each kept repository, merged pull requests matching
   `perf OR performance OR faster OR speedup OR optimize in:title`,
   `speed OR optimization OR optimized OR throughput OR latency
   in:title`, `revert in:title` and `regression in:title`, filtered
   to titles with a performance word.
3. **Evidence.** An improvement needs numbers in its description (a
   speedup or percentage claim, or two timings next to benchmarking
   words) or a benchmark file in its diff. Rules and their tests are in
   [mine/classify.py](mine/classify.py) and
   [mine/test_mine.py](mine/test_mine.py).
4. **Reverts.** The reverted pull request is found from "Reverts
   owner/repo#N", a pull request link, "This reverts commit <sha>"
   (resolved with `gh api repos/<r>/commits/<sha>/pulls`) or the
   `(#N)` in a GitHub revert title. The revert's text decides the
   kind. It is no gain when the text says slower or no gain only, and
   breaking when it says breaking behaviour only. It is unknown when
   the text says neither or both, or names a build, CI or lint failure
   (a failure word within a few words of a build word in one sentence).
5. **Regressions.** Some regression fixes name the cause in their
   description: "regression introduced in #N", "slowdown caused by #N",
   "regressed by #N". Each such fix gives its culprit pull request as a
   regression task. A bare "introduced in #N" is not enough, because it
   also describes features and optimizations.

### For the runner (T4.3)

- A hidden-fix oracle names the test files the pull request touched,
  which points at where the fix is. The agent must not see the oracle
  command.
- Task ids and file names contain pull request numbers
  (`...-pr7810`), and the leakage check searches transcripts for them.
  Keep ids out of checkout paths and prompts.

### Families and kinds

| Family | Kind | Task commit | Reference change | Right outcome |
|---|---|---|---|---|
| known-change | improvement | base of the pull request | the merged pull request | Set by T4.2's measurement |
| known-change | reverted-breaking, reverted-unknown-reason | base of the pull request | the reverted pull request | Set by T4.2; the oracle should catch a breaking change |
| known-change | regression | base of the culprit | the culprit pull request | Set by T4.2; expected not a KEEP |
| hidden-fix | improvement, kernel-version | base of the change | the change, never shown to the agent | Set by T4.2 |
| nothing-to-keep | after-landed | the merge commit itself | the optimization that already landed | No KEEP |
| nothing-to-keep | reverted-no-gain | base of the pull request | the pull request reverted for not helping | No KEEP |
| published-benchmark | benchmark-instance | the dataset's base commit | GSO: `opt_commit`; SWE-Perf and SWE-fficiency: the base commit plus the row's `patch` (`commit` is `null`) | Set by T4.2; the audit flags stability |

**Base of a pull request.** For a merge commit (two parents) or a
squash, the base is the first parent. A rebase merge replays the pull
request's `k` commits, so there the first parent is the pull request's
own second-to-last commit, which already holds most of the change.
`mine.pr_base` reads the pull request's commit headlines
(`gh pr view --json commits`). When a single-parent merge commit and
the `k - 1` commits before it carry those headlines in order, it takes
the base as `k` first parents back; otherwise it treats the merge as a
squash. GitHub shortens long headlines and ends them with "…", so a
shortened headline is matched as a prefix. The base is kept only if
`gh api repos/<r>/compare/<base>...<merge>` touches exactly the pull
request's files. A pull request that fails this is dropped and printed
by the `manifests` stage. SWE-Perf's `head_commit` is the pull request's
branch tip, which can sit behind the base and touch other files, so it
is kept as evidence only.

Each pull request backs at most one task, so no reference change can
sit in both splits. This holds across the mined tasks and the
SWE-Perf and SWE-fficiency instances (whose ids end in the pull request
number) and GSO's (by the `(#N)` in the commit title), and a test
checks it. A change landed twice and reverted twice under the same
title is kept once.

Per repository, at most one hidden-fix is kept, apart from llm.c's
kernel files (below). A hidden-fix needs an improvement with numbers,
a quoted benchmark command and at most three non-test source files
changed. Also per repository: at most three known-change improvements,
one after-landed task, three reverts and two regressions. This keeps
any single repository from dominating a class.

A hidden-fix task's commit is the base of the change, exported
without later history, as docs/evaluation.md specifies. A later commit
with the change reverted is used only where the base no longer
builds; no such case has come up yet.

An improvement, or a reverted change, must touch at least one source
file. None of these count as source files:

- tests and benchmarks;
- docs: `docs/` trees, `.md` and `.mdx` files;
- changelogs;
- CI configuration: `.github/` and the like;
- build or packaging files: `Dockerfile`, `Makefile`, `*.sh`, `*.yml`,
  `pyproject.toml` and similar;
- lockfiles: `uv.lock`, `*.lock`, `package-lock.json`;
- `scripts/`, examples and cookbooks;
- JSON data files.

A change whose title is about making tests, CI or docs faster ("Speed
up prepare CI job", "perf: speed up make tests", "[Docs] ...") doesn't
count either. Neither does a change whose source files are all browser
frontend code, in a python, native, serving or GPU repository. Titles
are checked again against the current rules when candidates are
selected.

### GPU: llm.c `dev/cuda`

[karpathy/llm.c](https://github.com/karpathy/llm.c) fails the activity
threshold (last push 2025-06-26). It is kept because the plan names
its kernels for GPU tasks, and each manifest says so.

**Which commits count as a new version.** A version is a label in the
file's dispatch switch, `case N:`, that calls the launcher `<name><N>(`.
Kernel function names are not used, because helpers such as
`softmax_forward_kernel5` carry numbers of their own. The `llmc` stage
reads the history of every `dev/cuda/*.cu` file. For each file it keeps
the earliest non-merge commit after which the file dispatches a
version `N` above every version of that launcher in the parent, where
the parent dispatches at least one. That gives 12 files.

**Commits whose dispatch never qualifies.** In `attention_backward.cu`,
cases 2 and 3 call `attention_backward1` with different softmax
launchers, so the rule never sees a new numbered launcher there, and
it has no task.

**Files touched.** `reference_change.files` lists every file the
commit touched, from the API. Most of the 12 touch only their own
`.cu` file. The exceptions are:

- global_norm also touches `dev/cuda/common.h`.
- layernorm_backward and matmul_backward_bias also touch
  `train_gpt2.cu`.
- matmul_forward also touches `Makefile`, `test_gpt2.cu` and
  `train_gpt2.cu`.

**Workload and oracle.** The workload compiles with the `nvcc` line
from the file's header, then runs the new version, a `case N:` that
exists at the reference commit. At the reference commit, 8 of the 12
files call `validate_result`, which checks the GPU result against the
file's CPU reference, and that run is the oracle. The other 4
(attention_forward, layernorm_forward, matmul_forward, softmax_forward)
have no `validate_result` call at that commit, so their oracle is
`null`.

**Still candidates.** A fixed command that runs the newest kernel on
both trees still has to be set up, so all 12 stay candidates. Their
workload lines (`cd dev/cuda && nvcc ... && ./<kernel> <N>`) are shell
lines. `mine/check.py` runs commands without a shell, so it can't run
them as written.

## Held-out discovery set

The discovery supplement of docs/evaluation.md is drawn by
`mine/draw_discovery.py` into `mine/heldout-discovery-draw.json` (pool,
removals, order, build results, picks; the script re-derives it and
exits 1 on a difference). The pool was too small for eight picks under
the two-per-language cap, so the miner was run on the repositories in
`mine/seeds-heldout-discovery.json` (hidden-fix rule only). That list
was fixed before the candidates were mined and before any build; the
first part of it was searched before the second part was added.
Those manifests are listed in the record's
`mined` and kept out of the per-class draw, which was fixed earlier.
The picks are `held-out` and named in
`skill/tasks-heldout-discovery.json`, which `draw.development_named`
does not read. The harness refuses held-out tasks unless
`skill_eval.py run --held-out` is given, and with that flag it takes
only held-out tasks that built.

## Second held-out discovery set

The first discovery set was spent, so "Discovery supplement 2" of
docs/evaluation.md draws another with `mine/draw_discovery2.py` into
`mine/heldout-discovery-2-draw.json` (same shape as the first record;
the script re-derives it and exits 1 on a difference). It reuses
`draw_discovery.derive` with the salt
`perfproof-heldout-discovery-2026-10-29` and one more skip: every task
in the first record's walk. The pool left only one unexamined candidate, so
the miner was run on the repositories in
`mine/seeds-heldout-discovery-2.json`, none of them a seed of the other
two lists. The seed list was fixed in two parts before any candidate of
its part was mined or built. The first part (346 hand-picked
repositories) gave 12 hidden-fix candidates in three languages, too few
for eight picks under the two-per-language cap. The extension (400
repositories) is, for each of seven languages, the most-starred
repositories not yet seeds, found by a GitHub repository search whose
query the file's note records. The miner kept only its hidden-fix
manifests: its known-change and nothing-to-keep output was deleted, and
the manifests it would have removed were restored, so the per-class draw
is unchanged. The 21 mined ids are the record's `mined`; they are kept out
of the per-class draws, which also read this record through
`draw.discovery_record`. `mine/draw_discovery.py` leaves them out of its
own derivation, as it was fixed before they existed.

The walk was judged by `mine/check.py` on the task tree alone, as for the
first set; those runs are in the manifests, with the reference tree's
workload recorded by exit status only. The picks are `held-out` and named
in `skill/tasks-heldout-discovery-2.json`. A task whose setup fetches a
Rust toolchain was retried once: its first attempt timed out at 1800 s on
a stalled download of that toolchain, and the second found the
half-installed toolchain, which was removed and installed again before
the third.

## Mixed held-out set

"Mixed held-out set" of docs/evaluation.md draws hidden-fix tasks whose
user path mixes languages, with `mine/draw_mixed_heldout.py` into
`mine/heldout-mixed-draw.json` (the script re-derives it and exits 1 on
a difference). Salt `perfproof-mixed-heldout-2026-10-30`; the walk
skips every task a task file names and every task a per-class or
discovery draw examined or took. Besides the committed pool, the miner
ran on three seed lists fixed before they were searched:
`mine/seeds-heldout-mixed.json` (hand-picked, 431 repositories) and
`mine/seeds-heldout-mixed-2.json` (905 repositories found by topic and
language-bytes rules the file's note records) and
`mine/seeds-heldout-mixed-3.json` (301 repositories whose primary
language is not Python, added because the first two lists left too few
picks below Python or JS). Only the hidden-fix manifests were written
(37 new ids, the record's `mined`); they are kept
out of the other draws, which read the record through
`draw.discovery_record`, `draw_discovery.later_mined` and
`draw_discovery2.mixed_mined`.

The readings (mixed or not, upper or lower layer) are the record's
`reviews`. The build judgement is not `mine/check.py`, which runs
unconfined: it is the setup path `skill_eval.py run` uses for the eval
user (`runner.build_real_repo` run in the guest, behind the sandbox
profile, planted files removed, the oracle run on the untouched tree,
the task's speed check run once), with no model call. The record's
`builds` holds each outcome. The picks are `held-out` and named in
`skill/tasks-mixed-heldout.json`. The pre-registration's per-language
cap and quotas were removed by its amendment of 2026-10-31, so the picks
are the first builds in hash order, up to 12; the record's `mix` reports
their languages and layers.

### System libraries for the mixed held-out set

By the amendment of 2026-10-31, a setup may take system libraries from
conda-forge, as a developer's Mac gets them from a package manager. The
recipe is one and the same for every candidate:

1. `micromamba` 2.10.0 (the `micromamba-osx-arm64` binary of mamba-org's
   official micromamba-releases, sha256
   `c547639f5caf742236641c362746abb4a3505774753ecd08fe341643670c4c01`)
   is installed as `~ppeval/.local/bin/micromamba`.
2. One environment is created as the eval user, outside the sandbox:
   `MAMBA_ROOT_PREFIX=~ppeval/.local/share/uv/conda-root micromamba
   create -y -p ~ppeval/.local/share/uv/conda-forge -c conda-forge
   --override-channels geos pkg-config openblas ninja cmake`. It sits
   under `.local/share/uv`, which the sandbox profile already lets the
   guest read; nothing outside the eval user's home is touched.
3. A task whose build needs one of those libraries names it in its
   `checks.json` `env` (for example `GEOS_CONFIG` and a linker rpath)
   with the environment's absolute path. No task is given a library
   it does not name.

Every candidate's setup check was redone in hash order after the recipe
existed (`mine/sbcheck.py`: it stages the harness
bundle as `skill_eval.py run` does and calls `runner.build_real_repo` in
the guest, with no model call).

## Published benchmarks

| | GSO | SWE-Perf | SWE-fficiency |
|---|---|---|---|
| Paper | [arXiv 2505.23671](https://arxiv.org/abs/2505.23671) | [arXiv 2507.12415](https://arxiv.org/abs/2507.12415) | [arXiv 2511.06090](https://arxiv.org/abs/2511.06090) |
| Code | [gso-bench/gso](https://github.com/gso-bench/gso) | [SWE-Perf/SWE-Perf](https://github.com/SWE-Perf/SWE-Perf) | [swefficiency/swefficiency](https://github.com/swefficiency/swefficiency) |
| Dataset | `gso-bench/gso`, split `test`, 102 rows | `SWE-Perf/SWE-Perf`, split `test`, 140 rows | `swefficiency/swefficiency`, split `test`, 498 rows |
| Workload field | `prob_script` | `efficiency_test` (pytest ids) | `workload` |
| Test command field | none | none | `test_cmd`, `rebuild_cmd` |
| Environments | linux/amd64 images (`slimshetty/gso`) | linux/amd64 images (`betty1202/sweb.eval.x86_64.*`) | linux/amd64 images (`ghcr.io/swefficiency/swefficiency-images`) |

**Fetching the task lists.** The Hugging Face datasets-server rows API,
paginated:

```
https://datasets-server.huggingface.co/rows?dataset=<id>&config=default&split=test&offset=<o>&length=<n>
```

`n = 100` works for GSO and SWE-Perf. SWE-fficiency's rows carry full
workload scripts and patches (2.88 GB of JSON pages in all), so it needs
`n = 10`; at 100 a page timed out and others returned HTTP 502, and
two pages at 10 returned 502 once before succeeding on retry.
`mine/benchmarks.py` fetches any page not cached, with retries. The
Parquet export (`/parquet?dataset=<id>`) is an alternative that wasn't
used.

The rows API serves a dataset's current revision and takes no revision
argument. The revisions shown below were read from
`https://huggingface.co/api/datasets/<id>` on the day the pages were
fetched. Each was last modified months earlier, so the cached pages
are probably from these revisions, but that wasn't checked.

| Dataset | Revision | Last modified |
|---|---|---|
| `gso-bench/gso` | `c2e4f1a58427cccd15e0e542f136bd204fb19284` | 2026-03-23 |
| `SWE-Perf/SWE-Perf` | `6298f128c550e92ac25af17653cf092224046516` | 2025-08-05 |
| `swefficiency/swefficiency` | `7fbfe6c2f1f409afb17acc4c3f737991ec6e1c27` | 2026-03-30 |

The workload scripts (`prob_script`, `workload`) and the reference
patches live only in the rows, which are cached under the gitignored
`mine/cache/`. A benchmark task can be rebuilt by fetching the rows
again, not from what is committed here.

**The cross-machine audit.** "Are Performance-Optimization Benchmarks
Reliably Measuring Coding Agents?" ([arXiv
2607.01211](https://arxiv.org/abs/2607.01211)) replays each reference
patch on four GCP machine profiles, three rounds each. Its per-task
data is public at
[chenzhi-cz/performance-optimization-benchmark-reliability](https://github.com/chenzhi-cz/performance-optimization-benchmark-reliability),
read here at commit `44a1cb86a9f7`. From `data/cross_machine_task_summary.csv`
(counted here, matching the paper's 39/102, 11/140 and 411/498):

| `final_status` | GSO | SWE-Perf | SWE-fficiency |
|---|---:|---:|---:|
| cross-machine-construction-valid | 39 | 11 | 411 |
| has-hard-flip | 43 | 101 | 28 |
| below-construction-threshold | 16 | 5 | 59 |
| has-no-significant-gain | 0 | 21 | 0 |
| not-fully-evaluable | 4 | 0 | 0 |
| not in the summary | 0 | 2 | 0 |

The audit's raw replay logs are not public; only these summaries and
the per-observation table are.

**Selection.** Instances with `final_status` of
`cross-machine-construction-valid`, in repositories whose environments
plausibly build on macOS arm64 with uv (pure Python, or Python with
prebuilt wheels for compiled dependencies), at most six per benchmark
and repository, in instance-id order. "Plausibly builds" is an
inference from the repository, not a build: none has been built here.
Each manifest's `audit` field carries the audit's row. All of the
benchmarks' own images are linux/amd64, so a native environment has to
be built for each; the benchmarks' reported speedups were measured on
x86_64 and may not carry over to arm64.

Two things in the datasets look wrong and are not used: SWE-fficiency's
`speedup` column is 0.0 for all 498 rows, and every GSO
`setup_commands` list contains `sudo upt upgrade -y`.

## Built and run here

`mine/check.py` builds a task from `mine/checks.json`. For each of the
two trees, the task commit and the reference change, it:

1. extracts that commit with `git archive`, so no later history is
   present, with its submodules at their pinned commits;
2. for a SWE-Perf or SWE-fficiency task, applies the row's patch to
   the reference tree, and for a GSO or SWE-fficiency task writes the
   row's script as `workload.py`;
3. runs the setup lines, which create the environment (a uv
   virtualenv, a cargo build, a container);
4. runs the oracle, then the workload.

Commands run through `shlex.split`, never a shell. The results go
into the manifest's `runs`. All of this happens under `mine/work/`,
which is gitignored. A `pytest.ini` there stops pytest from climbing
up to perfproof's own `pyproject.toml`. The first attempt without it
picked up perfproof's warnings-as-errors setting and failed two
more-itertools tests.

Machine: M4 MacBook Pro, `macOS-15.7.7-arm64-arm-64bit`, CPython
3.12.12 from uv.

### Build recipe

Every task drawn for a split was built the same way, so the draw does
not depend on how hard each one was pushed:

- **Python.** The newest CPython minor released at least six months
  before the reference change merged (3.8 is the oldest uv has for
  macOS arm64), and dependencies limited to releases before the day
  after the merge (`uv pip install --exclude-newer <date>`). A tree
  with its own `uv.lock` uses it instead (`uv sync --frozen`); one
  with a `poetry.lock` has it exported with poetry (`uvx --from
  poetry`) and installed as pinned requirements.
- **Fallback.** When no era build of a dependency exists for this
  platform and Python (an old C extension that does not compile
  against the oldest Python here), the dependencies are current
  releases instead, and the manifest's `status_reason` says so.
  Build tooling (setuptools and the like) is not held to the era where
  the era's version cannot build the tree.
- **Rust.** Current stable from rustup, the tree's `Cargo.lock` where
  it has one.
- **C and C++.** Apple clang; CMake and Ninja from PyPI through uv.
- **Go.** The official `golang:1.25` image under Docker (no Go is
  installed here), with `GOTOOLCHAIN=local` and a shared module cache
  volume. It runs in Docker's linux/arm64 VM, not on macOS itself.
- **Node.** Node 24 from nvm; `npm --before <date>`, or `npm ci`
  from the tree's lockfile.
- **Ruby.** The official `ruby:4.0` image plus Debian's
  `tzdata-legacy` (`tasks/workloads/_images/ruby-4.0-tzdata-legacy/`,
  built by the setup), gems from the tree's `Gemfile.lock`.
- **Python in a container.** Where a Python project needs a system
  library this Mac lacks (Saleor needs libmagic), the official
  `python:<version>-bookworm` image under Docker, with the venv on
  the tree.
- **Docker runs are bounded.** Every foreground `docker run` has
  `--rm --init --stop-timeout 10 --name perfproof-<task>-<step>`. When
  `check.py` times a command out, it kills that container by name
  with `docker kill`, not just the CLI. The Go and Django held-out
  tasks were run before this, so their recorded commands lack these
  flags; their specs have them.
- **Services.** Databases in local Docker containers (PostgreSQL 16
  for Django and Saleor), started by the task's setup and removed by
  its `teardown` lines after every check.
- **Workload files** written here (benchmarks, generators, settings)
  are in `tasks/workloads/<task-id>/` and are copied into or run
  against the tree by the setup lines; shared images are in
  `tasks/workloads/_images/`.
- **Captured output** has this checkout's work directory and the home
  directory rewritten (`eval/mine/work`, `~`).

`check.py` fetches each commit at depth 1 (the network here ran at
about 20 to 30 KB/s during this work), extracts submodules at their
pinned commits, applies a SWE-Perf or SWE-fficiency row's patch for
the reference tree, and writes a GSO or SWE-fficiency workload script
as `workload.py`. A GSO script only defines `setup`, `experiment`
and `run_test`; GSO's harness calls `run_test` after changing
`timeit`'s template, so `benchmarks.GSO_FOOTER` appends that call and
`python workload.py` runs the workload for every GSO task. A clone or
fetch that fails after three tries is a network failure: the manifest is left unchanged and the task stays a
candidate, never `failed`.

Every task built so far, with its split. Workload and oracle commands,
where each came from, and the runs are in the manifest. The six built
before the splits were drawn (networkx#7810, more-itertools#773 and
#699, attrs#1463, jsonschema#1482, pygments#3187) keep their earlier
runs and specs.

| Task | Class | Split | Status |
|---|---|---|---|
| `known-change/agno-agi-agno-pr3013` | agentic | held-out | runs |
| `nothing-to-keep/crewaiinc-crewai-pr2382-landed` | agentic | held-out | runs |
| `known-change/crewaiinc-crewai-pr5187` | agentic | held-out | runs |
| `known-change/crewaiinc-crewai-pr5584` | agentic | held-out | runs |
| `known-change/run-llama-llama-index-pr21755` | agentic | held-out | runs |
| `known-change/astral-sh-uv-pr20879` | native | held-out | runs |
| `known-change/libuv-libuv-pr2064` | native | held-out | runs |
| `known-change/nlohmann-json-pr5836` | native | held-out | runs |
| `nothing-to-keep/scipy-scipy-pr24913-landed` | native | held-out | runs |
| `hidden-fix/tokio-rs-tokio-pr5829` | native | held-out | runs |
| `hidden-fix/networkx-networkx-pr7810` | python | held-out | runs |
| `known-change/psf-black-pr3467` | python | held-out | runs |
| `nothing-to-keep/pydata-xarray-pr10921-landed` | python | held-out | runs |
| `known-change/python-mypy-pr20802` | python | held-out | runs |
| `published-benchmark/swe-perf-sympy-sympy-26546` | python | held-out | runs |
| `known-change/django-django-pr20911` | web | held-out | runs |
| `hidden-fix/gin-gonic-gin-pr2365` | web | held-out | runs |
| `known-change/gin-gonic-gin-pr4466` | web | held-out | runs |
| `nothing-to-keep/honojs-hono-pr5118-landed` | web | held-out | runs |
| `known-change/nestjs-nest-pr18136` | web | held-out | runs |
| `known-change/deepset-ai-haystack-pr12997` | agentic | development | runs |
| `known-change/langchain-ai-langchain-pr38073` | agentic | development | runs |
| `known-change/mem0ai-mem0-pr1260` | agentic | development | runs |
| `known-change/openhands-openhands-pr12105` | agentic | development | runs |
| `known-change/run-llama-llama-index-pr18289` | agentic | development | runs |
| `known-change/apache-arrow-pr48898` | native | development | runs |
| `known-change/duckdb-duckdb-pr25547` | native | development | runs |
| `nothing-to-keep/pola-rs-polars-pr29813-landed` | native | development | runs |
| `hidden-fix/sharkdp-fd-pr853` | native | development | runs |
| `known-change/tokio-rs-tokio-pr5513` | native | development | runs |
| `known-change/astropy-astropy-pr18455` | python | development | runs |
| `published-benchmark/gso-huggingface-transformers-d51b589` | python | development | reference-fails |
| `hidden-fix/pydata-xarray-pr6107` | python | development | runs |
| `known-change/scikit-learn-scikit-learn-pr19328` | python | development | runs |
| `nothing-to-keep/textualize-rich-pr2217-landed` | python | development | runs |
| `hidden-fix/django-django-pr21460` | web | development | runs |
| `known-change/labstack-echo-pr3023` | web | development | runs |
| `nothing-to-keep/rails-rails-pr58751-landed` | web | development | runs |
| `known-change/rails-rails-pr58964` | web | development | runs |
| `known-change/saleor-saleor-pr15725` | web | development | runs |
| `known-change/more-itertools-more-itertools-pr699` | python | development-candidate | runs |
| `hidden-fix/more-itertools-more-itertools-pr773` | python | development-candidate | runs |
| `known-change/pygments-pygments-pr3187` | python | development-candidate | builds |
| `known-change/python-attrs-attrs-pr1463` | python | development-candidate | runs |
| `known-change/python-jsonschema-jsonschema-pr1482` | python | development-candidate | runs |
| `published-benchmark/swe-fficiency-pydata-xarray-7382` | python | development-candidate | runs |
| `known-change/sympy-sympy-pr30321` | python | development-candidate | runs |
| `known-change/sanic-org-sanic-pr412` | web | development-candidate | builds |
| `known-change/sharkdp-fd-pr1422` | rust | development-candidate | runs |
| `nothing-to-keep/sharkdp-fd-pr1914-landed` | rust | development-candidate | runs |

### Rust tasks (T5.13)

Two manifests are in the `rust` class, which was added so that they do
not change the draws of the existing classes (as for `java`):
`known-change/sharkdp-fd-pr1422` and
`nothing-to-keep/sharkdp-fd-pr1914-landed`. The Rust find task is
`hidden-fix/sharkdp-fd-pr853`, which was in the pool already (class
`native`, split `development`). All three are fd, built with
`cargo build --release --locked` and run as the built binary
(`target/release/fd`), never through `cargo run`; the two new ones
search the 200,000-file tree of `tasks/workloads/rust/make_tree.py`.
They were written by hand, then built and run with `mine/check.py` on
this Mac (rustc 1.94.1, macOS arm64): status `runs`, their `runs`
hold every command. Both are `development-candidate`: the draw stops at
them for their step-1 review (`tasks/SPLITS.md`), so
`skill/realrepo.py` will not load them until that review is done, and
`skill/tasks-rust.json` cannot run the two new tasks before then. The
workload runs took 0.12 to 0.21 s each, one run each, so they say
nothing about whether either change is faster.

The exact commands, exit codes, wall times and output tails are in
each manifest's `runs`. Those wall times come from one run each. They
are not measurements and say nothing about whether a change is
faster. That is T4.2's job.

## Not verified

- **Nothing has a label.** No performance or correctness label has
  been produced for any task.
- **325 of 382 tasks are unbuilt.** Their workload and oracle fields
  come from text and dataset fields, not from runs. That includes
  every serving and GPU task: the ten held-out picks in those classes
  are drawn but unbuilt, and any that fails on the GPU host moves its
  draw on.
- **Serving and GPU have no development tasks yet.** Their draw waits
  for the held-out picks to be built on a GPU host.
- **Some workloads are much smaller than the change's own setting.**
  Saleor's runs against `populatedb` sample data, not the million
  categories its pull request measured; OpenHands's and the agentic
  fakes stand in for remote services with a fixed latency. A label
  may find no effect at this size.
- **The 46 tasks that run were checked against targeted tests only.**
  These are tests the change touched, or the module's own test file,
  not the full test suite (the evaluation's oracle). Their workloads
  were adapted or written here, as each manifest's `workload.source`
  says. Whether a written workload exercises the change was judged by
  reading the code it calls, not measured.
- **One machine.** Every build ran on this Mac. Go tasks ran in
  Docker's linux/arm64 VM. Nothing ran on Linux x86-64.
- **Step-1 judgment calls.** Class boundaries were taken from
  docs/targets.md (see "How the rule was applied"). That removed
  Rust and TypeScript changes from agentic and an AMD, an Apple and
  some non-serving changes from serving and GPU; a lead who reads the
  classes by repository instead would restore them and redraw.
- **Most of the miner's classifications have not been checked by
  hand.** Family, change kind and revert reason all come from keyword
  rules, and evidence detection is a regular expression. Two reviews
  sampled manifests against GitHub, and the false matches they found
  are fixed by rule ("Ryzen 7 5800X" read as a speedup, and the fixes
  under "Removed after review"). Others will remain. The 10 candidates
  that are new in round 3 have not been checked by a reviewer. That
  includes 5 llm.c tasks the dispatch rule selects (attention_forward
  bb4f04bd, gelu_forward 45252d43, layernorm_forward 466723bb,
  matmul_forward 8822e780, residual_forward 4ffcf5b9).
- **Repository activity is GitHub's `pushedAt`.** A push to any branch
  counts, so it is not a check of maintainer activity.
- **The search was not exhaustive.** It returned the top 60 hits per
  query (100 for the revert query), best match first. At most 6
  improvement hits, 6 reverts and 4 named culprits per repository were
  viewed.
  Some searches came back empty and were not investigated:
  `Kludex/starlette`, `pallets/flask`, `expressjs/express`,
  `ijl/orjson`, `microsoft/mimalloc`.
- **Known gaps in the tooling:**
  - A failure inside the checkout step that is not a network failure
    (a patch that does not apply, a gitlink with no `.gitmodules`
    entry) still writes `failed` with no `runs`, which the manifest
    consistency test rejects. None has happened yet.
  - The one-task-per-PR check finds a GSO instance's pull request only
    through a `(#N)` in its commit title. Two instances, transformers
    `d51b589` and tornado `ac13ee5`, have none. No mined task comes
    from those repositories today.
  - A pull request that is dropped or skipped still counts against its
    repository's caps, so that repository ends up one task short.
- **"Plausibly builds here" for benchmark instances is an inference.**
  It comes from the repository's languages, not from a build. Each
  benchmark's own environments are linux/amd64 images. Of
  SWE-fficiency's 498 images, only 9 were checked for architecture
  (all amd64).
