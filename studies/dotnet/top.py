"""Print the top hotspots of a folded-stacks file, as ranked.

Usage: ``python studies/dotnet/top.py <folded> [<project root>]``; the
project root is ``/src``, where the profile script mounts the tree, and
``Markdig`` is named as the project's own assembly.
"""

import sys
from pathlib import Path

from perfproof.profilers.folded import summarise_file


def main(argv: list[str]) -> int:
    """Write one line per hotspot: shares, function, file, line."""
    root = Path(argv[2]) if len(argv) > 2 else Path("/src")
    for h in summarise_file(
        argv[1], project_root=root, top_n=10, own_modules={"Markdig"}
    ):
        sys.stdout.write(
            f"{h.self_fraction:.3f} {h.total_fraction} {h.function} "
            f"{h.file}:{h.line} library={h.is_library}\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
