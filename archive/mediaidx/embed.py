"""Stage 3: one vector per photograph, and the search that vector enables.

Images and text land in the same space, so a typed phrase ranks photographs
directly -- no tag vocabulary, nothing to maintain. This is the primary search
mechanism and it does not depend on captions ever being generated.

Model choice is governed by a measurement, not a preference: the GPU encodes at
~47 images/second while the link delivers ~3.2. The model is fifteen times
faster than the pipeline can feed it, so there is no reason to trade quality
for speed. SigLIP SO400M at 384px is chosen on that basis.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

STAGE = "embed"
STAGE_VERSION = 1

DEFAULT_MODEL = "ViT-SO400M-14-SigLIP-384"
DEFAULT_PRETRAINED = "webli"
DIM = 1152


def pack(vec: np.ndarray) -> bytes:
    """float16 on disk: 1152 dims is 2.3 kB per photograph, ~107 MB at 46k."""
    return vec.astype(np.float16).tobytes()


def unpack(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float16).astype(np.float32)


def pack_f32(vec: np.ndarray) -> bytes:
    """sqlite-vec wants float32."""
    v = vec.astype(np.float32)
    return struct.pack(f"{len(v)}f", *v)


@dataclass
class Encoder:
    model_name: str = DEFAULT_MODEL
    pretrained: str = DEFAULT_PRETRAINED
    device: str = "cuda"

    def __post_init__(self):
        import torch
        import open_clip

        self._torch = torch
        if self.device == "cuda" and not torch.cuda.is_available():
            self.device = "cpu"
        model, _, preprocess = open_clip.create_model_and_transforms(
            self.model_name, pretrained=self.pretrained)
        self.model = model.to(self.device).eval()
        self.preprocess = preprocess
        self.tokenizer = open_clip.get_tokenizer(self.model_name)

    @property
    def tag(self) -> str:
        return f"{self.model_name}/{self.pretrained}"

    def prepare(self, im):
        """CPU-side transform. Runs in the reader threads, off the GPU path."""
        return self.preprocess(im)

    def encode_prepared(self, tensors) -> np.ndarray:
        torch = self._torch
        batch = torch.stack(tensors).to(self.device)
        with torch.no_grad():
            if self.device == "cuda":
                with torch.autocast("cuda", dtype=torch.float16):
                    f = self.model.encode_image(batch)
            else:
                f = self.model.encode_image(batch)
            f = f.float()
            f = f / f.norm(dim=-1, keepdim=True)
        return f.cpu().numpy()

    def encode_text(self, texts: list[str]) -> np.ndarray:
        torch = self._torch
        with torch.no_grad():
            tok = self.tokenizer(texts).to(self.device)
            f = self.model.encode_text(tok).float()
            f = f / f.norm(dim=-1, keepdim=True)
        return f.cpu().numpy()


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------

def load_vec_extension(con) -> bool:
    """sqlite-vec gives us a real ANN index. Absent, we fall back to numpy."""
    try:
        import sqlite_vec
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
        return True
    except Exception:
        return False


def vec_index_ready(con) -> bool:
    """Whether the ANN index can actually be queried.

    The extension loading and the index existing are two different facts, and
    only the pair of them makes `search` safe: a database embedded before
    sqlite-vec was available holds its vectors in `image_vectors` with no
    `vec_images` beside them, and asking the index for them raises rather than
    falling back. The numpy path answers such a database perfectly well.
    """
    if not load_vec_extension(con):
        return False
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'vec_images'").fetchone() \
        is not None


def ensure_vec_table(con) -> bool:
    if not load_vec_extension(con):
        return False
    con.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_images "
        f"USING vec0(asset_id INTEGER PRIMARY KEY, embedding float[{DIM}])")
    return True


def store(con, asset_id: int, model: str, vec: np.ndarray, *, vec_ok: bool) -> None:
    con.execute(
        """INSERT INTO image_vectors (asset_id, model, embedding) VALUES (?,?,?)
           ON CONFLICT(asset_id) DO UPDATE SET
             model=excluded.model, embedding=excluded.embedding""",
        (asset_id, model, pack(vec)))
    if vec_ok:
        con.execute("DELETE FROM vec_images WHERE asset_id = ?", (asset_id,))
        con.execute("INSERT INTO vec_images (asset_id, embedding) VALUES (?, ?)",
                    (asset_id, pack_f32(vec)))


# SQLite's default ceiling on host parameters is 999, so a candidate set is
# read back in chunks rather than as one enormous IN clause.
_CHUNK = 900


def _subset(con, ids) -> tuple[np.ndarray, np.ndarray]:
    """Vectors for a given set of assets, in no particular order."""
    got_ids, blobs = [], []
    ids = list(ids)
    for i in range(0, len(ids), _CHUNK):
        chunk = ids[i:i + _CHUNK]
        for r in con.execute(
                "SELECT asset_id, embedding FROM image_vectors "
                f"WHERE asset_id IN ({','.join('?' * len(chunk))})", chunk):
            got_ids.append(r[0])
            blobs.append(r[1])
    if not got_ids:
        return np.empty(0, dtype=np.int64), np.empty((0, 0), dtype=np.float32)
    mat = np.frombuffer(b"".join(blobs), dtype=np.float16)
    mat = mat.reshape(len(got_ids), -1).astype(np.float32)
    return np.asarray(got_ids, dtype=np.int64), mat


def search(con, query_vec: np.ndarray, limit: int = 60, *,
           vec_ok: bool, candidate_ids=None) -> list[tuple[int, float]]:
    """Return (asset_id, similarity) best first.

    `candidate_ids` narrows the search *before* it ranks, which is a
    correctness condition and not an optimisation (spec §7.2). The index
    returns a global top-k; ranking first and filtering afterwards would take
    the best matches in the whole library and then throw away the ones outside
    the filter, commonly leaving nothing and never leaving the best matches
    within it. Restricting the candidate set is also the cheaper direction --
    fewer vectors to multiply than the unfiltered path reads.
    """
    if candidate_ids is not None:
        ids, mat = _subset(con, candidate_ids)
        if len(ids) == 0:
            return []
        sims = mat @ query_vec.astype(np.float32)
        top = np.argsort(-sims)[:limit]
        return [(int(ids[i]), float(sims[i])) for i in top]

    if vec_ok:
        rows = con.execute(
            """SELECT asset_id, distance FROM vec_images
               WHERE embedding MATCH ? AND k = ?
               ORDER BY distance""",
            (pack_f32(query_vec), limit)).fetchall()
        # vec0 returns L2 distance over unit vectors: sim = 1 - d^2/2
        return [(r[0], 1.0 - (r[1] ** 2) / 2.0) for r in rows]

    rows = con.execute("SELECT asset_id, embedding FROM image_vectors").fetchall()
    if not rows:
        return []
    ids = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
    mat = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float16)
    mat = mat.reshape(len(rows), -1).astype(np.float32)
    sims = mat @ query_vec.astype(np.float32)
    top = np.argpartition(-sims, min(limit, len(sims) - 1))[:limit]
    top = top[np.argsort(-sims[top])]
    return [(int(ids[i]), float(sims[i])) for i in top]


# --------------------------------------------------------------------------
# Where the results stop
# --------------------------------------------------------------------------

# Never show fewer than this, whatever the scores say. A page holding two
# photographs for a reasonable question reads as a broken application rather
# than as an honest answer, and the cost of a few weak extras is far lower
# than the cost of looking empty.
MIN_RESULTS = 12

# How close to the best match a photograph has to score to be called an answer.
#
# Chosen by measurement, and the measurement overturned the first design. That
# was to find the largest fall between neighbouring scores and cut there, on the
# assumption that answers end at a cliff. Real scores have no cliff. Measured
# over this library, 2026-09-09: "snow" runs 0.146 at rank 1 to 0.113 at rank
# 600, decaying smoothly the whole way, and every other query behaves the same.
# There is no gap anywhere to find, so that rule cut nothing at all.
#
# What does separate them is distance from the top, taken as a share so it stays
# comparable across queries whose best scores differ (0.181 for "kids on a beach
# at sunset", 0.132 for "a dog"). At 0.90 this library answers "snow" with 71
# photographs, "birthday cake" with 30, "rippijuhlat" with 23 and "underwater"
# with 8 -- which is the right shape for an archive that has seen a great deal
# of snow and very little diving.
KEEP_FRACTION = 0.90


def cut(scores, *, floor: int = MIN_RESULTS,
        fraction: float = KEEP_FRACTION) -> int:
    """How many of these results are answers (spec §7.3).

    A fixed similarity threshold cannot do this job: the scores are not
    calibrated across queries, so a strong match for an unusual description can
    sit below a weak match for a common one, and one cutoff would over-trim some
    queries while under-trimming others. What is comparable within a query is
    how far each result has fallen behind the best one, and that is what this
    measures.

    Returns the number to show. The remainder is never discarded, only held
    behind "show more".
    """
    n = len(scores)
    if n <= floor:
        return n
    top = float(scores[0])
    if top <= 0:                    # nothing resembles the query at all
        return n
    bar = top * fraction
    keep = 0
    for s in scores:
        if float(s) < bar:
            break
        keep += 1
    return max(keep, floor)
