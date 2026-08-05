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
        "02-curator-breadth-mobile.webp",
        "03-curator-daily-briefing-mobile.webp",
        "04-curator-choose-article-mobile.webp",
        "05-curator-original-source-mobile.webp",
        "06-curator-investigate-save-mobile.webp",
        "07-curator-research-question-mobile.webp",
        "08-curator-scan-analysis-mobile.webp",
        "09-curator-deeper-dive-running-mobile.webp",
        "10-curator-deeper-dive-opening-mobile.webp",
        "11-curator-confirming-evidence-mobile.webp",
        "12-curator-gaps-uncertainty-mobile.webp",
        "13-curator-analytical-challenge-mobile.webp",
        "14-curator-alternative-interpretations-mobile.webp",
        "15-curator-challenger-provenance-mobile.webp",
    ]
    curator_desktop_assets = [
        "01-curator-landing-desktop.webp",
        "02-curator-daily-briefing-desktop.webp",
        "03-curator-choose-article-desktop.webp",
        "04-curator-original-source-desktop.webp",
        "05-curator-scans-dives-desktop.webp",
        "06-curator-scan-opening-desktop.webp",
        "07-curator-scan-connections-desktop.webp",
        "08-curator-scan-sources-desktop.webp",
        "09-curator-deeper-synthesis-desktop.webp",
        "10-curator-deeper-evidence-desktop.webp",
        "11-curator-deeper-challenge-desktop.webp",
        "12-curator-leanings-desktop.webp",
    ]
    german_mobile_assets = [
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
    german_desktop_assets = [
        "01-german-landing-desktop.webp",
        "02-german-reading-topics-desktop.webp",
        "03-german-current-article-desktop.webp",
        "04-german-translate-save-desktop.webp",
        "05-german-original-source-desktop.webp",
        "06-german-reading-note-desktop.webp",
        "07-german-reading-correction-desktop.webp",
        "08-german-vocabulary-desktop.webp",
        "09-german-conversation-choice-desktop.webp",
        "10-german-conversation-prepare-desktop.webp",
        "11-german-conversation-transcript-desktop.webp",
        "12-german-speaking-feedback-desktop.webp",
        "13-german-writing-desktop.webp",
        "14-german-writing-feedback-desktop.webp",
        "15-german-conversation-history-desktop.webp",
        "16-german-reopen-conversation-desktop.webp",
        "17-german-archived-writing-desktop.webp",
    ]
    portuguese_mobile_assets = [
        "portuguese-mobile-landing.webp",
        "portuguese-reading-categories.webp",
        "portuguese-reading-list.webp",
        "portuguese-reading-article.webp",
        "portuguese-reading-correction.webp",
        "portuguese-conversation-choice.webp",
        "portuguese-voice-transcript.webp",
        "portuguese-voice-coaching.webp",
        "portuguese-learning-archive.webp",
    ]
    guild_desktop_assets = [
        "01-guild-landing-desktop.webp",
        "02-guild-build-queue-desktop.webp",
        "03-guild-specification-desktop.webp",
        "04-guild-spec-evaluation-desktop.webp",
        "05-guild-build-log-desktop.webp",
        "06-guild-roadmap-desktop.webp",
        "07-guild-docs-desktop.webp",
        "08-guild-operate-desktop.webp",
        "09-guild-improve-desktop.webp",
    ]
    cos_desktop_assets = [
        "01-cos-landing-desktop.webp",
        "02-cos-confer-direction-desktop.webp",
        "03-cos-confirm-decision-desktop.webp",
        "04-cos-record-desktop.webp",
        "05-cos-track-desktop.webp",
        "06-cos-store-desktop.webp",
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

    for asset in curator_desktop_assets:
        assert asset in template
        assert (ROOT / "minimoi_portal/static/tour" / asset).is_file()
    for asset in german_desktop_assets + guild_desktop_assets + cos_desktop_assets:
        assert asset in template
        assert (ROOT / "minimoi_portal/static/tour" / asset).is_file()

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
    assert "padding-inline: 4.25rem" in css
    assert 'zoom.hidden = lightboxFormat === "mobile";' in template
    assert ".tour-lightbox-format button" in css
    assert ".tour-lightbox.is-mobile-format .tour-lightbox-stage" in css
    assert ".tour-lightbox.is-mobile-format img" in css
    assert ".tour-lightbox.is-mobile-format .tour-lightbox-zoom" in css
