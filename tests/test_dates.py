"""Date resolution: every rule in spec §5.1 fires on the right input."""

from datetime import datetime

import pytest

from mediaidx import config, dates

# A convention belonging to one particular library, supplied by its owner. The
# application ships no knowledge of it (P7); these tests provide it the same way
# a config.toml would.
ARCHIVE = {
    "re": r"^(?P<ymd>\d{8})@(?P<hms>\d{6})(?:-(?P<make>.+?)-(?P<model>.+?))?-\d+$",
    "hint": "archive",
}

_scan = dict(config.DEFAULTS["scan"])
_scan["filename_patterns"] = [ARCHIVE] + _scan["filename_patterns"]

FN, FOLDER = dates.compile_patterns(_scan)


def test_library_specific_convention_is_not_shipped():
    """P7: the application must not know how one person named their files."""
    shipped, _ = dates.compile_patterns(config.DEFAULTS["scan"])
    assert dates.parse_filename("20240101@151202-Apple-iPhone 15-03", shipped) is None




def fn(stem):
    return dates.parse_filename(stem, FN)


def folder(name):
    return dates.parse_folder(name, FOLDER)


# --- filename patterns ----------------------------------------------------

def test_archive_convention_gives_second_precision_and_camera():
    r = fn("20240101@151202-Apple-iPhone 15-03")
    assert r.when == datetime(2024, 1, 1, 15, 12, 2)
    assert r.precision == dates.SECOND
    assert r.hint == "archive"
    assert r.make == "Apple"
    assert r.model == "iPhone 15"


def test_archive_convention_without_camera_fields():
    r = fn("20010116@200800-116")
    assert r.when == datetime(2001, 1, 16, 20, 8, 0)
    assert r.make is None


def test_whatsapp_is_day_precision():
    r = fn("IMG-20190608-WA0012")
    assert r.when == datetime(2019, 6, 8)
    assert r.precision == dates.DAY
    assert r.hint == "whatsapp"


def test_screenshot_hint():
    assert fn("Screenshot_2019-06-08-13-40-22").hint == "screenshot"


@pytest.mark.parametrize("stem", [
    "IMG_99999999_001",     # date-shaped but not a date
    "IMG_1234",             # iPhone default, no date at all
    "DSC00042",
    "20190229@120000-x-y-01",  # 2019 is not a leap year
])
def test_non_dates_fall_through(stem):
    assert fn(stem) is None


def test_sanity_bound_rejects_pre_1990():
    assert fn("19700101@000000-a-b-01") is None


# --- folder patterns ------------------------------------------------------

def test_folder_year_month_and_label():
    p = folder("2019 05 Hennan rippijuhlat")
    assert (p.year, p.month, p.label) == (2019, 5, "Hennan rippijuhlat")


def test_folder_bare_month_has_no_label():
    p = folder("2019 01")
    assert (p.year, p.month, p.label) == (2019, 1, None)


def test_unrecognised_folder_yields_nothing():
    assert folder("Anskun").empty


# --- resolution order -----------------------------------------------------

MTIME = datetime(2006, 12, 3, 23, 23, 8)


def test_exif_wins():
    r = dates.resolve(datetime(2001, 1, 16, 20, 8), fn("20010116@200800-116"),
                      folder("2001 01 Thaimaa"), MTIME)
    assert r.source == "exif" and r.precision == dates.SECOND
    assert not r.conflict


def test_filename_used_when_exif_absent():
    r = dates.resolve(None, fn("20020714@180000-x-y-01"), folder("2002 07"), MTIME)
    assert r.source == "filename"
    assert r.when == datetime(2002, 7, 14, 18, 0)


def test_folder_used_when_exif_and_filename_absent():
    r = dates.resolve(None, None, folder("2002 07"), MTIME)
    assert r.source == "folder" and r.precision == dates.MONTH
    assert r.when == datetime(2002, 7, 1)


def test_year_only_folder():
    r = dates.resolve(None, None, folder("2002"), MTIME)
    assert r.precision == dates.YEAR


def test_mtime_is_the_last_resort():
    r = dates.resolve(None, None, folder("Unsorted"), MTIME)
    assert r.source == "mtime" and r.when == MTIME


# --- cross-validation -----------------------------------------------------

def test_exif_and_filename_disagreement_is_flagged():
    r = dates.resolve(datetime(2019, 1, 1, 12, 0), fn("20190608@120000-x-y-01"),
                      folder("2019 06"), MTIME)
    assert r.source == "exif"
    assert r.conflict and "vs" in r.conflict_note


def test_agreement_is_not_flagged():
    r = dates.resolve(datetime(2024, 1, 1, 15, 12, 2),
                      fn("20240101@151202-Apple-iPhone 15-03"),
                      folder("2024 01"), MTIME)
    assert not r.conflict


def test_filename_outside_folder_month_is_flagged():
    r = dates.resolve(None, fn("20190608@120000-x-y-01"), folder("2019 01"), MTIME)
    assert r.source == "filename" and r.conflict
