"""Build a source tree for one example, as baseline or as a candidate.

Each example directory is a self-contained project. A candidate lives
in ``<example>/candidates/<name>/`` as full copies of only the files it
changes, at the same paths relative to the example. Materializing
copies the example without its ``candidates/`` directory, then copies
the candidate's files over it.

Run as ``python examples/materialize.py EXAMPLE [CANDIDATE] DEST``.
"""

import argparse
import shutil
import sys
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent
"""Directory holding every example."""
_SKIPPED = shutil.ignore_patterns(
    "candidates", "__pycache__", "output", "build", ".pytest_cache"
)


def candidates(example: str) -> list[str]:
    """List the candidates an example ships.

    Parameters
    ----------
    example : str
        Name of the example directory, such as ``"rolling"``.

    Returns
    -------
    list of str
        Candidate names, sorted.

    Raises
    ------
    FileNotFoundError
        If the example has no ``candidates/`` directory.
    """
    root = EXAMPLES / example / "candidates"
    if not root.is_dir():
        msg = f"{root} is not a directory"
        raise FileNotFoundError(msg)
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def changed_files(example: str, candidate: str) -> list[str]:
    """List the files a candidate replaces.

    Parameters
    ----------
    example : str
        Name of the example directory.
    candidate : str
        Name of the candidate directory.

    Returns
    -------
    list of str
        POSIX paths relative to the example directory, sorted.

    Raises
    ------
    FileNotFoundError
        If the candidate directory does not exist.
    """
    root = EXAMPLES / example / "candidates" / candidate
    if not root.is_dir():
        msg = f"{root} is not a directory"
        raise FileNotFoundError(msg)
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )


def materialize(example: str, candidate: str | None, dest: Path) -> Path:
    """Write the baseline or a candidate tree of an example to ``dest``.

    Parameters
    ----------
    example : str
        Name of the example directory.
    candidate : str or None
        Candidate to apply over the baseline; ``None`` for the baseline
        itself.
    dest : Path
        Directory to create. It must not exist yet.

    Returns
    -------
    Path
        ``dest``.

    Raises
    ------
    FileExistsError
        If ``dest`` already exists.
    FileNotFoundError
        If the example or the candidate does not exist.
    """
    source = EXAMPLES / example
    overlay = [] if candidate is None else changed_files(example, candidate)
    shutil.copytree(source, dest, ignore=_SKIPPED)
    if candidate is not None:
        root = source / "candidates" / candidate
        for rel in overlay:
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, target)
    return dest


def main(argv: list[str] | None = None) -> int:
    """Materialize the tree named on the command line.

    Parameters
    ----------
    argv : list of str, optional
        Command-line arguments; ``sys.argv[1:]`` when omitted.

    Returns
    -------
    int
        Exit status, 0 on success.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("example")
    parser.add_argument("candidate", nargs="?")
    parser.add_argument("dest", type=Path)
    args = parser.parse_args(argv)
    dest = materialize(args.example, args.candidate, args.dest)
    sys.stdout.write(f"{dest}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
