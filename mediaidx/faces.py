"""Stage 2: finding faces and turning each one into a 512-d vector.

Detection and recognition are separate models and are run separately here,
rather than through insightface's one-call `FaceAnalysis.get`. Measured on a
random sample of this library, 46% of detections fail the score or size filter
-- background strangers, faces in a photograph on a wall, half a head at the
frame edge. Embedding those and discarding them afterwards is wasted work, and
worse, it invites the mistake of storing them. So: detect, filter, then embed
only what survived, in one batched call per photograph.

The filters matter more than they look. A 30-pixel face carries too little
detail to embed reliably, and a library-wide pass produces thousands of them;
left in, they cluster with each other rather than with the person they belong
to, and the review queue fills with clusters nobody can name.
"""

from __future__ import annotations

import contextlib
import io
from dataclasses import dataclass

import numpy as np

from .embed import pack, pack_f32, unpack  # noqa: F401  (float16 on disk, float32 for vec0)

STAGE = "faces"
STAGE_VERSION = 1

DEFAULT_MODEL = "buffalo_l"     # SCRFD detector + ArcFace R100 embedder
DIM = 512
DET_SIZE = 640
MIN_DET_SCORE = 0.5
MIN_FACE_PX = 40


def preload_cuda() -> bool:
    """Point onnxruntime at the CUDA wheels it was built for.

    onnxruntime-gpu 1.29 is built against CUDA 13; the PyTorch in this
    environment carries CUDA 12.8. Left alone, onnxruntime searches PyTorch's
    lib directory first, fails to resolve `cublasLt64_13.dll`, and falls back
    to the CPU -- silently, which is the real problem. A 40,000-image pass
    would simply run seven times slower with nothing in the output to say why.

    The empty `directory` means "use the nvidia site-packages wheels", which
    are the ones this onnxruntime asked for. Returns whether the call was
    available at all; a CPU-only install is still perfectly usable, just slow.
    """
    try:
        import onnxruntime as ort
        ort.preload_dlls(directory="")
        return True
    except Exception:
        return False


def cuda_available() -> bool:
    try:
        import onnxruntime as ort
        return "CUDAExecutionProvider" in ort.get_available_providers()
    except Exception:
        return False


@dataclass
class DetectedFace:
    """One surviving face.

    `bbox` is normalised to the image's own dimensions, not stored in pixels.
    Pixel coordinates would be measured against whatever size the image
    happened to be decoded at (1600 px here, but 320 or 1024 in the thumbnail
    cache and anything at all in the original), and every later consumer would
    have to know which. Fractions are true against all of them.
    """
    bbox: tuple[float, float, float, float]     # x, y, w, h in 0..1
    det_score: float
    blur: float
    embedding: np.ndarray                        # 512, L2-normalised, float32


def _blur_score(aligned_bgr) -> float:
    """Variance of the Laplacian over the aligned 112x112 crop.

    Computed on the aligned crop rather than the original pixels so the number
    means the same thing for a face 500 px across and one 50 px across. It is
    a relative measure, used to pick the sharpest face to represent a cluster.
    """
    import cv2
    grey = cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


@dataclass
class Detector:
    model_name: str = DEFAULT_MODEL
    det_size: int = DET_SIZE
    min_det_score: float = MIN_DET_SCORE
    min_face_px: int = MIN_FACE_PX
    device: str = "cuda"

    def __post_init__(self):
        preload_cuda()
        import warnings
        # insightface calls a scikit-image API that was deprecated under it.
        # Nothing here can act on the notice, and it lands in the middle of a
        # progress line during a run of tens of thousands of images.
        warnings.filterwarnings("ignore", message=r".*`estimate` is deprecated.*",
                                category=FutureWarning)
        from insightface.app import FaceAnalysis

        if self.device == "cuda" and not cuda_available():
            self.device = "cpu"
        providers = (["CUDAExecutionProvider", "CPUExecutionProvider"]
                     if self.device == "cuda" else ["CPUExecutionProvider"])

        # Only two of buffalo_l's five models are wanted. Age, gender and the
        # landmark models are loaded by default and used by nothing here.
        # insightface narrates its model loading to stdout; the CLI has its own
        # opinion about what belongs on the terminal.
        with contextlib.redirect_stdout(io.StringIO()):
            app = FaceAnalysis(name=self.model_name,
                               allowed_modules=["detection", "recognition"],
                               providers=providers)
            app.prepare(ctx_id=0 if self.device == "cuda" else -1,
                        det_size=(self.det_size, self.det_size))

        self._det = app.models["detection"]
        self._rec = app.models["recognition"]

    @property
    def tag(self) -> str:
        return self.model_name

    @property
    def provider(self) -> str:
        return self._det.session.get_providers()[0]

    def detect(self, im) -> list[DetectedFace]:
        """Faces in a PIL image, already upright and downscaled."""
        from insightface.utils import face_align

        w, h = im.size
        bgr = np.ascontiguousarray(np.asarray(im)[:, :, ::-1])   # PIL RGB -> BGR
        bboxes, kpss = self._det.detect(bgr, max_num=0, metric="default")
        if bboxes is None or len(bboxes) == 0 or kpss is None:
            return []

        keep = []
        for i in range(bboxes.shape[0]):
            x1, y1, x2, y2 = (float(v) for v in bboxes[i, :4])
            score = float(bboxes[i, 4])
            if score < self.min_det_score:
                continue
            if min(x2 - x1, y2 - y1) < self.min_face_px:
                continue
            keep.append((i, (x1, y1, x2, y2), score))
        if not keep:
            return []

        size = self._rec.input_size[0]
        crops = [face_align.norm_crop(bgr, landmark=kpss[i], image_size=size)
                 for i, _, _ in keep]
        feats = self._rec.get_feat(crops)         # one batched session run

        out = []
        for (_, (x1, y1, x2, y2), score), crop, feat in zip(keep, crops, feats):
            v = np.asarray(feat, dtype=np.float32).flatten()
            n = float(np.linalg.norm(v))
            v = v / n if n else v
            out.append(DetectedFace(
                bbox=(x1 / w, y1 / h, (x2 - x1) / w, (y2 - y1) / h),
                det_score=score,
                blur=_blur_score(crop),
                embedding=v,
            ))
        return out


@dataclass
class AgeEstimator:
    """buffalo_l's genderage head, run on the cached face thumbnail.

    Deliberately not part of `Detector`: that class is loaded once per
    40,000-image pass and detection+recognition are the only models worth
    their GPU time there (see the module docstring). Age is wanted for faces
    already detected and embedded -- 27,000-odd of them before this existed --
    and re-running detection over the whole archive just to get it would mean
    reading every photograph off the NAS a second time. The cached thumbnail
    already sits on local disk from the first pass, so this works from that
    instead and never touches the archive again.

    The trade-off: `Attribute.get` wants the asset's own bbox against the full
    frame, to crop and scale around it. The thumbnail is already that crop,
    at `images.FACE_MARGIN` margin and a known size (`images.py`), so the
    original bbox is reconstructed from that geometry rather than read from
    disk -- there is nothing else to align to, and a rough estimate is all
    this was ever asked for (spec: relative ordering, not absolute age).
    """
    model_name: str = DEFAULT_MODEL
    device: str = "cuda"

    def __post_init__(self):
        preload_cuda()
        from insightface.app import FaceAnalysis

        if self.device == "cuda" and not cuda_available():
            self.device = "cpu"
        providers = (["CUDAExecutionProvider", "CPUExecutionProvider"]
                     if self.device == "cuda" else ["CPUExecutionProvider"])

        # FaceAnalysis insists on a detection model being present (it asserts
        # so in its own __init__) even though it is never called here -- only
        # genderage.get() runs, against the geometry reconstructed in
        # estimate(). The unused detector is dead weight in memory, not in
        # time: it is loaded, never invoked.
        with contextlib.redirect_stdout(io.StringIO()):
            app = FaceAnalysis(name=self.model_name,
                               allowed_modules=["detection", "genderage"],
                               providers=providers)
            app.prepare(ctx_id=0 if self.device == "cuda" else -1,
                        det_size=(DET_SIZE, DET_SIZE))

        self._model = app.models["genderage"]

    def estimate(self, thumb) -> tuple[int, int] | None:
        """(gender, age) from a square face thumbnail, or None on failure.

        `thumb` is the cached crop `images.write_face_thumb` wrote: a square
        PIL image, the face centred in it with `images.FACE_MARGIN` margin
        on every side. That geometry is inverted here to the bbox
        `Attribute.get` expects, in the thumbnail's own pixel space -- no
        other image exists to compute a real one from.
        """
        from insightface.app.common import Face
        from .images import FACE_MARGIN
        import numpy as np

        side = min(thumb.size)
        face_side = side / (1 + 2 * FACE_MARGIN)
        pad = (side - face_side) / 2
        bbox = np.array([pad, pad, pad + face_side, pad + face_side])

        bgr = np.ascontiguousarray(np.asarray(thumb.convert("RGB"))[:, :, ::-1])
        try:
            gender, age = self._model.get(bgr, Face(bbox=bbox))
        except Exception:
            return None
        return int(gender), int(age)


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------

def ensure_vec_table(con) -> bool:
    from . import embed
    if not embed.load_vec_extension(con):
        return False
    con.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_faces "
        f"USING vec0(face_id INTEGER PRIMARY KEY, embedding float[{DIM}])")
    return True


def replace_for_asset(con, asset_id: int, faces: list[DetectedFace], *,
                      vec_ok: bool) -> None:
    """Write one asset's faces, replacing anything previously detected in it.

    Re-running the stage -- a new model version, a changed threshold -- must
    not leave the old faces behind alongside the new ones, so the asset's rows
    are cleared first. `idx` is the face's position within its photograph and
    is what names its cropped thumbnail on disk, so the crop stays valid across
    a re-run that hands out different row ids.
    """
    import json
    from . import corrections as corrections_mod

    # Whose faces these were. `person_id` lives on the rows about to be deleted,
    # so without this a re-run of the stage would take every name in the library
    # with it -- 19,232 of them here, and weeks of somebody's evenings. Identity
    # is carried across by where a face sits in the frame, because `idx` is only
    # the detector's output order and means nothing between runs (P8, spec 5.2).
    old = [dict(r) for r in con.execute(
        "SELECT id, idx, bbox, person_id, cluster_id FROM faces "
        "WHERE asset_id = ?", (asset_id,))]
    if old:
        con.execute("DELETE FROM faces WHERE asset_id = ?", (asset_id,))
        if vec_ok:
            con.executemany("DELETE FROM vec_faces WHERE face_id = ?",
                            [(r["id"],) for r in old])

    new_boxes = []
    for idx, f in enumerate(faces):
        cur = con.execute(
            """INSERT INTO faces
                 (asset_id, idx, bbox, det_score, blur_score, embedding)
               VALUES (?,?,?,?,?,?)""",
            (asset_id, idx, json.dumps([round(v, 6) for v in f.bbox]),
             f.det_score, f.blur, pack(f.embedding)))
        if vec_ok:
            con.execute("INSERT INTO vec_faces (face_id, embedding) VALUES (?,?)",
                        (cur.lastrowid, pack_f32(f.embedding)))
        new_boxes.append((idx, list(f.bbox)))

    carried = lost = 0
    named = [r for r in old if r["person_id"] or r["cluster_id"]]
    if named:
        taken: set = set()
        for r in named:
            box = json.loads(r["bbox"])
            best, best_iou = None, corrections_mod.MIN_IOU
            for idx, nb in new_boxes:
                if idx in taken:
                    continue
                score = corrections_mod.bbox_iou(box, nb)
                if score >= best_iou:
                    best, best_iou = idx, score
            if best is None:
                # The detector no longer finds a face there, so there is nothing
                # left to hold the name on. Counted, never passed over silently.
                lost += 1
                continue
            taken.add(best)
            con.execute(
                "UPDATE faces SET person_id = ?, cluster_id = ? "
                "WHERE asset_id = ? AND idx = ?",
                (r["person_id"], r["cluster_id"], asset_id, best))
            carried += 1

    h = con.execute("SELECT content_hash FROM assets WHERE id = ?",
                    (asset_id,)).fetchone()
    if h is not None:
        corrections_mod.reanchor_faces(con, h["content_hash"], new_boxes)
    return carried, lost
