"""Decoding and thumbnails.

Every image passes through `load()`, which is the only place orientation is
applied. Measured on the reference library, 22% of assets carry a non-default
EXIF orientation -- overwhelmingly value 6, a 90 degree rotation from a phone
held upright. Skipping the correction costs little in embedding quality (SigLIP
is fairly rotation-robust) but shows sideways thumbnails to the user, and face
detection in M3 is far less forgiving than CLIP.

Thumbnails go to a local cache keyed by content hash, never to the archive and
never over the network twice. Browsing 232 GB of originals across the link is
not viable at any speed, let alone this one.
"""

from __future__ import annotations

import os
from pathlib import Path

import pillow_heif
from PIL import Image, ImageOps

pillow_heif.register_heif_opener()

# Pillow refuses very large images by default as a decompression-bomb guard.
# Family archives contain legitimate panoramas well past the limit.
Image.MAX_IMAGE_PIXELS = 500_000_000

THUMB_SIZES = {"s": 320, "m": 1024}

# A face crop is saved while the photograph is already in memory. The
# alternative is to crop faces when the review UI asks for them, which means
# reading 39,000 originals back across the link to build one screen of
# thumbnails -- exactly the second pass the whole design exists to avoid (P4).
FACE_THUMB = 160
FACE_MARGIN = 0.4       # a face alone is hard to recognise; give it some room


def load(path: str, *, max_side: int | None = None) -> Image.Image:
    """Open an image upright, in RGB, optionally downscaled."""
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)      # the whole reason this function exists
    if im.mode != "RGB":
        im = im.convert("RGB")
    if max_side and max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    return im


def thumb_path(cache_dir: Path, content_hash: str, size: str) -> Path:
    """Shard by the first two hex characters so no directory holds 46,000 files."""
    return cache_dir / size / content_hash[:2] / f"{content_hash}.webp"


def write_thumbs(im: Image.Image, cache_dir: Path, content_hash: str) -> None:
    for name, side in THUMB_SIZES.items():
        out = thumb_path(cache_dir, content_hash, name)
        if out.exists():
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        t = im.copy()
        t.thumbnail((side, side), Image.LANCZOS)
        tmp = out.with_suffix(".tmp")
        t.save(tmp, "WEBP", quality=82, method=4)
        os.replace(tmp, out)              # atomic: a killed run leaves no partial file


def has_thumbs(cache_dir: Path, content_hash: str) -> bool:
    return all(thumb_path(cache_dir, content_hash, s).exists() for s in THUMB_SIZES)


def face_thumb_path(cache_dir: Path, content_hash: str, idx: int) -> Path:
    """Named by hash and position, never by face row id.

    Re-running detection assigns new row ids; the photograph and the position
    of a face within it do not change, so the crop written today is still the
    right file after a rebuild.
    """
    return cache_dir / "faces" / content_hash[:2] / f"{content_hash}-{idx}.webp"


def write_face_thumb(im: Image.Image, cache_dir: Path, content_hash: str,
                     idx: int, bbox: tuple[float, float, float, float]) -> None:
    """Square crop around a face given as fractions of the image.

    At most FACE_THUMB across and never upscaled: a 60-pixel face enlarged to
    160 is the same face with invented pixels around it, at four times the
    size on disk. The review UI can size its tiles in CSS.
    """
    out = face_thumb_path(cache_dir, content_hash, idx)
    if out.exists():
        return
    W, H = im.size
    x, y, w, h = bbox[0] * W, bbox[1] * H, bbox[2] * W, bbox[3] * H
    cx, cy = x + w / 2, y + h / 2
    # The side is rounded to whole pixels before the corner is placed, so the
    # crop is square by construction rather than square give or take a pixel.
    side = int(min(max(w, h) * (1 + 2 * FACE_MARGIN), W, H))
    # Clamp to the image rather than padding: a face at the frame edge gets an
    # off-centre crop, which is preferable to a black border around it.
    left = int(max(0, min(W - side, cx - side / 2)))
    top = int(max(0, min(H - side, cy - side / 2)))

    out.parent.mkdir(parents=True, exist_ok=True)
    crop = im.crop((left, top, left + side, top + side))
    crop.thumbnail((FACE_THUMB, FACE_THUMB), Image.LANCZOS)
    tmp = out.with_suffix(".tmp")
    crop.save(tmp, "WEBP", quality=82, method=4)
    os.replace(tmp, out)
