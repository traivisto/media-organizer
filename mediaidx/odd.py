"""Which photograph in a series is not like the others (spec §6.5).

A trip is a few hundred photographs of largely one thing, so the ones that are
*not* of that thing stand out arithmetically: the search vectors are already
there, and a photograph's distance from its event's centre is one multiplication.
Measured on this library, the two furthest outliers of two holidays turned out to
be a squat toilet in an alpine hut and a wall of museum text -- exactly the
incidental shots somebody would want to find and would never scroll to.

Nothing here reads a file or runs a model. This is the same machinery that
groups faces, pointed at whole photographs instead, and it needs no captions:
a caption is *derived* from the vector, so comparing captions is comparing a
lossy copy of what is compared here directly.

**How odd is odd.** Not a fixed similarity, for the reason the search cut is not
one either (§7.3): the scale differs per event. Measured over this library's 570
events of twelve photographs or more, the median similarity to the centre runs
from 0.71 to 0.86 depending on how varied the trip was, so one number would flag
everything in a varied event and nothing in a uniform one. What is comparable is
a photograph's distance below its *own* event's median, in units of that event's
own spread -- the median absolute deviation, which a handful of outliers cannot
inflate the way a standard deviation can.

Measured 2026-09-12, at k = 4: **1,308 photographs across 570 events**, 2.3 to an
event. k = 2.5 gives 5.8 an event and starts flagging ordinary variety.
"""

from __future__ import annotations

import numpy as np

# How far below its event's median a photograph must sit, in multiples of that
# event's own median absolute deviation.
K = 4.0

# Below this many photographs an event has no distribution worth the name, and
# "the odd one out of nine" is just the ninth one.
MIN_EVENT = 12

# How many to show from any one event. A trip whose photographs are all unlike
# each other is not a trip with forty problems in it.
PER_EVENT = 6


def find(con, *, k: float = K, min_event: int = MIN_EVENT,
         per_event: int = PER_EVENT) -> dict:
    """`{event_id: [(asset_id, similarity, median, spread), ...]}`, worst first.

    Dismissed photographs are left out: saying one belongs is an answer, and an
    answer that has to be given twice is not one (P8).
    """
    rows = con.execute("SELECT asset_id, embedding FROM image_vectors").fetchall()
    if not rows:
        return {}
    vec = {r[0]: r[1] for r in rows}

    members: dict = {}
    for eid, aid in con.execute(
            "SELECT event_id, asset_id FROM event_assets ORDER BY event_id"):
        if aid in vec:
            members.setdefault(eid, []).append(aid)

    dismissed = {r[0] for r in con.execute("SELECT asset_id FROM odd_dismissed")}

    out: dict = {}
    for eid, ids in members.items():
        if len(ids) < min_event:
            continue
        mat = np.frombuffer(b"".join(vec[i] for i in ids), dtype=np.float16)
        mat = mat.reshape(len(ids), -1).astype(np.float32)
        mat /= np.linalg.norm(mat, axis=1, keepdims=True)
        centre = mat.mean(0)
        centre /= np.linalg.norm(centre)
        sims = mat @ centre

        median = float(np.median(sims))
        # Median absolute deviation, not standard deviation: the outliers being
        # looked for would otherwise widen the very spread they are measured
        # against, and hide behind it.
        spread = float(np.median(np.abs(sims - median))) or 1e-6
        bar = median - k * spread

        found = [(ids[i], float(sims[i]), median, spread)
                 for i in np.argsort(sims)
                 if sims[i] < bar and ids[i] not in dismissed]
        if found:
            out[eid] = found[:per_event]
    return out


def dismiss(con, asset_id: int, *, now: int | None = None) -> None:
    """Say this photograph belongs where it is, permanently.

    Keyed by the photograph and not by its event, because `derive` throws every
    event away and builds them again -- the same reason `event_cuts` and
    `event_names` are keyed this way (§4.3).
    """
    import time
    con.execute(
        "INSERT OR IGNORE INTO odd_dismissed (asset_id, created_at) VALUES (?,?)",
        (asset_id, int(time.time()) if now is None else now))


def restore(con, asset_id: int) -> bool:
    """Put one back among the odd, having thought better of it."""
    return con.execute("DELETE FROM odd_dismissed WHERE asset_id = ?",
                       (asset_id,)).rowcount > 0


def counts(con) -> tuple[int, int]:
    """(photographs flagged, events holding one) plus how many were dismissed."""
    got = find(con)
    return sum(len(v) for v in got.values()), len(got)
