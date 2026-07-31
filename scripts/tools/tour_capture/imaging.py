"""Lossless-first image validation and review artifact generation."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageOps


class CaptureImageError(RuntimeError):
    """Raised when a generated image violates the approved profile."""


def _clean_pixels(source: Image.Image) -> Image.Image:
    mode = source.mode if source.mode in {"RGB", "RGBA"} else "RGB"
    converted = source.convert(mode)
    return Image.frombytes(mode, converted.size, converted.tobytes())


def optimize_png(
    raw_path: Path,
    optimized_dir: Path,
    expected_dimensions: tuple[int, int],
    quality: int = 92,
) -> Path:
    """Validate a PNG and emit a metadata-free WebP with identical dimensions."""
    optimized_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(raw_path) as source:
        if source.size != expected_dimensions:
            raise CaptureImageError(
                f"{raw_path.name} has dimensions {source.size}; expected {expected_dimensions}"
            )
        clean = _clean_pixels(source)

    output = optimized_dir / f"{raw_path.stem}.webp"
    clean.save(output, "WEBP", quality=quality, method=6, exif=b"")
    with Image.open(output) as result:
        if result.size != expected_dimensions:
            raise CaptureImageError(f"optimized image changed dimensions: {result.size}")
        if result.getexif():
            raise CaptureImageError(f"optimized image retains EXIF metadata: {output.name}")
    return output


def build_contact_sheet(
    scenes: Iterable[tuple[str, Path]],
    output_path: Path,
    columns: int = 3,
    thumb_size: tuple[int, int] = (260, 563),
) -> Path:
    """Create a compact visual review of the ordered scene sequence."""
    scene_list = list(scenes)
    if not scene_list:
        raise CaptureImageError("cannot build a contact sheet without scenes")
    columns = max(1, min(columns, len(scene_list)))
    rows = (len(scene_list) + columns - 1) // columns
    label_height = 34
    gap = 18
    cell_w = thumb_size[0] + gap
    cell_h = thumb_size[1] + label_height + gap
    canvas = Image.new("RGB", (columns * cell_w + gap, rows * cell_h + gap), "#eee3d2")
    draw = ImageDraw.Draw(canvas)

    for index, (label, path) in enumerate(scene_list):
        row, col = divmod(index, columns)
        x = gap + col * cell_w
        y = gap + row * cell_h
        with Image.open(path) as source:
            thumb = ImageOps.contain(source.convert("RGB"), thumb_size)
        canvas.paste(thumb, (x + (thumb_size[0] - thumb.width) // 2, y))
        draw.text((x, y + thumb_size[1] + 8), label, fill="#173f35")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, "WEBP", quality=90, method=6, exif=b"")
    return output_path
