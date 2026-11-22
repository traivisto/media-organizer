"""The review queue: what gets asked, in what order, and what an answer does.

The queue is rebuilt from scratch on every derive, so most of these tests are
about what must survive that and what must not: a name survives, a dismissal
survives, a skip deliberately does not.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from mediaidx import db, events, queue
from mediaidx.config import Config

from test_events import HOUR, DAY, T0, folder, photos


def cfg(**over) -> Config:
    base = {"time_gap_hours": 4, "place_gap_km": 25, "min_event_assets": 5,
            "queue_min_assets": 20, "queue_min_faces": 10}
    base.update(over)
    return Config(database=":memory:", thumbnails="", logs="", names_export=None,
                  events=base)


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.isolation_level = None
    db.migrate(c)
    yield c
    c.close()


def cluster_of(con, size, *, cluster_id=None, person=None, asset_ids=None) -> int:
    centroid = np.zeros(512, dtype=np.float16).tobytes()
    pid = None
    if person:
        cur = con.execute(
            "INSERT INTO persons (name, source, created_at) VALUES (?,'manual',0)",
            (person,))
        pid = cur.lastrowid
    cur = con.execute(
        "INSERT INTO face_clusters (id, person_id, size, centroid, status, "
        "updated_at) VALUES (?,?,?,?,?,0)",
        (cluster_id, pid, size, centroid, "named" if pid else "unnamed"))
    cid = cur.lastrowid
    for i in range(size):
        aid = (asset_ids[i % len(asset_ids)] if asset_ids else
               con.execute("INSERT INTO assets (content_hash) VALUES (?)",
                           (f"c{cid}-{i}",)).lastrowid)
        con.execute(
            "INSERT INTO faces (asset_id, idx, bbox, det_score, embedding, "
            "cluster_id, person_id) VALUES (?,?,'[0,0,1,1]',0.9,?,?,?)",
            (aid, 1000 + cid * 100 + i, centroid, cid, pid))
    return cid


# --------------------------------------------------------------------------
# what reaches the queue
# --------------------------------------------------------------------------

def test_only_events_above_the_floor_are_asked_about(con):
    """Every run of five is an event; not every run of five is a question.

    Measured on the reference library the first rule alone produces 1,209
    unnamed events, which is not a finite queue. Promotion to an event and
    promotion to a question are separate decisions.
    """
    small = folder(con, "X:/2019/2019 07", fid=1)
    big = folder(con, "X:/2019/2019 08", fid=2)
    photos(con, small, 8)
    photos(con, big, 25, start=T0 + 90 * DAY, first=100)
    events.run(con, cfg())
    st = queue.build(con, cfg())
    assert (st.events, st.clusters) == (1, 0)
    assert con.execute(
        "SELECT priority FROM review_queue").fetchone()[0] == 25


def test_a_named_event_is_never_asked_about(con):
    f = folder(con, "X:/2019/2019 07 Alicante", label="Alicante")
    photos(con, f, 40)
    events.run(con, cfg())
    assert queue.build(con, cfg()).events == 0


def test_the_floors_are_configurable_back_to_the_specification(con):
    f = folder(con, "X:/2019/2019 07")
    photos(con, f, 8)
    events.run(con, cfg())
    assert queue.build(con, cfg()).events == 0
    assert queue.build(con, cfg(queue_min_assets=5)).events == 1


def test_unnamed_clusters_above_the_floor_are_asked_about(con):
    cluster_of(con, 30)
    cluster_of(con, 4)                       # below queue_min_faces
    cluster_of(con, 30, person="Ansku")      # already named
    st = queue.build(con, cfg())
    assert (st.events, st.clusters) == (0, 1)


def test_an_ignored_cluster_is_not_asked_about(con):
    cid = cluster_of(con, 30)
    con.execute("UPDATE face_clusters SET status = 'ignored' WHERE id = ?", (cid,))
    assert queue.build(con, cfg()).clusters == 0


def test_the_queue_is_ordered_largest_first(con):
    a = folder(con, "X:/2019/2019 07", fid=1)
    b = folder(con, "X:/2019/2019 09", fid=2)
    photos(con, a, 22)
    photos(con, b, 60, start=T0 + 90 * DAY, first=100)
    events.run(con, cfg())
    cluster_of(con, 40)
    queue.build(con, cfg())
    got = [r["priority"] for r in con.execute(
        "SELECT priority FROM review_queue ORDER BY priority DESC")]
    assert got == sorted(got, reverse=True)
    assert queue.next_item(con)["priority"] == 60


def test_a_dismissed_event_stays_out_of_the_queue_after_a_rebuild(con):
    f = folder(con, "X:/2019/2019 07")
    photos(con, f, 30)
    events.run(con, cfg())
    events.dismiss(con, con.execute("SELECT id FROM events").fetchone()[0])
    events.run(con, cfg())
    assert queue.build(con, cfg()).events == 0


def test_a_skipped_item_comes_back_after_the_next_derive(con):
    """Deliberately: the question is easier once the neighbours have names."""
    f = folder(con, "X:/2019/2019 07")
    photos(con, f, 30)
    events.run(con, cfg())
    queue.build(con, cfg())
    queue.skip(con, queue.next_item(con)["id"])
    assert queue.next_item(con) is None
    assert queue.counts(con)["skipped"] == 1

    events.run(con, cfg())
    queue.build(con, cfg())
    assert queue.next_item(con) is not None


def test_naming_an_event_removes_its_question(con):
    f = folder(con, "X:/2019/2019 07")
    photos(con, f, 30)
    events.run(con, cfg())
    queue.build(con, cfg())
    eid = con.execute("SELECT id FROM events").fetchone()[0]
    events.set_name(con, eid, "Hennan rippijuhlat")
    assert queue.next_item(con) is None
    assert queue.counts(con)["open"] == 0


# --------------------------------------------------------------------------
# suggestions
# --------------------------------------------------------------------------

def place_tree(con):
    con.executemany(
        "INSERT INTO places (id, parent_id, kind, name, path) VALUES (?,?,?,?,?)",
        [(1, None, "country", "Spain", "Spain"),
         (2, 1, "admin1", "Valencia", "Spain/Valencia"),
         (3, 2, "locality", "Alicante", "Spain/Valencia/Alicante")])


def test_a_place_alone_produces_a_suggestion_with_its_evidence(con):
    place_tree(con)
    f = folder(con, "X:/2019/2019 07")
    ids = photos(con, f, 30)
    con.executemany("UPDATE assets SET place_id = 3, place_source = 'gps' "
                    "WHERE id = ?", [(i,) for i in ids])
    events.run(con, cfg())
    queue.build(con, cfg())
    row = queue.next_item(con)
    assert "Alicante" in row["suggestion"]
    assert "30 of 30 placed in Alicante by coordinate" in row["rationale"]


def test_people_present_throughout_are_offered_as_the_name(con):
    f = folder(con, "X:/2019/2019 07")
    ids = photos(con, f, 30)
    cluster_of(con, 20, person="Ansku", asset_ids=ids[:20])
    events.run(con, cfg())
    queue.build(con, cfg())
    row = queue.next_item(con)
    assert row["suggestion"].startswith("Ansku")
    assert "Ansku in 20 of 30 photographs" in row["rationale"]


def test_someone_barely_present_is_not_offered_as_the_name(con):
    f = folder(con, "X:/2019/2019 07")
    ids = photos(con, f, 30)
    cluster_of(con, 3, person="Ansku", asset_ids=ids[:3])
    events.run(con, cfg())
    queue.build(con, cfg())
    assert queue.next_item(con)["suggestion"] is None


def test_a_place_only_a_fifth_of_the_event_has_is_not_offered_as_its_name(con):
    """The event's place is decided among the photographs that have one, which
    on this archive can be a fifth of them. Naming the whole afternoon after
    those is a guess wearing a fact's clothes."""
    place_tree(con)
    f = folder(con, "X:/2019/2019 07")
    ids = photos(con, f, 30)
    con.executemany("UPDATE assets SET place_id = 3, place_source = 'gps' "
                    "WHERE id = ?", [(i,) for i in ids[:6]])
    events.run(con, cfg())
    assert con.execute("SELECT place_id FROM events").fetchone()[0] == 3
    queue.build(con, cfg())
    assert queue.next_item(con)["suggestion"] is None


def test_nothing_known_means_no_suggestion_rather_than_a_date(con):
    """A date is not a name, and offering one would only have to be deleted."""
    f = folder(con, "X:/2019/2019 07")
    photos(con, f, 30)
    events.run(con, cfg())
    queue.build(con, cfg())
    row = queue.next_item(con)
    assert row["suggestion"] is None and row["rationale"] is None


def test_a_folder_placed_event_says_so_rather_than_claiming_a_coordinate(con):
    place_tree(con)
    f = folder(con, "X:/2001/2001 01")
    ids = photos(con, f, 30)
    con.executemany("UPDATE assets SET place_id = 3, place_source = 'folder' "
                    "WHERE id = ?", [(i,) for i in ids])
    events.run(con, cfg())
    queue.build(con, cfg())
    assert "from the folder name" in queue.next_item(con)["rationale"]


def test_counts_report_what_answering_the_queue_would_label(con):
    a = folder(con, "X:/2019/2019 07", fid=1)
    b = folder(con, "X:/2019/2019 09", fid=2)
    photos(con, a, 22)
    photos(con, b, 30, start=T0 + 90 * DAY, first=100)
    events.run(con, cfg())
    queue.build(con, cfg())
    c = queue.counts(con)
    assert (c["open"], c["events"], c["clusters"]) == (2, 2, 0)
    assert c["photographs"] == 52
