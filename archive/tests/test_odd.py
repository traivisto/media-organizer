"""The odd one out of a series, and saying it is not (spec §6.5).

The signal is arithmetic and cheap, so what the tests are really about is the
judgement around it: how odd counts as odd when every event has its own scale,
and that saying "this belongs here" is an answer that survives everything --
including `derive`, which throws every event away and builds them again.
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from mediaidx import db, embed, odd, serve
from mediaidx.config import Config


def cfg_for(tmp_path, dbp) -> Config:
    return Config(database=dbp, thumbnails=tmp_path / "thumbs", logs=tmp_path,
                  names_export=None)


def _unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


def _event(con, eid, name, n, *, rng, spread=0.03, strays=0, start=0):
    """One event of `n` photographs of the same thing, plus `strays` of another."""
    anchor = _unit(rng.normal(size=embed.DIM))
    con.execute("""INSERT INTO events (id, name, name_source, start_at, end_at,
                                       asset_count) VALUES (?,?,'folder',?,?,?)""",
                (eid, name, 1_600_000_000, 1_600_000_000, n + strays))
    ids = []
    for i in range(n + strays):
        if i < n:
            v = _unit(anchor + rng.normal(scale=spread, size=embed.DIM))
        else:
            v = _unit(rng.normal(size=embed.DIM))     # nothing like the rest
        aid = db.insert_asset(con, f"{start + i:064d}",
                              {"format": "jpeg", "taken_at": 1_600_000_000 + i})
        embed.store(con, aid, "test", v, vec_ok=False)
        con.execute("INSERT INTO event_assets (event_id, asset_id) VALUES (?,?)",
                    (eid, aid))
        ids.append(aid)
    return ids


@pytest.fixture
def built(tmp_path):
    con = db.connect(tmp_path / "photos.db")
    db.migrate(con)
    rng = np.random.default_rng(4)
    a = _event(con, 1, "Lanzarote", 30, rng=rng, strays=2, start=0)
    b = _event(con, 2, "Monte Rosa", 20, rng=rng, strays=0, start=100)
    con.commit()
    return con, a, b


# --------------------------------------------------------------------------
# Finding them
# --------------------------------------------------------------------------

def test_the_stray_is_found_and_the_rest_are_not(built):
    con, a, _ = built
    got = odd.find(con)
    assert set(got) == {1}, "an event of one subject should hold no outlier"
    assert {aid for aid, *_ in got[1]} == set(a[-2:])


def test_an_event_of_one_subject_flags_nothing(built):
    """Every set has a furthest member; that does not make it odd."""
    con, _, _ = built
    assert 2 not in odd.find(con)


def test_a_short_event_is_left_alone(tmp_path):
    """"The odd one of nine" is just the ninth one."""
    con = db.connect(tmp_path / "photos.db")
    db.migrate(con)
    rng = np.random.default_rng(5)
    _event(con, 1, "A short day", 6, rng=rng, strays=1)
    con.commit()
    assert odd.find(con) == {}


def test_oddness_is_measured_against_the_event_not_a_number(tmp_path):
    """A varied trip and a uniform one sit at different similarities.

    One fixed cutoff would flag everything in the varied event and nothing in
    the uniform one, which is the same reason the search cut is relative (§7.3).
    """
    con = db.connect(tmp_path / "photos.db")
    db.migrate(con)
    rng = np.random.default_rng(6)
    _event(con, 1, "Uniform", 30, rng=rng, spread=0.02, strays=1, start=0)
    _event(con, 2, "Varied", 30, rng=rng, spread=0.30, strays=1, start=100)
    con.commit()
    got = odd.find(con)
    assert 1 in got, "the stray in the uniform event was missed"
    # Both events get looked at on their own terms rather than against a shared
    # similarity, so the uniform event's ordinary members are not all flagged.
    assert len(got.get(1, [])) <= 3


def test_at_most_a_handful_from_any_one_event(tmp_path):
    """An event of nothing but variety is not an event with forty faults."""
    con = db.connect(tmp_path / "photos.db")
    db.migrate(con)
    rng = np.random.default_rng(7)
    _event(con, 1, "Everything", 0, rng=rng, strays=40)
    con.commit()
    assert len(odd.find(con).get(1, [])) <= odd.PER_EVENT


# --------------------------------------------------------------------------
# Saying it belongs
# --------------------------------------------------------------------------

def test_dismissing_takes_it_out_of_the_list(built):
    con, a, _ = built
    first = odd.find(con)[1][0][0]
    odd.dismiss(con, first)
    assert first not in {aid for aid, *_ in odd.find(con).get(1, [])}


def test_dismissing_survives_the_events_being_rebuilt(built):
    """`derive` throws every event away, so this cannot be keyed to one."""
    con, a, _ = built
    first = odd.find(con)[1][0][0]
    odd.dismiss(con, first)
    con.execute("DELETE FROM event_assets")
    con.execute("DELETE FROM events")
    _event(con, 99, "Lanzarote again", 0, rng=np.random.default_rng(4))
    con.execute("INSERT INTO event_assets (event_id, asset_id) "
                "SELECT 99, id FROM assets")
    con.execute("UPDATE events SET asset_count = (SELECT COUNT(*) FROM assets) "
                "WHERE id = 99")
    con.commit()
    assert first not in {aid for aid, *_ in odd.find(con).get(99, [])}


def test_it_can_be_put_back_among_the_odd(built):
    con, a, _ = built
    first = odd.find(con)[1][0][0]
    odd.dismiss(con, first)
    assert odd.restore(con, first)
    assert first in {aid for aid, *_ in odd.find(con)[1]}


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------

@pytest.fixture
def client(built, tmp_path):
    con, a, b = built
    con.commit()
    con.close()
    return TestClient(serve.create_app(cfg_for(tmp_path,
                                              tmp_path / "photos.db"))), a


def test_the_page_shows_the_event_and_how_far_out(client):
    c, _ = client
    body = c.get("/odd").text
    assert "Lanzarote" in body
    assert "out</span>" in body
    assert "Monte Rosa" not in body


def test_the_page_says_most_of_these_are_not_faults(client):
    """A list of 1,028 photographs headed "problems" would be a lie."""
    c, _ = client
    body = c.get("/odd").text
    assert "good photographs" in body


def test_a_photograph_can_be_opened_and_comes_back_here(client):
    c, _ = client
    assert "back=%2Fodd" in c.get("/odd").text


def test_belongs_here_is_one_press(client):
    c, a = client
    first = a[-1]
    c.post(f"/odd/{first}/dismiss")
    body = c.get("/odd").text
    assert f'/odd/{first}/dismiss' not in body
    assert "put back as belonging" in body


def test_what_was_put_back_can_be_looked_at(client):
    c, a = client
    c.post(f"/odd/{a[-1]}/dismiss")
    body = c.get("/odd", params={"dismissed": 1}).text
    assert "odd after all" in body
    c.post(f"/odd/{a[-1]}/restore")
    assert f'/odd/{a[-1]}/dismiss' in c.get("/odd").text


def test_an_index_with_no_vectors_says_what_is_missing(tmp_path):
    con = db.connect(tmp_path / "photos.db")
    db.migrate(con)
    con.commit()
    con.close()
    c = TestClient(serve.create_app(cfg_for(tmp_path, tmp_path / "photos.db")))
    body = c.get("/odd").text
    assert "Nothing stands out" in body
    assert "mediaidx embed" in body
