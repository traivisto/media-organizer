"""Places: the gazetteer, reverse geocoding and folder-label matching.

The gazetteer here is four hand-written GeoNames rows rather than the real
236,000, so the tests are about the rules -- what a label is allowed to match,
what a distance is allowed to claim, which source wins -- and not about whether
GeoNames knows where Helsinki is.

Column order in the fixtures follows the real dumps exactly, because reading
the wrong column is the failure mode these files invite.
"""

from __future__ import annotations

import sqlite3

import pytest

from mediaidx import db, places
from mediaidx.config import Config

# ISO, ISO3, numeric, fips, Country, Capital, area, population, continent, ...
# ... geonameid is column 16.
COUNTRIES = [
    ("FI", "FIN", "246", "FI", "Finland", "Helsinki", "337030", "5500000",
     "EU", ".fi", "EUR", "Euro", "358", "", "", "fi", "660013", "NO,RU,SE", ""),
    ("ES", "ESP", "724", "SP", "Spain", "Madrid", "504782", "46000000",
     "EU", ".es", "EUR", "Euro", "34", "", "", "es", "2510769", "FR,PT", ""),
    ("SB", "SLB", "090", "BP", "Solomon Islands", "Honiara", "28450", "600000",
     "OC", ".sb", "SBD", "Dollar", "677", "", "", "en", "2103350", "", ""),
]

ADMIN1 = [
    ("FI.13", "Uusimaa", "Uusimaa", "830674"),
    ("ES.60", "Valencia", "Valencia", "2593113"),
    ("SB.03", "Isabel Province", "Isabel Province", "2178735"),
]

ADMIN2 = [
    ("FI.13.091", "Helsinki", "Helsinki", "658225"),
    ("ES.60.03", "Provincia de Alicante", "Provincia de Alicante", "2521976"),
]

# geonameid, name, asciiname, alternatenames, lat, lon, class, code, country,
# cc2, admin1, admin2, admin3, admin4, population, elevation, dem, tz, modified
CITIES = [
    ("658225", "Helsinki", "Helsinki", "Helsingfors,Gorod Khel'sinki",
     "60.16952", "24.93545", "P", "PPLC", "FI", "", "13", "091", "", "",
     "658864", "", "26", "Europe/Helsinki", "2024-01-01"),
    ("634963", "Tampere", "Tampere", "Tammerfors",
     "61.49911", "23.78712", "P", "PPL", "FI", "", "13", "091", "", "",
     "210000", "", "100", "Europe/Helsinki", "2024-01-01"),
    ("2521978", "Alicante", "Alicante", "Alacant,Lontoo",
     "38.34517", "-0.48149", "P", "PPLA2", "ES", "", "60", "03", "", "",
     "348901", "", "3", "Europe/Madrid", "2024-01-01"),
    # Small, obscure, and spelled like a Finnish given name. The trap.
    ("2178736", "Nina", "Nina", "Niina",
     "-8.00000", "159.00000", "P", "PPL", "SB", "", "03", "", "", "",
     "800", "", "10", "Pacific/Guadalcanal", "2024-01-01"),
]


def _write(path, rows, comment=None):
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        if comment:
            fh.write(comment + "\n")
        for r in rows:
            fh.write("\t".join(r) + "\n")


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
def cfg(tmp_path, gaz):
    return Config(database=tmp_path / "x.db", thumbnails=tmp_path, logs=tmp_path,
                  names_export=None, gazetteer=gaz,
                  places={"exonym_min_population": 50_000})


@pytest.fixture
def con(gaz):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.isolation_level = None
    db.migrate(con)
    places.load_gazetteer(con, gaz)
    return con


def _path(con, place_id):
    return con.execute("SELECT path FROM places WHERE id = ?",
                       (place_id,)).fetchone()[0]


def _asset(con, *, lat=None, lon=None, folder_id=None, name="a.jpg"):
    aid = con.execute(
        "INSERT INTO assets (content_hash, gps_lat, gps_lon) VALUES (?,?,?)",
        (name, lat, lon)).lastrowid
    if folder_id is not None:
        con.execute(
            """INSERT INTO files (path, folder_id, asset_id, size_bytes, mtime,
                                  first_seen, last_seen)
               VALUES (?,?,?,1,1,1,1)""",
            (f"\\\\srv\\share\\{name}", folder_id, aid))
    return aid


def _folder(con, path, label=None):
    return con.execute(
        "INSERT INTO folders (path, parsed_label) VALUES (?,?)",
        (path, label)).lastrowid


# --------------------------------------------------------------------------
# names
# --------------------------------------------------------------------------

@pytest.mark.parametrize("written,expected", [
    ("Helsinki", "helsinki"),
    ("HELSINKI", "helsinki"),
    ("Hämeenlinna", "hameenlinna"),
    ("  Bad   Ischl ", "bad ischl"),
    ("Île-de-France", "ile-de-france"),
])
def test_names_normalise_to_one_match_key(written, expected):
    assert places.norm_name(written) == expected


def test_a_postal_code_is_not_a_name():
    assert not places._useful("00100")
    assert not places._useful("x")
    assert places._useful("kia")


def test_a_two_letter_place_keeps_its_own_name():
    """Ii is a municipality in Finland. The rule that throws away stray
    initials from the alternate-name column must not throw that away too."""
    assert places._useful("ii", own=True)
    assert not places._useful("ii")
    assert not places._useful("00100", own=True)


# --------------------------------------------------------------------------
# the tree
# --------------------------------------------------------------------------

def test_the_gazetteer_becomes_a_tree_of_paths(con):
    assert con.execute(
        "SELECT COUNT(*) FROM places WHERE path = 'Finland/Uusimaa/Helsinki/Helsinki'"
    ).fetchone()[0] == 1


def test_a_locality_without_an_admin2_hangs_off_its_region(con):
    row = con.execute(
        "SELECT path FROM places WHERE name = 'Nina'").fetchone()
    assert row["path"] == "Solomon Islands/Isabel Province/Nina"


def test_alternate_names_reach_the_same_place(con):
    hits = places.lookup(con, "Helsingfors")
    assert [h["path"] for h in hits] == ["Finland/Uusimaa/Helsinki/Helsinki"]


def test_a_region_counts_what_is_inside_it(con):
    """GeoNames gives a region no population, so ambiguity would always pick a
    locality. `rank_pop` is what stops Florida losing to Floridablanca."""
    region = con.execute(
        "SELECT rank_pop FROM places WHERE path = 'Finland/Uusimaa'").fetchone()
    assert region["rank_pop"] == 658864 + 210000


def test_a_region_gets_a_representative_point_from_its_largest_place(con):
    row = con.execute(
        "SELECT lat, lon FROM places WHERE path = 'Finland/Uusimaa'").fetchone()
    assert (round(row["lat"], 3), round(row["lon"], 3)) == (60.170, 24.935)


def test_loading_twice_replaces_rather_than_accumulates(con, gaz):
    before = con.execute("SELECT COUNT(*) FROM places").fetchone()[0]
    places.load_gazetteer(con, gaz)
    assert con.execute("SELECT COUNT(*) FROM places").fetchone()[0] == before


def test_the_extract_may_be_a_zip(tmp_path, gaz):
    import zipfile
    with zipfile.ZipFile(gaz / "cities500.zip", "w") as z:
        z.write(gaz / "cities500.txt", "cities500.txt")
    (gaz / "cities500.txt").unlink()
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.isolation_level = None
    db.migrate(con)
    st = places.load_gazetteer(con, gaz)
    assert st.localities == len(CITIES)


# --------------------------------------------------------------------------
# folder labels
# --------------------------------------------------------------------------

def _resolve(con, label, floor=50_000):
    return places.resolve_label(con, label, exonym_floor=floor,
                                rejected=set(), blocked=set())


def test_a_label_must_match_a_whole_name(con):
    assert _resolve(con, "Hennan rippijuhlat")[0] is None
    assert _resolve(con, "Helsinki juhla")[0] is None


def test_a_place_is_found_by_its_own_name_however_small(con):
    place, _ = _resolve(con, "Nina")
    assert _path(con, place) == "Solomon Islands/Isabel Province/Nina"


def test_an_exonym_only_counts_for_a_place_people_talk_about(con):
    """`Lontoo` is planted on Alicante here. Above the floor it resolves; a
    village of 800 spelled like a given name never does."""
    place, _ = _resolve(con, "Lontoo")
    assert _path(con, place) == "Spain/Valencia/Provincia de Alicante/Alicante"
    assert _resolve(con, "Niina")[0] is None          # exonym, 800 people


def test_the_more_populous_of_two_namesakes_wins(con):
    place, candidates = _resolve(con, "Helsinki")
    assert candidates == 2                             # the city and its admin2
    assert _path(con, place) == "Finland/Uusimaa/Helsinki"


def test_a_rejected_match_is_never_offered_again(con):
    place, _ = _resolve(con, "Nina")
    places.reject(con, "Nina", place)
    rejected = {(places.norm_name("Nina"), place)}
    assert places.resolve_label(con, "Nina", exonym_floor=50_000,
                                rejected=rejected, blocked=set())[0] is None


def test_a_label_can_be_rejected_outright(con):
    places.reject(con, "Nina", None)
    assert places.resolve_label(con, "Nina", exonym_floor=50_000,
                                rejected=set(),
                                blocked={places.norm_name("Nina")})[0] is None


def test_a_folder_label_places_its_photographs(con, cfg):
    f = _folder(con, "\\\\srv\\share\\2019 07 Alicante", "Alicante")
    _asset(con, folder_id=f)
    places.match_folders(con, cfg)
    row = con.execute("SELECT place_id, place_source FROM assets").fetchone()
    assert row["place_source"] == "folder"
    assert _path(con, row["place_id"]).endswith("Alicante")


def test_a_subfolder_inherits_the_nearest_labelled_ancestor(con, cfg):
    _folder(con, "\\\\srv\\share\\2001 01 Thaimaa", "Alicante")
    sub = _folder(con, "\\\\srv\\share\\2001 01 Thaimaa\\skannatut")
    _asset(con, folder_id=sub)
    places.match_folders(con, cfg)
    row = con.execute("SELECT place_id, place_source FROM assets").fetchone()
    assert _path(con, row["place_id"]).endswith("Alicante")


def test_an_unlabelled_folder_places_nothing(con, cfg):
    f = _folder(con, "\\\\srv\\share\\2002 01 Vauva", "Vauva")
    _asset(con, folder_id=f)
    places.match_folders(con, cfg)
    assert con.execute(
        "SELECT place_id FROM assets").fetchone()["place_id"] is None


def test_every_folder_match_is_recorded_for_review(con, cfg):
    f = _folder(con, "\\\\srv\\share\\2019 07 Alicante", "Alicante")
    _asset(con, folder_id=f)
    places.match_folders(con, cfg)
    row = con.execute("SELECT * FROM folder_places").fetchone()
    assert row["folder_id"] == f and row["label"] == "Alicante"


# --------------------------------------------------------------------------
# coordinates
# --------------------------------------------------------------------------

def test_a_coordinate_finds_the_town_it_is_in(con):
    _asset(con, lat=60.1700, lon=24.9350)
    places.geocode(con)
    row = con.execute("SELECT place_id, place_source FROM assets").fetchone()
    assert row["place_source"] == "gps"
    assert _path(con, row["place_id"]) == "Finland/Uusimaa/Helsinki/Helsinki"


def test_a_coordinate_far_from_any_town_claims_only_the_region(con):
    # ~60 km north of Helsinki: past the locality tier, inside the region one.
    _asset(con, lat=60.7100, lon=24.9350)
    places.geocode(con)
    row = con.execute("SELECT place_id FROM assets").fetchone()
    assert _path(con, row["place_id"]) == "Finland/Uusimaa/Helsinki"


def test_a_coordinate_in_the_middle_of_nowhere_names_nothing(con):
    _asset(con, lat=0.0, lon=-30.0)          # mid-Atlantic
    places.geocode(con)
    assert con.execute(
        "SELECT place_id FROM assets").fetchone()["place_id"] is None


def test_null_island_is_not_a_place(con):
    _asset(con, lat=0.0, lon=0.0)
    st = places.geocode(con)
    assert st.with_gps == 0


def test_coordinates_within_a_kilometre_are_looked_up_once(con):
    for i, d in enumerate((0.0, 0.0005, 0.0009)):
        _asset(con, lat=60.1700 + d, lon=24.9350, name=f"a{i}.jpg")
    st = places.geocode(con)
    assert st.with_gps == 3 and st.bins == 1


def test_the_second_run_reuses_the_cache(con):
    _asset(con, lat=60.1700, lon=24.9350)
    places.geocode(con)
    st = places.geocode(con)
    assert st.bins == 1 and st.bins_computed == 0


# --------------------------------------------------------------------------
# precedence, and reading it back
# --------------------------------------------------------------------------

def test_a_coordinate_beats_the_folder_it_was_filed_in(con, cfg):
    f = _folder(con, "\\\\srv\\share\\2019 07 Alicante", "Alicante")
    _asset(con, lat=60.1700, lon=24.9350, folder_id=f)
    places.run(con, cfg)
    row = con.execute("SELECT place_id, place_source FROM assets").fetchone()
    assert row["place_source"] == "gps"
    assert _path(con, row["place_id"]).endswith("Helsinki/Helsinki")


def test_a_folder_still_places_what_has_no_coordinate(con, cfg):
    f = _folder(con, "\\\\srv\\share\\2019 07 Alicante", "Alicante")
    _asset(con, lat=60.1700, lon=24.9350, folder_id=f, name="gps.jpg")
    _asset(con, folder_id=f, name="none.jpg")
    st = places.run(con, cfg)
    assert (st.gps_placed, st.folder_placed) == (1, 1)


def test_a_country_means_everything_beneath_it(con):
    finland = con.execute(
        "SELECT id FROM places WHERE path = 'Finland'").fetchone()["id"]
    paths = {_path(con, i) for i in places.subtree_ids(con, finland)}
    assert "Finland/Uusimaa/Helsinki/Helsinki" in paths
    assert "Spain" not in paths


def test_rerunning_the_pass_does_not_double_count(con, cfg):
    f = _folder(con, "\\\\srv\\share\\2019 07 Alicante", "Alicante")
    _asset(con, folder_id=f)
    first = places.run(con, cfg)
    again = places.run(con, cfg)
    assert first.placed == again.placed == 1
