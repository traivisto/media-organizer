"""The places views.

Two things are worth holding still here. A place page must mean the place *and
everything inside it* -- `Finland` without its towns would be empty on this
library -- and a photograph's page must always say which of the two sources put
it there, because a coordinate and a folder name are not the same kind of claim.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mediaidx import db, places, serve
from mediaidx.config import Config
from test_places import ADMIN1, ADMIN2, CITIES, COUNTRIES, _write


@pytest.fixture
def gaz(tmp_path):
    d = tmp_path / "gazetteer"
    d.mkdir()
    _write(d / "countryInfo.txt", COUNTRIES, comment="#ISO\tISO3\theader")
    _write(d / "admin1CodesASCII.txt", ADMIN1)
    _write(d / "admin2Codes.txt", ADMIN2)
    _write(d / "cities500.txt", CITIES)
    return d


@pytest.fixture
def client(tmp_path, gaz):
    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)
    places.load_gazetteer(con, gaz)

    cfg = Config(database=dbp, thumbnails=tmp_path / "thumbs", logs=tmp_path,
                 names_export=None, gazetteer=gaz,
                 places={"exonym_min_population": 50_000})

    folder = con.execute(
        "INSERT INTO folders (path, parsed_label) VALUES (?,?)",
        ("\\\\nas\\pics\\2019 07 Alicante", "Alicante")).lastrowid
    # One in Helsinki by coordinate, one in Alicante by folder name.
    for i, (lat, lon, fid) in enumerate([(60.16952, 24.93545, None),
                                         (None, None, folder)]):
        h = f"{i:064d}"
        aid = db.insert_asset(con, h, {"format": "jpeg", "taken_at": 1_600_000_000,
                                       "gps_lat": lat, "gps_lon": lon})
        con.execute(
            """INSERT INTO files (path, folder_id, asset_id, size_bytes, mtime,
                                  first_seen, last_seen)
               VALUES (?,?,?,1,1,1,1)""", (f"\\\\nas\\pics\\{h}.jpg", fid, aid))
    con.commit()
    places.run(con, cfg)
    con.commit()
    con.close()
    return TestClient(serve.create_app(cfg))


def test_the_places_index_lists_countries_that_have_photographs(client):
    body = client.get("/places").text
    assert "Finland" in body and "Spain" in body
    assert "Solomon Islands" not in body       # in the gazetteer, never used


def test_a_country_counts_everything_inside_it(client):
    """The photograph is in Helsinki, three levels down. Finland still has it."""
    fid = _place_id(client, "Finland")
    body = client.get(f"/place/{fid}").text
    assert "1 photographs in Finland and everything within it" in body


def _place_id(client, name):
    import re
    body = client.get("/places").text
    m = re.search(r'/place/(\d+)"><b>' + name, body)
    assert m, f"{name} not listed"
    return int(m.group(1))


def test_a_place_page_offers_the_places_within_it(client):
    fid = _place_id(client, "Finland")
    body = client.get(f"/place/{fid}").text
    assert "Within" in body and "Uusimaa" in body


def test_a_place_page_carries_a_trail_back_up(client):
    fid = _place_id(client, "Finland")
    within = client.get(f"/place/{fid}").text
    import re
    child = int(re.search(r'/place/(\d+)"><b>Uusimaa', within).group(1))
    body = client.get(f"/place/{child}").text
    assert 'class="crumb"' in body and "Finland" in body


def test_searching_a_place_name_shows_the_place(client):
    body = client.get("/", params={"q": "Helsinki"}).text
    assert "Places" in body and "Helsinki" in body


def test_searching_a_place_nobody_photographed_shows_nothing(client):
    body = client.get("/", params={"q": "Nina"}).text
    assert 'class="people"' not in body


def test_a_photograph_says_the_coordinate_placed_it(client):
    body = client.get("/photo/1").text
    assert "from the coordinate" in body and "Helsinki" in body


def test_a_photograph_says_the_folder_placed_it(client):
    body = client.get("/photo/2").text
    assert "from the folder name" in body and "Alicante" in body


def test_folder_matches_are_offered_for_rejection(client):
    body = client.get("/places").text
    assert "Not this place" in body and "Alicante" in body


def test_rejecting_a_match_unplaces_its_photographs(client):
    pid = _place_id_from_review(client)
    r = client.post("/place/reject",
                    data={"label": "Alicante", "place_id": pid},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "from the folder name" not in client.get("/photo/2").text
    # and the coordinate-placed one is untouched
    assert "from the coordinate" in client.get("/photo/1").text


def test_a_rejection_survives_the_next_run(client):
    pid = _place_id_from_review(client)
    client.post("/place/reject", data={"label": "Alicante", "place_id": pid},
                follow_redirects=False)
    body = client.get("/places").text
    assert "Alicante" not in body


def _place_id_from_review(client):
    import re
    body = client.get("/places").text
    m = re.search(r'name="place_id" value="(\d+)"', body)
    assert m
    return int(m.group(1))
