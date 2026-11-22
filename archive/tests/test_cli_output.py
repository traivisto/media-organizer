"""What the command line writes, and in what encoding.

The gazetteer is worldwide, so its place names leave cp1252 immediately. On
Windows a redirected stdout gets the ANSI codepage, and printing a Greek place
name then raises UnicodeEncodeError -- after the pass has already done its
work, which is the worst moment to lose the output.
"""

from __future__ import annotations

import subprocess
import sys
import sqlite3

from mediaidx import db

GREEK = "Greece/South Aegean/Dodecanese/Ρόδος"


def _index(tmp_path):
    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)
    con.execute(
        "INSERT INTO place_exceptions (label_norm, place_path, created_at) "
        "VALUES (?,?,0)", ("rodos", GREEK))
    con.commit()
    con.close()

    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[paths]\n"
        f'database = "{dbp.as_posix()}"\n'
        f'thumbnails = "{(tmp_path / "t").as_posix()}"\n'
        f'logs = "{(tmp_path / "l").as_posix()}"\n',
        encoding="utf-8")
    return cfg


def test_a_place_name_outside_cp1252_survives_a_redirected_stdout(tmp_path):
    """Piped, not a console: this is the case Windows encodes as cp1252."""
    cfg = _index(tmp_path)
    env = {k: v for k, v in __import__("os").environ.items()
           if k != "PYTHONIOENCODING"}
    r = subprocess.run(
        [sys.executable, "-m", "mediaidx", "--config", str(cfg),
         "places", "--rejected"],
        capture_output=True, env=env)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert "Ρόδος" in r.stdout.decode("utf-8")
