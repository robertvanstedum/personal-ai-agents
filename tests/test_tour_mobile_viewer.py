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


def test_tour_supports_distinct_desktop_and_mobile_story_collections():
    template = (ROOT / "minimoi_portal/templates/tour.html").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "minimoi_portal/static/portal.css").read_text(encoding="utf-8")
    mobile_assets = [
        "german-mobile-01-landing.webp",
        "german-mobile-02-lesen.webp",
        "german-mobile-03-lesen-articles.webp",
        "german-mobile-04-lesen-translate.webp",
        "german-mobile-05-lesen-note.webp",
        "german-mobile-06-lesen-correction.webp",
        "german-mobile-07-lesen-source.webp",
        "german-mobile-08-gespraeche-persona.webp",
        "german-mobile-09-gespraeche-setup.webp",
        "german-mobile-10-gespraeche-active.webp",
        "german-mobile-11-gespraeche-transcript.webp",
        "german-mobile-12-gespraeche-feedback.webp",
        "german-mobile-13-gespraeche-archive.webp",
    ]

    assert 'class="tour-mobile-gallery"' in template
    assert template.count('class="tour-mobile-shot-link"') == len(mobile_assets)
    for mobile_asset in mobile_assets:
        assert f"/static/tour/{mobile_asset}" in template
        assert (
            ROOT / "minimoi_portal/static/tour" / mobile_asset
        ).is_file()

    assert 'id="tour-lightbox-format"' in template
    assert 'data-format="desktop"' in template
    assert 'data-format="mobile"' in template
    assert "let mobileLightboxLinks = [];" in template
    assert "function setFormatView(requestedFormat, requestedIndex = 0)" in template
    assert "function renderLightboxImage(index)" in template
    assert "function shouldStartInMobileView()" in template
    assert "function alignArrowsToImage()" in template
    assert 'window.addEventListener("resize", alignArrowsToImage)' in template
    assert 'zoom.hidden = lightboxFormat === "mobile";' in template
    assert ".tour-lightbox-format button" in css
    assert ".tour-lightbox.is-mobile-format .tour-lightbox-stage" in css
    assert ".tour-lightbox.is-mobile-format img" in css
    assert ".tour-lightbox.is-mobile-format .tour-lightbox-zoom" in css
