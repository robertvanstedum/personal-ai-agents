"""The Board's pop-out window (the owner's desktop notepad): the same Board and notes in a light page of its own.

The page is owner-only, carries the SAME Board data as the Board page and reads/writes only through the same API,
has no portal bar, sections bar or conversation panel, and loads only its own small script. The Board page gets the
Pop out button and says what it does. (The cross-window behaviour itself is exercised in a real browser: see the
verification notes; here the contract of the page and the data both windows share is pinned.)"""
from __future__ import annotations

import json
import re
from pathlib import Path

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)

API = "/guild-next/api/v1"
PAGE = "/guild-next/guild/board/popout"
BOARD = "/guild-next/guild/board"
STATIC = Path(__file__).resolve().parents[3] / "minimoi_portal" / "guild_ui" / "static"


def _add(client, token, text):
    r = client.post(f"{API}/postits", json=keyed(text=text), headers=write_headers(token))
    assert r.status_code == 200, r.get_json()
    return r.get_json()["postit"]


def _embedded(html):
    return json.loads(re.search(r'<script type="application/json" id="board-data">(.*?)</script>', html, re.S).group(1))


def test_the_pop_out_is_owner_only(floored):
    assert floored.client().get(PAGE).status_code == 302                  # anonymous: sent to sign in
    guest = floored.guest().get(PAGE)
    assert guest.status_code in (302, 403)
    assert floored.owner().get(PAGE).status_code == 200


def test_opened_directly_and_signed_out_it_returns_to_the_same_notepad_after_sign_in(floored):
    """Robert, 10 Oct: the notepad is launched on its own (a bookmark, a phone's home screen). Signed out, the sign-in sends him back to the same notepad
    in the same environment: a path, never another host."""
    r = floored.client().get(PAGE)
    assert r.status_code == 302
    loc = r.headers["Location"]
    assert "/login" in loc and "next=" in loc and PAGE.replace("/", "%2F") in loc.replace("/guild-next", "/guild-next") or PAGE in loc
    assert "http://" not in loc and "https://" not in loc and "minimoi.ai" not in loc


def test_it_shows_the_same_board_data_as_the_board_page(floored):
    client = floored.owner(); token = floored.csrf(client)
    _add(client, token, "one"); _add(client, token, "two")
    popout = _embedded(client.get(PAGE).get_data(as_text=True))
    board = _embedded(client.get(BOARD).get_data(as_text=True))
    assert [n["text"] for n in popout["active"]] == [n["text"] for n in board["active"]] == ["two", "one"]
    assert popout["order_rev"] == board["order_rev"] and popout["counts"] == board["counts"]
    assert json.dumps(popout, sort_keys=True) == json.dumps(board, sort_keys=True)          # one Board, not a copy


def test_it_is_a_bare_page_with_no_portal_bar_and_a_small_mc_head(floored):
    html = floored.owner().get(PAGE).get_data(as_text=True)
    assert 'data-bd-grid' in html and 'data-bd-sync' in html and 'data-bd-new' in html and 'data-bd-open="photo"' in html and 'data-bd-open="note"' not in html
    assert "guild-nav" not in html and "portal-nav" not in html and "gu-section-band" not in html          # no navigation of its own
    assert "data-mc-pill" in html and "data-mc-panel" in html and "Chat with Master Craftsman" in html     # the small MC head works here too (10 Oct)
    assert "js/main.js" in html and "js/popout.js" not in html
    assert "data-bd-popout" not in html                                       # no pop-out button inside the pop-out
    assert "phone-summary" not in html and "data-phone-open" not in html      # a bare notepad: no Needs-you summary above it on a phone
    assert 'data-page="board_popout"' in html and 'data-page-open="true"' in html
    page = json.loads(re.search(r'<script type="application/json" id="guild-page">(.*?)</script>', html, re.S).group(1))
    assert page["page"] == "board_popout" and page["urls"]["api"].endswith("/api/v1") and page["csrf_token"]
    assert page["chat_scope"] == "board"                                      # the same Board conversation as the Board page


def test_the_page_makes_no_inline_script_or_style(floored):
    """The CSP forbids them; the pop-out must work under the same policy."""
    html = floored.owner().get(PAGE).get_data(as_text=True)
    assert not re.search(r"<script(?![^>]*(src=|type=\"application/json\"))", html)
    assert " style=" not in html and "<style" not in html
    csp = floored.owner().get(PAGE).headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp


def test_the_board_page_offers_pop_out_and_says_what_it_does(floored):
    html = floored.owner().get(BOARD).get_data(as_text=True)
    assert "data-bd-popout" in html and "Pop out" in html and 'class="bd-plus"' in html and "＋" in html     # a small ＋ and a discreet pop-out icon
    assert "stays open while you go to other pages" in html and "depends on your computer" in html      # no always-on-top promise
    assert "js/main.js" in html


def test_the_static_files_it_loads_exist_and_the_editing_hooks_are_in_the_script():
    for name in ("js/board.js", "css/board_popout.css", "css/board.css"):
        assert (STATIC / name).is_file(), name
    board = (STATIC / "js/board.js").read_text()
    for needle in ("/postits/${id}/text", "'/board/rev'", "BroadcastChannel", "guild-board-popout", "data-bd-edit-save",
                   "bd-conflict", "visibilitychange"):
        assert needle in board, needle
    api = (STATIC / "js/api.js").read_text()
    assert "data.error === 'csrf'" in api and "call.retrying" in api         # a long-open window refreshes a stale token once
