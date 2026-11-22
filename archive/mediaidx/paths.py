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


def _same(a: str, b: str) -> bool:
    """Whether two path fragments name the same thing on the machine that
    stored them: Windows cares about neither case nor which slash."""
    return a.replace("/", "\\").casefold() == b.replace("/", "\\").casefold()


def local(path: str, mounts: dict | None, sep: str = os.sep) -> str:
    """Where a stored path is found on *this* machine.

    The index records each file where the indexing computer saw it, which by
    the rule above is a UNC path. Another machine may reach the very same folder
    some other way: a NAS serving its own share sees `\\\\nas\\Media\\Pictures`
    as `/volume1/Media/Pictures`, and a container sees it wherever it was
    mounted. `mounts` maps a stored prefix to where that folder is here, and
    the stored path itself is left alone -- it stays the one name a file has,
    whichever machine is asking (spec 8.3).

    The longest prefix that matches wins, and only on a whole folder name:
    `\\\\nas\\Pictures` does not claim `\\\\nas\\Pictures2`. What follows the
    prefix keeps its spelling exactly, because the machine that serves the
    files may care about case even though the one that listed them did not.
    A path no prefix claims comes back as it went in.
    """
    for stored in sorted(mounts or {}, key=len, reverse=True):
        head = stored.rstrip("\\/")
        if not head or not _same(path[:len(head)], head):
            continue
        rest = path[len(head):]
        if rest and rest[0] not in "\\/":
            continue                      # a longer name that merely starts alike
        here = str(mounts[stored])
        base = here.rstrip("\\/") or here
        parts = [p for p in rest.replace("\\", "/").split("/") if p]
        if not parts:
            return base
        return (base if base.endswith(sep) else base + sep) + sep.join(parts)
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
