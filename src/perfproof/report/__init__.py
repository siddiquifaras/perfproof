"""The run record on disk and the report rendered from it.

`write` puts ``record.json`` and ``report.md`` in a run directory.
`render` turns a `perfproof.schema.RunRecord` into the report's text:
verdict and what it means first, then the claim, what was measured,
gates, guardrails, calibration status, warnings, machines and links to
samples and profiles. It renders only recorded fields and computes
nothing new, so the same record always gives the same bytes.
"""

from perfproof.report.files import (
    RECORD_NAME,
    REPORT_NAME,
    read_record,
    record_json,
    write,
)
from perfproof.report.render import render

__all__ = [
    "RECORD_NAME",
    "REPORT_NAME",
    "read_record",
    "record_json",
    "render",
    "write",
]
