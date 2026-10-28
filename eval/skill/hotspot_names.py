"""Name matching for the hotspot check; it lives in the package.

`perfproof.profilers.names` holds the rules, shared with
``perfproof profile show --function``. This module keeps the import
the scorer and its tests use.
"""

from perfproof.profilers.names import (
    ShortName,
    files_match,
    names_match,
    short_name,
)

__all__ = ["ShortName", "files_match", "names_match", "short_name"]
