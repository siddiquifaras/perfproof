"""Machine fingerprint and noise warnings.

`fingerprint` probes the host once, before a comparison's first round
and never between rounds, and returns a
`perfproof.schema.MachineFingerprint`. `machine_warnings` turns one
into `perfproof.schema.RunWarning` items for the report.
"""

from perfproof.machine.probe import fingerprint
from perfproof.machine.warn import HIGH_LOAD_FRACTION, machine_warnings

__all__ = ["HIGH_LOAD_FRACTION", "fingerprint", "machine_warnings"]
