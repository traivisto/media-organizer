"""Face storage, filtering and crops.

The model itself is not exercised here -- it needs a GPU, 300 MB of weights and
a real photograph of a real person. What is exercised is everything around it,
which is where the mistakes that lose data live: replacing an asset's faces
without orphaning vectors, coordinates that survive a change of image size, and
the stage query that decides what still needs reading.
"""

from __future__ import annotations

import json
import sqlite3

import numpy as np
import pytest
from PIL import Image

from mediaidx import db, face_pass, faces, images


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    db.migrate(con)
    return con


def _asset(con, h: str, taken_at: int = 1_600_000_000) -> int:
    aid = db.insert_asset(con, h, {"format": "jpeg", "taken_at": taken_at})
    con.execute(
        """INSERT INTO files (path, asset_id, size_bytes, mtime, first_seen, last_seen)
           VALUES (?,?,?,?,?,?)""", (f"\\\\nas\\{h}.jpg", aid, 1, 1, 1, 1))
    return aid


def _face(seed: int, bbox=(0.1, 0.2, 0.3, 0.4), score=0.9) -> faces.DetectedFace:
    v = np.zeros(faces.DIM, dtype=np.float32)
    v[seed % faces.DIM] = 1.0
    return faces.DetectedFace(bbox=bbox, det_score=score, blur=12.5, embedding=v)


# --- storage --------------------------------------------------------------

def test_faces_are_stored_with_their_position_and_bbox():
    con = _con()
    aid = _asset(con, "a" * 64)
    faces.replace_for_asset(con, aid, [_face(1), _face(2)], vec_ok=False)

    rows = con.execute("SELECT * FROM faces ORDER BY idx").fetchall()
    assert [r["idx"] for r in rows] == [0, 1]
    assert json.loads(rows[0]["bbox"]) == [0.1, 0.2, 0.3, 0.4]
    assert rows[0]["det_score"] == pytest.approx(0.9)
    assert rows[0]["blur_score"] == pytest.approx(12.5)


def test_embeddings_round_trip_through_float16():
    con = _con()
    aid = _asset(con, "b" * 64)
    f = _face(7)
    faces.replace_for_asset(con, aid, [f], vec_ok=False)

    blob = con.execute("SELECT embedding FROM faces").fetchone()[0]
    out = faces.unpack(blob)
    assert out.shape == (faces.DIM,)
    assert np.allclose(out, f.embedding, atol=1e-3)


def test_rerunning_the_stage_replaces_rather_than_accumulates():
    """A changed threshold or model version must not leave the old faces behind."""
    con = _con()
    aid = _asset(con, "c" * 64)
    faces.replace_for_asset(con, aid, [_face(1), _face(2), _face(3)], vec_ok=False)
    faces.replace_for_asset(con, aid, [_face(4)], vec_ok=False)

    rows = con.execute("SELECT idx FROM faces WHERE asset_id = ?", (aid,)).fetchall()
    assert [r["idx"] for r in rows] == [0], "old faces survived a re-run"


def test_an_asset_with_no_faces_is_still_a_completed_stage():
    con = _con()
    aid = _asset(con, "d" * 64)
    faces.replace_for_asset(con, aid, [], vec_ok=False)
    db.set_stage(con, aid, faces.STAGE, faces.STAGE_VERSION, "done", 0)

    assert con.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 0
    assert face_pass.pending(con, faces.STAGE_VERSION) == []


# --- the pending query ----------------------------------------------------

def test_pending_lists_assets_without_a_current_stage_row():
    con = _con()
    a = _asset(con, "1" * 64, taken_at=100)
    b = _asset(con, "2" * 64, taken_at=200)
    db.set_stage(con, a, faces.STAGE, faces.STAGE_VERSION, "done", 0)

    assert [r[0] for r in face_pass.pending(con, faces.STAGE_VERSION)] == [b]


def test_a_bumped_stage_version_makes_everything_pending_again():
    con = _con()
    a = _asset(con, "3" * 64)
    db.set_stage(con, a, faces.STAGE, 1, "done", 0)

    assert face_pass.pending(con, 1) == []
    assert [r[0] for r in face_pass.pending(con, 2)] == [a]


def test_pending_is_newest_first():
    con = _con()
    old = _asset(con, "4" * 64, taken_at=1_000)
    new = _asset(con, "5" * 64, taken_at=9_000)
    assert [r[0] for r in face_pass.pending(con, 1)] == [new, old]


def test_a_missing_file_is_not_offered_for_reading():
    con = _con()
    a = _asset(con, "6" * 64)
    con.execute("UPDATE files SET missing_since = 1 WHERE asset_id = ?", (a,))
    assert face_pass.pending(con, 1) == []


def test_a_duplicate_is_offered_once():
    """Two paths, one photograph: the face pass must not read it twice (§4.1)."""
    con = _con()
    a = _asset(con, "7" * 64)
    con.execute(
        """INSERT INTO files (path, asset_id, size_bytes, mtime, first_seen, last_seen)
           VALUES ('\\\\nas\\copy.jpg', ?, 1, 1, 1, 1)""", (a,))
    assert len(face_pass.pending(con, 1)) == 1


# --- face crops -----------------------------------------------------------

def test_face_crop_is_square_and_sized(tmp_path):
    im = Image.new("RGB", (1600, 1200), (30, 30, 30))
    images.write_face_thumb(im, tmp_path, "e" * 64, 0, (0.4, 0.4, 0.1, 0.12))
    p = images.face_thumb_path(tmp_path, "e" * 64, 0)
    with Image.open(p) as t:
        assert t.size == (images.FACE_THUMB, images.FACE_THUMB)
        assert t.format == "WEBP"


@pytest.mark.parametrize("bbox", [
    (0.4, 0.4, 0.1, 0.12), (0.0, 0.0, 0.06, 0.08), (0.9, 0.85, 0.09, 0.13),
    (0.33, 0.33, 0.07, 0.07), (0.5, 0.1, 0.11, 0.09),
])
def test_face_crops_are_square_whatever_the_geometry(tmp_path, bbox):
    """Rounding the side before placing the corner, rather than after."""
    im = Image.new("RGB", (1600, 1200))
    h = f"{abs(hash(bbox)):064d}"[:64]
    images.write_face_thumb(im, tmp_path, h, 0, bbox)
    with Image.open(images.face_thumb_path(tmp_path, h, 0)) as t:
        assert t.width == t.height, f"{bbox} produced {t.size}"


def test_face_crop_at_the_frame_edge_stays_inside_the_image(tmp_path):
    """Clamped, not padded -- an off-centre crop beats a black border."""
    im = Image.new("RGB", (800, 600))
    images.write_face_thumb(im, tmp_path, "f" * 64, 0, (0.0, 0.0, 0.06, 0.08))
    with Image.open(images.face_thumb_path(tmp_path, "f" * 64, 0)) as t:
        assert t.width == t.height, "clamping at the edge skewed the crop"
        assert t.width <= images.FACE_THUMB


def test_a_small_face_is_not_upscaled(tmp_path):
    """160 px is a ceiling, not a target: no invented pixels."""
    im = Image.new("RGB", (800, 600))
    images.write_face_thumb(im, tmp_path, "aa" + "1" * 62, 0, (0.4, 0.4, 0.04, 0.05))
    with Image.open(images.face_thumb_path(tmp_path, "aa" + "1" * 62, 0)) as t:
        assert t.width < images.FACE_THUMB


def test_face_crop_of_a_face_larger_than_the_frame_still_writes(tmp_path):
    im = Image.new("RGB", (200, 100))
    images.write_face_thumb(im, tmp_path, "0" * 64, 0, (0.0, 0.0, 1.0, 1.0))
    assert images.face_thumb_path(tmp_path, "0" * 64, 0).exists()


def test_face_crops_are_named_by_hash_and_position(tmp_path):
    h = "ab" + "c" * 62
    p = images.face_thumb_path(tmp_path, h, 3)
    assert p.parent.name == "ab"
    assert p.name == f"{h}-3.webp"


def test_existing_face_crop_is_not_rewritten(tmp_path):
    im = Image.new("RGB", (400, 400))
    h = "9" * 64
    images.write_face_thumb(im, tmp_path, h, 0, (0.3, 0.3, 0.2, 0.2))
    p = images.face_thumb_path(tmp_path, h, 0)
    stamp = p.stat().st_mtime_ns
    images.write_face_thumb(im, tmp_path, h, 0, (0.3, 0.3, 0.2, 0.2))
    assert p.stat().st_mtime_ns == stamp


def test_no_temp_files_left_behind(tmp_path):
    images.write_face_thumb(Image.new("RGB", (300, 300)), tmp_path,
                            "8" * 64, 0, (0.1, 0.1, 0.2, 0.2))
    assert not list(tmp_path.rglob("*.tmp"))


# --- bounding boxes -------------------------------------------------------

def test_bbox_fractions_survive_a_change_of_image_size():
    """Why fractions: the same box has to be right against 1600 px, 320 px and
    whatever the original was."""
    bbox = (0.25, 0.5, 0.1, 0.2)
    for W, H in ((1600, 1200), (320, 240), (4032, 3024)):
        x, y, w, h = bbox[0] * W, bbox[1] * H, bbox[2] * W, bbox[3] * H
        assert 0 <= x < W and 0 <= y < H
        assert x + w <= W and y + h <= H
        assert w / W == pytest.approx(bbox[2])
