"""Read what built a C or C++ executable, from the file alone.

An ELF executable linked by gcc or clang carries a ``.comment`` section
with one line per compiler that contributed an object file (``GCC:
(Debian 14.2.0-19) 14.2.0``, ``Debian clang version 19.1.7``). A Mach-O
executable has no such section: the only toolchain record is the
``LC_BUILD_VERSION`` load command, which names the SDK and the linker
(``ld 1230.1``), not the compiler (captured in
``tests/fixtures/native/SOURCE.md``). This reads those two records
without running the program, so the runtime gate can tell two trees'
built binaries apart by the toolchain that built them. An executable
built with the section stripped, or a format not read here (a fat
Mach-O, a big-endian or 32-bit ELF), gives an empty tuple, which means
unknown and not "no compiler".
"""

import struct
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO, Final

__all__ = ["compiler_family", "native_build_info"]

_ELF_MAGIC: Final = b"\x7fELF"
_ELFCLASS64: Final = 2
_ELFDATA2LSB: Final = 1
_MACHO_64_LE: Final = b"\xcf\xfa\xed\xfe"
_LC_BUILD_VERSION: Final = 0x32
_MAX_SECTION_HEADERS: Final = 4096
_MAX_COMMENT_BYTES: Final = 1 << 16
_MAX_LOAD_COMMAND_BYTES: Final = 1 << 20
_TOOLS: Final = {1: "clang", 2: "swift", 3: "ld", 4: "lld", 5: "metal"}
"""Mach-O tool ids (``mach-o/loader.h``, ``TOOL_*``)."""
_PLATFORMS: Final = {1: "macos", 2: "ios", 7: "iossimulator"}


def native_build_info(path: Path) -> tuple[str, ...]:
    """Return the toolchain records stamped into a native executable.

    Parameters
    ----------
    path : pathlib.Path
        An executable.

    Returns
    -------
    tuple of str
        ELF: the distinct lines of ``.comment``, in file order. Mach-O:
        ``platform min-os sdk``-style records, one per tool of
        ``LC_BUILD_VERSION`` (``ld 1230.1``) after ``sdk 26.2``. Empty
        when the file is neither, has no such record, or is malformed.

    Raises
    ------
    OSError
        If the file cannot be read.

    Notes
    -----
    Only the header, the section or load-command tables and one small
    section are read: time and memory are constant in the file's size.
    """
    with path.open("rb") as handle:
        head = handle.read(64)
        if head[:4] == _ELF_MAGIC:
            return _elf_comment(handle, head)
        if head[:4] == _MACHO_64_LE:
            return _macho_build_version(handle, head)
    return ()


def compiler_family(lines: Iterable[str]) -> str | None:
    """Name the compiler family an executable's records show.

    Parameters
    ----------
    lines : iterable of str
        Records from `native_build_info`.

    Returns
    -------
    str or None
        ``"clang"`` when a line holds ``clang version`` (Apple's and
        the distributions' included), else ``"gcc"`` when one starts
        ``GCC:``, else ``None`` (a Mach-O file names no compiler).
        clang wins because a clang build on Debian also carries the
        ``GCC:`` line of the C runtime objects that gcc built.
    """
    found = list(lines)
    if any("clang version" in line for line in found):
        return "clang"
    return "gcc" if any(ln.startswith("GCC:") for ln in found) else None


def _elf_comment(handle: BinaryIO, head: bytes) -> tuple[str, ...]:
    """Read the ``.comment`` section of an ELF64 little-endian file."""
    if len(head) < 64 or head[4] != _ELFCLASS64 or head[5] != _ELFDATA2LSB:
        return ()
    shoff = struct.unpack_from("<Q", head, 0x28)[0]
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", head, 0x3A)
    if (
        shoff == 0
        or shentsize < 64
        or not 0 < shnum <= _MAX_SECTION_HEADERS
        or shstrndx >= shnum
    ):
        return ()
    handle.seek(shoff)
    table = handle.read(shentsize * shnum)
    if len(table) != shentsize * shnum:
        return ()

    def section(index: int) -> tuple[int, int, int]:
        """Return (name offset, file offset, size) of a section."""
        at = index * shentsize
        name = struct.unpack_from("<I", table, at)[0]
        offset, size = struct.unpack_from("<QQ", table, at + 0x18)
        return name, offset, size

    _, str_offset, str_size = section(shstrndx)
    if str_size > _MAX_COMMENT_BYTES:
        return ()
    handle.seek(str_offset)
    names = handle.read(str_size)
    for index in range(shnum):
        name, offset, size = section(index)
        if names[name : name + 9] != b".comment\x00":
            continue
        if size > _MAX_COMMENT_BYTES:
            return ()
        handle.seek(offset)
        raw = handle.read(size)
        lines: list[str] = []
        for part in raw.split(b"\x00"):
            text = part.decode("ascii", errors="replace").strip()
            if text and text not in lines:
                lines.append(text)
        return tuple(lines)
    return ()


def _version(packed: int) -> str:
    """Spell a packed Mach-O version, dropping a zero patch."""
    major, minor, patch = packed >> 16, (packed >> 8) & 0xFF, packed & 0xFF
    return f"{major}.{minor}.{patch}" if patch else f"{major}.{minor}"


def _macho_build_version(handle: BinaryIO, head: bytes) -> tuple[str, ...]:
    """Read ``LC_BUILD_VERSION`` of a thin Mach-O 64 file."""
    ncmds, sizeofcmds = struct.unpack_from("<II", head, 16)
    if sizeofcmds > _MAX_LOAD_COMMAND_BYTES:
        return ()
    handle.seek(32)
    commands = handle.read(sizeofcmds)
    at = 0
    for _ in range(ncmds):
        if at + 8 > len(commands):
            return ()
        cmd, size = struct.unpack_from("<II", commands, at)
        if size < 8 or at + size > len(commands):
            return ()
        if cmd == _LC_BUILD_VERSION and size >= 24:
            platform, minos, sdk, ntools = struct.unpack_from(
                "<IIII", commands, at + 8
            )
            found = [
                f"{_PLATFORMS.get(platform, f'platform {platform}')} "
                f"minos {_version(minos)}",
                f"sdk {_version(sdk)}",
            ]
            for i in range(min(ntools, (size - 24) // 8)):
                tool, packed = struct.unpack_from(
                    "<II", commands, at + 24 + 8 * i
                )
                found.append(
                    f"{_TOOLS.get(tool, f'tool {tool}')} {_version(packed)}"
                )
            return tuple(found)
        at += size
    return ()
