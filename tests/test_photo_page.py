"""The photograph screen: what it says, and putting it right (spec §8.2).

This is where the archive is checked against somebody's memory, so the tests are
about two things. Every fact must say where it came from -- a date read off a
folder name should never look like one measured by a camera -- and every one a
person disagrees with must be correctable from the place it is displayed.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from mediaidx import corrections, db, faces, serve
from mediaidx.config import Config


def cfg_for(tmp_path, dbp) -> Config:
    return Config(database=dbp, thumbnails=tmp_path / "thumbs", logs=tmp_path,
                  names_export=None)


@pytest.fixture
def client(tmp_path):
    """One photograph, two faces, a wrong place and a doubtful date."""
    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)

    con.execute("INSERT INTO places (id, kind, name, path, rank_pop) VALUES "
                "(1, 'locality', 'Alicante', 'Spain/Valencia/Alicante', 900)")
    con.execute("INSERT INTO places (id, kind, name, path, rank_pop) VALUES "
                "(2, 'locality', 'Hartola', 'Finland/Paijat-Hame/Hartola', 90)")
    for pid, norm in ((1, "alicante"), (2, "hartola")):
        con.execute("INSERT INTO place_names (place_id, norm, own) "
                    "VALUES (?,?,1)", (pid, norm))

    aid = db.insert_asset(con, "a" * 64, {
        "format": "jpeg", "taken_at": 1_020_000_000, "width": 4272,
        "height": 2848, "camera_make": "Canon", "camera_model": "EOS 400D",
        "taken_at_source": "folder", "taken_at_precision": "month",
        "date_conflict": 1, "conflict_note": "exif 2002-05 vs folder 2002-04"})
    con.execute("UPDATE assets SET place_id = 1, place_source = 'folder' "
                "WHERE id = ?", (aid,))
    con.execute("""INSERT INTO files (path, asset_id, size_bytes, mtime,
                                      first_seen, last_seen)
                   VALUES ('\\\\nas\\Pictures\\2002\\IMG_1.jpg',?,1,1,1,1)""",
                (aid,))

    rng = np.random.default_rng(5)
    for i in range(2):
        v = rng.normal(size=faces.DIM)
        v = (v / np.linalg.norm(v)).astype(np.float32)
        con.execute(
            """INSERT INTO faces (asset_id, idx, bbox, det_score, blur_score,
                                  embedding)
               VALUES (?,?,?,0.9,9.0,?)""",
            (aid, i, json.dumps([0.1 + 0.4 * i, 0.1, 0.2, 0.2]),
             faces.pack(v)))
    con.execute("INSERT INTO persons (id, name, source, created_at) "
                "VALUES (1, 'Ansku', 'manual', 0)")
    con.execute("UPDATE faces SET person_id = 1 WHERE asset_id = ? AND idx = 0",
                (aid,))
    con.commit()
    con.close()
    return TestClient(serve.create_app(cfg_for(tmp_path, dbp))), aid


# --------------------------------------------------------------------------
# Saying what it knows
# --------------------------------------------------------------------------

def test_every_fact_names_its_source(client):
    """A folder-derived date must not read like a measured one."""
    c, aid = client
    body = c.get(f"/photo/{aid}").text
    assert "folder, month" in body
    assert "from the folder name" in body
    assert "exif 2002-05 vs folder 2002-04" in body


def test_it_says_who_is_in_it_and_who_is_not_named(client):
    c, aid = client
    body = c.get(f"/photo/{aid}").text
    assert "Ansku" in body
    assert "not named" in body


def test_the_original_is_offered_but_never_loaded_for_browsing(client):
    """Browsing must not pull a full-size file across the network (§7.5)."""
    c, aid = client
    body = c.get(f"/photo/{aid}").text
    assert f'href="/full/{aid}"' in body           # a deliberate click
    assert f'src="/full/{aid}"' not in body        # never the page's own image
    assert "/thumb/m/" in body


def test_an_unreachable_original_says_so(client):
    """The fixture's path is a NAS that does not exist on this machine."""
    c, aid = client
    r = c.get(f"/full/{aid}")
    assert r.status_code == 503
    assert "not answering" in r.text


# --------------------------------------------------------------------------
# Putting it right
# --------------------------------------------------------------------------

def test_a_date_can_be_corrected_where_it_is_shown(client):
    c, aid = client
    c.post(f"/photo/{aid}/date", data={"value": "1998-07", "back": "/"})
    body = c.get(f"/photo/{aid}").text
    assert "July 1998" in body
    assert "put right by hand" in body
    assert "exif 2002-05" not in body, "the conflict outlived its settlement"


def test_a_date_it_cannot_read_is_refused_with_an_explanation(client):
    c, aid = client
    r = c.post(f"/photo/{aid}/date", data={"value": "last summer", "back": "/"},
               follow_redirects=True)
    assert "not a date I can read" in r.text
    assert "2002" in r.text, "the original date was thrown away anyway"


def test_a_place_can_be_corrected_by_name(client):
    c, aid = client
    c.post(f"/photo/{aid}/place", data={"value": "Hartola", "back": "/"})
    body = c.get(f"/photo/{aid}").text
    assert "Hartola" in body
    assert "put right by hand" in body


def test_a_place_nobody_has_heard_of_is_refused(client):
    c, aid = client
    r = c.post(f"/photo/{aid}/place", data={"value": "Narnia", "back": "/"},
               follow_redirects=True)
    assert "No place called" in r.text


def test_a_photograph_can_be_said_to_be_nowhere(client):
    c, aid = client
    c.post(f"/photo/{aid}/place", data={"value": "", "back": "/"})
    body = c.get(f"/photo/{aid}").text
    assert "nowhere" in body


def test_a_face_can_be_given_the_right_name(client):
    c, aid = client
    c.post(f"/photo/{aid}/person",
           data={"idx": "1", "value": "Tommi", "back": "/"})
    body = c.get(f"/photo/{aid}").text
    assert "Tommi" in body
    assert "by hand" in body


def test_correcting_a_face_records_its_box_not_just_its_index(client, tmp_path):
    """`idx` is the detector's output order and means nothing between runs."""
    c, aid = client
    c.post(f"/photo/{aid}/person",
           data={"idx": "1", "value": "Tommi", "back": "/"})
    con = db.connect(tmp_path / "photos.db")
    row = corrections.for_asset(con, "a" * 64)[0]
    assert json.loads(row["face_bbox"])[0] == pytest.approx(0.5)


def test_a_correction_can_be_withdrawn(client):
    c, aid = client
    c.post(f"/photo/{aid}/date", data={"value": "1998-07", "back": "/"})
    r = c.post(f"/photo/{aid}/uncorrect",
               data={"kind": "date", "idx": "-1", "back": "/"},
               follow_redirects=True)
    assert "Withdrawn" in r.text
    assert "comes back the next time" in r.text


def test_the_way_back_is_carried_through_a_correction(client):
    c, aid = client
    r = c.post(f"/photo/{aid}/date",
               data={"value": "1998-07", "back": "/?q=Ansku"},
               follow_redirects=False)
    assert "back=%2F%3Fq%3DAnsku" in r.headers["location"]


def test_the_way_back_through_a_correction_stays_local(client):
    c, aid = client
    c.post(f"/photo/{aid}/date",
           data={"value": "1998-07", "back": "https://example.com/"})
    body = c.get(f"/photo/{aid}", params={"back": "https://example.com/"}).text
    assert "example.com" not in body


# --------------------------------------------------------------------------
# The map link
# --------------------------------------------------------------------------

def _with_gps(tmp_path, h="b"):
    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)
    aid = db.insert_asset(con, h * 64, {"format": "jpeg",
                                        "taken_at": 1_600_000_000})
    con.execute("UPDATE assets SET gps_lat = 61.4978, gps_lon = 23.7610 "
                "WHERE id = ?", (aid,))
    con.commit()
    con.close()
    return TestClient(serve.create_app(cfg_for(tmp_path, dbp))), aid


def test_a_coordinate_offers_a_map_and_says_what_that_costs(tmp_path):
    c, aid = _with_gps(tmp_path)
    body = c.get(f"/photo/{aid}").text
    assert "see it on a map" in body
    assert "61.49780" in body
    # The cost is stated where the link is, not buried in a document.
    assert "loads a map from openstreetmap.org" in body
    assert "sending it this coordinate" in body


def test_nothing_remote_is_in_the_page_until_the_map_is_asked_for(tmp_path):
    """The deciding property is *when* anything is fetched (§10.4).

    A photograph opened normally must reach nowhere at all, however many
    coordinates it carries. This is the assertion the whole privacy argument
    rests on, so it checks every way a page can pull something in.
    """
    c, aid = _with_gps(tmp_path)
    body = c.get(f"/photo/{aid}").text
    for tag in ("<iframe", '<img src="http', "<script src=", "href=\"http"):
        assert tag not in body, f"the page reaches out on its own: {tag}"


def test_the_map_appears_in_the_page_when_it_is(tmp_path):
    """Drawn here rather than in another tab, so the photograph is still there."""
    c, aid = _with_gps(tmp_path)
    body = c.get(f"/photo/{aid}", params={"map": 1}).text
    assert "<iframe" in body
    assert "openstreetmap.org/export/embed.html" in body
    assert "marker=61.497800,23.761000" in body
    assert "hide the map" in body


def test_hiding_the_map_is_a_link_back_to_the_photograph(tmp_path):
    c, aid = _with_gps(tmp_path)
    body = c.get(f"/photo/{aid}", params={"map": 1}).text
    assert f'href="/photo/{aid}?back=' in body
    assert "<iframe" not in c.get(f"/photo/{aid}").text


def test_the_frame_is_told_to_send_no_referrer(tmp_path):
    """The map learns the coordinate. It need not also learn where from."""
    c, aid = _with_gps(tmp_path)
    body = c.get(f"/photo/{aid}", params={"map": 1}).text
    assert 'referrerpolicy="no-referrer"' in body


def test_a_photograph_with_no_coordinate_offers_no_map(tmp_path):
    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)
    aid = db.insert_asset(con, "d" * 64, {"format": "jpeg", "taken_at": 1})
    con.commit()
    con.close()
    c = TestClient(serve.create_app(cfg_for(tmp_path, dbp)))
    assert "see it on a map" not in c.get(f"/photo/{aid}").text
    # Nor does asking for one conjure a map of nowhere.
    assert "<iframe" not in c.get(f"/photo/{aid}", params={"map": 1}).text


def test_the_map_service_is_configurable(tmp_path):
    """P7 in spirit: no particular service is baked in."""
    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)
    aid = db.insert_asset(con, "e" * 64, {"format": "jpeg", "taken_at": 1})
    con.execute("UPDATE assets SET gps_lat = 61.0, gps_lon = 23.0 WHERE id = ?",
                (aid,))
    con.commit()
    con.close()
    cfg = cfg_for(tmp_path, dbp)
    cfg.ui["map_embed_url"] = "https://kartta.example/{lat},{lon}"
    cfg.ui["map_url"] = "https://kartta.example/at/{lat},{lon}"
    c = TestClient(serve.create_app(cfg))
    assert "kartta.example" in c.get(f"/photo/{aid}").text
    body = c.get(f"/photo/{aid}", params={"map": 1}).text
    assert "kartta.example/61.000000,23.000000" in body
    assert "openstreetmap.org" not in body


def test_showing_the_map_lands_on_the_map_not_the_top_of_the_page(tmp_path):
    """Following a link loads a fresh page, which otherwise starts at the top.

    The reader was at the bottom looking at the coordinate; sending them back to
    the top to scroll down again is the browser's default, not an intention.
    """
    c, aid = _with_gps(tmp_path)
    body = c.get(f"/photo/{aid}").text
    assert "#map" in body

    shown = c.get(f"/photo/{aid}", params={"map": 1}).text
    assert 'id="map"' in shown
    # And hiding it lands back on the row the link was on, not the top either.
    assert "#where" in shown
    assert 'id=where' in shown or 'id="where"' in shown


def test_the_anchors_clear_the_sticky_header(tmp_path):
    """A sticky header covers whatever an anchor scrolls to, without this."""
    c, aid = _with_gps(tmp_path)
    body = c.get(f"/photo/{aid}", params={"map": 1}).text
    assert "scroll-margin-top" in body


# --------------------------------------------------------------------------
# The page itself
# --------------------------------------------------------------------------

def test_what_matters_is_beside_the_picture(client):
    """Date, people, place and camera without opening anything."""
    c, aid = client
    body = c.get(f"/photo/{aid}").text
    side = body[body.index('<aside class="side">'):body.index('<details class="edit"')]
    assert "<h1>April 2002</h1>" in side
    assert 'class="face-chip"' in side and "Ansku" in side
    assert "Alicante, Spain" in side
    assert "Canon EOS 400D" in side
    assert "put it right" not in side, "the editing belongs under the details"


def test_the_details_are_closed_until_asked_for(client):
    c, aid = client
    assert '<details class="edit">' in c.get(f"/photo/{aid}").text


def test_a_correction_comes_back_with_the_details_open(client):
    """Several things are often put right in a row."""
    c, aid = client
    r = c.post(f"/photo/{aid}/date", data={"value": "1998-07", "back": "/"},
               follow_redirects=False)
    assert "edit=1" in r.headers["location"]
    assert '<details class="edit" open>' in c.get(r.headers["location"]).text


def test_a_date_conflict_is_flagged_under_the_date(client):
    c, aid = client
    assert "Its dates disagree" in c.get(f"/photo/{aid}").text
    c.post(f"/photo/{aid}/date", data={"value": "1998-07", "back": "/"})
    assert "Its dates disagree" not in c.get(f"/photo/{aid}").text


def test_there_is_always_a_way_back(client):
    c, aid = client
    body = c.get(f"/photo/{aid}", params={"back": "/person/1"}).text
    assert 'id="go-back" href="/person/1"' in body


def test_the_pin_is_on_the_picture_only_with_a_coordinate(tmp_path, client):
    c, aid = client
    assert 'title="Show it on a map' not in c.get(f"/photo/{aid}").text
    g, gid = _with_gps(tmp_path / "gps")
    body = g.get(f"/photo/{gid}").text
    assert 'title="Show it on a map (loads a map from openstreetmap.org)"' in body


# --------------------------------------------------------------------------
# The slideshow's sharper picture
# --------------------------------------------------------------------------

def test_the_sharp_picture_is_read_from_the_original_and_fitted(tmp_path):
    """Scaled to the screen asked for, never past the limit, and not kept."""
    import io
    from PIL import Image
    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)
    src = tmp_path / "orig.jpg"
    Image.new("RGB", (4000, 3000), (200, 30, 30)).save(src, "JPEG")
    aid = db.insert_asset(con, "f" * 64, {"format": "jpeg", "taken_at": 1})
    con.execute("""INSERT INTO files (path, asset_id, size_bytes, mtime,
                                      first_seen, last_seen)
                   VALUES (?,?,1,1,1,1)""", (str(src), aid))
    con.commit()
    con.close()
    c = TestClient(serve.create_app(cfg_for(tmp_path, dbp)))

    r = c.get(f"/view/{aid}", params={"w": 1600})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert max(Image.open(io.BytesIO(r.content)).size) == 1600
    big = c.get(f"/view/{aid}", params={"w": 99999})
    assert max(Image.open(io.BytesIO(big.content)).size) == 2560
    assert not list((tmp_path / "thumbs").rglob("*.*")), "nothing is kept"


def test_the_sharp_picture_says_when_the_nas_is_not_answering(client):
    """The fixture's path is a NAS that does not exist on this machine."""
    c, aid = client
    assert c.get(f"/view/{aid}").status_code == 503
