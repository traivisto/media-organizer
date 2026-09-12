"""Clustering, naming and merging.

Synthetic embeddings, deliberately: tight groups around well-separated anchors,
so the tests are about what the code does with a clustering rather than about
HDBSCAN's judgement on real faces. The quality of the clustering itself is a
question for the eye, not for pytest.

Every fixture seeds *several* groups, never one. HDBSCAN finds clusters by
density contrast, so a lone blob with nothing to contrast against is labelled
noise in its entirety -- correct behaviour, and a trap when writing tests.
Groups are given distinct sizes so each resulting cluster can be identified.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from mediaidx import cluster, corrections, db, faces
from mediaidx.config import Config

SIZES = (12, 10, 8)


@pytest.fixture
def cfg(tmp_path):
    return Config(database=tmp_path / "x.db", thumbnails=tmp_path, logs=tmp_path,
                  names_export=None, faces={"cluster_min_size": 4,
                                            "assign_threshold": 0.42})


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.isolation_level = None
    db.migrate(con)
    return con


def _unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


def _group(rng, n, spread=0.05):
    """n vectors clustered tightly around one random direction."""
    anchor = _unit(rng.normal(size=faces.DIM))
    return [_unit(anchor + rng.normal(scale=spread, size=faces.DIM))
            for _ in range(n)]


def _add_faces(con, vectors, *, folder_hint=None, start_asset=1):
    fid = None
    if folder_hint is not None:
        cur = con.execute(
            "INSERT INTO folders (path, person_hint) VALUES (?,?)",
            (f"\\\\nas\\{folder_hint}-{start_asset}", folder_hint))
        fid = cur.lastrowid
    for i, v in enumerate(vectors):
        h = f"{start_asset + i:064d}"
        aid = db.insert_asset(con, h, {"format": "jpeg"})
        con.execute(
            """INSERT INTO files (path, folder_id, asset_id, size_bytes, mtime,
                                  first_seen, last_seen)
               VALUES (?,?,?,1,1,1,1)""", (f"\\\\nas\\{h}.jpg", fid, aid))
        faces.replace_for_asset(
            con, aid,
            [faces.DetectedFace(bbox=(0.1, 0.1, 0.2, 0.2), det_score=0.9,
                                blur=100.0, embedding=v)],
            vec_ok=False)


def _seed(con, rng, sizes=SIZES, hints=None):
    """Several separated groups. Returns the vectors of each, in order."""
    groups = []
    for i, n in enumerate(sizes):
        vs = _group(rng, n)
        _add_faces(con, vs, folder_hint=(hints[i] if hints else None),
                   start_asset=1 + i * 1000)
        groups.append(vs)
    return groups


def _cluster_of_size(con, size: int) -> int:
    row = con.execute("SELECT id FROM face_clusters WHERE size = ?",
                      (size,)).fetchone()
    assert row is not None, f"no cluster of {size} faces was formed"
    return row[0]


# --- building -------------------------------------------------------------

def test_separated_groups_become_separate_clusters(cfg):
    con = _con()
    _seed(con, np.random.default_rng(1))
    st = cluster.build(con, cfg)
    assert st.faces == sum(SIZES)
    assert st.clusters == len(SIZES)
    assert st.clustered == sum(SIZES) and st.noise == 0


def test_too_few_faces_to_cluster_is_not_an_error(cfg):
    con = _con()
    _add_faces(con, _group(np.random.default_rng(2), 2))
    st = cluster.build(con, cfg)
    assert st.clusters == 0 and st.faces == 2


def test_cluster_rows_record_size_and_a_usable_centroid(cfg):
    con = _con()
    _seed(con, np.random.default_rng(3))
    cluster.build(con, cfg)

    r = con.execute("SELECT size, centroid, status FROM face_clusters "
                    "WHERE size = 10").fetchone()
    assert r["size"] == 10
    assert r["status"] == "unnamed"
    c = np.frombuffer(r["centroid"], dtype=np.float16).astype(np.float32)
    assert c.shape == (faces.DIM,)
    assert np.linalg.norm(c) == pytest.approx(1.0, abs=1e-2)


# --- incremental assignment ----------------------------------------------

def test_a_near_face_joins_an_existing_cluster(cfg):
    con = _con()
    rng = np.random.default_rng(4)
    groups = _seed(con, rng)
    cluster.build(con, cfg)

    near = _unit(groups[0][0] + rng.normal(scale=0.02, size=faces.DIM))
    _add_faces(con, [near], start_asset=90_000)
    assert cluster.assign(con, cfg) == 1
    assert con.execute(
        "SELECT COUNT(*) FROM faces WHERE cluster_id IS NULL").fetchone()[0] == 0


def test_a_distant_face_waits_rather_than_joining(cfg):
    con = _con()
    rng = np.random.default_rng(5)
    _seed(con, rng)
    cluster.build(con, cfg)

    _add_faces(con, [_unit(rng.normal(size=faces.DIM))], start_asset=90_000)
    assert cluster.assign(con, cfg) == 0
    assert con.execute(
        "SELECT COUNT(*) FROM faces WHERE cluster_id IS NULL").fetchone()[0] == 1


def test_assignment_inherits_the_cluster_person(cfg):
    con = _con()
    rng = np.random.default_rng(6)
    groups = _seed(con, rng)
    cluster.build(con, cfg)
    cid = _cluster_of_size(con, 12)
    pid = cluster.set_person(con, cid, "Anski")

    _add_faces(con, [_unit(groups[0][1] + rng.normal(scale=0.02, size=faces.DIM))],
               start_asset=90_000)
    cluster.assign(con, cfg)
    assert con.execute(
        "SELECT COUNT(*) FROM faces WHERE person_id = ?", (pid,)).fetchone()[0] == 13


def test_assignment_updates_the_cluster_size(cfg):
    con = _con()
    rng = np.random.default_rng(7)
    groups = _seed(con, rng)
    cluster.build(con, cfg)
    cid = _cluster_of_size(con, 12)

    _add_faces(con, [_unit(groups[0][2] + rng.normal(scale=0.02, size=faces.DIM))],
               start_asset=90_000)
    cluster.assign(con, cfg)
    assert con.execute("SELECT size FROM face_clusters WHERE id = ?",
                       (cid,)).fetchone()[0] == 13


# --- naming ---------------------------------------------------------------

def test_naming_tags_every_face_in_the_cluster(cfg):
    con = _con()
    _seed(con, np.random.default_rng(8))
    cluster.build(con, cfg)
    cid = _cluster_of_size(con, 10)

    cluster.set_person(con, cid, "Henna")
    assert con.execute("SELECT status FROM face_clusters WHERE id = ?",
                       (cid,)).fetchone()[0] == "named"
    assert con.execute(
        "SELECT COUNT(*) FROM faces WHERE person_id IS NOT NULL").fetchone()[0] == 10


def test_the_same_name_twice_is_one_person(cfg):
    """A person across 28 years is several clusters and exactly one person."""
    con = _con()
    _seed(con, np.random.default_rng(9))
    cluster.build(con, cfg)

    a = cluster.set_person(con, _cluster_of_size(con, 12), "Anski")
    b = cluster.set_person(con, _cluster_of_size(con, 10), "Anski")
    assert a == b
    assert con.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 1
    assert con.execute(
        "SELECT COUNT(*) FROM faces WHERE person_id = ?", (a,)).fetchone()[0] == 22


def test_clearing_a_name_releases_the_faces(cfg):
    con = _con()
    _seed(con, np.random.default_rng(10))
    cluster.build(con, cfg)
    cid = _cluster_of_size(con, 10)
    cluster.set_person(con, cid, "Anski")
    cluster.clear_person(con, cid)

    assert con.execute(
        "SELECT COUNT(*) FROM faces WHERE person_id IS NOT NULL").fetchone()[0] == 0
    assert con.execute("SELECT status FROM face_clusters WHERE id = ?",
                       (cid,)).fetchone()[0] == "unnamed"


# --- merging --------------------------------------------------------------

def test_merge_moves_every_face_and_removes_the_source(cfg):
    """A child at 4 and at 17: two clusters, one person, one action."""
    con = _con()
    _seed(con, np.random.default_rng(11))
    cluster.build(con, cfg)
    a, b = _cluster_of_size(con, 12), _cluster_of_size(con, 10)

    assert cluster.merge(con, a, b) == 10
    assert con.execute("SELECT COUNT(*) FROM face_clusters").fetchone()[0] == 2
    assert con.execute("SELECT size FROM face_clusters WHERE id = ?",
                       (a,)).fetchone()[0] == 22
    assert con.execute("SELECT COUNT(*) FROM face_clusters WHERE id = ?",
                       (b,)).fetchone()[0] == 0


def test_merging_into_an_unnamed_cluster_keeps_the_name(cfg):
    con = _con()
    _seed(con, np.random.default_rng(12))
    cluster.build(con, cfg)
    a, b = _cluster_of_size(con, 12), _cluster_of_size(con, 10)
    pid = cluster.set_person(con, b, "Anski")          # the *source* is named

    cluster.merge(con, a, b)
    assert con.execute("SELECT person_id FROM face_clusters WHERE id = ?",
                       (a,)).fetchone()[0] == pid
    assert con.execute(
        "SELECT COUNT(*) FROM faces WHERE person_id = ?", (pid,)).fetchone()[0] == 22


def test_merging_a_cluster_into_itself_does_nothing(cfg):
    con = _con()
    _seed(con, np.random.default_rng(13))
    cluster.build(con, cfg)
    cid = _cluster_of_size(con, 12)
    assert cluster.merge(con, cid, cid) == 0
    assert con.execute("SELECT size FROM face_clusters WHERE id = ?",
                       (cid,)).fetchone()[0] == 12


# --- rebuild --------------------------------------------------------------

def test_a_rebuild_keeps_the_names(cfg):
    """Names are the only irreplaceable thing in the database (P6)."""
    con = _con()
    _seed(con, np.random.default_rng(14))
    cluster.build(con, cfg)
    pid = cluster.set_person(con, _cluster_of_size(con, 12), "Anski")

    st = cluster.build(con, cfg, rebuild=True)
    assert st.named >= 1
    assert con.execute(
        "SELECT COUNT(*) FROM face_clusters WHERE person_id = ?",
        (pid,)).fetchone()[0] >= 1
    assert con.execute("SELECT name FROM persons WHERE id = ?",
                       (pid,)).fetchone()[0] == "Anski"
    assert con.execute(
        "SELECT COUNT(*) FROM faces WHERE person_id = ?", (pid,)).fetchone()[0] == 12


def test_a_first_build_leaves_nothing_named(cfg):
    con = _con()
    _seed(con, np.random.default_rng(15))
    st = cluster.build(con, cfg)
    assert st.named == 0


# --- suggestions ----------------------------------------------------------

def test_a_folder_hint_proposes_a_name_without_applying_it(cfg):
    con = _con()
    _seed(con, np.random.default_rng(16), hints=("Anski", None, None))
    cluster.build(con, cfg)
    cid = _cluster_of_size(con, 12)

    proposal = cluster.suggest(con, cid)
    assert proposal is not None and proposal[0] == "Anski"
    assert "12 of 12" in proposal[1]
    # Proposed, never applied: nothing is named until someone says so.
    assert con.execute("SELECT person_id FROM face_clusters WHERE id = ?",
                       (cid,)).fetchone()[0] is None


def test_no_hint_means_no_proposal(cfg):
    con = _con()
    _seed(con, np.random.default_rng(17))
    cluster.build(con, cfg)
    assert cluster.suggest(con, _cluster_of_size(con, 12)) is None


def test_a_weak_hint_does_not_propose(cfg):
    """Two faces of twelve from a marked folder say nothing about the rest."""
    con = _con()
    rng = np.random.default_rng(18)
    groups = _seed(con, rng)
    # Re-file two of the first group's faces under a hinted folder.
    fid = con.execute(
        "INSERT INTO folders (path, person_hint) VALUES ('\\\\nas\\h', 'Anski')"
    ).lastrowid
    con.execute(
        "UPDATE files SET folder_id = ? WHERE asset_id IN "
        "(SELECT id FROM assets ORDER BY id LIMIT 2)", (fid,))
    cluster.build(con, cfg)
    assert cluster.suggest(con, _cluster_of_size(con, 12)) is None


# --- ignoring -------------------------------------------------------------

def test_an_ignored_cluster_takes_no_further_faces(cfg):
    """Statues, posters, the same stranger in a museum: clusters, not people."""
    con = _con()
    rng = np.random.default_rng(19)
    groups = _seed(con, rng)
    cluster.build(con, cfg)
    cid = _cluster_of_size(con, 12)
    cluster.set_status(con, cid, "ignored")

    _add_faces(con, [_unit(groups[0][0] + rng.normal(scale=0.02, size=faces.DIM))],
               start_asset=90_000)
    assert cluster.assign(con, cfg) == 0


# --- reaching a cluster the shortlist did not offer ------------------------

def test_any_cluster_can_be_merged_not_only_similar_ones(cfg):
    """The shortlist is ranked by similarity, so the right answer can be
    outside it -- the same child at 4 and at 17 especially. Merging must not
    be limited to what was offered."""
    con = _con()
    rng = np.random.default_rng(20)
    _seed(con, rng, sizes=(12, 10, 8, 6))
    cluster.build(con, cfg)

    a = _cluster_of_size(con, 12)
    shortlist = {c["id"] for c in cluster.similar(con, a, limit=1)}
    outside = [r[0] for r in con.execute(
        "SELECT id FROM face_clusters WHERE id != ?", (a,))
        if r[0] not in shortlist]
    assert outside, "test needs a cluster outside the shortlist"

    before = con.execute("SELECT size FROM face_clusters WHERE id = ?",
                         (a,)).fetchone()[0]
    moved = cluster.merge(con, a, outside[0])
    assert moved > 0
    assert con.execute("SELECT size FROM face_clusters WHERE id = ?",
                       (a,)).fetchone()[0] == before + moved


def test_merging_a_cluster_that_does_not_exist_is_refused(cfg):
    con = _con()
    _seed(con, np.random.default_rng(21))
    cluster.build(con, cfg)
    cid = _cluster_of_size(con, 12)
    with pytest.raises(ValueError):
        cluster.merge(con, cid, 999_999)
    # and nothing was disturbed on the way
    assert con.execute("SELECT size FROM face_clusters WHERE id = ?",
                       (cid,)).fetchone()[0] == 12


def test_similar_never_offers_the_cluster_itself(cfg):
    con = _con()
    _seed(con, np.random.default_rng(22))
    cluster.build(con, cfg)
    cid = _cluster_of_size(con, 12)
    assert cid not in {c["id"] for c in cluster.similar(con, cid, limit=50)}


def test_similar_is_ordered_by_similarity(cfg):
    con = _con()
    _seed(con, np.random.default_rng(23), sizes=(12, 10, 8, 6))
    cluster.build(con, cfg)
    got = cluster.similar(con, _cluster_of_size(con, 12), limit=10)
    sims = [c["similarity"] for c in got]
    assert sims == sorted(sims, reverse=True)


def test_one_name_for_two_people_is_visible_not_silent(cfg):
    """Naming a cluster with an existing name joins that person. That is right
    for one person in several clusters and wrong for two people who share a
    first name, so the page has to say it is happening."""
    con = _con()
    _seed(con, np.random.default_rng(24))
    cluster.build(con, cfg)
    a, b = _cluster_of_size(con, 12), _cluster_of_size(con, 10)
    pid = cluster.set_person(con, a, "Reetta")
    assert cluster.set_person(con, b, "Reetta") == pid, "should be one person"

    # Both clusters, one person, and the count reflects both.
    assert con.execute(
        "SELECT COUNT(*) FROM face_clusters WHERE person_id = ?",
        (pid,)).fetchone()[0] == 2
    assert con.execute(
        "SELECT COUNT(*) FROM faces WHERE person_id = ?", (pid,)).fetchone()[0] == 22


def test_renaming_one_cluster_separates_two_people_again(cfg):
    """The repair for a shared name: rename one, and only its faces move."""
    con = _con()
    _seed(con, np.random.default_rng(25))
    cluster.build(con, cfg)
    a, b = _cluster_of_size(con, 12), _cluster_of_size(con, 10)
    first = cluster.set_person(con, a, "Reetta")
    cluster.set_person(con, b, "Reetta")

    second = cluster.set_person(con, b, "Reetta S")
    assert second != first
    assert con.execute(
        "SELECT COUNT(*) FROM faces WHERE person_id = ?", (first,)).fetchone()[0] == 12
    assert con.execute(
        "SELECT COUNT(*) FROM faces WHERE person_id = ?", (second,)).fetchone()[0] == 10


# --------------------------------------------------------------------------
# A face somebody named speaks for that person
# --------------------------------------------------------------------------

def test_a_hand_named_face_pulls_in_its_likenesses(cfg):
    """The surest evidence in the database used to count for nothing here.

    `assign` compared against cluster centroids, and a centroid is computed from
    cluster members -- so a face somebody named by hand, which usually belongs to
    no cluster, was invisible. This is the case that changes: a stranger to every
    cluster, named once, and others like them.
    """
    rng = np.random.default_rng(31)
    con = _con()
    groups = [_group(rng, 6) for _ in range(2)]
    _add_faces(con, groups[0] + groups[1])
    cluster.build(con, cfg)

    # Somebody the clusterer never grouped: one face, named by hand, plus three
    # more like it that it also left alone.
    apart = _unit(rng.normal(size=faces.DIM))
    _add_faces(con, [apart] + [_unit(apart + rng.normal(scale=0.02, size=faces.DIM))
                               for _ in range(3)], start_asset=500)
    con.commit()
    lone = con.execute(
        "SELECT id FROM faces WHERE cluster_id IS NULL ORDER BY id").fetchone()[0]
    pid = cluster.person_id_for(con, "Reetta")
    con.execute("UPDATE faces SET person_id = ? WHERE id = ?", (pid, lone))
    h = con.execute("""SELECT a.content_hash FROM assets a JOIN faces f
                        ON f.asset_id = a.id WHERE f.id = ?""", (lone,)).fetchone()[0]
    corrections.record(con, "person", h, "Reetta", face_idx=0,
                       face_bbox=(0.0, 0.0, 0.2, 0.2))
    con.commit()

    cluster.assign(con, cfg)
    con.commit()

    got = con.execute(
        """SELECT COUNT(*) FROM faces f JOIN persons p ON p.id = f.person_id
            WHERE p.name = 'Reetta'""").fetchone()[0]
    assert got >= 3, "the hand-named face did not speak for anybody"


def test_a_face_named_by_assign_does_not_become_evidence_itself(cfg):
    """A guess must not become the evidence for the next guess.

    Otherwise a name walks outward one near-miss at a time and ends up on
    somebody else entirely. Only a cluster member or a hand correction is ever
    an anchor, so a face `assign` named is given no cluster and stays silent.
    """
    rng = np.random.default_rng(32)
    con = _con()
    _add_faces(con, _group(rng, 6) + _group(rng, 6))
    cluster.build(con, cfg)

    apart = _unit(rng.normal(size=faces.DIM))
    _add_faces(con, [apart, _unit(apart + rng.normal(scale=0.02, size=faces.DIM))],
               start_asset=500)
    con.commit()
    lone = con.execute(
        "SELECT id FROM faces WHERE cluster_id IS NULL ORDER BY id").fetchone()[0]
    pid = cluster.person_id_for(con, "Reetta")
    con.execute("UPDATE faces SET person_id = ? WHERE id = ?", (pid, lone))
    h = con.execute("""SELECT a.content_hash FROM assets a JOIN faces f
                        ON f.asset_id = a.id WHERE f.id = ?""", (lone,)).fetchone()[0]
    corrections.record(con, "person", h, "Reetta", face_idx=0,
                       face_bbox=(0.0, 0.0, 0.2, 0.2))
    con.commit()

    cluster.assign(con, cfg)
    con.commit()
    named = con.execute(
        """SELECT f.id FROM faces f JOIN persons p ON p.id = f.person_id
            WHERE p.name = 'Reetta' AND f.id != ?""", (lone,)).fetchall()
    for r in named:
        assert con.execute("SELECT cluster_id FROM faces WHERE id = ?",
                           (r[0],)).fetchone()[0] is None, \
            "a face assign named joined a cluster and became an anchor"


def test_assign_still_leaves_a_stranger_alone(cfg):
    """Waiting is the cheaper mistake, and that has not changed."""
    rng = np.random.default_rng(33)
    con = _con()
    _add_faces(con, _group(rng, 6) + _group(rng, 6))
    cluster.build(con, cfg)
    _add_faces(con, [_unit(rng.normal(size=faces.DIM))], start_asset=900)
    con.commit()
    before = con.execute(
        "SELECT COUNT(*) FROM faces WHERE person_id IS NOT NULL").fetchone()[0]
    cluster.assign(con, cfg)
    con.commit()
    after = con.execute(
        "SELECT COUNT(*) FROM faces WHERE person_id IS NOT NULL").fetchone()[0]
    assert after == before
