"""Face clustering: turning vectors into people (spec §6.1).

This job reads the database and nothing else. No file is opened, no model runs,
no network is touched -- which is the whole reason stage 2 writes face crops as
it goes. Clustering can therefore be re-run whenever the thresholds need tuning,
at no cost beyond CPU, long after the archive has been read.

Two facts about this library shape the design. A person photographed across 28
years does not produce one cluster: a child at 4 and the same child at 17 sit
further apart in ArcFace space than two unrelated adults, and no threshold fixes
that. So the model allows many clusters per person and merging is a first-class
action rather than a repair. And most faces belong to nobody worth naming --
strangers at the next table, a crowd behind the subject -- so noise is left as
noise instead of being forced into a cluster.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from . import faces as faces_mod
from .embed import pack

MIN_CLUSTER_SIZE = 4
ASSIGN_THRESHOLD = 0.42          # cosine distance, for incremental assignment


def load(con, where: str = "", args: tuple = ()) -> tuple[np.ndarray, np.ndarray]:
    """(face_ids, matrix) with rows L2-normalised float32."""
    rows = con.execute(
        f"SELECT id, embedding FROM faces {where} ORDER BY id", args).fetchall()
    if not rows:
        return (np.empty(0, np.int64),
                np.empty((0, faces_mod.DIM), np.float32))
    ids = np.fromiter((r[0] for r in rows), np.int64, len(rows))
    mat = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float16)
    mat = mat.reshape(len(rows), -1).astype(np.float32)
    # float16 storage costs a little of the norm; restore it so that a dot
    # product is exactly cosine similarity everywhere below.
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return ids, mat / norms


def centroid(mat: np.ndarray) -> np.ndarray:
    c = mat.mean(axis=0)
    n = float(np.linalg.norm(c))
    return (c / n if n else c).astype(np.float32)


@dataclass
class ClusterStats:
    faces: int = 0
    clusters: int = 0
    clustered: int = 0
    noise: int = 0
    named: int = 0
    assigned: int = 0
    started: float = field(default_factory=time.time)

    @property
    def elapsed(self) -> float:
        return time.time() - self.started


# --------------------------------------------------------------------------
# building
# --------------------------------------------------------------------------

def build(con, cfg, *, rebuild: bool = False) -> ClusterStats:
    """HDBSCAN over every face embedding.

    Euclidean distance over L2-normalised vectors is a monotone function of
    cosine distance (d^2 = 2(1 - cos)), so the neighbour ordering HDBSCAN sees
    is the cosine ordering, computed on the faster path.

    Measured on random unit vectors, which is the worst case for the tree
    search: 20,000 faces in 6 minutes, and the cost grows with the square of
    the count. Real embeddings cluster, so they prune better than that.
    """
    from sklearn.cluster import HDBSCAN

    st = ClusterStats()
    fc = cfg.faces
    min_size = int(fc.get("cluster_min_size", MIN_CLUSTER_SIZE))

    ids, mat = load(con)
    st.faces = len(ids)
    if st.faces < min_size:
        return st

    # A rebuild must not lose the names, which are the only irreplaceable thing
    # in the database (P6). Each new cluster inherits the person its members
    # mostly belonged to before.
    previous: dict[int, int] = {}
    if rebuild:
        previous = {r[0]: r[1] for r in con.execute(
            "SELECT id, person_id FROM faces WHERE person_id IS NOT NULL")}

    labels = HDBSCAN(min_cluster_size=min_size, metric="euclidean",
                     copy=False).fit_predict(mat)

    con.execute("BEGIN")
    try:
        con.execute("UPDATE faces SET cluster_id = NULL, person_id = NULL")
        con.execute("DELETE FROM face_clusters")
        now = int(time.time())

        for label in sorted({int(x) for x in labels}):
            if label < 0:                       # HDBSCAN's noise
                continue
            sel = labels == label
            members, vecs = ids[sel], mat[sel]

            person_id, status = None, "unnamed"
            if previous:
                votes: dict[int, int] = {}
                for fid in members:
                    pid = previous.get(int(fid))
                    if pid is not None:
                        votes[pid] = votes.get(pid, 0) + 1
                if votes:
                    winner, n = max(votes.items(), key=lambda kv: kv[1])
                    if n * 2 > sum(votes.values()):     # a real majority
                        person_id, status = winner, "named"
                        st.named += 1

            cur = con.execute(
                """INSERT INTO face_clusters
                     (person_id, size, centroid, status, updated_at)
                   VALUES (?,?,?,?,?)""",
                (person_id, len(members), pack(centroid(vecs)), status, now))
            cid = cur.lastrowid
            con.executemany(
                "UPDATE faces SET cluster_id = ?, person_id = ? WHERE id = ?",
                [(cid, person_id, int(f)) for f in members])
            st.clusters += 1
            st.clustered += len(members)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise

    st.noise = st.faces - st.clustered
    return st


def assign(con, cfg) -> int:
    """Attach faces found since the last build to existing clusters.

    Above the threshold a face joins its nearest cluster; below it, it waits for
    the next full build. Waiting is the cheaper mistake: a stranger forced into
    a named cluster is invisible until someone browses that person and finds
    them there.
    """
    threshold = float(cfg.faces.get("assign_threshold", ASSIGN_THRESHOLD))

    crows = con.execute(
        "SELECT id, centroid FROM face_clusters WHERE status != 'ignored'").fetchall()
    if not crows:
        return 0
    cids = np.fromiter((r[0] for r in crows), np.int64, len(crows))
    cents = np.frombuffer(b"".join(r[1] for r in crows), dtype=np.float16)
    cents = cents.reshape(len(crows), -1).astype(np.float32)

    ids, mat = load(con, "WHERE cluster_id IS NULL")
    if len(ids) == 0:
        return 0

    sims = mat @ cents.T
    best = sims.argmax(axis=1)
    dist = 1.0 - sims[np.arange(len(ids)), best]
    take = dist <= threshold
    if not take.any():
        return 0

    con.execute("BEGIN")
    try:
        pairs = [(int(cids[best[i]]), int(ids[i]))
                 for i in range(len(ids)) if take[i]]
        con.executemany(
            """UPDATE faces SET cluster_id = ?,
                 person_id = (SELECT person_id FROM face_clusters WHERE id = ?)
               WHERE id = ?""",
            [(c, c, f) for c, f in pairs])
        for cid in {c for c, _ in pairs}:
            refresh(con, cid)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return len(pairs)


def refresh(con, cluster_id: int) -> None:
    """Recompute a cluster's size and centroid from its current members."""
    ids, mat = load(con, "WHERE cluster_id = ?", (cluster_id,))
    if len(ids) == 0:
        con.execute("DELETE FROM face_clusters WHERE id = ?", (cluster_id,))
        return
    con.execute(
        "UPDATE face_clusters SET size = ?, centroid = ?, updated_at = ? WHERE id = ?",
        (len(ids), pack(centroid(mat)), int(time.time()), cluster_id))


# --------------------------------------------------------------------------
# naming and merging
# --------------------------------------------------------------------------

def person_id_for(con, name: str) -> int:
    name = name.strip()
    row = con.execute("SELECT id FROM persons WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    cur = con.execute(
        "INSERT INTO persons (name, source, created_at) VALUES (?,?,?)",
        (name, "manual", int(time.time())))
    return cur.lastrowid


def set_person(con, cluster_id: int, name: str) -> int:
    pid = person_id_for(con, name)
    con.execute(
        "UPDATE face_clusters SET person_id = ?, status = 'named', updated_at = ? "
        "WHERE id = ?", (pid, int(time.time()), cluster_id))
    con.execute("UPDATE faces SET person_id = ? WHERE cluster_id = ?",
                (pid, cluster_id))
    con.commit()
    return pid


def clear_person(con, cluster_id: int) -> None:
    con.execute(
        "UPDATE face_clusters SET person_id = NULL, status = 'unnamed', "
        "updated_at = ? WHERE id = ?", (int(time.time()), cluster_id))
    con.execute("UPDATE faces SET person_id = NULL WHERE cluster_id = ?",
                (cluster_id,))
    con.commit()


def set_status(con, cluster_id: int, status: str) -> None:
    con.execute("UPDATE face_clusters SET status = ?, updated_at = ? WHERE id = ?",
                (status, int(time.time()), cluster_id))
    con.commit()


def merge(con, into: int, other: int) -> int:
    """Fold one cluster into another. The 28-year problem, in one action."""
    if into == other:
        return 0
    row = con.execute("SELECT person_id FROM face_clusters WHERE id = ?",
                      (into,)).fetchone()
    if row is None:
        raise ValueError(f"no cluster {into}")
    src = con.execute("SELECT person_id FROM face_clusters WHERE id = ?",
                      (other,)).fetchone()
    if src is None:
        raise ValueError(f"no cluster {other}")

    pid = row[0] if row[0] is not None else src[0]
    n = con.execute("SELECT COUNT(*) FROM faces WHERE cluster_id = ?",
                    (other,)).fetchone()[0]

    con.execute("UPDATE faces SET cluster_id = ? WHERE cluster_id = ?",
                (into, other))
    con.execute("UPDATE faces SET person_id = ? WHERE cluster_id = ?", (pid, into))
    con.execute("DELETE FROM face_clusters WHERE id = ?", (other,))
    con.execute("UPDATE face_clusters SET person_id = ?, status = ? WHERE id = ?",
                (pid, "named" if pid else "unnamed", into))
    refresh(con, into)
    con.commit()
    return n


def suggest(con, cluster_id: int, *, share: float = 0.6):
    """A name proposed from folder person hints -- proposed, never applied.

    `add --person NAME` is the only way a name enters the system without being
    typed, and even then it only reaches the point of being offered. Naming a
    cluster silently would put a name on photographs nobody has looked at.
    """
    rows = con.execute(
        """SELECT fo.person_hint AS hint, COUNT(DISTINCT fa.id) AS n
           FROM faces fa
           JOIN files fi ON fi.asset_id = fa.asset_id
           JOIN folders fo ON fo.id = fi.folder_id
           WHERE fa.cluster_id = ? AND fo.person_hint IS NOT NULL
           GROUP BY hint ORDER BY n DESC""", (cluster_id,)).fetchall()
    if not rows:
        return None
    total = con.execute("SELECT COUNT(*) FROM faces WHERE cluster_id = ?",
                        (cluster_id,)).fetchone()[0]
    top = rows[0]
    if not total or top["n"] / total < share:
        return None
    return (top["hint"],
            f'{top["n"]} of {total} faces come from folders you marked as '
            f'{top["hint"]}')


def named_people(con) -> list:
    """Everyone who has been named, with how much of the library they are in."""
    return con.execute(
        """SELECT p.id, p.name,
                  COUNT(DISTINCT f.asset_id) AS photos,
                  (SELECT a.content_hash FROM faces x
                     JOIN assets a ON a.id = x.asset_id
                    WHERE x.person_id = p.id
                    ORDER BY x.blur_score DESC LIMIT 1) AS cover_hash,
                  (SELECT x.idx FROM faces x WHERE x.person_id = p.id
                    ORDER BY x.blur_score DESC LIMIT 1) AS cover_idx
           FROM persons p JOIN faces f ON f.person_id = p.id
           GROUP BY p.id ORDER BY photos DESC""").fetchall()


def people_in_query(con, q: str) -> list:
    """Named people whose name appears *inside* the query.

    The opposite direction to the search strip, which asks whether the query
    appears inside a name so that typing "emm" finds Emmi. Here the question is
    which people a phrase like "Tommi and Emmi" is asking about, so the names
    are looked for in the phrase. Both are needed and they are not the same
    test.

    Where one matched name contains another -- Emmi inside Emmiliina -- only
    the longer is kept, otherwise every search for Emmiliina would silently
    become a search for two people.
    """
    needle = q.casefold()
    hits = [r for r in named_people(con) if r["name"].casefold() in needle]
    return [r for r in hits
            if not any(o["id"] != r["id"]
                       and r["name"].casefold() in o["name"].casefold()
                       for o in hits)]


def photos_with_all(con, person_ids: list[int], limit: int = 300) -> list:
    """Photographs containing every one of these people.

    Everyone in the list has to appear, which is the question actually being
    asked: "the two of them together", not "either of them".
    """
    if not person_ids:
        return []
    marks = ",".join("?" * len(person_ids))
    return con.execute(
        f"""SELECT a.id, a.content_hash, a.taken_at, a.taken_at_precision,
                   a.camera_model
            FROM assets a
            JOIN faces f ON f.asset_id = a.id AND f.person_id IN ({marks})
            GROUP BY a.id
            HAVING COUNT(DISTINCT f.person_id) = ?
            ORDER BY a.taken_at DESC LIMIT ?""",
        [*person_ids, len(person_ids), limit]).fetchall()


def representatives(con, cluster_id: int, limit: int = 12) -> list:
    """Sharpest and most confident first -- what a person is recognised from."""
    return con.execute(
        """SELECT fa.id, fa.idx, fa.det_score, fa.blur_score, fa.asset_id,
                  a.content_hash, a.taken_at
           FROM faces fa JOIN assets a ON a.id = fa.asset_id
           WHERE fa.cluster_id = ?
           ORDER BY fa.blur_score DESC, fa.det_score DESC
           LIMIT ?""", (cluster_id, limit)).fetchall()


def similar(con, cluster_id: int, limit: int = 12) -> list[dict]:
    """Clusters nearest this one by centroid, likeliest first.

    The merge dropdown used to list clusters by size and stop at 60, which on a
    library with 737 of them meant most could not be reached at all -- and size
    is a useless ordering for this question anyway. What someone merging wants
    is "which of these is the same person", and the embeddings already answer
    that. The same child at 4 and at 17 will not be the top match, but it is
    far more likely to be in the first dozen by similarity than in the first
    dozen by size.
    """
    rows = con.execute(
        """SELECT c.id, c.size, c.centroid, p.name,
                  (SELECT a.content_hash FROM faces f
                     JOIN assets a ON a.id = f.asset_id
                    WHERE f.cluster_id = c.id
                    ORDER BY f.blur_score DESC LIMIT 1) AS cover_hash,
                  (SELECT f.idx FROM faces f WHERE f.cluster_id = c.id
                    ORDER BY f.blur_score DESC LIMIT 1) AS cover_idx
           FROM face_clusters c
           LEFT JOIN persons p ON p.id = c.person_id
           WHERE c.status != 'ignored'""").fetchall()
    mine = next((r for r in rows if r["id"] == cluster_id), None)
    if mine is None:
        return []
    others = [r for r in rows if r["id"] != cluster_id]
    if not others:
        return []

    def vec(blob):
        v = np.frombuffer(blob, dtype=np.float16).astype(np.float32)
        n = float(np.linalg.norm(v))
        return v / n if n else v

    me = vec(mine["centroid"])
    mat = np.vstack([vec(r["centroid"]) for r in others])
    sims = mat @ me
    order = np.argsort(-sims)[:limit]
    return [{"id": others[i]["id"], "size": others[i]["size"],
             "name": others[i]["name"], "similarity": float(sims[i]),
             "cover_hash": others[i]["cover_hash"],
             "cover_idx": others[i]["cover_idx"]} for i in order]


def overview(con) -> list:
    """Clusters for the review screen: biggest first, unnamed before named."""
    return con.execute(
        """SELECT c.id, c.size, c.status, p.name AS person,
                  (SELECT a.content_hash FROM faces f
                     JOIN assets a ON a.id = f.asset_id
                    WHERE f.cluster_id = c.id
                    ORDER BY f.blur_score DESC LIMIT 1) AS cover_hash,
                  (SELECT f.idx FROM faces f
                    WHERE f.cluster_id = c.id
                    ORDER BY f.blur_score DESC LIMIT 1) AS cover_idx
           FROM face_clusters c
           LEFT JOIN persons p ON p.id = c.person_id
           WHERE c.status != 'ignored'
           ORDER BY (c.person_id IS NOT NULL), c.size DESC""").fetchall()
