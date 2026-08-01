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
    template = (ROOT / "minimoi_portal/templates/tour.html").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "minimoi_portal/static/portal.css").read_text(encoding="utf-8")

    assert 'window.matchMedia("(max-width: 900px)").matches' in template
    assert '"(max-width: 900px) and (orientation: portrait)"' not in template
    assert "@media (max-width: 900px)" in css
    assert "height: 100dvh;" in css
    assert ".tour-lightbox.is-detail-view .tour-lightbox-stage" in css
    assert "touch-action: pan-x pan-y pinch-zoom;" in css
    assert "@media (max-width: 900px) and (orientation: landscape)" in css
    assert ".tour-lightbox.is-mobile-format .tour-lightbox-media" in css
    assert "overflow-y: auto;" in css
    assert "touch-action: pan-y;" in css
    assert ".tour-lightbox.is-mobile-format .tour-lightbox-format" in css


def test_tour_resets_scroll_position_when_a_slide_changes():
    template = (ROOT / "minimoi_portal/templates/tour.html").read_text(
        encoding="utf-8"
    )

    reset_function = template.index("function resetLightboxScroll()")
    render_function = template.index("function renderLightboxImage(index)")
    reset_call = template.index("resetLightboxScroll();", render_function)
    link_render = template.index("const link = lightboxLinks[lightboxIndex];")

    assert reset_function < render_function
    assert render_function < reset_call < link_render
    assert template.count("lightboxStage.scrollTop = 0;") >= 3


def test_tour_supports_distinct_desktop_and_mobile_story_collections():
    template = (ROOT / "minimoi_portal/templates/tour.html").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "minimoi_portal/static/portal.css").read_text(encoding="utf-8")
    curator_mobile_assets = [
        "01-curator-landing-mobile.webp",
        "02-curator-sections-overview-mobile.webp",
        "03-curator-daily-briefing-mobile.webp",
        "04-curator-article-fiscal-mobile.webp",
        "05-curator-article-list-2-mobile.webp",
        "06-curator-article-list-3-mobile.webp",
        "07-curator-article-saved-mobile.webp",
        "08-curator-reading-room-mobile.webp",
        "09-curator-article-saved-2-mobile.webp",
        "10-curator-scans-dives-mobile.webp",
        "11-curator-deeper-dive-thread-mobile.webp",
        "12-curator-scans-dives-tools-mobile.webp",
    ]
    german_mobile_assets = [
        "01-german-landing-mobile.webp",
        "02-german-reading-categories-mobile.webp",
    ]
    portuguese_mobile_assets = [
        "01-portuguese-landing-mobile.webp",
        "02-portuguese-reading-categories-mobile.webp",
    ]
    guild_mobile_assets = [
        "01-guild-landing-mobile.webp",
        "02-guild-landing-partners-mobile.webp",
        "03-guild-build-log-mobile.webp",
        "04-guild-spec-mobile.webp",
        "05-guild-roadmap-mobile.webp",
        "06-guild-github-issues-mobile.webp",
        "07-guild-operate-mobile.webp",
        "08-guild-improve-mobile.webp",
    ]
    cos_mobile_assets = [
        "01-cos-landing-mobile.webp",
        "02-cos-confer-mobile.webp",
    ]
    mobile_assets = (
        curator_mobile_assets
        + german_mobile_assets
        + portuguese_mobile_assets
        + guild_mobile_assets
        + cos_mobile_assets
    )

    assert 'class="tour-mobile-gallery"' in template
    assert template.count('class="tour-mobile-shot-link"') == len(mobile_assets)
    for mobile_asset in mobile_assets:
        assert f"/static/tour/{mobile_asset}" in template
        assert (
            ROOT / "minimoi_portal/static/tour" / mobile_asset
        ).is_file()
    curator_mobile_positions = [
        template.index(f"/static/tour/{mobile_asset}")
        for mobile_asset in curator_mobile_assets
    ]
    assert curator_mobile_positions == sorted(curator_mobile_positions)

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
