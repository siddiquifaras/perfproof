"""Probe a tree whose go.mod asks for another Go toolchain.

``docs/method.md`` says the runtime gate's Go probe names the toolchain
that ``go.mod``'s ``go`` line selects, which ``go env`` and ``go
version`` do not. This runs the gate's probe (``probe_environment``)
on trees whose go.mod asks for a Go newer than the one installed and
for the one installed, with ``GOTOOLCHAIN=auto``, and prints what it
recorded. A toolchain that is not yet in the module cache is downloaded
by the probe, so this needs the network the first time.

Command that produced the committed output, in a Linux box with Go
1.25 and uv (the ``golang:1.25`` image with uv added)::

    uv run python studies/go/toolchain_switch.py 1.26.0 1.25.0 \
        > studies/go/toolchain-switch.txt
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from perfproof.bench.lock import probe_environment
from perfproof.schema import BenchmarkSpec


def main(versions: list[str]) -> None:
    """Probe one tree per version and write what was recorded."""
    out = sys.stdout.write
    spec = BenchmarkSpec.model_validate(
        {
            "name": "probe",
            "command": ("go", "test", "."),
            "metric": {
                "kind": "wall",
                "direction": "lower_is_better",
                "unit": "s",
            },
            "correctness": {"command": ("true",), "timeout_seconds": 60.0},
            "warmup": 1,
            "budget": {"max_rounds": 20, "max_seconds": 600.0},
            "looks": {"n0": 10, "k": 10, "max_looks": 3},
            "timeout_seconds": 30.0,
            "harness": ("bench.go",),
            "oracle": ("oracle.go",),
            "inputs": ("input.go",),
            "config": ("config.go",),
        }
    )
    env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]}
    env["GOTOOLCHAIN"] = "auto"
    local = subprocess.run(
        ["go", "env", "GOVERSION"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout.strip()
    out(f"installed go: {local}; GOTOOLCHAIN=auto\n")
    for version in versions:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "go.mod").write_text(
                f"module example.com/x\n\ngo {version}\n"
            )
            (root / "x.go").write_text("package x\n")
            probed = probe_environment(
                "baseline", spec, root, env, timeout=600.0
            )
        out(f"\ngo.mod says go {version}:\n")
        out(f"  runtime_kind: {probed.runtime_kind}\n")
        out(f"  runtime_executable: {probed.runtime_executable}\n")
        out(f"  runtime_version: {probed.runtime_version}\n")
        out(f"  missing: {dict(probed.missing)}\n")


if __name__ == "__main__":
    main(sys.argv[1:])
