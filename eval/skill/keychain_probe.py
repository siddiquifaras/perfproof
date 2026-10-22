"""Ask the Security framework for a keychain item and print the status.

Run by ``test_sandbox.py`` inside and outside the ``sandbox-exec``
profile to show the profile closes the route through ``securityd`` that
the file and ``security`` rules leave open. Only the item's attributes
are requested, never its secret, and only a status code is printed.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys

_UTF8 = 0x08000100


def status() -> int:
    """Query one generic-password item and return the ``OSStatus``.

    Returns
    -------
    int
        0 if the framework found the item, -25300 if it reached the
        keychain service and found nothing, any other value if the
        service could not be reached.
    """
    cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation") or "")
    sec = ctypes.CDLL(ctypes.util.find_library("Security") or "")
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_uint32,
    ]
    cf.CFDictionaryCreateMutable.restype = ctypes.c_void_p
    cf.CFDictionaryCreateMutable.argtypes = [
        ctypes.c_void_p,
        ctypes.c_long,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    cf.CFDictionarySetValue.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    sec.SecItemCopyMatching.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    sec.SecItemCopyMatching.restype = ctypes.c_int32

    def constant(lib: ctypes.CDLL, name: str) -> int | None:
        return ctypes.c_void_p.in_dll(lib, name).value

    key_cb = ctypes.addressof(
        ctypes.c_char.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")
    )
    value_cb = ctypes.addressof(
        ctypes.c_char.in_dll(cf, "kCFTypeDictionaryValueCallBacks")
    )
    query = cf.CFDictionaryCreateMutable(None, 0, key_cb, value_cb)
    service = cf.CFStringCreateWithCString(
        None, b"Claude Code-credentials", _UTF8
    )
    for key, value in (
        (
            constant(sec, "kSecClass"),
            constant(sec, "kSecClassGenericPassword"),
        ),
        (constant(sec, "kSecAttrService"), service),
        (constant(sec, "kSecMatchLimit"), constant(sec, "kSecMatchLimitOne")),
        (
            constant(sec, "kSecReturnAttributes"),
            constant(cf, "kCFBooleanTrue"),
        ),
    ):
        cf.CFDictionarySetValue(query, key, value)
    out = ctypes.c_void_p()
    return int(sec.SecItemCopyMatching(query, ctypes.byref(out)))


if __name__ == "__main__":
    sys.stdout.write(f"{status()}\n")
