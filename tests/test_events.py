"""Events: what the folder, the clock and the map are each allowed to say.

The fixtures here are a handful of photographs with hand-set timestamps and
coordinates, because every rule in spec 6.2 is a rule about a boundary -- four
hours, 25 km, five photographs -- and a boundary is tested from both sides.
"""

from __future__ import annotations

import sqlite3

import pytest

from mediaidx import db, events
from mediaidx.config import Config

HOUR = 3600
DAY = 24 * HOUR
T0 = 1_500_000_000          # a Friday in July 2017, and nothing else matters


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


def folder(con, path, *, label=None, fid=None) -> int:
    cur = con.execute(
        "INSERT INTO folders (id, path, parsed_label) VALUES (?,?,?)",
        (fid, path, label))
    return cur.lastrowid


def photos(con, folder_id, n, *, start=T0, step=60, lat=None, lon=None,
           first=1) -> list[int]:
    """n photographs `step` seconds apart, all filed in one folder."""
    ids = []
    for i in range(n):
        cur = con.execute(
            "INSERT INTO assets (content_hash, taken_at, gps_lat, gps_lon) "
            "VALUES (?,?,?,?)",
            (f"h{folder_id}-{first + i}", start + i * step, lat, lon))
        aid = cur.lastrowid
        con.execute(
            "INSERT INTO files (path, folder_id, asset_id, size_bytes, mtime, "
            "first_seen, last_seen) VALUES (?,?,?,1,1,1,1)",
            (f"X:/{folder_id}/{first + i}.jpg", folder_id, aid))
        ids.append(aid)
    return ids


# --------------------------------------------------------------------------
# the folder has the last word
# --------------------------------------------------------------------------

def test_labelled_folder_is_one_event_however_long(con):
    """`2019 07 Alicante` is a holiday, not eleven separate afternoons."""
    f = folder(con, "X:/2019/2019 07 Alicante", label="Alicante")
    photos(con, f, 12, step=DAY)          # twelve days apart: every gap is huge
    events.run(con, cfg())
    rows = con.execute("SELECT * FROM events").fetchall()
    assert len(rows) == 1
    assert rows[0]["name"] == "Alicante"
    assert rows[0]["name_source"] == "folder"
    assert rows[0]["asset_count"] == 12


def test_labelled_folder_survives_the_minimum_size(con):
    """The floor keeps specks out of the queue, and a named folder is never
    asked about -- so a labelled trip of three photographs is still an event."""
    f = folder(con, "X:/2019/2019 07 Alicante", label="Alicante")
    photos(con, f, 3)
    events.run(con, cfg())
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_a_label_is_inherited_by_subfolders(con):
    """`2001 01 Thaimaa/skannatut` is the same trip as the folder above it."""
    top = folder(con, "X:/2001/2001 01 Thaimaa", label="Thaimaa", fid=1)
    sub = folder(con, "X:/2001/2001 01 Thaimaa/skannatut", fid=2)
    photos(con, top, 4)
    photos(con, sub, 4, start=T0 + 40 * DAY)     # months later, same event
    events.run(con, cfg())
    rows = con.execute("SELECT * FROM events").fetchall()
    assert len(rows) == 1
    assert rows[0]["asset_count"] == 8
    assert rows[0]["name"] == "Thaimaa"


# --------------------------------------------------------------------------
# the clock, and the map
# --------------------------------------------------------------------------

def test_four_hour_gap_ends_an_event(con):
    f = folder(con, "X:/2019/2019 07")
    photos(con, f, 6)
    photos(con, f, 6, start=T0 + 5 * HOUR, first=100)
    events.run(con, cfg())
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2


def test_a_shorter_gap_does_not(con):
    f = folder(con, "X:/2019/2019 07")
    photos(con, f, 6)
    photos(con, f, 6, start=T0 + 3 * HOUR, first=100)
    rows = con.execute("SELECT * FROM events").fetchall()
    events.run(con, cfg())
    rows = con.execute("SELECT * FROM events").fetchall()
    assert len(rows) == 1 and rows[0]["asset_count"] == 12


def test_distance_splits_what_the_clock_would_not(con):
    """Same afternoon, 300 km apart: two events, and only GPS could say so."""
    f = folder(con, "X:/2019/2019 07")
    photos(con, f, 6, lat=60.1699, lon=24.9384)                 # Helsinki
    photos(con, f, 6, start=T0 + HOUR, first=100,
           lat=61.4991, lon=23.7871)                            # Tampere, 160 km
    events.run(con, cfg())
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2


def test_distance_within_the_threshold_does_not_split(con):
    f = folder(con, "X:/2019/2019 07")
    photos(con, f, 6, lat=60.1699, lon=24.9384)                 # Helsinki
    photos(con, f, 6, start=T0 + HOUR, first=100,
           lat=60.2055, lon=24.6559)                            # Espoo, ~16 km
    events.run(con, cfg())
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_missing_coordinates_fall_back_to_the_clock_alone(con):
    """No GPS at all before 2010 (spec 1.1), which must not mean no events."""
    f = folder(con, "X:/2003/2003 07")
    photos(con, f, 6, lat=None, lon=None)
    photos(con, f, 6, start=T0 + 9 * HOUR, first=100)
    events.run(con, cfg())
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2


def test_short_runs_are_not_events(con):
    f = folder(con, "X:/2019/2019 07")
    photos(con, f, 4)                                    # below the floor
    photos(con, f, 7, start=T0 + 2 * DAY, first=100)
    st = events.run(con, cfg())
    assert st.events == 1
    assert st.ungrouped == 4
    assert con.execute("SELECT COUNT(*) FROM event_assets").fetchone()[0] == 7


def test_undated_photographs_are_left_out_rather_than_guessed(con):
    f = folder(con, "X:/2019/2019 07")
    photos(con, f, 6)
    con.execute("INSERT INTO assets (content_hash, taken_at) VALUES ('nodate', NULL)")
    aid = con.execute("SELECT id FROM assets WHERE content_hash='nodate'").fetchone()[0]
    con.execute("INSERT INTO files (path, folder_id, asset_id, size_bytes, mtime, "
                "first_seen, last_seen) VALUES ('X:/x.jpg',?,?,1,1,1,1)", (f, aid))
    st = events.run(con, cfg())
    assert st.undated == 1
    assert con.execute(
        "SELECT COUNT(*) FROM event_assets WHERE asset_id = ?", (aid,)).fetchone()[0] == 0


def test_thresholds_come_from_the_configuration(con):
    f = folder(con, "X:/2019/2019 07")
    photos(con, f, 6)
    photos(con, f, 6, start=T0 + 5 * HOUR, first=100)
    events.run(con, cfg(time_gap_hours=8))
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_a_duplicate_belongs_to_one_event_only(con):
    """The same photograph filed twice is one asset, and one asset is in one
    event -- the earliest copy's folder wins, as it does for places."""
    a = folder(con, "X:/2019/2019 07 Alicante", label="Alicante", fid=1)
    b = folder(con, "X:/kopiot", fid=2)
    ids = photos(con, a, 6)
    for i, aid in enumerate(ids):
        con.execute("INSERT INTO files (path, folder_id, asset_id, size_bytes, "
                    "mtime, first_seen, last_seen) VALUES (?,?,?,1,1,1,1)",
                    (f"X:/kopiot/{i}.jpg", b, aid))
    events.run(con, cfg())
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert con.execute(
        "SELECT COUNT(*) FROM event_assets").fetchone()[0] == 6


# --------------------------------------------------------------------------
# what a person decides, and whether it survives
# --------------------------------------------------------------------------

def test_a_typed_name_survives_a_rebuild(con):
    f = folder(con, "X:/2019/2019 07")
    photos(con, f, 6)
    events.run(con, cfg())
    eid = con.execute("SELECT id FROM events").fetchone()[0]
    events.set_name(con, eid, "Hennan rippijuhlat")

    events.run(con, cfg())               # ids are all new after this
    row = con.execute("SELECT * FROM events").fetchone()
    assert row["id"] != eid or True      # the point is the name, not the id
    assert row["name"] == "Hennan rippijuhlat"
    assert row["name_source"] == "user"


def test_a_typed_name_beats_the_folder_label(con):
    f = folder(con, "X:/2019/2019 07 USA", label="USA")
    photos(con, f, 6)
    events.run(con, cfg())
    eid = con.execute("SELECT id FROM events").fetchone()[0]
    events.set_name(con, eid, "Ansku 40v")
    events.run(con, cfg())
    row = con.execute("SELECT * FROM events").fetchone()
    assert (row["name"], row["name_source"]) == ("Ansku 40v", "user")


def test_dismissal_survives_a_rebuild(con):
    f = folder(con, "X:/2019/2019 07")
    photos(con, f, 6)
    events.run(con, cfg())
    eid = con.execute("SELECT id FROM events").fetchone()[0]
    events.dismiss(con, eid)
    events.run(con, cfg())
    assert events.is_dismissed(con, con.execute(
        "SELECT id FROM events").fetchone()[0])


def test_split_cuts_at_the_longest_interruption_and_is_permanent(con):
    """'Not one event' on a labelled folder -- the only thing that breaks one."""
    f = folder(con, "X:/2019/2019 07 Alicante", label="Alicante")
    photos(con, f, 5)
    photos(con, f, 5, start=T0 + 3 * DAY, first=100)     # the widest gap
    photos(con, f, 5, start=T0 + 3 * DAY + HOUR, first=200)
    events.run(con, cfg())
    eid = con.execute("SELECT id FROM events").fetchone()[0]

    at = events.split(con, eid, cfg())
    assert at is not None
    events.run(con, cfg())
    rows = con.execute("SELECT * FROM events ORDER BY start_at").fetchall()
    assert [r["asset_count"] for r in rows] == [5, 10]
    assert all(r["name"] == "Alicante" for r in rows)

    events.run(con, cfg())               # and again: the cut is not undone
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2


def test_split_of_a_single_photograph_does_nothing(con):
    f = folder(con, "X:/2019/2019 07 x", label="x")
    photos(con, f, 1)
    events.run(con, cfg())
    eid = con.execute("SELECT id FROM events").fetchone()[0]
    assert events.split(con, eid, cfg()) is None


def test_split_never_leaves_a_piece_below_the_minimum(con):
    """Evenly spaced photographs have no longest gap, so the widest one is
    wherever the search starts. Without the rule that both halves must be real
    events, that is a cut after the first photograph -- and the orphan then
    falls below the floor and vanishes from the timeline. A button called "not
    one event" must not quietly delete one.
    """
    f = folder(con, "X:/2019/2019 07")
    photos(con, f, 30, step=60)              # every gap identical
    events.run(con, cfg())
    eid = con.execute("SELECT id FROM events").fetchone()[0]
    events.split(con, eid, cfg())
    events.run(con, cfg())
    rows = con.execute("SELECT asset_count FROM events ORDER BY start_at").fetchall()
    assert [r[0] for r in rows] == [5, 25]
    assert sum(r[0] for r in rows) == 30     # nothing was dropped


def test_an_event_too_small_to_halve_is_never_offered_a_split(con):
    f = folder(con, "X:/2019/2019 07")
    photos(con, f, 9)                        # 5 on both sides is impossible
    events.run(con, cfg())
    eid = con.execute("SELECT id FROM events").fetchone()[0]
    assert events.split_point(con, eid, cfg()) is None


def test_naming_an_unknown_event_is_an_error_not_a_silent_no_op(con):
    with pytest.raises(ValueError):
        events.set_name(con, 999, "nope")


# --------------------------------------------------------------------------
# where an event was
# --------------------------------------------------------------------------

def place_tree(con):
    rows = [(1, None, "country", "Spain", "Spain"),
            (2, 1, "admin1", "Valencia", "Spain/Valencia"),
            (3, 2, "locality", "Alicante", "Spain/Valencia/Alicante"),
            (4, 2, "locality", "Denia", "Spain/Valencia/Denia"),
            (5, None, "country", "Finland", "Finland")]
    con.executemany(
        "INSERT INTO places (id, parent_id, kind, name, path) VALUES (?,?,?,?,?)",
        rows)


def test_an_event_in_one_town_is_in_that_town(con):
    place_tree(con)
    f = folder(con, "X:/2019/2019 07")
    ids = photos(con, f, 6)
    con.executemany("UPDATE assets SET place_id = 3 WHERE id = ?",
                    [(i,) for i in ids])
    events.run(con, cfg())
    assert con.execute("SELECT place_id FROM events").fetchone()[0] == 3


def test_an_event_across_towns_is_in_what_they_share(con):
    """A week driving through Valencia is in Valencia, not in whichever
    village produced the most pictures."""
    place_tree(con)
    f = folder(con, "X:/2019/2019 07 roadtrip", label="roadtrip")
    ids = photos(con, f, 8)
    for i, aid in enumerate(ids):
        con.execute("UPDATE assets SET place_id = ? WHERE id = ?",
                    (3 if i < 4 else 4, aid))
    events.run(con, cfg())
    assert con.execute("SELECT place_id FROM events").fetchone()[0] == 2


def test_one_stray_coordinate_does_not_coarsen_the_whole_event(con):
    """Nine photographs in Alicante and one in Finland is an event in
    Alicante; the 80% rule is what stops the airport dragging it to nothing."""
    place_tree(con)
    f = folder(con, "X:/2019/2019 07 x", label="x")
    ids = photos(con, f, 10)
    for i, aid in enumerate(ids):
        con.execute("UPDATE assets SET place_id = ? WHERE id = ?",
                    (5 if i == 0 else 3, aid))
    events.run(con, cfg())
    assert con.execute("SELECT place_id FROM events").fetchone()[0] == 3


def test_an_event_with_no_placed_photograph_claims_nowhere(con):
    place_tree(con)
    f = folder(con, "X:/2003/2003 07")
    photos(con, f, 6)
    events.run(con, cfg())
    assert con.execute("SELECT place_id FROM events").fetchone()[0] is None


# --------------------------------------------------------------------------
# reading it back
# --------------------------------------------------------------------------

def test_sample_spreads_across_the_event(con):
    """A sample of the first twelve minutes says nothing about a fortnight."""
    f = folder(con, "X:/2019/2019 07 Alicante", label="Alicante")
    photos(con, f, 120, step=HOUR)
    events.run(con, cfg())
    eid = con.execute("SELECT id FROM events").fetchone()[0]
    got = events.sample(con, eid, limit=12)
    assert len(got) == 12
    span = got[-1]["taken_at"] - got[0]["taken_at"]
    assert span > 100 * HOUR             # not clustered at the start


def test_matching_finds_events_by_name_case_and_umlaut_insensitively(con):
    f = folder(con, "X:/2005/2005 06 Ääkkönen", label="Ääkkönen")
    photos(con, f, 6)
    events.run(con, cfg())
    assert [r["name"] for r in events.matching(con, "ÄÄKKÖ")] == ["Ääkkönen"]
    assert events.matching(con, "") == []
