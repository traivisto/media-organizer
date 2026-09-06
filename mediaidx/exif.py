"""The exiftool bridge.

The encoding settings here are not optional. pyexiftool defaults to the process
codepage -- cp1252 on this machine -- and decoding fails outright on the first
file whose metadata carries a byte outside that range. Finnish characters and
binary maker notes both trigger it, within the first few hundred files of the
reference library (spec §5.1).
"""

from __future__ import annotations

import shutil
from typing import Iterable

import exiftool

TAGS = (
    "EXIF:DateTimeOriginal", "EXIF:CreateDate", "EXIF:ModifyDate",
    "EXIF:Make", "EXIF:Model", "EXIF:LensModel",
    "EXIF:GPSLatitude", "EXIF:GPSLongitude",
    "EXIF:Orientation", "EXIF:ISO", "EXIF:FNumber",
    "EXIF:ExposureTime", "EXIF:FocalLength",
    "File:ImageWidth", "File:ImageHeight", "File:FileType",
    "QuickTime:CreateDate",
)


def find_exiftool(configured: str = "") -> str | None:
    if configured:
        return configured
    return shutil.which("exiftool") or shutil.which("ExifTool")


class Exif:
    """A long-lived exiftool process, driven in -stay_open batch mode."""

    def __init__(self, executable: str):
        self.executable = executable
        self._et: exiftool.ExifToolHelper | None = None

    def __enter__(self):
        self._et = exiftool.ExifToolHelper(
            executable=self.executable,
            encoding="utf-8",
            common_args=["-G", "-n", "-charset", "filename=utf8"],
        )
        self._et.run()
        return self

    def __exit__(self, *exc):
        if self._et is not None:
            try:
                self._et.terminate()
            except Exception:
                pass
        self._et = None
        return False

    def batch(self, paths: Iterable[str]) -> dict[str, dict]:
        """Metadata for many files at once, keyed by the path as given.

        A batch that fails as a whole is retried file by file, so one unreadable
        file cannot cost us the metadata of the 200 it was travelling with.
        """
        paths = list(paths)
        if not paths:
            return {}
        try:
            results = self._et.get_tags(paths, TAGS)
        except Exception:
            results = []
            for p in paths:
                try:
                    results.extend(self._et.get_tags([p], TAGS))
                except Exception:
                    continue

        out: dict[str, dict] = {}
        for m in results:
            src = m.get("SourceFile", "")
            out[src.replace("/", "\\")] = m
        return out


def to_fields(m: dict) -> dict:
    """Map exiftool output onto the `assets` columns."""

    def num(key):
        v = m.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def integer(key):
        v = num(key)
        return int(v) if v is not None else None

    return {
        "width": integer("File:ImageWidth"),
        "height": integer("File:ImageHeight"),
        "orientation": integer("EXIF:Orientation"),
        "gps_lat": num("EXIF:GPSLatitude"),
        "gps_lon": num("EXIF:GPSLongitude"),
        "camera_make": m.get("EXIF:Make") or None,
        "camera_model": m.get("EXIF:Model") or None,
        "lens": m.get("EXIF:LensModel") or None,
        "iso": integer("EXIF:ISO"),
        "f_number": num("EXIF:FNumber"),
        "exposure_time": num("EXIF:ExposureTime"),
        "focal_length": num("EXIF:FocalLength"),
        "format": (m.get("File:FileType") or "").lower() or None,
    }


def best_datetime_tag(m: dict):
    for key in ("EXIF:DateTimeOriginal", "EXIF:CreateDate",
                "QuickTime:CreateDate", "EXIF:ModifyDate"):
        if m.get(key):
            return m[key]
    return None
