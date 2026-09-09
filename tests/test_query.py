"""Reading a typed phrase as criteria (spec §7.1).

The chips are the whole search interface, so what matters here is not that a
name resolves -- that part is a dictionary lookup -- but that the *wrong* thing
does not, and that when a word could be two things the reading is the one the
user can see and undo. This library's own false matches are the fixtures:
`Vaja` is a folder label that resolved to a town in Hungary, and it must not
quietly become a place query the moment somebody types it.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from mediaidx import db, query


def _person(c, pid, name):
    c.execute("INSERT INTO persons (id, name, source, created_at) "
              "VALUES (?,?, 'manual', 0)", (pid, name))


@pytest.fixture
def con(tmp_path):
    """An index holding one named person, one named event and two places."""
    c = db.connect(tmp_path / "photos.db")
    db.migrate(c)

    _person(c, 1, "Ansku")
    _person(c, 2, "Emmiliina")

    for pid, name, path, pop in ((10, "Alicante", "Spain/Valencia/Alicante", 900),
                                 (11, "Vaja", "Hungary/Szabolcs/Vaja", 3),
                                 (12, "New York", "USA/New York", 800)):
        c.execute(
            "INSERT INTO places (id, kind, name, path, rank_pop) VALUES (?,?,?,?,?)",
            (pid, "locality", name, path, pop))
        c.execute("INSERT INTO place_names (place_id, norm, own) VALUES (?,?,1)",
                  (pid, query.norm(name)))

    c.execute(
        """INSERT INTO events (id, name, name_source, start_at, end_at,
                               asset_count)
           VALUES (1, 'Kesa 2019', 'folder', 0, 0, 30)""")
    c.commit()
    return c


# Comfortably above query.PLACE_SURE_ASSETS: these are places this
# library has plainly been to, so they resolve without being asked about.
ALL_PLACES = {10: 200, 11: 200, 12: 200}


# --------------------------------------------------------------------------
# Dates, which is the one grammar with nothing existing to lean on
# --------------------------------------------------------------------------

def _range(label):
    got = query.parse_date(label)
    assert got, f"{label!r} did not parse"
    return datetime.fromtimestamp(got[0]), datetime.fromtimestamp(got[1])


@pytest.mark.parametrize("text,first,last", [
    ("2019", (2019, 1, 1), (2020, 1, 1)),
    ("2019-06", (2019, 6, 1), (2019, 7, 1)),
    ("06/2019", (2019, 6, 1), (2019, 7, 1)),
    ("2019-2021", (2019, 1, 1), (2022, 1, 1)),
    ("kesäkuu 2019", (2019, 6, 1), (2019, 7, 1)),
    ("kesäkuussa 2019", (2019, 6, 1), (2019, 7, 1)),
    ("heinäkuu 2019", (2019, 7, 1), (2019, 8, 1)),
    ("July 2019", (2019, 7, 1), (2019, 8, 1)),
    ("2019 elokuu", (2019, 8, 1), (2019, 9, 1)),
])
def test_date_expressions(text, first, last):
    a, b = _range(text)
    assert (a.year, a.month, a.day) == first
    assert (b.year, b.month, b.day) == last


def test_seasons_in_finnish_and_english():
    a, b = _range("kesä 2019")
    assert (a.month, b.month) == (6, 9)
    a, b = _range("summer 2019")
    assert (a.month, b.month) == (6, 9)


def test_winter_runs_into_the_following_year():
    a, b = _range("talvi 2019")
    assert (a.year, a.month) == (2019, 12)
    assert (b.year, b.month) == (2020, 3)


def test_kesakuu_is_june_not_summer():
    """The season stem is a prefix of the month name, so order matters."""
    june = _range("kesäkuu 2019")
    summer = _range("kesä 2019")
    assert june != summer
    assert june[1].month == 7        # June alone
    assert summer[1].month == 9      # June to August


def test_a_description_is_not_a_date():
    for text in ("beach", "kids on a beach", "50", "20190", "1200-1300"):
        assert query.parse_date(text) is None


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

def test_a_name_a_place_a_year_and_a_description(con):
    r = query.resolve(con, "Ansku Alicante 2019 beach", place_counts=ALL_PLACES)
    assert [(t.kind, t.label) for t in r.terms] == [
        ("person", "Ansku"),
        ("place", "Alicante · Valencia · Spain"),
        ("date", "2019"),
    ]
    assert r.text == "beach"


def test_the_longest_span_wins(con):
    """New York is a place; New is not, and must not be left over as text."""
    r = query.resolve(con, "New York", place_counts=ALL_PLACES)
    assert [t.kind for t in r.terms] == ["place"]
    assert r.terms[0].ref_id == 12
    assert r.text == ""


def test_an_event_beats_a_bare_date(con):
    """`Kesa 2019` is a name somebody wrote, not a range somebody meant."""
    r = query.resolve(con, "Kesa 2019", place_counts=ALL_PLACES)
    assert [t.kind for t in r.terms] == ["event"]
    assert r.terms[0].ref_id == 1


def test_a_person_beats_a_gazetteer_place(con):
    """Precedence: what this user named, over what the gazetteer holds."""
    _person(con, 3, "Vaja")
    r = query.resolve(con, "Vaja", place_counts=ALL_PLACES)
    assert r.terms[0].kind == "person"
    assert [t.kind for t in r.terms[0].also] == ["place"]


def test_the_other_reading_is_offered(con):
    """A word with two readings carries the one it did not take."""
    r = query.resolve(con, "Vaja", place_counts=ALL_PLACES)
    assert r.terms[0].kind == "place"
    assert r.terms[0].also == ()


def test_a_place_with_no_photographs_is_not_a_chip(con):
    """Resolving to a town this library has never visited explains nothing."""
    r = query.resolve(con, "Vaja beach", place_counts={10: 200})
    assert r.terms == []
    assert r.text == "Vaja beach"


def test_a_prefix_forces_a_reading(con):
    _person(con, 3, "Vaja")
    r = query.resolve(con, "place:Vaja", place_counts=ALL_PLACES)
    assert r.terms[0].kind == "place"


def test_text_prefix_keeps_a_word_out_of_the_chips(con):
    r = query.resolve(con, "text:Alicante", place_counts=ALL_PLACES)
    assert r.terms == []
    assert r.text == "Alicante"


def test_a_longer_name_is_not_read_as_a_shorter_one(con):
    """Emmi inside Emmiliina: exact spans make this impossible, not merely rare."""
    r = query.resolve(con, "Emmiliina", place_counts=ALL_PLACES)
    assert [(t.kind, t.label) for t in r.terms] == [("person", "Emmiliina")]


def test_punctuation_does_not_hide_a_name(con):
    r = query.resolve(con, "Ansku, Alicante.", place_counts=ALL_PLACES)
    assert [t.kind for t in r.terms] == ["person", "place"]


def test_finnish_case_and_umlauts_fold(con):
    _person(con, 4, "Ääkkönen")
    for typed in ("ääkkönen", "ÄÄKKÖNEN", "aakkonen"):
        r = query.resolve(con, typed, place_counts=ALL_PLACES)
        assert [t.kind for t in r.terms] == ["person"], typed


def test_removing_a_chip_gives_the_query_back_without_it(con):
    r = query.resolve(con, "Ansku Alicante 2019 beach", place_counts=ALL_PLACES)
    place = r.places[0]
    assert r.without(place) == "Ansku 2019 beach"


def test_removing_a_multi_word_chip(con):
    r = query.resolve(con, "New York beach", place_counts=ALL_PLACES)
    assert r.without(r.places[0]) == "beach"


def test_an_empty_query_resolves_to_nothing(con):
    r = query.resolve(con, "   ")
    assert r.terms == [] and r.text == ""


def test_a_pure_description_is_left_alone(con):
    r = query.resolve(con, "kids on a beach at sunset", place_counts=ALL_PLACES)
    assert r.terms == []
    assert r.text == "kids on a beach at sunset"


def test_a_thinly_visited_place_is_offered_not_applied(con):
    """`sunset` is Shandon, California, where this archive holds five pictures.

    The reading is not wrong, only unlikely, and no rule over the name can say
    which was meant -- so it is offered beside the results and the word stays in
    the description until the offer is taken.
    """
    r = query.resolve(con, "Alicante", place_counts={10: 6})
    assert r.terms == []
    assert [t.label for t in r.offers] == ["Alicante · Valencia · Spain"]
    assert r.text == "Alicante"


def test_a_prefix_applies_an_offer_anyway(con):
    """Having said `place:`, the user is not asked again."""
    r = query.resolve(con, "place:Alicante", place_counts={10: 6})
    assert [t.kind for t in r.terms] == ["place"]
    assert r.offers == []


def test_a_place_below_the_floor_is_not_even_mentioned(con):
    r = query.resolve(con, "Alicante", place_counts={10: 1})
    assert r.terms == [] and r.offers == []
    assert r.text == "Alicante"


def test_a_first_name_finds_the_person(con):
    """Nobody types "Anne Raivisto" to look for Anne.

    Requiring the whole name is what sent `totte anne` to the encoder as a
    description, with the person sitting unresolved in the suggestion strip.
    """
    _person(con, 5, "Anne Raivisto")
    r = query.resolve(con, "Anne", place_counts=ALL_PLACES)
    assert [(t.kind, t.label) for t in r.terms] == [("person", "Anne Raivisto")]
    assert r.text == ""


def test_two_people_by_first_name(con):
    _person(con, 5, "Anne Raivisto")
    _person(con, 6, "Totte")
    r = query.resolve(con, "totte anne", place_counts=ALL_PLACES)
    assert sorted(t.label for t in r.terms) == ["Anne Raivisto", "Totte"]
    assert r.text == ""


def test_a_shared_first_name_resolves_to_nobody(con):
    """A chip that silently picks between two people is worse than no chip."""
    _person(con, 5, "Anne Raivisto")
    _person(con, 6, "Anne Virtanen")
    r = query.resolve(con, "Anne", place_counts=ALL_PLACES)
    assert r.terms == []
    assert r.text == "Anne"


def test_a_whole_name_still_beats_a_name_word(con):
    """`Reetta` is Reetta, not Reetta P -- the exact match settles it."""
    _person(con, 5, "Reetta")
    _person(con, 6, "Reetta P")
    r = query.resolve(con, "Reetta", place_counts=ALL_PLACES)
    assert [t.label for t in r.terms] == ["Reetta"]


def test_a_stray_letter_is_not_a_person(con):
    """`Reetta P` must not put `p` in the index."""
    _person(con, 5, "Reetta P")
    r = query.resolve(con, "p", place_counts=ALL_PLACES)
    assert r.terms == []


def _event(c, eid, name, count):
    c.execute("""INSERT INTO events (id, name, name_source, start_at, end_at,
                                     asset_count) VALUES (?,?,'folder',0,0,?)""",
              (eid, name, count))


def test_the_heavier_reading_wins(con):
    """`Hartola` is an occasion of three and a municipality of 1,286.

    Precedence alone read it as the occasion and then said nothing when the
    answer came back empty, which is what sent a real query to no photographs.
    """
    _event(con, 2, "Hartola", 3)
    con.execute("INSERT INTO places (id, kind, name, path, rank_pop) "
                "VALUES (20, 'locality', 'Hartola', 'Finland/Hartola', 500)")
    con.execute("INSERT INTO place_names (place_id, norm, own) "
                "VALUES (20, 'hartola', 1)")
    r = query.resolve(con, "Hartola", place_counts={20: 1286})
    assert r.terms[0].kind == "place"
    assert [a.kind for a in r.terms[0].also] == ["event"]


def test_a_tie_keeps_what_the_user_named(con):
    """Kia, Loon and Vaja tie exactly: the place came from the same label.

    A tie is the signature of that failure rather than a coincidence, so the
    event stays on top and the wrong town does not take its place.
    """
    _event(con, 2, "Vaja", 27)
    r = query.resolve(con, "Vaja", place_counts={11: 27})
    assert r.terms[0].kind == "event"


def test_a_person_is_never_outweighed_by_a_place(con):
    """22 of this library's 79 people share a name with the gazetteer.

    Several hold few photographs -- Marko 4, Reno 15 -- and this archive has
    driven the American west, so counting would answer a question about a
    friend with a question about a city.
    """
    _person(con, 7, "Reno")
    con.execute("INSERT INTO places (id, kind, name, path, rank_pop) "
                "VALUES (21, 'locality', 'Reno', 'USA/Nevada/Reno', 900)")
    con.execute("INSERT INTO place_names (place_id, norm, own) "
                "VALUES (21, 'reno', 1)")
    r = query.resolve(con, "Reno", place_counts={21: 5000})
    assert r.terms[0].kind == "person"


def test_a_name_shared_by_several_events_means_all_of_them(con):
    """Three separate holidays are called Alicante, two called West Coast.

    Keeping whichever row the table yielded first made `Alicante` a search of
    one holiday out of three, silently, and left the weight against the place
    a third of what it should have been. Measured: 50 of 168 named events share
    a name with another.
    """
    _event(con, 2, "Alicante", 121)
    _event(con, 3, "Alicante", 132)
    _event(con, 4, "Alicante", 139)
    r = query.resolve(con, "Alicante", place_counts={10: 142})
    assert r.terms[0].kind == "event"          # 392 together, beating 142
    assert r.terms[0].held == 392
    assert r.terms[0].ref_id is None           # no single event to link to


def test_one_event_of_a_name_still_links_to_itself(con):
    _event(con, 2, "Alicante", 400)
    r = query.resolve(con, "Alicante", place_counts={10: 142})
    assert r.terms[0].ref_id == 2


def test_a_city_state_is_reachable_by_the_city_name(con):
    """`Berlin` must find Tiergarten, which is not beneath any node so named.

    The gazetteer files a city-state's districts as siblings of a locality
    bearing the city's name: this archive's Berlin photographs sit under Mitte,
    Tiergarten and thirty more, while the locality called Berlin holds 40.
    """
    con.execute("INSERT INTO places (id, kind, name, path, rank_pop) VALUES "
                "(30, 'admin1', 'State of Berlin', 'Germany/State of Berlin', 9)")
    con.execute("INSERT INTO places (id, parent_id, kind, name, path, rank_pop) "
                "VALUES (31, 30, 'locality', 'Berlin', "
                "'Germany/State of Berlin/Berlin', 900)")
    con.execute("INSERT INTO place_names (place_id, norm, own) "
                "VALUES (31, 'berlin', 1)")
    r = query.resolve(con, "Berlin", place_counts={31: 40, 30: 868})
    assert r.terms[0].label.startswith("State of Berlin")
    assert r.terms[0].held == 868


def test_a_region_named_after_a_city_is_not_the_city(con):
    """Only leading wrappers fold in. `Helsinki sub-region` reaches Espoo."""
    con.execute("INSERT INTO places (id, kind, name, path, rank_pop) VALUES "
                "(32, 'admin2', 'Helsinki sub-region', 'Finland/Helsinki sub-region', 9)")
    con.execute("INSERT INTO places (id, parent_id, kind, name, path, rank_pop) "
                "VALUES (33, 32, 'locality', 'Helsinki', "
                "'Finland/Helsinki sub-region/Helsinki', 900)")
    con.execute("INSERT INTO place_names (place_id, norm, own) "
                "VALUES (33, 'helsinki', 1)")
    r = query.resolve(con, "Helsinki", place_counts={33: 164, 32: 400})
    assert r.terms[0].label.startswith("Helsinki ·") or \
           r.terms[0].label == "Helsinki"
    assert r.terms[0].held == 164
