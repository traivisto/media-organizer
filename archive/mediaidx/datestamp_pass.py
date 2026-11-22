"""Orchestration for `datestamp.py`, over assets `dates.resolve` could not date.

Gate: an asset only qualifies once EXIF and the filename have both already
failed -- `taken_at_source` is `folder` or `mtime`. A real digital photograph
almost never reaches that state, so nothing here ever runs on one; a scan
with no camera stamp and no readable folder date is the common case that
does. The gate is this query, not a flag recorded anywhere -- the same fact
`dates.resolve` already computed answers it for free.

A `stage_state` row is written for every asset considered, whether or not a
date came out of it, so an unreadable scan is not retried on every run --
only `--rebuild`-style re-indexing (a version bump) asks again.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import datestamp, db
from . import paths as pathutil


@dataclass
class DateStampStats:
    considered: int = 0
    done: int = 0
    dated: int = 0
    dated_by_neighbor: int = 0
    no_stamp_found: int = 0
    disagreed: int = 0
    failed: int = 0
    started: float = field(default_factory=time.time)

    @property
    def elapsed(self) -> float:
        return time.time() - self.started

    @property
    def rate(self) -> float:
        e = self.elapsed
        return self.done / e if e > 0 else 0.0


def pending(con, version: int = datestamp.STAGE_VERSION,
           limit: int | None = None) -> list[tuple[int, str]]:
    """(asset_id, path) for undated assets not yet tried at this version."""
    sql = """
        SELECT a.id, MIN(f.path) AS path
        FROM assets a
        JOIN files f ON f.asset_id = a.id AND f.missing_since IS NULL
        LEFT JOIN stage_state s
               ON s.asset_id = a.id AND s.stage = ? AND s.version >= ?
        WHERE s.asset_id IS NULL
          AND a.taken_at_source IN ('folder', 'mtime')
        GROUP BY a.id
        ORDER BY a.id
    """
    args: list = [datestamp.STAGE, version]
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    return [(r["id"], r["path"]) for r in con.execute(sql, args)]


def run(con, cfg, *, limit: int | None = None, progress=None) -> DateStampStats:
    st = DateStampStats()
    todo = pending(con, limit=limit)
    st.considered = len(todo)
    if not todo:
        return st

    dc = cfg.datestamp
    locator = datestamp.Locator(conf=float(dc.get("conf_threshold", datestamp.DEFAULT_CONF)))
    models = tuple(dc.get("ollama_models", datestamp.DEFAULT_MODELS))
    now = int(time.time())

    con.execute("BEGIN")
    pending_writes = 0
    try:
        for asset_id, path in todo:
            long_path = pathutil.long_path(path)
            try:
                result = datestamp.read_stamp(long_path, locator, models=models)
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"[:200]
                st.failed += 1
                db.set_stage(con, asset_id, datestamp.STAGE, datestamp.STAGE_VERSION,
                            "failed", now, msg)
                db.record_error(con, path, datestamp.STAGE, msg, now)
                pending_writes += 1
                continue

            if result is None:
                st.no_stamp_found += 1
            elif result.agreed:
                # The guard on taken_at_source, not just the WHERE clause that
                # selected this asset: a correction may have landed on it
                # since `pending()` ran, and a hand-settled date always wins.
                cur = con.execute(
                    """UPDATE assets SET taken_at = ?, taken_at_source = 'camera_stamp',
                         taken_at_precision = 'day', date_conflict = 0, conflict_note = NULL
                       WHERE id = ? AND taken_at_source IN ('folder', 'mtime')""",
                    (int(result.date.timestamp()), asset_id))
                if cur.rowcount:
                    st.dated += 1
            else:
                # Two readers differed -- see if the neighbour tiebreak
                # (datestamp.resolve_disagreement) can still settle it. Assets
                # already written earlier in *this* run are visible here too
                # (same connection, same open transaction), so resolving them
                # in asset-id order lets one dated frame anchor the next.
                parsed = [datestamp.parse_groups(v) for v in result.raw.values()]
                nb = datestamp.neighbor_date(con, path)
                picked = datestamp.resolve_disagreement(parsed, nb)
                if picked is None:
                    st.disagreed += 1
                else:
                    note = (f"two readers disagreed ({result.raw}); kept the one "
                            f"closer to a neighbouring frame's date ({nb:%Y-%m-%d})")
                    cur = con.execute(
                        """UPDATE assets SET taken_at = ?, taken_at_source = 'camera_stamp',
                             taken_at_precision = 'day', date_conflict = 1, conflict_note = ?
                           WHERE id = ? AND taken_at_source IN ('folder', 'mtime')""",
                        (int(picked.timestamp()), note, asset_id))
                    if cur.rowcount:
                        st.dated_by_neighbor += 1
                    else:
                        st.disagreed += 1

            db.set_stage(con, asset_id, datestamp.STAGE, datestamp.STAGE_VERSION,
                        "done", now)
            st.done += 1
            pending_writes += 1

            if pending_writes >= 64:
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


def render_progress(st: DateStampStats, stream=None) -> None:
    import sys
    stream = stream or sys.stderr
    stream.write(
        f"\r  considered {st.done:>6,} / {st.considered:,}   "
        f"dated {st.dated:>5,}   +neighbour {st.dated_by_neighbor:>4,}   "
        f"no stamp {st.no_stamp_found:>5,}   "
        f"disagreed {st.disagreed:>4,}   failed {st.failed:>4,}   "
        f"{st.rate:>4.1f} img/s   {st.elapsed:>5.0f}s")
    stream.flush()
