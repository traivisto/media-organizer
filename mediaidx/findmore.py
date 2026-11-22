"""Looking for more of one person, with a person deciding every face.

`cluster.assign` names faces for everyone at once, so it has to use one cautious
distance for the whole library and can never revisit a face it has already put
under somebody else's name. That fails exactly for the people photographed most:
a child across twenty years arrives as dozens of clusters, and a sibling sits
closer in ArcFace space than the same child at another age.

Here the question is narrower. One person, their own sure faces as the evidence,
and nothing written until somebody has looked -- which is what makes a much
longer reach affordable. Measured on this library, 2026-11-22, for the most
photographed child: between 0.55 and 0.65 from the nearest sure face, 13 of 16
offered faces were her, against a library-wide bound of 0.42-0.50.

Every answer feeds the next page: a yes becomes a hand correction, and a hand
correction is one of the sure faces the next ranking is measured against.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

EPOCH = datetime(1970, 1, 1)

import numpy as np

from . import cluster, corrections

REACH = 0.65          # furthest a face may be from the nearest sure face
PAGE = 48
NEAREST = 3           # sure faces averaged for the ranking distance
MODES = ("new", "guesses")


@dataclass
class Candidate:
    face_id: int
    asset_id: int
    content_hash: str
    idx: int
    taken_at: int | None
    distance: float           # to the nearest sure face of this person
    margin: float             # >0: nearer this person than anyone else
    rival: str | None         # whose sure face is nearest, among everyone else
    now: str | None           # the name this face carries at the moment


def born(con, person_id: int) -> int | None:
    row = con.execute("SELECT birth_year FROM persons WHERE id = ?",
                      (person_id,)).fetchone()
    return row[0] if row else None


def set_born(con, person_id: int, year: int | None) -> None:
    con.execute("UPDATE persons SET birth_year = ? WHERE id = ?",
                (year, person_id))


def candidates(con, person_id: int, mode: str = "new", *, limit: int = PAGE,
               reach: float = REACH) -> tuple[list[Candidate], int]:
    """(one page of faces to ask about, how many there are in all).

    `new`: faces not carrying this name, nearest first -- noise, unnamed
    clusters, and faces under somebody else's name, since a sibling's name on
    the wrong child is the mistake this exists to find. A face somebody has
    already answered for by hand is left alone; so is one in a photograph that
    already holds this person for sure, since nobody is in a photograph twice.

    `guesses`: faces `assign` named this person and nobody has confirmed,
    weakest first.
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    a_ids, a_mat, a_who = cluster.anchor_set(con)
    mine = a_who == person_id
    P = a_mat[mine]
    if len(P) == 0:
        return [], 0
    O, O_who = a_mat[~mine], a_who[~mine]
    sure = {int(i) for i in a_ids[mine]}

    fixed = corrections.corrected_face_ids(con)
    ruled_out = {f for f, p in corrections.rejected_pairs(con) if p == person_id}
    meta = {r["id"]: r for r in con.execute(
        """SELECT f.id, f.asset_id, f.idx, f.person_id, a.content_hash,
                  a.taken_at
             FROM faces f JOIN assets a ON a.id = f.asset_id""")}

    if mode == "new":
        holds = {meta[i]["asset_id"] for i in sure if i in meta}
        year = born(con, person_id)
        # Not .timestamp(): on Windows it raises OSError for any date before
        # 1970, and a grandparent's birth year is exactly that.  A few hours'
        # zone difference is nothing against a whole-year floor.
        floor = (int((datetime(year, 1, 1) - EPOCH).total_seconds())
                 if year else None)

        def wanted(fid: int) -> bool:
            m = meta.get(fid)
            return (m is not None and m["person_id"] != person_id
                    and fid not in fixed and fid not in ruled_out
                    and m["asset_id"] not in holds
                    and not (floor and m["taken_at"] and m["taken_at"] < floor))
    else:
        def wanted(fid: int) -> bool:
            m = meta.get(fid)
            return (m is not None and m["person_id"] == person_id
                    and fid not in sure and fid not in fixed
                    and fid not in ruled_out)

    ids, mat = cluster.load(con)
    keep = np.fromiter((wanted(int(i)) for i in ids), bool, len(ids))
    ids, mat = ids[keep], mat[keep]
    if len(ids) == 0:
        return [], 0

    k = min(NEAREST, len(P))
    near = np.empty(len(ids), np.float32)
    rank = np.empty(len(ids), np.float32)
    for i in range(0, len(ids), 4096):
        sims = mat[i:i + 4096] @ P.T
        top = -np.partition(-sims, k - 1, axis=1)[:, :k]
        near[i:i + 4096] = 1.0 - top.max(axis=1)
        rank[i:i + 4096] = 1.0 - top.mean(axis=1)

    if mode == "new":
        sel = near <= reach
        ids, mat, near, rank = ids[sel], mat[sel], near[sel], rank[sel]

    rival_d = np.ones(len(ids), np.float32)
    rival_w = np.full(len(ids), -1, np.int64)
    if len(O):
        for i in range(0, len(ids), 4096):
            sims = mat[i:i + 4096] @ O.T
            j = sims.argmax(axis=1)
            rival_d[i:i + 4096] = 1.0 - sims[np.arange(len(j)), j]
            rival_w[i:i + 4096] = O_who[j]
    margin = rival_d - rank

    # Nearest first when looking for more; weakest first when checking, since
    # a wrong guess is one nearer somebody else than the person it is under.
    order = np.argsort(rank if mode == "new" else margin, kind="stable")
    names = {r[0]: r[1] for r in con.execute("SELECT id, name FROM persons")}
    out = []
    for j in order[:limit]:
        m = meta[int(ids[j])]
        out.append(Candidate(
            face_id=int(ids[j]), asset_id=m["asset_id"],
            content_hash=m["content_hash"], idx=m["idx"],
            taken_at=m["taken_at"], distance=float(near[j]),
            margin=float(margin[j]),
            rival=names.get(int(rival_w[j])),
            now=names.get(m["person_id"])))
    return out, len(ids)


def decide(con, person_id: int, answers: dict[int, str]) -> dict:
    """Write a page of answers: `{faces.id: "yes" | "no" | "is:<name>"}`.

    yes      a hand correction naming this person
    no       ruled out for this person, and the name taken off if it was on
    is:NAME  a hand correction naming somebody else -- one answer that puts
             right both the person being looked for and the one it really is
    """
    name = con.execute("SELECT name FROM persons WHERE id = ?",
                       (person_id,)).fetchone()[0]
    done = {"yes": 0, "no": 0, "is": 0}
    for fid, answer in answers.items():
        row = con.execute(
            """SELECT a.content_hash, f.idx, f.bbox FROM faces f
                 JOIN assets a ON a.id = f.asset_id WHERE f.id = ?""",
            (fid,)).fetchone()
        if row is None:
            continue
        bbox = json.loads(row["bbox"])
        if answer == "yes":
            corrections.record(con, "person", row["content_hash"], name,
                               face_idx=row["idx"], face_bbox=bbox)
            done["yes"] += 1
        elif answer == "no":
            corrections.reject(con, row["content_hash"], row["idx"], name,
                               face_bbox=bbox)
            done["no"] += 1
        elif answer.startswith("is:") and answer[3:].strip():
            other = answer[3:].strip()
            cluster.person_id_for(con, other)
            corrections.record(con, "person", row["content_hash"], other,
                               face_idx=row["idx"], face_bbox=bbox)
            done["is"] += 1
    return done
