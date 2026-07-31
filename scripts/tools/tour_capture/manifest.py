"""Manifest, report, and human review page generation."""

from __future__ import annotations

import html
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from PIL import Image


class ManifestValidationError(RuntimeError):
    """Raised when a manifest would reference incomplete or mismatched files."""


@dataclass
class CapturedScene:
    order: int
    scene: str
    title: str
    description: str
    alt: str
    raw: str
    optimized: str
    width: int
    height: int
    bytes: int


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _artifact_path(run_dir: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ManifestValidationError(f"artifact path must remain in the run directory: {relative}")
    resolved = (run_dir / candidate).resolve()
    if run_dir.resolve() not in resolved.parents:
        raise ManifestValidationError(f"artifact path escapes the run directory: {relative}")
    if not resolved.is_file():
        raise ManifestValidationError(f"manifest artifact does not exist: {relative}")
    return resolved


def _validate_scenes(run_dir: Path, profile: str, scenes: list[CapturedScene]) -> None:
    orders = [scene.order for scene in scenes]
    if orders != list(range(1, len(scenes) + 1)):
        raise ManifestValidationError("manifest scene orders must be consecutive and start at 1")

    paths: set[str] = set()
    for scene in scenes:
        if scene.raw in paths or scene.optimized in paths:
            raise ManifestValidationError("manifest contains a duplicate artifact path")
        paths.update((scene.raw, scene.optimized))

        raw_path = _artifact_path(run_dir, scene.raw)
        optimized_path = _artifact_path(run_dir, scene.optimized)
        if raw_path.suffix.lower() != ".png" or optimized_path.suffix.lower() != ".webp":
            raise ManifestValidationError("manifest artifacts must be PNG/WebP pairs")
        if raw_path.stem != optimized_path.stem:
            raise ManifestValidationError("raw and optimized artifacts must share one basename")
        if not optimized_path.stem.endswith(f"-{profile}"):
            raise ManifestValidationError(
                f"optimized artifact does not match profile {profile!r}: {scene.optimized}"
            )

        for path, expected_format in ((raw_path, "PNG"), (optimized_path, "WEBP")):
            with Image.open(path) as image:
                if image.format != expected_format:
                    raise ManifestValidationError(f"unexpected image format for {path.name}")
                if image.size != (scene.width, scene.height):
                    raise ManifestValidationError(
                        f"manifest dimensions do not match {path.name}: {image.size}"
                    )


def write_manifest(
    run_dir: Path,
    scenario: dict[str, Any],
    scenes: list[CapturedScene],
    records: dict[str, Any],
) -> Path:
    profile = scenario["device_profile"]
    _validate_scenes(run_dir, profile, scenes)
    manifest_scenes = []
    for scene in scenes:
        item = asdict(scene)
        item[profile] = Path(scene.optimized).name
        manifest_scenes.append(item)

    payload = {
        "scenario": scenario["id"],
        "domain": scenario["domain"],
        "profile": profile,
        "records": records,
        "scenes": manifest_scenes,
    }
    return _write_json(run_dir / "manifest.json", payload)


def write_report(run_dir: Path, payload: dict[str, Any]) -> Path:
    return _write_json(run_dir / "report.json", payload)


def write_review_page(run_dir: Path, scenario: dict[str, Any], scenes: list[CapturedScene]) -> Path:
    cards = []
    for scene in scenes:
        cards.append(
            "<figure>"
            f"<figcaption><strong>{scene.order:02d}. {html.escape(scene.title)}</strong>"
            f"<span>{html.escape(scene.description)}</span>"
            f"<span>{scene.width} × {scene.height} · {scene.bytes:,} bytes</span></figcaption>"
            '<div class="presentations">'
            '<section><h2>Mobile presentation</h2><div class="mobile-frame">'
            f'<img src="{html.escape(scene.optimized)}" alt="{html.escape(scene.alt)}">'
            "</div></section>"
            '<section><h2>Desktop presentation</h2><div class="desktop-frame">'
            f'<img src="{html.escape(scene.optimized)}" alt="">'
            "</div></section></div>"
            "</figure>"
        )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(scenario['id'])} capture review</title>
<style>
body{{margin:0;padding:2rem;background:#eee3d2;color:#173f35;font-family:system-ui,sans-serif}}
h1{{font-family:Georgia,serif;font-weight:400}}
main{{display:grid;gap:2rem}}
figure{{margin:0;background:#fffaf1;padding:1rem;border:1px solid #cabda8;border-radius:12px}}
figcaption{{display:grid;gap:.35rem;margin-bottom:1rem}}
figcaption span{{color:#6b6257;font-size:.9rem}}
.presentations{{display:grid;grid-template-columns:minmax(260px,390px) minmax(360px,1fr);gap:1.25rem;align-items:start}}
h2{{font-size:.8rem;letter-spacing:.08em;text-transform:uppercase;color:#6b6257}}
.mobile-frame,.desktop-frame{{box-sizing:border-box;background:#eee3d2;border:1px solid #cabda8;padding:.75rem;display:flex;justify-content:center;overflow:hidden}}
.mobile-frame{{width:100%}}.mobile-frame img{{display:block;width:100%;height:auto}}
.desktop-frame{{width:100%;min-height:480px;align-items:center}}.desktop-frame img{{display:block;width:auto;max-width:100%;height:auto;max-height:720px}}
@media(max-width:820px){{.presentations{{grid-template-columns:1fr}}.desktop-frame{{min-height:0}}}}
</style></head><body><h1>{html.escape(scenario['id'])}</h1>
<p>Generated review output. Nothing on this page is deployed automatically.</p>
<main>{''.join(cards)}</main></body></html>
"""
    path = run_dir / "review.html"
    path.write_text(document, encoding="utf-8")
    return path
