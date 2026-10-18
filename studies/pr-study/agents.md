# Unaided agents asked to make code faster

Task T3.11, part B. Fresh Claude Code and Codex sessions, given no
perfproof and no skill, were asked to make something faster or explain
why it was slow, in the repositories of the T3.10 hand-runs. Each run
is scored on what the agent claimed against my own repeated timing and
the project's tests.

**My timings are descriptive A/B comparisons, not calibrated verdicts.**
They alternate old and new for a fixed number of rounds on one loaded
laptop and report medians and the per-round spread. They have no
interval, no stopping rule and no minimum effect. They can show that a
claim did or did not reproduce here; they can't say KEEP or REJECT.

## Setup

Sources for the session rows are the run logs, timing JSON and probe
scripts under the gitignored `scratch/pr-study/`. They are not in this
PR; a reader cannot re-open them. Some numbers have no surviving
record at all, because an output was not saved or a later run
overwrote it. Each of those is labelled "no surviving record" below.
The measured picolm and NetBox timings, C1, the X1 100k and 1M rows
and both X1 repeats survive as JSON.

- **Machine:** M4 MacBook Pro, 10 cores, macOS. Other sessions were
  working on the same machine (NetBox containers, Python test suites,
  Rust builds), so the 1-minute load average is recorded next to every
  number. Agent runs and timings were meant to start below 20; C1 started at 23.2 and one X1 repeat ran at 20.2–33.5, and both are marked.
- **Clones:** one fresh clone per run under the gitignored
  `scratch/pr-study/runs/`, with pushing disabled
  (`git remote set-url --push origin DISABLED-no-push`). Each clone was
  set up as a developer's checkout would be:
  - frames2py at `9e35f5f`, from `~/code/frames2py`, with `uv sync`;
  - picolm at `4914f5b`, from `~/code/picolm`, with the TinyLlama 1.1B
    Q4_K_M model symlinked into `models/`;
  - NetBox at `1d270e5` (4.7.3), from `netbox-community/netbox`, with a
    virtualenv, `configuration.py`, collected static files, and local
    Postgres 17 and Redis 7 containers on their default ports holding
    the NetBox 4.7 demo data (72 devices).
- **Claude Code** 2.1.291, default model (the run logs report
  `claude-opus-5-5`):
  `claude -p --safe-mode --permission-mode auto --output-format stream-json --verbose`,
  prompt on stdin. `--safe-mode` is needed: a probe showed that a
  session started inside this repository's `scratch/` otherwise loads
  perfproof's `CLAUDE.md`, `CLAUDE.local.md` and the user's global
  `~/.claude/CLAUDE.md`, which would not be an unaided agent. Safe mode
  also drops a repository's own `CLAUDE.md`; for NetBox, whose
  `AGENTS.md` a user would normally get, it was passed back with
  `--append-system-prompt`.
- **Codex** 0.163.0:
  `codex exec -m gpt-5.6-luna -s workspace-write --json`. Codex reads the
  clone's own `AGENTS.md`; the user's `~/.codex/AGENTS.md` is empty.
- **Cost** is what the CLI reported at list price. These runs used
  subscription quota, not paid compute.
- **Scoring each run:**
  - *Claimed* and *measured*: from the session log (`logs/<id>.jsonl`).
  - *Faster?*: `harness/ab.py` alternates the base clone and the run's
    clone (AB, BA, AB, ...) for 10 rounds (6 for picolm `-j 1`). Ratio = base time / new
    time, so above 1 means the change is faster. Reported: the median
    of per-round ratios, its range, and how many rounds the change won.
  - *Same output?*: every benchmark round hashes the program's output
    (frames2py frames, picolm's generated text, NetBox's JSON body) and
    the two arms' digests are compared.
  - *Tests*: the project's suite on the changed clone, against the same
    suite on the base.
  - *Beyond the ask*: every file in the diff that the prompt did not
    call for.

My workloads are written outside the repositories, so an agent that
edits a repository's benchmarks can't change them. They are throwaway
probes in the gitignored `scratch/pr-study/harness/` and are not
committed; each is described with its run below.

## Summary

Ratio is base time over new time from my alternated rounds, so above 1
means the agent's change is faster. "Faster" counts rounds the change
won out of 10. Full tables, loads and the workload for each number are
in the run's section.

| Run | Repo, prompt | Agent claimed | Agent measured | My ratio (rounds won) | Same output | Tests | Beyond the ask | Cost |
|---|---|---|---|---|---|---|---|---|
| [C1](#c1-claude-code-frames2py-make-voxelgrid-accumulation-faster) | frames2py, make VoxelGrid faster | Mixed: 1.1–1.34x large batches, 0.72–0.85x small; would not merge | Interleaved, in-process, load 18–38 | 1.03–1.08 (6–10/10); small-batch slowdown not reproduced | yes | 2239 pass (base 2237 + 2 new) | one test file | $2.40, 947 s |
| [X1](#x1-codex-frames2py-make-voxelgrid-accumulation-faster) | frames2py, same prompt | +9% | 7 reps each, not alternated, loaded machine | **0.76–0.95 (0–3/10): slower** | yes | 2237 pass | none | 136 s |
| [C2](#c2-claude-code-picolm-make-token-generation-faster) | picolm, make generation faster | 6–7x; output no longer identical (disclosed) | Alternated binaries, 5 rounds | 7.39 at 4 threads, 6.30 at 1 (10/10, 6/6) | **no**: text diverges after 14–293 chars | none exist; builds | none | $3.31, 2338 s |
| [X2](#x2-codex-picolm-make-token-generation-faster) | picolm, same prompt | "Faster" | nothing run (cause unclear, see run) | 0.98–1.01 (4–6/10): no change | yes | none exist; builds | output flushed in 16-token bursts | 51 s |
| [C3](#c3-claude-code-netbox-make-the-device-list-api-faster) | NetBox, make device list API faster | None: session ended with the suite still running | Own seeded 2,090 devices, back to back twice | 10.37 on `?fields=` with primary IPs (10/10); 1.03–1.20 otherwise | yes | 4525 OK on six API modules; full suite not run | URL cache touches every endpoint; 2 containers left | $2.49, 1489 s |
| [X3](#x3-codex-netbox-make-the-device-list-api-faster) | NetBox, same prompt | "Faster device list API" | nothing run; sandbox blocked Postgres, reported as "unavailable" | **0.58 and 0.71 full list (0/10): slower**; brief 1.01 and 0.78; 1.57 on `?fields=` large copy | yes | **fails**: query count 19 to 13 trips a baseline test | none | 80 s |
| [C4](#c4-claude-code-frames2py-why-is-engineingest-slow-with-the-stacked_histogram-kernel) | frames2py, why is ingest slow | Publishing dominates: 105 MiB frames, page faults; `add.at` ~5% | Own stage timers | Four numbers rechecked: same direction, smaller effects; shares and ranking not rechecked | n/a | n/a | nothing changed | $0.66, 106 s |

Claude Code runs cost $8.86 in total at list price. Codex reports
tokens, not cost: 2.9M input tokens (2.8M cached) and 20k output tokens
over three runs.

## Runs

### C1. Claude Code, frames2py: "make VoxelGrid accumulation faster"

- **Session:** 947 s wall, 41 turns, $2.40, started at load 23.2 and
  ended at 29.2 (`logs/C1-f2p.*`). The session stopped partway: its
  last message says the account's usage limit was reached.
- **What it did:** profiled, tried trimming the arithmetic (about 10%,
  which it judged to be within noise), then replaced the two
  `np.add.at` scatter calls per event with one scatter into a
  complex128 array, both parts side by side. Because float64 is exact
  only up to 2^53, it added a lazily allocated uint64 "spill" that
  keeps the documented exact-modulo-2^64 behaviour. It wrote two new
  tests against the repository's independent oracle and checked them by
  breaking its own code two ways (both mutants failed the tests).
- **What it measured:** an interleaved old-versus-new comparison inside
  one process, per-thread CPU time, over sensor sizes, batch sizes and
  bin counts, at load 18–38. It reported 1.1–1.34x for 100k and 1M
  event batches and 0.72–0.85x (slower) for 10k batches.
- **What it claimed:** the speedup is mixed and unconfirmed, and it
  would not merge yet. It listed what was left: re-run on a quiet
  machine, a size cutoff if small batches really are slower, the full
  suite, the slow wraparound tests, and docs. It also said memory use is
  unchanged.
- **My timing** (workload: 4M events at one per microsecond, half
  uniform and half clustered batches, `VoxelGrid(bins=5, bin_us=50_000)`,
  median of 5 passes per round, 10 rounds; output digest over frames
  read every third batch and at the end):

  | Batch | Sensor | Load | Base median | C1 median | Ratio (range) | C1 faster |
  |---|---|---|---|---|---|---|
  | 10k | 640x480 | 3.9–5.6 | 80.1 ms | 84.7 ms | 1.03 (0.79–1.10) | 6/10 |
  | 200k | 640x480 | 5.6–5.8 | 69.5 ms | 65.2 ms | 1.08 (1.01–1.18) | 10/10 |
  | 100k | 1280x720 | 5.7–7.4 | 87.9 ms | 86.6 ms | 1.06 (0.91–1.25) | 7/10 |
  | 1M | 1280x720 | 7.2–7.3 | 38.4 ms | 35.6 ms | 1.08 (0.95–1.15) | 9/10 |

  Output was identical to the base in every configuration. The large
  batch gain reproduced in direction but is smaller than C1's best case
  (C1's 1.34x was at 15 bins; I ran 5). The small-batch slowdown did not
  reproduce here: the median ratio favours C1 slightly, while the ratio
  of medians favours the base, which is what noise looks like. I
  also saw the frames2py base time swing widely between sessions (no
  surviving record of the figures), and did not isolate the cause.
- **Tests:** full suite 2239 passed, 320 skipped (the base's 2237 plus
  C1's two), at load ~13. The slow wraparound test C1 had left running
  passed (1 test, 619 s).
- **Memory:** two uint64 planes became one complex128 plane, the same
  16 bytes per pixel per bin; the spill arrays add more only when a
  plane nears 2^53. Not measured. Its own list asks to document the
  extra arrays, which sits oddly with "memory is unchanged".
- **Beyond the ask:** one new test file. Nothing committed. It kept a
  backup of the source file in `/tmp` while mutating it.

### C2. Claude Code, picolm: "make token generation faster"

- **Session:** 2338 s wall, 48 turns, $3.31, load 7.4 at start and 5.9
  at the end.
- **What it did:** profiled (about 55% in the Q4_K matmul, 25% in a
  Q6_K matmul with no SIMD, 15% creating and joining threads ~150 times
  per token), then: quantized the activation vector to int8 once per
  matmul and used integer dot-product instructions for Q4_K and Q6_K
  rows, with a fallback for CPUs without them; a persistent spinning
  thread pool; attention parallel across heads; and a pre-filter before
  the top-p sort. Six files, all in `picolm/`.
- **What it measured:** old and new binaries alternately, 5 rounds,
  medians, load ~9: greedy `-j 4` 14.0 to 98.7 tok/s, `-j 1` 7.3 to
  44.3, default sampling ~12 to ~79, ~1.5k context 12.7 to 69.9 (one
  round, timed out).
- **What it claimed:** 6–7x faster. It said plainly that greedy output
  is no longer identical because activations are quantized, reported
  98% agreement on the next token over 400 positions and perplexity
  77.37 to 76.93, kernels matching the old path to about 1e-8, identical
  output across thread counts, and what it could not test (Linux GCC,
  MSVC, real Pi hardware).
- **My timing** (greedy, 96 tokens from a fixed story prompt, seconds
  per generated token from picolm's own generation timer, one run per
  round, 10 rounds; both binaries rebuilt with `make native`):

  | Threads | Load | Base | C2 | Ratio (range) | C2 faster |
  |---|---|---|---|---|---|
  | `-j 4` | 7.8–11.4 | 77.4 ms/token (12.9 tok/s) | 10.6 ms/token (94 tok/s) | 7.39 (6.31–8.07) | 10/10 |
  | `-j 1` | 6.6–9.2 | 134.7 ms/token | 21.2 ms/token | 6.30 (6.18–6.45) | 6/6 |

  The speedup reproduced. **The output did not:** on three prompts the
  greedy text diverged from the base after 293, 14 and 155 characters.
  On "The capital of France is" the base continues with a list of
  capitals; C2 continues with the alphabet. Both are plausible outputs
  of a 1.1B model, and I did not check the perplexity claim. A
  `make pi` build of C2's code (no dot-product instructions) produced
  exactly the same text as its native build, as claimed.
- **Tests:** picolm has none. Its CI only builds, for macOS, Linux,
  Windows (MSVC) and a static Pi cross-compile
  (`.github/workflows/build.yml`). I built natively and with `make pi`;
  the MSVC and cross-compile builds were not tried.
- **Beyond the ask:** nothing outside the generation path; it suggested
  a Makefile change for the Pi target instead of making it. Nothing
  committed.

### X1. Codex, frames2py: "make VoxelGrid accumulation faster"

- **Session:** 136 s wall, started at load 10.0; 1,168,603 input tokens
  (1,126,784 cached) and 9,164 output tokens as the CLI reported. Codex
  reports no cost. The user's default model in `~/.codex/config.toml`
  is rejected for this login, so the model was `gpt-5.6-luna`.
- **What it did:** read the kernel and the repository's benchmark
  matrix, timed the parts of `accumulate()` in isolation, tried and
  dropped a combined single scatter (fast in isolation, slower end to
  end in its own check), and settled on a two-line change: negate the
  OFF-event weights in place with `np.negative(..., where=off, out=...)`
  instead of building two `np.where` results.
- **What it measured:** one comparison inside one process, 7
  repetitions of each variant run back to back, not alternated, at 1M
  events and 1280x720: 25.37 ms with `np.where` against 23.17 ms in
  place.
- **What it claimed:** the accumulation core is roughly 9% faster on 1M
  events; 120 temporal contract tests pass, 2 skipped.
- **My timing** (same workload as C1):

  | Batch | Sensor | Load | Base median | X1 median | Ratio (range) | X1 faster |
  |---|---|---|---|---|---|---|
  | 10k, first pass | 640x480 | 11.3–15.2 | no surviving record (overwritten by the repeat) | | 0.77 (0.62–0.83), as noted at the time | 0/10 |
  | 200k, first pass | 640x480 | 15.0–17.6 | no surviving record (overwritten by the repeat) | | 0.78 (0.72–0.91), as noted at the time | 0/10 |
  | 100k | 1280x720 | 17.3–19.7 | 113.9 ms | 140.2 ms | 0.83 (0.70–1.02) | 1/10 |
  | 1M | 1280x720 | 19.4–21.7 | 49.7 ms | 55.4 ms | 0.95 (0.78–1.58) | 3/10 |
  | 10k, repeat | 640x480 | 15.2–21.4 | 102.7 ms | 139.1 ms | 0.76 (0.64–0.98) | 0/10 |
  | 200k, repeat | 640x480 | 20.2–33.5 | 102.1 ms | 126.7 ms | 0.81 (0.64–0.91) | 0/10 |

  **The change is slower here, not faster.** Output was identical. The
  virtualenvs match (numpy 2.4.6, Python 3.11.14, the same 19
  packages). The mechanism, timed alone in one process with the order
  alternated over 15 rounds (a one-off probe; no surviving record of its
  output, so these figures are as I noted them): masked in-place `np.negative` took 42 µs
  against 16 µs for `np.where` at 10k elements, 2123 against 812 µs at
  200k, and 11364 against 6921 µs at 1M, so it is 1.6–2.6x slower for
  this step on this machine. X1's +9% came from one unalternated
  comparison of 7 repetitions on a loaded machine. The load during my
  runs was high and rising (up to 33.5 on the last repeat), so these
  ratios are not precise, but every run went the same way.
- **Tests:** X1 ran the 120 temporal contract tests and not the full
  suite. Its first attempt, with the system Python, exited with code 4;
  it then used `uv run` with a cache under `/tmp`. Full suite run by me:
  2237 passed, 320 skipped, the same as the base.
- **Beyond the ask:** nothing; two lines in one file, a comment added.

### C4. Claude Code, frames2py: "why is Engine.ingest slow with the stacked_histogram kernel?"

- **Session:** 106 s wall, 13 turns, $0.66, started at load 17.2.
- **What it did:** read the kernel, engine, publisher and docs, then
  timed each stage with its own hooks around `ingest()`. It changed no
  code.
- **What it claimed** (1280x720, `StackedHistogram(bins=15, bin_us=3333)`,
  100k-event calls): most time goes to publishing, not accumulating.
  Reading into the published frame took 64–74% of the time when
  publishing every call. Each frame is 105.5 MiB of fresh memory, so
  most of the cost is page faults: 12.6 ms into a fresh array against
  2.45 ms into a reused one. `np.add.at` is about 5% (0.67 ms per call),
  so swapping it for `bincount` would not help. Publishing every 16 ms
  instead of every call went from 7.3M to 16.3M events/s. It ranked
  options, named the immutability contract that makes buffer reuse a
  design change, and warned that its hooks add overhead so only the
  percentages should be compared.
- **What I checked** (`harness/c4_probe.py`, base code, load 10.7–10.8;
  the script survives in scratch but its output was not saved, so the
  table below is as I noted it at the time, no surviving record;
  40 calls of 100k events at one per microsecond, 8 alternated rounds
  per setting):

  | Claim | C4 | Mine |
  |---|---|---|
  | Frame size | 105.5 MiB | 105.5 MiB |
  | Read into fresh vs reused frame | 12.6 vs 2.45 ms | 10.1 vs 4.66 ms |
  | `np.add.at` per 100k-event call | 0.67 ms | 0.65 ms (accumulate whole: 1.21 ms) |
  | Publish every call vs every 16 ms | 7.3M vs 16.3M events/s | 6.29M vs 8.93M events/s (ranges 5.68–7.44, 6.48–12.1) |

  The explanation held in direction on the four numbers I rechecked.
  C4's percentage shares and its ranking of options were not rechecked.
  The sizes of the effects are smaller than C4 reported (the 16 ms gain 1.4x against its
  2.2x), and the 16 ms result depends
  on how long each call takes on the machine, since the interval is in
  wall time. A diagnosis without a change can't be scored for speed or
  tests; whether its ranking is right is not established here.
- **Beyond the ask:** nothing changed.

### X2. Codex, picolm: "make token generation faster"

- **Session:** 51 s wall, started at load 11.0; 354,861 input tokens
  (324,736 cached), 3,844 output tokens.
- **What it did:** read the generation loop, model, sampler and
  Makefile, and changed three things: reuse the top-p sort buffer across
  tokens instead of allocating it per token, flush stdout every 16
  tokens instead of every token, and free the sampler at exit.
- **What it measured:** nothing. It built the code with `make native`
  and never ran the model. The clone's `models/` symlink to the
  TinyLlama file was created at 19:27 on 16 October, before this run
  (the link's mtime), so the file was reachable by path. Codex's
  `workspace-write` sandbox also blocked X3's localhost connection, and
  I did not test whether it lets a process read a file through a symlink
  that points outside the workspace. So "never ran the model" may be the
  agent's choice or the sandbox; this run is weak evidence of agent
  behaviour either way.
- **What it claimed:** "Made token generation faster" by those changes;
  build verified.
- **My timing** (same harness as C2; both binaries rebuilt):

  | Mode | Load | Base | X2 | Ratio (range) | X2 faster |
  |---|---|---|---|---|---|
  | Greedy, `-j 4` | 14.8–20.6 | 137.7 ms/token | 139.2 ms/token | 0.98 (0.95–1.04) | 4/10 |
  | Sampling `-t 0.8`, `-j 4` | 16.8–22.7 | 140.8 ms/token | 139.7 ms/token | 1.01 (0.85–1.08) | 6/10 |

  No measurable change, and outputs identical in both modes. The buffer
  reuse never runs under greedy decoding. A 32,000-entry allocation and
  one flush per token are microseconds against 140 ms per token. The
  base itself ran at 77 ms per token at load 8–11 and 138 ms at load
  15–23, a 1.8x difference between sessions whose cause I did not isolate.
- **Tests:** picolm has none; both builds succeed.
- **Beyond the ask:** flushing every 16 tokens changes behaviour a user
  sees: interactive output now arrives in bursts of 16 tokens. The
  sampler cleanup is unrelated hygiene. Nothing committed.

### C3. Claude Code, NetBox: "make the device list API faster"

- **Session:** 1489 s wall, 64 turns, $2.49, started at load 8.2.
  NetBox's `AGENTS.md` was passed with `--append-system-prompt`.
- **What it did:** ignored the configured checkout (venv,
  `configuration.py`, Postgres and Redis on their default ports, all
  visible to it) and built its own: two new Docker containers, a venv in
  its scratchpad, its own config modules and a seeded dataset of 2,090
  devices with primary IPs. It found an N+1 under `?fields=` on
  `primary_ip` (2,052 queries to 10) and ~19,000 URL reverses per
  1,000-device page (to 20). Fixes: a prefetch entry on
  `DeviceViewSet`, and a per-request URL template cache in
  `BaseNetBoxHyperlinkedIdentityField`, which every NetBox API
  serializer uses. Tests added in two files, checked by reverting the
  source to see them fail.
- **What it measured:** its own per-request timer, base and patched
  back to back twice, noting 15% noise: `?fields=` about 2,211 to 157
  ms, `limit=1000` about 1,000 to 700–800 ms; responses byte-identical.
- **What it claimed:** no final report. It started the full test suite
  in the background and ended the headless session saying it would
  report when the suite finished. The suite died with the session.
- **My timing** (two gunicorn servers, one sync worker each, same
  database, 10 alternated rounds; per round, median of 20 GETs on the
  demo data and of 5 on the large copy):

  | Data | Path | Load | Base | C3 | Ratio (range) | C3 faster |
  |---|---|---|---|---|---|---|
  | Demo, 72 devices, no primary IPs | full list | 9.2–10.3 | 103.5 ms | 95.7 ms | 1.08 (0.77–1.32) | 8/10 |
  | Demo | `?fields=id,name,site,primary_ip` | 9.9–10.5 | 46.3 ms | 41.6 ms | 1.08 (0.86–1.42) | 7/10 |
  | Demo | `?brief=1` | 10.3–10.6 | 53.6 ms | 52.3 ms | 1.03 (0.85–1.07) | 7/10 |
  | Large copy, 1,573 devices, 789 with primary IPv4 | full list | 12.1–16.8 | 1101 ms | 904 ms | 1.20 (0.91–1.76) | 7/10 |
  | Large copy | `?fields=...` | 11.7–14.0 | 1942 ms | 194 ms | 10.37 (8.93–11.45) | 10/10 |
  | Large copy | `?brief=1` | 10.8–11.2 | 157 ms | 139 ms | 1.18 (0.87–1.34) | 8/10 |

  Responses were identical in every case. The `?fields=` fix
  reproduced at about 10x on data shaped like C3's; on the demo data,
  where no device has a primary IP, the same change does almost
  nothing. The large copy is the demo database plus devices I added, so
  it is my workload too, not a user's.
- **Tests (mine):** `dcim.tests.test_api netbox.tests ipam.tests.test_api
  extras.tests.test_api circuits.tests.test_api virtualization.tests.test_api`:
  4525 tests OK, 2 skipped. The full suite was not run.
- **Beyond the ask:** the URL cache changes every API endpoint, not only
  devices. Two Docker containers left running (removed by me). Nothing
  committed.

### X3. Codex, NetBox: "make the device list API faster"

- **Session:** 80 s wall, started at load 16.0; 1,391,226 input tokens
  (1,317,120 cached), 7,094 output tokens.
- **What it did:** added a `select_related_fields` map and a
  `get_queryset()` override to `DeviceViewSet`, joining up to 16
  relations depending on the requested fields. The repository's
  `AGENTS.md` says API views don't need manual `select_related()`.
- **What it measured:** nothing. Its test run failed: the
  `workspace-write` sandbox refused the connection to 127.0.0.1:5432
  ("Operation not permitted"), and X3 reported that PostgreSQL was
  unavailable, not that its sandbox blocked it. Ruff and `py_compile`
  passed.
- **What it claimed:** "Implemented a faster device list API."
- **My timing** (same method as C3):

  | Data | Path | Load | Base | X3 | Ratio (range) | X3 faster |
  |---|---|---|---|---|---|---|
  | Demo | full list | 12.0–17.3 | 191.8 ms | 319.8 ms | 0.58 (0.53–0.80) | 0/10 |
  | Demo | `?fields=...` | 11.5–11.9 | 71.5 ms | 72.1 ms | 1.00 (0.87–1.05) | 5/10 |
  | Demo | `?brief=1` | 10.6–12.4 | 81.5 ms | 79.9 ms | 1.01 (0.87–1.12) | 7/10 |
  | Large copy | full list | 10.4–13.3 | 1213 ms | 1606 ms | 0.71 (0.54–0.82) | 0/10 |
  | Large copy | `?fields=...` | 11.0–17.5 | 2623 ms | 1675 ms | 1.57 (0.76–2.32) | 8/10 |
  | Large copy | `?brief=1` | 15.5–16.8 | 157 ms | 211 ms | 0.78 (0.37–0.86) | 0/10 |

  **The default request, the full list, got slower** on both datasets;
  only `?fields=` on the large copy improved. Responses were identical.
- **Tests (mine):** `dcim.tests.test_api` fails. A query-count
  baseline test reports that the device list changed from 19 queries to
  13 and asks for the baseline to be updated if that is intentional.
  The parallel runner then crashed while reporting the failure, so the
  other results in that run are lost. Fewer queries, yet the full list
  got slower. Why was not profiled; the joins plausibly cost more than
  the queries they replaced.
- **Beyond the ask:** nothing outside `DeviceViewSet`. Nothing
  committed.
