"""Corrections, and what must not be able to undo one (spec §4.7, P8).

The whole point of this table is that an answer given by hand outlives every
operation the application can perform on itself. So these tests are mostly not
about storing a correction -- that part is a row -- but about running the very
things that would destroy it and finding it still there afterwards.
"""

from __future__ import annotations

import numpy as np
import pytest

from mediaidx import cluster, corrections, db, faces
from mediaidx.config import Config


def _cfg(tmp_path):
    return Config(database=tmp_path / "photos.db",
                  thumbnails=tmp_path / "thumbs", logs=tmp_path,
                  names_export=None, faces={"cluster_min_size": 3})


def _unit(x, dim=faces.DIM):
    """A unit vector: from a generator, or from a vector to normalise."""
    v = x.normal(size=dim) if hasattr(x, "normal") else x
    return (v / np.linalg.norm(v)).astype(np.float32)


@pytest.fixture
def built(tmp_path):
    """Nine photographs of one person, and five of another.

    Two groups rather than one, because a single uniform blob has no density
    contrast and HDBSCAN calls all of it noise -- and because the situation
    being tested is a real library, where one face inside a named cluster turns
    out to be somebody else.
    """
    con = db.connect(tmp_path / "photos.db")
    db.migrate(con)
    rng = np.random.default_rng(3)

    hashes = []
    n = 0
    for size in (9, 5):
        anchor = _unit(rng)
        for _ in range(size):
            v = _unit(anchor + rng.normal(scale=0.05, size=faces.DIM))
            h = f"{n:064d}"
            aid = db.insert_asset(con, h,
                                  {"format": "jpeg", "taken_at": 1_600_000_000})
            faces.replace_for_asset(con, aid, [faces.DetectedFace(
                bbox=(0.1, 0.1, 0.2, 0.2), det_score=0.9, blur=9.0,
                embedding=v)], vec_ok=False)
            if size == 9:
                hashes.append(h)
            n += 1
    con.commit()
    return con, hashes


# --------------------------------------------------------------------------
# Storing one
# --------------------------------------------------------------------------

def test_a_correction_replaces_the_earlier_answer(built):
    con, hashes = built
    corrections.record(con, "date", hashes[0], "1998-07")
    corrections.record(con, "date", hashes[0], "1999-08")
    rows = corrections.for_asset(con, hashes[0])
    assert len(rows) == 1 and rows[0]["value"] == "1999-08"


def test_two_kinds_about_one_photograph_coexist(built):
    con, hashes = built
    corrections.record(con, "date", hashes[0], "1998-07")
    corrections.record(con, "place", hashes[0], "Finland/Uusimaa/Helsinki")
    assert len(corrections.for_asset(con, hashes[0])) == 2


def test_a_correction_can_say_that_nothing_is_right(built):
    """`None` is not the absence of a correction but an assertion of its own."""
    con, hashes = built
    corrections.record(con, "date", hashes[0], None, note="no way to know")
    rows = corrections.for_asset(con, hashes[0])
    assert len(rows) == 1 and rows[0]["value"] is None
    assert corrections.clear(con, "date", hashes[0])
    assert corrections.for_asset(con, hashes[0]) == []


def test_an_unknown_kind_is_refused(built):
    con, hashes = built
    with pytest.raises(ValueError):
        corrections.record(con, "weather", hashes[0], "sunny")


# --------------------------------------------------------------------------
# Surviving a rebuild -- the reason any of this exists
# --------------------------------------------------------------------------

def test_a_corrected_face_is_not_outvoted_by_its_cluster(built, tmp_path):
    """The failure this design exists to prevent.

    A rebuild re-derives each cluster's identity by majority vote of its
    members' previous names. A hand-corrected face is one vote inside a cluster
    whose majority is the person it was wrongly grouped with -- because looking
    like them is exactly why it was put there -- so it loses that vote every
    time, and the correction is gone by morning.
    """
    con, hashes = built
    cfg = _cfg(tmp_path)
    cluster.build(con, cfg)
    cid = con.execute(
        "SELECT id FROM face_clusters ORDER BY size DESC").fetchone()["id"]
    cluster.set_person(con, cid, "Ansku")
    con.commit()

    # One of them is not Ansku at all.
    odd = hashes[-1]
    corrections.record(con, "person", odd, "Tommi", face_idx=0,
                       face_bbox=(0.1, 0.1, 0.2, 0.2))
    tommi = cluster.person_id_for(con, "Tommi")
    fid = con.execute(
        """SELECT f.id FROM faces f JOIN assets a ON a.id = f.asset_id
            WHERE a.content_hash = ?""", (odd,)).fetchone()["id"]
    con.execute("UPDATE faces SET person_id = ? WHERE id = ?", (tommi, fid))
    con.commit()

    cluster.build(con, cfg, rebuild=True)
    con.commit()

    who = con.execute(
        """SELECT p.name FROM faces f LEFT JOIN persons p ON p.id = f.person_id
            WHERE f.id = ?""", (fid,)).fetchone()["name"]
    assert who == "Tommi", "the rebuild voted the correction away"

    others = con.execute(
        """SELECT DISTINCT p.name FROM faces f
             JOIN persons p ON p.id = f.person_id WHERE f.id != ?""",
        (fid,)).fetchall()
    assert [r["name"] for r in others] == ["Ansku"], "the rest lost their name"
    assert con.execute(
        """SELECT COUNT(*) c FROM faces f JOIN persons p ON p.id = f.person_id
            WHERE p.name = 'Ansku'""").fetchone()["c"] == 8


def test_a_correction_survives_the_faces_being_detected_again(built):
    """Stage 2 deletes every face row for a photograph and writes new ones.

    `idx` is the detector's output order, so it means nothing between runs. The
    correction has to follow the face by where it sits in the frame.
    """
    con, hashes = built
    h = hashes[0]
    aid = con.execute("SELECT id FROM assets WHERE content_hash = ?",
                      (h,)).fetchone()["id"]
    corrections.record(con, "person", h, "Tommi", face_idx=0,
                    face_bbox=(0.10, 0.10, 0.20, 0.20))

    rng = np.random.default_rng(11)
    # Re-detected: two faces now, and the corrected one comes back second.
    faces.replace_for_asset(con, aid, [
        faces.DetectedFace(bbox=(0.60, 0.60, 0.20, 0.20), det_score=0.9,
                           blur=9.0, embedding=_unit(rng)),
        faces.DetectedFace(bbox=(0.11, 0.11, 0.19, 0.19), det_score=0.9,
                           blur=9.0, embedding=_unit(rng)),
    ], vec_ok=False)
    con.commit()

    row = corrections.for_asset(con, h)[0]
    assert row["face_idx"] == 1, "the correction stayed on the old index"


def test_a_name_survives_the_faces_being_detected_again(built):
    """Not only corrections: `person_id` lives on the rows being deleted."""
    con, hashes = built
    aid = con.execute("SELECT id FROM assets WHERE content_hash = ?",
                      (hashes[0],)).fetchone()["id"]
    con.execute("INSERT INTO persons (id, name, source, created_at) "
                "VALUES (1, 'Ansku', 'manual', 0)")
    con.execute("UPDATE faces SET person_id = 1 WHERE asset_id = ?", (aid,))

    rng = np.random.default_rng(12)
    carried, lost = faces.replace_for_asset(con, aid, [
        faces.DetectedFace(bbox=(0.09, 0.09, 0.22, 0.22), det_score=0.9,
                           blur=9.0, embedding=_unit(rng))], vec_ok=False)
    assert (carried, lost) == (1, 0)
    assert con.execute("SELECT person_id FROM faces WHERE asset_id = ?",
                       (aid,)).fetchone()["person_id"] == 1


def test_a_face_the_detector_stops_finding_is_reported_not_hidden(built):
    con, hashes = built
    aid = con.execute("SELECT id FROM assets WHERE content_hash = ?",
                      (hashes[0],)).fetchone()["id"]
    con.execute("INSERT INTO persons (id, name, source, created_at) "
                "VALUES (1, 'Ansku', 'manual', 0)")
    con.execute("UPDATE faces SET person_id = 1 WHERE asset_id = ?", (aid,))

    rng = np.random.default_rng(13)
    carried, lost = faces.replace_for_asset(con, aid, [
        faces.DetectedFace(bbox=(0.80, 0.80, 0.10, 0.10), det_score=0.9,
                           blur=9.0, embedding=_unit(rng))], vec_ok=False)
    assert (carried, lost) == (0, 1)


def test_two_people_side_by_side_are_not_confused(built):
    """The overlap bar has to be tight enough to tell neighbours apart."""
    con, hashes = built
    aid = con.execute("SELECT id FROM assets WHERE content_hash = ?",
                      (hashes[0],)).fetchone()["id"]
    rng = np.random.default_rng(14)
    faces.replace_for_asset(con, aid, [
        faces.DetectedFace(bbox=(0.10, 0.10, 0.15, 0.15), det_score=0.9,
                           blur=9.0, embedding=_unit(rng)),
        faces.DetectedFace(bbox=(0.30, 0.10, 0.15, 0.15), det_score=0.9,
                           blur=9.0, embedding=_unit(rng))], vec_ok=False)
    con.execute("INSERT INTO persons (id, name, source, created_at) "
                "VALUES (1, 'Ansku', 'manual', 0)")
    con.execute("UPDATE faces SET person_id = 1 WHERE asset_id = ? AND idx = 0",
                (aid,))

    # Only the right-hand face is found this time. It must not inherit the name
    # of the left-hand one merely for being the only candidate left.
    carried, lost = faces.replace_for_asset(con, aid, [
        faces.DetectedFace(bbox=(0.30, 0.10, 0.15, 0.15), det_score=0.9,
                           blur=9.0, embedding=_unit(rng))], vec_ok=False)
    assert (carried, lost) == (0, 1)


def test_overlap_is_measured_as_a_share_of_the_union():
    assert corrections.bbox_iou((0, 0, 1, 1), (0, 0, 1, 1)) == 1.0
    assert corrections.bbox_iou((0, 0, 1, 1), (2, 2, 1, 1)) == 0.0
    assert 0.3 < corrections.bbox_iou((0, 0, 1, 1), (0.5, 0, 1, 1)) < 0.4
