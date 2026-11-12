"""1.2 fast-follow lane A in a real browser, synthetic local data, no providers.

FF-01: one way into the chat list (the rail's ☰; MC's header ☰ only on a phone, where there is no rail), search inside it.
FF-02: ⋯ › Archive this chat archives in place, lands on another chat, and Undo brings it back.

    venv/bin/python3 -m pytest tests/guild/shop_floor/browser_checks_ff_lane_a.py -q -p no:cacheprovider
"""
import pytest
from playwright.sync_api import expect
from browser_checks_b1 import browser, server, fresh_queue, floor, _context, _errors, go  # noqa: F401 (fixtures)

RAIL = ".mm-chat-rail"
HEADER_MENU = ".mc-floorbar [data-history-toggle]"
LIST = "[data-floor-history]"


def _watch(page):
    """Console errors, minus the browser's generic line for a 404, and every 404 by URL except the model-route read, which
    this synthetic server does not install (routes off); any other missing resource still fails the check."""
    errors, missing = _errors(page), []
    page.on("response", lambda r: missing.append(r.url) if r.status == 404 and not r.url.endswith("/route") else None)
    def problems():
        return [e for e in errors if "status of 404" not in e] + missing
    return problems


def _rail_buttons(page):
    return page.locator(f"{RAIL} button").evaluate_all("bs => bs.map(b => b.textContent.trim())")


@pytest.mark.parametrize("width", [1440, 860, 700])
def test_ff01_the_rail_menu_is_the_one_way_in_where_the_rail_shows(browser, server, floor, width):
    ctx, page = _context(browser, server, width, 860)
    problems = _watch(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    if width >= 900:                                                    # the list is open beside the chat: fold it to the rail
        expect(page.locator(LIST)).to_be_visible()
        page.click(f"{LIST} [data-history-close]")
    expect(page.locator(RAIL)).to_be_visible()
    assert _rail_buttons(page) == ["☰", "+"]                            # no ⌕ any more
    expect(page.locator(HEADER_MENU)).to_be_hidden()                    # and no second ☰ in the header
    page.click(f"{RAIL} [data-sidebar-toggle]")
    expect(page.locator(LIST)).to_be_visible()
    expect(page.locator(f"{LIST} [data-chat-filter]")).to_be_visible()  # search lives inside the list
    page.fill(f"{LIST} [data-chat-filter]", "zz-no-such-chat")
    expect(page.locator("[data-conv-list] [data-conv]").filter(visible=True)).to_have_count(0)
    assert not problems(), problems()
    ctx.close()


def test_ff01_a_phone_keeps_the_header_menu_because_it_has_no_rail(browser, server, floor):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    go(page, f"{server['url']}/guild-next/guild/build")
    expect(page.locator(RAIL)).to_be_hidden()
    expect(page.locator(HEADER_MENU)).to_be_visible()
    page.click(HEADER_MENU)
    expect(page.locator(LIST)).to_be_visible()
    expect(page.locator(f"{LIST} [data-chat-filter]")).to_be_visible()
    ctx.close()


@pytest.mark.parametrize("width,way_in", [(700, RAIL + " [data-sidebar-toggle]"), (420, HEADER_MENU)], ids=["wide-pane", "narrow-pane"])
def test_ff01_mc_beside_cos_has_exactly_one_way_in(browser, server, floor, monkeypatch, width, way_in):
    monkeypatch.setenv("MINIMOI_MC_EVERYWHERE", "1")
    ctx, page = _context(browser, server, width, 800)
    go(page, f"{server['url']}/guild-next/guild/companion/cos")
    visible = [s for s in (RAIL + " [data-sidebar-toggle]", HEADER_MENU) if page.locator(s).first.is_visible()]
    assert visible == [way_in.strip()], visible
    page.click(way_in)
    expect(page.locator(LIST)).to_be_visible()
    ctx.close()


def test_ff02_archive_this_chat_lands_on_another_and_undo_brings_it_back(browser, server, floor):
    ctx, page = _context(browser, server, 1440, 900)
    problems = _watch(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    with page.expect_navigation():
        page.click(f"{LIST} [data-conv-new]")
    page.wait_for_selector("body[data-ready=true]")
    cid = page.url.split("?c=")[1]
    page.click("[data-fb-more] > summary")
    with page.expect_navigation():
        page.click("[data-fb-archive]")
    page.wait_for_selector("body[data-ready=true]")
    assert f"c={cid}" not in page.url and "?c=" in page.url              # another chat opened
    expect(page.locator(f'[data-conv="{cid}"]')).to_have_count(0)
    toast = page.locator("[data-undo-toast]")
    expect(toast).to_be_visible()
    expect(toast).to_have_text("Archived · Undo")
    with page.expect_navigation():
        toast.locator("[data-undo-archive]").click()
    page.wait_for_selector("body[data-ready=true]")
    assert page.url.endswith(f"?c={cid}")
    expect(page.locator(f'[data-conv="{cid}"]')).to_have_count(1)        # back in the list
    expect(page.locator("[data-undo-toast]")).to_have_count(0)           # shown once
    assert not problems(), problems()
    ctx.close()


def test_ff02_archived_chats_are_still_reachable_inside_the_list(browser, server, floor):
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}/guild-next/guild/build")
    with page.expect_navigation():
        page.click(f"{LIST} [data-conv-new]")
    page.wait_for_selector("body[data-ready=true]")
    cid = page.url.split("?c=")[1]
    page.click("[data-fb-more] > summary")
    with page.expect_navigation():
        page.click("[data-fb-archive]")
    page.wait_for_selector("body[data-ready=true]")
    with page.expect_navigation():
        page.click(f'{LIST} [data-conv-view="archived"]')
    page.wait_for_selector("body[data-ready=true]")
    expect(page.locator(f'[data-conv="{cid}"]')).to_have_count(1)
    ctx.close()
