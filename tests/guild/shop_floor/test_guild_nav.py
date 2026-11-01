"""One Workshop row on every Guild page; Team lives within Operate."""
from __future__ import annotations

import re

import pytest

from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)

DOCS = "https://github.com/robertvanstedum/personal-ai-agents/tree/main/docs"
DAILY = [("chat", "Craft", "/guild-next/guild/build"), ("board", "Board", "/guild-next/guild/board"),
         ("build-log", "Build Log", "/guild-next/guild/build/log"),
         ("rooms", "Guildhall", "/guild-next/guild/rooms")]
MORE = [("workshop", "Workshop", "/guild-next/guild/workshop"), ("media", "Media library", "/guild-next/guild/media"),
        ("home", "Guild home", "/guild-next/"),
        ("operate", "Operate", "/guild-next/guild/operate"), ("labs", "Labs", "/guild-next/guild/labs"),
        ("design-studio", "Design Studio", "/guild-next/guild/labs#planning-studio")]
PAGES = {"/guild-next/": None, "/guild-next/guild/build": "chat", "/guild-next/guild/build/bench": "board",
         "/guild-next/guild/build/postits": "board", "/guild-next/guild/board": "board",
         "/guild-next/guild/media": None, "/guild-next/guild/build/queue": "build-log",
         "/guild-next/guild/build/log": "build-log", "/guild-next/guild/rooms": "rooms",
         "/guild-next/guild/workshop": None}


def _nav(page):
    start = page.index('<div class="workshop-row">')
    return page[start:page.index("</nav>", start)]


@pytest.mark.parametrize("path", sorted(PAGES))
def test_every_build_page_has_work_tabs_and_phone_more(staging, path):
    r = staging.owner().get(path)
    assert r.status_code == 200, (path, r.status_code)
    nav = _nav(r.get_data(as_text=True))
    tabs = re.findall(r'<a href="([^"]+)" class="subnav-link[^"]*" data-section="([a-z-]+)"[^>]*>([^<]+)</a>', nav)
    assert [(s, label, href) for href, s, label in tabs] == DAILY, path
    assert "data-subnav-more" in nav
    assert "/static/guild/guild-build.webp" in nav
    assert ">Admin</a>" not in nav
    assert "gu-section-band" not in nav
    for word in ("Shop floor", "Wall", "Prototype Lab</a>", "Planning Studio</a>", "subnav-dot"):
        assert word not in nav, (path, word)                   # the old strip is gone
    active = re.findall(r'class="subnav-link subnav-active" data-section="([a-z-]+)"', nav)
    assert active == ([PAGES[path]] if PAGES[path] else []), (path, active)
    assert nav.count('aria-current="page"') <= 1


def test_build_has_no_docs_tab_or_general_menu_and_the_board_still_reaches_the_media_library(staging):
    page = staging.owner().get("/guild-next/guild/build").get_data(as_text=True)
    nav = _nav(page)
    for gone in ("GitHub repository", "Documentation", "Guild home", "/guild-next/guild/docs", "Previous Guild", "Open docs"):
        assert gone not in nav, gone
    assert "data-open-wall" not in page and "Open wall" not in page            # the old Wall link is retired from Chat
    board = staging.owner().get("/guild-next/guild/board").get_data(as_text=True)
    assert 'data-bd-media-link' in board                                        # Media library stays on the Board's own menu


def test_rooms_is_the_real_page_since_slice_4(staging):
    r = staging.owner().get("/guild-next/guild/rooms")
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and "data-rm-composer" in page and "Coming in a later slice" not in page
    assert 'aria-current="page"' in _nav(page)
    for client in (staging.guest(), staging.client()):
        assert client.get("/guild-next/guild/rooms").status_code in (302, 403)


def test_build_log_is_the_real_page_since_slice_2(staging):
    page = staging.owner().get("/guild-next/guild/build/log").get_data(as_text=True)
    assert "data-bl-matrix" in page and "Coming in a later slice" not in page


def test_the_portal_workspace_bar_is_unchanged(staging):
    for path in ("/guild-next/", "/guild-next/guild/build"):
        page = staging.owner().get(path).get_data(as_text=True)
        assert page.count('id="portal-nav-bar"') == 1
        assert page.index('id="portal-nav-bar"') < page.index('class="guild-nav"')


def test_the_labs_entry_points_stay_truthful(staging):
    body = staging.owner().get("/guild-next/guild/labs").get_data(as_text=True)
    assert "neither is served on dev yet" in body and 'id="planning-studio"' in body
    assert "planning-studio/" in body and "prototype-lab/" in body
    assert staging.guest().get("/guild-next/guild/labs").status_code in (302, 403)

@pytest.mark.parametrize('path', ['/guild-next/guild/operate', '/guild-next/guild/improve', '/guild-next/guild/experiment'])
def test_other_areas_share_one_workshop_row(staging, path):
    response = staging.owner().get(path)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    nav = _nav(html)
    assert html.count('class="workshop-row"') == 1
    assert 'gu-section-band' not in html and 'guild-subnav' not in html
    assert 'guild-build.webp' in nav and '>Admin</a>' not in nav
    assert nav.count('aria-current="page"') == 2  # desktop link and phone More equivalent
