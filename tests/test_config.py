"""Configuration, and the separation it is there to enforce (spec P7).

The application ships conventions that belong to *applications* — what a phone
or a messaging app names its files, which is the same on everyone's machine. It
ships nothing that belongs to one particular *library*: no path, no host, no
share, no folder naming scheme, no person mapping. Those arrive from the user.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mediaidx import config, dates

ARCHIVE_RE = r"^(?P<ymd>\d{8})@(?P<hms>\d{6})(?:-(?P<make>.+?)-(?P<model>.+?))?-\d+$"


@pytest.fixture
def nowhere(tmp_path: Path) -> Path:
    """A config path that genuinely does not exist.

    Not os.devnull: on Windows `Path("nul").exists()` is True, so it reads as a
    config file that was found and left empty.
    """
    return tmp_path / "absent.toml"


def write_cfg(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(body, encoding="utf-8")
    return p


# --- what must NOT be shipped --------------------------------------------

def test_defaults_name_no_library(nowhere):
    """No path, host, share or person mapping in the shipped configuration."""
    cfg = config.load(nowhere)
    assert cfg.scan.get("roots") is None
    assert "people" not in config.DEFAULTS
    blob = repr(config.DEFAULTS).lower()
    for leak in ("tradiskstation", "z:", "pictures", "onedrive", "anskun"):
        assert leak not in blob, f"{leak!r} is baked into the defaults"


def test_a_fresh_install_indexes_nothing_until_told(nowhere):
    cfg = config.load(nowhere)
    assert cfg.source is None            # no config file was found
    assert not hasattr(cfg, "roots")     # and no roots to act on


# --- what the user supplies ----------------------------------------------

def test_extra_filename_patterns_are_tried_before_the_defaults(tmp_path):
    cfg = config.load(write_cfg(tmp_path, f"""
[scan]
filename_patterns_extra = [
  {{ re = '{ARCHIVE_RE}', hint = "archive" }},
]
"""))
    fn, _ = dates.compile_patterns(cfg.scan)
    got = dates.parse_filename("20240101@151202-Apple-iPhone 15-03", fn)
    assert got is not None and got.hint == "archive"
    assert got.make == "Apple"


def test_supplying_extras_does_not_lose_the_shipped_defaults(tmp_path):
    """Adding your own convention must not mean restating the universal ones."""
    cfg = config.load(write_cfg(tmp_path, f"""
[scan]
filename_patterns_extra = [
  {{ re = '{ARCHIVE_RE}', hint = "archive" }},
]
"""))
    fn, _ = dates.compile_patterns(cfg.scan)
    assert dates.parse_filename("IMG-20190608-WA0012", fn).hint == "whatsapp"


def test_a_library_with_no_convention_still_works(tmp_path):
    """Emptying both pattern lists must not break indexing (spec §5.1)."""
    cfg = config.load(write_cfg(tmp_path, """
[scan]
filename_patterns = []
folder_patterns = []
"""))
    fn, folder = dates.compile_patterns(cfg.scan)
    assert dates.parse_filename("20240101@151202-Apple-iPhone 15-03", fn) is None
    assert dates.parse_folder("2019 05 Hennan rippijuhlat", folder).empty


def test_extra_folder_patterns_prepend_too(tmp_path):
    cfg = config.load(write_cfg(tmp_path, r"""
[scan]
folder_patterns_extra = ['^(?P<label>Trip .+)$']
"""))
    _, folder = dates.compile_patterns(cfg.scan)
    assert dates.parse_folder("Trip to Alicante", folder).label == "Trip to Alicante"
    # the shipped pattern still applies to everything else
    assert dates.parse_folder("2019 05", folder).year == 2019


def test_paths_default_outside_any_sync_folder(nowhere):
    from mediaidx import paths as pathutil
    cfg = config.load(nowhere)
    assert pathutil.is_in_sync_folder(str(cfg.database)) is None
