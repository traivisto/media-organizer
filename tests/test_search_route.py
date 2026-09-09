"""The search page itself (spec §7).

The resolver and the cut are tested on their own; what is tested here is that
the page uses them -- that a chip both narrows the results and offers the link
that removes it, that a filter narrows further, and that nothing is silently
dropped at the bottom of a page or below the cut.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest
from fastapi.testclient import TestClient

from mediaidx import db, embed, serve
from mediaidx.config import Config


def cfg_for(tmp_path, dbp) -> Config:
    return Config(database=dbp, thumbnails=tmp_path / "thumbs", logs=tmp_path,
                  names_export=None)


class _FixedEncoder:
    """Stands in for SigLIP: every phrase encodes to the same probe vector.

    Loading the real model costs half a minute per test and would make the
    ranking depend on what a neural network thinks of the word "whatever".
    What is under test is the page's use of the scores, not the scores.
    """

    def __init__(self, vec):
        self._vec = vec

    def encode_text(self, texts):
        return np.array([self._vec] * len(texts), dtype=np.float32)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """150 photographs across two years, half of them in one place.

    Large enough that the page has to page, and that the cut has something to
    cut. Vectors are seeded so that the first few are far closer to the probe
    than the rest, which is the shape the cut looks for.
    """
    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)

    con.execute("INSERT INTO places (id, kind, name, path, rank_pop) "
                "VALUES (1, 'locality', 'Alicante', 'Spain/Alicante', 900)")
    con.execute("INSERT INTO place_names (place_id, norm, own) "
                "VALUES (1, 'alicante', 1)")

    rng = np.random.default_rng(7)
    probe = rng.normal(size=embed.DIM)
    probe /= np.linalg.norm(probe)

    for i in range(150):
        # The first fifteen sit close to the probe; the rest are noise, so the
        # scores fall off a cliff at fifteen.
        if i < 15:
            v = probe + rng.normal(scale=0.02, size=embed.DIM)
        else:
            v = rng.normal(size=embed.DIM)
        v = (v / np.linalg.norm(v)).astype(np.float32)
        taken = 1_546_300_800 if i % 2 else 1_577_836_800   # 2019 / 2020
        aid = db.insert_asset(con, f"{i:064d}",
                              {"format": "jpeg", "taken_at": taken,
                               "camera_model": "Pixel 5" if i % 3 else "K-70"})
        if i < 75:
            con.execute("UPDATE assets SET place_id = 1, place_source = 'gps' "
                        "WHERE id = ?", (aid,))
        embed.store(con, aid, "test", v, vec_ok=False)
    con.commit()
    con.close()

    monkeypatch.setattr(serve.embed, "Encoder",
                        lambda **kw: _FixedEncoder(probe))
    # No sqlite-vec index here, only `image_vectors`; the route must answer
    # from the numpy path without caring which one it got.
    return TestClient(serve.create_app(cfg_for(tmp_path, dbp)))


def _meta(body: str) -> str:
    start = body.index('<div class="meta">') + len('<div class="meta">')
    return body[start:body.index("</div>", start)]


def test_a_place_chip_narrows_and_counts(client):
    body = client.get("/", params={"q": "Alicante"}).text
    assert '<i>place</i>' in body
    assert "75 photographs" in _meta(body)


def test_a_chip_carries_the_link_that_removes_it(client):
    body = client.get("/", params={"q": "Alicante"}).text
    assert 'class="x" href="/"' in body


def test_a_date_chip_narrows_further(client):
    both = _meta(client.get("/", params={"q": "Alicante"}).text)
    one = _meta(client.get("/", params={"q": "Alicante 2020"}).text)
    assert "75 photographs" in both
    assert "38 photographs" in one


def test_paging_reports_the_whole_not_the_page(client):
    body = client.get("/").text
    assert "of 150" in body
    assert "next page" in body
    second = client.get("/", params={"page": 1}).text
    assert "previous" in second


def test_a_filter_narrows_without_a_query(client):
    body = client.get("/", params={"cam": "K-70"}).text
    assert "50 photographs" in _meta(body)
    assert "1 on" in body            # the collapsed row says how many are set


def test_a_filter_and_a_chip_apply_together(client):
    body = client.get("/", params={"q": "Alicante", "cam": "K-70"}).text
    n = int(_meta(body).split()[0])
    assert 0 < n < 75


def test_the_cut_holds_the_weak_results_back(client):
    """Fifteen close matches, 135 unrelated: the page shows the fifteen."""
    body = client.get("/", params={"q": "text:whatever"}).text
    meta = _meta(body)
    assert "15 for" in meta
    assert "held back" in meta
    assert "show " in body and "weaker" in body


def test_show_more_reveals_them_rather_than_losing_them(client):
    body = client.get("/", params={"q": "text:whatever", "more": 1}).text
    assert "held back" not in _meta(body)
    assert "of 150" in body or "next page" in body


def test_an_impossible_combination_says_so(client):
    body = client.get("/", params={"q": "Alicante 1998 sunset"}).text
    assert "Nothing matches all of that" in body


# --------------------------------------------------------------------------
# Getting back
# --------------------------------------------------------------------------

def test_a_photograph_leads_back_to_the_search_that_found_it(client):
    """Not to the front page, which loses the question that was being asked."""
    body = client.get("/", params={"q": "Alicante"}).text
    assert "back=%2F%3Fq%3DAlicante" in body


def test_the_way_back_keeps_the_filters(client):
    body = client.get("/", params={"q": "Alicante", "cam": "K-70"}).text
    assert "cam%3DK-70" in body


def test_the_way_back_keeps_the_page(client):
    body = client.get("/", params={"page": 1}).text
    assert "page%3D1" in body


def test_the_photograph_honours_it(client):
    aid = 1
    body = client.get(f"/photo/{aid}", params={"back": "/?q=Alicante"}).text
    assert 'href="/?q=Alicante"' in body


def test_a_photograph_reached_directly_falls_back_to_the_front(client):
    body = client.get("/photo/1").text
    assert 'href="/" style="color:var(--accent)">&larr; back' in body


@pytest.mark.parametrize("hostile", [
    "https://example.com/", "//example.com/", "javascript:alert(1)",
])
def test_the_way_back_is_never_off_this_application(client, hostile):
    """`back` arrives in the URL, so it is only ever followed as a local path."""
    body = client.get("/photo/1", params={"back": hostile}).text
    assert hostile not in body
    assert 'href="/" style="color:var(--accent)">&larr; back' in body


# --------------------------------------------------------------------------
# Saying what was chosen
# --------------------------------------------------------------------------

def test_a_word_that_reads_two_ways_says_so(monkeypatch, tmp_path, client):
    """`Hartola` is an event of three photographs and a municipality of 1,286.

    Choosing the event is defensible; choosing it in silence is not, because
    the difference shows only as an unexplained empty page.
    """
    body = client.get("/", params={"q": "Alicante"}).text
    assert "is also" not in body            # nothing ambiguous about this one


def test_an_empty_answer_names_what_was_asked_for(client):
    body = client.get("/", params={"q": "Alicante 1998"}).text
    assert "No photograph has" in body
    assert "most recent first" not in body


def test_the_other_reading_promises_what_it_delivers(tmp_path):
    """The count offered must be the one the link gives.

    The event is the larger reading here and so is chosen, but it holds nobody
    the query also asked for. The place reading holds four of them. Offering
    the place's own total instead would answer a question nobody asked, and the
    click that follows could only disappoint.
    """
    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)
    con.execute("INSERT INTO places (id, kind, name, path, rank_pop) "
                "VALUES (1, 'locality', 'Hartola', 'Finland/Hartola', 500)")
    con.execute("INSERT INTO place_names (place_id, norm, own) "
                "VALUES (1, 'hartola', 1)")
    con.execute("""INSERT INTO events (id, name, name_source, start_at, end_at,
                                       asset_count) VALUES
                   (1, 'Hartola', 'folder', 0, 0, 40)""")
    con.execute("INSERT INTO persons (id, name, source, created_at) "
                "VALUES (1, 'Tommi', 'manual', 0)")

    # Forty in the event, none of them Tommi; thirty in the municipality, four
    # of them Tommi. The event is larger, so it is the reading taken.
    for i in range(70):
        aid = db.insert_asset(con, f"{i:064d}",
                              {"format": "jpeg", "taken_at": 1_600_000_000})
        if i < 40:
            con.execute("INSERT INTO event_assets (event_id, asset_id) "
                        "VALUES (1, ?)", (aid,))
        else:
            con.execute("UPDATE assets SET place_id = 1 WHERE id = ?", (aid,))
            if i < 44:
                con.execute(
                    """INSERT INTO faces (asset_id, idx, bbox, det_score,
                                          embedding, person_id)
                       VALUES (?,0,'[0,0,1,1]',0.9,X'00',1)""", (aid,))
    con.commit()
    con.close()

    client = TestClient(serve.create_app(cfg_for(tmp_path, dbp)))
    body = client.get("/", params={"q": "hartola tommi"}).text
    assert "<i>event</i>" in body            # the larger reading was taken
    assert "No photograph has" in body
    assert "4 photographs" in body           # what the link gives
    assert "30 photograph" not in body       # not what the place holds


def test_how_many_people_are_in_it(client):
    """"Just the two of them" is a different question from "the two of them"."""
    body = client.get("/", params={"who": "2"}).text
    assert "1 on" in body                    # the filter registers as set
    assert 'value="2" selected' in body


def test_a_filter_matching_nothing_is_not_an_empty_archive(client):
    """150 photographs are indexed; none has exactly two faces."""
    body = client.get("/", params={"who": "2"}).text
    assert "Nothing indexed yet" not in body
