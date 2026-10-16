"""Cached, rate-limited calls to the GitHub CLI.

Every response the miner uses comes through `gh` and is stored under
``eval/mine/cache/gh/`` keyed by the exact argv, so a rerun reads the
same evidence instead of asking GitHub again, and each manifest field
can be traced back to the response it came from.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CACHE_DIR = Path(__file__).resolve().parent / "cache" / "gh"

# The search API allows 30 requests a minute for an authenticated
# user; 2.5 s between calls stays under it with margin. Other calls
# draw on the 5000-an-hour core and GraphQL budgets.
SEARCH_PAUSE_S = 2.5
CORE_PAUSE_S = 0.5
TIMEOUT_S = 120.0
RATE_LIMIT_WAIT_S = 65.0
MAX_ATTEMPTS = 4


class GhError(RuntimeError):
    """A `gh` call failed after its retries.

    Parameters
    ----------
    argv : list of str
        The arguments passed to `gh`.
    stderr : str
        The last error output from `gh`.
    """

    def __init__(self, argv: list[str], stderr: str) -> None:
        super().__init__(f"gh {' '.join(argv)}: {stderr.strip()}")
        self.argv = argv
        self.stderr = stderr


def _key(argv: list[str]) -> str:
    return hashlib.sha256("\0".join(argv).encode()).hexdigest()[:32]


def cache_path(argv: list[str]) -> Path:
    """Return the cache file for one `gh` invocation.

    Parameters
    ----------
    argv : list of str
        Arguments to `gh`, without the program name.

    Returns
    -------
    Path
        Location of the cached response, whether or not it exists.
    """
    return CACHE_DIR / f"{_key(argv)}.json"


def _is_rate_limited(stderr: str) -> bool:
    text = stderr.lower()
    return "rate limit" in text or "abuse" in text or "403" in text


def gh_json(argv: list[str], *, search: bool = False) -> Any:
    """Run `gh` and return its parsed JSON output, cached by argv.

    Parameters
    ----------
    argv : list of str
        Arguments to `gh`; the command must print JSON.
    search : bool, optional
        Whether the call uses the search API, which has a lower rate
        limit and gets a longer pause before it.

    Returns
    -------
    Any
        The decoded JSON response.

    Raises
    ------
    GhError
        If `gh` exits non-zero on every attempt, or prints something
        that is not JSON. A 404 is not retried.
    """
    path = cache_path(argv)
    if path.exists():
        record = json.loads(path.read_text())
        return record["response"]
    stderr = ""
    for attempt in range(MAX_ATTEMPTS):
        time.sleep(SEARCH_PAUSE_S if search else CORE_PAUSE_S)
        proc = subprocess.run(
            ["gh", *argv],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            check=False,
        )
        if proc.returncode == 0:
            try:
                response = json.loads(proc.stdout or "null")
            except json.JSONDecodeError as exc:
                raise GhError(argv, f"not JSON: {exc}") from exc
            record = {
                "argv": ["gh", *argv],
                "fetched_at": datetime.now(UTC).isoformat(),
                "response": response,
            }
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record))
            return response
        stderr = proc.stderr
        if "404" in stderr or "Not Found" in stderr:
            break
        if _is_rate_limited(stderr) and attempt + 1 < MAX_ATTEMPTS:
            time.sleep(RATE_LIMIT_WAIT_S)
    raise GhError(argv, stderr)


def fetched_at(argv: list[str]) -> str | None:
    """Return when a cached response was fetched.

    Parameters
    ----------
    argv : list of str
        Arguments to `gh`, as passed to `gh_json`.

    Returns
    -------
    str or None
        ISO 8601 UTC timestamp, or None if nothing is cached.
    """
    path = cache_path(argv)
    if not path.exists():
        return None
    return str(json.loads(path.read_text())["fetched_at"])
