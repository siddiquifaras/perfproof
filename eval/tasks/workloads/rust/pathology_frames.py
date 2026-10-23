"""Profile each mode of ``pathologies.rs`` and list its top frames.

Usage::

    uv run python eval/tasks/workloads/rust/pathology_frames.py \
        <work directory> <label> [<pathologies binary> <seconds>]

With a binary, each mode first runs under
`perfproof.profilers.rust.record` for the given time and writes
``<mode>.folded`` in the work directory. Without one, the folded files
already there are summarised: that is how a Linux run is read, whose
``<mode>.folded`` files were written inside the container by
``python -m perfproof.profilers.rust record`` (which needs no pydantic,
as the summary does). The output is, per mode, the five frames with the
largest self share, as ``pathologies-frames.txt``.
"""

import sys
from pathlib import Path

from perfproof.profilers.folded import summarise
from perfproof.profilers.rust import record

MODES = (
    "grow",
    "clone",
    "contains",
    "format",
    "hash",
    "mutex",
    "unbuffered",
    "bounds",
)
TOP = 5


def main(argv: list[str]) -> int:
    """Profile every mode if asked, then write the table to stdout."""
    if len(argv) not in (2, 4):
        sys.stderr.write(__doc__ or "")
        return 2
    work, label = Path(argv[0]), argv[1]
    out = sys.stdout
    out.write(f"{label}\n")
    for mode in MODES:
        folded = work / f"{mode}.folded"
        status = "read"
        if len(argv) == 4:
            binary = Path(argv[2]).resolve()
            work.mkdir(parents=True, exist_ok=True)
            code = record(
                [str(binary), mode, argv[3]],
                folded,
                project_root=work,
                timeout_seconds=300,
            )
            status = f"record exit {code}"
        out.write(f"\nmode {mode} ({status})\n")
        if not folded.is_file():
            continue
        lines = folded.read_text(encoding="utf-8").splitlines()
        for spot in summarise(lines, project_root=work, top_n=TOP):
            share = f"{spot.self_fraction:.3f}"
            out.write(f"  {share} {spot.function}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
