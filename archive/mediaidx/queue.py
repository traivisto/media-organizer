"""The review queue: the questions worth asking, largest first (spec 6.4).

One row per thing that has no name and is big enough to be worth naming --
an event, or a cluster of faces -- with `priority` set to the number of
photographs the answer would label. So the first question is always the one
that covers the most of the archive.

**Why there is a floor at all.** The spec expects 400-600 events and 150-250
questions. Measured on the reference library the grouping produces 1,374
events, of which 1,209 carry no folder label -- because 28 years of ordinary
photography really is that many separate afternoons, and no threshold makes it
fewer (raising the time gap to eight hours *increases* the count, by merging
small runs over the minimum size). Asking 1,209 questions is not a finite
queue, it is a second job. So promotion to an event and promotion to a
question are separated: everything of five photographs or more is an event and
appears in the timeline, and only the substantial ones are asked about. The
floors are `queue_min_assets` and `queue_min_faces`; setting them to
`min_event_assets` and `cluster_min_size` restores the literal specification.

Nothing is decided here. A suggestion is offered with the evidence for it, and
the answer is always somebody typing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

from . import cluster as cluster_mod
from . import events as events_mod

# A person has to be in this share of an event's photographs before their name
# is offered as part of its name. Below it they were present; above it the
# event is arguably about them.
PERSON_SHARE = 0.4

# And a place has to hold this share of them. The event's own place is decided
# among the photographs that *have* a coordinate (events.place_for), which on
# this archive can be a fifth of them: measured, 20 of the 103 queued events
# with a place had under 20% placed, and "New York County" proposed on the
# strength of 27 photographs out of 124 is a guess wearing a fact's clothes.
# The place is still shown among the event's facts; it is only kept out of the
# proposed *name*.
PLACE_SHARE = 0.4


@dataclass
class QueueStats:
    events: int = 0
    clusters: int = 0
    suggested: int = 0
    photographs: int = 0
    started: float = field(default_factory=time.time)

    @property
    def total(self) -> int:
        return self.events + self.clusters

    @property
    def elapsed(self) -> float:
        return time.time() - self.started


def _when(start: int, end: int) -> str:
    a, b = datetime.fromtimestamp(start), datetime.fromtimestamp(end)
    if a.date() == b.date():
        return f"{a.day} {a:%B %Y}"
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}-{b.day} {a:%B %Y}"
    if a.year == b.year:
        return f"{a:%B}-{b:%B %Y}"
    return f"{a:%B %Y} - {b:%B %Y}"


def suggest_event(con, event) -> tuple[str, str] | None:
    """A name proposed from who is in it and where it was -- never applied.

    Captions would be the third source and are M5; until then the two things
    the database already knows are the people whose faces have been named and
    the place the coordinates or the folder gave. Both are stated as counts in
    the rationale, because a proposal nobody can check is worse than none.
    """
    n = event["asset_count"]
    people = [p for p in events_mod.people_in(con, event["id"])
              if n and p["photos"] / n >= PERSON_SHARE][:2]
    place = event["place"] if "place" in event.keys() else None
    if not place:
        row = con.execute("SELECT name FROM places WHERE id = ?",
                          (event["place_id"],)).fetchone() if event["place_id"] else None
        place = row[0] if row else None

    placed = by_gps = 0
    if place:
        placed, by_gps = con.execute(
            """SELECT COUNT(a.place_id), COALESCE(SUM(a.place_source = 'gps'), 0)
                 FROM event_assets ea JOIN assets a ON a.id = ea.asset_id
                WHERE ea.event_id = ?""", (event["id"],)).fetchone()
        if not n or placed / n < PLACE_SHARE:
            place = None

    who = " and ".join(p["name"] for p in people)
    when = _when(event["start_at"], event["end_at"])
    if who and place:
        name = f"{who} in {place}"
    elif place:
        name = f"{place}, {when}"
    elif who:
        name = f"{who}, {when}"
    else:
        return None

    why = [f'{p["name"]} in {p["photos"]} of {n} photographs' for p in people]
    if place:
        how = "by coordinate" if by_gps >= placed - by_gps else "from the folder name"
        why.append(f"{placed} of {n} placed in {place} {how}")
    return name, "; ".join(why)


def build(con, cfg) -> QueueStats:
    """Rebuild the queue. Skipped items return, which is the point of skipping."""
    st = QueueStats()
    ev = getattr(cfg, "events", None) or {}
    min_assets = int(ev.get("queue_min_assets", 20))
    min_faces = int(ev.get("queue_min_faces", 10))

    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM review_queue")
        rows = con.execute(
            """SELECT e.*, p.name AS place FROM events e
                 LEFT JOIN places p ON p.id = e.place_id
                 LEFT JOIN event_names n ON n.anchor_id = e.anchor_id
                WHERE e.name IS NULL AND e.asset_count >= ?
                  AND COALESCE(n.dismissed, 0) = 0
                ORDER BY e.asset_count DESC""", (min_assets,)).fetchall()
        for r in rows:
            proposal = suggest_event(con, r)
            con.execute(
                """INSERT INTO review_queue (kind, ref_id, priority, suggestion,
                                             rationale, status)
                   VALUES ('event',?,?,?,?,'open')""",
                (r["id"], r["asset_count"],
                 proposal[0] if proposal else None,
                 proposal[1] if proposal else None))
            st.events += 1
            st.photographs += r["asset_count"]
            if proposal:
                st.suggested += 1

        clusters = con.execute(
            """SELECT c.id, c.size, COUNT(DISTINCT f.asset_id) AS photos
                 FROM face_clusters c JOIN faces f ON f.cluster_id = c.id
                WHERE c.person_id IS NULL AND c.status != 'ignored' AND c.size >= ?
                GROUP BY c.id ORDER BY photos DESC""", (min_faces,)).fetchall()
        for c in clusters:
            proposal = cluster_mod.suggest(con, c["id"])
            con.execute(
                """INSERT INTO review_queue (kind, ref_id, priority, suggestion,
                                             rationale, status)
                   VALUES ('face_cluster',?,?,?,?,'open')""",
                (c["id"], c["photos"],
                 proposal[0] if proposal else None,
                 proposal[1] if proposal else None))
            st.clusters += 1
            st.photographs += c["photos"]
            if proposal:
                st.suggested += 1
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return st


# --------------------------------------------------------------------------
# working through it
# --------------------------------------------------------------------------

def next_item(con):
    """The largest open question. Skipped ones wait for the next derive run."""
    return con.execute(
        "SELECT * FROM review_queue WHERE status = 'open' "
        "ORDER BY priority DESC, id LIMIT 1").fetchone()


def get(con, queue_id: int):
    return con.execute("SELECT * FROM review_queue WHERE id = ?",
                       (queue_id,)).fetchone()


def skip(con, queue_id: int, *, now=None) -> None:
    """Put it aside. It comes back after the next derive, when the surrounding
    events and faces have names and the question is easier."""
    con.execute("UPDATE review_queue SET status = 'skipped', skipped_at = ? "
                "WHERE id = ?", (now or int(time.time()), queue_id))
    con.commit()


def drop(con, queue_id: int) -> None:
    con.execute("DELETE FROM review_queue WHERE id = ?", (queue_id,))
    con.commit()


def counts(con) -> dict:
    row = con.execute(
        """SELECT COUNT(*) AS total,
                  SUM(status = 'open') AS open,
                  SUM(status = 'skipped') AS skipped,
                  SUM(status = 'open' AND kind = 'event') AS events,
                  SUM(status = 'open' AND kind = 'face_cluster') AS clusters,
                  COALESCE(SUM(CASE WHEN status = 'open' THEN priority END), 0) AS photographs
             FROM review_queue""").fetchone()
    return {k: (row[k] or 0) for k in row.keys()}
