"""Draw the second held-out discovery set.

The rule is the "Discovery supplement 2" of docs/evaluation.md: the
four steps of the Discovery supplement (``draw_discovery.py``), with
the salt ``perfproof-heldout-discovery-2026-10-29`` and one more skip.
The pool leaves out every task the first discovery draw examined, that
is every candidate in the walk of ``heldout-discovery-draw.json``,
whether the first draw took it, passed it over or never built it
because of the language cap.

The record ``eval/mine/heldout-discovery-2-draw.json`` has the same
shape as the first one (human inputs ``mined``, ``reviews``,
``passed_over``, ``notes``; derived part from the manifests), so the
draw can be re-derived: ``uv run python eval/mine/draw_discovery2.py``
prints the derivation and exits 1 if the record differs, and
``--write`` rewrites the derived part. The step-1 readings of the
first record also apply here, for candidates it mined but did not
examine.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import draw
import draw_discovery
from draw_discovery import Derived, derive
from task import load_tasks

RECORD = draw.DISCOVERY_2
SALT = "perfproof-heldout-discovery-2026-10-29"
RULE = "docs/evaluation.md, Discovery supplement 2"
FIRST = draw.DISCOVERY


def examined_by_first(path: Path = FIRST) -> dict[str, str]:
    """Return the tasks the first discovery draw examined.

    Parameters
    ----------
    path : Path
        The first draw's record.

    Returns
    -------
    dict of str to str
        Task id to the reason this draw skips it: every id in the
        walk of the first record, with its outcome there.
    """
    walk = json.loads(path.read_text())["walk"]
    return {
        v["id"]: f"examined by the first discovery draw ({v['outcome']})"
        for v in walk
    }


def load_record(path: Path = RECORD) -> dict[str, Any]:
    """Read the record, or an empty one when none is committed yet."""
    if not path.exists():
        return {"mined": {"ids": []}, "reviews": {}}
    return draw_discovery.load_record(path)


def first_record() -> dict[str, Any]:
    """Return the first discovery draw's record."""
    return draw_discovery.load_record(FIRST)


def derive_second(
    record: dict[str, Any], tasks: list[Any] | None = None
) -> Derived:
    """Apply the rule of this draw to the manifests and `record`.

    Parameters
    ----------
    record : dict
        This draw's record (human inputs).
    tasks : list of Task, optional
        Manifests; defaults to every committed one.

    Returns
    -------
    Derived
        As `draw_discovery.derive`.
    """
    first = first_record()
    return derive(
        load_tasks() if tasks is None else tasks,
        record["mined"]["ids"],
        {**first["reviews"], **record["reviews"]},
        record.get("passed_over"),
        salt=SALT,
        examined=examined_by_first(),
        earlier_mined=first["mined"]["ids"],
    )


def main(argv: list[str] | None = None) -> int:
    """Print the derived draw; check or rewrite the record.

    Parameters
    ----------
    argv : list of str, optional
        Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        0 when the record matches the derivation, else 1.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    record = load_record()
    new = draw_discovery.render(
        record, derive_second(record), rule=RULE, salt=SALT
    )
    text = json.dumps(new, indent=1) + "\n"
    sys.stdout.write(text)
    if args.write:
        RECORD.write_text(text)
        return 0
    return 0 if RECORD.exists() and RECORD.read_text() == text else 1


if __name__ == "__main__":
    raise SystemExit(main())
