"""Check read_env_file's output on a fixed file (the task's oracle).

The tree has no test for embedchain.utils.cli, so this one was written
here from the task tree's own read_env_file: comments and blank lines
are skipped, surrounding whitespace is stripped, a line must start with
a word key followed by "=", and the value is everything after the first
"=". Exits non-zero on any difference.
"""

import sys
import tempfile
from pathlib import Path

from embedchain.utils.cli import read_env_file

TEXT = """# comment
  A=1
B = 2
C=x=y

   # indented comment
D=
-E=5
F_1=  spaced value
"""
EXPECTED = {"A": "1", "C": "x=y", "D": "", "F_1": "  spaced value"}

with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "fixture.env"
    path.write_text(TEXT)
    got = read_env_file(str(path))
if got != EXPECTED:
    sys.exit(f"read_env_file returned {got!r}, expected {EXPECTED!r}")
sys.stdout.write("ok\n")
