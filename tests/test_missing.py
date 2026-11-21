"""A photograph whose files have all gone is left out of every screen (spec 10.3).

Deleted while looking through a new batch, typically. The rows stay -- a file
that was only moved, or a share half mounted, brings the photograph back with
its names at the next scan -- so this is about what the screens show, both on
the PC and in the viewing copy on the NAS.
"""

from __future__ import annotations

import re

import numpy as np
import pytest
from fastapi.testclient import TestClient

from mediaidx import db, embed, serve
from mediaidx.config import Config

N, GONE = 12, 4


class _FixedEncoder:
    def __init__(self, vec):
        self._vec = vec

    def encode_text(self, texts):
        return np.array([self._vec] * len(texts), dtype=np.float32)


@pytest.fixture
def library(tmp_path, monkeypatch):
    """Twelve photographs, each in one file. The first four -- also the four
    closest to any description, so a ranking would put them first -- have been
    deleted; the fifth has two copies, one of them deleted."""
    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)
    rng = np.random.default_rng(3)
    probe = rng.normal(size=embed.DIM)
    probe /= np.linalg.norm(probe)
    ids = []
    for i in range(N):
        v = probe + rng.normal(scale=0.02 if i < GONE + 1 else 1.0, size=embed.DIM)
        v = (v / np.linalg.norm(v)).astype(np.float32)
        aid = db.insert_asset(con, f"{i:064d}", {"format": "jpeg",
                                                  "taken_at": 1_700_000_000 + i})
        embed.store(con, aid, "test", v, vec_ok=False)
        con.execute("INSERT INTO files (path, asset_id, size_bytes, mtime, "
                    "first_seen, last_seen, missing_since) VALUES (?,?,1,0,0,0,?)",
                    (f"\\\\NAS\\Pictures\\2026\\{i}.heic", aid,
                     5 if i < GONE else None))
        ids.append(aid)
    con.execute("INSERT INTO files (path, asset_id, size_bytes, mtime, first_seen, "
                "last_seen, missing_since) VALUES (?,?,1,0,0,0,5)",
                ("\\\\NAS\\Pictures\\Unsorted\\copy.heic", ids[GONE]))
    con.commit()
    con.close()
    monkeypatch.setattr(serve.embed, "Encoder", lambda **kw: _FixedEncoder(probe))

    def client(**ui):
        cfg = Config(database=dbp, thumbnails=tmp_path / "thumbs", logs=tmp_path,
                     names_export=None, ui=ui)
        return TestClient(serve.create_app(cfg))
    return dbp, ids, client


def _shown(body: str, ids) -> set:
    linked = {int(m) for m in re.findall(r'href="/photo/(\d+)', body)}
    return linked & set(ids)


@pytest.mark.parametrize("ui", [{}, {"read_only": True}], ids=["pc", "nas"])
def test_browsing_leaves_out_what_was_deleted(library, ui):
    dbp, ids, client = library
    body = client(**ui).get("/").text
    assert f"{N - GONE} photographs" in body
    assert _shown(body, ids) == set(ids[GONE:])


@pytest.mark.parametrize("ui", [{}, {"read_only": True}], ids=["pc", "nas"])
def test_a_described_search_leaves_it_out_too(library, ui):
    """The vector index still holds them, and they rank first."""
    dbp, ids, client = library
    body = client(**ui).get("/", params={"q": "whatever", "more": 1}).text
    shown = _shown(body, ids)
    assert shown and not shown & set(ids[:GONE])
    assert ids[GONE] in shown          # one copy left is enough


def test_its_own_page_says_not_found(library):
    dbp, ids, client = library
    assert client().get(f"/photo/{ids[0]}").status_code == 404


def test_found_again_it_is_back(library):
    """Nothing was deleted from the index: a scan that finds the file clears
    missing_since, and the photograph is simply there again."""
    dbp, ids, client = library
    con = db.connect(dbp, create=False)
    con.execute("UPDATE files SET missing_since = NULL WHERE asset_id = ?", (ids[0],))
    con.commit()
    con.close()
    c = client()
    assert c.get(f"/photo/{ids[0]}").status_code == 200
    assert f"{N - GONE + 1} photographs" in c.get("/").text
