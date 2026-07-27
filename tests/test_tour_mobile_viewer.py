"""Mobile Look inside viewer must remain usable in both phone orientations."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tour_exposes_mobile_fit_and_detail_control():
    template = (ROOT / "minimoi_portal/templates/tour.html").read_text(
        encoding="utf-8"
    )

    assert 'id="tour-lightbox-zoom"' in template
    assert 'class="tour-lightbox-media"' in template
    assert 'aria-pressed="false"' in template
    assert 'dialog.classList.toggle("is-detail-view", enabled)' in template
    assert "function shouldStartInDetailView()" in template
    assert '"(max-width: 600px) and (orientation: portrait)"' in template
    assert "setDetailView(shouldStartInDetailView())" in template
    assert 'image.addEventListener("load", () =>' in template
    assert "centerDetailView();" in template
    assert 'zoom.textContent = enabled ? "Fit slide" : "View details"' in template


def test_tour_mobile_viewer_covers_portrait_and_landscape():
    css = (ROOT / "minimoi_portal/static/portal.css").read_text(encoding="utf-8")

    assert "@media (max-width: 900px)" in css
    assert "height: 100dvh;" in css
    assert ".tour-lightbox.is-detail-view .tour-lightbox-stage" in css
    assert "touch-action: pan-x pan-y pinch-zoom;" in css
    assert "@media (max-width: 900px) and (orientation: landscape)" in css


def test_tour_supports_real_desktop_and_mobile_screenshot_pairs():
    template = (ROOT / "minimoi_portal/templates/tour.html").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "minimoi_portal/static/portal.css").read_text(encoding="utf-8")
    mobile_asset = (
        ROOT / "minimoi_portal/static/tour/german-landing-mobile.png"
    )

    assert (
        'data-mobile-src="/static/tour/german-landing-mobile.png"'
        in template
    )
    assert 'id="tour-lightbox-format"' in template
    assert 'data-format="desktop"' in template
    assert 'data-format="mobile"' in template
    assert "function setFormatView(requestedFormat)" in template
    assert "function shouldStartInMobileView()" in template
    assert "function alignArrowsToImage()" in template
    assert 'window.addEventListener("resize", alignArrowsToImage)' in template
    assert 'zoom.hidden = hasMobileView' in template
    assert ".tour-lightbox-format button" in css
    assert ".tour-lightbox.is-mobile-format img" in css
    assert mobile_asset.is_file()
