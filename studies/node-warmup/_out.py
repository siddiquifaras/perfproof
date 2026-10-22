"""Write one line to standard output, flushed."""

import sys


def emit(text: str = "") -> None:
    """Write ``text`` and a newline to standard output, then flush.

    Parameters
    ----------
    text : str, optional
        The line, without its newline.
    """
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
