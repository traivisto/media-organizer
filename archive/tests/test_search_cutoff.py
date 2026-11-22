"""Where results stop, and searching inside a filter (spec §7.2, §7.3).

Two faults are being corrected. The grid showed everything the index returned,
including photographs that plainly did not match, and it stopped at a fixed
count with no way to reach the rest. Both are about the *shape* of a result
set rather than about any one score, so both are tested over synthetic score
sequences where the right answer is obvious by eye.
"""

from __future__ import annotations

import numpy as np
import pytest

from mediaidx import db, embed


# --------------------------------------------------------------------------
# The cut
# --------------------------------------------------------------------------

def _decay(top, floor_, n):
    """A real score curve: smooth decay from `top` to `floor_` over n results.

    Shaped after the measured ones (spec §7.3) rather than after a step, since
    a step is what these scores conspicuously do not have.
    """
    return [top - (top - floor_) * (i / (n - 1)) ** 0.5 for i in range(n)]


def test_the_answers_are_the_ones_close_to_the_best():
    scores = _decay(0.18, 0.08, 600)
    n = embed.cut(scores)
    assert 12 <= n < 200
    assert scores[n - 1] >= 0.18 * embed.KEEP_FRACTION
    assert scores[n] < 0.18 * embed.KEEP_FRACTION


def test_a_smooth_curve_is_still_cut():
    """The rule this replaced found no gap here, and so cut nothing at all."""
    assert embed.cut(_decay(0.146, 0.113, 600)) < 600


def test_a_flat_result_set_is_kept_whole():
    """Everything within a hair of the best is an answer."""
    assert embed.cut([0.30 - 0.00001 * i for i in range(40)]) == 40


def test_a_short_result_set_is_never_cut():
    assert embed.cut([0.9, 0.2, 0.1]) == 3


def test_the_floor_holds_against_an_early_fall():
    """A fall at rank two does not leave a page holding two photographs."""
    scores = [0.95, 0.94] + [0.20 - 0.001 * i for i in range(30)]
    assert embed.cut(scores) == embed.MIN_RESULTS


def test_the_floor_is_configurable_and_respected():
    scores = [0.95, 0.94] + [0.20 - 0.001 * i for i in range(30)]
    assert embed.cut(scores, floor=2) == 2


def test_a_fixed_threshold_would_have_got_these_wrong():
    """The reason the cut is relative and not a number (§7.3).

    Two queries with the same shape at different heights -- which is exactly
    what the measured scores do, ranging from 0.132 to 0.181 at rank one. Any
    single similarity threshold keeps all of one and none of the other; a share
    of each query's own best score treats them alike.
    """
    high = _decay(0.18, 0.08, 300)
    low = _decay(0.09, 0.04, 300)
    assert embed.cut(high) == embed.cut(low)


def test_nothing_resembling_the_query_is_left_alone():
    """Negative scores mean the top itself matched nothing; cutting is moot."""
    assert embed.cut([-0.01 * i for i in range(1, 40)]) == 39


# --------------------------------------------------------------------------
# Narrow, then rank
# --------------------------------------------------------------------------

@pytest.fixture
def indexed(tmp_path):
    """Twenty photographs, each with a vector pointing a known distance away."""
    con = db.connect(tmp_path / "photos.db")
    db.migrate(con)
    rng = np.random.default_rng(0)
    target = rng.normal(size=embed.DIM)
    target /= np.linalg.norm(target)

    ids = []
    for i in range(20):
        # Similarity falls off with i, so asset order is also score order.
        v = target + rng.normal(scale=0.01, size=embed.DIM) * (i + 1)
        v = (v / np.linalg.norm(v)).astype(np.float32)
        aid = db.insert_asset(con, f"{i:064d}",
                              {"format": "jpeg", "taken_at": 1_600_000_000})
        embed.store(con, aid, "test", v, vec_ok=False)
        ids.append(aid)
    con.commit()
    return con, target, ids


def test_a_filtered_search_ranks_within_the_filter(indexed):
    """The bug §7.2 exists to prevent.

    The five best matches in the library are the first five assets. Asking for
    the best matches *among the last five* must return those, ranked -- not the
    empty set that filtering a global top-k afterwards would leave.
    """
    con, target, ids = indexed
    tail = ids[-5:]
    got = embed.search(con, target, limit=5, vec_ok=False, candidate_ids=tail)
    assert sorted(a for a, _ in got) == tail
    assert [s for _, s in got] == sorted((s for _, s in got), reverse=True)


def test_an_empty_candidate_set_finds_nothing(indexed):
    con, target, _ = indexed
    assert embed.search(con, target, vec_ok=False, candidate_ids=[]) == []


def test_the_unfiltered_search_is_unchanged(indexed):
    con, target, ids = indexed
    got = embed.search(con, target, limit=3, vec_ok=False)
    assert [a for a, _ in got] == ids[:3]


def test_a_candidate_set_larger_than_sqlite_takes_at_once(indexed):
    """The chunking exists because the parameter ceiling is 999."""
    con, target, ids = indexed
    padded = list(range(10_000, 12_000)) + ids       # 2,020 ids, none real but ours
    got = embed.search(con, target, limit=3, vec_ok=False, candidate_ids=padded)
    assert [a for a, _ in got] == ids[:3]
