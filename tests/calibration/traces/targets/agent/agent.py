"""A deterministic agent loop that replays recorded model responses.

The loop is plan, tool calls, answer. The model side is a fixture
(``fixture.json``): the assistant turns are read from it in order and no
model or network is called. The tools do real local work on the corpus:
a regex search over every file, a JSON parse, and a latency summary.
Prints the final answer and a digest of everything the tools returned.
"""

import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def search_files(pattern: str) -> dict[str, object]:
    """Count lines matching ``pattern`` in every corpus text file."""
    rx = re.compile(pattern)
    hits: dict[str, int] = {}
    for path in sorted((ROOT / "corpus").glob("*.txt")):
        n = sum(1 for line in path.read_text().splitlines() if rx.search(line))
        if n:
            hits[path.name] = n
    top = sorted(hits.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    return {"files": len(hits), "lines": sum(hits.values()), "top": top}


def load_events(path: str) -> dict[str, object]:
    """Parse an events file and count events per service."""
    events = json.loads((ROOT / path).read_text())["events"]
    counts: dict[str, int] = defaultdict(int)
    for e in events:
        counts[e["service"]] += 1
    return {"events": len(events), "per_service": dict(sorted(counts.items()))}


def p95_by_service(path: str) -> dict[str, float]:
    """95th percentile latency per service, over successful events."""
    events = json.loads((ROOT / path).read_text())["events"]
    by: dict[str, list[float]] = defaultdict(list)
    for e in events:
        if e["ok"]:
            by[e["service"]].append(e["latency_ms"])
    return {s: sorted(v)[int(0.95 * len(v))] for s, v in sorted(by.items())}


TOOLS = {
    "search_files": search_files,
    "load_events": load_events,
    "p95_by_service": p95_by_service,
}


def main() -> int:
    """Replay the fixture, running its tool calls for real."""
    fixture = json.loads((ROOT / "fixture.json").read_text())
    digest = hashlib.sha256()
    answer = ""
    for turn in fixture["assistant_turns"]:
        for call in turn.get("tool_calls", []):
            result = TOOLS[call["name"]](**call["arguments"])
            digest.update(json.dumps(result, sort_keys=True).encode())
        if not turn.get("tool_calls"):
            answer = turn["content"]
    sys.stdout.write(f"{answer}\ndigest {digest.hexdigest()[:16]}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
