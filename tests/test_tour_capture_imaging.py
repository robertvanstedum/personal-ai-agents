from pathlib import Path

from PIL import Image

from scripts.tools.tour_capture.imaging import build_contact_sheet, optimize_png


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "tour_capture" / "sample.ppm"


def test_optimized_webp_preserves_dimensions_and_strips_metadata(tmp_path):
    raw = tmp_path / "01-portuguese-landing-mobile.png"
    with Image.open(FIXTURE) as source:
        exif = Image.Exif()
        exif[0x010E] = "capture fixture"
        source.save(raw, "PNG", exif=exif)

    output = optimize_png(raw, tmp_path / "optimized", (4, 3), quality=92)

    with Image.open(output) as image:
        assert image.size == (4, 3)
        assert not image.getexif()


def test_contact_sheet_is_generated_from_ordered_scenes(tmp_path):
    images = []
    for order in (1, 2):
        path = tmp_path / f"scene-{order}.webp"
        with Image.open(FIXTURE) as source:
            source.save(path, "WEBP", quality=92)
        images.append((f"{order:02d} scene", path))

    sheet = build_contact_sheet(
        images,
        tmp_path / "contact-sheet.webp",
        columns=2,
        thumb_size=(40, 30),
    )

    assert sheet.exists()
    with Image.open(sheet) as image:
        assert image.width > 80
        assert image.height > 30
