"""A stored path, found where it is on the machine that is asking (spec 8.3).

The index records where the indexing computer saw each file. The same folder
is somewhere else to a NAS serving its own share, or to a container with the
share mounted inside it, so a viewing copy is told where each stored prefix is
*here* -- and the stored path itself is never rewritten.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from mediaidx import config, db, paths, serve
from mediaidx.config import Config

STORED = r"\\TRaDiskStation\Media\Pictures"


# --------------------------------------------------------------------------
# The mapping itself
# --------------------------------------------------------------------------

def test_a_stored_path_is_found_under_its_mount():
    got = paths.local(STORED + r"\2016\2016 10\IMG_1.jpg",
                      {STORED: "/photos"}, sep="/")
    assert got == "/photos/2016/2016 10/IMG_1.jpg"


def test_what_follows_the_prefix_keeps_its_spelling():
    """Linux cares about case; the listing that was stored had it right."""
    got = paths.local(STORED + r"\Kesä\Äiti ja Öljy.JPG", {STORED: "/p"}, sep="/")
    assert got == "/p/Kesä/Äiti ja Öljy.JPG"


@pytest.mark.parametrize("prefix", [
    r"\\tradiskstation\media\pictures",        # Windows does not care about case
    "//TRaDiskStation/Media/Pictures",         # nor which slash
    STORED + "\\",                             # nor a trailing one
])
def test_the_prefix_is_matched_the_way_windows_would(prefix):
    got = paths.local(STORED + r"\1998\a.jpg", {prefix: "/photos/"}, sep="/")
    assert got == "/photos/1998/a.jpg"


def test_only_a_whole_folder_name_matches():
    other = r"\\TRaDiskStation\Media\Pictures2\a.jpg"
    assert paths.local(other, {STORED: "/photos"}, sep="/") == other


def test_the_longest_prefix_wins():
    mounts = {r"\\TRaDiskStation\Media": "/media", STORED: "/photos"}
    assert paths.local(STORED + r"\a.jpg", mounts, sep="/") == "/photos/a.jpg"
    assert paths.local(r"\\TRaDiskStation\Media\Video\b.mp4", mounts,
                       sep="/") == "/media/Video/b.mp4"


def test_a_path_nobody_claims_is_left_alone():
    p = r"\\OtherNas\Share\a.jpg"
    assert paths.local(p, {STORED: "/photos"}, sep="/") == p
    assert paths.local(p, {}) == p
    assert paths.local(p, None) == p


def test_the_folder_itself_maps_to_the_mount():
    assert paths.local(STORED, {STORED: "/photos"}, sep="/") == "/photos"
    assert paths.local(STORED + r"\a.jpg", {STORED: "/"}, sep="/") == "/a.jpg"


def test_it_maps_onto_a_windows_folder_too():
    got = paths.local(STORED + r"\1998\a.jpg", {STORED: r"D:\Copy"}, sep="\\")
    assert got == r"D:\Copy\1998\a.jpg"


def test_the_config_carries_the_mounts(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        "[paths]\n"
        "database = 'x.db'\n\n"
        "[paths.mounts]\n"
        "'\\\\TRaDiskStation\\Media\\Pictures' = '/photos'\n", encoding="utf-8")
    cfg = config.load(cfg_file)
    assert cfg.mounts == {STORED: "/photos"}
    assert config.load(tmp_path / "none.toml").mounts == {}


# --------------------------------------------------------------------------
# Through the application
# --------------------------------------------------------------------------

@pytest.fixture
def served(tmp_path):
    """A photograph the index knows by a NAS path that does not exist here,
    whose file is really in a folder of this machine."""
    here = tmp_path / "mounted"
    (here / "2002").mkdir(parents=True)
    buf = io.BytesIO()
    Image.new("RGB", (3000, 2000), (20, 90, 160)).save(buf, "JPEG")
    (here / "2002" / "IMG_1.jpg").write_bytes(buf.getvalue())

    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)
    aid = db.insert_asset(con, "a" * 64, {"format": "jpeg", "taken_at": 1_020_000_000})
    con.execute("""INSERT INTO files (path, asset_id, size_bytes, mtime,
                                      first_seen, last_seen) VALUES (?,?,?,1,1,1)""",
                (STORED + r"\2002\IMG_1.jpg", aid, len(buf.getvalue())))
    con.commit()
    con.close()

    def client(mounts):
        cfg = Config(database=dbp, thumbnails=tmp_path / "thumbs", logs=tmp_path,
                     names_export=None, mounts=mounts)
        return TestClient(serve.create_app(cfg), client=("127.0.0.1", 50000))

    return client, aid, buf.getvalue(), here


def test_the_original_is_served_from_where_it_is_here(served):
    client, aid, original, here = served
    r = client({STORED: str(here)}).get(f"/full/{aid}")
    assert r.status_code == 200
    assert r.content == original


def test_without_the_mount_it_is_not_reachable(served):
    client, aid, _, _ = served
    assert client({}).get(f"/full/{aid}").status_code == 503


def test_the_slideshow_picture_and_the_zip_find_it_too(served):
    client, aid, original, here = served
    c = client({STORED: str(here)})
    r = c.get(f"/view/{aid}", params={"w": 1200})
    assert r.status_code == 200
    assert max(Image.open(io.BytesIO(r.content)).size) == 1200
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(c.get("/zip", params={"go": 1}).content))
    assert z.read("IMG_1.jpg") == original


def test_the_page_still_shows_the_path_as_it_is_stored(served):
    """One name for a file, whichever machine is asking."""
    client, aid, _, here = served
    body = client({STORED: str(here)}).get(f"/photo/{aid}").text
    assert r"\\TRaDiskStation\Media\Pictures\2002\IMG_1.jpg" in body
    assert "mounted" not in body


def test_opening_hands_over_the_local_path(served, monkeypatch):
    client, aid, _, here = served
    opened = []
    monkeypatch.setattr(serve, "open_with_default_app", opened.append)
    r = client({STORED: str(here)}).post(
        f"/photo/{aid}/open", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert opened == [paths.local(STORED + r"\2002\IMG_1.jpg", {STORED: str(here)})]
