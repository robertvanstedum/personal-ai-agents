"""Mobile Look inside viewer must remain usable in both phone orientations."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tour_exposes_mobile_fit_and_detail_control():
    template = (ROOT / "minimoi_portal/templates/tour.html").read_text(
        encoding="utf-8"
    )

    assert 'id="tour-lightbox-zoom"' in template
    assert 'aria-pressed="false"' in template
    assert 'dialog.classList.toggle("is-detail-view", enabled)' in template
    assert 'zoom.textContent = enabled ? "Fit slide" : "View details"' in template


def test_tour_mobile_viewer_covers_portrait_and_landscape():
    css = (ROOT / "minimoi_portal/static/portal.css").read_text(encoding="utf-8")

    assert "@media (max-width: 900px)" in css
    assert "height: 100dvh;" in css
    assert ".tour-lightbox.is-detail-view .tour-lightbox-stage" in css
    assert "touch-action: pan-x pan-y pinch-zoom;" in css
    assert "@media (max-width: 900px) and (orientation: landscape)" in css
