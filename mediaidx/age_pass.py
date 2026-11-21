"""A light pass over already-detected faces: age and gender from the genderage
head of buffalo_l, read from the cached thumbnail rather than the archive.

This is not stage 2 again. It touches no photograph on the NAS and runs no
detector -- see `faces.AgeEstimator` for why. A face whose thumbnail was never
written (thumbnails off, or the crop failed) is left alone rather than forcing
a re-read; it can be filled in once a normal `faces` run writes the thumbnail.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import faces, images

COMMIT_EVERY = 256


@dataclass
class AgeStats:
    considered: int = 0
    done: int = 0
    missing_thumb: int = 0
    failed: int = 0
    started: float = field(default_factory=time.time)

    @property
    def elapsed(self) -> float:
        return time.time() - self.started

    @property
    def rate(self) -> float:
        e = self.elapsed
        return self.done / e if e > 0 else 0.0


def pending(con, limit: int | None = None) -> list[tuple[int, str, int]]:
    """(face_id, content_hash, idx) for faces with no age yet."""
    sql = """
        SELECT f.id, a.content_hash, f.idx
        FROM faces f
        JOIN assets a ON a.id = f.asset_id
        WHERE f.age IS NULL
        ORDER BY f.id
    """
    args: list = []
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    return [(r["id"], r["content_hash"], r["idx"]) for r in con.execute(sql, args)]


def run(con, cfg, *, limit: int | None = None,
        progress=None, device: str = "cuda") -> AgeStats:
    st = AgeStats()
    todo = pending(con, limit)
    st.considered = len(todo)
    if not todo:
        return st

    estimator = faces.AgeEstimator(
        model_name=cfg.models.get("face", faces.DEFAULT_MODEL), device=device)
    cache_dir = Path(cfg.thumbnails)

    con.execute("BEGIN")
    pending_writes = 0
    try:
        for face_id, content_hash, idx in todo:
            thumb_path = images.face_thumb_path(cache_dir, content_hash, idx)
            if not thumb_path.exists():
                st.missing_thumb += 1
                continue

            try:
                with Image.open(thumb_path) as im:
                    result = estimator.estimate(im)
            except Exception:
                result = None

            if result is None:
                st.failed += 1
                continue

            gender, age = result
            con.execute("UPDATE faces SET age = ?, gender = ? WHERE id = ?",
                        (age, gender, face_id))
            st.done += 1
            pending_writes += 1

            if pending_writes >= COMMIT_EVERY:
                con.execute("COMMIT")
                pending_writes = 0
                con.execute("BEGIN")
                if progress:
                    progress(st)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise

    if progress:
        progress(st)
    return st


def render_progress(st: AgeStats, stream=None) -> None:
    import sys
    stream = stream or sys.stderr
    stream.write(
        f"\r  aged {st.done:>7,} / {st.considered:,}   "
        f"no thumbnail {st.missing_thumb:>5,}   failed {st.failed:>4,}   "
        f"{st.rate:>5.1f} faces/s   {st.elapsed:>5.0f}s")
    stream.flush()
