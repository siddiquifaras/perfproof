"""perfproof-owned harness files, installed identically in both trees.

A benchmark script the agent wrote for a repository is not part of
either version of that repository: it is usually uncommitted, so a
baseline checkout does not have it, and it must never differ between
the trees. The spec says so by listing it, under ``harness``,
``inputs`` or ``config``, below ``.perfproof/harness/``. Before
anything runs, `install_harness` replaces that whole directory in a
tree with a copy of the one in the main checkout, where the lock
manifest was taken. The lock gate then checks both copies against the
frozen hashes, so an edit to the main checkout after the spec was
frozen is still refused.

The whole directory is replaced, not only the listed files: a file a
candidate adds beside the benchmark (a module it imports) would
otherwise be code the lock never sees.
"""

import os
import shutil
from pathlib import Path

from perfproof.bench.lock import HARNESS_DIR, IGNORED_NAMES, owns_harness
from perfproof.bench.spec import STATE_DIR
from perfproof.schema import BenchmarkSpec

__all__ = ["HARNESS_DIR", "install_harness", "owns_harness"]


def install_harness(
    spec: BenchmarkSpec, source_root: Path, tree_root: Path
) -> None:
    """Make a tree's ``.perfproof/harness/`` a copy of the main one.

    Does nothing unless `owns_harness`. Whatever the tree had at that
    path (files, extra files, a symlink) is removed first, so the result
    is byte-identical to the source, ``__pycache__`` and ``.git``
    excepted, as the lock ignores them. Executable bits are kept. A
    symlink standing where ``.perfproof`` or ``.perfproof/harness``
    should be a directory is replaced, never written through. This
    holds for a candidate directory the user supplied too: whatever is
    in its ``.perfproof/harness/`` is deleted. Linear in the bytes of
    the directory.

    Parameters
    ----------
    spec : BenchmarkSpec
        The spec in force.
    source_root : Path
        Root of the main checkout, where the spec was defined.
    tree_root : Path
        Root of the tree to prepare. When it is the main checkout there
        is nothing to copy.

    Raises
    ------
    OSError
        If the source directory is missing or a copy fails.
    """
    if not owns_harness(spec):
        return
    source = source_root / HARNESS_DIR
    if not source.is_dir():
        msg = f"{source} is not a directory; it holds the listed harness files"
        raise FileNotFoundError(msg)
    target = tree_root / HARNESS_DIR
    if target.exists() and os.path.samefile(source, target):
        return
    state = tree_root / STATE_DIR
    if state.is_symlink() or (state.exists() and not state.is_dir()):
        state.unlink()
    state.mkdir(exist_ok=True)
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        source,
        target,
        symlinks=False,
        ignore=shutil.ignore_patterns(*IGNORED_NAMES),
    )
