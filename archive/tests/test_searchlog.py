"""The search log (spec 8.7): what serve writes, and what `searches` makes of it."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from mediaidx import db, searchlog, serve
from mediaidx.config import Config


@pytest.fixture
def library(tmp_path):
    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)
    con.execute("INSERT INTO places (id, kind, name, path, rank_pop) "
                "VALUES (1, 'locality', 'Alicante', 'Spain/Alicante', 900)")
    con.execute("INSERT INTO place_names (place_id, norm, own) VALUES (1, 'alicante', 1)")
    # Enough that the name is read as the place, not offered as a maybe.
    for i in range(30):
        aid = db.insert_asset(con, f"{i:064d}", {"format": "jpeg",
                                                  "taken_at": 1_700_000_000 + i})
        con.execute("UPDATE assets SET place_id = 1, place_source = 'gps' "
                    "WHERE id = ?", (aid,))
    con.commit()
    con.close()
    log = tmp_path / "logs" / "searches.jsonl"

    def client(**ui):
        cfg = Config(database=dbp, thumbnails=tmp_path / "t", logs=tmp_path,
                     names_export=None, search_log=log, ui=ui)
        return TestClient(serve.create_app(cfg))
    return log, client


def _lines(log):
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]


def test_a_search_is_written_with_how_it_was_read(library):
    log, client = library
    assert client().get("/", params={"q": "Alicante"}).status_code == 200
    [e] = _lines(log)
    assert e["q"] == "Alicante" and e["places"] == ["Alicante · Spain"]
    assert e["found"] == 30 and e["where"] == "pc" and e["ranked"] is False
    assert e["ip"] == "testclient"


def test_one_line_per_search_not_per_page(library):
    log, client = library
    c = client()
    c.get("/")                                         # the front page: nothing asked
    c.get("/", params={"q": "Alicante", "page": 1})    # paging through
    assert not log.exists()
    c.get("/", params={"from": "2023"})                # a filter alone is a search
    assert len(_lines(log)) == 1


def test_the_viewing_copy_says_nas_and_names_the_device_behind_the_proxy(library):
    log, client = library
    client(read_only=True).get("/", params={"q": "Alicante"},
                               headers={"X-Forwarded-For": "100.64.1.7, 192.168.86.253"})
    [e] = _lines(log)
    assert e["where"] == "nas" and e["ip"] == "100.64.1.7"


def test_a_log_that_cannot_be_written_does_not_stop_the_search(library, tmp_path):
    log, client = library
    log.parent.write_text("a file where the folder should be")
    assert client().get("/", params={"q": "Alicante"}).status_code == 200


def test_the_summary_finds_what_went_badly():
    # From different devices, so none of them is a return to the same results.
    n = iter(range(10))
    e = lambda q, found, **kw: {"t": "2026-11-21T10:00:00", "where": "nas",
                                "ip": f"1.2.3.{next(n)}", "q": q, "found": found,
                                "text": kw.get("text", ""), "ranked": kw.get("ranked", False),
                                "people": kw.get("people", []), "places": [],
                                "events": [], "dates": [], "mode": []}
    s = searchlog.summary([e("Emmi", 40, people=["Emmi"]),
                           e("emmi", 40, people=["Emmi"]),
                           e("Anskun", 12, text="anskun", ranked=True),
                           e("Emmi 1998", 0, people=["Emmi"])])
    assert s["common"][0] == ("emmi", 2)
    assert s["nothing"] == [("emmi 1998", 1)]
    assert s["unread"] == [("anskun", 1)]


def test_coming_back_to_the_results_is_not_a_second_search():
    """Seen on the NAS on the first evening: the same words twelve seconds
    apart, from returning to the results from a photograph."""
    e = lambda t, q, ip="1.2.3.4": {"t": f"2026-11-21T{t}", "where": "nas",
                                    "ip": ip, "q": q, "found": 8, "filters": {}}
    s = searchlog.summary([e("18:12:46", "totte tommi only"),
                           e("18:12:58", "Totte Tommi only"),
                           e("18:20:00", "totte tommi only"),     # still browsing it
                           e("18:20:05", "totte tommi only", ip="5.6.7.8"),
                           e("19:00:00", "totte tommi only")])    # an hour on: again
    assert s["count"] == 3
    assert s["common"] == [("totte tommi only", 3)]


def test_searches_reads_the_pc_log_and_the_nas_one(tmp_path):
    pc = tmp_path / "pc" / "searches.jsonl"
    nas = tmp_path / "project" / "logs" / "searches.jsonl"
    for path, where, q in ((pc, "pc", "Alicante"), (nas, "nas", "auringonlasku")):
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"t": "2026-11-21T10:00:00", "where": where,
                                    "ip": "x", "q": q, "found": 0, "text": q,
                                    "ranked": True}) + "\n", encoding="utf-8")
    cfg = tmp_path / "config.toml"
    cfg.write_text("[paths]\n"
                   f'database = "{(tmp_path / "photos.db").as_posix()}"\n'
                   f'search_log = "{pc.as_posix()}"\n'
                   f'publish_to = "{(tmp_path / "project").as_posix()}"\n',
                   encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "MEDIAIDX_CONFIG"}
    r = subprocess.run([sys.executable, "-m", "mediaidx", "--config", str(cfg),
                        "searches"], capture_output=True, env=env)
    out = r.stdout.decode("utf-8")
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert "2 searches" in out and "Alicante" in out and "auringonlasku" in out
    assert "found nothing" in out
