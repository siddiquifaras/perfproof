"""
Run repro.py (vLLM #51663) on Modal using the OFFICIAL vllm/vllm-openai Docker
images, i.e. exactly what the reporter ran. Each version gets its own fresh
GPU container (a Modal Sandbox); a small CPU function orchestrates them, so
`--detach` keeps everything running if your laptop sleeps.

Run (default: L40S, 0.23.0 / 0.26.0 / 0.31.0, prefix caching ON):
    modal run --detach modal_repro.py
    modal run --detach modal_repro.py --versions "0.23.0 0.26.0" --args "--no-prefix-caching"
    modal run --detach modal_repro.py --gpu A100-80GB --versions "0.23.0 0.26.0"

Results:
    modal volume ls vllm-51663-results runs
    modal volume get vllm-51663-results runs/<timestamp> ./results/

Cost: L40S ~$1.95/hr + 8 CPU / 48 GiB (~$0.75/hr) per version container,
~30-45 min per version. The model (~35 GB) is cached in a volume after the
first download. Docker image pulls are cached by Modal after the first run.
"""
import shlex
import subprocess
import time
from pathlib import Path

import modal

HERE = Path(__file__).parent
APP_NAME = "vllm-51663-repro"

cache = modal.Volume.from_name("vllm-51663-cache", create_if_missing=True)
results = modal.Volume.from_name("vllm-51663-results", create_if_missing=True)

orchestrator_image = (
    modal.Image.debian_slim(python_version="3.12")
    .add_local_file(HERE / "repro.py", "/app/repro.py")
)

app = modal.App(APP_NAME, image=orchestrator_image)


def vllm_image(version: str) -> modal.Image:
    return (
        modal.Image.from_registry(f"vllm/vllm-openai:v{version}")
        .entrypoint([])  # the image's default entrypoint starts a server
        .add_local_file("/app/repro.py", "/app/repro.py")  # path inside the orchestrator container
    )



@app.function(timeout=10 * 3600, volumes={"/vol-results": results})
def orchestrate(versions: list[str], args: str, gpu: str) -> str:
    sb_app = modal.App.lookup(APP_NAME, create_if_missing=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dirs = []
    for i, v in enumerate(versions):
        # index prefix so the same version can run twice (A, B, A drift check)
        out = f"runs/{stamp}/{i}-{v}"
        run_dirs.append(out)
        print(f"\n===== vLLM {v} on {gpu} -> {out} =====", flush=True)
        cmd = (
            "export HF_HOME=/vol-cache/hf; "
            "(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader || true); "
            f"cd /vol-results && python3 /app/repro.py --system-vllm --versions {shlex.quote(v)} "
            f"--out-dir {out} {args} 2>&1; rc=$?; sync; exit $rc"
        )
        # The repro IS the sandbox's main command. (An empty sandbox exits at
        # once once the image entrypoint is cleared, which caused
        # "Sandbox is shutting down".) Volumes commit when it exits.
        sb = modal.Sandbox.create(
            "bash", "-c", cmd,
            app=sb_app,
            image=vllm_image(v),
            gpu=gpu,
            cpu=8,
            memory=48 * 1024,
            timeout=3 * 3600,
            volumes={"/vol-cache": cache, "/vol-results": results},
        )
        try:
            for line in sb.stdout:
                print(line, end="", flush=True)
            sb.wait(raise_on_termination=False)
            print(f"[sandbox for {v} exited with code {sb.returncode}]", flush=True)
        finally:
            sb.terminate()
        if sb.returncode:
            # Fail fast: the next versions would almost certainly hit the same
            # problem (OOM, bad flag) and just burn credit.
            print(f"Stopping: {v} failed, skipping the remaining versions.", flush=True)
            break
        time.sleep(5)

    results.reload()
    r = subprocess.run(
        ["python3", "/app/repro.py", "--summarize", *[f"/vol-results/{d}" for d in run_dirs]],
        capture_output=True, text=True,
    )
    text = r.stdout + r.stderr
    print(text, flush=True)
    return f"results: runs/{stamp}\n\n{text}"


@app.local_entrypoint()
def main(versions: str = "0.23.0 0.26.0 0.31.0", args: str = "", gpu: str = "L40S"):
    vs = versions.split()
    print(f"GPU={gpu}  versions={vs}  args={args!r}")
    print(orchestrate.remote(vs, args, gpu))
