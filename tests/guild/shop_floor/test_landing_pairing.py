"""Guild 1.1 slice 1 (spec §3): /guild-next/ is a Guild home paired with
Curator's landing, no longer a redirect to the Shop floor.

The four doors keep the original Guild art, labels, kickers, flows and CTAs
(templates/guild/guild_landing.html and _build_card.html) and go where the
spec's door table says. Owner-guarded like every page. No tunnel photo and no
square tiles. The page reads no store and calls no model."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)

REPO = Path(__file__).resolve().parents[3]
LEGACY = REPO / "minimoi_portal" / "templates" / "guild"
LANDING_CSS = REPO / "minimoi_portal" / "guild_ui" / "static" / "css" / "landing.css"
ROOTS = ["/guild-next", "/guild-next/", "/guild-next/guild", "/guild-next/guild/"]

# Door, mapped href (spec §3 table), original image, kicker, flow, CTA.
DOORS = [
    ("Build", "/guild-next/guild/build", "/static/guild/guild-build.webp",
     "Chat · Board · Build Log", "design → build", "Chat →"),
    ("Operate", "/guild-next/guild/operate", "/static/guild/guild-operate.webp",
     "Monitor · Maintain", "monitor → maintain", "Status →"),
    ("Improve", "/guild-next/guild/improve", "/static/guild/guild-improve.webp",
     "Review · Analyze", "review → analyze → improve", "Explore →"),
    ("Prototype Lab", "/guild-next/guild/experiment", "/static/guild/guild-experiment.webp",
     "Ideas · Tinkering · Reference demos", "try → prove → keep", "Open →"),
]


def _home(staging, path="/guild-next/"):
    r = staging.owner().get(path)
    assert r.status_code == 200, (path, r.status_code)
    return r.get_data(as_text=True)


def _drawers(page):
    return re.findall(r'<a href="([^"]+)" class="gl-drawer" data-door="([a-z]+)">(.*?)</a>', page, re.S)


@pytest.mark.parametrize("path", ROOTS)
def test_the_mount_root_is_the_guild_home_for_the_owner(staging, path):
    r = staging.owner().get(path)
    assert r.status_code == 200 and "Location" not in r.headers, (path, r.status_code)
    page = r.get_data(as_text=True)
    assert 'data-page="home"' in page and '<h1 class="gl-title">Guild</h1>' in page
    assert "Practicing our craft together" in page
    assert "script-src 'self'" in r.headers["Content-Security-Policy"] and "no-store" in r.headers["Cache-Control"]


@pytest.mark.parametrize("path", ROOTS)
def test_a_guest_or_a_signed_out_visitor_gets_the_guard_not_the_home(staging, path):
    for client in (staging.guest(), staging.client()):
        r = client.get(path)
        assert r.status_code in (302, 403), (path, r.status_code)
        body = r.get_data(as_text=True)
        assert "gl-drawer" not in body and 'data-page="home"' not in body


def test_the_four_doors_keep_their_original_labels_and_go_where_the_spec_says(staging):
    page = _home(staging)
    drawers = _drawers(page)
    assert [d[1] for d in drawers] == ["build", "operate", "improve", "experiment"]
    for (href, _id, body), (label, want_href, img, kicker, flow, cta) in zip(drawers, DOORS):
        assert href == want_href, (label, href)
        assert f'<div class="gl-tab">{label}</div>' in body
        assert f'src="{img}"' in body
        assert f'<div class="gl-kicker">{kicker}</div>' in body
        assert f'<div class="gl-line">{flow}</div>' in body
        assert f'<div class="gl-link">{cta}</div>' in body


def test_the_door_words_are_the_originals_from_the_legacy_landing():
    original = (LEGACY / "guild_landing.html").read_text() + (LEGACY / "_build_card.html").read_text()
    # Build and Prototype Lab were renamed and re-worded in Guild 1.1: only their art is the original's.
    for label, _href, img, kicker, flow, cta in DOORS:
        words = (img,) if label in ("Build", "Prototype Lab") else (f">{label}<", img, kicker, cta)
        for word in words:
            assert word in original, word


def test_the_legacy_landing_and_its_build_card_are_unchanged(load_portal):
    html = load_portal().owner().get("/guild-previous").get_data(as_text=True)
    assert html.count('class="guild-card"') == 4
    assert '<a href="/guild/build/queue" class="guild-card">' in html
    assert 'href="/guild/experiment" class="guild-card"' in html


def test_no_tunnel_photo_and_no_square_tiles(staging):
    page = _home(staging).lower()
    assert "tunnel" not in page and "square" not in page
    assert page.count('class="gl-drawer"') == 4
    images = re.findall(r'<img src="([^"]+)"', page)
    assert sorted(images) == sorted(d[2] for d in DOORS)          # only the four original paintings
    css = LANDING_CSS.read_text()
    assert "aspect-ratio: 210 / 136" in css                         # Curator's rectangular drawer image
    assert not re.search(r"aspect-ratio:\s*1\s*(/\s*1)?\s*;", css)
    assert "tunnel" not in css.lower() and "url(" not in css         # no background photograph at all


def test_the_catalog_follows_curators_responsive_pattern():
    css = LANDING_CSS.read_text()
    assert "@media (max-width: 900px)" in css and ".gl-drawer { width: calc(50% - 12px); }" in css
    assert "@media (max-width: 480px)" in css and ".gl-drawer { width: 100%; }" in css
    curator = (REPO / "domains" / "curator" / "templates" / "curator_landing.html").read_text()
    assert ".card-catalog" in curator and "gl-" not in curator      # Curator keeps its own inline CSS


def test_the_home_uses_only_same_origin_assets(staging):
    page = _home(staging)
    for url in re.findall(r'(?:src|href)="([^"#]+)"', page):
        if url == "data:,":                                            # the empty favicon
            continue
        if url.startswith("http"):
            assert url.startswith("https://github.com/robertvanstedum/personal-ai-agents"), url
        else:
            assert url.startswith("/"), url
    assert "fonts.googleapis" not in page
    css = staging.owner().get("/guild-next/guild/ui-assets/css/landing.css")
    assert css.status_code == 200 and b".gl-catalog" in css.data


def test_the_home_reads_no_floor_state_and_calls_no_model(staging, monkeypatch):
    import minimoi_portal.guild_ui.floor_state as fs

    def _boom(*_a, **_k):
        raise AssertionError("the Guild home must not compute the floor state")
    monkeypatch.setattr(fs, "compute", _boom)
    page = _home(staging)
    assert page.count('class="gl-drawer"') == 4
    assert "data-mc-thread" not in page and "guild-page" not in page   # no chat, no floor bootstrap
