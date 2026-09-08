"""Stage 3 over already-indexed assets, plus thumbnails.

Ideally this runs in the same pass as stage 1, so a file crosses the network
once (P4). It is a separate command as well, for assets indexed before M2
existed -- which is the whole 2019 folder, and would be the whole library had
we run the full index before building this.

The GPU is not the constraint here today: it encodes at ~47 img/s against the
~3.2 img/s the 100 Mbps link delivers. Everything below is arranged around
keeping the reader threads busy, not the GPU -- but not on the assumption that
they can never get ahead, because fixing the link (spec 1.2) is the point and
would raise reads to ~32 img/s against a consumer that also writes two
thumbnails per image. Hence the bounded reader window in `run`.
"""

from __future__ import annotations

import itertools
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import db, embed, images
from . import paths as pathutil
from .face_pass import rss_mb


@dataclass
class EmbedStats:
    considered: int = 0
    done: int = 0
    skipped: int = 0
    failed: int = 0
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
    """Assets with no current `embed` stage row, newest first.

    Newest first because those are the photographs someone is most likely to
    search for while the rest of the library is still being processed.
    """
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
    args: list = [embed.STAGE, version]
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    return [(r["id"], r["content_hash"], r["path"]) for r in con.execute(sql, args)]


def _prepare_one(item, encoder, cache_dir: Path):
    """Read, orient, embed-preprocess and write thumbnails. Runs in a thread."""
    asset_id, content_hash, path = item
    try:
        im = images.load(pathutil.long_path(path), max_side=2048)
    except Exception as e:
        return asset_id, None, False, f"{type(e).__name__}: {e}"[:200]

    wrote = False
    if not images.has_thumbs(cache_dir, content_hash):
        try:
            images.write_thumbs(im, cache_dir, content_hash)
            wrote = True
        except Exception:
            pass                        # a missing thumbnail is not a failed stage
    try:
        tensor = encoder.prepare(im)
    except Exception as e:
        return asset_id, None, wrote, f"{type(e).__name__}: {e}"[:200]
    return asset_id, tensor, wrote, None


def run(con, cfg, *, limit: int | None = None, threads: int = 8,
        gpu_batch: int = 32, progress=None) -> EmbedStats:
    st = EmbedStats()
    todo = pending(con, embed.STAGE_VERSION, limit)
    st.considered = len(todo)
    if not todo:
        return st

    encoder = embed.Encoder(
        model_name=cfg.scan.get("embed_model", embed.DEFAULT_MODEL),
        pretrained=cfg.scan.get("embed_pretrained", embed.DEFAULT_PRETRAINED),
    )
    vec_ok = embed.ensure_vec_table(con)
    cache_dir = Path(cfg.thumbnails)
    now = int(time.time())

    with ThreadPoolExecutor(max_workers=threads) as pool:
        batch_ids: list[int] = []
        batch_tensors: list = []

        def flush():
            if not batch_ids:
                return
            vecs = encoder.encode_prepared(batch_tensors)
            con.execute("BEGIN")
            try:
                for aid, vec in zip(batch_ids, vecs):
                    embed.store(con, aid, encoder.tag, vec, vec_ok=vec_ok)
                    db.set_stage(con, aid, embed.STAGE, embed.STAGE_VERSION,
                                 "done", now)
                    st.done += 1
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
            batch_ids.clear()
            batch_tensors.clear()
            if progress:
                progress(st)

        # Bounded in-flight work, and the reason this is not `pool.map(...)`
        # over the whole list: map submits every read immediately and holds
        # each result -- a decoded 2048 px image's preprocessed tensor -- until
        # the single consumer reaches it, with no ceiling on the backlog. That
        # is what killed the stage 2 pass. Here the margin is currently wide
        # (the GPU encodes at ~47 img/s against ~3.2 img/s of reading), but it
        # is a margin the link speed happens to give us, not one this loop
        # earns: the consumer also writes two thumbnails per image, and at
        # gigabit the readers would deliver ~32 img/s into it. A window keeps
        # the readers fed either way and memory flat.
        window = max(threads * 4, 16)
        stream = iter(todo)
        inflight = deque(
            pool.submit(_prepare_one, item, encoder, cache_dir)
            for item in itertools.islice(stream, window))

        while inflight:
            asset_id, tensor, wrote, err = inflight.popleft().result()
            nxt = next(stream, None)
            if nxt is not None:
                inflight.append(
                    pool.submit(_prepare_one, nxt, encoder, cache_dir))

            if wrote:
                st.thumbs += 1
            if err:
                st.failed += 1
                db.set_stage(con, asset_id, embed.STAGE, embed.STAGE_VERSION,
                             "failed", now, err)
                continue
            batch_ids.append(asset_id)
            batch_tensors.append(tensor)
            if len(batch_ids) >= gpu_batch:
                flush()
        flush()

    con.commit()
    return st


def render_progress(st: EmbedStats, stream=None) -> None:
    import sys
    stream = stream or sys.stderr
    # Memory is on the line for the same reason as in stage 2: it is the
    # one number that shows the reader window holding rather than merely
    # being outrun by a slow link.
    stream.write(
        f"\r  embedded {st.done:>7,} / {st.considered:,}   "
        f"thumbs {st.thumbs:>7,}   failed {st.failed:>4,}   "
        f"{st.rate:>5.1f} img/s   {rss_mb():>6,.0f} MB   {st.elapsed:>5.0f}s")
    stream.flush()
