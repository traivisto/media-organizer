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


def search(con, query_vec: np.ndarray, limit: int = 60, *,
           vec_ok: bool) -> list[tuple[int, float]]:
    """Return (asset_id, similarity) best first."""
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
