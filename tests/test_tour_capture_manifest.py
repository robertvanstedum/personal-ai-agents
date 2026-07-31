import json

from PIL import Image
import pytest

from scripts.tools.tour_capture.manifest import (
    CapturedScene,
    ManifestValidationError,
    write_manifest,
    write_review_page,
)


def _write_artifacts(tmp_path):
    raw = tmp_path / "raw" / "01-portuguese-landing-mobile.png"
    optimized = tmp_path / "optimized" / "01-portuguese-landing-mobile.webp"
    raw.parent.mkdir()
    optimized.parent.mkdir()
    image = Image.new("RGB", (4, 3), "#173f35")
    image.save(raw, "PNG")
    image.save(optimized, "WEBP", quality=92)


def test_manifest_maps_mobile_scene_to_stable_optimized_filename(tmp_path):
    _write_artifacts(tmp_path)
    scene = CapturedScene(
        order=1,
        scene="landing",
        title="Enter the Portuguese immersion space",
        description="Start in the personal Portuguese immersion workspace.",
        alt="Meu Português landing page in a mobile viewport",
        raw="raw/01-portuguese-landing-mobile.png",
        optimized="optimized/01-portuguese-landing-mobile.webp",
        width=4,
        height=3,
        bytes=12345,
    )
    scenario = {
        "id": "portuguese-reading",
        "domain": "portuguese",
        "device_profile": "mobile",
    }

    path = write_manifest(tmp_path, scenario, [scene], {"article": {"title": "Example"}})
    manifest = json.loads(path.read_text(encoding="utf-8"))

    assert manifest["scenes"][0]["mobile"] == "01-portuguese-landing-mobile.webp"
    assert manifest["scenes"][0]["raw"].startswith("raw/")
    assert manifest["scenes"][0]["optimized"].startswith("optimized/")


def test_manifest_rejects_missing_artifacts(tmp_path):
    scene = CapturedScene(
        order=1,
        scene="landing",
        title="Enter the Portuguese immersion space",
        description="Start in the personal Portuguese immersion workspace.",
        alt="Meu Português landing page in a mobile viewport",
        raw="raw/01-portuguese-landing-mobile.png",
        optimized="optimized/01-portuguese-landing-mobile.webp",
        width=4,
        height=3,
        bytes=12345,
    )
    scenario = {
        "id": "portuguese-reading",
        "domain": "portuguese",
        "device_profile": "mobile",
    }

    with pytest.raises(ManifestValidationError, match="does not exist"):
        write_manifest(tmp_path, scenario, [scene], {})


def test_review_page_shows_mobile_and_desktop_presentations(tmp_path):
    scene = CapturedScene(
        order=1,
        scene="landing",
        title="Enter the Portuguese immersion space",
        description="Start in the personal Portuguese immersion workspace.",
        alt="Meu Português landing page in a mobile viewport",
        raw="raw/01-portuguese-landing-mobile.png",
        optimized="optimized/01-portuguese-landing-mobile.webp",
        width=1170,
        height=2532,
        bytes=12345,
    )

    path = write_review_page(tmp_path, {"id": "portuguese-reading"}, [scene])
    review = path.read_text(encoding="utf-8")

    assert "Mobile presentation" in review
    assert "Desktop presentation" in review
