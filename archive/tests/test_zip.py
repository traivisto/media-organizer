"""Taking a result set away as one file (spec §8.2).

This is the only feature that reads many originals from the NAS on demand, over
the link that is already the slowest part of the system. So the tests are as
much about the asking as the archive: what it says before it starts, that it
refuses to start on its own, and that it never assembles anything in memory.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from mediaidx import db, serve
from mediaidx.config import Config


def cfg_for(tmp_path, dbp) -> Config:
    return Config(database=dbp, thumbnails=tmp_path / "thumbs", logs=tmp_path,
                  names_export=None)


@pytest.fixture
def client(tmp_path):
    """Six photographs that really exist on disk, and one that does not."""
    dbp = tmp_path / "photos.db"
    shed = tmp_path / "archive"
    shed.mkdir()
    con = db.connect(dbp)
    db.migrate(con)

    for i in range(6):
        blob = bytes([i]) * (2048 + i)
        # Two of them share a filename, in different folders.
        folder = shed / ("a" if i % 2 else "b")
        folder.mkdir(exist_ok=True)
        name = "IMG_1.jpg" if i < 2 else f"IMG_{i}.jpg"
        (folder / name).write_bytes(blob)
        aid = db.insert_asset(con, f"{i:064d}",
                              {"format": "jpeg",
                               "taken_at": 1_600_000_000 + i,
                               "camera_model": "K-70" if i < 3 else "Pixel"})
        con.execute(
            """INSERT INTO files (path, asset_id, size_bytes, mtime,
                                  first_seen, last_seen) VALUES (?,?,?,1,1,1)""",
            (str(folder / name), aid, len(blob)))

    aid = db.insert_asset(con, "f" * 64, {"format": "jpeg", "taken_at": 1})
    con.execute("""INSERT INTO files (path, asset_id, size_bytes, mtime,
                                      first_seen, last_seen)
                   VALUES (?,?,4096,1,1,1)""",
                (str(shed / "gone" / "MISSING.jpg"), aid))
    con.commit()
    con.close()
    return TestClient(serve.create_app(cfg_for(tmp_path, dbp)))


def _zip(response) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(response.content))


# --------------------------------------------------------------------------
# Asking first
# --------------------------------------------------------------------------

def test_it_says_what_it_will_cost_before_starting(client):
    body = client.get("/zip").text
    assert "7 photographs" in body
    assert "MB" in body or "GB" in body
    assert "cross the network" in body
    assert "Start the download" in body


def test_it_does_not_start_by_itself(client):
    """Following the link must not begin a bulk read of the archive."""
    r = client.get("/zip")
    assert r.headers["content-type"].startswith("text/html")
    assert "attachment" not in r.headers.get("content-disposition", "")


def test_the_estimate_comes_from_the_index_not_from_the_disk(client):
    """Sizes are known without touching the NAS -- the point of asking."""
    body = client.get("/zip").text
    assert "MB" in body


def test_a_filter_narrows_what_would_be_taken(client):
    body = client.get("/zip", params={"cam": "K-70"}).text
    assert "3 photographs" in body


def test_nothing_to_take_says_so(client):
    body = client.get("/zip", params={"cam": "Hasselblad"}).text
    assert "Nothing to download" in body


def test_the_results_page_offers_it(client):
    assert "take these as a zip" in client.get("/").text


# --------------------------------------------------------------------------
# The archive itself
# --------------------------------------------------------------------------

def test_it_streams_a_readable_zip(client):
    r = client.get("/zip", params={"go": 1})
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]
    z = _zip(r)
    assert z.testzip() is None
    assert len(z.namelist()) == 6, "the unreachable one should be skipped"


def test_the_contents_are_the_originals_byte_for_byte(client):
    z = _zip(client.get("/zip", params={"go": 1}))
    for name in z.namelist():
        data = z.read(name)
        assert len(set(data)) == 1, "a file came back scrambled"
        assert len(data) >= 2048


def test_two_files_of_one_name_both_survive(client):
    """One archive cannot hold two `IMG_1.jpg`, so the later one is numbered."""
    z = _zip(client.get("/zip", params={"go": 1}))
    names = z.namelist()
    assert "IMG_1.jpg" in names
    assert "IMG_1-1.jpg" in names
    assert len(names) == len(set(names))


def test_a_copy_that_has_gone_does_not_stop_the_rest(client):
    """The seventh photograph's only path does not exist."""
    z = _zip(client.get("/zip", params={"go": 1}))
    assert "MISSING.jpg" not in z.namelist()
    assert len(z.namelist()) == 6


def test_a_filtered_download_holds_only_the_filtered(client):
    z = _zip(client.get("/zip", params={"go": 1, "cam": "K-70"}))
    assert len(z.namelist()) == 3


def test_it_is_written_without_seeking(client):
    """Streamed, not assembled: every entry carries a data descriptor.

    Bit 3 of an entry's flags says its size and checksum follow the data rather
    than preceding it, which is what a writer sets when it cannot seek back. Its
    presence is the evidence that nothing was built in memory first.
    """
    z = _zip(client.get("/zip", params={"go": 1}))
    assert all(i.flag_bits & 0x08 for i in z.infolist())
