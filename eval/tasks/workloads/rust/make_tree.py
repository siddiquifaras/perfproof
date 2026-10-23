"""Create the fixed directory tree the fd workloads search.

Writes ``eval-tree/`` in the current directory: 40 directories of 50
subdirectories of 100 empty files each (200,000 files in 2,040
directories), with a mix of extensions. Deterministic, so both trees and
every machine search the same tree.
"""

from pathlib import Path

ROOT = Path("eval-tree")
EXTENSIONS = (".rs", ".py", ".md", ".tar.gz", ".png", ".sh", ".txt", "")

for top in range(40):
    for sub in range(50):
        directory = ROOT / f"top{top:02d}" / f"sub{sub:02d}"
        directory.mkdir(parents=True, exist_ok=True)
        for number in range(100):
            extension = EXTENSIONS[number % len(EXTENSIONS)]
            (directory / f"file{number:03d}{extension}").touch()
