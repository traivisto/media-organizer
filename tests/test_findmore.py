"""Looking for more of one person, a page of faces at a time.

Synthetic embeddings placed at exact cosine distances from a person's direction,
so each test is about what the code does with a ranking and an answer -- not
about whether ArcFace can tell two children apart, which is for the eye.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import numpy as np
import pytest
from fastapi.testclient import TestClient

from mediaidx import cluster, corrections, db, export, faces, findmore, serve
from mediaidx.config import Config
from mediaidx.embed import pack

RNG = np.random.default_rng(11)


def _unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


def _at(direction, d):
    """A unit vector exactly `d` in cosine distance from `direction`."""
    u = RNG.normal(size=faces.DIM)
    u = _unit(u - u.dot(direction) * direction)
    c = 1.0 - d
    return _unit(c * direction + np.sqrt(1 - c * c) * u)


class Lib:
    def __init__(self, con):
        self.con = con
        self.n = 0

    def photo(self, vectors, *, year=2010):
        """One photograph holding these faces; returns their face ids."""
        self.n += 1
        h = f"{self.n:064d}"
        aid = db.insert_asset(self.con, h, {
            "format": "jpeg",
            "taken_at": int(datetime(year, 6, 1).timestamp())})
        self.con.execute(
            """INSERT INTO files (path, asset_id, size_bytes, mtime,
                                  first_seen, last_seen)
               VALUES (?,?,1,1,1,1)""", (f"\\\\nas\\{h}.jpg", aid))
        out = []
        for i, v in enumerate(vectors):
            cur = self.con.execute(
                """INSERT INTO faces (asset_id, idx, bbox, det_score,
                                      blur_score, embedding)
                   VALUES (?,?,?,0.9,9.0,?)""",
                (aid, i, json.dumps([0.1 + 0.3 * i, 0.1, 0.2, 0.2]),
                 faces.pack(v)))
            out.append(cur.lastrowid)
        return out[0] if len(out) == 1 else out

    def person(self, name, direction, sure=6):
        """A named person with a cluster of sure faces around `direction`."""
        pid = cluster.person_id_for(self.con, name)
        cid = self.con.execute(
            "INSERT INTO face_clusters (person_id, size, centroid, status, "
            "updated_at) VALUES (?,?,?,'named',0)",
            (pid, sure, pack(direction))).lastrowid
        for _ in range(sure):
            fid = self.photo([_at(direction, 0.05)])
            self.con.execute("UPDATE faces SET cluster_id = ?, person_id = ? "
                             "WHERE id = ?", (cid, pid, fid))
        return pid


@pytest.fixture
def lib():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.isolation_level = None
    db.migrate(con)
    return Lib(con)


@pytest.fixture
def two(lib):
    """Emmi and Luka, far apart, with nothing yet to ask about."""
    e, l = _unit(RNG.normal(size=faces.DIM)), _unit(RNG.normal(size=faces.DIM))
    return lib, e, l, lib.person("Emmi", e), lib.person("Luka", l)


def _ids(rows):
    return [c.face_id for c in rows]


def test_nearest_first_and_nothing_beyond_reach(two):
    lib, e, _, emmi, _ = two
    far = lib.photo([_at(e, 0.80)])
    mid = lib.photo([_at(e, 0.50)])
    near = lib.photo([_at(e, 0.30)])
    rows, total = findmore.candidates(lib.con, emmi)
    assert _ids(rows) == [near, mid]
    assert total == 2 and far not in _ids(rows)


def test_a_face_under_somebody_else_is_offered_and_says_so(two):
    lib, e, _, emmi, luka = two
    fid = lib.photo([_at(e, 0.40)])
    lib.con.execute("UPDATE faces SET person_id = ? WHERE id = ?", (luka, fid))
    rows, _ = findmore.candidates(lib.con, emmi)
    assert _ids(rows) == [fid] and rows[0].now == "Luka"


def test_not_offered_beside_a_sure_face_of_the_same_person(two):
    lib, e, _, emmi, _ = two
    sure_photo = lib.con.execute(
        "SELECT asset_id FROM faces WHERE person_id = ? LIMIT 1",
        (emmi,)).fetchone()[0]
    lib.con.execute(
        """INSERT INTO faces (asset_id, idx, bbox, det_score, blur_score,
                              embedding) VALUES (?, 5, '[0.7,0.1,0.2,0.2]',
                              0.9, 9.0, ?)""", (sure_photo, faces.pack(_at(e, 0.3))))
    assert findmore.candidates(lib.con, emmi) == ([], 0)


def test_a_birth_year_rules_out_what_came_before(two):
    lib, e, _, emmi, _ = two
    old = lib.photo([_at(e, 0.3)], year=2001)
    new = lib.photo([_at(e, 0.3)], year=2008)
    findmore.set_born(lib.con, emmi, 2005)
    rows, _ = findmore.candidates(lib.con, emmi)
    assert _ids(rows) == [new] and old not in _ids(rows)


def test_a_birth_year_before_1970_works(two):
    lib, e, _, emmi, _ = two
    fid = lib.photo([_at(e, 0.3)], year=1975)
    findmore.set_born(lib.con, emmi, 1944)
    assert _ids(findmore.candidates(lib.con, emmi)[0]) == [fid]


def test_yes_makes_a_sure_face_that_ranks_the_next_page(two):
    lib, e, _, emmi, _ = two
    # Out of reach of the original sure faces, but close to a face that will
    # be confirmed: a yes has to widen what counts as near.
    # Both in one plane with `e`: 0.60 and 0.75 from it, 0.01 from each other.
    bridge = _at(e, 0.60)
    u = _unit(bridge - bridge.dot(e) * e)
    first = lib.photo([bridge])
    beyond = lib.photo([_unit(0.25 * e + np.sqrt(1 - 0.25 ** 2) * u)])
    before = _ids(findmore.candidates(lib.con, emmi)[0])
    assert first in before and beyond not in before

    findmore.decide(lib.con, emmi, {first: "yes"})
    assert corrections.corrected_face_ids(lib.con)[first] == emmi
    after = _ids(findmore.candidates(lib.con, emmi)[0])
    assert first not in after and beyond in after


def test_no_is_not_asked_again_and_assign_does_not_undo_it(two, tmp_path):
    lib, e, _, emmi, _ = two
    fid = lib.photo([_at(e, 0.2)])
    findmore.decide(lib.con, emmi, {fid: "no"})
    assert findmore.candidates(lib.con, emmi) == ([], 0)
    cfg = Config(database=tmp_path / "x.db", thumbnails=tmp_path, logs=tmp_path,
                 names_export=None, faces={})
    cluster.assign(lib.con, cfg)
    assert lib.con.execute("SELECT person_id FROM faces WHERE id = ?",
                           (fid,)).fetchone()[0] is None


def test_no_takes_a_wrong_guess_off(two):
    lib, e, _, emmi, _ = two
    fid = lib.photo([_at(e, 0.3)])
    lib.con.execute("UPDATE faces SET person_id = ? WHERE id = ?", (emmi, fid))
    rows, _ = findmore.candidates(lib.con, emmi, "guesses")
    assert _ids(rows) == [fid]
    findmore.decide(lib.con, emmi, {fid: "no"})
    assert lib.con.execute("SELECT person_id FROM faces WHERE id = ?",
                           (fid,)).fetchone()[0] is None
    assert findmore.candidates(lib.con, emmi, "guesses") == ([], 0)


def test_naming_the_rival_answers_for_both(two):
    lib, e, _, emmi, luka = two
    fid = lib.photo([_at(e, 0.3)])
    findmore.decide(lib.con, emmi, {fid: "is:Luka"})
    assert corrections.corrected_face_ids(lib.con)[fid] == luka
    assert fid not in _ids(findmore.candidates(lib.con, emmi)[0])


def test_guesses_weakest_first(two):
    lib, e, l, emmi, _ = two
    # Both named Emmi; one sits nearly on Luka, which is the one to check first.
    sure = _unit(e * 0.8 + _at(e, 0.3) * 0.2)
    doubtful = _unit(0.45 * e + 0.55 * l)
    a = lib.photo([sure])
    b = lib.photo([doubtful])
    lib.con.execute("UPDATE faces SET person_id = ? WHERE id IN (?,?)",
                    (emmi, a, b))
    rows, _ = findmore.candidates(lib.con, emmi, "guesses")
    assert _ids(rows) == [b, a] and rows[0].rival == "Luka"
    assert rows[0].margin < 0 < rows[1].margin


def test_a_rejection_follows_its_face_through_a_new_detection(two):
    lib, e, _, emmi, _ = two
    a_vec, b_vec = _at(e, 0.3), _at(e, 0.35)
    fa, fb = lib.photo([a_vec, b_vec])
    findmore.decide(lib.con, emmi, {fa: "no"})
    aid = lib.con.execute("SELECT asset_id FROM faces WHERE id = ?",
                          (fa,)).fetchone()[0]
    # The detector now reports the two faces in the opposite order.
    faces.replace_for_asset(lib.con, aid, [
        faces.DetectedFace(bbox=(0.4, 0.1, 0.2, 0.2), det_score=0.9,
                           blur=9.0, embedding=b_vec),
        faces.DetectedFace(bbox=(0.1, 0.1, 0.2, 0.2), det_score=0.9,
                           blur=9.0, embedding=a_vec)], vec_ok=False)
    row = lib.con.execute("SELECT face_idx FROM face_rejections").fetchone()
    assert row[0] == 1
    offered = findmore.candidates(lib.con, emmi)[0]
    assert [c.idx for c in offered if c.asset_id == aid] == [0]


def test_birth_years_and_rejections_survive_an_export(two):
    lib, e, _, emmi, _ = two
    fid = lib.photo([_at(e, 0.3)])
    findmore.set_born(lib.con, emmi, 2004)
    findmore.decide(lib.con, emmi, {fid: "no"})
    data = json.loads(json.dumps(export.dump(lib.con)))

    lib.con.execute("DELETE FROM face_rejections")
    lib.con.execute("UPDATE persons SET birth_year = NULL")
    export.load(lib.con, data)
    assert findmore.born(lib.con, emmi) == 2004
    assert lib.con.execute("SELECT COUNT(*) FROM face_rejections").fetchone()[0] == 1


# --------------------------------------------------------------------------
# the screen
# --------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)
    lib = Lib(con)
    e = _unit(RNG.normal(size=faces.DIM))
    emmi = lib.person("Emmi", e)
    cand = lib.photo([_at(e, 0.3)])
    con.close()
    cfg = Config(database=dbp, thumbnails=tmp_path / "thumbs", logs=tmp_path,
                 names_export=None)
    return cfg, emmi, cand


def test_the_page_asks_and_a_save_answers(client):
    cfg, emmi, cand = client
    c = TestClient(serve.create_app(cfg))
    r = c.get(f"/person/{emmi}/more")
    assert r.status_code == 200 and f'name="v{cand}"' in r.text
    assert "find more of Emmi" in c.get(f"/person/{emmi}").text

    r = c.post(f"/person/{emmi}/more",
               data={"mode": "new", "skip": "0", "shown": "1",
                     f"v{cand}": "yes"}, follow_redirects=False)
    assert r.status_code == 303 and "skip=0" in r.headers["location"]
    assert "Nothing left" in c.get(r.headers["location"]).text

    c.post(f"/person/{emmi}/born", data={"year": "2006"})
    assert 'value="2006"' in c.get(f"/person/{emmi}/more").text


def test_an_unanswered_face_is_stepped_past(client):
    cfg, emmi, cand = client
    c = TestClient(serve.create_app(cfg))
    r = c.post(f"/person/{emmi}/more",
               data={"mode": "new", "skip": "0", "shown": "1"},
               follow_redirects=False)
    assert "skip=1" in r.headers["location"]


def test_a_viewing_copy_does_not_offer_it(client):
    cfg, emmi, _ = client
    cfg.ui["read_only"] = True
    c = TestClient(serve.create_app(cfg))
    assert "viewing copy" in c.get(f"/person/{emmi}/more").text
    assert "find more" not in c.get(f"/person/{emmi}").text
