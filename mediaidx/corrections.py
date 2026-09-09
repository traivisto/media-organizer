"""What a person put right by hand (spec §4.7).

Everything the application derives, it rebuilds: `derive` throws away places,
events and the queue and works them out again, and `faces cluster --rebuild`
does the same for identities. A correction therefore cannot be an edit of those
tables, because the next rebuild would quietly undo it. It is an *input*, read
at the point each value is computed -- the same rule that already governs a
rejected folder-place match (§6.3), generalised.

That rule is why there is no `apply()` here to stamp corrections over the
derived tables afterwards. Such a pass is only correct if every path runs it,
and `mediaidx places` recomputes `place_id` for the whole library outside
`derive` -- with `mediaidx meta` printing advice to do exactly that. A
correction whose survival depends on the order commands were run in is not a
correction (P8).

Three kinds:

    date    assets.taken_at, where EXIF, folder and filename disagree
    place   assets.place_id for one photograph
    person  faces.person_id for one face

Values are stored as the durable form of the answer, never as a row id: a place
by its path, a person by their name, a date by the text that was typed. Ids are
handed out while a gazetteer is loaded or a stage is re-run, and every one of
them changes when that happens.
"""

from __future__ import annotations

import json
import time

NO_FACE = -1                      # `face_idx` for the kinds that are not a face
KINDS = ("date", "place", "person")


def record(con, kind: str, content_hash: str, value, *, face_idx: int = NO_FACE,
        face_bbox=None, note: str | None = None, now: int | None = None) -> None:
    """Record one correction, replacing any earlier answer to the same question.

    `value=None` is not "no correction" but "no value is right" -- a photograph
    whose date is unknowable, or a face that is nobody. Use `clear` to withdraw
    a correction instead.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown correction: {kind}")
    con.execute(
        """INSERT INTO corrections
             (kind, content_hash, face_idx, face_bbox, value, note, created_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(kind, content_hash, face_idx) DO UPDATE SET
             value=excluded.value, note=excluded.note,
             face_bbox=excluded.face_bbox, created_at=excluded.created_at""",
        (kind, content_hash, face_idx,
         json.dumps([round(float(v), 6) for v in face_bbox]) if face_bbox else None,
         value, note, int(time.time()) if now is None else now))


def clear(con, kind: str, content_hash: str, *, face_idx: int = NO_FACE) -> bool:
    """Withdraw a correction, leaving the application to decide again."""
    cur = con.execute(
        "DELETE FROM corrections WHERE kind = ? AND content_hash = ? "
        "AND face_idx = ?", (kind, content_hash, face_idx))
    return cur.rowcount > 0


def for_asset(con, content_hash: str) -> list:
    """Every correction made about one photograph."""
    return list(con.execute(
        "SELECT * FROM corrections WHERE content_hash = ? "
        "ORDER BY kind, face_idx", (content_hash,)))


def of_kind(con, kind: str) -> dict:
    """`{content_hash: row}` for the kinds that are one-per-photograph."""
    return {r["content_hash"]: r for r in con.execute(
        "SELECT * FROM corrections WHERE kind = ? AND face_idx = ?",
        (kind, NO_FACE))}


def faces_by_asset(con) -> dict:
    """`{asset_id: {face_idx: row}}` for every `person` correction.

    Joined through the content hash, because that is what a correction is keyed
    by and `assets.id` is not what it means (§4.7).
    """
    out: dict = {}
    for r in con.execute(
            """SELECT a.id AS asset_id, c.* FROM corrections c
                 JOIN assets a ON a.content_hash = c.content_hash
                WHERE c.kind = 'person'"""):
        out.setdefault(r["asset_id"], {})[r["face_idx"]] = r
    return out


def corrected_face_ids(con) -> dict:
    """`{faces.id: person_id}` for every face a person has been named on.

    Resolved from name to id here rather than stored that way, so a correction
    survives the persons table being rebuilt from an export (P6).
    """
    out: dict = {}
    for r in con.execute(
            """SELECT f.id AS face_id, p.id AS person_id
                 FROM corrections c
                 JOIN assets a ON a.content_hash = c.content_hash
                 JOIN faces  f ON f.asset_id = a.id AND f.idx = c.face_idx
                 LEFT JOIN persons p ON p.name = c.value
                WHERE c.kind = 'person'"""):
        out[r["face_id"]] = r["person_id"]        # None means "nobody"
    return out


def count(con) -> dict:
    """How many corrections of each kind, for `status`."""
    return {r["kind"]: r["n"] for r in con.execute(
        "SELECT kind, COUNT(*) AS n FROM corrections GROUP BY kind")}


# --------------------------------------------------------------------------
# Re-anchoring, when the faces beneath a correction are re-detected
# --------------------------------------------------------------------------

def bbox_iou(a, b) -> float:
    """Overlap of two [x, y, w, h] boxes as a fraction of their union."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


# How much two boxes must overlap to be the same face. A re-detection of the
# same person in the same photograph agrees closely; this is loose enough to
# survive a detector that crops a little differently, and tight enough that two
# people standing side by side are never confused for one another.
MIN_IOU = 0.35


def reanchor_faces(con, content_hash: str, new_boxes: list) -> tuple[int, int]:
    """Move this photograph's `person` corrections onto re-detected faces.

    `new_boxes` is [(idx, bbox)] as just written. A correction follows the new
    face whose box overlaps its own the most; one whose face is no longer found
    keeps its recorded box and is reported rather than deleted, since the
    detector may find it again on the next run and the answer is not wrong
    merely because a model stopped seeing it.

    Returns (moved, orphaned).
    """
    rows = [r for r in con.execute(
        "SELECT face_idx, face_bbox FROM corrections "
        "WHERE kind = 'person' AND content_hash = ?", (content_hash,))]
    if not rows:
        return 0, 0

    moved = orphaned = 0
    taken: set = set()
    for r in rows:
        if not r["face_bbox"]:
            continue
        old = json.loads(r["face_bbox"])
        best, best_iou = None, MIN_IOU
        for idx, box in new_boxes:
            if idx in taken:
                continue
            score = bbox_iou(old, box)
            if score >= best_iou:
                best, best_iou = idx, score
        if best is None:
            orphaned += 1
            continue
        taken.add(best)
        if best != r["face_idx"]:
            con.execute(
                "UPDATE corrections SET face_idx = ? WHERE kind = 'person' "
                "AND content_hash = ? AND face_idx = ?",
                (best, content_hash, r["face_idx"]))
            moved += 1
    return moved, orphaned
