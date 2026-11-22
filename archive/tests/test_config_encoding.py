"""Config files written by Windows tools must still load.

Windows PowerShell 5.1's `-Encoding utf8` prepends a UTF-8 BOM, and tomllib
rejects it with "Invalid statement (at line 1, column 1)" -- an error that says
nothing about the actual cause. Notepad and several editors do the same. Since
a config is a file a person edits on Windows, tolerating a BOM is the correct
behaviour rather than a workaround.
"""

from __future__ import annotations

import pytest

from mediaidx import config

BODY = """
[scan]
reader_threads = 3
"""


def test_config_with_utf8_bom_loads(tmp_path):
    p = tmp_path / "config.toml"
    p.write_bytes(b"\xef\xbb\xbf" + BODY.encode("utf-8"))
    assert config.load(p).scan["reader_threads"] == 3


def test_config_without_bom_still_loads(tmp_path):
    p = tmp_path / "config.toml"
    p.write_bytes(BODY.encode("utf-8"))
    assert config.load(p).scan["reader_threads"] == 3


def test_non_ascii_values_survive(tmp_path):
    """Finnish paths and labels are ordinary content here, not an edge case."""
    p = tmp_path / "config.toml"
    p.write_bytes("﻿".encode("utf-8") + b"""
[scan]
folder_patterns_extra = ['^(?P<label>Hyv\xc3\xa4\xc3\xa4 .+)$']
""")
    _, folder = __import__("mediaidx.dates", fromlist=["x"]).compile_patterns(
        config.load(p).scan)
    from mediaidx import dates
    assert dates.parse_folder("Hyvää yötä", folder).label == "Hyvää yötä"


def test_broken_toml_fails_with_the_file_named(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("this is not toml = = =", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        config.load(p)
    assert "config.toml" in str(e.value)
