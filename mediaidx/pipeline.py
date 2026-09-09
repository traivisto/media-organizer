"""Stage 1 orchestration: hash, metadata, date resolution.

Concurrency is a pool of reader threads feeding a single database writer. The
network is the bottleneck (a single sequential read off the NAS measured around
10 MB/s against a ~110 MB/s link), so parallel reads are the main lever.

Resumability (spec P3) is at chunk granularity: each chunk is committed in one
transaction, so an interrupted run loses at most the chunk in flight and
repeats no completed work.
"""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

import blake3

from . import corrections
from . import db, dates, exif, walker
from . import paths as pathutil

STAGE = "meta"
# 2: GPS was read without its N/S/E/W reference, so every western longitude was
# stored as its eastern mirror image. The coordinates on disk cannot be
# repaired -- the sign is not in them -- so the stage goes pending again.
STAGE_VERSION = 2
READ_BLOCK = 1 << 20


@dataclass
class Stats:
    seen: int = 0
    skipped: int = 0
    new_files: int = 0
    new_assets: int = 0
    duplicates: int = 0
    failed: int = 0
    missing: int = 0
    bytes_read: int = 0
    conflicts: int = 0
    by_source: dict = field(default_factory=dict)
    started: float = field(default_factory=time.time)

    @property
    def elapsed(self) -> float:
        return time.time() - self.started

    @property
    def mb_per_s(self) -> float:
        e = self.elapsed
        return (self.bytes_read / 1e6 / e) if e > 0 else 0.0


def hash_file(path: str) -> tuple[str, int]:
    h = blake3.blake3()
    n = 0
    with open(path, "rb", buffering=0) as fh:
        while True:
            block = fh.read(READ_BLOCK)
            if not block:
                break
            h.update(block)
            n += len(block)
    return h.hexdigest(), n


def _chunks(iterable, size):
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def index_folder(con, root: str, cfg, *, recurse: bool = True,
                 user_label: str | None = None, person_hint: str | None = None,
                 dry_run: bool = False, progress=None) -> Stats:
    st = Stats()
    now = int(time.time())
    fn_pats, folder_pats = dates.compile_patterns(cfg.scan)

    exe = exif.find_exiftool(cfg.tools.get("exiftool", ""))
    if exe is None and not dry_run:
        raise RuntimeError(
            "exiftool not found. Install it, or set tools.exiftool in the config.")

    root_unc = pathutil.to_unc(root)
    if not dry_run:
        parsed_root = dates.parse_folder(os.path.basename(root_unc.rstrip("\\")), folder_pats)
        db.upsert_folder(con, root_unc, parsed_root, is_root=True,
                         user_label=user_label, person_hint=person_hint, now=now)
        con.commit()

    known = {} if dry_run else db.known_files(con, root_unc)
    folder_ids: dict[str, int] = {}
    seen_paths: set[str] = set()
    threads = int(cfg.scan.get("reader_threads", 12))
    chunk_size = int(cfg.scan.get("chunk_size", 256))

    ctx = exif.Exif(exe) if not dry_run else None
    et = ctx.__enter__() if ctx else None

    try:
        pool = ThreadPoolExecutor(max_workers=threads)
        try:
            for chunk in _chunks(walker.walk(root, cfg, recurse=recurse), chunk_size):
                todo = []
                for c in chunk:
                    st.seen += 1
                    seen_paths.add(c.path)
                    prev = known.get(c.path)
                    if prev and prev[0] == c.size and prev[1] == c.mtime:
                        st.skipped += 1
                        continue
                    todo.append(c)

                if progress:
                    progress(st)
                if dry_run or not todo:
                    continue

                # --- read and hash in parallel -----------------------------
                hashed: list[tuple[walker.Candidate, str]] = []
                for c, result in zip(todo, pool.map(
                        lambda x: _safe_hash(x.walk_path), todo)):
                    if result is None:
                        st.failed += 1
                        db.record_error(con, c.path, STAGE, "read failed", now)
                        continue
                    digest, nbytes = result
                    st.bytes_read += nbytes
                    hashed.append((c, digest))

                # --- which of these are assets we have never seen? ----------
                fresh = []
                for c, digest in hashed:
                    if db.get_asset_id(con, digest) is None:
                        fresh.append((c, digest))

                meta = et.batch([c.walk_path for c, _ in fresh]) if fresh else {}

                # --- write the whole chunk in one transaction ---------------
                con.execute("BEGIN")
                try:
                    for c, digest in hashed:
                        fid = folder_ids.get(c.folder)
                        if fid is None:
                            fp = dates.parse_folder(os.path.basename(c.folder), folder_pats)
                            fid = db.upsert_folder(con, c.folder, fp, now=now)
                            folder_ids[c.folder] = fid

                        asset_id = db.get_asset_id(con, digest)
                        if asset_id is None:
                            m = meta.get(c.walk_path, {})
                            asset_id = _create_asset(
                                con, c, digest, m, fn_pats, folder_pats, st, now)
                            st.new_assets += 1
                        else:
                            st.duplicates += 1

                        db.upsert_file(con, c.path, fid, asset_id,
                                       c.size, c.mtime, now)
                        st.new_files += 1
                    con.execute("COMMIT")
                except Exception:
                    con.execute("ROLLBACK")
                    raise

                if progress:
                    progress(st)
        finally:
            pool.shutdown(wait=True)
    finally:
        if ctx:
            ctx.__exit__(None, None, None)

    if not dry_run:
        st.missing = db.mark_missing(con, root_unc, seen_paths, now)
        con.commit()
    return st


def _safe_hash(path: str):
    try:
        return hash_file(path)
    except OSError:
        return None


def _create_asset(con, c: walker.Candidate, digest: str, m: dict,
                  fn_pats, folder_pats, st: Stats, now: int) -> int:
    fields = exif.to_fields(m) if m else {}

    exif_dt = dates.parse_exif_datetime(exif.best_datetime_tag(m)) if m else None
    stem = os.path.splitext(c.name)[0]
    fn = dates.parse_filename(stem, fn_pats)
    fp = dates.parse_folder(os.path.basename(c.folder), folder_pats)
    mtime_dt = datetime.fromtimestamp(c.mtime)

    r = dates.resolve(exif_dt, fn, fp, mtime_dt)

    # The archive's filename convention carries make and model too, which fills
    # the gap for the ~7% of files whose EXIF has neither (spec §5.1).
    if fn:
        fields.setdefault("camera_make", None)
        if not fields.get("camera_make") and fn.make:
            fields["camera_make"] = fn.make
        if not fields.get("camera_model") and fn.model:
            fields["camera_model"] = fn.model

    fields.update({
        "taken_at": int(r.when.timestamp()),
        "taken_at_source": r.source,
        "taken_at_precision": r.precision,
        "date_conflict": 1 if r.conflict else 0,
        "conflict_note": r.conflict_note,
        "source_hint": fn.hint if fn else None,
    })
    # A date somebody settled by hand outranks anything the file says, and has
    # to be re-read here because indexing is where the date is decided (P8).
    fixed = corrections.date_for(con, digest)
    if fixed:
        fields.update({"taken_at": fixed[0], "taken_at_precision": fixed[1],
                       "taken_at_source": "corrected", "date_conflict": 0,
                       "conflict_note": None})

    if not fields.get("format"):
        fields["format"] = os.path.splitext(c.name)[1].lstrip(".").lower()

    st.by_source[r.source] = st.by_source.get(r.source, 0) + 1
    if r.conflict:
        st.conflicts += 1

    asset_id = db.insert_asset(con, digest, fields)
    db.set_stage(con, asset_id, STAGE, STAGE_VERSION, "done", now)
    return asset_id


def render_progress(st: Stats, stream=sys.stderr) -> None:
    stream.write(
        f"\r  seen {st.seen:>7,}  new {st.new_files:>7,}  "
        f"assets {st.new_assets:>7,}  dup {st.duplicates:>6,}  "
        f"skip {st.skipped:>7,}  {st.mb_per_s:>6.1f} MB/s  "
        f"{st.elapsed:>5.0f}s"
    )
    stream.flush()


# --------------------------------------------------------------------------
# Re-reading metadata for assets that already exist
# --------------------------------------------------------------------------
#
# `scan` skips a file whose size and mtime are unchanged, and extracts metadata
# only for content it has never seen. That is what makes a re-scan cheap, and
# it also means a bumped STAGE_VERSION does nothing on its own: the files are
# unchanged, so nothing is re-read, and stage 1's version number was decorative
# until this existed.
#
# The cost is not the same cost. Hashing reads the whole file at ~3.2 files/s
# over this link; exiftool reads headers at ~37. So the repair is its own pass
# rather than a forced re-scan: 18 minutes over the whole library instead of
# three and a half hours, and no file is hashed to prove what is already known.

@dataclass
class MetaStats:
    considered: int = 0
    updated: int = 0
    moved: int = 0          # coordinates that actually changed
    failed: int = 0
    started: float = field(default_factory=time.time)

    @property
    def elapsed(self) -> float:
        return time.time() - self.started

    @property
    def rate(self) -> float:
        e = self.elapsed
        return self.updated / e if e > 0 else 0.0


# What a re-read is allowed to touch: the columns that come straight off the
# file. Dates are deliberately absent -- they are resolved from the filename and
# the folder as well as from EXIF (spec 5.1), and a metadata refresh has no
# business relitigating them.
REFRESHED = ("width", "height", "orientation", "gps_lat", "gps_lon",
             "camera_make", "camera_model", "lens", "iso", "f_number",
             "exposure_time", "focal_length")


def pending_meta(con, version: int = STAGE_VERSION,
                 limit: int | None = None) -> list[tuple[int, str]]:
    """(asset_id, path) for assets whose stage 1 row is behind the version."""
    sql = """
        SELECT a.id, MIN(f.path) AS path
        FROM assets a
        JOIN files f ON f.asset_id = a.id AND f.missing_since IS NULL
        LEFT JOIN stage_state s
               ON s.asset_id = a.id AND s.stage = ? AND s.version >= ?
        WHERE s.asset_id IS NULL
        GROUP BY a.id
        ORDER BY a.id
    """
    args: list = [STAGE, version]
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    return [(r["id"], r["path"]) for r in con.execute(sql, args)]


def refresh_meta(con, cfg, *, limit: int | None = None,
                 progress=None) -> MetaStats:
    exe = exif.find_exiftool(cfg.tools.get("exiftool", ""))
    if not exe:
        raise SystemExit("exiftool not found -- `mediaidx doctor`")

    st = MetaStats()
    todo = pending_meta(con, limit=limit)
    st.considered = len(todo)
    if not todo:
        return st

    now = int(time.time())
    chunk_size = int(cfg.scan.get("chunk_size", 256))
    with exif.Exif(exe) as et:
        for i in range(0, len(todo), chunk_size):
            chunk = todo[i:i + chunk_size]
            by_path = {pathutil.long_path(p): aid for aid, p in chunk}
            meta = et.batch(list(by_path))

            con.execute("BEGIN")
            try:
                for long, asset_id in by_path.items():
                    m = meta.get(long)
                    if not m:
                        st.failed += 1
                        db.set_stage(con, asset_id, STAGE, STAGE_VERSION,
                                     "failed", now, "metadata re-read failed")
                        continue
                    fields = exif.to_fields(m)
                    before = con.execute(
                        "SELECT gps_lat, gps_lon FROM assets WHERE id = ?",
                        (asset_id,)).fetchone()
                    # A value the file no longer carries is left alone rather
                    # than blanked: for ~7% of this library the camera make
                    # comes from the filename, not from EXIF (spec 5.1), and a
                    # re-read must not throw that away.
                    sets = {k: v for k, v in fields.items()
                            if k in REFRESHED and v is not None}
                    if sets:
                        con.execute(
                            "UPDATE assets SET "
                            + ",".join(f"{k}=?" for k in sets)
                            + " WHERE id = ?",
                            [*sets.values(), asset_id])
                    if (sets.get("gps_lat"), sets.get("gps_lon")) != \
                            (before["gps_lat"], before["gps_lon"]) and \
                            sets.get("gps_lat") is not None:
                        st.moved += 1
                    db.set_stage(con, asset_id, STAGE, STAGE_VERSION,
                                 "done", now)
                    st.updated += 1
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
            if progress:
                progress(st)
    con.commit()
    return st


def render_meta_progress(st: MetaStats, stream=None) -> None:
    stream = stream or sys.stderr
    stream.write(
        f"\r  re-read {st.updated:>7,} / {st.considered:,}   "
        f"moved {st.moved:>6,}   failed {st.failed:>4,}   "
        f"{st.rate:>5.1f} files/s   {st.elapsed:>5.0f}s")
    stream.flush()
