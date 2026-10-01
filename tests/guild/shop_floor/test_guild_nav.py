"""Guild 1.1 slice 1 (spec §3): the Guild bar is Chat · Board · Build Log ·
Rooms · More ▾ on every /guild-next page, the Guild home included.

Chat is the Shop floor; Board is the wall (the Workbench) and its post-its
page; Build Log and Rooms say "coming in a later slice" until slices 2 and 4.
More holds the Workshop, the Guild home, the Docs line with its GitHub link,
Operate, Labs and Design Studio (Planning Studio, under CoS, linked honestly
to the Labs entry point). The portal's workspace bar is unchanged."""
from __future__ import annotations

import re

import pytest

from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)

DOCS = "https://github.com/robertvanstedum/personal-ai-agents/tree/main/docs"
DAILY = [("chat", "Chat", "/guild-next/guild/build"), ("board", "Board", "/guild-next/guild/build/bench"),
         ("build-log", "Build Log", "/guild-next/guild/build/log"), ("rooms", "Rooms", "/guild-next/guild/rooms")]
MORE = [("workshop", "Workshop", "/guild-next/guild/workshop"), ("home", "Guild home", "/guild-next/"),
        ("operate", "Operate", "/guild-next/guild/operate"), ("labs", "Labs", "/guild-next/guild/labs"),
        ("design-studio", "Design Studio", "/guild-next/guild/labs#planning-studio")]
PAGES = {"/guild-next/": None, "/guild-next/guild/build": "chat", "/guild-next/guild/build/bench": "board",
         "/guild-next/guild/build/postits": "board", "/guild-next/guild/build/queue": "build-log",
         "/guild-next/guild/build/log": "build-log", "/guild-next/guild/rooms": "rooms",
         "/guild-next/guild/workshop": None, "/guild-next/guild/operate": None, "/guild-next/guild/labs": None}


def _nav(page):
    start = page.index('<div class="guild-subnav guild-sections">')
    return page[start:page.index("</nav>", start)]


@pytest.mark.parametrize("path", sorted(PAGES))
def test_every_page_has_the_four_daily_tabs_then_more(staging, path):
    r = staging.owner().get(path)
    assert r.status_code == 200, (path, r.status_code)
    nav = _nav(r.get_data(as_text=True))
    tabs = re.findall(r'<a href="([^"]+)" class="subnav-link[^"]*" data-section="([a-z-]+)"[^>]*>([^<]+)</a>', nav)
    assert [(s, label, href) for href, s, label in tabs] == DAILY, path
    assert '<summary class="subnav-link' in nav and "More ▾</summary>" in nav
    assert nav.index("Rooms</a>") < nav.index("More ▾")
    for word in ("Shop floor", "Wall", "Prototype Lab</a>", "Planning Studio</a>", "subnav-dot"):
        assert word not in nav, (path, word)                   # the old strip is gone
    active = re.findall(r'class="subnav-link subnav-active" data-section="([a-z-]+)"', nav)
    assert active == ([PAGES[path]] if PAGES[path] else []), (path, active)
    assert nav.count('aria-current="page"') <= 1


def test_more_holds_the_workshop_home_docs_operate_labs_and_design_studio(staging):
    nav = _nav(staging.owner().get("/guild-next/guild/build").get_data(as_text=True))
    menu = nav[nav.index("data-subnav-menu"):]
    found = re.findall(r'<a href="([^"]+)" data-more="([a-z-]+)"[^>]*>([^<]+)<small>', menu)
    assert [(m, label, href) for href, m, label in found] == MORE
    assert f'<a href="{DOCS}" target="_blank" rel="noopener">Open docs ↗</a>' in menu
    assert "Docs: the project's written record lives on GitHub." in menu
    assert "Planning Studio, under CoS · not served on dev yet" in menu    # Design Studio, said honestly


@pytest.mark.parametrize("path,slice_no", [("/guild-next/guild/build/log", 2), ("/guild-next/guild/rooms", 4)])
def test_build_log_and_rooms_say_coming_in_a_later_slice(staging, path, slice_no):
    r = staging.owner().get(path)
    page = r.get_data(as_text=True)
    assert r.status_code == 200
    assert f"Coming in a later slice (Guild 1.1 slice {slice_no}). Nothing here is wired yet." in page
    assert 'aria-current="page"' in _nav(page)
    for client in (staging.guest(), staging.client()):
        assert client.get(path).status_code in (302, 403)


def test_build_log_points_to_what_works_today(staging):
    page = staging.owner().get("/guild-next/guild/build/log").get_data(as_text=True)
    assert '<a href="/guild-next/guild/build/queue">Build Queue</a>' in page
    assert '<a href="/guild/build">legacy Build Log</a>' in page


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
