"""Date resolution and the folder/filename parsing that feeds it (spec §5.1).

Resolution order, first match wins:

    1. EXIF DateTimeOriginal          second
    2. Filename pattern               second or day
    3. Parsed folder year + month     month
    4. Parsed folder year             year
    5. File mtime                     second, flagged unreliable

Measured against the reference library: rule 1 covers 95%, rule 2 covers 95%
(overlapping heavily), and only ~4% of files fall through to rules 3-5.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

# A parsed date outside these bounds is a false positive -- a resolution, a
# serial number, a version string. This guard is the main defence of filename
# parsing.
MIN_DATE = datetime(1990, 1, 1)

# A camera's own date stamp is not a false-positive risk the way a number
# pulled from a filename is -- it was printed by hardware, onto the print, at
# the time. The bound only needs to rule out a misread digit producing a
# year nothing in the archive could be from (spec: scanned prints going back
# to the 1970s).
MIN_STAMP_DATE = datetime(1970, 1, 1)

SECOND, DAY, MONTH, YEAR = "second", "day", "month", "year"

_PRECISION_WINDOW = {
    SECOND: timedelta(seconds=1),
    DAY: timedelta(days=1),
    MONTH: timedelta(days=31),
    YEAR: timedelta(days=366),
}


def _max_date() -> datetime:
    return datetime.now() + timedelta(days=1)


def _valid(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if MIN_DATE <= dt <= _max_date() else None


def valid_stamp_date(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if MIN_STAMP_DATE <= dt <= _max_date() else None


# --------------------------------------------------------------------------
# EXIF
# --------------------------------------------------------------------------

_EXIF_DT = re.compile(
    r"^(\d{4})[:\-](\d{2})[:\-](\d{2})"          # date
    r"(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?"     # optional time
)


def parse_exif_datetime(value) -> datetime | None:
    """EXIF stores '2024:01:01 15:12:02'. Zeroed fields mean 'unset'.

    Any timezone suffix is ignored: the archive is dated in local wall time,
    which is what every other source here provides too.
    """
    if not value:
        return None
    m = _EXIF_DT.match(str(value).strip())
    if not m:
        return None
    y, mo, d, hh, mm, ss = m.groups()
    if y == "0000" or mo == "00" or d == "00":
        return None
    try:
        return _valid(datetime(
            int(y), int(mo), int(d),
            int(hh or 0), int(mm or 0), int(ss or 0),
        ))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Filenames
# --------------------------------------------------------------------------

@dataclass
class FilenameParse:
    when: datetime
    precision: str
    hint: str | None = None
    make: str | None = None
    model: str | None = None


def _compile_filename_patterns(patterns: list[dict]):
    out = []
    for p in patterns:
        try:
            out.append((re.compile(p["re"]), p.get("hint")))
        except re.error:
            continue
    return out


def parse_filename(stem: str, compiled) -> FilenameParse | None:
    """Try each configured pattern in order; first valid date wins."""
    for rx, hint in compiled:
        m = rx.match(stem)
        if not m:
            continue
        g = m.groupdict()
        when, precision = None, None

        if g.get("ymd"):
            try:
                d = datetime.strptime(g["ymd"], "%Y%m%d")
            except ValueError:
                continue
            if g.get("hms"):
                try:
                    t = datetime.strptime(g["hms"], "%H%M%S")
                    when, precision = d.replace(
                        hour=t.hour, minute=t.minute, second=t.second), SECOND
                except ValueError:
                    when, precision = d, DAY
            else:
                when, precision = d, DAY

        elif g.get("date"):
            try:
                d = datetime.strptime(g["date"], "%Y-%m-%d")
            except ValueError:
                continue
            if g.get("time"):
                try:
                    t = datetime.strptime(g["time"], "%H-%M-%S")
                    when, precision = d.replace(
                        hour=t.hour, minute=t.minute, second=t.second), SECOND
                except ValueError:
                    when, precision = d, DAY
            else:
                when, precision = d, DAY

        if _valid(when):
            return FilenameParse(
                when=when, precision=precision, hint=hint,
                make=(g.get("make") or None), model=(g.get("model") or None),
            )
    return None


# --------------------------------------------------------------------------
# Folders
# --------------------------------------------------------------------------

@dataclass
class FolderParse:
    year: int | None = None
    month: int | None = None
    label: str | None = None

    @property
    def empty(self) -> bool:
        return self.year is None and self.label is None


def _compile_folder_patterns(patterns: list[str]):
    out = []
    for p in patterns:
        try:
            out.append(re.compile(p))
        except re.error:
            continue
    return out


def parse_folder(name: str, compiled) -> FolderParse:
    for rx in compiled:
        m = rx.match(name)
        if not m:
            continue
        g = m.groupdict()
        year = int(g["year"]) if g.get("year") else None
        month = int(g["month"]) if g.get("month") else None
        if year is not None and not (1900 <= year <= _max_date().year):
            year = None
        if month is not None and not (1 <= month <= 12):
            month = None
        label = (g.get("label") or "").strip() or None
        return FolderParse(year=year, month=month, label=label)
    return FolderParse()


def folder_date(fp: FolderParse) -> tuple[datetime, str] | None:
    if fp.year is None:
        return None
    if fp.month:
        return datetime(fp.year, fp.month, 1), MONTH
    return datetime(fp.year, 1, 1), YEAR


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

@dataclass
class Resolved:
    when: datetime
    source: str          # exif | filename | folder | mtime
    precision: str
    conflict: bool = False
    conflict_note: str | None = None


def resolve(
    exif_dt: datetime | None,
    fn: FilenameParse | None,
    fp: FolderParse,
    mtime: datetime,
) -> Resolved:
    fd = folder_date(fp)
    conflict, note = False, None

    # Cross-validation: EXIF and a dated filename are both present on ~95% of
    # the reference library, so they can check each other for free. A camera
    # clock that was never set shows up here and nowhere else.
    if exif_dt and fn and abs(exif_dt - fn.when) > timedelta(minutes=1):
        conflict = True
        note = (f"exif {exif_dt:%Y-%m-%d %H:%M} vs "
                f"filename {fn.when:%Y-%m-%d %H:%M}")

    if exif_dt:
        return Resolved(exif_dt, "exif", SECOND, conflict, note)

    if fn:
        if fd and abs(fn.when - fd[0]) > _PRECISION_WINDOW[fd[1]]:
            conflict = True
            note = (f"filename {fn.when:%Y-%m-%d} vs "
                    f"folder {fd[0]:%Y-%m}")
        return Resolved(fn.when, "filename", fn.precision, conflict, note)

    if fd:
        return Resolved(fd[0], "folder", fd[1], conflict, note)

    return Resolved(mtime, "mtime", SECOND, conflict, note)


def compile_patterns(scan_cfg: dict):
    """Compile both pattern sets once, for reuse across a whole run."""
    return (
        _compile_filename_patterns(scan_cfg.get("filename_patterns", [])),
        _compile_folder_patterns(scan_cfg.get("folder_patterns", [])),
    )
