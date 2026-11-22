"""Events: the stretch of time a run of photographs is about (spec 6.2).

Like places and clustering, this job reads the database and nothing else -- no
file is opened, no model runs, no network is touched -- so it can be re-run in
seconds whenever a threshold changes.

Three sources of evidence, in this order:

  folder  A folder somebody labelled (`2019 07 Alicante`) is authoritative. It
          is one event, it is already named, and it is never asked about.
  clock   Inside a bare month folder, a gap longer than four hours ends the
          event. This is what covers the twelve years of the archive that have
          no GPS at all.
  map     ...and so does a jump of more than 25 km, where both photographs
          carry a coordinate. Only the last decade has them (spec 1.1), so
          this refines the clock rather than replacing it.

Nothing here is stable across a run: every event is rebuilt from scratch, and
the ids are new each time. So nothing a person decided may be stored on an
event. A typed name lives in `event_names` and a split lives in `event_cuts`,
both keyed by a photograph -- which is a fact about the archive and survives
every rebuild, exactly as a rejected place is keyed by its path rather than by
a gazetteer id.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass, field

EARTH_KM = 6371.0

# How much of an event's placed photographs a place has to cover before the
# event is said to be there. An event is allowed to span towns -- a week in
# Spain is not "Alicante" -- so the answer is the deepest place containing
# this share of them, which for a trip is the region or the country.
PLACE_COVERAGE = 0.8

# Used where a group must not be split by time or distance at all, only where
# somebody has said to. Larger than the age of the archive and than the planet.
NEVER = 10 ** 12


@dataclass
class EventStats:
    events: int = 0
    from_folder: int = 0
    from_time: int = 0
    named: int = 0
    unnamed: int = 0
    assets: int = 0
    ungrouped: int = 0
    undated: int = 0
    started: float = field(default_factory=time.time)

    @property
    def elapsed(self) -> float:
        return time.time() - self.started


def _km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(h))


def _parent_path(path: str) -> str | None:
    cut = max(path.rfind("\\"), path.rfind("/"))
    return path[:cut] if cut > 0 else None


def label_owners(folders) -> tuple[dict, dict]:
    """folder id -> the nearest folder at or above it that carries a label.

    A photograph filed under `2001 01 Thaimaa\\skannatut` belongs to the
    Thailand trip as much as one filed directly in the labelled folder, so a
    label is inherited by everything beneath it -- the same rule places uses,
    for the same reason.
    """
    by_path = {r["path"]: r["id"] for r in folders}
    label = {r["id"]: r["label"] for r in folders}
    path_of = {r["id"]: r["path"] for r in folders}
    owner: dict[int, int | None] = {}

    def resolve(fid: int) -> int | None:
        if fid in owner:
            return owner[fid]
        owner[fid] = None                   # cycles cannot happen through real
        found = fid if label.get(fid) else None   # paths, but recursion over
        if found is None:                   # user-supplied ones shouldn't hang
            parent = _parent_path(path_of[fid])
            while parent is not None and parent not in by_path:
                parent = _parent_path(parent)
            if parent is not None:
                found = resolve(by_path[parent])
        owner[fid] = found
        return found

    for fid in path_of:
        resolve(fid)
    return owner, label


@dataclass
class Group:
    folder_id: int
    label: str | None
    assets: list          # (asset_id, taken_at, lat, lon), in time order

    @property
    def anchor(self) -> int:
        return self.assets[0][0]


def split_runs(assets, *, gap_s: int, gap_km: float, cuts: set) -> list[list]:
    """Break a run of photographs where the clock, the map or a person says to."""
    runs, cur, prev = [], [], None
    for a in assets:
        if prev is not None:
            here = a[0] in cuts or (a[1] - prev[1]) > gap_s
            if not here and a[2] is not None and prev[2] is not None:
                here = _km(prev[2], prev[3], a[2], a[3]) > gap_km
            if here:
                runs.append(cur)
                cur = []
        cur.append(a)
        prev = a
    if cur:
        runs.append(cur)
    return runs


def plan(con, cfg, st: EventStats | None = None) -> list[Group]:
    """The grouping, computed but not written. The whole of spec 6.2 is here."""
    st = st or EventStats()
    ev = getattr(cfg, "events", None) or {}
    gap_s = int(float(ev.get("time_gap_hours", 4)) * 3600)
    gap_km = float(ev.get("place_gap_km", 25))
    min_assets = int(ev.get("min_event_assets", 5))

    folders = con.execute(
        "SELECT id, path, COALESCE(user_label, parsed_label) AS label "
        "FROM folders").fetchall()
    owner, label_of = label_owners(folders)
    cuts = {r[0] for r in con.execute("SELECT asset_id FROM event_cuts")}

    # One row per asset: a duplicate filed in two folders belongs to the event
    # its earliest copy was found in, which is the tie-break places uses too.
    by_key: dict[tuple, list] = defaultdict(list)
    seen: set[int] = set()
    for aid, fid, taken, lat, lon in con.execute(
            """SELECT f.asset_id, f.folder_id, a.taken_at, a.gps_lat, a.gps_lon
                 FROM files f JOIN assets a ON a.id = f.asset_id
                WHERE f.missing_since IS NULL
                ORDER BY f.id"""):
        if aid in seen or fid is None or fid not in label_of:
            continue
        seen.add(aid)
        own = owner.get(fid)
        by_key[("folder", own) if own is not None else ("bare", fid)].append(
            (aid, taken, lat, lon))

    groups: list[Group] = []
    for (kind, fid), assets in sorted(by_key.items(), key=lambda kv: kv[0][1]):
        dated = [a for a in assets if a[1] is not None]
        st.undated += len(assets) - len(dated)
        if not dated:
            st.ungrouped += len(assets)
            continue
        dated.sort(key=lambda a: (a[1], a[0]))
        if kind == "folder":
            # A labelled folder is one event however few photographs are in
            # it: the minimum size exists to keep specks out of the review
            # queue, and a folder somebody named is never asked about. It
            # still splits where somebody has said it should.
            for run in split_runs(dated, gap_s=NEVER, gap_km=NEVER, cuts=cuts):
                groups.append(Group(fid, label_of[fid], run))
                st.from_folder += 1
        else:
            for run in split_runs(dated, gap_s=gap_s, gap_km=gap_km, cuts=cuts):
                if len(run) < min_assets:
                    st.ungrouped += len(run)
                    continue
                groups.append(Group(fid, None, run))
                st.from_time += 1
    return groups


# --------------------------------------------------------------------------
# where an event was
# --------------------------------------------------------------------------

def place_for(con, asset_ids) -> int | None:
    """The deepest place holding most of an event's placed photographs.

    An event may span towns, so the answer is not simply the commonest one: a
    week driving through Spain is in Spain, not in whichever village produced
    the most pictures. Paths are taken commonest first until 80% of the placed
    photographs are covered, and what those share is the answer -- for a single
    afternoon the town itself, for a road trip the country.
    """
    if not asset_ids:
        return None
    counts: dict[str, int] = defaultdict(int)
    for i in range(0, len(asset_ids), 900):
        batch = asset_ids[i:i + 900]
        for path, n in con.execute(
                "SELECT p.path, COUNT(*) FROM assets a JOIN places p ON p.id = a.place_id "
                f"WHERE a.id IN ({','.join('?' * len(batch))}) GROUP BY p.path", batch):
            counts[path] += n
    if not counts:
        return None
    want = PLACE_COVERAGE * sum(counts.values())
    taken, got = [], 0
    for path, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        taken.append(path.split("/"))
        got += n
        if got >= want:
            break
    common = taken[0]
    for segs in taken[1:]:
        keep = 0
        while keep < min(len(common), len(segs)) and common[keep] == segs[keep]:
            keep += 1
        common = common[:keep]
        if not common:
            return None
    row = con.execute("SELECT id FROM places WHERE path = ?",
                      ("/".join(common),)).fetchone()
    return row[0] if row else None


# --------------------------------------------------------------------------
# building
# --------------------------------------------------------------------------

def run(con, cfg, *, progress=None) -> EventStats:
    """Rebuild every event. Re-runnable at any time; decisions survive it."""
    st = EventStats()
    groups = plan(con, cfg, st)
    marks = {r["anchor_id"]: r for r in con.execute("SELECT * FROM event_names")}

    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM event_assets")
        con.execute("DELETE FROM events")
        for g in groups:
            ids = [a[0] for a in g.assets]
            mark = marks.get(g.anchor)
            typed = mark["name"] if mark else None
            name = typed or g.label
            source = "user" if typed else ("folder" if g.label else None)
            cur = con.execute(
                """INSERT INTO events (name, name_source, start_at, end_at,
                                       place_id, folder_id, anchor_id, asset_count)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (name, source, g.assets[0][1], g.assets[-1][1],
                 place_for(con, ids), g.folder_id, g.anchor, len(ids)))
            eid = cur.lastrowid
            con.executemany(
                "INSERT INTO event_assets (event_id, asset_id) VALUES (?,?)",
                [(eid, a) for a in ids])
            st.events += 1
            st.assets += len(ids)
            if name:
                st.named += 1
            else:
                st.unnamed += 1
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    if progress:
        progress(st)
    return st


# --------------------------------------------------------------------------
# what a person decides
# --------------------------------------------------------------------------

def _mark(con, anchor_id: int, *, name=..., dismissed=..., now=None) -> None:
    now = now or int(time.time())
    con.execute(
        "INSERT OR IGNORE INTO event_names (anchor_id, name, dismissed, created_at) "
        "VALUES (?,NULL,0,?)", (anchor_id, now))
    if name is not ...:
        con.execute("UPDATE event_names SET name = ? WHERE anchor_id = ?",
                    (name, anchor_id))
    if dismissed is not ...:
        con.execute("UPDATE event_names SET dismissed = ? WHERE anchor_id = ?",
                    (1 if dismissed else 0, anchor_id))


def set_name(con, event_id: int, name: str, *, now=None) -> None:
    """Name an event. Permanent, and survives every rebuild."""
    row = con.execute("SELECT anchor_id FROM events WHERE id = ?",
                      (event_id,)).fetchone()
    if row is None:
        raise ValueError(f"no event {event_id}")
    name = name.strip()
    _mark(con, row[0], name=name or None, dismissed=False, now=now)
    con.execute("UPDATE events SET name = ?, name_source = ? WHERE id = ?",
                (name or None, "user" if name else None, event_id))
    con.execute("DELETE FROM review_queue WHERE kind = 'event' AND ref_id = ?",
                (event_id,))
    con.commit()


def dismiss(con, event_id: int, *, now=None) -> None:
    """Never ask about this one again. It stays an event; it stays unnamed."""
    row = con.execute("SELECT anchor_id FROM events WHERE id = ?",
                      (event_id,)).fetchone()
    if row is None:
        raise ValueError(f"no event {event_id}")
    _mark(con, row[0], dismissed=True, now=now)
    con.execute("DELETE FROM review_queue WHERE kind = 'event' AND ref_id = ?",
                (event_id,))
    con.commit()


def is_dismissed(con, event_id: int) -> bool:
    row = con.execute(
        """SELECT n.dismissed FROM events e
             JOIN event_names n ON n.anchor_id = e.anchor_id
            WHERE e.id = ?""", (event_id,)).fetchone()
    return bool(row and row[0])


def split_point(con, event_id: int, cfg=None) -> int | None:
    """Where "this is not one event" would cut, or None if nowhere sensible.

    The cut goes at the longest interruption -- one per press, so the result is
    predictable and the button can simply be pressed again -- but only where it
    leaves a real event on both sides. Without that rule an event whose
    photographs are evenly spaced is cut after its first one, and the orphan
    then falls below the minimum size and drops out of the archive's events
    entirely: a button labelled "not one event" would be quietly deleting a
    photograph from the timeline.

    Asked before the button is drawn as well as when it is pressed, so an
    event too small to split is never offered the action.
    """
    ev = getattr(cfg, "events", None) or {}
    keep = max(1, int(ev.get("min_event_assets", 5)))
    rows = con.execute(
        """SELECT a.id, a.taken_at FROM event_assets e
             JOIN assets a ON a.id = e.asset_id
            WHERE e.event_id = ? AND a.taken_at IS NOT NULL
            ORDER BY a.taken_at, a.id""", (event_id,)).fetchall()
    if len(rows) < 2 * keep:
        return None
    widest, at = -1, None
    for i in range(keep, len(rows) - keep + 1):
        gap = rows[i][1] - rows[i - 1][1]
        if gap > widest:
            widest, at = gap, rows[i][0]
    return at


def split(con, event_id: int, cfg=None, *, now=None) -> int | None:
    """Record the cut. Permanent, survives a rebuild, and the only way a
    labelled folder ever becomes more than one event."""
    at = split_point(con, event_id, cfg)
    if at is None:
        return None
    con.execute("INSERT OR IGNORE INTO event_cuts (asset_id, created_at) VALUES (?,?)",
                (at, now or int(time.time())))
    con.commit()
    return at


# --------------------------------------------------------------------------
# reading it back
# --------------------------------------------------------------------------

def overview(con, *, limit: int = 500, unnamed_only: bool = False) -> list:
    where = "WHERE e.name IS NULL" if unnamed_only else ""
    return con.execute(
        f"""SELECT e.*, p.name AS place, p.path AS place_path,
                   (SELECT a.content_hash FROM event_assets ea
                      JOIN assets a ON a.id = ea.asset_id
                     WHERE ea.event_id = e.id ORDER BY a.taken_at LIMIT 1) AS cover
              FROM events e LEFT JOIN places p ON p.id = e.place_id
              {where}
             ORDER BY e.start_at DESC LIMIT ?""", (limit,)).fetchall()


def get(con, event_id: int):
    return con.execute(
        """SELECT e.*, p.name AS place, p.path AS place_path, f.path AS folder
             FROM events e
             LEFT JOIN places p ON p.id = e.place_id
             LEFT JOIN folders f ON f.id = e.folder_id
            WHERE e.id = ?""", (event_id,)).fetchone()


def assets_of(con, event_id: int, limit: int = 300) -> list:
    """A scan with no real date (taken_at_source 'mtime', dates.py) still
    joins the event its mtime happened to fall into -- the grouping itself
    is a separate, larger question (spec 6.2's gap rule runs on taken_at
    regardless of its source) -- but within the page it is filed last, same
    as everywhere else a photograph's date is shown (serve.py's _date_order).
    """
    return con.execute(
        """SELECT a.id, a.content_hash, a.taken_at, a.taken_at_precision,
                  a.taken_at_source, a.camera_model
             FROM event_assets ea JOIN assets a ON a.id = ea.asset_id
            WHERE ea.event_id = ?
            ORDER BY (a.taken_at_source = 'mtime') ASC, a.taken_at ASC LIMIT ?""",
        (event_id, limit)).fetchall()


def sample(con, event_id: int, limit: int = 12) -> list:
    """A spread across the event, not its first twelve minutes.

    An event of 700 photographs whose sample is all from one afternoon says
    nothing about what it is, and the review screen exists to be answered from
    the sample alone.
    """
    rows = con.execute(
        """SELECT a.id, a.content_hash, a.taken_at, a.taken_at_precision,
                  a.camera_model
             FROM event_assets ea JOIN assets a ON a.id = ea.asset_id
            WHERE ea.event_id = ? ORDER BY a.taken_at""", (event_id,)).fetchall()
    if len(rows) <= limit:
        return rows
    step = len(rows) / limit
    return [rows[int(i * step)] for i in range(limit)]


def for_asset(con, asset_id: int):
    return con.execute(
        """SELECT e.* FROM events e JOIN event_assets ea ON ea.event_id = e.id
            WHERE ea.asset_id = ? LIMIT 1""", (asset_id,)).fetchone()


def people_in(con, event_id: int) -> list:
    """Who is already known to be in it, commonest first."""
    return con.execute(
        """SELECT p.id, p.name, COUNT(DISTINCT f.asset_id) AS photos
             FROM event_assets ea
             JOIN faces f ON f.asset_id = ea.asset_id
             JOIN persons p ON p.id = f.person_id
            WHERE ea.event_id = ?
            GROUP BY p.id ORDER BY photos DESC""", (event_id,)).fetchall()


def matching(con, q: str, *, limit: int = 8) -> list:
    """Events whose name contains the query.

    The same reasoning as the people and places strips: an event name is not
    in the photograph, so no model can find it and the rows have to be asked
    directly. Matched in Python because SQLite's NOCASE folds ASCII and
    nothing else, and these names are Finnish.
    """
    needle = q.strip().casefold()
    if not needle:
        return []
    rows = con.execute(
        """SELECT e.id, e.name, e.start_at, e.asset_count,
                  (SELECT a.content_hash FROM event_assets ea
                     JOIN assets a ON a.id = ea.asset_id
                    WHERE ea.event_id = e.id ORDER BY a.taken_at LIMIT 1) AS cover
             FROM events e WHERE e.name IS NOT NULL
            ORDER BY e.asset_count DESC""").fetchall()
    return [r for r in rows if needle in r["name"].casefold()][:limit]
