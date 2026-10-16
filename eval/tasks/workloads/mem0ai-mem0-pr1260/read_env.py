"""Parse a fixed 300-line .env file 2,000 times with read_env_file.

Writes the file next to itself on first use, then calls
embedchain.utils.cli.read_env_file, the function the reference change
rewrites. Prints the number of keys so both trees can be compared for
equal output.
"""

import sys
from pathlib import Path

from embedchain.utils.cli import read_env_file

path = Path("eval.env")
lines = []
for i in range(300):
    if i % 10 == 0:
        lines.append(f"# section {i}")
    elif i % 7 == 0:
        lines.append("")
    else:
        lines.append(f"  KEY_{i}=value-{i}=with=equals  ")
path.write_text("\n".join(lines) + "\n")
keys = 0
for _ in range(2000):
    keys = len(read_env_file(str(path)))
sys.stdout.write(f"{keys}\n")
