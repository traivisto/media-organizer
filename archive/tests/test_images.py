"""Decoding, orientation and the thumbnail cache."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from mediaidx import images


def _jpeg_with_orientation(path, orientation: int, size=(80, 40)):
    """A landscape image tagged as needing rotation, the way a phone writes it."""
    im = Image.new("RGB", size, (200, 40, 40))
    exif = im.getexif()
    exif[0x0112] = orientation          # 0x0112 = Orientation
    im.save(path, "JPEG", exif=exif)


def test_orientation_6_is_applied(tmp_path):
    """Value 6 is a 90-degree rotation and is 80 of the 87 rotated samples."""
    p = tmp_path / "rot.jpg"
    _jpeg_with_orientation(p, 6, size=(80, 40))
    assert Image.open(p).size == (80, 40)        # as stored: landscape
    assert images.load(str(p)).size == (40, 80)  # as intended: portrait


def test_orientation_1_is_left_alone(tmp_path):
    p = tmp_path / "up.jpg"
    _jpeg_with_orientation(p, 1, size=(80, 40))
    assert images.load(str(p)).size == (80, 40)


def test_missing_orientation_is_not_an_error(tmp_path):
    p = tmp_path / "plain.jpg"
    Image.new("RGB", (60, 30)).save(p, "JPEG")
    assert images.load(str(p)).size == (60, 30)


def test_load_converts_to_rgb(tmp_path):
    p = tmp_path / "grey.png"
    Image.new("L", (20, 20), 128).save(p)
    assert images.load(str(p)).mode == "RGB"


def test_max_side_downscales_preserving_aspect(tmp_path):
    p = tmp_path / "big.jpg"
    Image.new("RGB", (4000, 2000)).save(p, "JPEG")
    out = images.load(str(p), max_side=1000)
    assert max(out.size) == 1000 and out.size == (1000, 500)


def test_thumbnails_are_sharded_and_named_by_hash(tmp_path):
    h = "abcdef0123456789" * 4
    p = images.thumb_path(tmp_path, h, "s")
    assert p.parent.name == "ab"        # no directory holds 46,000 files
    assert p.name == f"{h}.webp"
    assert p.parent.parent.name == "s"


def test_write_thumbs_creates_every_size_and_is_idempotent(tmp_path):
    src = tmp_path / "src.jpg"
    Image.new("RGB", (2000, 1000), (10, 120, 90)).save(src, "JPEG")
    cache = tmp_path / "cache"
    h = "0" * 64

    images.write_thumbs(images.load(str(src)), cache, h)
    assert images.has_thumbs(cache, h)
    for name, side in images.THUMB_SIZES.items():
        with Image.open(images.thumb_path(cache, h, name)) as t:
            assert max(t.size) == side
            assert t.format == "WEBP"

    stamps = {n: images.thumb_path(cache, h, n).stat().st_mtime_ns
              for n in images.THUMB_SIZES}
    images.write_thumbs(images.load(str(src)), cache, h)
    assert {n: images.thumb_path(cache, h, n).stat().st_mtime_ns
            for n in images.THUMB_SIZES} == stamps, "existing thumbnails rewritten"


def test_no_temp_files_left_behind(tmp_path):
    src = tmp_path / "src.jpg"
    Image.new("RGB", (400, 400)).save(src, "JPEG")
    cache = tmp_path / "cache"
    images.write_thumbs(images.load(str(src)), cache, "f" * 64)
    assert not list(cache.rglob("*.tmp"))
