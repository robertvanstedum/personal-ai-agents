"""Screenshots of the M experience in a real browser, for the review pack. Run explicitly with GUILD_SHOTS=<folder>:

    GUILD_SHOTS=/path/to/folder venv/bin/python3 -m pytest tests/guild/shop_floor/browser_checks_m_evidence.py -q -p no:cacheprovider

It also asserts that no page scrolls sideways at 320/390/768/900/1440 and that nothing logs a console error. Skipped without GUILD_SHOTS."""
from __future__ import annotations

import os
import pathlib

import pytest
from playwright.sync_api import expect

from browser_checks_b1 import (_context, _errors, _no_page_overflow, _seed_topic, _wt_open, browser, floor, fresh_queue, go,  # noqa: F401
                               server)

SHOTS = os.environ.get("GUILD_SHOTS")
WIDTHS = [(1440, 900), (900, 800), (768, 1024), (390, 844), (320, 700)]
pytestmark = pytest.mark.skipif(not SHOTS, reason="set GUILD_SHOTS to a folder to capture")


def _shot(page, name, width):
    pathlib.Path(SHOTS).mkdir(parents=True, exist_ok=True)
    page.screenshot(path=f"{SHOTS}/{name}-{width}.png", full_page=False)


@pytest.mark.parametrize("width,height", WIDTHS)
def test_m_pages_at_every_width(browser, server, floor, fresh_queue, width, height):
    ctx, page = _context(browser, server, width, height)
    errors = _errors(page)
    tid, ids = _seed_topic(page, server, "M evidence")
    narrow = width < 701
    # The Work with a comment written under the discussion
    _wt_open(page, server, tid, item=ids["doc"])
    page.wait_for_selector("[data-wt-commentform] textarea")
    page.fill("[data-wt-commentform] textarea", "Say the phone width in the evidence before this goes to Codex.")
    page.locator("[data-wt-send-comment]").click()
    expect(page.locator(".wt-cmt[data-cid]")).to_have_count(1)
    page.fill("[data-wt-commentform] textarea", "A second thought, still being written")
    page.wait_for_timeout(200)
    assert _no_page_overflow(page), ("work", width)
    _shot(page, "01-the-work-comment", width)
    if narrow:
        page.locator("[data-wt-back]").click()
        page.wait_for_selector(".wt-shelf")
        assert _no_page_overflow(page), ("shelf", width)
        _shot(page, "02-the-work-shelf-phone", width)
    # the ask bar, then the sheet
    if not narrow:
        page.locator("[data-mc-pill]").click()
        page.wait_for_timeout(300)
        assert _no_page_overflow(page), ("sheet", width)
        _shot(page, "03-ask-sheet-open", width)
        hb = page.locator(".mc-panel[data-mode=floating] .mc-head").bounding_box()
        page.mouse.move(hb["x"] + 40, hb["y"] + hb["height"] / 2); page.mouse.down()
        page.mouse.move(hb["x"] - 60, hb["y"] - 220, steps=6); page.mouse.up()
        page.wait_for_timeout(200)
        assert _no_page_overflow(page), ("moved sheet", width)
        _shot(page, "03b-ask-sheet-moved-by-its-header", width)
        page.locator("[data-mc-resetpos]").click()
    # Board, Build Log, Rooms
    for path, name in (("board", "04-board"), ("build/log", "05-build-log"), ("rooms", "06-rooms")):
        go(page, f"{server['url']}/guild-next/guild/{path}")
        page.wait_for_timeout(300)
        assert _no_page_overflow(page), (name, width)
        _shot(page, name, width)
    # Operate / Improve construction posters and the Prototype Lab hero
    for path, name in (("operate", "07-operate"), ("improve", "08-improve"), ("experiment", "09-prototype-lab")):
        go(page, f"{server['url']}/guild-next/guild/{path}")
        page.wait_for_timeout(400)
        assert _no_page_overflow(page), (name, width)
        _shot(page, name, width)
    assert not [e for e in errors if "401" not in e], errors
    ctx.close()


@pytest.mark.parametrize("width,height", [(1440, 900), (440, 720), (390, 844), (320, 700)])
def test_board_and_notepad_shots(browser, server, floor, fresh_queue, width, height):
    from browser_checks_board_popout import POPOUT, BOARD, _add
    ctx = browser.new_context(viewport={"width": width, "height": height}, is_mobile=width < 641, has_touch=width < 641)
    page = ctx.new_page(); page.goto(f"{server['url']}/__b1_test_sign_in")
    go(page, f"{server['url']}{BOARD}")
    page.wait_for_selector("[data-bd-example]")
    _shot(page, "10-board-empty-example", width)
    _add(page, "Call back about the quote\nhttps://example.com/quote?id=42")
    _add(page, "Ask about the 12% option")
    page.locator("[data-bd-note] .bd-edit > summary").first.click()
    page.wait_for_timeout(150)
    _shot(page, "11-board-note-menu", width)
    page.keyboard.press("Escape")
    go(page, f"{server['url']}{POPOUT}")
    page.wait_for_selector("[data-bd-grid]")
    _shot(page, "12-notepad-standalone", width)
    page.locator("[data-mc-pill]").click()
    page.wait_for_timeout(250)
    assert _no_page_overflow(page)
    _shot(page, "13-notepad-mc-head-open", width)
    ctx.close()


@pytest.mark.parametrize("width,height", [(1440, 900), (768, 1024), (390, 844)])
def test_craftsman_chat_and_workshop_log_shots(browser, server, floor, fresh_queue, width, height):
    from browser_checks_b1 import _wt
    ctx = browser.new_context(viewport={"width": width, "height": height}, is_mobile=width < 641, has_touch=width < 641)
    page = ctx.new_page(); page.goto(f"{server['url']}/__b1_test_sign_in")
    tid, ids = _seed_topic(page, server, "Guild chat improvements")
    _wt(page, "/topics/create", {"title": "Rooms follow-up"}); _wt(page, "/topics/create", {"title": "Mobile notepad"})
    go(page, f"{server['url']}/guild-next/guild/build")                                  # the original Craftsman Chat, the Build landing
    page.wait_for_selector("[data-mc-input]")
    assert _no_page_overflow(page), ("chat", width)
    _shot(page, "40-craftsman-chat-landing", width)
    _wt_open(page, server, tid)
    page.wait_for_timeout(300)
    assert _no_page_overflow(page), ("log", width)
    _shot(page, "41-workshop-log", width)
    page.locator(".wt-logtitle", has_text="Spec · topic records").click()
    page.wait_for_selector(".wt-reader")
    assert _no_page_overflow(page), ("document", width)
    _shot(page, "42-workshop-log-document-open", width)
    page.get_by_role("button", name="‹ Back to the log").click()
    page.locator("[data-wt-chat-link]").click()                                           # Discuss in Chat: the topic's conversation, with its quiet link back
    page.wait_for_selector("[data-topic-materials]", state="attached")
    _shot(page, "43-topic-conversation-with-materials-link", width)
    ctx.close()


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_lab_operate_improve_and_build_log_shots(browser, server, floor, fresh_queue, width, height):
    ctx, page = _context(browser, server, width, height)
    for path, name in (("experiment", "30-prototype-lab-cards"), ("operate", "31-operate"), ("improve", "32-improve"), ("build/log", "33-build-log"), ("rooms", "35-rooms")):
        go(page, f"{server['url']}/guild-next/guild/{path}")
        page.wait_for_timeout(300)
        assert _no_page_overflow(page), (name, width)
        _shot(page, name, width)
    go(page, f"{server['url']}/guild-next/guild/experiment")
    page.click('[data-proto-view="list"]')
    page.wait_for_timeout(150)
    _shot(page, "34-prototype-lab-list", width)
    ctx.close()
