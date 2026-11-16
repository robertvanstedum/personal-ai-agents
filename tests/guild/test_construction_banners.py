"""Operate, Improve, and Lab carry the In construction banner. They are not ready for 1.2."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLOOR = ROOT / "minimoi_portal/guild_ui/templates/guild_floor"
PARTIAL = (FLOOR / "_in_construction.html").read_text()
CSS = (ROOT / "minimoi_portal/guild_ui/static/components.css").read_text()


def test_the_banner_says_in_construction():
    assert 'class="gu-construction' in PARTIAL
    assert 'content: "In construction"' in CSS


def test_the_page_banner_is_a_large_diagonal_sash():
    assert "gu-construction-top" in PARTIAL
    assert "transform: rotate(-4deg)" in CSS
    assert "font-size: 32px" in CSS


def test_operate_improve_and_lab_show_the_banner_above_the_title():
    pages = {
        "operate.html": "Operate is not ready for 1.2.",
        "library.html": "Improve is not ready for 1.2.",
        "prototype_lab.html": "Lab is not ready for 1.2.",
    }
    for name, sentence in pages.items():
        text = (FLOOR / name).read_text()
        banner, _, hero = text.partition("{% include 'guild_floor/_area_hero.html' %}")
        assert hero
        assert '<div class="area-under-construction">' in banner
        assert "{% set construction_top = true %}" in banner
        assert sentence in banner
        assert "{% include 'guild_floor/_in_construction.html' %}" in banner
        if "{% include 'guild_floor/_area_preview.html' %}" in text:
            assert "{% set construction_top = false %}" in text
            assert text.index("{% set construction_top = false %}") < text.index(
                "{% include 'guild_floor/_area_preview.html' %}"
            )
