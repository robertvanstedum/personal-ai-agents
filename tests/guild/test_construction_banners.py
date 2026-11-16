"""Operate, Improve, and Lab carry the In construction banner. They are not ready for 1.2."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLOOR = ROOT / "minimoi_portal/guild_ui/templates/guild_floor"
PARTIAL = (FLOOR / "_in_construction.html").read_text()
CSS = (ROOT / "minimoi_portal/guild_ui/static/components.css").read_text()


def test_the_banner_says_in_construction():
    assert 'class="gu-construction"' in PARTIAL
    assert 'content: "In construction"' in CSS


def test_operate_improve_and_lab_show_the_banner_under_the_title():
    pages = {
        "operate.html": "Operate is not ready for 1.2.",
        "library.html": "Improve is not ready for 1.2.",
        "prototype_lab.html": "Lab is not ready for 1.2.",
    }
    for name, sentence in pages.items():
        text = (FLOOR / name).read_text()
        hero, _, rest = text.partition("{% include 'guild_floor/_area_hero.html' %}")
        assert hero and rest
        assert rest.lstrip().startswith("{% set construction_text = \"" + sentence + "\" %}")
        assert "{% include 'guild_floor/_in_construction.html' %}" in rest[:240]
