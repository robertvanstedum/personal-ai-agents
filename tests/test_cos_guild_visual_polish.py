import hashlib
from pathlib import Path

from PIL import Image

from minimoi_portal.workspaces import WORKSPACES


ROOT = Path(__file__).resolve().parents[1]
APPROVED_CONFER_PHOTO_SHA256 = (
    "211614894902f84d1d3033d39b977415134963c0448458af05bfc524d4335480"
)
APPROVED_CONFER_SLIDE_SHA256 = (
    "8c7176a124a28320cdebd4ca5eb3fbc66a6cff4f417f2f0b0c21b19747db0521"
)
APPROVED_GUILD_LANDING_SHA256 = (
    "59768e40f85604fe03d284d2ccd4be03a599f6f26f503dc599431c0fc2ba9b1d"
)
APPROVED_GUILD_TRANSITION_SHA256 = (
    "b50312c92914d0216d1f8c6cf56c2301b9ef7d4ec884a2c5f61dff2b0bd7b390"
)
APPROVED_GUILD_OPERATE_SHA256 = (
    "3c834fe758f1faa62dc29da3016cb6eae7751dc44c7afd28b98c31034b197227"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cos_confer_uses_privacy_softened_photo():
    template = (ROOT / "domains/cos/templates/cos_ui.html").read_text(
        encoding="utf-8"
    )
    photo = ROOT / "domains/cos/static/photos/cos_discuss_private.png"

    assert "/static/photos/cos_discuss_private.png?v=1" in template
    assert photo.is_file()
    assert _sha256(photo) == APPROVED_CONFER_PHOTO_SHA256
    with Image.open(photo) as image:
        assert image.size == (1086, 1448)


def test_cos_and_guild_subnavigation_share_readable_palette():
    cos_css = (ROOT / "domains/cos/static/cos.css").read_text(encoding="utf-8")
    guild_css = (ROOT / "minimoi_portal/static/guild.css").read_text(
        encoding="utf-8"
    )
    guild_nav = (
        ROOT / "minimoi_portal/templates/guild/_portal_nav.html"
    ).read_text(encoding="utf-8")

    for source in (cos_css, guild_css, guild_nav):
        assert "#C9AB85" in source
        assert "#F5EDE0" in source
        assert "#D49A6A" in source


def test_public_cos_confer_slide_restores_photo_version():
    template = (ROOT / "minimoi_portal/templates/tour.html").read_text(
        encoding="utf-8"
    )
    screenshot = ROOT / "minimoi_portal/static/tour/cos-confer.png"

    assert "/static/tour/cos-confer.png?v=20260805-cosrestore1" in template
    assert _sha256(screenshot) == APPROVED_CONFER_SLIDE_SHA256
    with Image.open(screenshot) as image:
        assert image.size == (2900, 1602)


def test_guild_and_cos_cards_use_full_landscape_captures():
    workspaces = {workspace["key"]: workspace for workspace in WORKSPACES}
    expected = {
        "guild": "/static/tour/guild-catalog-desktop.jpg?v=20261014-g8-review1",
        "cos": "/static/tour/01-cos-landing-desktop.webp",
    }

    for key, public_path in expected.items():
        assert workspaces[key]["image"] == public_path
        asset = (
            ROOT
            / "minimoi_portal/static"
            / public_path.split("?", 1)[0].removeprefix("/static/")
        )
        with Image.open(asset) as image:
            assert image.size == ((1180, 331) if key == "guild" else (2880, 1800))


def test_guild_public_slides_are_the_approved_privacy_safe_refresh():
    expected = {
        "01-guild-landing-desktop.webp": APPROVED_GUILD_LANDING_SHA256,
        "10-guild-transition-desktop.webp": APPROVED_GUILD_TRANSITION_SHA256,
        "11-guild-operate-desktop.webp": APPROVED_GUILD_OPERATE_SHA256,
    }

    for name, approved_hash in expected.items():
        asset = ROOT / "minimoi_portal/static/tour" / name
        assert _sha256(asset) == approved_hash
        with Image.open(asset) as image:
            assert image.size == (2880, 1800)


def test_card_frames_preserve_landscape_composition():
    css = (ROOT / "minimoi_portal/static/portal.css").read_text(
        encoding="utf-8"
    )

    assert ".tour-domain-card-image {\n  aspect-ratio: 16 / 10;" in css
    assert "grid-template-rows: auto 1fr;" in css
    assert ".dashboard-card-visual {\n  min-width: 0;\n  aspect-ratio: 16 / 10;" in css
    assert "object-fit: contain;\n  object-position: center top;" in css


def test_guild_landing_card_tabs_use_readable_cream_type():
    template = (
        ROOT / "minimoi_portal/templates/guild/guild_landing.html"
    ).read_text(encoding="utf-8")

    assert ".card-tab {" in template
    assert "color: #F5EDE0;" in template
    assert "font-size: 10px;" in template
    assert "font-weight: 500;" in template


def test_guild_four_area_capture_bytes_and_actual_dimensions():
    # Exact reviewed dev-capture identity; this does not assert owner acceptance.
    expected = {
        "guild-main-desktop.jpg": ("9b8140bf95e889037f658d9dc3ee691cf5b4e99b1fcf9c5ddd9bc4b1b1825ea2", (1440, 654)),
        "guild-catalog-desktop.jpg": ("b9494fbaa3797d8811a5479bf2aea18dedf7033a829d6b2fd47923119e0610e6", (1180, 331)),
        "guild-main-mobile.jpg": ("cf38008856084bd255ef6fd4eef1ddc1d7324411eccfa8be9d9cddeb7941e7db", (390, 1874)),
    }
    for name, (digest, dimensions) in expected.items():
        path = ROOT / "minimoi_portal/static/tour" / name
        assert _sha256(path) == digest
        with Image.open(path) as image:
            assert image.size == dimensions


def test_guild_capture_source_keeps_four_areas():
    from minimoi_portal.guild_ui.pages import DOORS
    assert [door["label"] for door in DOORS] == [
        "Build", "Operate", "Improve", "Prototype Lab"
    ]
    template = (ROOT / "minimoi_portal/templates/tour.html").read_text()
    assert "Guild overview with Build, Operate, Improve and Prototype Lab" in template
    assert "Build has two partners" not in template
    assert "artwork illustrates the Guild areas" in template
