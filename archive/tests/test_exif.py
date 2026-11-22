"""Mapping exiftool's output onto the asset columns.

The coordinate tests are here because of a bug that survived a full index of
the library: `EXIF:GPSLongitude` is unsigned, the hemisphere lives in
`EXIF:GPSLongitudeRef`, and a photograph of Adeje on Tenerife was therefore
filed 16.78 degrees *east* -- in the Libyan desert. Nothing downstream could
notice, because an unsigned coordinate is still a valid coordinate.
"""

from __future__ import annotations

import pytest

from mediaidx import exif

ADEJE = {"EXIF:GPSLatitude": 28.1249, "EXIF:GPSLongitude": 16.7772,
         "EXIF:GPSLatitudeRef": "N", "EXIF:GPSLongitudeRef": "W"}


def test_a_western_longitude_is_negative():
    f = exif.to_fields(ADEJE)
    assert (round(f["gps_lat"], 4), round(f["gps_lon"], 4)) == (28.1249, -16.7772)


def test_a_southern_latitude_is_negative():
    f = exif.to_fields({"EXIF:GPSLatitude": 33.86, "EXIF:GPSLongitude": 151.20,
                        "EXIF:GPSLatitudeRef": "S", "EXIF:GPSLongitudeRef": "E"})
    assert (f["gps_lat"], f["gps_lon"]) == (-33.86, 151.20)


def test_the_composite_tag_is_preferred_when_present():
    """exiftool has already applied the reference there, for every format."""
    f = exif.to_fields({**ADEJE, "Composite:GPSLongitude": -16.7772})
    assert round(f["gps_lon"], 4) == -16.7772


def test_a_signed_value_with_no_reference_is_left_alone():
    f = exif.to_fields({"EXIF:GPSLatitude": -33.86, "EXIF:GPSLongitude": -70.66})
    assert (f["gps_lat"], f["gps_lon"]) == (-33.86, -70.66)


def test_a_reference_never_flips_a_value_twice():
    """A file carrying both a signed value and a reference must not end up
    positive again."""
    f = exif.to_fields({"EXIF:GPSLongitude": -16.7772,
                        "EXIF:GPSLongitudeRef": "W"})
    assert round(f["gps_lon"], 4) == -16.7772


def test_no_coordinate_is_not_a_coordinate():
    f = exif.to_fields({"EXIF:Make": "Apple"})
    assert f["gps_lat"] is None and f["gps_lon"] is None


@pytest.mark.parametrize("ref", ["", None, "  ", "Q"])
def test_a_missing_or_odd_reference_does_not_lose_the_coordinate(ref):
    f = exif.to_fields({"EXIF:GPSLatitude": 60.17, "EXIF:GPSLatitudeRef": ref})
    assert f["gps_lat"] == 60.17


def test_the_rest_of_the_mapping_still_works():
    f = exif.to_fields({"File:ImageWidth": "4032", "File:ImageHeight": "3024",
                        "EXIF:Orientation": "6", "EXIF:Make": "Apple",
                        "EXIF:FNumber": "1.78", "File:FileType": "HEIC"})
    assert (f["width"], f["height"], f["orientation"]) == (4032, 3024, 6)
    assert f["camera_make"] == "Apple" and f["f_number"] == 1.78
    assert f["format"] == "heic"
