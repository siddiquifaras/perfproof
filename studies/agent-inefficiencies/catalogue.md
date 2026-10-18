# What agents leave slow: a catalogue from real diffs

Task T3.18. Which inefficiencies do real performance changes remove,
and which do shipped changes introduce, and what is the cheapest
measurement that confirms or rules out each one? The answer is the
evidence behind the angle checklist in `docs/discovery.md`; the proposed
evidence column is in [proposal.md](proposal.md) and the per-change data
in [data.csv](data.csv).

Everything below is counted from `data.csv`. A count is the number of
changes I classified, not a rate in any population: see "How to read the
counts".

## How the sample was drawn

- **Population.** The T4.1 pool: 376 manifests in `eval/tasks/`.
  Excluded before any diff was opened: the 44 ids listed under
  "held-out" in `eval/tasks/SPLITS.md` (every row of that section,
  including those marked removed, passed or unbuilt) and 5 further ids
  that the review marked "removed" in the development section (not an
  optimization or the wrong class). 327 remain. No held-out change was
  fetched; I checked every URL in `data.csv`, including revert and fix
  links, against the held-out manifests' change and revert URLs and
  found no overlap.
- **Random draw (55 changes).** `random.Random(318)` over each target
  class's remaining landed changes (kinds `improvement`,
  `after-landed`, `kernel-version`, `benchmark-instance`), sorted by id,
  without replacement: python 12, native 10, web 8, agentic 8,
  serving 7, gpu 10. Families in the draw: hidden-fix 5, known-change 41, nothing-to-keep 4, published-benchmark 5.
- **All reverted and regression (38 changes).** The complete
  population of reverted changes (miner kinds `reverted-breaking` and
  `reverted-unknown-reason`: 30) and of changes later named as the
  cause of a performance regression (`regression`: 8) among the 327.
  The original change is classified; for the 8 regressions I also read
  the fixing pull request to see what the change had introduced.
- **Targeted supplement (18 changes).** The random draw found no lock
  contention, no blocking call in async code and few N+1 or quadratic
  cases, so I scanned the titles of the remaining pool for those words
  and read 18 more landed changes. These are chosen by keyword, not
  drawn; the counts table shows them separately and "in random draw"
  counts only the draw.
- **Agent-written code (12 changes).** From the T3.11 study (branch
  `claude/t3-11-pr-study-0f488d`, `studies/pr-study/`): the 6 agent-
  written or agent-assisted pull requests (P1, P2, P4, P5, P7, P8) and
  the 6 unaided runs that changed code (C1, C2, C3, X1, X2, X3), whose
  diffs I took with `diff -ru` against the base clones in that
  worktree's gitignored `scratch/pr-study/`. P3, P6 and P10 (no agent)
  are read as human changes; P9 is already in the pool as
  `vllm-project-vllm-pr48223`. C4 changed no code.
- **Total.** 126 changes. Population split:

| Population | n | landed | reverted | regression | other |
|---|---:|---:|---:|---:|---:|
| random draw | 55 | 55 | 0 | 0 | 0 |
| targeted | 18 | 18 | 0 | 0 | 0 |
| all reverted and regression | 38 | 0 | 30 | 8 | 0 |
| T3.11 PR list | 9 | 5 | 1 | 0 | 3 |
| T3.11 agent runs | 6 | 0 | 0 | 0 | 6 |

## How each change was classified

- I read the diff of the change itself, not its title. Diffs came from
  `gh api repos/{o}/{r}/commits/{sha}` (the manifest's change
  commit) or `pulls/{n}/files`, both read-only GETs. Test, doc and
  lock files were filtered out; up to 7 source files and the first 70
  lines of each were fetched; I read the first 45 to 75 lines of that
  filtered text per change and the rest of a file only where the head
  did not settle it. `partly_read` in `data.csv` is `yes` where the
  filtered diff ran past 75 lines (56 of 120 diffs).
- Each change gets one **primary kind** (the mechanism that takes the
  largest share of the diff or that the change itself names) and
  optionally one secondary kind. Kinds are not mutually exclusive;
  forcing one primary is a judgement call and a second reader could
  differ on some.
- `direction` is `removed` (a landed fix), `attempted` (a fix that was
  reverted, closed unmerged, or measured slower or equal in T3.11) or
  `introduced` (a change later blamed for a regression, classified by
  the cause the fixing change shows).
- Tree-sitter and GumTree were not used. Each diff was short enough to
  read, and a syntax-level diff would have helped most on the
  56 partly-read changes; that is the first thing to add if this
  study is repeated.
- The T4.1 manifests' `change_kind` is the miner's keyword reading of
  GitHub text; it is carried in `miner_change_kind` and is not my
  classification.

## How to read the counts

- The random draw is the only part that is a sample of landed changes.
  All reverted and regression changes are included, so the ratio of
  `reverted` to `landed` in the table below says nothing about how often
  a kind is reverted. Read it as "reverted changes of this kind exist",
  and compare kinds only inside the reverted column.
- n is small. A kind with 3 or 4 changes is a pointer, not a
  distribution. Counts of human against agent are 114 against 12 and
  support no comparison of rates.
- Classes are the manifests' `target_class` (for T3.11 rows, the study's
  class). Some pool entries sit in a class that fits only loosely, for
  example a Rust tokenizer filed under gpu.

## Kinds, with counts

| Kind | n | landed | reverted | regression | other | human | agent | in random draw |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| repeated-work | 19 | 13 | 5 | 1 | 0 | 18 | 1 | 7 |
| unneeded-work | 16 | 9 | 6 | 1 | 0 | 16 | 0 | 8 |
| codegen | 11 | 7 | 3 | 0 | 1 | 10 | 1 | 7 |
| launch-fusion | 11 | 7 | 3 | 0 | 1 | 9 | 2 | 4 |
| copy-alloc | 10 | 5 | 0 | 3 | 2 | 8 | 2 | 2 |
| settings-backend | 7 | 5 | 1 | 1 | 0 | 5 | 2 | 2 |
| cheaper-primitive | 7 | 6 | 0 | 0 | 1 | 6 | 1 | 5 |
| serialized | 5 | 3 | 1 | 0 | 1 | 5 | 0 | 1 |
| memory-access | 4 | 4 | 0 | 0 | 0 | 4 | 0 | 4 |
| n-plus-1 | 5 | 3 | 0 | 0 | 2 | 3 | 2 | 2 |
| pyloop | 4 | 1 | 1 | 1 | 1 | 4 | 0 | 1 |
| underparallel | 4 | 3 | 1 | 0 | 0 | 3 | 1 | 1 |
| quadratic | 3 | 2 | 1 | 0 | 0 | 3 | 0 | 1 |
| eager-init | 4 | 4 | 0 | 0 | 0 | 4 | 0 | 4 |
| repeated-io | 3 | 1 | 1 | 1 | 0 | 3 | 0 | 1 |
| data-structure | 3 | 3 | 0 | 0 | 0 | 3 | 0 | 3 |
| over-fetch | 1 | 0 | 1 | 0 | 0 | 1 | 0 | 0 |
| contention | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| mixed | 1 | 0 | 1 | 0 | 0 | 1 | 0 | 0 |
| none | 8 | 2 | 6 | 0 | 0 | 8 | 0 | 2 |
| **total** | 126 | 78 | 31 | 8 | 9 | 114 | 12 | 55 |

By target class:

| Kind | python | native | web | agentic | serving | gpu |
|---|---:|---:|---:|---:|---:|---:|
| repeated-work | 10 | 1 | 4 | 1 | 1 | 2 |
| unneeded-work | 5 | 5 | 3 | 1 | 2 | 0 |
| codegen | 0 | 8 | 0 | 0 | 0 | 3 |
| launch-fusion | 1 | 0 | 0 | 0 | 6 | 4 |
| copy-alloc | 3 | 2 | 1 | 1 | 2 | 1 |
| settings-backend | 1 | 2 | 1 | 0 | 2 | 1 |
| cheaper-primitive | 4 | 0 | 2 | 1 | 0 | 0 |
| serialized | 0 | 0 | 0 | 2 | 3 | 0 |
| memory-access | 0 | 1 | 0 | 0 | 1 | 2 |
| n-plus-1 | 0 | 0 | 3 | 1 | 1 | 0 |
| pyloop | 3 | 0 | 1 | 0 | 0 | 0 |
| underparallel | 1 | 1 | 0 | 0 | 0 | 2 |
| quadratic | 2 | 0 | 1 | 0 | 0 | 0 |
| eager-init | 1 | 0 | 0 | 3 | 0 | 0 |
| repeated-io | 2 | 0 | 0 | 0 | 1 | 0 |
| data-structure | 1 | 1 | 0 | 1 | 0 | 0 |
| over-fetch | 0 | 0 | 0 | 0 | 1 | 0 |
| contention | 0 | 0 | 0 | 0 | 0 | 0 |
| mixed | 0 | 0 | 0 | 0 | 1 | 0 |
| none | 1 | 1 | 1 | 1 | 2 | 2 |
| **total** | 35 | 22 | 17 | 12 | 23 | 17 |

Primary kind only. "other" is closed-unmerged (3) plus unsubmitted agent
runs (6).

## What reverted changes were

Of the 31 `reverted` changes (30 from the pool, plus llama.cpp#29182
from T3.11), the miner's reading of the revert text matched a breaking
reason for 5 (`reverted-breaking`; by primary kind: launch-fusion 1,
repeated-work 2, unneeded-work 2) and none for the other 25 pool
reverts. The miner's "none" is not always right: the manifests'
excerpts of a few revert texts do give reasons (a CI image build break
in vllm#52606, a query that slows with key count in litellm#41293, "in
favour of" another change in libuv#4512, "breaks tutorials" in
triton#7770). The causes visible in those excerpts, in the diffs and in
T3.11: broken behaviour or output (Oracle multi-column `__in`, accuracy
collapse in the EAGLE draft, lost error information in pip), a memory
fault, a build or CI break, a race and low-concurrency loss (vLLM dual
stream, T3.11), slowdowns on other GPUs (llama.cpp, T3.11), or being
replaced by another approach. I read the manifests' excerpts of the
revert texts, not the revert threads, so a reason may exist that I did
not see.

6 of the 31 reverted changes have no identifiable inefficiency in the
diff (bug fixes, a behaviour change, removal of an optimisation), and
one (vllm#48597, a 29-file squash) has no single primary kind, so it is
`mixed`. The six look like keyword-miner noise in the pool, not a kind
of inefficiency. The last two items below are landed changes kept in
`none` for the same reason (a bug fix; a benchmark-harness change):

- [pydata-xarray-pr4432](https://github.com/pydata/xarray/pull/4432) (reverted): Bug fix for optimize(); deletes a dict-filter comprehension, no inefficiency named in the diff
- [langchain-ai-langchain-pr8610](https://github.com/langchain-ai/langchain/pull/8610) (reverted): Loop turned into comprehension and results concatenated; no complexity change
- [berriai-litellm-pr9387](https://github.com/BerriAI/litellm/pull/9387) (reverted): Bug fix for timedelta latencies in router; no inefficiency in the diff
- [nestjs-nest-pr13056](https://github.com/nestjs/nest/pull/13056) (reverted): Adds an undefined-token error check; no inefficiency removed
- [triton-lang-triton-pr9204](https://github.com/triton-lang/triton/pull/9204) (reverted): Deletes the moveUpTranspose scheduling pass; removes an optimisation rather than an inefficiency
- [astral-sh-ruff-pr23378](https://github.com/astral-sh/ruff/pull/23378) (reverted): Removes the NoReturn shortcut predicate; removes an optimisation, no inefficiency removed
- [ollama-ollama-pr17998](https://github.com/ollama/ollama/pull/17998) (landed): Bug fix for Metal GPU timeouts when loading from slow storage (adds a CPU pre-read); also drops one redundant forced evaluation, which is not the change's purpose
- [deepseek-ai-deepgemm-pr68-landed](https://github.com/deepseek-ai/DeepGEMM/pull/68) (landed): Benchmark harness change (L2 flush size, power-limited mode); changes how kernels are timed, not the kernels

Among the 24 reverted changes that did target one inefficiency, the
largest group is the kinds that skip or reuse work (`unneeded-work` 6,
`repeated-work` 5: 11 of 24), and 4 of the 5 reverts with a breaking
reason are in those two kinds. INFERENCE, not tested: skipping or
caching work is correct only for the inputs the author thought of, so
the correctness check, not the timing, is what stops these.

## What shipped changes introduced (the 8 regressions)

These are the changes later named as the cause of a slowdown. The
"introduced" kind comes from what the fixing change undoes.

| Change | Introduced | Diff | Fix |
|---|---|---|---|
| pola-rs-polars-pr28494 | unneeded-work | [pola-rs-polars-pr28494](https://github.com/pola-rs/polars/pull/28494) | [fix](https://github.com/pola-rs/polars/pull/29427) |
| vllm-project-vllm-pr21146 | copy-alloc | [vllm-project-vllm-pr21146](https://github.com/vllm-project/vllm/pull/21146) | [fix](https://github.com/vllm-project/vllm/pull/24128) |
| sgl-project-sglang-pr2786 | copy-alloc | [sgl-project-sglang-pr2786](https://github.com/sgl-project/sglang/pull/2786) | [fix](https://github.com/sgl-project/sglang/pull/3008) |
| python-mypy-pr5465 | repeated-io | [python-mypy-pr5465](https://github.com/python/mypy/pull/5465) | [fix](https://github.com/python/mypy/pull/5556) |
| dask-dask-pr9267 | repeated-work | [dask-dask-pr9267](https://github.com/dask/dask/pull/9267) | [fix](https://github.com/dask/dask/pull/9413) |
| hypothesisworks-hypothesis-pr4395 | copy-alloc | [hypothesisworks-hypothesis-pr4395](https://github.com/HypothesisWorks/hypothesis/pull/4395) | [fix](https://github.com/HypothesisWorks/hypothesis/pull/4440) |
| pandas-dev-pandas-pr56037 | pyloop | [pandas-dev-pandas-pr56037](https://github.com/pandas-dev/pandas/pull/56037) | [fix](https://github.com/pandas-dev/pandas/pull/64438) |
| vllm-project-vllm-pr12405 | settings-backend | [vllm-project-vllm-pr12405](https://github.com/vllm-project/vllm/pull/12405) | [fix](https://github.com/vllm-project/vllm/pull/12434) |

Three of the eight introduced a copy, allocation or garbage-collection
cost (`copy-alloc`): a deep copy in a repr, a full-size division copy on
the common path, uncollected garbage after a gc freeze. One introduced a
per-module filesystem probe, one dropped a reuse of already-computed
dependencies, one lost a pushdown, one a silent fallback to a slower
kernel, and one a per-element loop with a per-element exception handler.
All eight were found after merge.

## Agent-written code

The 12 agent changes, by what they targeted. The measured effects are
from the T3.11 study (descriptive A/B timings on one loaded laptop, not
calibrated verdicts); the classification is mine.

| Change | Class | Primary | Secondary | Outcome | Diff |
|---|---|---|---|---|---|
| t311-P1-pandas-64347 | python | underparallel | - | landed | [diff](https://github.com/pandas-dev/pandas/pull/64347) |
| t311-P2-pandas-66238 | python | cheaper-primitive | - | closed-unmerged | [diff](https://github.com/pandas-dev/pandas/pull/66238) |
| t311-P4-llama.cpp-29182 | native | settings-backend | - | reverted | [diff](https://github.com/ggml-org/llama.cpp/pull/29182) |
| t311-P5-onnxruntime-32301 | native | settings-backend | - | landed | [diff](https://github.com/microsoft/onnxruntime/pull/32301) |
| t311-P7-django-22141 | web | repeated-work | - | landed | [diff](https://github.com/django/django/pull/22141) |
| t311-P8-vllm-58737 | serving | launch-fusion | - | landed | [diff](https://github.com/vllm-project/vllm/pull/58737) |
| agent-C1-frames2py | python | launch-fusion | - | not-submitted | T3.11 section (data.csv) |
| agent-X1-frames2py | python | copy-alloc | - | not-submitted | T3.11 section (data.csv) |
| agent-C2-picolm | native | codegen | launch-fusion | not-submitted | T3.11 section (data.csv) |
| agent-X2-picolm | native | copy-alloc | repeated-io | not-submitted | T3.11 section (data.csv) |
| agent-C3-netbox | web | n-plus-1 | repeated-work | not-submitted | T3.11 section (data.csv) |
| agent-X3-netbox | web | n-plus-1 | - | not-submitted | T3.11 section (data.csv) |

What the data supports, and no more:

- The kinds agents targeted are the kinds humans fixed: launch and
  fusion 2, n-plus-1 2, copy-alloc 2, settings-backend 2, and one each of
  codegen, cheaper-primitive, underparallel and repeated-work. Nothing in
  these 12 is a kind the 114 human changes lack.
- What differs is the outcome on the path T3.11 measured. T3.11 measured
  X1 (0.76-0.95 times, slower), X2 (0.98-1.01, no change) and X3
  (0.58 and 0.71 times on the full list, slower), and llama.cpp#29182
  was reverted after slowdowns on other GPUs: 4 of the 12 changes. In
  each, the agent chose a mechanism that sounded right (fewer
  temporaries, fewer allocations, fewer queries, a better tile for one
  device) and the measured result on the other path did not follow. X3
  cut the device list from 19 queries to 13 and was slower.
- Two more are faster but wrong or risky: C2 (7.4 times, output
  diverged from the base) and P1 (parallel read_csv; divergent results
  found in review and after merge, per T3.11).
- P5 (Copilot) shipped a fix for a roughly 3,500 times regression with no
  timing after the fix, per T3.11's reading of the thread.
- n = 12, one machine, three repositories for the runs. This shows the
  mechanism of the failure (a proxy or one path that moved the right
  way), not its frequency. It does not show that agents leave particular
  kinds slow: the data cannot separate that from what humans leave.

## Kinds with no hit, and a limit of the sample

- **Lock contention and false sharing: 0.** I scanned the titles of the
  pool candidates that were neither drawn nor benchmark instances for
  lock, mutex, contention, false sharing, atomic, rwlock, sharded,
  cache line and spin. The only hit was a false match ("blocked" in
  lmdeploy#4857, a Triton tile heuristic, classed `settings-backend`).
  `eval/tasks/SPLITS.md` names a false-sharing change among the held-out
  tasks (tokio#5829); it was not opened. The catalogue therefore has no
  evidence for this kind, and the measurement named for it below comes
  from general practice, not from a diff here.
- **Blocking call in async code: 0 as stated.** Nearest: `serialized`
  changes (a startup network call moved to a background task in Codex's
  TUI, a CPU-GPU sync removed in vLLM, a graph build overlapped with GPU
  compute in Ollama, sequential awaited writes made concurrent in
  Cline). No change replaced a blocking call inside an event loop.
- **Row-by-row dataframe work.** Not a kind of its own here: 4 `pyloop`
  changes, 2 of them inside pandas internals (to_numpy, a Cython
  per-element loop), 1 a dask bag, 1 a netbox view.
- **Uncoalesced memory access.** 4 `memory-access` changes: 2 name
  coalescing or vector loads directly (llm.c encoder float4, vLLM FP8
  abs-max) and 2 are access-pattern or cache-locality changes (llama.cpp
  pad_f32, DeepGEMM grouped GEMM). Of the 17 gpu-class rows, 5 are llm.c
  `dev/cuda` kernels and 3 are DeepGEMM, so they are not a sample of GPU
  practice.

## Kinds, with the cheap measurement for each

Each kind lists the change links. `Confirm with` is the cheapest
measurement I would run before writing the change; `Rule out when` is
what would make me drop the idea. None of these measurements was run for
this study: they are what the diffs, the pull-request bodies and the
T3.11 results point to, and they are proposals for the playbooks.

### repeated-work: Repeated identical work

n = 19: 13 landed, 5 reverted, 1 introduced a regression, 0 other; 18 human, 1 agent.

- **Looks like:** A value, object or decision is recomputed per call, per item or per request although its inputs have not changed: no cache or memo, loop-invariant not hoisted, setup repeated.
- **Confirm with:** Calls per distinct argument tuple on the workload (captured by the differential checks): hit potential = 1 - distinct/total. Counter on the suspected function.
- **Rule out when:** Distinct/total close to 1, or the profile share of the function is below the gain you need (Amdahl cap 1/(1-p)).
- **Discovery angle today:** Repeated identical work

Changes:

- [libuv-libuv-pr4512](https://github.com/libuv/libuv/pull/4512) (reverted; [revert](https://github.com/libuv/libuv/pull/4516)): Windows uv_os_getppid enumerated all processes per call; change caches result with uv_once; reverted in favour of #4514.
- [dask-dask-pr9267](https://github.com/dask/dask/pull/9267) (regression; [fix](https://github.com/dask/dask/pull/9413)): HighLevelGraph.cull stopped reusing culled_deps for non-Blockwise layers so dependencies are recomputed; fix #9413.
- [pypa-pip-pr13128](https://github.com/pypa/pip/pull/13128) (reverted; [revert](https://github.com/pypa/pip/pull/13333)): lru_cache on check_requires_python and early return in _log_skipped_link; reverted because skipped-link errors lost information.
- [pandas-dev-pandas-pr42761](https://github.com/pandas-dev/pandas/pull/42761) (reverted; [revert](https://github.com/pandas-dev/pandas/pull/45646)): nancorr recomputed per-column mean and variance for every column pair; change precomputes them once when no NaNs.
- [matplotlib-matplotlib-pr8626](https://github.com/matplotlib/matplotlib/pull/8626) (reverted; [revert](https://github.com/matplotlib/matplotlib/pull/9718)): Skips cla() on axes and spines that __init__ already cleared; reverted for breakage.
- [astropy-astropy-pr17461](https://github.com/astropy/astropy/pull/17461) (reverted; [revert](https://github.com/astropy/astropy/pull/18144)): ConfigItem value cached on the descriptor to avoid repeated ConfigObj access.
- [swe-fficiency-dask-dask-5501](https://datasets-server.huggingface.co/rows?dataset=swefficiency/swefficiency&config=default&split=test&offset=50&length=10) (landed): ensure_dict updates the result once per distinct dict id instead of once per layer.
- [swe-fficiency-dask-dask-5553](https://datasets-server.huggingface.co/rows?dataset=swefficiency/swefficiency&config=default&split=test&offset=50&length=10) (landed): meta_nonempty_dataframe builds one non-empty series per dtype and reuses it across columns.
- [django-django-pr21460](https://github.com/django/django/pull/21460) (landed): Sync/async test-function wrapping decided once at decoration time instead of per call.
- [nestjs-nest-pr10823-landed](https://github.com/nestjs/nest/pull/10823) (landed): Intl.DateTimeFormat constructed once instead of toLocaleString on every log line.
- [berriai-litellm-pr46617](https://github.com/BerriAI/litellm/pull/46617) (landed): Bundled provider and agent JSON re-read and re-validated per request; now cached.
- [unslothai-unsloth-pr13508](https://github.com/unslothai/unsloth/pull/13508) (landed): Prompt encoded once across stages; denoiser held on device between pipeline calls instead of re-uploaded.
- [huggingface-diffusers-pr11665](https://github.com/huggingface/diffusers/pull/11665) (landed): clear_cache stops re-walking all modules to count conv layers.
- [django-django-pr21459](https://github.com/django/django/pull/21459) (landed): CSP decorator checks coroutine-ness at decoration time.
- [python-jsonschema-jsonschema-pr896](https://github.com/python-jsonschema/jsonschema/pull/896) (landed): Subschema search done once into a cached dict instead of a deque walk per lookup.
- [sphinx-doc-sphinx-pr10337](https://github.com/sphinx-doc/sphinx/pull/10337) (landed): Publisher objects cached per file type instead of rebuilt per document.
- [langchain-ai-langchain-pr38073](https://github.com/langchain-ai/langchain/pull/38073) (landed): BaseTool.tool_call_schema subset model memoised and model_json_schema cached on the class.
- [astropy-astropy-pr20107](https://github.com/astropy/astropy/pull/20107) (landed): Unit converters cached by (from, to) pair.
- [t311-P7-django-22141](https://github.com/django/django/pull/22141) (landed, agent assisted): Many-to-many manager class attributes computed once and the DB router consulted only when needed; author disclosed Claude Code.

### unneeded-work: Work the result does not need

n = 16: 9 landed, 6 reverted, 1 introduced a regression, 0 other; 16 human, 0 agent.

- **Looks like:** The path does work whose output is dropped or could be skipped: no early exit or fast path, no pushdown, a branch computed though only one is used, a string formatted while logging is off, a stage run when its input is unchanged.
- **Confirm with:** Stub it out (the 'what if this were free?' cheap test) and time the workload; count how often the work's result is unused (rows scanned vs returned, calls whose output is discarded).
- **Rule out when:** Workload time with the work stubbed out barely moves, or the unused share is near zero. Also check behaviour: of the 6 reverted changes of this kind, 2 were reverted for broken behaviour (the miner's reading of the revert text) and 4 have no matched reason.
- **Discovery angle today:** none in the table (a cheap test, not an angle)

Changes:

- [pola-rs-polars-pr28494](https://github.com/pola-rs/polars/pull/28494) (regression; [fix](https://github.com/pola-rs/polars/pull/29427)): Feature PR; fix #29427 restores Select(len) projection pushdown that the change lost.
- [django-django-pr8417](https://github.com/django/django/pull/8417) (reverted; [revert](https://github.com/django/django/pull/8433)): Subquery ORDER BY clearing moved into the __in lookup; revert says it broke multi-column __in on Oracle.
- [sgl-project-sglang-pr26235](https://github.com/sgl-project/sglang/pull/26235) (reverted; [revert](https://github.com/sgl-project/sglang/pull/26358)): Full-vocab softmax skipped for argmax when topk==1; reverted for accuracy collapse.
- [pypa-pip-pr7132](https://github.com/pypa/pip/pull/7132) (reverted; [revert](https://github.com/pypa/pip/pull/7158)): Skip the index lookup when a pinned requirement is already installed.
- [rails-rails-pr51966](https://github.com/rails/rails/pull/51966) (reverted; [revert](https://github.com/rails/rails/pull/51985)): exists? answered from loaded records instead of issuing a query.
- [pola-rs-polars-pr29395](https://github.com/pola-rs/polars/pull/29395) (reverted; [revert](https://github.com/pola-rs/polars/pull/29503)): Float literal coerced to decimal once instead of casting the whole column.
- [python-mypy-pr20860](https://github.com/python/mypy/pull/20860) (reverted; [revert](https://github.com/python/mypy/pull/20894)): Stop type-checking bodies of deferred functions.
- [dask-dask-pr11899](https://github.com/dask/dask/pull/11899) (landed): cull returns the graph untouched when all keys are requested; per-key membership scans dropped.
- [python-attrs-attrs-pr1636](https://github.com/python-attrs/attrs/pull/1636) (landed): astuple skips the attrs-class lookup for elements of atomic types.
- [sympy-sympy-pr29921](https://github.com/sympy/sympy/pull/29921) (landed): ask() answers from recursive handlers before building SAT queries.
- [scipy-scipy-pr24660](https://github.com/scipy/scipy/pull/24660) (landed): Negative scale evaluated both f(x,y) and f(y,x) then np.where; apply_where evaluates each branch only where needed.
- [rust-lang-regex-pr228](https://github.com/rust-lang/regex/pull/228) (landed): Reverse suffix literal optimisation reintroduced, with a no-quadratic benchmark.
- [astral-sh-uv-pr20118](https://github.com/astral-sh/uv/pull/20118) (landed): Five-byte d.d.d fast path ahead of the general version parser.
- [tokio-rs-axum-pr3924](https://github.com/tokio-rs/axum/pull/3924) (landed): Path-tracking deserializer used only after a plain deserialize fails.
- [langchain-ai-langgraph-pr9248](https://github.com/langchain-ai/langgraph/pull/9248) (landed): Postgres delta-history walk starts at the target checkpoint instead of the newest one.
- [berriai-litellm-pr45252](https://github.com/BerriAI/litellm/pull/45252) (landed): Debug strings built only when verbose logging is on (printf-style arguments).

### codegen: Instruction-level inefficiency (native and kernel code)

n = 11: 7 landed, 3 reverted, 0 introduced a regression, 1 other; 10 human, 1 agent.

- **Looks like:** SIMD or vector helpers not used, a dependency chain on one accumulator, a byte-at-a-time loop where memchr exists, code alignment, instruction selection, MMA tile shape.
- **Confirm with:** IPC and instruction counts per work unit (perf stat; Linux only in v1) and a microbenchmark of the loop in isolation against a vectorised or lane-split variant.
- **Rule out when:** Loop is under a few percent of the profile, or the compiler already vectorises it (check compiler remarks or the disassembly).
- **Discovery angle today:** CPU hotspots (counters add the evidence; Linux only)

Changes:

- [triton-lang-triton-pr11985](https://github.com/triton-lang/triton/pull/11985) (reverted; [revert](https://github.com/triton-lang/triton/pull/12008)): bf16 to f32 extension rewritten as zext+shift so ptxas emits fewer instructions.
- [pola-rs-polars-pr29573](https://github.com/pola-rs/polars/pull/29573) (reverted; [revert](https://github.com/pola-rs/polars/pull/29589)): Few-group sum/mean/count updated in 4 lanes to break accumulation dependency chains.
- [ggml-org-llama-cpp-pr16522](https://github.com/ggml-org/llama.cpp/pull/16522) (reverted; [revert](https://github.com/ggml-org/llama.cpp/pull/16723)): ggml_vec_* fills and adds rewritten with GGML_F32_VEC SIMD helpers.
- [serde-rs-json-pr1160](https://github.com/serde-rs/json/pull/1160) (landed): Error-position search replaces a byte-at-a-time loop with memchr.
- [redis-redis-pr15741](https://github.com/redis/redis/pull/15741) (landed): lzf_compress aligned to a cache line to stop an x86-64 code-alignment effect (about 30% on RDB save per its comment).
- [redis-redis-pr15765](https://github.com/redis/redis/pull/15765) (landed): New faster LZF decompressor (lzf_d_2.c, 412 lines) used for RDB load.
- [serde-rs-json-pr1175](https://github.com/serde-rs/json/pull/1175) (landed): Faster \uXXXX escape parsing and surrogate handling.
- [zlib-ng-zlib-ng-pr2353](https://github.com/zlib-ng/zlib-ng/pull/2353) (landed): inflate refill issued behind the table lookup so load latency overlaps.
- [deepseek-ai-deepgemm-pr227](https://github.com/deepseek-ai/DeepGEMM/pull/227) (landed): Larger UMMA shape, fewer math threads, more registers per thread in sm100 MQA logits kernel.
- [karpathy-llm-c-adamw-97e61e2258](https://github.com/karpathy/llm.c/commit/97e61e2258c12a7090a0df2837110eb1144f7129) (landed): AdamW kernel loads each operand into registers once and uses a two-op lerp.
- `agent-C2-picolm` (not-submitted, agent written): T3.11 run, section in studies/pr-study/agents.md (see data.csv): Int8 dot-product Q4_K and Q6_K kernels, a persistent thread pool replacing per-matmul pthread_create and join, attention parallel across heads; T3.11 measured 7.4 times but output diverged.

### launch-fusion: Launch and dispatch overhead, unfused passes

n = 11: 7 landed, 3 reverted, 0 introduced a regression, 1 other; 9 human, 2 agent.

- **Looks like:** Several small kernels or passes where one would do, per-call setup, attribute interception on every launch, intermediates written to memory and read back, a launch grid sized for the worst case.
- **Confirm with:** Kernels (or dispatches) per step and mean duration; GPU idle gap fraction in the timeline; fixed per-call cost from timing a zero-size or one-element input.
- **Rule out when:** Kernel durations are far above the launch latency and the timeline has no gaps: fusion saves memory traffic at most, so measure bandwidth instead.
- **Discovery angle today:** GPU idle time

Changes:

- [triton-lang-triton-pr7770](https://github.com/triton-lang/triton/pull/7770) (reverted; [revert](https://github.com/triton-lang/triton/pull/7848)): Kernel.run looked up through __getattribute__ on every launch; replaced with a property.
- [vllm-project-vllm-pr52606](https://github.com/vllm-project/vllm/pull/52606) (reverted; [revert](https://github.com/vllm-project/vllm/pull/53294)): Fused prologue and recurrence kernels replace a Triton chain so per-chunk states are never written to HBM; reverted for an AMD CI image build break.
- [sgl-project-sglang-pr21734](https://github.com/sgl-project/sglang/pull/21734) (reverted; [revert](https://github.com/sgl-project/sglang/pull/23159)): Static FP8 activation quantisation as plain torch ops so inductor fuses it with neighbours; reverted for a memory fault.
- [internlm-lmdeploy-pr4861](https://github.com/InternLM/lmdeploy/pull/4861) (landed): Programmatic dependent launch for paged attention; prefill compress kernels fused into one.
- [internlm-lmdeploy-pr4853](https://github.com/InternLM/lmdeploy/pull/4853) (landed): Fused all-reduce plus residual plus RMSNorm and a router GEMM op (read the first files of a large diff).
- [sgl-project-sglang-pr43570](https://github.com/sgl-project/sglang/pull/43570) (landed): Causal-conv grid compacted for skewed prefill batches instead of a rectangular launch grid.
- [pytorch-ao-pr4798](https://github.com/pytorch/ao/pull/4798) (landed): Fused grouped RHT and NVFP4 CuteDSL kernels with a persistent scheduler (1,723-line new file; read the header).
- [karpathy-llm-c-layernorm-forward-466723bb06](https://github.com/karpathy/llm.c/commit/466723bb068a579423677e395f9e45f8f5e21024) (landed): Three layernorm kernels (mean, rstd, normalise) fused into one warp-per-row kernel.
- [karpathy-llm-c-matmul-forward-8822e7803e](https://github.com/karpathy/llm.c/commit/8822e7803ef91be8f47d35d95d5c6e590fb83ea3) (landed): cuBLASLt fuses the bias add into the GEMM; TF32 enabled where supported.
- [t311-P8-vllm-58737](https://github.com/vllm-project/vllm/pull/58737) (landed, agent written): Seven tiny PyTorch kernels over a one-element tensor replaced by one Triton kernel; Claude co-author trailers.
- `agent-C1-frames2py` (not-submitted, agent written): T3.11 run, section in studies/pr-study/agents.md (see data.csv): Two np.add.at scatters per event fused into one complex128 scatter plus a uint64 spill; T3.11 measured 1.03-1.08 times.

### copy-alloc: Needless copies, allocations and temporaries

n = 10: 5 landed, 0 reverted, 3 introduced a regression, 2 other; 8 human, 2 agent.

- **Looks like:** A full-size temporary where an in-place or out= form exists, a buffer allocated per call, repeated string concatenation, a deep copy in a hot path, a broadcast that is materialised, garbage-collector pressure.
- **Confirm with:** Allocations and bytes allocated per call (tracemalloc, allocator counters), peak resident memory, GC time share.
- **Rule out when:** Allocations per call are constant and small, or allocation time is a few percent of the profile. Two agent attempts at this kind (X1, X2) measured no gain or a loss.
- **Discovery angle today:** Allocation churn

Changes:

- [vllm-project-vllm-pr21146](https://github.com/vllm-project/vllm/pull/21146) (regression; [fix](https://github.com/vllm-project/vllm/pull/24128)): gc.freeze during CUDA graph capture; fix #24128 adds gc.collect() after unfreeze (garbage left uncollected cut throughput).
- [sgl-project-sglang-pr2786](https://github.com/sgl-project/sglang/pull/2786) (regression; [fix](https://github.com/sgl-project/sglang/pull/3008)): FP8 KV cache: cache_k / k_scale made a full-size copy even for scale 1.0; fix #3008 uses in-place div_ only when a scale is set.
- [hypothesisworks-hypothesis-pr4395](https://github.com/HypothesisWorks/hypothesis/pull/4395) (regression; [fix](https://github.com/HypothesisWorks/hypothesis/pull/4440)): dataclasses.asdict (deep copy) used in repr; fix #4440 iterates fields instead.
- [swe-fficiency-pydata-xarray-7472](https://datasets-server.huggingface.co/rows?dataset=swefficiency/swefficiency&config=default&split=test&offset=380&length=10) (landed): Chunk a non-dask variable before broadcast so set_dims does not materialise the full broadcast array.
- [pydantic-pydantic-ai-pr6485](https://github.com/pydantic/pydantic-ai/pull/6485) (landed): Streamed text deltas buffered in lists and joined once instead of concatenated per delta.
- [lz4-lz4-pr1158](https://github.com/lz4/lz4/pull/1158) (landed): calloc replaced by malloc for the tmp buffer and the buffer is kept for reuse across frames.
- [linkedin-liger-kernel-pr1468](https://github.com/linkedin/Liger-Kernel/pull/1468) (landed): Logits buffers reused across chunks with out= matmul and gradients written in place.
- [actix-actix-web-pr3261](https://github.com/actix/actix-web/pull/3261) (landed): HttpDate written straight into the buffer without an intermediate String.
- `agent-X1-frames2py` (not-submitted, agent written): T3.11 run, section in studies/pr-study/agents.md (see data.csv): np.where temporaries replaced by in-place masked np.negative; T3.11 measured 0.76-0.95 times (slower).
- `agent-X2-picolm` (not-submitted, agent written): T3.11 run, section in studies/pr-study/agents.md (see data.csv): Top-p sort buffer reused across tokens; stdout flushed every 16 tokens; T3.11 measured 0.98-1.01 times (no change).

### settings-backend: Settings, defaults, backend or kernel selection

n = 7: 5 landed, 1 reverted, 1 introduced a regression, 0 other; 5 human, 2 agent.

- **Looks like:** A flow-control window, buffer size, tile configuration, kernel backend or feature flag is wrong for the workload, or the build silently falls back to a slower implementation.
- **Confirm with:** Sweep the setting on the confirmed workload (perfproof measure --set, then confirm); log which backend or kernel actually ran.
- **Rule out when:** Flat response across the sweep. Only llama.cpp#29182 shows a sign change between machines (T3.11: faster on one iGPU, slower on others); of the 7 changes of this kind, it was reverted and vllm#12405 (a fallback to a slower kernel) regressed.
- **Discovery angle today:** Settings

Changes:

- [vllm-project-vllm-pr12405](https://github.com/vllm-project/vllm/pull/12405) (regression; [fix](https://github.com/vllm-project/vllm/pull/12434)): Build change made vLLM fall back to FA2 instead of FA3; fix #12434 is one CMake line.
- [actix-actix-web-pr1394](https://github.com/actix/actix-web/pull/1394) (landed): HTTP/2 client connection and stream flow-control windows set to 2 MiB and 1 MiB.
- [unslothai-unsloth-pr13600](https://github.com/unslothai/unsloth/pull/13600) (landed): MoE expert-cache flag emitted only in the resident-share band where it was measured faster.
- [internlm-lmdeploy-pr4857](https://github.com/InternLM/lmdeploy/pull/4857) (landed): Shape-keyed Triton tile and transposed-MMA configuration for compact blocked FP8 MoE.
- [t311-P3-cpython-118144](https://github.com/python/cpython/pull/118144) (landed): Default buffer size raised from 8 KiB to 128 KiB (fewer read and write calls).
- [t311-P4-llama.cpp-29182](https://github.com/ggml-org/llama.cpp/pull/29182) (reverted, agent assisted; [revert](https://github.com/ggml-org/llama.cpp/pull/29936)): Vulkan MoE matmul tile and pipeline chosen from n_per_expert instead of nei1; author disclosed an agent (opencode); reverted after slowdowns on other GPUs.
- [t311-P5-onnxruntime-32301](https://github.com/microsoft/onnxruntime/pull/32301) (landed, agent written): FP16 GEMM kernels registered only when an accelerated half-GEMM backend exists, ending a fallback to a slow path; opened by the Copilot agent.

### cheaper-primitive: A cheaper primitive for the same job

n = 7: 6 landed, 0 reverted, 0 introduced a regression, 1 other; 6 human, 1 agent.

- **Looks like:** A slow call is swapped for a faster equivalent: regex or format replaced by direct operations, split()[0] by partition, a generic Enumerable path by a specialised one.
- **Confirm with:** Microbenchmark the two primitives on representative input and multiply by call counts from the profile (an Amdahl bound).
- **Rule out when:** Profile share of the line is small: the typical gain here is a constant factor on a small share.
- **Discovery angle today:** CPU hotspots

Changes:

- [swe-fficiency-sympy-sympy-14772](https://datasets-server.huggingface.co/rows?dataset=swefficiency/swefficiency&config=default&split=test&offset=460&length=10) (landed): pow(a % p, e) % p replaced by three-argument modular pow: same job with a cheaper primitive, no change in algorithm structure.
- [psf-black-pr4739](https://github.com/psf/black/pull/4739) (landed): Line.__str__ replaces repeated string += with join.
- [rails-rails-pr58751-landed](https://github.com/rails/rails/pull/58751) (landed): Regex sub and strftime replaced by Time#iso8601, #to_s and a two-digit lookup table.
- [rails-rails-pr59068](https://github.com/rails/rails/pull/59068) (landed): Single-column Key gets direct each, all?, any?, map instead of the generic Enumerable path.
- [crewaiinc-crewai-pr3255](https://github.com/crewAIInc/crewAI/pull/3255) (landed): split(sep)[0] replaced by partition(sep)[0].
- [pygments-pygments-pr3184](https://github.com/pygments/pygments/pull/3184) (landed): Per-token loop hoists attribute lookups and rebuilds line assembly.
- [t311-P2-pandas-66238](https://github.com/pandas-dev/pandas/pull/66238) (closed-unmerged, agent written): strtoll replaced by fast_float parsing for integers; Claude Code footer on the body.

### serialized: Independent work run one after another

n = 5: 3 landed, 1 reverted, 0 introduced a regression, 1 other; 5 human, 0 agent.

- **Looks like:** A blocking call on the critical path, sequential awaits, GPU streams not overlapped, a CPU-GPU sync between steps, startup waiting on a network call.
- **Confirm with:** Wall time against CPU time (and off-CPU profile); on GPU, overlap between streams in the timeline; sum of stage times against end to end.
- **Rule out when:** Wall is close to CPU time and the timeline is busy: nothing to overlap. Overlap changes can fail at low concurrency (vllm#48223 was reverted).
- **Discovery angle today:** Waiting, not computing

Changes:

- [vllm-project-vllm-pr48223](https://github.com/vllm-project/vllm/pull/48223) (reverted; [revert](https://github.com/vllm-project/vllm/pull/52024)): Shared-expert work moved to a second GPU stream to overlap routed-expert kernel; reverted after race and low-concurrency loss.
- [openai-codex-pr17039](https://github.com/openai/codex/pull/17039) (landed): Rate-limit fetch moved off the startup path into a background task.
- [ollama-ollama-pr11863](https://github.com/ollama/ollama/pull/11863) (landed): Next-batch graph built on a goroutine while the GPU computes the current one.
- [cline-cline-pr4676](https://github.com/cline/cline/pull/4676) (landed): Sequential awaited storage writes replaced by Promise.all batches.
- [t311-P10-vllm-29957](https://github.com/vllm-project/vllm/pull/29957) (closed-unmerged): Async speculative decoding assumes all drafts accepted so the CPU sync between steps is skipped.

### memory-access: Memory access pattern and bandwidth

n = 4: 4 landed, 0 reverted, 0 introduced a regression, 0 other; 4 human, 0 agent.

- **Looks like:** Strided or scalar loads where vector or coalesced loads exist, loop order against memory layout, scheduling that evicts reusable data from L2.
- **Confirm with:** Achieved bandwidth divided by device peak (bytes moved over kernel time), sectors per request and L2 hit rate (Nsight Compute); for CPU, bytes per second against a memcpy baseline.
- **Rule out when:** Achieved bandwidth already within a few percent of peak, or the kernel is compute-bound (arithmetic intensity above the roofline ridge).
- **Discovery angle today:** none in the table (GPU idle time only)

Changes:

- [ggml-org-llama-cpp-pr30526](https://github.com/ggml-org/llama.cpp/pull/30526) (landed): pad_f32 innermost loop strode across dim 3 per element; rewritten row-wise with memset and memcpy.
- [vllm-project-vllm-pr58194-landed](https://github.com/vllm-project/vllm/pull/58194) (landed): Dynamic FP8 abs-max uses 16-element vector loads and one-sync block reduction.
- [deepseek-ai-deepgemm-pr168](https://github.com/deepseek-ai/DeepGEMM/pull/168) (landed): Removes the grouped-GEMM special case that forced the block grouping to kNumSMs (weights kept L2-resident, activations read once) so grouped GEMMs use the same candidate heuristic as the other GEMM types.
- [karpathy-llm-c-encoder-forward-cfccd821e6](https://github.com/karpathy/llm.c/commit/cfccd821e6c2e0c9496c3560cbbe1eb21288ada3) (landed): float4 loads and stores in the encoder forward kernel for coalescing.

### n-plus-1: Unbatched queries or remote calls

n = 5: 3 landed, 0 reverted, 0 introduced a regression, 2 other; 3 human, 2 agent.

- **Looks like:** One query or request per item where one batched call or one UPDATE or IN query would do.
- **Confirm with:** Queries (or remote calls) per request as a function of result size: fit the slope.
- **Rule out when:** Constant query count as N grows. Fewer queries is not enough: the T3.11 run X3 cut 19 queries to 13 and was slower.
- **Discovery angle today:** Database patterns

Changes:

- [saleor-saleor-pr11874](https://github.com/saleor/saleor/pull/11874) (landed): Four filtered UPDATE statements over orders merged into one UPDATE with CASE.
- [berriai-litellm-pr46412](https://github.com/BerriAI/litellm/pull/46412) (landed): SCIM group member lookups batched into chunked IN queries.
- [openhands-openhands-pr12105](https://github.com/OpenHands/OpenHands/pull/12105) (landed): One batch runtime request replaces one request per sandbox.
- `agent-C3-netbox` (not-submitted, agent written): T3.11 run, section in studies/pr-study/agents.md (see data.csv): Prefetch for primary_ip under fields= (2,052 queries to 10 per its log) and a per-request URL template cache; T3.11 measured 10.4 times on the fields path, 1.03-1.20 otherwise.
- `agent-X3-netbox` (not-submitted, agent written): T3.11 run, section in studies/pr-study/agents.md (see data.csv): select_related joins replaced prefetch queries (19 to 13 queries); T3.11 measured 0.58 and 0.71 times on the full list (slower).

### pyloop: Per-element interpreted loop where a bulk operation exists

n = 4: 1 landed, 1 reverted, 1 introduced a regression, 1 other; 4 human, 0 agent.

- **Looks like:** A Python (or per-element compiled) loop over rows or values, sometimes with a try/except per element, where numpy, SQL or a library call does the same in bulk.
- **Confirm with:** Profile share of the loop body and iterations per call; a microbenchmark of the bulk operation on the same data.
- **Rule out when:** The loop runs few iterations at workload sizes, or the bulk form needs the same per-element work in a callback.
- **Discovery angle today:** CPU hotspots

Changes:

- [pandas-dev-pandas-pr51227](https://github.com/pandas-dev/pandas/pull/51227) (reverted; [revert](https://github.com/pandas-dev/pandas/pull/51273)): ArrowExtensionArray.to_numpy(object): np.array(list(self)) replaced with bulk asarray on the non-null slice.
- [pandas-dev-pandas-pr56037](https://github.com/pandas-dev/pandas/pull/56037) (regression; [fix](https://github.com/pandas-dev/pandas/pull/64438)): Cython per-element loop with per-element try/except and overflow checks; fix #64438 vectorises with numpy (about 40x per its text).
- [swe-fficiency-dask-dask-10356](https://datasets-server.huggingface.co/rows?dataset=swefficiency/swefficiency&config=default&split=test&offset=50&length=10) (landed): 624 randint calls per state in a Python comprehension replaced by numpy Generator draws.
- [t311-P6-netbox-21954](https://github.com/netbox-community/netbox/pull/21954) (closed-unmerged): Prefix utilisation counted distinct hosts in SQL instead of building a netaddr IPSet row by row; panel loaded lazily.

### underparallel: Serial where parallel is available

n = 4: 3 landed, 1 reverted, 0 introduced a regression, 0 other; 3 human, 1 agent.

- **Looks like:** Work on one thread, one thread per row, or fewer tasks than cores; a batch API that does not fan out.
- **Confirm with:** Speedup against thread count (1, 2, 4, ... N) and core utilisation; for GPU, threads per row.
- **Rule out when:** Utilisation already near all cores, or speedup flat in thread count (memory-bound). Memory and ordering cost grow with threads (pandas#64347).
- **Discovery angle today:** Settings (thread counts)

Changes:

- [pola-rs-polars-pr24980](https://github.com/pola-rs/polars/pull/24980) (reverted; [revert](https://github.com/pola-rs/polars/pull/25664)): take_unchecked split into index chunks when threads exceed columns.
- [karpathy-llm-c-softmax-forward-8386e5393c](https://github.com/karpathy/llm.c/commit/8386e5393c61ec2faf706f3040e68127c2f08398) (landed): Softmax row reductions moved from a single-thread loop to warp-shuffle and shared-memory reductions.
- [karpathy-nanochat-pr383](https://github.com/karpathy/nanochat/pull/383) (landed): batch_encode runs texts in parallel with rayon and releases the GIL.
- [t311-P1-pandas-64347](https://github.com/pandas-dev/pandas/pull/64347) (landed, agent written): Parallel read_csv across chunks; Claude co-author trailers on the commits; correctness and memory problems found in review and after merge.

### quadratic: Quadratic or worse algorithm

n = 3: 2 landed, 1 reverted, 0 introduced a regression, 0 other; 3 human, 0 agent.

- **Looks like:** Pairwise comparison, a full product of buckets, a big-integer power then a modulus, repeated rescans.
- **Confirm with:** Time against input size at n, 2n, 4n (log-log slope clearly above 1) -- perfproof measure --sizes.
- **Rule out when:** Slope near 1 over the workload's real size range.
- **Discovery angle today:** Growth with input size

Changes:

- [pandas-dev-pandas-pr49404](https://github.com/pandas-dev/pandas/pull/49404) (reverted; [revert](https://github.com/pandas-dev/pandas/pull/50848)): Series.replace on categoricals looped over replace values with list.index and a full codes pass each; replaced by Series.replace on categories.
- [kludex-uvicorn-pr3184](https://github.com/Kludex/uvicorn/pull/3184) (landed): Pairwise directory-parent comparison replaced by sorted pass with set lookups.
- [pylint-dev-pylint-pr10881](https://github.com/pylint-dev/pylint/pull/10881) (landed): Cartesian product of equal-hash chunk buckets capped; read from pulls/10881/files because the manifest commit lists tests only.

### eager-init: Eager startup work

n = 4: 4 landed, 0 reverted, 0 introduced a regression, 0 other; 4 human, 0 agent.

- **Looks like:** Heavy imports or setup at module load that most runs never use.
- **Confirm with:** python -X importtime and startup wall time with the module stubbed; modules imported per run.
- **Rule out when:** The import is on every run's path anyway, or cumulative import time is a few milliseconds.
- **Discovery angle today:** Startup and imports

Changes:

- [pypa-pip-pr14054](https://github.com/pypa/pip/pull/14054) (landed): pylock utils import moved inside the one branch that uses it.
- [browser-use-browser-use-pr3764](https://github.com/browser-use/browser-use/pull/3764) (landed): openai and reportlab imports moved into the functions that use them (about 800 ms per its comment).
- [huggingface-smolagents-pr253](https://github.com/huggingface/smolagents/pull/253) (landed): Optional dependencies imported lazily at the call site.
- [langchain-ai-langchain-pr30769](https://github.com/langchain-ai/langchain/pull/30769) (landed): Package __init__ files resolve names through a lazy __getattr__.

### repeated-io: Repeated I/O, syscalls and boundary crossings

n = 3: 1 landed, 1 reverted, 1 introduced a regression, 0 other; 3 human, 0 agent.

- **Looks like:** A file copied and hashed again, per-chunk callbacks into Python, filesystem probes per module, a write per token.
- **Confirm with:** Syscalls and bytes read per operation (strace -c on Linux; fs_usage or dtruss on macOS) against the minimum needed.
- **Rule out when:** Syscall count per operation is already near one, or I/O is served from page cache and costs little.
- **Discovery angle today:** Waiting, not computing (partly)

Changes:

- [python-mypy-pr5465](https://github.com/python/mypy/pull/5465) (regression; [fix](https://github.com/python/mypy/pull/5556)): Per-module find_module filesystem probes for every suppressed import on each load; fix #5556 skips them in fine-grained mode.
- [ollama-ollama-pr5898](https://github.com/ollama/ollama/pull/5898) (reverted; [revert](https://github.com/ollama/ollama/pull/6323)): When the digest is known, link the existing blob (NewLayerFromLayer) instead of copying and hashing the file again.
- [matplotlib-matplotlib-pr32064](https://github.com/matplotlib/matplotlib/pull/32064) (landed): FreeType reads font data from a memory map instead of a Python read callback per chunk.

### data-structure: Wrong data structure or algorithm family

n = 3: 3 landed, 0 reverted, 0 introduced a regression, 0 other; 3 human, 0 agent.

- **Looks like:** List membership or index() where a set or dict fits; a general engine where a dedicated matcher exists.
- **Confirm with:** Profile share of membership or lookup calls; time against size n; microbenchmark with the candidate structure.
- **Rule out when:** Collection sizes are small at workload scale (under a few dozen elements).
- **Discovery angle today:** Growth with input size, CPU hotspots

Changes:

- [pypa-pip-pr14122](https://github.com/pypa/pip/pull/14122) (landed): Supported tags converted to a set for per-wheel membership tests.
- [burntsushi-ripgrep-pr1238](https://github.com/BurntSushi/ripgrep/pull/1238) (landed): Alternations of literals routed to a dedicated multi-literal searcher instead of the regex engine.
- [browser-use-browser-use-pr4587](https://github.com/browser-use/browser-use/pull/4587) (landed): List membership tests on snapshot rare-boolean data converted to sets; 2 s timeout on pending-request check.

### over-fetch: Over-fetching and unbounded result size

n = 1: 0 landed, 1 reverted, 0 introduced a regression, 0 other; 1 human, 0 agent.

- **Looks like:** A query or API returns more rows or bytes than the caller uses, or its cost scales with cardinality that can be bounded.
- **Confirm with:** Rows or bytes returned against rows used; query time against cardinality (here: number of keys).
- **Rule out when:** Returned size is already bounded or fully used.
- **Discovery angle today:** Database patterns

Changes:

- [berriai-litellm-pr41293](https://github.com/BerriAI/litellm/pull/41293) (reverted; [revert](https://github.com/BerriAI/litellm/pull/43596)): SQL split into key-free rollups plus bounded top-N keys; revert text says the query slows as key count grows.

### contention: Lock contention and false sharing

n = 0: 0 landed, 0 reverted, 0 introduced a regression, 0 other; 0 human, 0 agent.

- **Looks like:** Threads wait on a shared lock, or write to different data on one cache line.
- **Confirm with:** Lock wait time, wall against CPU time per thread, speedup against thread count.
- **Rule out when:** No change in speedup when the lock is removed or sharded.
- **Discovery angle today:** Waiting, not computing

No change in the sample is primarily of this kind (see 'Kinds with no hit').

### mixed: No single primary kind

n = 1: 0 landed, 1 reverted, 0 introduced a regression, 0 other; 1 human, 0 agent.

- **Looks like:** A large squash that bundles several unrelated changes, so no one mechanism takes the largest share.
- **Confirm with:** Not applicable: split it by file and classify the parts.
- **Rule out when:** Not applicable.
- **Discovery angle today:** none

Changes:

- [vllm-project-vllm-pr48597](https://github.com/vllm-project/vllm/pull/48597) (reverted; [revert](https://github.com/vllm-project/vllm/pull/49768)): 29-file squash (new skinny GEMM kernel, MTP and speculative config, fused all-reduce norm, cute utilities); no single primary kind.

### none: No inefficiency identifiable in the diff

n = 8: 2 landed, 6 reverted, 0 introduced a regression, 0 other; 8 human, 0 agent.

- **Looks like:** The change is a bug fix, a behaviour change, a benchmark harness change, or removes an optimisation.
- **Confirm with:** Not applicable.
- **Rule out when:** Not applicable.
- **Discovery angle today:** none

Changes:

- [pydata-xarray-pr4432](https://github.com/pydata/xarray/pull/4432) (reverted; [revert](https://github.com/pydata/xarray/pull/4434)): Bug fix for optimize(); deletes a dict-filter comprehension, no inefficiency named in the diff.
- [langchain-ai-langchain-pr8610](https://github.com/langchain-ai/langchain/pull/8610) (reverted; [revert](https://github.com/langchain-ai/langchain/pull/11143)): Loop turned into comprehension and results concatenated; no complexity change.
- [berriai-litellm-pr9387](https://github.com/BerriAI/litellm/pull/9387) (reverted; [revert](https://github.com/BerriAI/litellm/pull/9388)): Bug fix for timedelta latencies in router; no inefficiency in the diff.
- [nestjs-nest-pr13056](https://github.com/nestjs/nest/pull/13056) (reverted; [revert](https://github.com/nestjs/nest/pull/13370)): Adds an undefined-token error check; no inefficiency removed.
- [triton-lang-triton-pr9204](https://github.com/triton-lang/triton/pull/9204) (reverted; [revert](https://github.com/triton-lang/triton/pull/9229)): Deletes the moveUpTranspose scheduling pass; removes an optimisation rather than an inefficiency.
- [astral-sh-ruff-pr23378](https://github.com/astral-sh/ruff/pull/23378) (reverted; [revert](https://github.com/astral-sh/ruff/pull/23955)): Removes the NoReturn shortcut predicate; removes an optimisation, no inefficiency removed.
- [ollama-ollama-pr17998](https://github.com/ollama/ollama/pull/17998) (landed): Bug fix for Metal GPU timeouts when loading from slow storage (adds a CPU pre-read); also drops one redundant forced evaluation, which is not the change's purpose.
- [deepseek-ai-deepgemm-pr68-landed](https://github.com/deepseek-ai/DeepGEMM/pull/68) (landed): Benchmark harness change (L2 flush size, power-limited mode); changes how kernels are timed, not the kernels.


## Not verified

- Classification is one reader's judgement from the head of each diff,
  with no second rater and no check of inter-rater agreement.
- 56 of 120 diffs were read only in part (see
  `partly_read`). The primary kind of those rests on the first lines of
  the filtered source diff and on the change's own title or description.
- The 5 `swe-fficiency` rows were read from the upstream pull request
  (`pulls/N/files`, with N taken from the instance id) and not compared
  with the benchmark dataset's reference patch. Their `diff_url` is the
  dataset row the manifest cites.
- pylint#10881: the manifest's change commit lists only a test file, so
  that diff was read from `pulls/10881/files`.
- The effects named in the notes (speedups, "about 30%", "about 800 ms",
  "40x") are as stated in the upstream text or in T3.11. None was
  re-run.
- The measurements per kind were not run.
- GPU kernels were read, not built or run; no GPU host was used.
