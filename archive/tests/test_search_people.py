"""Searching for a person by name.

The vector search answers "what is in the photograph". A person's name is not
in the photograph -- SigLIP was never told what anyone is called -- so a typed
name has to be matched against the names people have given, separately from the
model. These tests cover that path, and particularly the Finnish casing that
SQLite's own case-insensitive comparison gets wrong.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest
from fastapi.testclient import TestClient

from mediaidx import cluster, db, faces, serve
from mediaidx.config import Config


@pytest.fixture
def app_with(tmp_path):
    """A served index holding one named person."""
    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)

    rng = np.random.default_rng(0)
    anchor = rng.normal(size=faces.DIM)
    anchor /= np.linalg.norm(anchor)
    for i in range(6):
        v = anchor + rng.normal(scale=0.02, size=faces.DIM)
        v = (v / np.linalg.norm(v)).astype(np.float32)
        h = f"{i:064d}"
        aid = db.insert_asset(con, h, {"format": "jpeg", "taken_at": 1_600_000_000})
        con.execute(
            """INSERT INTO files (path, asset_id, size_bytes, mtime,
                                  first_seen, last_seen)
               VALUES (?,?,1,1,1,1)""", (f"\\\\nas\\{h}.jpg", aid))
        faces.replace_for_asset(
            con, aid,
            [faces.DetectedFace(bbox=(0.1, 0.1, 0.2, 0.2), det_score=0.9,
                                blur=10.0, embedding=v)], vec_ok=False)

    cur = con.execute(
        """INSERT INTO face_clusters (size, centroid, status, updated_at)
           VALUES (6, ?, 'unnamed', 0)""",
        (faces.pack(anchor.astype(np.float32)),))
    cid = cur.lastrowid
    con.execute("UPDATE faces SET cluster_id = ?", (cid,))
    cluster.set_person(con, cid, "Ääkkösen Öykkäri")
    con.commit()
    con.close()

    cfg = Config(database=dbp, thumbnails=tmp_path / "thumbs", logs=tmp_path,
                 names_export=None)
    return TestClient(serve.create_app(cfg))


def _matched(client, q) -> bool:
    """Whether the page found the person at all.

    A partial name arrives as a suggestion strip and a whole one as a resolved
    chip (spec 7.1); both are ways of having found her, and both put a link to
    her page on the screen, so that is what is asserted rather than the shape
    the answer happened to take.
    """
    return "/person/" in client.get("/", params={"q": q}).text


@pytest.mark.parametrize("q", [
    "Ääkkösen", "ääkkösen", "ÄÄKKÖSEN",       # the casing SQLite NOCASE misses
    "öykkäri", "ÖYKKÄRI",
    "Ääkkösen Öykkäri",
    "kkös",                                     # a substring, mid-word
])
def test_a_name_is_found_whatever_its_casing(app_with, q):
    """SQLite's NOCASE folds ASCII A-Z only; this library is Finnish."""
    assert _matched(app_with, q), f"{q!r} did not find the person"


@pytest.mark.parametrize("q", ["Anski", "", "   ", "zzz"])
def test_a_name_nobody_has_matches_nobody(app_with, q):
    assert not _matched(app_with, q)


def test_the_person_strip_reports_the_photograph_count(app_with):
    body = app_with.get("/", params={"q": "Ääkkösen"}).text
    assert "6 photographs" in body


def test_the_strip_links_to_the_person_page(app_with):
    body = app_with.get("/", params={"q": "Ääkkösen"}).text
    assert 'href="/person/1"' in body
    page = app_with.get("/person/1")
    assert page.status_code == 200
    assert "Ääkkösen Öykkäri" in page.text


def test_an_unnamed_cluster_is_not_searchable_by_anything(tmp_path):
    """Nothing is searchable by person until a person has been named."""
    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)
    con.commit()
    con.close()
    cfg = Config(database=dbp, thumbnails=tmp_path, logs=tmp_path, names_export=None)
    client = TestClient(serve.create_app(cfg))
    assert not _matched(client, "anyone")
