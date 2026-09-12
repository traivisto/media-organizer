"""Everything done by hand, written out and read back (spec §8.1, P6, P8).

P6 says deleting the database and rebuilding must lose nothing except what a
person supplied, and P8 says that what a person supplied is never lost at all.
Both promises rest on this file existing: the database holds 19,512 named faces
and a hundred corrections that no amount of recomputation can recreate, and
until now there was nothing that could carry them out of it.

**Keyed by content hash throughout, never by row id.** That is the whole point:
the file has to be readable by a database built from nothing, where every
`assets.id`, `faces.id`, `places.id` and `events.id` is different or absent. A
face is found again by its index within its photograph and, failing that, by
where it sits in the frame -- the same anchor a re-detection uses (§5.2).

**More than names.** The spec's `export people` was written when names were the
only handmade thing. They are not any more: a correction, a rejected place, a
cut between two events, a photograph said to belong where it is -- each is an
answer somebody gave, and each is equally unrecoverable. All of it goes in one
file, because a backup that covers most of your work is a backup you cannot
rely on.
"""

from __future__ import annotations

import json
import time

VERSION = 1


def dump(con) -> dict:
    """Everything a person decided, in a form a fresh database can read."""
    faces = [
        {"hash": r["content_hash"], "idx": r["idx"],
         "bbox": json.loads(r["bbox"]), "name": r["name"]}
        for r in con.execute(
            """SELECT a.content_hash, f.idx, f.bbox, p.name
                 FROM faces f
                 JOIN assets a  ON a.id = f.asset_id
                 JOIN persons p ON p.id = f.person_id
                ORDER BY p.name, a.content_hash, f.idx""")]

    return {
        "version": VERSION,
        "exported_at": int(time.time()),
        # Every name, including anybody named but not yet on a face, so a
        # spelling somebody settled on is not lost with the last photograph.
        "persons": [r["name"] for r in con.execute(
            "SELECT name FROM persons ORDER BY name")],
        "faces": faces,
        "events": [
            {"hash": r["content_hash"], "name": r["name"],
             "dismissed": r["dismissed"]}
            for r in con.execute(
                """SELECT a.content_hash, n.name, n.dismissed
                     FROM event_names n JOIN assets a ON a.id = n.anchor_id
                    ORDER BY a.content_hash""")],
        "cuts": [r["content_hash"] for r in con.execute(
            """SELECT a.content_hash FROM event_cuts c
                 JOIN assets a ON a.id = c.asset_id ORDER BY a.content_hash""")],
        "places_rejected": [
            {"label": r["label_norm"], "path": r["place_path"]}
            for r in con.execute(
                "SELECT label_norm, place_path FROM place_exceptions "
                "ORDER BY label_norm")],
        "corrections": [
            {k: r[k] for k in
             ("kind", "content_hash", "face_idx", "face_bbox", "value", "note")}
            for r in con.execute(
                "SELECT * FROM corrections ORDER BY kind, content_hash")],
        "not_odd": [r["content_hash"] for r in con.execute(
            """SELECT a.content_hash FROM odd_dismissed d
                 JOIN assets a ON a.id = d.asset_id ORDER BY a.content_hash""")],
    }


def write(con, path) -> dict:
    """Write the export, and only replace the old one once it is complete.

    Through a temporary file beside it: a backup half-written over its own
    predecessor is worse than no backup, and this one lives in a syncing folder
    (§10.5) where a partial file would be copied away immediately.
    """
    import pathlib
    p = pathlib.Path(path)
    data = dump(con)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".part")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(p)
    return data


class Restored:
    """What came back, and what could not."""

    def __init__(self):
        self.persons = self.faces = self.events = self.cuts = 0
        self.places = self.corrections = self.not_odd = 0
        self.missing_assets = self.missing_faces = 0

    def __str__(self) -> str:
        return (f"{self.persons:,} people, {self.faces:,} faces, "
                f"{self.events:,} event names, {self.cuts:,} cuts, "
                f"{self.places:,} place rejections, "
                f"{self.corrections:,} corrections, {self.not_odd:,} kept")


def load(con, data: dict) -> Restored:
    """Read an export back into a database, which may be a fresh one.

    Photographs absent from this database are counted and skipped rather than
    refused: an export made from the whole archive is still worth restoring into
    a machine holding half of it, and the entries for what is missing are left
    in the file for the machine that does have them.
    """
    if data.get("version") != VERSION:
        raise ValueError(f"unknown export version: {data.get('version')!r}")
    from . import corrections as corrections_mod

    st = Restored()
    now = int(time.time())

    for name in data.get("persons", []):
        cur = con.execute(
            "INSERT OR IGNORE INTO persons (name, source, created_at) "
            "VALUES (?, 'manual', ?)", (name, now))
        st.persons += cur.rowcount

    ids = {r["content_hash"]: r["id"] for r in
           con.execute("SELECT id, content_hash FROM assets")}

    for f in data.get("faces", []):
        aid = ids.get(f["hash"])
        if aid is None:
            st.missing_assets += 1
            continue
        pid = con.execute("SELECT id FROM persons WHERE name = ?",
                          (f["name"],)).fetchone()
        if pid is None:
            continue
        got = _face_at(con, aid, f["idx"], f.get("bbox"))
        if got is None:
            st.missing_faces += 1
            continue
        con.execute("UPDATE faces SET person_id = ? WHERE id = ?",
                    (pid["id"], got))
        st.faces += 1

    for e in data.get("events", []):
        aid = ids.get(e["hash"])
        if aid is None:
            st.missing_assets += 1
            continue
        con.execute(
            """INSERT INTO event_names (anchor_id, name, dismissed, created_at)
               VALUES (?,?,?,?) ON CONFLICT(anchor_id) DO UPDATE SET
                 name=excluded.name, dismissed=excluded.dismissed""",
            (aid, e.get("name"), e.get("dismissed", 0), now))
        st.events += 1

    for h in data.get("cuts", []):
        aid = ids.get(h)
        if aid is None:
            st.missing_assets += 1
            continue
        con.execute("INSERT OR IGNORE INTO event_cuts (asset_id, created_at) "
                    "VALUES (?,?)", (aid, now))
        st.cuts += 1

    for r in data.get("places_rejected", []):
        con.execute(
            "INSERT OR IGNORE INTO place_exceptions "
            "(label_norm, place_path, created_at) VALUES (?,?,?)",
            (r["label"], r["path"], now))
        st.places += 1

    for c in data.get("corrections", []):
        bbox = json.loads(c["face_bbox"]) if c.get("face_bbox") else None
        corrections_mod.record(
            con, c["kind"], c["content_hash"], c.get("value"),
            face_idx=c.get("face_idx", corrections_mod.NO_FACE),
            face_bbox=bbox, note=c.get("note"), now=now)
        st.corrections += 1

    for h in data.get("not_odd", []):
        aid = ids.get(h)
        if aid is None:
            st.missing_assets += 1
            continue
        con.execute("INSERT OR IGNORE INTO odd_dismissed (asset_id, created_at) "
                    "VALUES (?,?)", (aid, now))
        st.not_odd += 1

    return st


def read(con, path) -> Restored:
    import pathlib
    return load(con, json.loads(pathlib.Path(path).read_text(encoding="utf-8")))


def _face_at(con, asset_id: int, idx: int, bbox):
    """The face this entry means, by index or by where it sits in the frame.

    A database rebuilt from nothing has detected its faces again, and `idx` is
    only the detector's output order (§5.2). So the index is tried first and the
    box decides when it disagrees -- the same anchor the re-detection itself
    uses, and the reason the box is in the file at all.
    """
    row = con.execute("SELECT id, bbox FROM faces WHERE asset_id = ? AND idx = ?",
                      (asset_id, idx)).fetchone()
    if row is not None and bbox:
        from . import corrections as corrections_mod
        if corrections_mod.bbox_iou(json.loads(row["bbox"]), bbox) >= \
                corrections_mod.MIN_IOU:
            return row["id"]
    elif row is not None:
        return row["id"]

    if not bbox:
        return None
    from . import corrections as corrections_mod
    best, best_iou = None, corrections_mod.MIN_IOU
    for r in con.execute("SELECT id, bbox FROM faces WHERE asset_id = ?",
                         (asset_id,)):
        got = corrections_mod.bbox_iou(json.loads(r["bbox"]), bbox)
        if got >= best_iou:
            best, best_iou = r["id"], got
    return best
