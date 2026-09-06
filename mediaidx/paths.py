"""Windows path handling: UNC normalisation and extended-length prefixes.

Two rules from the spec (§10.3):

* Paths are stored as UNC, never as a mapped drive letter. Drive letters get
  reassigned; the index must not care.
* Every filesystem call uses the extended-length prefix, because UNC plus deep
  folders will exceed the legacy 260-character limit.
"""

from __future__ import annotations

import ctypes
import os
from functools import lru_cache

_ERROR_SUCCESS = 0


@lru_cache(maxsize=64)
def _drive_to_unc(drive: str) -> str | None:
    """Resolve 'Z:' to '\\\\server\\share', or None if it is a local drive."""
    if os.name != "nt":
        return None
    buf_len = ctypes.c_ulong(1024)
    buf = ctypes.create_unicode_buffer(buf_len.value)
    try:
        res = ctypes.windll.mpr.WNetGetConnectionW(
            ctypes.c_wchar_p(drive), buf, ctypes.byref(buf_len)
        )
    except OSError:
        return None
    return buf.value if res == _ERROR_SUCCESS and buf.value else None


def to_unc(path: str) -> str:
    """Canonical stored form: absolute, UNC where the drive is a network mapping."""
    path = os.path.abspath(path)
    if path.startswith("\\\\"):
        return path
    drive, rest = os.path.splitdrive(path)
    if drive:
        unc = _drive_to_unc(drive.rstrip("\\"))
        if unc:
            return unc.rstrip("\\") + rest
    return path


def long_path(path: str) -> str:
    """Prefix a path for the wide-character APIs so it can exceed 260 chars."""
    if os.name != "nt":
        return path
    path = os.path.abspath(path)
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def display(path: str) -> str:
    """Strip the extended-length prefix again for anything a human reads."""
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def is_in_sync_folder(path: str) -> str | None:
    """Return the name of a known sync root containing `path`, if any (§10.5)."""
    lowered = os.path.abspath(path).lower()
    for marker, name in (
        ("\\onedrive", "OneDrive"),
        ("\\dropbox", "Dropbox"),
        ("\\google drive", "Google Drive"),
        ("\\iclouddrive", "iCloud Drive"),
    ):
        if marker in lowered:
            return name
    return None
