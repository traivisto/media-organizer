"""A viewing copy: the same screens, and nothing that changes anything (§8.3).

The archive is kept on one computer and looked at from another. The copy that
is looked at must not be able to drift from the one that is kept, so the tests
are about three separate promises: the forms are not drawn, the requests are
turned away, and the database itself is opened so that a write would fail.
"""

from __future__ import annotations

import json
import sqlite3

import numpy as np
import pytest
from fastapi.testclient import TestClient

from mediaidx import db, faces, serve
from mediaidx.config import Config


def _build(tmp_path):
    """One photograph with a named and an unnamed face, each in a cluster, in
    a named event, in a place a folder gave it, with a question waiting."""
    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)
    con.execute("INSERT INTO places (id, kind, name, path, rank_pop) VALUES "
                "(1, 'locality', 'Alicante', 'Spain/Valencia/Alicante', 900)")
    con.execute("INSERT INTO place_names (place_id, norm, own) "
                "VALUES (1, 'alicante', 1)")
    aid = db.insert_asset(con, "a" * 64, {
        "format": "jpeg", "taken_at": 1_020_000_000, "width": 4272,
        "height": 2848, "camera_make": "Canon", "camera_model": "EOS 400D",
        "taken_at_source": "folder", "taken_at_precision": "month"})
    con.execute("UPDATE assets SET place_id = 1, place_source = 'folder', "
                "gps_lat = 38.35, gps_lon = -0.48 WHERE id = ?", (aid,))
    con.execute("""INSERT INTO files (path, asset_id, size_bytes, mtime,
                                      first_seen, last_seen)
                   VALUES ('\\\\nas\\Pictures\\2002\\IMG_1.jpg',?,1,1,1,1)""",
                (aid,))
    con.execute("INSERT INTO persons (id, name, source, created_at) "
                "VALUES (1, 'Ansku', 'manual', 0)")
    rng = np.random.default_rng(5)
    for i in range(2):
        v = rng.normal(size=faces.DIM)
        v = (v / np.linalg.norm(v)).astype(np.float32)
        con.execute(
            "INSERT INTO face_clusters (id, person_id, size, centroid, status, "
            "updated_at) VALUES (?,?,1,?,?,0)",
            (i + 1, 1 if i == 0 else None, faces.pack(v),
             "named" if i == 0 else "unnamed"))
        con.execute(
            """INSERT INTO faces (asset_id, idx, bbox, det_score, blur_score,
                                  embedding, cluster_id, person_id)
               VALUES (?,?,?,0.9,9.0,?,?,?)""",
            (aid, i, json.dumps([0.1 + 0.4 * i, 0.1, 0.2, 0.2]), faces.pack(v),
             i + 1, 1 if i == 0 else None))
    con.execute("""INSERT INTO events (id, name, name_source, start_at, end_at,
                                       asset_count)
                   VALUES (1, 'Alicante', 'folder', 1020000000, 1020000000, 1)""")
    con.execute("INSERT INTO event_assets (event_id, asset_id) VALUES (1, ?)",
                (aid,))
    con.execute("INSERT INTO review_queue (id, kind, ref_id, priority, status) "
                "VALUES (1, 'face_cluster', 2, 1, 'open')")
    con.commit()
    con.close()
    return dbp, aid


def _client(tmp_path, dbp, read_only=True):
    cfg = Config(database=dbp, thumbnails=tmp_path / "thumbs", logs=tmp_path,
                 names_export=None)
    cfg.ui["read_only"] = read_only
    return TestClient(serve.create_app(cfg), client=("127.0.0.1", 50000))


@pytest.fixture
def copy(tmp_path):
    dbp, aid = _build(tmp_path)
    return _client(tmp_path, dbp), aid, dbp


PAGES = ["/", "/people", "/places", "/events", "/odd", "/review",
         "/photo/{aid}", "/photo/{aid}?edit=1", "/cluster/1", "/cluster/2",
         "/person/1", "/event/1", "/place/1"]


@pytest.mark.parametrize("page", PAGES)
def test_no_page_draws_a_form_that_changes_anything(copy, page):
    c, aid, _ = copy
    r = c.get(page.format(aid=aid))
    assert r.status_code == 200
    assert 'method="post"' not in r.text
    assert "Viewing copy" in r.text


@pytest.mark.parametrize("page", PAGES)
def test_the_same_pages_do_draw_them_where_the_archive_is_kept(tmp_path, page):
    """So the test above is about the mode, not about an empty fixture."""
    dbp, aid = _build(tmp_path)
    c = _client(tmp_path, dbp, read_only=False)
    assert "Viewing copy" not in c.get(page.format(aid=aid)).text


def test_the_fixture_has_forms_to_leave_out(tmp_path):
    dbp, aid = _build(tmp_path)
    c = _client(tmp_path, dbp, read_only=False)
    for page in (f"/photo/{aid}", "/cluster/2", "/event/1", "/review"):
        assert 'method="post"' in c.get(page).text, page


@pytest.mark.parametrize("path,data", [
    ("/photo/{aid}/date", {"value": "1998-07"}),
    ("/photo/{aid}/place", {"value": ""}),
    ("/photo/{aid}/person", {"idx": "1", "value": "Tommi"}),
    ("/photo/{aid}/open", {}),
    ("/cluster/2/name", {"name": "Tommi"}),
    ("/cluster/1/merge", {"other": "2"}),
    ("/cluster/2/ignore", {}),
    ("/event/1/name", {"name": "Something else"}),
    ("/event/1/dismiss", {}),
    ("/odd/{aid}/dismiss", {}),
    ("/review/1/skip", {}),
    ("/review/1/person", {"name": "Tommi"}),
    ("/place/reject", {"label": "Alicante", "place_id": "1"}),
])
def test_every_change_is_turned_away(copy, monkeypatch, path, data):
    c, aid, dbp = copy
    opened = []
    monkeypatch.setattr(serve, "open_with_default_app", opened.append)
    r = c.post(path.format(aid=aid), data=data, follow_redirects=False)
    assert r.status_code == 403
    assert "viewing copy" in r.text
    assert opened == []
    con = sqlite3.connect(dbp)
    assert con.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 0
    assert con.execute("SELECT name FROM events").fetchone()[0] == "Alicante"
    assert con.execute("SELECT status FROM review_queue").fetchone()[0] == "open"
    assert con.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 1


def test_the_photograph_still_says_what_it_knows(copy):
    """Details stay: where each fact came from, the paths, the original."""
    c, aid, _ = copy
    body = c.get(f"/photo/{aid}").text
    assert "Ansku" in body and "not named" in body
    assert "from the folder name" in body and "folder, month" in body
    assert "IMG_1.jpg" in body
    assert f'href="/full/{aid}"' in body
    assert "Details</summary>" in body and "Edit &amp; details" not in body
    assert "put it right" not in body and "Who is this?" not in body
    assert "see it on a map" in body               # looking is not changing


def test_review_is_not_offered_and_says_why_if_reached(copy):
    c, aid, dbp = copy
    assert 'href="/review"' not in c.get("/").text
    assert "open question" not in c.get(f"/photo/{aid}").text
    body = c.get("/review").text
    assert "viewing copy" in body
    con = sqlite3.connect(dbp)
    assert con.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] == 1


def test_the_database_is_opened_so_a_write_would_fail(tmp_path, monkeypatch):
    """The promise kept by SQLite rather than by this application: even a
    route that tried could not change the copy."""
    dbp, aid = _build(tmp_path)
    seen = []
    real = sqlite3.connect

    def spy(target, *a, **kw):
        seen.append((str(target), kw))
        return real(target, *a, **kw)

    monkeypatch.setattr(serve.sqlite3, "connect", spy)
    c = _client(tmp_path, dbp)
    assert c.get(f"/photo/{aid}").status_code == 200
    assert seen and all("mode=ro" in target for target, _ in seen)
    # The very way the application opened it, tried from here.
    target, kw = seen[0]
    con = real(target, **kw)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        con.execute("UPDATE events SET name = 'x'")


def test_searching_and_walking_work_as_before(copy):
    c, aid, _ = copy
    body = c.get("/", params={"q": "Ansku 2002"}).text
    assert 'data-kind="person"' in body and 'data-kind="date"' in body
    assert f'href="/photo/{aid}?back=' in body
    d = c.get("/slides", params={"back": "/?q=Ansku", "at": aid}).json()
    assert d["total"] == 1
