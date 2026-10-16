"""Build the uv workload's project from files in the uv tree.

Copies ``test/ecosystem/transformers/pyproject.toml`` into
``eval-project/`` and writes the lockfile uv's ecosystem test
snapshots for it as ``eval-project/uv.lock``, so `uv export --frozen`
can read a large real lockfile without the network. Run from the root
of a uv checkout.
"""

import shutil
from pathlib import Path

SNAPSHOT = Path(
    "crates/uv/tests/it/snapshots/it__ecosystem__transformers-lock-file.snap"
)
OUT = Path("eval-project")

OUT.mkdir(exist_ok=True)
shutil.copy("test/ecosystem/transformers/pyproject.toml", OUT)
text = SNAPSHOT.read_text()
# An insta snapshot is a YAML header between two "---" lines, then the
# snapshot itself.
_, _, body = text.split("---\n", 2)
(OUT / "uv.lock").write_text(body)
