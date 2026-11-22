"""Stage 2 over already-indexed assets.

Same shape as the stage 3 pass, and for the same reason: the GPU detects and
embeds at ~22 images/second while the link delivers ~3.2. Reader threads are
the whole game; inference runs on one thread and is never the thing waiting.

What this pass writes is deliberately more than the face rows. The face crops
go to the thumbnail cache as they are found, because clustering and the review
UI that follow run entirely over the database -- but only if the pictures they
need to show are already local. Otherwise naming clusters means reading the
archive a second time, which at this link speed is another three hours.
"""

from __future__ import annotations

import itertools
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import db, faces, images
from . import paths as pathutil

COMMIT_EVERY = 64      # chunk granularity for resumability, as in stage 1


def rss_mb() -> float:
    """Resident memory, so a growing backlog is visible while it grows."""
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        # Without these, GetCurrentProcess is assumed to return a C int, the
        # pseudo-handle is mangled on 64-bit, and the call quietly fails --
        # which is exactly how this returned a confident 0 MB the first time.
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

        c = Counters()
        c.cb = ctypes.sizeof(c)
        if not psapi.GetProcessMemoryInfo(
                kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return 0.0
        return c.WorkingSetSize / 1e6
    except Exception:
        return 0.0


@dataclass
class FaceStats:
    considered: int = 0
    done: int = 0
    failed: int = 0
    faces: int = 0
    with_faces: int = 0
    thumbs: int = 0
    started: float = field(default_factory=time.time)

    @property
    def elapsed(self) -> float:
        return time.time() - self.started

    @property
    def rate(self) -> float:
        e = self.elapsed
        return self.done / e if e > 0 else 0.0


def pending(con, version: int, limit: int | None = None) -> list[tuple[int, str, str]]:
    """Assets with no current `faces` stage row, newest first."""
    sql = """
        SELECT a.id, a.content_hash, f.path
        FROM assets a
        JOIN files f ON f.asset_id = a.id AND f.missing_since IS NULL
        LEFT JOIN stage_state s
               ON s.asset_id = a.id AND s.stage = ? AND s.version >= ?
        WHERE s.asset_id IS NULL
        GROUP BY a.id
        ORDER BY a.taken_at DESC
    """
    args: list = [faces.STAGE, version]
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    return [(r["id"], r["content_hash"], r["path"]) for r in con.execute(sql, args)]


def _read_one(item, cache_dir: Path, want_thumbs: bool):
    """Read, orient and downscale. Runs in a reader thread."""
    asset_id, content_hash, path = item
    try:
        im = images.load(pathutil.long_path(path), max_side=1600)
    except Exception as e:
        return asset_id, content_hash, path, None, False, f"{type(e).__name__}: {e}"[:200]

    wrote = False
    if want_thumbs and not images.has_thumbs(cache_dir, content_hash):
        try:
            images.write_thumbs(im, cache_dir, content_hash)
            wrote = True
        except Exception:
            pass                        # a missing thumbnail is not a failed stage
    return asset_id, content_hash, path, im, wrote, None


def run(con, cfg, *, limit: int | None = None, threads: int = 8,
        progress=None, device: str = "cuda") -> FaceStats:
    st = FaceStats()
    todo = pending(con, faces.STAGE_VERSION, limit)
    st.considered = len(todo)
    if not todo:
        return st

    fc = cfg.faces
    detector = faces.Detector(
        model_name=cfg.models.get("face", faces.DEFAULT_MODEL),
        det_size=int(fc.get("det_size", faces.DET_SIZE)),
        min_det_score=float(fc.get("min_det_score", faces.MIN_DET_SCORE)),
        min_face_px=int(fc.get("min_face_px", faces.MIN_FACE_PX)),
        device=device,
    )
    vec_ok = faces.ensure_vec_table(con)
    cache_dir = Path(cfg.thumbnails)
    want_crops = bool(fc.get("face_thumbnails", True))
    now = int(time.time())

    pending_writes = 0

    def commit():
        nonlocal pending_writes
        if pending_writes:
            con.execute("COMMIT")
            pending_writes = 0
            con.execute("BEGIN")

    with ThreadPoolExecutor(max_workers=threads) as pool:
        # Bounded in-flight work, and the reason this is not `pool.map(...)`
        # over the whole list: map submits every read immediately and holds
        # each decoded image -- about 5.8 MB at 1600 px -- until the single
        # consumer reaches it. Here the consumer detects faces *and* encodes a
        # crop per face, so the eight readers outrun it, and the backlog has no
        # ceiling. It reached several gigabytes and the process died of it
        # after 6,720 images. A window keeps the readers fed and memory flat.
        window = max(threads * 4, 16)
        stream = iter(todo)
        inflight = deque(
            pool.submit(_read_one, item, cache_dir, True)
            for item in itertools.islice(stream, window))

        con.execute("BEGIN")
        try:
            while inflight:
                asset_id, content_hash, path, im, wrote, err = \
                    inflight.popleft().result()
                nxt = next(stream, None)
                if nxt is not None:
                    inflight.append(pool.submit(_read_one, nxt, cache_dir, True))

                if wrote:
                    st.thumbs += 1
                if err:
                    st.failed += 1
                    db.set_stage(con, asset_id, faces.STAGE, faces.STAGE_VERSION,
                                 "failed", now, err)
                    db.record_error(con, path, faces.STAGE, err, now)
                    pending_writes += 1
                    continue

                try:
                    found = detector.detect(im)
                except Exception as e:
                    st.failed += 1
                    msg = f"{type(e).__name__}: {e}"[:200]
                    db.set_stage(con, asset_id, faces.STAGE, faces.STAGE_VERSION,
                                 "failed", now, msg)
                    db.record_error(con, path, faces.STAGE, msg, now)
                    pending_writes += 1
                    continue

                faces.replace_for_asset(con, asset_id, found, vec_ok=vec_ok)
                if want_crops:
                    for idx, f in enumerate(found):
                        try:
                            images.write_face_thumb(im, cache_dir, content_hash,
                                                    idx, f.bbox)
                        except Exception:
                            pass        # as with thumbnails: not a failed stage
                db.set_stage(con, asset_id, faces.STAGE, faces.STAGE_VERSION,
                             "done", now)

                st.done += 1
                st.faces += len(found)
                st.with_faces += 1 if found else 0
                pending_writes += 1
                if pending_writes >= COMMIT_EVERY:
                    commit()
                    if progress:
                        progress(st)
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise

    return st


def render_progress(st: FaceStats, stream=None) -> None:
    import sys
    stream = stream or sys.stderr
    # Memory is on the progress line because this pass has already died once of
    # a backlog that nothing was watching. A flat number here is the evidence
    # that the reader window is holding.
    stream.write(
        f"\r  scanned {st.done:>7,} / {st.considered:,}   "
        f"faces {st.faces:>7,}   failed {st.failed:>4,}   "
        f"{st.rate:>5.1f} img/s   {rss_mb():>6,.0f} MB   {st.elapsed:>5.0f}s")
    stream.flush()
