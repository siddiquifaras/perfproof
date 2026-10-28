# Recording the GPU-kernel real A/A traces

Why: `CALIBRATED` is flipped with `gpu_kernels` excluded because no real
A/A trace of a GPU kernel exists (`.claude/worklog.md`, 2026-10-28). The
owner rents one GPU for at most 6 hours before launch (2026-11-02) to
record them. This file is the runbook. Nothing here was run on a GPU.

> **Terminate the pod by (rent time + 6 h): write the clock time here
> when you rent: ________.** Console: RunPod console, Pods, the pod's
> menu, Stop, then Terminate. CLI (Mac, after `runpodctl config --apiKey
> <key>`): `runpodctl pod delete <POD_ID>`
> ([manage pods](https://docs.runpod.io/pods/manage-pods.md)). Confirm
> the pod reads Terminated. Section "Hard stop" says what is and is not
> automatic.

Status labels: CODE FACT is read from a file or run on this Mac;
INFERENCE is reasoned and has not been measured; UNMEASURED is a number
that only the rented GPU can give, with the rule that replaces it.

## Approval and ledger (read first)

`.claude/local/gpu-spend.md` says: free credit only, nothing out of
pocket, no paid run without Faras present. RunPod is out of pocket (about
$3 to $4 below). So this run needs Faras's explicit yes to the estimate
below, in chat, with Faras present, and a ledger row afterwards. The
`gpu-run` skill's estimate block:

| | |
|---|---|
| GPU | 1x NVIDIA L4 24 GB, RunPod Secure Cloud |
| Why this GPU | Cheapest single NVIDIA GPU that is data-centre class (power-limited at 72 W, so clock and throttle noise is the real kind). It is also the L4 that `docs/targets.md` names for the vLLM case study |
| Wall time | 5.0 h planned, 6.0 h hard stop |
| Cost | $0.59/h x 6 h = $3.54 worst case; $2.95 planned; plus about $0.03 container disk |
| Produces | 4 traces of 300 to 600 pairs, plus a loaded variant if time allows |
| Informs | Whether the `gpu_kernels` schedule `(20, 20, 6, 120)` holds its false-KEEP rate on real GPU timings, i.e. whether `gpu_kernels` can join `CALIBRATED` |

Price source and date: RunPod's pricing page
(`https://www.runpod.io/gpu-instance/pricing`, page text "Updated October
8, 2026") lists L4 at $0.59/h Secure Cloud and $0.44/h Community Cloud;
container disk $0.10 per GB per month. Read on 2026-10-28 through a
page-summarising fetch tool, not a browser: re-read the live price in the
RunPod console before renting. A second source,
`https://computeprices.com/providers/runpod/gpus/l4` (updated 2026-10-28),
agrees on $0.59 Secure and lists other providers: Jarvis Labs $0.29,
Google Cloud $0.318, Vast.ai $0.495, Modal $0.799.

**Secure Cloud, not Community.** Community is $0.15/h cheaper
(INFERENCE: about $0.9 over the run) but has no SLA and a job can be
evicted mid-way; a trace must stay on one GPU (below), so an eviction costs
more than the saving.

Provider options, in the order to try:

1. **RunPod Secure Cloud L4**: SSH over TCP, PyTorch template, billed per
   second. Setup below is written for it.
2. **Vast.ai L4** ($0.495 listed): a marketplace; hosts differ, so check
   `nvidia-smi` and that you get root SSH and a CUDA-devel image. Cheaper
   on paper, riskier on host quality.
3. **Modal**: uses free credit, but the ledger shows the period's credit
   spent (2026-10-16: $21.86 metered, $21.67 credit); read `modal billing
   summary` first. A per-round process model needs a long-lived sandbox
   with a volume; not set up here. Only if RunPod is refused.

Any provider must give: root shell, `nvidia-smi` working, PyTorch with
CUDA, `nvcc`, one whole GPU, and no MPS daemon.

## Hard stop (6 hours)

CODE FACT: nothing in `gpu_collect.py` or `record_gpu.sh` ends the pod,
and the 14400 s recording budget only stops the recorder. What there is:

- **Owner-enforced terminate-by time (the guarantee).** Set a phone
  alarm for rent time + 5 h 45 min, so the copy-back is done by then, and
  terminate no later than the time written at the top (rent + 6 h),
  whether or not recording has finished.
- **Dead-man stop in the pod (best effort, unverified).** RunPod's docs
  give `runpodctl pod stop $RUNPOD_POD_ID` for stopping a pod from inside
  it, after `runpodctl config --apiKey <key>`
  ([manage pods](https://docs.runpod.io/pods/manage-pods.md)). The docs
  page does not say the key or `RUNPOD_POD_ID` are already present in a
  pod, so this is not assumed. `record_gpu.sh deadman` (also run by
  `probe` and `record`) arms a timer that runs that command at
  `HARD_STOP_EPOCH`, and prints `DEAD-MAN OFF` and why if `runpodctl`,
  `RUNPOD_POD_ID` or the epoch is missing. Putting an API key on a rented
  host is the owner's decision; without it, only the terminate-by time
  protects the 6 hours. It was tested here only with a stub `runpodctl`
  (the timer fires and passes `pod stop pod123`); whether RunPod accepts
  the real command from inside a pod was not tested. Arm it right after
  the first SSH:
  `export HARD_STOP_EPOCH=$(( $(date +%s) + 5*3600 + 40*60 ))`.
- **Balance backstop.** RunPod's billing docs say pods are stopped when
  the account balance reaches $0
  ([billing](https://docs.runpod.io/accounts-billing/billing)); a balance
  not far above the run's cost would cap the loss. Not tested, and the
  owner's call.

**Stop is not terminate.** The same docs say a stopped pod still bills
for its volume disk (listed at $0.20 per GB-month idle, $0.10 running,
[billing](https://docs.runpod.io/accounts-billing/billing)), and terminating
deletes the disk. The dead-man therefore uses stop, so the GPU meter ends
but `/workspace` survives, and the owner terminates once the traces are
copied back. A stopped pod cannot be reached over SSH until it is
restarted (which needs a free GPU), so rely on the pull loop's copy, never
on reaching the pod afterwards: at most 5 minutes of recording is lost.

## What gets recorded

CODE FACT: every run is a fresh process, as in `collect.py`. A pair is
two runs (A then B or B then A by the seed), the warm-up pair is `pair -1`
with `warmup: true`, and the files are `linux-gpu-<name>.jsonl` and
`.header.json` (the `linux-` prefix is what `real_traces.SOURCES` globs
for the other Linux traces). Rows are `collect.execute`'s rows plus
`gpu_sm_mhz`, `gpu_mem_mhz`, `gpu_temp_c`, `gpu_power_w`, `gpu_util_pct`,
`gpu_throttle` (one `nvidia-smi` sample after each pair) and `segment`.
`reported_s` is CUDA-event device time for a fixed batch of launches;
`wall_s` includes process start-up.

| Workload (`name`) | What it times | Character | Command |
|---|---|---|---|
| `matmul` | fp16 `torch.mm`, 8192^3, 30 launches | compute-bound, tensor cores, power-limited | `python3 matmul_bench.py` |
| `sgemm` | fp32 tiled matmul written in CUDA C++, 4096^3, 10 launches, result checked on two elements | compute-bound, plain CUDA C++, native binary | `./sgemm` (built with `nvcc` in each tree) |
| `membound` | `torch.add` over 1 GiB in and 1 GiB out, 70 launches | memory-bandwidth-bound | `python3 membound_bench.py` |
| `attention` | causal fp16 `scaled_dot_product_attention`, 8x16x2048x64, 300 launches | mixed, fused kernel | `python3 attention_bench.py` |
| `matmul_loaded` (optional) | `matmul` while `gpu_burner.py` runs fp16 matmuls on the same GPU | a second tenant time-slicing the GPU | as `matmul`, with the burner as `--background` |

Not included: a Triton kernel (it adds a compiler and a cache and
nothing a CUDA C++ kernel and PyTorch's fused kernels do not already
cover for timing noise).

Loaded variant: the class contract in `docs/targets.md` does not require
one. The existing off-class failures include loaded traces, so one loaded
GPU trace is worth having if the plan fits it; the plan adds it only when
300 pairs fit after the four required traces.

### Pairs per workload

Basis, CODE FACT: `real_traces.replicate_indexes` draws blocks of the
trace's pairs and raises if a block exceeds the trace's even pair count;
each calibration replicate uses up to 120 rounds (the `gpu_kernels`
maximum). The existing traces hold 1000 pairs (CPU workloads), 600
(`linux-web`, `linux-loaded-steady-web`) and 500 (`linux-llm`) pairs,
counted from the files in `data/`.

| | Pairs |
|---|---|
| Hard floor | 240 (two 120-round replicates that do not overlap) |
| Minimum for a trace to count | 300 (2.5x the 120-round maximum) |
| Target | 300 for `matmul`, `membound`, `attention`; 500 for `sgemm` (cheap to run) |
| Cap | 600 (5x the maximum, as the 600-pair web traces) |

300 is below the 500 to 1000 of the CPU traces. That is a cost decision,
not a statistical one: INFERENCE, a fresh process on a GPU host spends
seconds on start-up, so pairs are the scarce thing. Spare time goes to
more pairs (up to the cap) by the rule below.

## Time and cost

The budget, from renting to stopping the pod:

| Step | Time |
|---|---|
| Rent, SSH, copy files, check tools | 0:30 |
| First-10-minutes check (`probe`) and plan | 0:15 |
| Recording, hard budget `BUDGET_S` = 14400 s | 4:00 |
| Copy back, verify, terminate the pod | 0:15 |
| **Scheduled total** | **5:00** |
| Slack to the 6 h limit | 1:00 |

If a step overruns, take it from the recording, not from the slack: the
script's deadline is 14400 s from the first `record`, whenever that is.

### The start-up cost is UNMEASURED

Everything that decides the pair counts hangs on the seconds one fresh
process takes, which no one has measured on a GPU: importing PyTorch,
creating the CUDA context, cuBLAS first-use, the timed batch, teardown.
Nothing below is a measurement. The numbers in the table are what the
sizing rule gives for assumed run times, to show its behaviour.

| Assumed seconds per `torch` run (kernel about 0.5 s included) | 4 | 5 | 6 | 7 | 8 | 10 |
|---|---|---|---|---|---|---|
| `matmul` pairs | 350 | 400 | 300 | 400 | 350 | 300 |
| `sgemm` pairs (run assumed 1.5 s) | 500 | 600 | 600 | 550 | 550 | 300 |
| `membound` pairs | 350 | 350 | 300 | 400 | 350 | 300 |
| `attention` pairs | 350 | 350 | 300 | 0 | 0 | 0 |
| `matmul_loaded` pairs | 300 | 0 | 0 | 0 | 0 | 0 |

Generated by calling `plan_pairs` with a 14400 s budget and these
assumed inputs (all INFERENCE, none measured): seconds per pair = 2 x
run + 0.15 s (one `nvidia-smi` sample); a `sgemm` run of 1.5 s; the
loaded variant's run 1.2 x a `torch` run. Margin 10% is inside the
function.
Read it as: up to about 6 s per run all four required traces fit at 300
pairs or more; at 7 s and above `attention` is dropped, and at 10 s the
recording is `matmul`, `sgemm` and `membound` at the minimum.

### First-10-minutes check (measures it, then sets the counts)

After setup, `bash record_gpu.sh probe` runs every workload's health
check: the GPU is visible, is one device, has no other compute process;
5 runs of the real command each print a `reported_s`. It takes about 3 to
5 minutes (INFERENCE: 5 workloads x 6 runs x about 6 s, plus the SGEMM
build) and costs nothing but that time. It writes
`data/linux-gpu-<name>.health.json` and `data/plan.env`. Read:

```
health linux-gpu-matmul: run 6.30 s (kernel 0.52 s, start-up 5.78 s),
pair 12.75 s, 300 pairs = 64 min
```

Then apply these rules, in order, and stop to decide only where marked:

1. **A probe fails.** The script prints the last 80 lines of the
   command's output and exits. Fix it or terminate the pod. A failing
   `matmul` or `sgemm` makes `probe` exit non-zero and `record` refuse to
   start; a failing `membound`, `attention` or `matmul_loaded` is left
   out of the plan.
2. **Kernel time out of range.** `median_reported_s` should be 0.2 to
   1.5 s. If it is outside, scale that workload's `ITERS` constant in
   `targets/gpu/<name>_bench.py` (or `sgemm.cu`) by 0.5 / kernel time,
   delete `trees/<name>-*`, rerun `probe`. The `iters` field is in every
   row's `metrics`, so the change is recorded. Do this once.
3. **Noise.** `reported_spread` (max minus min over the 5 probes, over
   the median) above 0.25 means the host is not quiet: check
   `nvidia-smi -q -d PERFORMANCE` and `nvidia-smi --query-compute-apps=pid,name --format=csv`.
   Another tenant or throttling is a reason to terminate and re-rent
   (cost of trying again: about 25 minutes of the slack). A noisy GPU
   would still be a real trace, but then the host is not the quiet L4 of
   the plan; say so in the report.
4. **Pair counts.** Use `plan.env` as printed; it applies the sizing
   rule, with 10% margin on the measured per-pair seconds
   (`2 x median run + one sample`):
   - targets (300, 500, 300, 300) if they fit 14400 s; otherwise every
     required workload at 300; otherwise drop `attention`, then
     `membound`; `matmul` and `sgemm` are never dropped;
   - the loaded variant at 300 if it fits after that;
   - then spend what is left in steps of 50 pairs, round-robin over
     `matmul`, `membound`, `attention`, `sgemm`, to the cap of 600.
   - If even `matmul` and `sgemm` at 300 do not fit (run time above about
     20 s), the plan is empty: stop and report the measured run time
     (cost so far about $0.5), and ask the owner whether to record fewer
     pairs.
5. **Go.** Any non-empty plan goes ahead. Write the measured run times
   and the plan in the report.

## Commands

On the Mac (from the repo root), `PORT` and `IP` from the RunPod
console's SSH-over-TCP line, `/workspace` being the persistent disk:

```bash
ssh -p $PORT root@$IP 'mkdir -p /workspace/pp-gpu/targets && (command -v rsync || (apt-get update -qq && apt-get install -y -qq rsync))'
T=tests/calibration/traces
rsync -av -e "ssh -p $PORT" $T/collect.py $T/gpu_collect.py $T/burner.py \
  $T/record_gpu.sh root@$IP:/workspace/pp-gpu/
rsync -av -e "ssh -p $PORT" $T/targets/gpu root@$IP:/workspace/pp-gpu/targets/
```

On the pod:

```bash
cd /workspace/pp-gpu
python3 --version    # must be 3.11 or newer (datetime.UTC); else set PY=
nvidia-smi; nvcc --version | tail -1
python3 -c 'import torch, numpy; print(torch.__version__, torch.cuda.is_available())'
command -v rsync    # installed by the first Mac command; else use the tar fallback below
export HARD_STOP_EPOCH=$(( $(date +%s) + 5*3600 + 40*60 ))
command -v runpodctl; echo "pod id: ${RUNPOD_POD_ID:-unset}"
pgrep -x nvidia-cuda-mps-control || echo "no MPS daemon (good)"
export GPU_NOTE="RunPod Secure Cloud, 1x L4 24GB, template <name>, $(date -u +%F)"
bash record_gpu.sh probe 2>&1 | tee probe.log       # the first-10-minutes check
setsid nohup bash record_gpu.sh record > record.log 2>&1 &
tail -f record.log
```

The pull loop, in a second Mac terminal, started right after `record`
(a pod that vanishes takes its disk with it; this is the only copy that
matters):

```bash
mkdir -p scratch/gpu-traces
while true; do
  rsync -a --timeout=60 -e "ssh -p $PORT -o ConnectTimeout=20" \
    root@$IP:/workspace/pp-gpu/data/ scratch/gpu-traces/data/ || true
  sleep 300
done
```

Traces are about 0.4 MB each, so a full copy every 5 minutes costs
nothing. Without `rsync` on either side, the same copy with `tar`:
`ssh -p $PORT root@$IP 'tar -C /workspace/pp-gpu/data -cf - .' | tar -C
scratch/gpu-traces/data -xf -`; the way in is
`tar -cf - -C $T collect.py gpu_collect.py burner.py record_gpu.sh targets/gpu | ssh -p $PORT root@$IP 'tar -C /workspace/pp-gpu -xf -'`. A push from the pod is also available (`SYNC_CMD='<cmd with
{files}>'`, run every 25 pairs), but it needs credentials on a rented
host; the pull loop does not.

### Disconnect and resume

`record` runs under `setsid nohup`; a dropped SSH session does not stop
it. After a disconnect or reboot, SSH back in and rerun exactly
`setsid nohup bash record_gpu.sh record > record.log 2>&1 &`; if a
recorder is still running, the lock refuses (below) and nothing is
touched. It reads the same `data/plan.env` and the same deadline
(`data/record_started`). Each workload's recorder holds an exclusive,
non-blocking `fcntl.flock` on `data/linux-gpu-<name>.lock` (`--health-only`
too, since a probe beside a recording would disturb its timings): a second
one prints "another recorder holds ..." and exits 3 without touching the
trace; the OS drops the lock if the process dies. For each workload
`gpu_collect.py` then:

- drops a torn last line, a half pair, a repeated pair or a pair after a
  gap, and checks every pair's first arm against the seed (a wrong seed
  stops with exit 3 instead of appending);
- refuses to continue on a GPU whose UUID differs from the header's
  (exit 3): a trace stays on one GPU. If the pod is replaced, keep the
  old files as they are, and record the rest as new traces under new
  labels; do not splice. `--allow-new-host` overrides, for the owner only;
- records its own warm-up pair for the new `segment` (cold caches after a
  restart stay out of the statistics) and appends from pair `k`, so no
  pair number appears twice; a finished trace is left alone.

### Verify and copy back

On the Mac, when `record.log` ends with the `verify` table (or the time is
up):

```bash
rsync -a --timeout=60 -e "ssh -p $PORT" root@$IP:/workspace/pp-gpu/data/ scratch/gpu-traces/data/
uv run python $T/gpu_collect.py verify --min-pairs 300 \
  --calibration-loader tests/calibration scratch/gpu-traces/data/linux-gpu-*.jsonl
```

This checks, per trace: header with a GPU block; a warm-up pair; pair
numbers 0..n-1 with no repeat or gap; two runs per pair; first arms
equal to the seed's order; `reported_s` positive and finite on every
row; at least 300 pairs; and that `tests/calibration/real_traces.load_trace`
(the calibration's own reader) loads it. Then terminate the pod in the
RunPod console and confirm it shows as terminated. Add the ledger row to
`.claude/local/gpu-spend.md` (date, task, RunPod, L4, timeout, worst case
$3.54, actual from the console).

Put the files in `tests/calibration/traces/data/` gzipped, as the others.
Two follow-ups for the lead, not done here: `real_traces.SOURCES` points at
worktree scratch directories and does not read `traces/data/` at all, so it
must be pointed at the new files; and the `gpu_kernels` real-trace cells
need a whole calibration run at a new seed.

## If time runs short

The recording stops itself at its deadline (a stop at a pair boundary,
exit 76, files intact). Before that, to cut by hand:

1. Drop `matmul_loaded` (set `PAIRS_matmul_loaded=0` in `data/plan.env`,
   or just let the deadline skip it).
2. Then `attention`, then `membound`.
3. Never `matmul` or `sgemm`: one compute-bound PyTorch trace and the
   CUDA C++ trace are the least the class needs.
4. A trace below 300 pairs: usable down to the hard floor of 240, with
   its pair count stated in the report; below 240 do not use it.
5. To stop one workload now without killing the script, `kill -TERM` the
   `gpu_collect.py` process for it: it finishes the pair, writes the header
   and exits 76; the script goes on to the next workload.

## What the CPU dry run did not prove

Run on this Mac with `STANDIN=1` and a NumPy stand-in for the kernel
(`tests/calibration/traces/targets/gpu/cpu_standin.py`): the record
format, the warm-up flag, resume after a torn tail, the time budget, the
background process, the sync hook, the health check's failure paths, the
plan rule, `verify`, and loading by `real_traces.load_trace`. The tests
in `test_gpu_collect.py` cover those with a stub `nvidia-smi`.

Not proven, needs the GPU:

- Real `nvidia-smi` output and field names (table below). The stub only
  fixes the CSV shape. Every static field is read on its own and falls
  back to `null` with a reason in `gpu.unreadable`; each per-pair field
  is tried alone in the health check and a rejected one becomes a null
  column (`sample_fields_rejected` in the health file), failing the check
  only if none is accepted. Do not hand-write an `nvidia-smi` fixture; if
  you need one, capture it on the pod (`nvidia-smi --help-query-gpu`
  lists the names the driver accepts).
- The four workloads: none was run. Sizes (`ITERS`) are chosen from the
  hardware's nominal rates, not measured. `sgemm.cu` was only syntax
  checked on its host code against a stub header (no `nvcc` here); the
  device code and the `-arch=native` flag are untested.
- Start-up cost and per-pair seconds, hence every pair count above.
- Whether the container shows the host's load average (`load1` is the
  host's inside a container, so it is a weak covariate here), and
  whether `nvidia-smi` shows other tenants' processes (it may not inside
  a container, so the "GPU idle" check can pass on a shared GPU).
- Clock behaviour: the clocks are read after a pair, when an idle GPU may
  already have dropped them. They are not locked: the trace records the
  clocks the host really runs.

### `nvidia-smi --query-gpu` names

Checked 2026-10-28 against NVIDIA's
[nvidia-smi page](https://docs.nvidia.com/deploy/nvidia-smi/index.html)
(it names attributes by display label and defers to `--help-query-gpu`
for field names) and the `nvidia-smi` man page and help text found
through search ([man page](https://man.archlinux.org/man/nvidia-smi.1)).

| Field used | Status |
|---|---|
| `clocks_throttle_reasons.active` | Confirmed in the help text (older drivers) |
| `ecc.mode.current` | Confirmed in the help text |
| `pci.bus_id`, `persistence_mode` | Used in NVIDIA's own query examples |
| `clocks_event_reasons.active` | UNVERIFIED as a field name: the NVIDIA page has a "Clocks Event Reasons" section and NVML uses "clocks event reasons"; `pick_throttle_field` tries it first and falls back |
| `clocks.sm`, `clocks.mem`, `clocks.max.sm`, `clocks.max.mem`, `power.limit`, `power.draw`, `pstate`, `compute_mode`, `vbios_version`, `uuid`, `memory.total`, `utilization.gpu`, `temperature.gpu`, `driver_version` | UNVERIFIED against the docs (the page gives labels, not names); in common use; per-field fallback covers a miss |
| `clocks.applications.graphics` | UNVERIFIED, and NVIDIA's page marks Applications Clocks deprecated and slated for removal: expect it may be rejected, which costs one header field |

The page also marks "Driver Version" and "CUDA Version" as deprecated in
favour of other labels; the header's CUDA value is read from the banner
with a pattern that accepts both `CUDA Version` and `CUDA UMD Version`,
and is `null` if neither appears.
