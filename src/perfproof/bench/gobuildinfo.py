r"""Read the Go toolchain version a Go executable was built with.

A Go build stamps its executable with a header, ``\xff Go buildinf:``,
followed by the toolchain's version (``go1.25.14``). This reads that
header without running the program or the ``go`` command, so the
runtime gate can tell two trees' built binaries apart by the toolchain
that built them. Only the inline form of Go 1.18 and later is read; an
older executable, or one that is not a Go build, gives ``None``.
"""

import re
from pathlib import Path
from typing import Final

__all__ = ["go_build_version"]

_MAGIC: Final = b"\xff Go buildinf:"
_ALIGN: Final = 16
_HEADER_BYTES: Final = 32
_INLINE_FLAG: Final = 0x02
_CHUNK_BYTES: Final = 1 << 20
_MAX_VERSION_BYTES: Final = 256
_VERSION: Final = re.compile(rb"(go[0-9]+\.[0-9]|devel )[ -~]*")
"""What a toolchain version looks like: ``go1.25.14``, ``go1.22rc1``,
``devel go1.26-abc``, optionally followed by ``X:experiment``."""


def go_build_version(path: Path) -> str | None:
    """Return the Go toolchain version stamped into an executable.

    Parameters
    ----------
    path : pathlib.Path
        An executable, or any file that holds the build header.

    Returns
    -------
    str or None
        The version, such as ``go1.25.14``; ``None`` when the file has
        no readable Go build header (not a Go build, built before Go
        1.18, or the header's bytes are not a version).

    Raises
    ------
    OSError
        If the file cannot be read.

    Notes
    -----
    The file is read in chunks of 1 MiB: time is linear in the file's
    size and memory is constant. The header sits at an offset that is a
    multiple of 16 in an ELF or Mach-O file; the string is also present
    as a constant in programs that read such headers, so a match is
    kept only when what follows it parses as a version.
    """
    keep = len(_MAGIC) + _HEADER_BYTES + _MAX_VERSION_BYTES
    with path.open("rb") as handle:
        buffer = b""
        offset = 0  # file offset of buffer[0]
        at_end = False
        while not at_end:
            chunk = handle.read(_CHUNK_BYTES)
            at_end = not chunk
            buffer += chunk
            # A hit near the end waits for the next chunk, which
            # carries the bytes after it.
            limit = len(buffer) if at_end else len(buffer) - keep
            start = 0
            while (
                limit > 0
                and (
                    hit := buffer.find(_MAGIC, start, limit + len(_MAGIC) - 1)
                )
                >= 0
            ):
                if (offset + hit) % _ALIGN == 0:
                    version = _version(buffer[hit : hit + keep])
                    if version is not None:
                        return version
                start = hit + 1
            drop = max(0, len(buffer) - keep)
            offset += drop
            buffer = buffer[drop:]
    return None


def _version(region: bytes) -> str | None:
    """Parse the header at the start of ``region``, or ``None``."""
    head = region[len(_MAGIC) : _HEADER_BYTES]
    if len(head) < _HEADER_BYTES - len(_MAGIC):
        return None
    pointer_size, flags = head[0], head[1]
    if pointer_size not in (4, 8) or not flags & _INLINE_FLAG:
        return None
    length, used = _uvarint(region, _HEADER_BYTES)
    if length is None or not 0 < length <= _MAX_VERSION_BYTES:
        return None
    begin = _HEADER_BYTES + used
    text = region[begin : begin + length]
    if len(text) != length or _VERSION.fullmatch(text) is None:
        return None
    return text.decode("ascii")


def _uvarint(data: bytes, at: int) -> tuple[int | None, int]:
    """Decode an unsigned LEB128 integer; ``(None, 0)`` if malformed."""
    value = 0
    for i in range(10):
        if at + i >= len(data):
            return None, 0
        byte = data[at + i]
        value |= (byte & 0x7F) << (7 * i)
        if byte < 0x80:
            return value, i + 1
    return None, 0
