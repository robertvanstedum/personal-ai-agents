"""Bundle one or more capture runs into a single review PDF.

Usage:
    python -m scripts.tools.tour_capture.review_pdf RUN_DIR [RUN_DIR ...] \\
        -o OUT.pdf [--title "..."]

Each run directory is one ``tour_capture`` output folder containing
``manifest.json`` and ``report.json``. The PDF gets a cover page, then one
page per scene in run order: the screenshot scaled to fit without distortion
plus a caption band with the scene title, description, device profile,
viewport, and capture time. Pages are rendered with Pillow only.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .scenario import DEVICE_PROFILES


# A4 at 200 dpi; tall screenshots get a portrait page, wide ones landscape.
DPI = 200
LANDSCAPE = (2339, 1654)
PORTRAIT = (1654, 2339)
MARGIN = 70
CAPTION_HEIGHT = 300
BACKGROUND = "#fffaf1"
BAND = "#eee3d2"
INK = "#173f35"
MUTED = "#6b6257"
LOCAL_NOTE = (
    "These captures were taken locally for review. They may show simulated "
    "or sample data and are not a record of production state."
)


class ReviewPdfError(RuntimeError):
    """Raised when a run directory cannot be turned into review pages."""


@dataclass(frozen=True)
class ReviewScene:
    run_dir: Path
    scenario: str
    order: int
    title: str
    description: str
    image_path: Path
    profile: str
    viewport: str
    captured_at: str


@dataclass(frozen=True)
class ReviewRun:
    run_dir: Path
    scenario: str
    base_url: str
    started_at: str
    scenes: list[ReviewScene]


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf")
        if bold
        else ("DejaVuSans.ttf", "Arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf")
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 has no sized default font
        return ImageFont.load_default()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReviewPdfError(f"missing {path.name} in {path.parent}") from exc
    except json.JSONDecodeError as exc:
        raise ReviewPdfError(f"invalid JSON in {path}") from exc
    if not isinstance(payload, dict):
        raise ReviewPdfError(f"{path} must contain a JSON object")
    return payload


def _viewport_label(manifest: dict[str, Any]) -> str:
    viewport = manifest.get("viewport")
    if not viewport:
        known = DEVICE_PROFILES.get(manifest.get("profile", ""))
        if known:
            viewport = {
                "width": known.width,
                "height": known.height,
                "device_scale_factor": known.device_scale_factor,
            }
    if not viewport:
        return "unknown viewport"
    return (
        f"{viewport['width']}×{viewport['height']} "
        f"@{viewport['device_scale_factor']}x"
    )


def _scene_image(run_dir: Path, scene: dict[str, Any]) -> Path:
    for key in ("raw", "optimized"):
        relative = scene.get(key)
        if not relative:
            continue
        candidate = (run_dir / relative).resolve()
        if run_dir.resolve() in candidate.parents and candidate.is_file():
            return candidate
    raise ReviewPdfError(f"no image found for scene {scene.get('order')} in {run_dir}")


def load_run(run_dir: Path | str) -> ReviewRun:
    """Read one capture run's manifest and report into ordered review scenes."""
    run_dir = Path(run_dir)
    manifest = _read_json(run_dir / "manifest.json")
    report = _read_json(run_dir / "report.json")
    if report.get("status") not in (None, "complete"):
        raise ReviewPdfError(f"run did not complete: {run_dir} ({report.get('status')})")

    scenario = manifest.get("scenario") or report.get("scenario") or run_dir.parent.name
    profile = manifest.get("profile", "unknown")
    viewport = _viewport_label(manifest)
    fallback_time = report.get("completed_at") or report.get("started_at") or ""
    scenes = [
        ReviewScene(
            run_dir=run_dir,
            scenario=scenario,
            order=int(item.get("order", index)),
            title=item.get("title", ""),
            description=item.get("description", ""),
            image_path=_scene_image(run_dir, item),
            profile=profile,
            viewport=viewport,
            captured_at=item.get("captured_at") or fallback_time,
        )
        for index, item in enumerate(manifest.get("scenes", []), start=1)
    ]
    scenes.sort(key=lambda scene: scene.order)
    return ReviewRun(
        run_dir=run_dir,
        scenario=scenario,
        base_url=report.get("base_url", ""),
        started_at=report.get("started_at", ""),
        scenes=scenes,
    )


def _format_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value or "unknown time"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    """Greedy word wrap measured with the real font, so bold or wide text fits."""
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}".strip()
            if not current or draw.textlength(candidate, font=font) <= width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _fit_lines(draw: ImageDraw.ImageDraw, text: str, font, width: int, max_lines: int) -> list[str]:
    """Wrap, then cap the line count; the last kept line ends with an ellipsis."""
    lines = _wrap(draw, text, font, width)
    if len(lines) <= max_lines:
        return lines
    kept = lines[:max_lines]
    last = kept[-1]
    while last and draw.textlength(last + " …", font=font) > width:
        last = last[:-1]
    kept[-1] = last.rstrip() + " …"
    return kept


def _cover_page(title: str, runs: list[ReviewRun]) -> Image.Image:
    page = Image.new("RGB", LANDSCAPE, BACKGROUND)
    draw = ImageDraw.Draw(page)
    x, y = MARGIN * 2, MARGIN * 2
    width = LANDSCAPE[0] - x * 2
    title_font = _font(96, bold=True)
    for line in _fit_lines(draw, title, title_font, width, 3):
        draw.text((x, y), line, fill=INK, font=title_font)
        y += 116
    y += 44
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = _font(44)
    label = _font(44, bold=True)
    scene_total = sum(len(run.scenes) for run in runs)
    rows = [
        ("Generated", generated),
        ("Scenes", str(scene_total)),
        ("Sources", ", ".join(sorted({run.base_url for run in runs if run.base_url})) or "unknown"),
    ]
    for name, value in rows:
        draw.text((x, y), name, fill=MUTED, font=label)
        for line in _wrap(draw, value, body, width - 420):
            draw.text((x + 420, y), line, fill=INK, font=body)
            y += 64
        y += 12
    y += 30
    draw.text((x, y), "Scenarios", fill=MUTED, font=label)
    y += 72
    for run in runs:
        line = (
            f"{run.scenario} · {len(run.scenes)} scene(s) · "
            f"{_format_time(run.started_at)} · {run.base_url}"
        )
        for wrapped in _wrap(draw, line, body, width):
            draw.text((x + 30, y), wrapped, fill=INK, font=body)
            y += 64
    y = max(y + 60, LANDSCAPE[1] - MARGIN * 2 - 160)
    draw.rectangle((x - 30, y - 30, LANDSCAPE[0] - x + 30, y + 140), fill=BAND)
    for line in _wrap(draw, LOCAL_NOTE, _font(40), width):
        draw.text((x, y), line, fill=INK, font=_font(40))
        y += 58
    return page


def _scene_page(scene: ReviewScene, position: int, total: int) -> Image.Image:
    with Image.open(scene.image_path) as source:
        shot = source.convert("RGB")
    size = PORTRAIT if shot.height > shot.width else LANDSCAPE
    page = Image.new("RGB", size, BACKGROUND)
    draw = ImageDraw.Draw(page)

    image_box = (size[0] - MARGIN * 2, size[1] - MARGIN * 2 - CAPTION_HEIGHT)
    # contain() keeps aspect ratio; never upscale small images past 1:1.
    if shot.width > image_box[0] or shot.height > image_box[1]:
        shot = ImageOps.contain(shot, image_box, Image.Resampling.LANCZOS)
    left = (size[0] - shot.width) // 2
    top = MARGIN + (image_box[1] - shot.height) // 2
    draw.rectangle((left - 2, top - 2, left + shot.width + 1, top + shot.height + 1), outline="#cabda8", width=2)
    page.paste(shot, (left, top))

    band_top = size[1] - MARGIN - CAPTION_HEIGHT + 20
    draw.rectangle((MARGIN, band_top, size[0] - MARGIN, size[1] - MARGIN), fill=BAND)
    x, y = MARGIN + 30, band_top + 24
    text_width = size[0] - MARGIN * 2 - 60
    title_font = _font(46, bold=True)
    heading = _fit_lines(draw, f"{scene.order:02d}. {scene.title}", title_font, text_width, 1)[0]
    draw.text((x, y), heading, fill=INK, font=title_font)
    y += 68
    body = _font(34)
    for line in _fit_lines(draw, scene.description, body, text_width, 3):
        draw.text((x, y), line, fill=INK, font=body)
        y += 46
    meta = (
        f"{scene.scenario} · {scene.profile} · {scene.viewport} · "
        f"captured {_format_time(scene.captured_at)} · page {position} of {total}"
    )
    draw.text((x, size[1] - MARGIN - 56), meta, fill=MUTED, font=_font(30))
    return page


def build_review_pdf(
    run_dirs: list[Path | str],
    output: Path | str,
    title: str = "Review capture",
) -> Path:
    """Write a cover page plus one page per scene across all runs, in order."""
    if not run_dirs:
        raise ReviewPdfError("at least one run directory is required")
    runs = [load_run(run_dir) for run_dir in run_dirs]
    scenes = [scene for run in runs for scene in run.scenes]
    if not scenes:
        raise ReviewPdfError("the selected runs contain no scenes")

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    total = len(scenes) + 1
    cover = _cover_page(title, runs)
    # Pillow's PDF writer materialises every page before writing anyway.
    pages = [
        _scene_page(scene, position, total)
        for position, scene in enumerate(scenes, start=2)
    ]
    cover.save(
        output,
        "PDF",
        save_all=True,
        append_images=pages,
        resolution=DPI,
        quality=90,
        title=title,
        author="tour_capture",
    )
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bundle capture runs into one review PDF")
    parser.add_argument("run_dirs", nargs="+", type=Path, help="capture run directories")
    parser.add_argument("-o", "--output", type=Path, required=True, help="PDF to write")
    parser.add_argument("--title", default="Review capture", help="cover page title")
    args = parser.parse_args(argv)
    try:
        path = build_review_pdf(args.run_dirs, args.output, args.title)
    except ReviewPdfError as exc:
        print(f"review pdf error: {exc}")
        return 1
    print(f"review pdf: {path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
