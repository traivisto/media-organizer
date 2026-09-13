"""Everything done by hand, written out and read back (P6, P8).

P6 says deleting the database and rebuilding must lose nothing except what a
person supplied. That is only true if there is something to restore it from, so
the test that matters here is not that a file is written -- it is that a
database built from nothing, whose every row id differs and whose faces have
been detected afresh, comes back knowing who everybody is.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from mediaidx import corrections, db, export, faces, odd


def _unit(rng, dim=faces.DIM):
    v = rng.normal(size=dim)
    return (v / np.linalg.norm(v)).astype(np.float32)


def _library(path, *, boxes=((0.1, 0.1, 0.2, 0.2), (0.6, 0.1, 0.2, 0.2)),
            order=(0, 1)):
    """Six photographs, two faces each, written in a given detection order."""
    con = db.connect(path)
    db.migrate(con)
    rng = np.random.default_rng(21)
    for i in range(6):
        h = f"{i:064d}"
        aid = db.insert_asset(con, h, {"format": "jpeg",
                                       "taken_at": 1_600_000_000 + i})
        con.execute(
            """INSERT INTO files (path, asset_id, size_bytes, mtime,
                                  first_seen, last_seen)
               VALUES (?,?,1,1,1,1)""", (f"\\\\nas\\{h}.jpg", aid))
        faces.replace_for_asset(con, aid, [
            faces.DetectedFace(bbox=boxes[j], det_score=0.9, blur=9.0,
                               embedding=_unit(rng)) for j in order],
            vec_ok=False)
    con.commit()
    return con


def _decide(con):
    """Everything a person might have decided, one of each kind."""
    con.execute("INSERT INTO persons (id, name, source, created_at) "
                "VALUES (1,'Ansku','manual',0), (2,'Tommi','manual',0), "
                "(3,'Emmiliina','manual',0)")
    # Ansku on the left of every photograph, Tommi on the right of the first two.
    con.execute("UPDATE faces SET person_id = 1 WHERE idx = 0")
    con.execute("""UPDATE faces SET person_id = 2 WHERE idx = 1
                    AND asset_id IN (SELECT id FROM assets ORDER BY id LIMIT 2)""")
    con.execute("INSERT INTO event_names (anchor_id, name, dismissed, created_at) "
                "VALUES ((SELECT id FROM assets ORDER BY id LIMIT 1), "
                "'Rippijuhlat', 0, 0)")
    con.execute("INSERT INTO event_cuts (asset_id, created_at) VALUES "
                "((SELECT id FROM assets ORDER BY id LIMIT 1 OFFSET 3), 0)")
    con.execute("INSERT INTO place_exceptions (label_norm, place_path, created_at)"
                " VALUES ('west coast', 'New Zealand/West Coast', 0)")
    corrections.record(con, "date", f"{2:064d}", "1998-07", note="rippijuhlat")
    odd.dismiss(con, con.execute(
        "SELECT id FROM assets ORDER BY id LIMIT 1 OFFSET 4").fetchone()[0])
    con.commit()


# --------------------------------------------------------------------------
# Writing it out
# --------------------------------------------------------------------------

def test_the_export_holds_every_kind_of_decision(tmp_path):
    con = _library(tmp_path / "a.db")
    _decide(con)
    data = export.dump(con)
    assert len(data["persons"]) == 3
    assert len(data["faces"]) == 8            # six left, two right
    assert len(data["events"]) == 1
    assert len(data["cuts"]) == 1
    assert len(data["places_rejected"]) == 1
    assert len(data["corrections"]) == 1
    assert len(data["not_odd"]) == 1


def test_nothing_in_it_is_a_row_id(tmp_path):
    """The file has to be readable by a database where every id is different."""
    con = _library(tmp_path / "a.db")
    _decide(con)
    data = export.dump(con)
    for f in data["faces"]:
        assert set(f) == {"hash", "idx", "bbox", "name"}
    assert all(len(e["hash"]) == 64 for e in data["events"])
    assert all(len(h) == 64 for h in data["cuts"] + data["not_odd"])


def test_a_person_with_no_faces_is_still_kept(tmp_path):
    """A spelling somebody settled on outlives the last photograph of them."""
    con = _library(tmp_path / "a.db")
    _decide(con)
    assert "Emmiliina" in export.dump(con)["persons"]


def test_the_file_is_replaced_only_once_it_is_whole(tmp_path):
    """It lives in a syncing folder; a half-written one would be copied away."""
    con = _library(tmp_path / "a.db")
    _decide(con)
    out = tmp_path / "names" / "names.json"
    export.write(con, out)
    assert out.exists() and not list(out.parent.glob("*.part"))
    assert json.loads(out.read_text(encoding="utf-8"))["version"] == export.VERSION


# --------------------------------------------------------------------------
# Reading it back -- the promise itself
# --------------------------------------------------------------------------

def test_a_database_built_from_nothing_knows_everyone_again(tmp_path):
    """P6, in one test.

    The second library has different row ids throughout and has detected its
    faces again. Only the content hash and the box connect the two.
    """
    src = _library(tmp_path / "a.db")
    _decide(src)
    data = export.dump(src)

    fresh = _library(tmp_path / "b.db")
    assert fresh.execute(
        "SELECT COUNT(*) FROM faces WHERE person_id IS NOT NULL").fetchone()[0] == 0

    st = export.load(fresh, data)
    fresh.commit()

    assert st.faces == 8 and st.missing_faces == 0
    named = {r[0]: r[1] for r in fresh.execute(
        """SELECT p.name, COUNT(*) FROM faces f JOIN persons p ON p.id = f.person_id
            GROUP BY p.name""")}
    assert named == {"Ansku": 6, "Tommi": 2}
    assert fresh.execute("SELECT name FROM event_names").fetchone()[0] == "Rippijuhlat"
    assert fresh.execute("SELECT COUNT(*) FROM event_cuts").fetchone()[0] == 1
    assert fresh.execute("SELECT COUNT(*) FROM place_exceptions").fetchone()[0] == 1
    assert fresh.execute("SELECT COUNT(*) FROM odd_dismissed").fetchone()[0] == 1
    assert fresh.execute(
        "SELECT value FROM corrections WHERE kind='date'").fetchone()[0] == "1998-07"


def test_names_follow_the_face_when_the_detector_reorders_them(tmp_path):
    """`idx` is the detector's output order and means nothing between runs.

    The rebuilt library finds the same two faces the other way round. Ansku must
    still be the one on the left.
    """
    src = _library(tmp_path / "a.db")
    _decide(src)
    data = export.dump(src)

    # Same boxes, opposite detection order.
    fresh = _library(tmp_path / "b.db", order=(1, 0))
    export.load(fresh, data)
    fresh.commit()

    left = fresh.execute(
        """SELECT p.name FROM faces f JOIN persons p ON p.id = f.person_id
            WHERE f.bbox LIKE '[0.1%' LIMIT 1""").fetchone()
    assert left is not None and left[0] == "Ansku", "the name followed the index"


def test_a_photograph_this_database_lacks_is_counted_not_refused(tmp_path):
    """An export of the whole archive is worth restoring onto half of it."""
    src = _library(tmp_path / "a.db")
    _decide(src)
    data = export.dump(src)
    data["faces"].append({"hash": "f" * 64, "idx": 0,
                          "bbox": [0.1, 0.1, 0.2, 0.2], "name": "Ansku"})

    fresh = _library(tmp_path / "b.db")
    st = export.load(fresh, data)
    assert st.missing_assets == 1
    assert st.faces == 8, "the ones it does hold were restored anyway"


def test_restoring_twice_changes_nothing_the_second_time(tmp_path):
    con = _library(tmp_path / "a.db")
    _decide(con)
    data = export.dump(con)
    fresh = _library(tmp_path / "b.db")
    export.load(fresh, data)
    fresh.commit()
    before = export.dump(fresh)
    export.load(fresh, data)
    fresh.commit()
    after = export.dump(fresh)
    for k in ("persons", "faces", "events", "cuts", "places_rejected",
              "corrections", "not_odd"):
        assert before[k] == after[k], f"{k} changed on a second restore"


def test_an_export_from_another_version_is_refused(tmp_path):
    con = _library(tmp_path / "a.db")
    with pytest.raises(ValueError):
        export.load(con, {"version": 99})


# --------------------------------------------------------------------------
# The backup taken without being asked
# --------------------------------------------------------------------------

def test_a_snapshot_is_written_beside_the_database(tmp_path):
    con = _library(tmp_path / "a.db")
    _decide(con)
    got = export.snapshot(con, tmp_path / "a.db")
    assert got is not None and got.parent.name == "backups"
    assert json.loads(got.read_text(encoding="utf-8"))["faces"]


def test_a_database_with_nothing_handmade_writes_no_snapshot(tmp_path):
    """A backup of nothing is noise in a folder somebody has to read."""
    con = _library(tmp_path / "a.db")
    assert export.snapshot(con, tmp_path / "a.db") is None


def test_snapshots_do_not_pile_up_forever(tmp_path):
    con = _library(tmp_path / "a.db")
    _decide(con)
    import time as _t
    for _ in range(export.KEEP + 3):
        export.snapshot(con, tmp_path / "a.db")
        _t.sleep(0.01)
    kept = list((tmp_path / "backups").glob("names-*.json"))
    assert len(kept) <= export.KEEP


def test_a_snapshot_restores_what_was_there(tmp_path):
    """The point of it: the minutes after something that cannot be undone."""
    con = _library(tmp_path / "a.db")
    _decide(con)
    got = export.snapshot(con, tmp_path / "a.db")
    con.execute("UPDATE faces SET person_id = NULL")
    con.execute("DELETE FROM corrections")
    con.commit()
    export.read(con, got)
    con.commit()
    assert con.execute(
        "SELECT COUNT(*) FROM faces WHERE person_id IS NOT NULL").fetchone()[0] == 8


# --------------------------------------------------------------------------
# What a person typed at `add` time
# --------------------------------------------------------------------------

def _folders(con):
    """Two roots, one of them labelled and hinted at, and one plain subfolder."""
    from mediaidx import dates
    parsed = dates.FolderParse(year=None, month=None, label=None)
    db.upsert_folder(con, r"\nas\Pictures\2019", parsed, is_root=True, now=1)
    db.upsert_folder(con, r"\nas\Pictures\Anskun", parsed, is_root=True,
                     user_label="Anskun kuvat", person_hint="Ansku", now=1)
    db.upsert_folder(con, r"\nas\Pictures\2019\07", parsed)
    con.commit()


def test_the_export_carries_the_roots_and_the_hints(tmp_path):
    """Typed by a person, and derivable from nothing in the archive."""
    con = _library(tmp_path / "a.db")
    _folders(con)
    got = {f["path"]: f for f in export.dump(con)["folders"]}
    assert set(got) == {r"\nas\Pictures\2019", r"\nas\Pictures\Anskun"}
    assert got[r"\nas\Pictures\Anskun"]["person_hint"] == "Ansku"
    assert got[r"\nas\Pictures\Anskun"]["user_label"] == "Anskun kuvat"


def test_a_folder_nobody_said_anything_about_is_not_in_it(tmp_path):
    """The export is what a person decided, not a copy of the folder tree."""
    con = _library(tmp_path / "a.db")
    _folders(con)
    paths = {f["path"] for f in export.dump(con)["folders"]}
    assert r"\nas\Pictures\2019\07" not in paths


def test_a_restored_database_knows_which_folders_to_scan(tmp_path):
    """So the rebuild is `import` and `scan`, not thirty paths retyped."""
    src = _library(tmp_path / "a.db")
    _folders(src)
    data = export.dump(src)

    fresh = db.connect(tmp_path / "b.db")
    db.migrate(fresh)
    st = export.load(fresh, data)
    fresh.commit()

    assert st.folders == 2
    roots = {r["path"]: r for r in fresh.execute(
        "SELECT path, person_hint, user_label FROM folders WHERE is_root = 1")}
    assert set(roots) == {r"\nas\Pictures\2019", r"\nas\Pictures\Anskun"}
    assert roots[r"\nas\Pictures\Anskun"]["person_hint"] == "Ansku"


def test_restoring_does_not_flatten_a_folder_that_has_since_been_scanned(tmp_path):
    """`add` fills in the parsed year; a restore must not wipe it."""
    from mediaidx import dates
    con = _library(tmp_path / "a.db")
    _folders(con)
    data = export.dump(con)
    db.upsert_folder(con, r"\nas\Pictures\2019",
                     dates.FolderParse(year=2019, month=7, label="Alicante"))
    con.commit()

    export.load(con, data)
    con.commit()
    row = con.execute("SELECT parsed_year, parsed_label, is_root FROM folders "
                      "WHERE path = ?", (r"\nas\Pictures\2019",)).fetchone()
    assert (row["parsed_year"], row["parsed_label"], row["is_root"]) == (
        2019, "Alicante", 1)


def test_an_export_written_before_folders_existed_still_reads(tmp_path):
    """Version 1 is what every snapshot on disk was written by."""
    con = _library(tmp_path / "a.db")
    _decide(con)
    data = export.dump(con)
    data["version"] = 1
    del data["folders"]

    fresh = _library(tmp_path / "b.db")
    st = export.load(fresh, data)
    fresh.commit()
    assert st.faces == 8 and st.folders == 0


# --------------------------------------------------------------------------
# The copy on storage that is backed up on its own schedule
# --------------------------------------------------------------------------

def _cli_library(tmp_path):
    """A database with one decision in it, and a config naming two exports."""
    con = _library(tmp_path / "photos.db")
    _decide(con)
    con.close()
    return tmp_path / "photos.db"


def _cfg(tmp_path, dbp, primary, extra):
    body = ["[paths]",
            f'database = "{dbp.as_posix()}"',
            f'thumbnails = "{(tmp_path / "t").as_posix()}"',
            f'logs = "{(tmp_path / "l").as_posix()}"',
            f'names_export = "{primary.as_posix()}"',
            "names_export_extra = ["
            + ", ".join(f'"{p.as_posix()}"' for p in extra) + "]"]
    p = tmp_path / "config.toml"
    p.write_text("\n".join(body) + "\n", encoding="utf-8")
    return p


def _run(cfg, *args):
    import subprocess
    import sys
    return subprocess.run(
        [sys.executable, "-m", "mediaidx", "--config", str(cfg), *args],
        capture_output=True, text=True, encoding="utf-8")


def test_every_named_copy_is_written_and_they_are_identical(tmp_path):
    """One dump, several destinations: the backup is not a near-copy."""
    dbp = _cli_library(tmp_path)
    primary = tmp_path / "one" / "names.json"
    away = tmp_path / "nas" / "names.json"
    r = _run(_cfg(tmp_path, dbp, primary, [away]), "export")

    assert r.returncode == 0, r.stderr
    assert primary.read_bytes() == away.read_bytes()
    assert json.loads(away.read_text(encoding="utf-8"))["faces"]


def test_a_copy_that_could_not_be_written_is_never_quiet(tmp_path):
    """A NAS that was asleep must not look like a backup that happened."""
    dbp = _cli_library(tmp_path)
    primary = tmp_path / "one" / "names.json"
    blocked = tmp_path / "blocker"
    blocked.write_text("not a directory", encoding="utf-8")

    r = _run(_cfg(tmp_path, dbp, primary, [blocked / "names.json"]), "export")

    assert r.returncode != 0
    assert "NOT written" in r.stderr
    assert primary.exists(), "the copy that could be written still was"


def test_a_named_file_means_that_file_and_no_other(tmp_path):
    """`export <file>` is a one-off; it does not fan out to the backups."""
    dbp = _cli_library(tmp_path)
    primary = tmp_path / "one" / "names.json"
    away = tmp_path / "nas" / "names.json"
    asked = tmp_path / "asked.json"
    r = _run(_cfg(tmp_path, dbp, primary, [away]), "export", "all", str(asked))

    assert r.returncode == 0, r.stderr
    assert asked.exists() and not away.exists() and not primary.exists()
