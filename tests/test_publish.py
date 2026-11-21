"""Publishing a viewing copy (spec 8.4).

What is published is served to whoever opens the viewing copy, so the tests are
about it arriving whole: laid out as the container expects, readable without
writing, never half-copied, and never put where it could damage anything --
inside the archive, or in a folder publish did not make.
"""

from __future__ import annotations

import json
import os
import sqlite3

import numpy as np
import pytest

from mediaidx import db, embed, publish, textmodel
from mediaidx.config import Config

TAG = "ViT-SO400M-14-SigLIP-384/webli"
BS = chr(92)
BS2 = BS * 2


@pytest.fixture
def kept(tmp_path):
    """The computer that keeps the archive: a database in WAL mode, as the
    real one is, thumbnails in all three caches, and a built text model."""
    home = tmp_path / "home"
    home.mkdir()
    dbp = home / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)
    rng = np.random.default_rng(1)
    for i in range(6):
        aid = db.insert_asset(con, f"{i:064d}", {"format": "jpeg", "taken_at": 1_600_000_000 + i})
        v = rng.normal(size=embed.DIM)
        embed.store(con, aid, TAG, (v / np.linalg.norm(v)).astype(np.float32), vec_ok=False)
    con.execute("INSERT INTO folders (path, root_added_at) VALUES (?, 1)",
                (str(tmp_path / "Pictures"),))
    con.commit()
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

    thumbs = home / "thumbs"
    for sub, n in (("s", 6), ("m", 6), ("faces", 3)):
        for i in range(n):
            f = thumbs / sub / f"{i:02d}" / f"{i:064d}.webp"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(bytes([i]) * (100 + i))

    model = home / "textmodel"
    model.mkdir()
    (model / textmodel.MODEL_FILE).write_bytes(b"onnx" * 1000)
    (model / textmodel.TOKENIZER_FILE).write_text("{}", encoding="utf-8")
    (model / textmodel.META_FILE).write_text(json.dumps({"tag": TAG}), encoding="utf-8")

    cfg = Config(database=dbp, thumbnails=thumbs, logs=home, names_export=None)
    yield cfg, con, tmp_path / "nas" / "kuva-arkisto"
    con.close()


def _quiet(*a, **k):
    pass


def _files(folder):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def test_the_first_publish_lays_out_the_project(kept):
    cfg, _, target = kept
    done = publish.publish(cfg, target, log=_quiet)
    assert (target / "docker-compose.yml").is_file()
    assert (target / "README.md").is_file()
    assert (target / "data" / "config.toml").is_file()
    assert (target / "app" / "deploy" / "nas" / "Dockerfile").is_file()
    assert (target / "app" / "mediaidx" / "serve.py").is_file()
    assert (target / "app" / "mediaidx" / "publish.py").is_file()
    assert not list((target / "app").rglob("__pycache__"))
    assert (target / "data" / "textmodel" / textmodel.MODEL_FILE).read_bytes() == b"onnx" * 1000
    assert len(_files(target / "data" / "thumbs")) == 15
    assert done["thumbs_copied"] == 15 and done["photographs"] == 6
    assert not [f for f in _files(target) if f.endswith(".part")]


def test_the_database_arrives_readable_without_writing(kept):
    """DELETE mode, so a read-only mount needs no -shm beside it."""
    cfg, _, target = kept
    publish.publish(cfg, target, log=_quiet)
    sent = target / "data" / "photos.db"
    assert not sent.with_name("photos.db-wal").exists()
    con = sqlite3.connect(sent.resolve().as_uri() + "?mode=ro", uri=True)
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert con.execute("SELECT COUNT(*) FROM image_vectors").fetchone()[0] == 6
    con.close()
    assert not cfg.database.with_name("publish-snapshot.db").exists()


def test_what_the_nas_builds_from_has_unix_line_endings(kept, monkeypatch, tmp_path):
    """A line continuation followed by a carriage return is not a continuation."""
    cfg, _, target = kept
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    for name in ("Dockerfile", "Dockerfile.dockerignore", "requirements.txt",
                 "docker-compose.yml", "README.md", "config.toml"):
        (deploy / name).write_bytes(b"RUN a \\\r\n && b\r\n")
    monkeypatch.setattr(publish, "DEPLOY", deploy)
    publish.publish(cfg, target, log=_quiet)
    for f in ("app/deploy/nas/Dockerfile", "docker-compose.yml", "data/config.toml"):
        assert b"\r" not in (target / f).read_bytes(), f


def test_a_second_publish_sends_only_what_changed(kept):
    cfg, _, target = kept
    publish.publish(cfg, target, log=_quiet)
    old = target / "data" / "thumbs" / "s" / "01" / f"{1:064d}.webp"
    before = old.stat().st_mtime_ns
    new = cfg.thumbnails / "m" / "99" / f"{99:064d}.webp"
    new.parent.mkdir(parents=True)
    new.write_bytes(b"x" * 50)
    (cfg.thumbnails / "faces" / "02" / f"{2:064d}.webp").unlink()

    done = publish.publish(cfg, target, log=_quiet)
    assert (done["thumbs_copied"], done["thumbs_removed"]) == (1, 1)
    assert done["text_model_copied"] == [] and done["app_changed"] == []
    assert old.stat().st_mtime_ns == before, "an unchanged thumbnail was sent again"
    assert (target / "data" / "thumbs" / "m" / "99" / f"{99:064d}.webp").is_file()
    assert not (target / "data" / "thumbs" / "faces" / "02" / f"{2:064d}.webp").exists()


def test_a_changed_application_is_reported_so_the_image_is_rebuilt(kept):
    cfg, _, target = kept
    first = publish.publish(cfg, target, log=_quiet)
    assert "app/mediaidx/serve.py" in first["app_changed"]
    (target / "app" / "mediaidx" / "serve.py").write_text("old", encoding="utf-8")
    (target / "app" / "mediaidx" / "gone.py").write_text("x", encoding="utf-8")
    done = publish.publish(cfg, target, log=_quiet)
    assert done["app_changed"] == ["app/mediaidx/gone.py", "app/mediaidx/serve.py"]
    assert not (target / "app" / "mediaidx" / "gone.py").exists()


def test_new_photographs_reach_the_copy(kept):
    cfg, con, target = kept
    publish.publish(cfg, target, log=_quiet)
    db.insert_asset(con, "f" * 64, {"format": "jpeg", "taken_at": 1})
    con.commit()
    publish.publish(cfg, target, log=_quiet)
    sent = sqlite3.connect((target / "data" / "photos.db").resolve().as_uri() + "?mode=ro", uri=True)
    assert sent.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 7
    sent.close()


def test_a_failed_send_leaves_the_previous_database_in_place(kept, monkeypatch):
    cfg, con, target = kept
    publish.publish(cfg, target, log=_quiet)
    db.insert_asset(con, "f" * 64, {"format": "jpeg", "taken_at": 1})
    con.commit()
    real = publish._copy

    def broken(src, dest):
        if dest.name == "photos.db":
            dest.with_name("photos.db.part").write_bytes(b"half")
            raise OSError("the NAS went to sleep")
        real(src, dest)

    monkeypatch.setattr(publish, "_copy", broken)
    with pytest.raises(OSError):
        publish.publish(cfg, target, log=_quiet)
    sent = sqlite3.connect((target / "data" / "photos.db").resolve().as_uri() + "?mode=ro", uri=True)
    assert sent.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 6
    sent.close()
    assert not cfg.database.with_name("publish-snapshot.db").exists()


def test_a_dry_run_writes_nothing(kept):
    cfg, _, target = kept
    done = publish.publish(cfg, target, dry_run=True, log=_quiet)
    assert done["thumbs_copied"] == 15 and "docker-compose.yml" in done["files_changed"]
    assert not target.exists()


def test_a_folder_it_did_not_make_is_left_alone(kept):
    """It removes what the archive no longer has; not in somebody else's folder."""
    cfg, _, target = kept
    target.mkdir(parents=True)
    (target / "mine.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(publish.PublishError, match="did not put there"):
        publish.publish(cfg, target, log=_quiet)
    assert _files(target) == ["mine.txt"]


def test_nothing_is_published_inside_the_archive(kept, tmp_path):
    cfg, _, _ = kept
    inside = tmp_path / "Pictures" / "viewer"
    with pytest.raises(publish.PublishError, match="indexed"):
        publish.publish(cfg, inside, log=_quiet)
    assert not inside.exists()


def test_a_text_model_from_another_model_is_refused(kept):
    cfg, _, target = kept
    meta = cfg.database.parent / "textmodel" / textmodel.META_FILE
    meta.write_text(json.dumps({"tag": "ViT-B-16-SigLIP/webli"}), encoding="utf-8")
    with pytest.raises(publish.PublishError, match="mediaidx textmodel"):
        publish.publish(cfg, target, log=_quiet)
    assert not target.exists()


def test_no_text_model_is_refused(kept):
    cfg, _, target = kept
    (cfg.database.parent / "textmodel" / textmodel.MODEL_FILE).unlink()
    with pytest.raises(publish.PublishError, match="mediaidx textmodel"):
        publish.publish(cfg, target, log=_quiet)


# --------------------------------------------------------------------------
# Opening by URI, wherever the database is
# --------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="Windows paths")
@pytest.mark.parametrize("path,uri", [
    (BS2 + "TRaDiskStation" + BS + "docker" + BS + "kuva arkisto" + BS + "photos.db",
     "file:////TRaDiskStation/docker/kuva%20arkisto/photos.db?mode=ro"),
    ("C:" + BS + "Users" + BS + "x" + BS + "Kesä.db", "file:/C:/Users/x/Kes%C3%A4.db?mode=ro"),
])
def test_a_network_path_opens_with_no_authority(path, uri):
    """`as_uri()` gave file://TRaDiskStation/..., which SQLite refuses."""
    assert db.read_only_uri(path) == uri


def test_the_read_only_uri_really_opens_read_only(kept):
    cfg, _, _ = kept
    con = db.connect_read_only(cfg.database)
    assert con.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 6
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        con.execute("DELETE FROM assets")
    con.close()
    with pytest.raises(FileNotFoundError):
        db.connect_read_only(cfg.database.with_name("none.db"))


def test_a_compose_file_by_an_older_name_is_taken_away(kept):
    """Container Manager must not be offered two compose files."""
    cfg, _, target = kept
    publish.publish(cfg, target, log=_quiet)
    (target / "compose.yaml").write_text("services: {}", encoding="utf-8")
    done = publish.publish(cfg, target, log=_quiet)
    assert "compose.yaml" in done["files_changed"]
    assert not (target / "compose.yaml").exists()
    assert (target / "docker-compose.yml").is_file()


def _version(target):
    for line in (target / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("KUVA_APP_VERSION="):
            return line.split("=", 1)[1]


def test_the_image_is_named_after_the_application_in_it(kept):
    """Container Manager's Build keeps an image whose name exists already."""
    cfg, con, target = kept
    first = publish.publish(cfg, target, log=_quiet)
    v1 = _version(target)
    assert v1 == first["app_version"] and len(v1) == 12

    db.insert_asset(con, "e" * 64, {"format": "jpeg", "taken_at": 1})
    con.commit()
    publish.publish(cfg, target, log=_quiet)
    assert _version(target) == v1, "new photographs are not a new application"

    (target / "app" / "mediaidx" / "serve.py").write_text("old", encoding="utf-8")
    publish.publish(cfg, target, log=_quiet)
    assert _version(target) == v1, "the version is of what is sent, not what was there"


def test_a_changed_application_gets_a_new_name(kept, monkeypatch, tmp_path):
    cfg, _, target = kept
    publish.publish(cfg, target, log=_quiet)
    v1 = _version(target)
    deploy = tmp_path / "deploy2"
    deploy.mkdir()
    for p in publish.DEPLOY.iterdir():
        if p.is_file():
            (deploy / p.name).write_bytes(p.read_bytes())
    (deploy / "requirements.txt").write_bytes(b"numpy==9.9\n")
    monkeypatch.setattr(publish, "DEPLOY", deploy)
    done = publish.publish(cfg, target, log=_quiet)
    assert _version(target) != v1
    assert "app/deploy/nas/requirements.txt" in done["app_changed"]
