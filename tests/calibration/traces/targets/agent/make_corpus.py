"""Write the agent target's local corpus, deterministically."""

import json
import random
import sys
from pathlib import Path

WORDS = [
    "alpha",
    "beta",
    "gamma",
    "delta",
    "timeout",
    "retry",
    "cache",
    "queue",
    "worker",
    "shard",
    "lease",
    "commit",
    "rollback",
    "index",
    "scan",
    "merge",
    "flush",
    "batch",
    "stream",
    "token",
    "window",
    "backoff",
    "circuit",
    "breaker",
    "deadline",
    "probe",
    "replica",
    "leader",
    "follower",
]
SERVICES = ["auth", "billing", "search", "ingest", "export", "notify"]


def main(root: Path) -> None:
    """Create ``root/corpus``: 600 text files and an events file."""
    rng = random.Random(20261203)  # noqa: S311
    corpus = root / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    for i in range(600):
        lines = [
            " ".join(rng.choice(WORDS) for _ in range(12)) for _ in range(400)
        ]
        (corpus / f"note-{i:03d}.txt").write_text("\n".join(lines) + "\n")
    events = [
        {
            "id": i,
            "service": rng.choice(SERVICES),
            "latency_ms": round(rng.lognormvariate(3.0, 0.8), 3),
            "ok": rng.random() > 0.02,
        }
        for i in range(100000)
    ]
    (corpus / "events.json").write_text(json.dumps({"events": events}))


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd())
