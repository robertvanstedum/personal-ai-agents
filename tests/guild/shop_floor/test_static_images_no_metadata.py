"""Every image the portal serves from /static/guild/ is public (the portal's
static folder has no owner guard), so none may carry metadata: no EXIF (GPS
position, camera, capture time), no XMP, no other APPn block, no comment
(spec §10 Q4, "EXIF stripped"; review of PR #284, F1)."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

REPO = Path(__file__).resolve().parents[3]
FOLDER = REPO / "minimoi_portal" / "static" / "guild"
IMAGES = sorted(p for p in FOLDER.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif"))
GPS_IFD = 0x8825


def _jpeg_segments(data: bytes):
    """(marker, payload) for each header segment before the image data."""
    assert data[:2] == b"\xff\xd8", "not a JPEG"
    i = 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        if marker == 0xDA:            # start of scan: the header is over
            return
        length = int.from_bytes(data[i + 2:i + 4], "big")
        yield marker, data[i + 4:i + 2 + length]
        i += 2 + length


def test_the_folder_holds_the_guild_images():
    names = {p.name for p in IMAGES}
    assert {"guild-build.jpg", "guild-chat-hero.jpg", "guild-mc-portrait.jpg"} <= names


@pytest.mark.parametrize("path", IMAGES, ids=lambda p: p.name)
def test_no_exif_gps_or_other_metadata(path):
    with Image.open(path) as im:
        exif = im.getexif()
        assert len(exif) == 0, f"{path.name} has EXIF tags {sorted(exif)}"
        assert not exif.get_ifd(GPS_IFD), f"{path.name} has a GPS block"
        for key in ("exif", "xmp", "XML:com.adobe.xmp", "photoshop", "comment"):
            assert key not in im.info, f"{path.name} carries {key}"
    data = path.read_bytes()
    assert b"Exif\x00\x00" not in data and b"http://ns.adobe.com/xap" not in data, path.name
    if path.suffix.lower() in (".jpg", ".jpeg"):
        extra = [hex(m) for m, _ in _jpeg_segments(data) if 0xE1 <= m <= 0xEF or m == 0xFE]
        assert not extra, f"{path.name} has metadata segments {extra}"
