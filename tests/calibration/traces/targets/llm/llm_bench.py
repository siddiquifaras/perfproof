"""One LLM-serving round against a running Ollama server on this host.

Sends four fixed prompts one after another (temperature 0, fixed seed,
``num_predict`` 32, streamed), and prints ``PERFPROOF {...}`` with the
mean time to first token, the mean decode speed and the total time. The
server and the model stay up across rounds and arms; only this client
process is fresh each run. ``OLLAMA_HOST`` (host:port) picks the server.
"""

import json
import os
import sys
import time
import urllib.request

MODEL = os.environ.get("TRACE_MODEL", "qwen2.5:0.5b")
HOST = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
PROMPTS = [
    "Name three prime numbers greater than 50.",
    "Write one sentence about the sea.",
    "What is 17 times 23? Answer with the number only.",
    "List two uses of a hash map.",
]
NUM_PREDICT = 32


def generate(prompt: str) -> dict[str, float]:
    """Stream one completion and return its timings."""
    body = json.dumps(
        {
            "model": MODEL,
            "prompt": prompt,
            "stream": True,
            "keep_alive": "2h",
            "options": {
                "temperature": 0,
                "seed": 7,
                "num_predict": NUM_PREDICT,
                "num_ctx": 1024,
                "num_gpu": 0,
            },
        }
    ).encode()
    req = urllib.request.Request(
        f"http://{HOST}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    start = time.perf_counter()
    first = 0.0
    final: dict[str, float] = {}
    with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310
        for line in resp:
            chunk = json.loads(line)
            if not first and chunk.get("response"):
                first = time.perf_counter() - start
            if chunk.get("done"):
                final = chunk
    total = time.perf_counter() - start
    tokens = float(final["eval_count"])
    return {
        "ttft": first,
        "total": total,
        "tokens": tokens,
        "decode_tps": tokens / (final["eval_duration"] / 1e9),
    }


def main() -> int:
    """Run the four prompts and print the round's metrics."""
    t0 = time.perf_counter()
    runs = [generate(p) for p in PROMPTS]
    elapsed = time.perf_counter() - t0
    n = len(runs)
    metrics = {
        "value": elapsed,
        "ttft_ms_mean": 1000 * sum(r["ttft"] for r in runs) / n,
        "decode_tps_mean": sum(r["decode_tps"] for r in runs) / n,
        "tokens_total": sum(r["tokens"] for r in runs),
    }
    sys.stdout.write(f"PERFPROOF {json.dumps(metrics)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
