"""Store a discovery result as evidence, never as a verdict.

Every cheap test (`profile`, `measure --sizes`, ``--stub``, ``--set``)
leaves a record in ``.perfproof/evidence/<workload>/``, one JSON file
per result. A hypothesis cites it by path and content hash, as
``docs/discovery.md`` says an observation is cited, so the evidence can
be read again later and shown unchanged. The hypothesis log itself is
the ``log`` command's; until it exists, ``hypothesis`` is the ID the
caller gives, kept in the record so the log can adopt the files.

A record names what kind of number it holds:

``bound``
    An upper limit from a model of the workload, such as a cap.
``estimate``
    A measurement from a few runs on a machine that may be loaded.

There is no third kind. A record has no verdict, no outcome and no
direction; a result is never claimed here. Only ``compare`` decides.
"""

import argparse
import hashlib
import json
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

from pydantic import TypeAdapter, ValidationError

from perfproof.bench.spec import STATE_DIR
from perfproof.schema import Name

__all__ = [
    "EVIDENCE_DIR",
    "SCHEMA_VERSION",
    "Claim",
    "EvidenceRef",
    "hypothesis_id",
    "write_evidence",
]

EVIDENCE_DIR: Final = "evidence"
"""Directory under ``.perfproof/`` holding the records."""
SCHEMA_VERSION: Final = 1
"""Version of the record layout."""
type Claim = Literal["bound", "estimate"]
"""What a record's numbers are."""
_CLAIMS: Final = ("bound", "estimate")
_NAME: Final = TypeAdapter(Name)


def hypothesis_id(text: str) -> str:
    """Check a ``--hypothesis`` value, for argparse.

    The hypothesis log names a hypothesis with the schema's ``Name``,
    so only such an ID can be cited from it.

    Parameters
    ----------
    text : str
        The value given.

    Returns
    -------
    str
        The same text.

    Raises
    ------
    argparse.ArgumentTypeError
        If the text is not a valid `perfproof.schema.Name`.
    """
    try:
        return _NAME.validate_python(text)
    except ValidationError as exc:
        msg = (
            f"{text!r} is not a hypothesis ID: use letters, digits, '.', "
            "'_' and '-', starting with a letter or digit"
        )
        raise argparse.ArgumentTypeError(msg) from exc


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """Where a record is and what it held.

    Attributes
    ----------
    path : pathlib.Path
        The record's file.
    sha256 : str
        Hex SHA-256 of the file's bytes.
    """

    path: Path
    sha256: str


def write_evidence(
    root: Path,
    workload: str,
    kind: str,
    claim: Claim,
    result: Mapping[str, object],
    *,
    hypothesis: str | None,
    spec_hash: str,
) -> EvidenceRef:
    """Write one evidence record atomically.

    Parameters
    ----------
    root : pathlib.Path
        Repository root.
    workload : str
        The benchmark's name; already validated as a name.
    kind : str
        ``profile``, ``sizes``, ``stub`` or ``set``.
    claim : {"bound", "estimate"}
        What the numbers in ``result`` are.
    result : Mapping of str to object
        The JSON-serializable result.
    hypothesis : str or None
        ID of the hypothesis this is evidence for; ``None`` when the
        caller named none.
    spec_hash : str
        Hash of the benchmark spec the result was measured under.

    Returns
    -------
    EvidenceRef
        The record's path and hash.

    Raises
    ------
    ValueError
        If ``claim`` is neither ``bound`` nor ``estimate``, or
        ``result`` is not JSON-serializable.
    OSError
        If the record cannot be written.
    """
    if claim not in _CLAIMS:
        msg = f"claim must be one of {_CLAIMS}, got {claim!r}"
        raise ValueError(msg)
    now = datetime.now(UTC)
    record = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "claim": claim,
        "calibrated": False,
        "hypothesis": hypothesis,
        "workload": workload,
        "spec_hash": spec_hash,
        "recorded_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "result": result,
    }
    data = (
        json.dumps(record, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode()
    directory = root / STATE_DIR / EVIDENCE_DIR / workload
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{kind}-{secrets.token_hex(3)}"
    path = directory / f"{stem}.json"
    temporary = directory / f".{stem}.tmp"
    with temporary.open("xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)
    return EvidenceRef(path, hashlib.sha256(data).hexdigest())
