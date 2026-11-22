"""The events and review screens.

The review screen is the one that decides whether the application is pleasant
to use (spec 8.2), so what is asserted here is mostly about it: that a question
carries the evidence for its own answer, that answering advances to the next
one, and that every action -- name, split, skip, never ask again -- actually
does what its button says.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from mediaidx import db, events, queue, serve
from mediaidx.config import Config

DAY = 86_400
T0 = 1_500_000_000


def cfg_for(tmp_path, dbp, **over) -> Config:
    base = {"time_gap_hours": 4, "place_gap_km": 25, "min_event_assets": 5,
            "queue_min_assets": 20, "queue_min_faces": 10}
    base.update(over)
    return Config(database=dbp, thumbnails=tmp_path / "thumbs", logs=tmp_path,
                  names_export=None, events=base)


@pytest.fixture
def built(tmp_path):
    """One labelled folder and one bare month, both big enough to matter."""
    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)
    cfg = cfg_for(tmp_path, dbp)

    con.execute("INSERT INTO places (id, parent_id, kind, name, path) "
                "VALUES (1, NULL, 'country', 'Spain', 'Spain')")
    named = con.execute(
        "INSERT INTO folders (path, parsed_label) VALUES (?,?)",
        ("X:/2019/2019 07 Alicante", "Alicante")).lastrowid
    bare = con.execute(
        "INSERT INTO folders (path) VALUES (?)", ("X:/2019/2019 09",)).lastrowid

    n = 0
    for fid, count, start, place in ((named, 25, T0, 1), (bare, 30, T0 + 90 * DAY, 1)):
        for i in range(count):
            n += 1
            h = f"{n:064d}"
            aid = db.insert_asset(con, h, {"taken_at": start + i * 60,
                                           "place_id": place,
                                           "place_source": "gps"})
            con.execute(
                "INSERT INTO files (path, folder_id, asset_id, size_bytes, mtime,"
                " first_seen, last_seen) VALUES (?,?,?,1,1,1,1)",
                (f"X:/{n}.jpg", fid, aid))
    con.commit()
    events.run(con, cfg)
    queue.build(con, cfg)
    con.commit()
    con.close()
    return TestClient(serve.create_app(cfg)), cfg, dbp


@pytest.fixture
def client(built):
    return built[0]


def open_ids(client):
    body = client.get("/events").text
    return [int(m) for m in re.findall(r'/event/(\d+)"', body)]


# --------------------------------------------------------------------------
# the events list
# --------------------------------------------------------------------------

def test_the_events_list_shows_both_kinds_and_counts_them(client):
    body = client.get("/events").text
    assert "2 events" in body and "1 named" in body
    assert "Alicante" in body and "unnamed" in body


def test_the_unnamed_filter_hides_the_named_ones(client):
    body = client.get("/events", params={"unnamed": 1}).text
    assert "Alicante" not in body and "unnamed" in body


def test_an_event_page_says_where_its_name_came_from(client):
    for eid in open_ids(client):
        body = client.get(f"/event/{eid}").text
        assert ("named by its folder" in body
                or "grouped by time and place" in body)


def test_a_photograph_page_links_to_its_event(client):
    body = client.get("/photo/1").text
    assert "event" in body and "Alicante" in body and "from the folder name" in body


# --------------------------------------------------------------------------
# the review queue
# --------------------------------------------------------------------------

def test_review_asks_the_largest_question_first_and_says_how_many_are_left(client):
    body = client.get("/review").text
    assert "1 questions left" in body        # only the unnamed one qualifies
    assert "30 photographs waiting on an answer" in body


def test_a_question_carries_the_evidence_for_its_own_answer(client):
    body = client.get("/review").text
    assert "Suggested:" in body and "Spain" in body
    assert "30 of 30 placed in Spain by coordinate" in body


def test_the_sample_is_a_spread_and_offers_the_whole_event(client):
    body = client.get("/review").text
    assert "12 of 30, spread across it" in body
    assert "see all of them" in body


def test_every_action_the_spec_names_is_on_the_screen(client):
    body = client.get("/review").text
    for action in ("Name it", "Not one event", "Skip", "Don&rsquo;t ask again"):
        assert action in body, action
    assert "Escape" in body                  # Esc skips, without a framework


def test_naming_from_review_advances_to_an_empty_queue(client):
    eid = re.search(r'action="/event/(\d+)/name"', client.get("/review").text)
    r = client.post(f"/event/{eid.group(1)}/name",
                    data={"name": "Hennan rippijuhlat", "back": "/review"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/review"
    body = client.get("/review").text
    assert "Nothing left to ask" in body
    assert "Hennan rippijuhlat" in client.get("/events").text


def test_skipping_puts_it_aside_and_says_it_comes_back(client):
    qid = re.search(r'action="/review/(\d+)/skip"', client.get("/review").text)
    client.post(f"/review/{qid.group(1)}/skip", follow_redirects=False)
    body = client.get("/review").text
    assert "Nothing left to ask" in body and "1 skipped" in body


def test_never_ask_again_leaves_the_event_but_empties_the_queue(client):
    eid = re.search(r'action="/event/(\d+)/dismiss"', client.get("/review").text)
    client.post(f"/event/{eid.group(1)}/dismiss", data={"back": "/review"},
                follow_redirects=False)
    assert "Nothing left to ask" in client.get("/review").text
    assert "2 events" in client.get("/events").text


def test_not_one_event_splits_it_and_the_split_holds(built):
    client, cfg, dbp = built
    eid = int(re.search(r'action="/event/(\d+)/split"',
                        client.get("/review").text).group(1))
    r = client.post(f"/event/{eid}/split", data={"back": "/review"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "3 events" in client.get("/events").text

    con = db.connect(dbp, create=False)
    events.run(con, cfg)                     # a later derive must not undo it
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
    con.close()


def test_searching_an_event_name_finds_the_event(client):
    body = client.get("/", params={"q": "Alicante"}).text
    assert "Events" in body and "/event/" in body


def test_an_empty_queue_says_how_to_build_one(tmp_path):
    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)
    con.close()
    client = TestClient(serve.create_app(cfg_for(tmp_path, dbp)))
    assert "mediaidx derive" in client.get("/review").text
    assert "mediaidx derive" in client.get("/events").text
