"""Create the fixed directory tree the fd workload searches.

Writes ``eval-tree/`` in the current directory: 40 directories of 500
empty files each, with a mix of extensions that fd's default colours
distinguish, so every output line is coloured. Deterministic, so both
trees and every machine search the same tree.
"""

from pathlib import Path

ROOT = Path("eval-tree")
EXTENSIONS = (".rs", ".py", ".md", ".tar.gz", ".png", ".sh", ".txt", "")

for d in range(40):
    sub = ROOT / f"dir{d:02d}" / f"nested{d % 7}"
    sub.mkdir(parents=True, exist_ok=True)
    for f in range(500):
        (sub / f"file{f:03d}{EXTENSIONS[f % len(EXTENSIONS)]}").touch()
