"""Publishing a viewing copy (spec 8.4).

The archive is kept on one computer and looked at from another (spec 8.3).
`publish` copies what the viewing copy serves into one folder, laid out as the
Container Manager project in deploy/nas expects:

    <target>/docker-compose.yml
    <target>/README.md     how to set it up in Container Manager
    <target>/app/       the application, from which the image is built
    <target>/data/      config.toml, photos.db, thumbs/, textmodel/

Run after indexing or naming on the computer that keeps the archive. It copies
only what has changed, except the database, which is small beside the rest and
is always copied whole -- a consistent snapshot rather than a file caught in the
middle of a write.

The order is the correctness condition. Thumbnails and the text model go first
and the database last, so a viewing copy never serves a photograph whose
thumbnail has not arrived. Every file is written beside its final name and moved
into place in one step, so nothing half-copied is ever served, and an
interrupted publish leaves the previous copy whole.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import db
from . import paths as pathutil
from . import textmodel

# Left in every folder publish has made. It refuses to write into a folder
# that holds anything else and lacks this, because it removes files there that
# the archive no longer has, and somebody else's folder is not its to tidy.
MARK = ".kuva-arkisto-publish"

PACKAGE = Path(__file__).resolve().parent
DEPLOY = PACKAGE.parent / "deploy" / "nas"

# What the image is built from, beside the package itself.
BUILD_FILES = ("Dockerfile", "Dockerfile.dockerignore", "requirements.txt")
THUMB_DIRS = ("s", "m", "faces")
RETIRED = ("compose.yaml",)


class PublishError(Exception):
    """A reason not to publish, said before anything has been written."""


def _inside(path: str, root: str) -> bool:
    p = path.replace("/", "\\").rstrip("\\").casefold()
    r = root.replace("/", "\\").rstrip("\\").casefold()
    return p == r or p.startswith(r + "\\")


def _unix(data: bytes) -> bytes:
    """Line endings as the NAS reads them. A Dockerfile checked out on Windows
    may carry CRLF, and a line continuation followed by a carriage return is
    not a continuation."""
    return data.replace(b"\r\n", b"\n")


def _put(src_bytes: bytes, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    part.write_bytes(src_bytes)
    os.replace(part, dest)


def _copy(src: Path, dest: Path) -> None:
    """A file, copied beside its final name and moved into place."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    shutil.copyfile(src, part)
    os.replace(part, dest)


def _listing(folder: Path) -> dict:
    """{path relative to folder: size} for every file beneath it, one level
    of shards deep as the thumbnail cache is."""
    out = {}
    if not folder.is_dir():
        return out
    for entry in os.scandir(folder):
        if entry.is_dir():
            for f in os.scandir(entry.path):
                if f.is_file() and not f.name.endswith(".part"):
                    out[f"{entry.name}/{f.name}"] = f.stat().st_size
        elif entry.is_file() and not entry.name.endswith(".part"):
            out[entry.name] = entry.stat().st_size
    return out


def _check_target(con, target: Path) -> None:
    roots = [r[0] for r in con.execute(
        "SELECT path FROM folders WHERE root_added_at IS NOT NULL")]
    # A mapped drive is compared as the share it maps, which is how roots are
    # stored (spec 10.3).
    where = {str(target), pathutil.to_unc(str(target))}
    for root in roots:
        if any(_inside(w, root) for w in where):
            raise PublishError(
                f"{target} is inside {root}, which is indexed. Nothing is ever "
                "written under a folder the archive reads (P1).")
    if target.is_dir() and any(target.iterdir()) and not (target / MARK).exists():
        raise PublishError(
            f"{target} already holds files that publish did not put there. "
            "Choose an empty folder, or one publish made.")


def _check_text_model(con, folder: Path) -> str:
    meta = folder / textmodel.META_FILE
    if not meta.exists() or not (folder / textmodel.MODEL_FILE).exists():
        raise PublishError(
            f"no text model at {folder}, and the viewing copy needs it for "
            "described search. Run `mediaidx textmodel` first.")
    have = json.loads(meta.read_text(encoding="utf-8"))["tag"]
    row = con.execute("SELECT model FROM image_vectors LIMIT 1").fetchone()
    if row is not None and row[0] != have:
        raise PublishError(
            f"the text model at {folder} is {have}, but the photographs were "
            f"indexed with {row[0]}. Run `mediaidx textmodel` again.")
    return have


def publish(cfg, target, *, dry_run: bool = False, workers: int = 8,
            log=print) -> dict:
    """Bring `target` up to date with the archive. Returns what was done."""
    target = Path(target)
    model_dir = Path(cfg.text_model or Path(cfg.database).parent / "textmodel")
    if not DEPLOY.is_dir():
        raise PublishError(f"{DEPLOY} is missing; publish runs from the source tree")

    src = db.connect_read_only(cfg.database)
    try:
        _check_target(src, target)
        tag = _check_text_model(src, model_dir)
        assets = src.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    finally:
        src.close()

    done = {"target": str(target), "dry_run": dry_run, "text_model": tag,
            "photographs": assets}
    data = target / "data"

    # -- the application, the project file, the configuration ---------------
    wanted = {f"app/deploy/nas/{n}": _unix((DEPLOY / n).read_bytes())
              for n in BUILD_FILES}
    for py in sorted(PACKAGE.glob("*.py")):
        wanted[f"app/mediaidx/{py.name}"] = py.read_bytes()
    wanted["docker-compose.yml"] = _unix((DEPLOY / "docker-compose.yml").read_bytes())
    wanted["README.md"] = _unix((DEPLOY / "README.md").read_bytes())
    wanted["data/config.toml"] = _unix((DEPLOY / "config.toml").read_bytes())
    changed = [rel for rel, body in wanted.items()
               if not (target / rel).is_file() or (target / rel).read_bytes() != body]
    stale = []
    if (target / "app" / "mediaidx").is_dir():
        stale = [f"app/mediaidx/{p.name}" for p in (target / "app" / "mediaidx").iterdir()
                 if p.is_file() and f"app/mediaidx/{p.name}" not in wanted]
    # Names this project's files once had, taken away so Container Manager is
    # never offered two compose files to choose between.
    if (target / MARK).exists():
        stale += [old for old in RETIRED if (target / old).is_file()]
    done["app_changed"] = sorted(r for r in changed + stale if r.startswith("app/"))
    done["files_changed"] = sorted(changed + stale)

    # -- thumbnails: the new ones, and away with the ones nobody has -----------
    copy, prune, size = [], [], 0
    for sub in THUMB_DIRS:
        here = _listing(Path(cfg.thumbnails) / sub)
        there = _listing(data / "thumbs" / sub)
        for rel, n in here.items():
            if there.get(rel) != n:
                copy.append(f"{sub}/{rel}")
                size += n
        prune += [f"{sub}/{rel}" for rel in there if rel not in here]
    done["thumbs_copied"], done["thumbs_removed"] = len(copy), len(prune)
    done["thumbs_bytes"] = size

    # -- the text model ----------------------------------------------------------
    model_copy = [p.name for p in sorted(model_dir.iterdir()) if p.is_file() and (
        not (data / "textmodel" / p.name).is_file()
        or (data / "textmodel" / p.name).stat().st_size != p.stat().st_size
        or (p.name == textmodel.META_FILE and
            (data / "textmodel" / p.name).read_bytes() != p.read_bytes()))]
    done["text_model_copied"] = model_copy
    done["database_bytes"] = Path(cfg.database).stat().st_size

    if dry_run:
        return done

    t0 = time.time()
    target.mkdir(parents=True, exist_ok=True)
    _put(json.dumps({"started": int(t0)}).encode(), target / MARK)

    for rel in done["files_changed"]:
        if rel in wanted:
            _put(wanted[rel], target / rel)
        else:
            (target / rel).unlink()
    if done["files_changed"]:
        log(f"  application and settings: {len(done['files_changed'])} files")

    if copy:
        log(f"  thumbnails: copying {len(copy):,} ({size / 1e9:.2f} GB)")
        t = time.time()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            jobs = [pool.submit(_copy, Path(cfg.thumbnails) / rel,
                                data / "thumbs" / rel) for rel in copy]
            for i, job in enumerate(jobs, 1):
                job.result()
                if i % 5000 == 0:
                    log(f"    {i:,} of {len(copy):,} ({time.time() - t:.0f}s)")
    for rel in prune:
        (data / "thumbs" / rel).unlink(missing_ok=True)
    if prune:
        log(f"  thumbnails: removed {len(prune):,} nobody has any more")

    for name in model_copy:
        log(f"  text model: {name}")
        _copy(model_dir / name, data / "textmodel" / name)

    # A snapshot, not a copy of the file: the database may be in the middle of
    # a write, and VACUUM INTO reads it in one transaction. It is made on this
    # machine and then sent, because building it over the network would hold
    # that transaction open for the whole crossing. The result is in DELETE
    # mode, which is what lets the NAS open it read-only with no -shm beside it.
    t = time.time()
    snap = Path(cfg.database).with_name("publish-snapshot.db")
    snap.unlink(missing_ok=True)
    src = db.connect_read_only(cfg.database)
    try:
        src.execute("VACUUM INTO ?", (str(snap),))
    finally:
        src.close()
    try:
        log(f"  database: snapshot {snap.stat().st_size / 1e9:.2f} GB in "
            f"{time.time() - t:.0f}s, sending")
        _copy(snap, data / "photos.db")
    finally:
        snap.unlink(missing_ok=True)

    check = db.connect_read_only(data / "photos.db")
    try:
        sent = check.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        mode = check.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        check.close()
    if sent != assets or mode != "delete":
        raise PublishError(f"the database that arrived holds {sent:,} photographs "
                           f"in {mode} mode; expected {assets:,} in delete mode")

    done["seconds"] = round(time.time() - t0)
    _put(json.dumps({"published": int(time.time()), **{
        k: v for k, v in done.items() if k != "files_changed"}}, indent=2).encode(),
         target / MARK)
    return done
