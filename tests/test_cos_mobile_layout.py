"""Chief of Staff must contain its navigation inside the phone viewport."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cos_mobile_header_scrolls_inside_the_viewport():
    css = (ROOT / "domains/cos/static/cos.css").read_text(encoding="utf-8")

    mobile = css[css.index("@media (max-width: 768px)") :]
    assert ".cos-header" in mobile
    assert "max-width: 100%;" in mobile
    assert "overflow: hidden;" in mobile
    assert ".header-nav" in mobile
    assert "overflow-x: auto;" in mobile
    assert "scrollbar-width: none;" in mobile
    assert ".tab-nav," in mobile
    assert "flex: 0 0 auto;" in mobile
