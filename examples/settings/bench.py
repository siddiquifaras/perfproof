"""Benchmark the record pipeline of `writer`.

Run as ``python bench.py [--output PATH]`` from this directory. The
timed region makes the records, adds each to a `writer.Writer` built
from the environment (the ``FLUSH_EVERY`` setting) and flushes it;
``PERFPROOF {"value": <seconds>}`` on stdout reports it, so the metric
is ``reported``. With ``--output`` the number of characters written is
saved outside the timed region.
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from writer import Writer

RECORDS = 1_000
"""Records written."""
ROUNDS = 40
"""Hashing rounds per record, standing in for real per-record work."""


def make_record(index: int) -> str:
    """Make one record.

    Parameters
    ----------
    index : int
        Record number.

    Returns
    -------
    str
        A line with the number and a digest derived from it.
    """
    digest = str(index).encode()
    for _ in range(ROUNDS):
        digest = hashlib.sha256(digest).digest()
    return f"{index}:{digest.hex()}"


def main(argv: list[str] | None = None) -> int:
    """Run the pipeline once and report its time.

    Parameters
    ----------
    argv : list of str, optional
        Command-line arguments; ``sys.argv[1:]`` when omitted.

    Returns
    -------
    int
        Exit status, 0 on success.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="save the count here")
    args = parser.parse_args(argv)
    start = time.perf_counter()
    out = Writer.from_environment()
    for i in range(RECORDS):
        out.add(make_record(i))
    out.flush()
    elapsed = time.perf_counter() - start
    if args.output is not None:
        output: Path = args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"{out.written}\n", encoding="utf-8")
    sys.stdout.write(f"PERFPROOF {json.dumps({'value': elapsed})}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
