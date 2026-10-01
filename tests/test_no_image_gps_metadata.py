"""Guard: bundled images must not carry camera location (GPS) or EXIF/XMP metadata.

Phone photos embed GPS coordinates in EXIF. Images under /static are served
publicly, so they must be stripped before being committed. To fix a failure,
strip the metadata losslessly (JPEG: drop APP1/APP13 segments; PNG: drop
eXIf/iTXt chunks; WebP: drop EXIF/XMP chunks) or re-save without metadata.
"""
import os
import struct
import subprocess

import pytest
from PIL import Image

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic")
SKIP_DIRS = {"node_modules", "venv", ".venv", "_working", ".git"}
GPS_IFD = 0x8825


def _tracked_images():
    try:
        out = subprocess.check_output(
            ["git", "ls-files", "-z"], cwd=REPO, stderr=subprocess.DEVNULL
        ).decode("utf-8", "surrogateescape")
        paths = [p for p in out.split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        paths = []
        for root, dirs, names in os.walk(REPO):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            paths += [os.path.relpath(os.path.join(root, n), REPO) for n in names]
    return sorted(
        p
        for p in paths
        if p.lower().endswith(IMAGE_EXTS)
        and not (set(p.split("/")) & SKIP_DIRS)
        and os.path.isfile(os.path.join(REPO, p))
    )


def _jpeg_metadata_segments(data):
    """Marker bytes of APP1 (EXIF/XMP) and APP13 segments before the scan data."""
    found, i = [], 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        if marker in (0xDA, 0xD9):
            break
        if marker == 0xFF:
            i += 1
            continue
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            i += 2
            continue
        if marker in (0xE1, 0xED):
            found.append(marker)
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return found


def _problems(path):
    full = os.path.join(REPO, path)
    problems = []
    with Image.open(full) as im:
        if im.getexif().get_ifd(GPS_IFD):
            problems.append("GPS IFD")
    with open(full, "rb") as fh:
        data = fh.read()
    low = path.lower()
    if low.endswith((".jpg", ".jpeg")) and data[:2] == b"\xff\xd8":
        if _jpeg_metadata_segments(data):
            problems.append("JPEG APP1/APP13 metadata")
        # A second embedded JPEG that carries its own APP1 (e.g. a gain map).
        if data.find(b"\xff\xd8\xff\xe1", 2) != -1:
            problems.append("embedded JPEG with APP1 metadata")
    elif low.endswith(".png") and data[:8] == b"\x89PNG\r\n\x1a\n":
        for tag in (b"eXIf", b"iTXt"):
            if _png_has_chunk(data, tag):
                problems.append("PNG %s chunk" % tag.decode())
    elif low.endswith(".webp") and data[:4] == b"RIFF":
        for tag in (b"EXIF", b"XMP "):
            if _webp_has_chunk(data, tag):
                problems.append("WebP %s chunk" % tag.decode().strip())
    return problems


def _png_has_chunk(data, tag):
    i = 8
    while i + 8 <= len(data):
        length = struct.unpack(">I", data[i:i + 4])[0]
        if data[i + 4:i + 8] == tag:
            return True
        i += 12 + length
    return False


def _webp_has_chunk(data, tag):
    i = 12
    while i + 8 <= len(data):
        length = struct.unpack("<I", data[i + 4:i + 8])[0]
        if data[i:i + 4] == tag:
            return True
        i += 8 + length + (length & 1)
    return False


def test_images_found():
    assert _tracked_images(), "expected to find bundled images"


def test_no_image_has_gps_or_exif_metadata():
    offenders = {}
    for path in _tracked_images():
        found = _problems(path)
        if found:
            offenders[path] = found
    assert not offenders, (
        "Images carry camera metadata (possibly location). Strip it before committing:\n"
        + "\n".join("  %s: %s" % (p, ", ".join(v)) for p, v in sorted(offenders.items()))
    )
