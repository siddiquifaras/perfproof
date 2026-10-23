"""Wait until the host is quiet, then print what was observed as JSON.

Quiet is a 1-minute load average below 3 and none of the other workers'
jobs running, for ten minutes in a row. Used by the record scripts
before a pass, and before the container targets, whose own process
cannot see the host's load or the other workers' jobs.
"""

from __future__ import annotations

import json
import sys

from collect import wait_idle

SUSTAIN_SECONDS = 600.0
MAX_HOURS = 48.0


def main() -> int:
    """Wait for an idle host and print the observation."""
    sys.stdout.write(json.dumps(wait_idle(MAX_HOURS, SUSTAIN_SECONDS)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
