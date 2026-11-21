"""`mediaidx update`: the routine sequence as one command, for Media Organizer."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from mediaidx import dates, db
from mediaidx.update import EVENT_PREFIX, new_year_folders

P = "\\\\NAS\\Media\\Pictures"


def _fake(tree: dict[str, list[str]]):
    def listdir(path):
        path = path.removeprefix("\\\\?\\UNC\\")
        path = "\\\\" + path if not path.startswith("\\\\") else path
        return tree[path]
    return listdir


def test_the_next_year_beside_the_year_roots_is_found():
    roots = [f"{P}\\2025", f"{P}\\2026"]
    tree = {P: ["2025", "2026", "2027", "Unsorted", "Scans", "New Images"]}
    got = new_year_folders(roots, listdir=_fake(tree), isdir=lambda p: True)
    assert got == [f"{P}\\2027"]


def test_nothing_new_when_every_year_is_a_root():
    roots = [f"{P}\\2025", f"{P}\\2026"]
    tree = {P: ["2025", "2026", "Unsorted"]}
    assert new_year_folders(roots, listdir=_fake(tree), isdir=lambda p: True) == []


def test_a_parent_with_no_year_root_is_never_looked_in():
    """`2025 12 Teneriffa` is a root, but its parent is not a home of years."""
    roots = [f"{P}\\2025", f"{P}\\2025\\2025 12 Teneriffa"]
    tree = {P: ["2025"]}            # P\2025 is not listed: KeyError if it were
    assert new_year_folders(roots, listdir=_fake(tree), isdir=lambda p: True) == []


def test_a_file_named_like_a_year_is_not_a_folder():
    roots = [f"{P}\\2026"]
    tree = {P: ["2026", "2027"]}
    got = new_year_folders(roots, listdir=_fake(tree),
                           isdir=lambda p: not p.endswith("2027"))
    assert got == []


def test_roots_compare_without_regard_to_case():
    roots = [f"{P}\\2026", f"{P.lower()}\\2025"]
    tree = {P: ["2025", "2026"]}
    assert new_year_folders(roots, listdir=_fake(tree), isdir=lambda p: True) == []


def test_an_archive_that_does_not_answer_is_an_error_not_an_empty_year():
    def asleep(path):
        raise OSError("the network name cannot be found")
    with pytest.raises(OSError):
        new_year_folders([f"{P}\\2026"], listdir=asleep, isdir=lambda p: True)


# ---------------------------------------------------------------- end to end

def _library(tmp_path):
    pictures = tmp_path / "Pictures"
    (pictures / "2026").mkdir(parents=True)
    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)
    db.upsert_folder(con, str(pictures / "2026"), dates.parse_folder("2026", []),
                     is_root=True, now=0)
    con.commit()
    con.close()
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[paths]\n"
        f'database = "{dbp.as_posix()}"\n'
        f'thumbnails = "{(tmp_path / "t").as_posix()}"\n'
        f'logs = "{(tmp_path / "l").as_posix()}"\n',
        encoding="utf-8")
    return pictures, dbp, cfg


def _run(cfg, *extra):
    env = {k: v for k, v in os.environ.items() if k != "MEDIAIDX_CONFIG"}
    r = subprocess.run(
        [sys.executable, "-m", "mediaidx", "--config", str(cfg), "update", *extra],
        capture_output=True, env=env, timeout=300)
    out = r.stdout.decode("utf-8")
    events = [json.loads(line[len(EVENT_PREFIX):]) for line in out.splitlines()
              if line.startswith(EVENT_PREFIX)]
    return r, out, events


def test_an_update_with_nothing_new_runs_every_stage_and_says_so(tmp_path):
    pictures, dbp, cfg = _library(tmp_path)
    (pictures / "2027").mkdir()
    (pictures / "Unsorted").mkdir()

    r, out, events = _run(cfg, "--no-publish", "--events")
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")

    stages = [e["stage"] for e in events if "stage" in e]
    assert stages == ["scan", "embed", "faces", "cluster", "derive"]
    assert [e["plan"] for e in events if "plan" in e] == [stages]
    assert events[-1]["done"] is True and events[-1]["ok"] is True
    assert events[-1]["photographs"] == 0
    assert events[-1]["published"] is False
    assert events[-1]["added_roots"] == [str(pictures / "2027")]

    con = db.connect(dbp, create=False)
    roots = {r[0] for r in con.execute("SELECT path FROM folders WHERE is_root = 1")}
    assert str(pictures / "2027") in roots
    assert str(pictures / "Unsorted") not in roots


def _with_export(tmp_path, cfg):
    names = tmp_path / "repo" / "names.json"
    extra = tmp_path / "nas" / "names.json"
    with open(cfg, "a", encoding="utf-8") as f:
        f.write(f'names_export = "{names.as_posix()}"\n'
                f'names_export_extra = ["{extra.as_posix()}"]\n')
    return names, extra


def test_names_json_is_written_when_out_of_date_and_left_alone_when_not(tmp_path):
    _, dbp, cfg = _library(tmp_path)
    names, extra = _with_export(tmp_path, cfg)

    r, out, events = _run(cfg, "--no-publish", "--events")
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert [e["stage"] for e in events if "stage" in e][-1] == "export"
    assert names.exists() and extra.exists()
    first = names.read_bytes()

    # Nothing decided by hand since: neither copy is rewritten, so the one in
    # the repository does not change on every update for a new time stamp.
    r, out, _ = _run(cfg, "--no-publish")
    assert r.returncode == 0
    assert "nothing to write" in out
    assert names.read_bytes() == first

    # A name given in the UI makes it due again, and a copy gone missing too.
    con = db.connect(dbp, create=False)
    con.execute("INSERT INTO persons (name, source, created_at) VALUES ('Emmi', 'manual', 0)")
    con.commit()
    con.close()
    extra.unlink()
    r, out, _ = _run(cfg, "--no-publish")
    assert r.returncode == 0
    assert "Emmi" in json.loads(names.read_text(encoding="utf-8"))["persons"]
    assert extra.exists()


def test_without_events_no_machine_lines_are_written(tmp_path):
    _, _, cfg = _library(tmp_path)
    r, out, events = _run(cfg, "--no-publish")
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert events == []
    assert "== 1/5  scan" in out


def test_no_roots_is_refused_with_a_reason(tmp_path):
    _, dbp, cfg = _library(tmp_path)
    con = db.connect(dbp, create=False)
    con.execute("UPDATE folders SET is_root = 0")
    con.commit()
    con.close()
    r, out, events = _run(cfg, "--events")
    assert r.returncode == 1
    assert events == [{"done": True, "ok": False, "failed": "scan",
                       "error": "no roots recorded yet"}]
