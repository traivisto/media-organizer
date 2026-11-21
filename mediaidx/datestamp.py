"""Reading a camera's own printed date stamp off a scan.

Only for photographs EXIF and the filename could not date (`dates.resolve`
already tried and fell through to `folder` or `mtime`) -- a real digital photo
almost always carries EXIF, so this never runs on one. See `datestamp_pass.py`
for that gate.

Two steps, deliberately kept apart:

    locate   a small local YOLO model (pike00/yolo-date-stamp-detector, run
             for its bounding box only -- see the licence note in
             pyproject.toml) finds the stamp, or finds nothing.
    read     the cropped stamp -- not the photograph -- is sent to two
             independent local vision models (Ollama). Only a date both of
             them agree on, letter for letter, is trusted.

That second step is the actual safety net. A colour/shape heuristic was tried
first and rejected: skin tone sits in the same colour range as an orange LED
digit closely enough that no threshold separated them reliably (see the
project's own notes). A single reading model has the same problem one level
up -- it confidently misreads an 8 as a 0 often enough (observed on this
library: ~1 in 8 crops) that a plausibility check alone (month 1-12, day
1-31) does not catch it, because the wrong digit is often still a valid date.
Two independent models agreeing on every digit is cheap here (both already
installed, running locally) and catches far more than validity ever could.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

import requests

from . import dates

STAGE = "datestamp"
STAGE_VERSION = 1

YOLO_REPO = "pike00/yolo-date-stamp-detector"
YOLO_FILE = "best.pt"
DEFAULT_CONF = 0.6
CROP_PAD = 10

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODELS = ("gemma4:12b", "qwen3.5:9b")
OLLAMA_TIMEOUT = 60

PROMPT = (
    "This is a cropped close-up of a camera-printed digital date stamp "
    "(orange/red LED-style digits) burned into the corner of a scanned "
    "photograph. Read ONLY the digits you see, in the groups they appear "
    "in (there are usually three groups: year, month, day, in whatever "
    "order they are printed), separated by spaces, e.g. '89 6 25'. Keep "
    "any apostrophe exactly where it appears, e.g. \"30 4'93\". If you "
    "cannot confidently read it, reply exactly: UNREADABLE. Do not "
    "explain, just the digits or UNREADABLE."
)


@dataclass
class Locator:
    """pike00/yolo-date-stamp-detector, for its bounding box only.

    CPU by design, not a stopgap: this project's torch/torchvision pairing
    throws `NotImplementedError` for `torchvision::nms` on CUDA (a build
    mismatch orthogonal to onnxruntime's own CUDA wheels used elsewhere in
    this codebase). The model is small enough that CPU is not a cost worth
    chasing GPU support for.
    """
    conf: float = DEFAULT_CONF
    _model: object = field(default=None, init=False, repr=False)

    def __post_init__(self):
        from huggingface_hub import hf_hub_download
        from ultralytics import YOLO
        weights = hf_hub_download(YOLO_REPO, YOLO_FILE)
        self._model = YOLO(weights)

    def locate(self, path: str):
        """(x0, y0, x1, y1, confidence) in pixels, or None."""
        result = self._model(path, conf=self.conf, device="cpu", verbose=False)[0]
        if len(result.boxes) == 0:
            return None
        box = max(result.boxes, key=lambda b: float(b.conf[0]))
        x0, y0, x1, y1 = (int(v) for v in box.xyxy[0])
        return x0, y0, x1, y1, float(box.conf[0])


def crop_for_reading(path: str, box):
    """The stamp plus a small margin, as a PIL image -- never the full photo.

    Read fresh from `path` at full resolution, not from whatever downscaled
    copy a caller may have decoded for another stage: the YOLO box is in the
    original file's pixel space, and a handful of LED digits is exactly the
    kind of small detail a 1600px working copy (sized for face detection)
    would blur away.
    """
    from PIL import Image
    im = Image.open(path).convert("RGB")
    x0, y0, x1, y1, _ = box
    w, h = im.size
    x0, y0 = max(x0 - CROP_PAD, 0), max(y0 - CROP_PAD, 0)
    x1, y1 = min(x1 + CROP_PAD, w), min(y1 + CROP_PAD, h)
    crop = im.crop((x0, y0, x1, y1))
    scale = max(1, 300 // max(crop.height, 1))
    if scale > 1:
        crop = crop.resize((crop.width * scale, crop.height * scale))
    return crop


def ask_model(model: str, png_bytes: bytes) -> str:
    import base64
    b64 = base64.b64encode(png_bytes).decode("ascii")
    resp = requests.post(
        OLLAMA_URL,
        json={
            "model": model,
            "prompt": PROMPT,
            "images": [b64],
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "num_predict": 64},
        },
        timeout=OLLAMA_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["response"].strip()


def ollama_available() -> bool:
    try:
        requests.get(OLLAMA_URL.rsplit("/api/", 1)[0] + "/api/tags", timeout=3)
        return True
    except requests.RequestException:
        return False


# --------------------------------------------------------------------------
# Parsing a model's raw answer into a date
# --------------------------------------------------------------------------

_GROUP = re.compile(r"(['’]?)(\d{1,4})")


def parse_groups(raw: str) -> datetime | None:
    """Three digit groups, in whatever order this camera prints them, to a date.

    The grouping is printed by the camera, not chosen by the reader, so three
    conventions are all seen in one archive: `YY MM DD`, `DD MM 'YY` (the
    apostrophe marking the abbreviated year), and a bare 4-digit year. Only
    one group can be >31 or carry the apostrophe or be 4 digits -- that one is
    the year. Of the other two, whichever is >12 must be the day; if neither
    is, the printed order (year first means month-then-day; year last means
    day-then-month) breaks the tie, which is the convention actually observed
    on every stamp in this archive bar the two tested.
    """
    if raw.strip().upper() == "UNREADABLE":
        return None
    groups = _GROUP.findall(raw)
    if len(groups) != 3:
        return None

    nums = [int(g[1]) for g in groups]
    marks = [bool(g[0]) for g in groups]

    year_idx = None
    if any(marks):
        year_idx = marks.index(True)
    elif any(n > 99 for n in nums):
        year_idx = next(i for i, n in enumerate(nums) if n > 99)
    elif sum(1 for n in nums if n > 31) == 1:
        year_idx = next(i for i, n in enumerate(nums) if n > 31)
    else:
        return None   # no group can be distinguished as the year

    year = nums[year_idx]
    year += 1900 if year < 100 else 0
    rest_idx = [i for i in range(3) if i != year_idx]
    a, b = nums[rest_idx[0]], nums[rest_idx[1]]

    if a > 12 and b <= 12:
        day, month = a, b
    elif b > 12 and a <= 12:
        day, month = b, a
    elif a <= 12 and b <= 12:
        # Both could be either. Break the tie by where the year sits: this
        # archive's `YY MM DD` stamps put the year first, `DD MM 'YY` put it
        # last -- so the remaining two keep that same month-then-day or
        # day-then-month order relative to the year's position.
        if year_idx == 0:
            month, day = a, b
        else:
            day, month = a, b
    else:
        return None   # both >12 -- not a valid day or month in either slot

    try:
        dt = datetime(year, month, day)
    except ValueError:
        return None
    return dates.valid_stamp_date(dt)


@dataclass
class StampResult:
    box_conf: float
    date: datetime | None
    agreed: bool
    raw: dict = field(default_factory=dict)   # {model: raw answer}, for `doctor`/debugging


def read_stamp(path: str, locator: Locator, *,
               models=DEFAULT_MODELS) -> StampResult | None:
    """Locate, then read with every model in `models`; a date only if they agree."""
    box = locator.locate(path)
    if box is None:
        return None

    crop = crop_for_reading(path, box)
    import io
    buf = io.BytesIO()
    crop.save(buf, "PNG")
    png_bytes = buf.getvalue()

    raw = {}
    parsed = []
    for model in models:
        try:
            answer = ask_model(model, png_bytes)
        except requests.RequestException as e:
            raw[model] = f"ERROR: {e}"
            continue
        raw[model] = answer
        parsed.append(parse_groups(answer))

    if len(parsed) < len(models) or any(p is None for p in parsed):
        return StampResult(box_conf=box[4], date=None, agreed=False, raw=raw)

    agreed = all(p == parsed[0] for p in parsed[1:])
    return StampResult(box_conf=box[4], date=parsed[0] if agreed else None,
                       agreed=agreed, raw=raw)
