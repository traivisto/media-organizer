"""Searching for two people together.

"Tommi and Emmi" is a question about a pair. Either name alone answers a
different question, and the union of the two answers a third; what was asked is
the intersection.
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from mediaidx import cluster, db, faces, serve
from mediaidx.config import Config


def _unit(rng):
    v = rng.normal(size=faces.DIM)
    return (v / np.linalg.norm(v)).astype(np.float32)


@pytest.fixture
def built(tmp_path):
    """Three people; some photographs hold two of them, some only one."""
    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)
    rng = np.random.default_rng(0)
    who = {name: _unit(rng) for name in ("Tommi", "Emmi", "Anski")}

    # asset -> the people in it
    plan = {
        1: ["Tommi", "Emmi"],
        2: ["Tommi", "Emmi"],
        3: ["Tommi", "Emmi", "Anski"],
        4: ["Tommi"],
        5: ["Emmi"],
        6: ["Anski"],
        7: ["Tommi", "Anski"],
    }
    persons = {}
    for name in who:
        persons[name] = cluster.person_id_for(con, name)

    for aid_n, names in plan.items():
        h = f"{aid_n:064d}"
        aid = db.insert_asset(con, h, {"format": "jpeg", "taken_at": 1_600_000_000 + aid_n})
        con.execute(
            """INSERT INTO files (path, asset_id, size_bytes, mtime,
                                  first_seen, last_seen) VALUES (?,?,1,1,1,1)""",
            (f"\\\\nas\\{h}.jpg", aid))
        detected = [
            faces.DetectedFace(bbox=(0.1, 0.1, 0.2, 0.2), det_score=0.9,
                               blur=10.0, embedding=who[n]) for n in names]
        faces.replace_for_asset(con, aid, detected, vec_ok=False)
        for idx, n in enumerate(names):
            con.execute(
                "UPDATE faces SET person_id = ? WHERE asset_id = ? AND idx = ?",
                (persons[n], aid, idx))
    con.commit()
    con.close()

    cfg = Config(database=dbp, thumbnails=tmp_path / "t", logs=tmp_path,
                 names_export=None)
    return TestClient(serve.create_app(cfg)), persons, dbp


def _con(dbp):
    c = db.connect(dbp, create=False)
    import sqlite3
    c.row_factory = sqlite3.Row
    return c


# --- the query itself -----------------------------------------------------

def test_photographs_with_both_people(built):
    _, persons, dbp = built
    rows = cluster.photos_with_all(_con(dbp), [persons["Tommi"], persons["Emmi"]])
    assert len(rows) == 3, "should be the three photographs holding both"


def test_a_third_person_narrows_it_further(built):
    _, persons, dbp = built
    rows = cluster.photos_with_all(
        _con(dbp), [persons["Tommi"], persons["Emmi"], persons["Anski"]])
    assert len(rows) == 1


def test_it_is_an_intersection_not_a_union(built):
    """Photograph 4 has only Tommi and must not appear in a Tommi+Emmi search."""
    _, persons, dbp = built
    rows = cluster.photos_with_all(_con(dbp), [persons["Tommi"], persons["Emmi"]])
    hashes = {r["content_hash"] for r in rows}
    assert f"{4:064d}" not in hashes
    assert f"{5:064d}" not in hashes


def test_one_person_alone_is_every_photograph_they_are_in(built):
    _, persons, dbp = built
    rows = cluster.photos_with_all(_con(dbp), [persons["Tommi"]])
    assert len(rows) == 5


def test_nobody_asked_for_is_nothing(built):
    _, _, dbp = built
    assert cluster.photos_with_all(_con(dbp), []) == []


# --- picking the names out of the phrase ----------------------------------

@pytest.mark.parametrize("q,expected", [
    ("Tommi and Emmi", {"Tommi", "Emmi"}),
    ("tommi emmi", {"Tommi", "Emmi"}),
    ("Emmi ja Tommi", {"Tommi", "Emmi"}),          # Finnish "and"
    ("TOMMI, EMMI", {"Tommi", "Emmi"}),
    ("Tommi", {"Tommi"}),
    ("Tommi and Emmi and Anski", {"Tommi", "Emmi", "Anski"}),
    ("a beach at sunset", set()),
])
def test_names_are_found_inside_the_phrase(built, q, expected):
    _, _, dbp = built
    got = {r["name"] for r in cluster.people_in_query(_con(dbp), q)}
    assert got == expected


def test_a_longer_name_is_not_also_a_shorter_one(tmp_path):
    """Searching Emmiliina must not silently become Emmi and Emmiliina."""
    dbp = tmp_path / "p.db"
    con = db.connect(dbp)
    db.migrate(con)
    rng = np.random.default_rng(1)
    for name in ("Emmi", "Emmiliina"):
        pid = cluster.person_id_for(con, name)
        h = f"{abs(hash(name)):064d}"[:64]
        aid = db.insert_asset(con, h, {"format": "jpeg"})
        con.execute("""INSERT INTO files (path, asset_id, size_bytes, mtime,
                       first_seen, last_seen) VALUES (?,?,1,1,1,1)""",
                    (f"\\\\nas\\{h}.jpg", aid))
        faces.replace_for_asset(con, aid, [faces.DetectedFace(
            bbox=(0, 0, 0.2, 0.2), det_score=0.9, blur=1.0,
            embedding=_unit(rng))], vec_ok=False)
        con.execute("UPDATE faces SET person_id = ? WHERE asset_id = ?", (pid, aid))
    con.commit()

    got = {r["name"] for r in cluster.people_in_query(con, "Emmiliina")}
    assert got == {"Emmiliina"}


# --- through the page -----------------------------------------------------

def test_the_page_reports_the_pair(built):
    client, _, _ = built
    body = client.get("/", params={"q": "Tommi and Emmi"}).text
    assert "photographs with" in body.lower()
    assert "Tommi and Emmi" in body


def test_a_pair_that_never_appears_together_says_so(built):
    client, _, dbp = built
    con = _con(dbp)
    cluster.person_id_for(con, "Henna")          # named, but in no photograph
    con.commit()
    body = client.get("/", params={"q": "Tommi and Henna"}).text
    # Henna is in nothing, so she is not a named person with photographs and
    # the pair query does not fire; the page must not claim a pairing.
    assert "together" not in body or "No photograph" in body


def test_the_count_is_the_count_not_the_page_size(built):
    """A capped page must not report its own length as the total."""
    _, persons, dbp = built
    con = _con(dbp)
    ids = [persons["Tommi"], persons["Emmi"]]
    assert cluster.count_photos_with_all(con, ids) == 3
    # A page smaller than the result set still reports the true total.
    assert len(cluster.photos_with_all(con, ids, limit=1)) == 1
    assert cluster.count_photos_with_all(con, ids) == 3


def test_counting_nobody_is_zero(built):
    _, _, dbp = built
    assert cluster.count_photos_with_all(_con(dbp), []) == 0
