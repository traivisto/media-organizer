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
