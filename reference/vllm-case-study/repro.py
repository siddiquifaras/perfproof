#!/usr/bin/env python3
"""
Reproduce vLLM issue #51663: output-throughput regression for
Qwen/Qwen3.6-35B-A3B-FP8 between v0.23.0 and v0.24.0+.

What it does, per vLLM version:
  1. Creates an isolated venv (uv) and installs that vLLM release.
  2. Starts the OpenAI server with the reporter's flags.
  3. Records the GPU, driver, packages, VLLM_* env vars and the
     backend lines from the startup log (MoE / attention / CUDA graphs).
  4. Runs `vllm bench serve` (40 concurrent, ~1k input) several times.
  5. Stops the server.
Then it prints a comparison with a bootstrap confidence interval.

Versions run in the order given, so `--versions 0.23.0 0.26.0 0.23.0`
also catches drift (if the two 0.23.0 runs differ, the machine is noisy).

Usage (on a Linux GPU box, e.g. a RunPod L40S or A100 pod, ~150 GB disk):
    pip install uv
    export HF_TOKEN=...            # optional, speeds up downloads
    python3 repro.py                                   # 0.23.0 vs 0.26.0 vs 0.31.0, prefix caching ON
    python3 repro.py --versions 0.23.0 0.26.0 --no-prefix-caching   # hypothesis check
    python3 repro.py --versions 0.23.0 0.26.0 --extra-args "--moe-backend <name>"

Everything lands in ./runs/<timestamp>/ : logs, env, raw bench JSON, summary.md
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shlex
import signal
import statistics
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

MODEL = "Qwen/Qwen3.6-35B-A3B-FP8"

# Reporter's config from the issue (runai_streamer left optional: it only
# affects loading, and needs an extra package).
REPORTER_ARGS = [
    "--served-model-name", MODEL,
    "--trust-remote-code",
    "--tensor-parallel-size", "1",
    "--tool-call-parser", "qwen3_coder",
    "--reasoning-parser", "qwen3",
    "--mm-encoder-tp-mode", "data",
    "--max-num-seqs", "256",
]

# Startup-log lines worth diffing between versions.
LOG_PATTERNS = re.compile(
    r"(moe backend|moe.*kernel|fp8|marlin|cutlass|triton|flashinfer|deep_?gemm|"
    r"attention backend|using .*backend|cuda ?graph|compil|model runner|"
    r"chunked prefill|max_num_batched_tokens|gpu_memory|kv cache|"
    r"V2_MODEL_RUNNER|async sched)",
    re.IGNORECASE,
)


def sh(cmd: str, **kw) -> subprocess.CompletedProcess:
    print(f"$ {cmd}", flush=True)
    return subprocess.run(cmd, shell=True, text=True, **kw)


def capture(cmd: str) -> str:
    r = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    return (r.stdout + r.stderr).strip()


def ensure_venv(root: Path, version: str, python: str) -> Path:
    venv = root / f"vllm-{version}"
    py = venv / "bin" / "python"
    if py.exists() and f"vllm=={version}" in capture(f"VIRTUAL_ENV={venv} uv pip freeze"):
        return venv
    sh(f"uv venv --python {python} {venv}", check=True)
    extras = os.environ.get("REPRO_EXTRA_PKGS", "")
    sh(f"VIRTUAL_ENV={venv} uv pip install 'vllm=={version}' {extras}", check=True)
    return venv


def wait_healthy(port: int, proc: subprocess.Popen, timeout_s: int) -> None:
    deadline = time.time() + timeout_s
    url = f"http://127.0.0.1:{port}/health"
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early with code {proc.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        time.sleep(5)
    raise TimeoutError(f"server not healthy after {timeout_s}s")


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    os.killpg(os.getpgid(proc.pid), signal.SIGINT)
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait()
    time.sleep(10)  # let the GPU memory free up


def system_vllm() -> tuple[Path, Path]:
    """vllm binary + the python it runs on (read from its shebang)."""
    import shutil
    vbin = shutil.which("vllm")
    if not vbin:
        raise RuntimeError("--system-vllm given but `vllm` is not on PATH")
    head = Path(vbin).read_text(errors="ignore").splitlines()[:3]
    py = head[0][2:].strip().split()[0] if head and head[0].startswith("#!") else "python3"
    if "python" not in Path(py).name:  # pip's '#!/bin/sh' + exec "<python>" trick
        m = re.search(r'exec\'? "?([^" ]*python[^" ]*)"?', "\n".join(head))
        py = m.group(1) if m else "python3"
    return Path(vbin), Path(py)


def env_report(vllm_bin: Path, py: Path) -> dict:
    return {
        "gpu": capture("nvidia-smi --query-gpu=name,memory.total,driver_version,clocks.max.sm --format=csv,noheader"),
        "cuda_runtime": capture(f"{py} -c 'import torch;print(torch.version.cuda)'"),
        "torch": capture(f"{py} -c 'import torch;print(torch.__version__)'"),
        "vllm": capture(f"{py} -c 'import vllm;print(vllm.__version__)'"),
        "flashinfer": capture(f"{py} -c 'import flashinfer;print(flashinfer.__version__)' 2>/dev/null || echo none"),
        "vllm_env_vars": sorted(k for k in os.environ if k.startswith("VLLM_")),
        "cpu": capture("lscpu | grep 'Model name' | head -1"),
    }


def backend_lines(log_path: Path) -> list[str]:
    lines = []
    for line in log_path.read_text(errors="ignore").splitlines():
        if "Capturing CUDA graphs" in line or "it/s]" in line:
            continue  # progress bars, not configuration
        if LOG_PATTERNS.search(line):
            # strip timestamps / pids so versions diff cleanly
            lines.append(re.sub(r"^.*?\] ", "", line)[:300])
    return lines


def scrape_prefix_cache(port: int) -> dict:
    """Sum vLLM's prefix-cache counters from /metrics (names vary a bit across versions)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=10) as r:
            text = r.read().decode()
    except Exception:
        return {}
    tot = {"hits": 0.0, "queries": 0.0}
    for line in text.splitlines():
        if line.startswith("#") or "prefix_cache" not in line:
            continue
        name = line.split("{")[0].split(" ")[0]
        try:
            val = float(line.rsplit(" ", 1)[1])
        except ValueError:
            continue
        if name.endswith("prefix_cache_hits_total") or name.endswith("prefix_cache_hits"):
            tot["hits"] += val
        elif name.endswith("prefix_cache_queries_total") or name.endswith("prefix_cache_queries"):
            tot["queries"] += val
    return tot


def run_bench(vllm_bin: Path, port: int, out_dir: Path, tag: str, a: argparse.Namespace) -> dict:
    result_file = f"{tag}.json"
    cmd = (
        f"{vllm_bin} bench serve --backend openai --base-url http://127.0.0.1:{port} "
        f"--model {MODEL} --tokenizer {MODEL} --trust-remote-code "
        f"--dataset-name random --random-input-len {a.input_len} --random-output-len {a.output_len} "
        f"--random-prefix-len {a.prefix_len} "
        f"--num-prompts {a.num_prompts} --max-concurrency {a.concurrency} --ignore-eos "
        f"--seed {a.seed} --save-result --result-dir {out_dir} --result-filename {result_file}"
    )
    before = scrape_prefix_cache(port)
    with open(out_dir / f"{tag}.bench.log", "w") as f:
        sh(cmd, stdout=f, stderr=subprocess.STDOUT, check=True)
    after = scrape_prefix_cache(port)
    res = json.loads((out_dir / result_file).read_text())
    if before and after and after["queries"] > before["queries"]:
        res["prefix_cache_hit_pct"] = 100.0 * (after["hits"] - before["hits"]) / (after["queries"] - before["queries"])
    return res


KEYS = ["output_throughput", "total_token_throughput", "request_throughput",
        "median_ttft_ms", "median_tpot_ms", "median_itl_ms", "prefix_cache_hit_pct"]


def bootstrap_ratio_ci(a: list[float], b: list[float], n: int = 5000, seed: int = 0):
    """95% CI for median(b)/median(a)."""
    rng = random.Random(seed)
    ratios = []
    for _ in range(n):
        ra = [rng.choice(a) for _ in a]
        rb = [rng.choice(b) for _ in b]
        ratios.append(statistics.median(rb) / statistics.median(ra))
    ratios.sort()
    return ratios[int(0.025 * n)], ratios[int(0.975 * n)]


def summarize(results: dict[str, list[dict]], envs: dict[str, dict], backends: dict[str, list[str]], out_dir: Path) -> str:
    out = ["# vLLM #51663 reproduction", ""]
    first_env = next(iter(envs.values()))
    out += [f"- GPU: {first_env['gpu']}", f"- CPU: {first_env['cpu']}",
            f"- VLLM_* env vars set: {first_env['vllm_env_vars'] or 'none'}", ""]
    out += ["| version | runs | " + " | ".join(KEYS) + " |", "|" + "---|" * (len(KEYS) + 2)]
    for ver, runs in results.items():
        cells = [f"{statistics.median([r[k] for r in runs]):.1f}" if all(k in r for r in runs) else "n/a" for k in KEYS]
        out.append(f"| {ver} | {len(runs)} | " + " | ".join(cells) + " |")
    vers = list(results)
    if len(vers) >= 2:
        base = vers[0]
        a = [r["output_throughput"] for r in results[base]]
        out.append("")
        for v in vers[1:]:
            b = [r["output_throughput"] for r in results[v]]
            ratio = statistics.median(b) / statistics.median(a)
            lo, hi = bootstrap_ratio_ci(a, b) if len(a) > 1 and len(b) > 1 else (float("nan"),) * 2
            out.append(f"- output throughput {v} / {base}: **{ratio:.2f}x** (95% CI {lo:.2f}-{hi:.2f}, {len(a)} vs {len(b)} runs)")
    out += ["", "## Environment per version", ""]
    for v, e in envs.items():
        out.append(f"- **{v}**: vllm {e['vllm']}, torch {e['torch']}, CUDA {e['cuda_runtime']}, flashinfer {e['flashinfer']}"
                   f", server_env {e.get('server_env') or '-'}, args {e.get('args', '-')}")
    out += ["", "## Backend lines from startup logs (diff these)", ""]
    for v, lines in backends.items():
        out += [f"### {v}", "```", *lines[:60], "```", ""]
    text = "\n".join(out)
    (out_dir / "summary.md").write_text(text)
    return text


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # 0.31.0 = latest on PyPI (Oct 2026): tells you if it is already fixed.
    p.add_argument("--versions", nargs="+", default=["0.23.0", "0.26.0", "0.31.0"])
    p.add_argument("--repeats", type=int, default=3, help="bench runs per server start")
    p.add_argument("--concurrency", type=int, default=40)
    p.add_argument("--num-prompts", type=int, default=400)
    p.add_argument("--input-len", type=int, default=1024)
    p.add_argument("--output-len", type=int, default=512)
    p.add_argument("--prefix-len", type=int, default=0,
                   help="shared prefix tokens per prompt (e.g. 512 = system prompt + tools, like an agent workload)")
    p.add_argument("--max-model-len", type=int, default=8192)
    p.add_argument("--gpu-mem", type=float, default=0.90)
    p.add_argument("--extra-args", default="", help="extra server flags, e.g. '--moe-backend X'")
    p.add_argument("--runai-streamer", action="store_true", help="add --load-format runai_streamer like the reporter")
    p.add_argument("--server-env", action="append", default=[], metavar="KEY=VALUE",
                   help="env var for the vLLM server only, e.g. VLLM_USE_V2_MODEL_RUNNER=0 (repeatable)")
    p.add_argument("--no-prefix-caching", action="store_true",
                   help="turn prefix caching OFF (reporter had it ON); use to test the hybrid-model hypothesis")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--python", default="3.12")
    p.add_argument("--venv-root", default=str(Path.home() / "vllm-venvs"))
    p.add_argument("--startup-timeout", type=int, default=1800)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--system-vllm", action="store_true",
                   help="use the vllm already installed (official vllm/vllm-openai image); one version per run")
    p.add_argument("--out-dir", default=None, help="where to write results (default runs/<timestamp>)")
    p.add_argument("--summarize", nargs="+", metavar="DIR",
                   help="only combine results.json from these run dirs into one summary, then exit")
    a = p.parse_args()

    if a.summarize:
        results, envs, backends = {}, {}, {}
        for d in a.summarize:
            f = Path(d) / "results.json"
            if not f.exists():
                print(f"skip {d}: no results.json")
                continue
            data = json.loads(f.read_text())
            for k in data["results"]:
                nk = k if k not in results else f"{k} ({Path(d).name})"  # same version run twice
                results[nk] = data["results"][k]
                envs[nk] = data["envs"][k]
                backends[nk] = data["backends"].get(k, [])
        out = Path(a.summarize[0]).parent
        print(summarize(results, envs, backends, out))
        return

    if a.runai_streamer:
        os.environ["REPRO_EXTRA_PKGS"] = os.environ.get("REPRO_EXTRA_PKGS", "") + " 'vllm[runai]'"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(a.out_dir) if a.out_dir else Path("runs") / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "args.json").write_text(json.dumps(vars(a), indent=2))

    results: dict[str, list[dict]] = {}
    envs: dict[str, dict] = {}
    backends: dict[str, list[str]] = {}

    for i, ver in enumerate(a.versions):
        key = ver if ver not in results else f"{ver}#{i}"
        if a.system_vllm:
            vllm_bin, py = system_vllm()
        else:
            venv = ensure_venv(Path(a.venv_root), ver, a.python)
            vllm_bin, py = venv / "bin" / "vllm", venv / "bin" / "python"
        envs[key] = env_report(vllm_bin, py)
        envs[key]["server_env"] = a.server_env
        envs[key]["args"] = {k: getattr(a, k) for k in ("prefix_len", "input_len", "output_len", "no_prefix_caching", "extra_args")}
        server_args = REPORTER_ARGS + [
            "--port", str(a.port), "--max-model-len", str(a.max_model_len),
            "--gpu-memory-utilization", str(a.gpu_mem),
        ]
        # The reporter sets prefix caching in their Helm values
        # (enablePrefixCaching: true), not in extraArgs, so a repro that only
        # copies extraArgs may miss it. The model is hybrid (Gated DeltaNet),
        # where prefix caching changes the scheduling path.
        server_args += ["--no-enable-prefix-caching"] if a.no_prefix_caching else ["--enable-prefix-caching"]
        if a.runai_streamer:
            server_args += ["--load-format", "runai_streamer"]
        server_args += shlex.split(a.extra_args)
        log_path = out_dir / f"server-{key}.log"
        cmd = [str(vllm_bin), "serve", MODEL, *server_args]
        print("$ " + " ".join(shlex.quote(c) for c in cmd), flush=True)
        server_env = dict(os.environ)
        for kv in a.server_env:
            k, _, v = kv.partition("=")
            server_env[k] = v
            print(f"server env: {k}={v}", flush=True)
        with open(log_path, "w") as log:
            proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=server_env)
        try:
            try:
                wait_healthy(a.port, proc, a.startup_timeout)
            except Exception:
                tail = log_path.read_text(errors="ignore").splitlines()[-80:]
                print(f"\n----- last 80 lines of {log_path} -----\n" + "\n".join(tail), flush=True)
                raise
            backends[key] = backend_lines(log_path)
            # warmup (not recorded): short run so CUDA graphs / caches are hot
            warm = argparse.Namespace(**{**vars(a), "num_prompts": a.concurrency})
            run_bench(vllm_bin, a.port, out_dir, f"warmup-{key}", warm)
            results[key] = []
            for r in range(a.repeats):
                res = run_bench(vllm_bin, a.port, out_dir, f"bench-{key}-r{r}", a)
                results[key].append(res)
                print(f"[{key} run {r}] output tok/s = {res.get('output_throughput', float('nan')):.1f}"
                      f"  prefix-cache hit = {res.get('prefix_cache_hit_pct', float('nan')):.1f}%", flush=True)
        finally:
            stop(proc)
        (out_dir / "env.json").write_text(json.dumps(envs, indent=2))
        (out_dir / "results.json").write_text(json.dumps(
            {"results": results, "envs": envs, "backends": backends}, indent=2))

    print(summarize(results, envs, backends, out_dir))
    print(f"\nAll artifacts in {out_dir}/")


if __name__ == "__main__":
    main()
