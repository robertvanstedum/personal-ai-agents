"""Guild 1.1 slice 1 (spec §3): the Guild bar is Chat · Board · Build Log ·
Rooms · More ▾ on every /guild-next page, the Guild home included.

Chat is the Shop floor; Board is the wall (the Workbench) and its post-its
page; Build Log is the real page since slice 2, Rooms since slice 4.
More holds the Workshop, the Guild home, the Docs line with its GitHub link,
Operate, Labs and Design Studio (Planning Studio, under CoS, linked honestly
to the Labs entry point). The portal's workspace bar is unchanged."""
from __future__ import annotations

import re

import pytest

from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)

DOCS = "https://github.com/robertvanstedum/personal-ai-agents/tree/main/docs"
DAILY = [("chat", "Chat", "/guild-next/guild/build"), ("board", "Board", "/guild-next/guild/board"),
         ("build-log", "Build Log", "/guild-next/guild/build/log"), ("workshop", "Workshop", "/guild-next/guild/workshop"), ("rooms", "Rooms", "/guild-next/guild/rooms")]
MORE = [("workshop", "Workshop", "/guild-next/guild/workshop"), ("media", "Media library", "/guild-next/guild/media"),
        ("home", "Guild home", "/guild-next/"),
        ("operate", "Operate", "/guild-next/guild/operate"), ("labs", "Labs", "/guild-next/guild/labs"),
        ("design-studio", "Design Studio", "/guild-next/guild/labs#planning-studio")]
PAGES = {"/guild-next/": None, "/guild-next/guild/build": "chat", "/guild-next/guild/build/bench": "board",
         "/guild-next/guild/build/postits": "board", "/guild-next/guild/board": "board",
         "/guild-next/guild/media": None, "/guild-next/guild/build/queue": "build-log",
         "/guild-next/guild/build/log": "build-log", "/guild-next/guild/rooms": "rooms",
         "/guild-next/guild/workshop": "workshop"}


def _nav(page):
    start = page.index('<div class="guild-subnav guild-sections">')
    return page[start:page.index("</nav>", start)]


@pytest.mark.parametrize("path", sorted(PAGES))
def test_every_build_page_has_five_tabs_and_no_more_menu(staging, path):
    r = staging.owner().get(path)
    assert r.status_code == 200, (path, r.status_code)
    nav = _nav(r.get_data(as_text=True))
    tabs = re.findall(r'<a href="([^"]+)" class="subnav-link[^"]*" data-section="([a-z-]+)"[^>]*>([^<]+)</a>', nav)
    assert [(s, label, href) for href, s, label in tabs] == DAILY, path
    assert "More ▾" not in nav and "subnav-more" not in nav and "data-more=" not in nav      # retired 5 Oct
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
