"""The profiler table: the one place a language picks its profiler.

``perfproof profile`` takes ``--profiler-command``, an argv template in
which ``{command}`` stands for the benchmark's argv and ``{output}``
for the file the folded stacks go to. `perfproof find` runs the
profiler the language needs, so the map from a language to a template
lives here and nowhere else.

Python has no template: ``profile`` already picks py-spy on Linux and
pyinstrument on macOS, and the test command arrives as ``<python> -m
pytest`` so pyinstrument can wrap it.
"""

import sys
from pathlib import Path
from typing import Final

__all__ = [
    "NATIVE_LANGUAGES",
    "native_template",
    "profiler_template",
]

NATIVE_LANGUAGES: Final = frozenset({"c", "cpp"})
"""Languages whose own profiler is already the native recorder."""

_MODULE_FORMS: Final = {
    "javascript": ("node", "run", False),
    "typescript": ("node", "run", False),
    "java": ("java", "record", True),
    "kotlin": ("java", "record", True),
    "go": ("go", "record", True),
    "rust": ("rust", "record", True),
    "csharp": ("dotnet", "record", True),
    "ruby": ("ruby", "record", False),
}
"""Language to ``(module, verb, takes --project-root)`` of its recorder
in `perfproof.profilers`."""


def _recorder(
    module: str, verb: str, root_flag: tuple[str, ...]
) -> tuple[str, ...]:
    """Build one recorder's argv template."""
    return (
        sys.executable,
        "-m",
        f"perfproof.profilers.{module}",
        verb,
        "--output",
        "{output}",
        *root_flag,
        "--",
        "{command}",
    )


def profiler_template(language: str, root: Path) -> tuple[str, ...] | None:
    """Return the ``--profiler-command`` template for a language.

    Parameters
    ----------
    language : str
        A ``Part.language`` value.
    root : pathlib.Path
        Repository root, given to the recorders that classify project
        frames by it.

    Returns
    -------
    tuple of str or None
        The argv template; ``()`` for Python, whose default profiler
        ``profile`` picks itself; ``None`` when no profiler is known for
        the language.
    """
    if language == "python":
        return ()
    if language in NATIVE_LANGUAGES:
        return native_template()
    form = _MODULE_FORMS.get(language)
    if form is None:
        return None
    module, verb, takes_root = form
    flag = ("--project-root", str(root)) if takes_root else ()
    return _recorder(module, verb, flag)


def native_template() -> tuple[str, ...]:
    """Return the native recorder's template.

    Returns
    -------
    tuple of str
        ``<python> -m perfproof.profilers.native record --output
        {output} --root . -- {command}``. The binary that is project
        code goes to ``profile`` as ``--own-module``, not in here.
    """
    return _recorder("native", "record", ("--root", "."))
